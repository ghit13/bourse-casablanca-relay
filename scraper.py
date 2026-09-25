#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relais cloud V7 — sources officielles uniquement.

Sources:
- Bourse de Casablanca: univers actions, marché actions, overview, avis
- AMMC: communiqués émetteurs, états financiers

Sorties:
- data/latest.json
- data/history.csv
- data/market.csv
- data/universe.csv
- data/news.csv
- data/status.json
"""
from __future__ import annotations
import csv, json, re, time, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

URL_UNIVERSE = "https://www.casablanca-bourse.com/en/marches-produits/actions"
URL_MARKET = "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing"
URL_OVERVIEW = "https://www.casablanca-bourse.com/live-market/overview"
URL_NOTICES = "https://www.casablanca-bourse.com/marches-produits/avis-et-instructions/avis"
URL_AMMC_PRESS = "https://www.ammc.ma/fr/communiques-presse-emetteurs"
URL_AMMC_FS = "https://www.ammc.ma/fr/liste-etats-financiers-emetteurs"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"

def session():
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
    return s

S = session()

def fetch(url, params=None):
    r = S.get(url, params=params, timeout=50)
    r.raise_for_status()
    return r.text

def soup(url, params=None):
    return BeautifulSoup(fetch(url, params=params), "html.parser")

def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def norm(s):
    s = unicodedata.normalize("NFKD", clean(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()

def num_fr(value):
    s = clean(value).replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    s = s.replace("MAD", "").replace("%", "").replace("−", "-").replace("—", "")
    if s in ("", "-", "N.T", "NT"):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.+-]", "", s)
    try:
        return float(s)
    except Exception:
        return None

def find_table(sp, required_headers):
    req = [norm(x) for x in required_headers]
    for table in sp.find_all("table"):
        heads = [norm(x.get_text(" ", strip=True)) for x in table.find_all("th")]
        joined = " | ".join(heads)
        if all(any(r in h or h in r for h in heads) for r in req):
            return table
        if all(r in joined for r in req):
            return table
    return None

def parse_universe():
    sp = soup(URL_UNIVERSE)
    table = find_table(sp, ["Ticker", "Issuer"])
    if table is None:
        raise RuntimeError("Table officielle des actions introuvable.")
    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        ticker = clean(tds[0].get_text(" ", strip=True)).upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}", ticker):
            continue
        issuer = clean(tds[1].get_text(" ", strip=True))
        compartment = clean(tds[2].get_text(" ", strip=True))
        shares = num_fr(tds[3].get_text(" ", strip=True))
        out.append({
            "ticker": ticker,
            "issuer": issuer,
            "compartment": compartment,
            "shares": int(shares) if shares is not None else None,
            "instrument_url": f"https://www.casablanca-bourse.com/fr/live-market/instruments/{ticker}",
            "source": URL_UNIVERSE,
        })
    # Deduplicate, keeping official order.
    seen, dedup = set(), []
    for r in out:
        if r["ticker"] not in seen:
            seen.add(r["ticker"]); dedup.append(r)
    if len(dedup) < 70:
        raise RuntimeError(f"Univers officiel incomplet: {len(dedup)} actions.")
    return dedup

def header_map(table):
    hs = [clean(x.get_text(" ", strip=True)) for x in table.find_all("th")]
    return hs

def pick(row, headers, candidates):
    nh = [norm(h) for h in headers]
    for cand in candidates:
        nc = norm(cand)
        for i, h in enumerate(nh):
            if nc == h or nc in h or h in nc:
                return row[i] if i < len(row) else None
    return None

def parse_market(universe):
    sp = soup(URL_MARKET)
    table = find_table(sp, ["Instrument", "Dernier cours", "Volume"])
    if table is None:
        raise RuntimeError("Table marché actions introuvable.")
    headers = header_map(table)
    valid = {x["ticker"] for x in universe}
    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        row = [clean(td.get_text(" ", strip=True)) for td in tds]
        ticker = None
        a = tr.find("a", href=re.compile(r"/live-market/instruments/"))
        if a:
            m = re.search(r"/live-market/instruments/([A-Z0-9]+)", a.get("href", ""), re.I)
            if m:
                ticker = m.group(1).upper()
        if ticker not in valid:
            # Some pages show ticker itself as the instrument.
            first = row[0].upper()
            if first in valid:
                ticker = first
        if ticker not in valid:
            continue

        rec = {
            "ticker": ticker,
            "instrument": row[0],
            "status": pick(row, headers, ["Statut"]),
            "reference": num_fr(pick(row, headers, ["Cours de référence"])),
            "open": num_fr(pick(row, headers, ["Ouverture"])),
            "close": num_fr(pick(row, headers, ["Dernier cours"])),
            "quantity": num_fr(pick(row, headers, ["Quantité échangée"])),
            "volume_mad": num_fr(pick(row, headers, ["Volume"])),
            "change_pct": num_fr(pick(row, headers, ["Variation en %", "Variation"])),
            "high": num_fr(pick(row, headers, ["+ haut jour", "Plus haut"])),
            "low": num_fr(pick(row, headers, ["+ bas jour", "Plus bas"])),
            "bid": num_fr(pick(row, headers, ["Meilleur prix à l'achat"])),
            "ask": num_fr(pick(row, headers, ["Meilleur prix à la vente"])),
            "bid_qty": num_fr(pick(row, headers, ["Quantité Meilleur prix à l'achat"])),
            "ask_qty": num_fr(pick(row, headers, ["Quantité Meilleur prix à la vente"])),
            "market_cap": num_fr(pick(row, headers, ["Capitalisation"])),
            "transactions": num_fr(pick(row, headers, ["Nombre de transactions"])),
            "source": URL_MARKET,
        }
        if rec["close"] is None:
            rec["close"] = rec["reference"]
        out.append(rec)
    return out

def parse_instrument_fallback(ticker):
    url = f"https://www.casablanca-bourse.com/fr/live-market/instruments/{ticker}"
    sp = soup(url)
    text = clean(sp.get_text(" ", strip=True))
    pats = {
        "close": r"Cours \(MAD\)\s+([0-9\s\u202f.,-]+)",
        "change_pct": r"Variation\s+([+\-−]?[0-9\s.,]+)\s*%",
        "open": r"Ouverture\s+([0-9\s\u202f.,-]+)",
        "high": r"Plus haut\s+([0-9\s\u202f.,-]+)",
        "low": r"Plus bas\s+([0-9\s\u202f.,-]+)",
        "reference": r"Cours de clôture veille\s+([0-9\s\u202f.,-]+)",
        "market_cap": r"Capitalisation\s+([0-9\s\u202f.,-]+)",
        "volume_mad": r"Volume\s+([0-9\s\u202f.,-]+)",
        "quantity": r"Quantité échangée\s+([0-9\s\u202f.,-]+)",
        "transactions": r"Nombre de transactions\s+([0-9\s\u202f.,-]+)",
    }
    rec = {"ticker": ticker, "instrument": ticker, "status": None, "source": url}
    for k, p in pats.items():
        m = re.search(p, text, re.I)
        rec[k] = num_fr(m.group(1)) if m else None
    if rec.get("close") is None:
        rec["close"] = rec.get("reference")
    return rec

def enrich_missing_market(universe, market):
    by = {x["ticker"]: x for x in market}
    missing = [u["ticker"] for u in universe if u["ticker"] not in by]
    # Fetch missing tickers individually; this also covers suspended/no-trade names.
    for i, ticker in enumerate(missing, 1):
        try:
            rec = parse_instrument_fallback(ticker)
            by[ticker] = rec
        except Exception as e:
            by[ticker] = {"ticker": ticker, "instrument": ticker, "source": f"ERROR: {e}"}
        time.sleep(0.10)
    return [by[u["ticker"]] for u in universe]

def parse_overview():
    try:
        sp = soup(URL_OVERVIEW)
        text = clean(sp.get_text(" ", strip=True))
    except Exception:
        return {}
    out = {}
    for label in ["MASI", "MASI 20", "MASI ESG", "MASI Mid and Small Cap"]:
        m = re.search(re.escape(label) + r"\s+([0-9\s\u202f.,]+)\s+([+\-−]?[0-9.,]+)\s*%", text, re.I)
        if m:
            out[label] = {"value": num_fr(m.group(1)), "change_pct": num_fr(m.group(2))}
    return out

def scrape_dated_links(base_url, pages=5, kind="AMMC"):
    out, seen = [], set()
    for page in range(pages):
        url = base_url if page == 0 else f"{base_url}?page={page}"
        try:
            sp = soup(url)
        except Exception:
            continue
        for a in sp.find_all("a", href=True):
            title = clean(a.get_text(" ", strip=True))
            if len(title) < 8:
                continue
            block = a
            context = title
            for _ in range(5):
                block = getattr(block, "parent", None)
                if block is None:
                    break
                context = clean(block.get_text(" ", strip=True))
                if re.search(r"\b\d{2}/\d{2}/\d{4}\b", context):
                    break
            dm = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", context)
            if not dm:
                continue
            href = urljoin(base_url, a.get("href", ""))
            key = (dm.group(1), title, href)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "date": dm.group(1),
                "title": title,
                "url": href,
                "kind": kind,
                "source_page": url,
            })
    return out

def parse_notices():
    return scrape_dated_links(URL_NOTICES, pages=3, kind="Bourse - avis")

def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

def update_history(market, today):
    path = DATA / "history.csv"
    fields = ["date","ticker","close","open","high","low","volume_mad","quantity",
              "transactions","change_pct","market_cap"]
    rows = []
    if path.exists():
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    keep = {(r.get("date"), r.get("ticker")): r for r in rows if r.get("date") and r.get("ticker")}
    for m in market:
        if m.get("close") is None:
            continue
        keep[(today, m["ticker"])] = {
            "date": today, "ticker": m["ticker"], "close": m.get("close"),
            "open": m.get("open"), "high": m.get("high"), "low": m.get("low"),
            "volume_mad": m.get("volume_mad"), "quantity": m.get("quantity"),
            "transactions": m.get("transactions"), "change_pct": m.get("change_pct"),
            "market_cap": m.get("market_cap"),
        }
    # Keep roughly 3 years.
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=1100)).isoformat()
    final = [v for (d, _), v in keep.items() if d >= cutoff]
    final.sort(key=lambda r: (r["date"], r["ticker"]))
    write_csv(path, final, fields)
    return len(final)

def main():
    generated = datetime.now(timezone.utc)
    warnings = []

    universe = parse_universe()
    market0 = parse_market(universe)
    market = enrich_missing_market(universe, market0)
    valid_market = [m for m in market if m.get("close") is not None]
    if len(valid_market) < 40:
        raise RuntimeError(f"Couverture marché insuffisante: {len(valid_market)} cours valides.")

    indices = parse_overview()
    notices = parse_notices()
    ammc_press = scrape_dated_links(URL_AMMC_PRESS, pages=6, kind="AMMC - communiqué")
    ammc_fs = scrape_dated_links(URL_AMMC_FS, pages=6, kind="AMMC - états financiers")

    # Merge universe metadata into market records.
    umap = {u["ticker"]: u for u in universe}
    for m in market:
        u = umap.get(m["ticker"], {})
        m["issuer"] = u.get("issuer")
        m["compartment"] = u.get("compartment")
        m["shares"] = u.get("shares")
        if m.get("market_cap") is None and m.get("close") is not None and u.get("shares"):
            m["market_cap"] = m["close"] * u["shares"]

    latest = {
        "schema_version": 1,
        "generated_at_utc": generated.isoformat(),
        "sources": {
            "universe": URL_UNIVERSE,
            "market": URL_MARKET,
            "overview": URL_OVERVIEW,
            "bourse_notices": URL_NOTICES,
            "ammc_press": URL_AMMC_PRESS,
            "ammc_financial_statements": URL_AMMC_FS,
        },
        "coverage": {
            "universe_count": len(universe),
            "market_rows": len(market),
            "market_valid_close": len(valid_market),
            "ammc_press_count": len(ammc_press),
            "ammc_financial_statements_count": len(ammc_fs),
            "bourse_notices_count": len(notices),
        },
        "indices": indices,
        "universe": universe,
        "market": market,
        "bourse_notices": notices,
        "ammc_press": ammc_press,
        "ammc_financial_statements": ammc_fs,
        "warnings": warnings,
    }

    (DATA / "latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    write_csv(DATA / "universe.csv", universe,
              ["ticker","issuer","compartment","shares","instrument_url","source"])
    write_csv(DATA / "market.csv", market,
              ["ticker","issuer","instrument","status","reference","open","close","quantity",
               "volume_mad","change_pct","high","low","bid","ask","bid_qty","ask_qty",
               "market_cap","transactions","shares","compartment","source"])

    news = notices + ammc_press + ammc_fs
    write_csv(DATA / "news.csv", news, ["date","title","url","kind","source_page"])

    hist_count = update_history(market, generated.date().isoformat())
    status = {
        "ok": True,
        "generated_at_utc": generated.isoformat(),
        "universe_count": len(universe),
        "market_valid_close": len(valid_market),
        "history_rows": hist_count,
        "news_rows": len(news),
    }
    (DATA / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(status, ensure_ascii=False))

if __name__ == "__main__":
    main()
