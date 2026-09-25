#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Relais GitHub — Bourse de Casablanca / AMMC
Version finale basée sur le package `casabourse` 0.1.5.

Principe:
- les données marché proviennent de casablanca-bourse.com via le package Casabourse;
- l'univers est dynamique, jamais codé en dur;
- les données brutes sont conservées pour audit;
- `latest.json` n'est remplacé que si les contrôles qualité sont suffisants;
- les communiqués AMMC sont collectés séparément et ne bloquent pas le marché.
"""

from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
import casabourse as cb

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

SOURCE_BOURSE = "https://www.casablanca-bourse.com/"
SOURCE_ACTIONS = "https://www.casablanca-bourse.com/en/marches-produits/actions"
SOURCE_AMMC_PRESS = "https://www.ammc.ma/fr/communiques-presse-emetteurs"
SOURCE_AMMC_FS = "https://www.ammc.ma/fr/liste-etats-financiers-emetteurs"

def norm(x: Any) -> str:
    s = "" if x is None else str(x)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()
    return s

def json_safe(v: Any):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if pd.isna(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat()
    if isinstance(v, (dict, list, tuple, str, int, float, bool)):
        return v
    try:
        return v.item()
    except Exception:
        return str(v)

def df_records(df: pd.DataFrame):
    return [
        {str(k): json_safe(v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]

def find_col(df: pd.DataFrame, candidates):
    cols = list(df.columns)
    ncols = {c: norm(c) for c in cols}
    # exact first
    for cand in candidates:
        nc = norm(cand)
        for c, n in ncols.items():
            if n == nc:
                return c
    # then contains
    for cand in candidates:
        nc = norm(cand)
        for c, n in ncols.items():
            if nc and (nc in n or n in nc):
                return c
    return None

def first_value(row: pd.Series, col):
    if col is None:
        return None
    return json_safe(row.get(col))

def dump_debug(name, df):
    df.to_csv(DATA/f"{name}.csv", index=False, encoding="utf-8-sig")
    (DATA/f"{name}_columns.json").write_text(
        json.dumps([str(c) for c in df.columns], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def collect_market():
    print("1) Récupération officielle via casabourse…")
    live = cb.get_live_market_data()
    instruments = cb.get_available_instrument()

    if not isinstance(live, pd.DataFrame) or live.empty:
        raise RuntimeError("casabourse.get_live_market_data() n'a renvoyé aucune donnée.")
    if not isinstance(instruments, pd.DataFrame) or instruments.empty:
        raise RuntimeError("casabourse.get_available_instrument() n'a renvoyé aucun instrument.")

    dump_debug("raw_live_market", live)
    dump_debug("raw_instruments", instruments)

    ticker_live = find_col(live, [
        "ticker","symbol","code","instrument","code instrument",
        "field code","field_code","code societe"
    ])
    name_live = find_col(live, [
        "libelle","libelle fr","name","nom","instrument name","societe","emetteur"
    ])
    close_col = find_col(live, [
        "cours courant","field cours courant","field_cours_courant",
        "dernier cours","last","close","cours"
    ])
    change_col = find_col(live, [
        "variation","variation veille","field var veille","field_var_veille",
        "change","var"
    ])
    open_col = find_col(live, ["ouverture","opening price","open","openingprice"])
    high_col = find_col(live, ["plus haut","high","high price","highprice"])
    low_col = find_col(live, ["plus bas","low","low price","lowprice"])
    volume_col = find_col(live, [
        "volume","volume echange","cumul volume echange","cumulvolumeechange"
    ])
    qty_col = find_col(live, [
        "quantite","titres echanges","cumul titres echanges","cumultitresechanges"
    ])
    trades_col = find_col(live, [
        "transactions","total trades","totaltrades","nombre transactions"
    ])
    cap_col = find_col(live, ["capitalisation","market cap","marketcap"])
    bid_col = find_col(live, ["bid","meilleur achat","meilleur prix achat"])
    ask_col = find_col(live, ["ask","meilleur vente","meilleur prix vente"])

    if close_col is None:
        raise RuntimeError(
            "Colonne de cours introuvable. Voir data/raw_live_market_columns.json pour audit."
        )

    # Universe fields
    ticker_inst = find_col(instruments, ["ticker","symbol","code","code instrument"])
    name_inst = find_col(instruments, ["libelle","name","nom","instrument","societe","emetteur"])
    if ticker_inst is None:
        # Sometimes one column combines ticker/name; keep raw and derive from live as fallback.
        print("⚠ Ticker non identifié dans l'univers brut; univers dérivé du live market.")

    universe = []
    seen = set()
    if ticker_inst is not None:
        for _, row in instruments.iterrows():
            t = first_value(row, ticker_inst)
            if t is None:
                continue
            t = str(t).strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{2,8}", t):
                continue
            if t in seen:
                continue
            seen.add(t)
            universe.append({
                "ticker": t,
                "issuer": first_value(row, name_inst),
                "source": SOURCE_ACTIONS,
            })

    market = []
    live_seen = set()
    for _, row in live.iterrows():
        t = first_value(row, ticker_live)
        if t is None:
            # Try extracting a ticker-looking token from the first few columns.
            for c in live.columns[:5]:
                v = first_value(row, c)
                sv = str(v or "").strip().upper()
                if re.fullmatch(r"[A-Z0-9]{2,8}", sv):
                    t = sv
                    break
        if t is None:
            continue
        t = str(t).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,8}", t):
            continue
        if t in live_seen:
            continue
        live_seen.add(t)

        market.append({
            "ticker": t,
            "issuer": first_value(row, name_live),
            "close": first_value(row, close_col),
            "change_pct": first_value(row, change_col),
            "open": first_value(row, open_col),
            "high": first_value(row, high_col),
            "low": first_value(row, low_col),
            "volume_mad": first_value(row, volume_col),
            "quantity": first_value(row, qty_col),
            "transactions": first_value(row, trades_col),
            "market_cap": first_value(row, cap_col),
            "bid": first_value(row, bid_col),
            "ask": first_value(row, ask_col),
            "source": SOURCE_BOURSE,
        })

    if not universe:
        universe = [
            {"ticker": r["ticker"], "issuer": r.get("issuer"), "source": SOURCE_ACTIONS}
            for r in market
        ]

    # Quality checks
    universe_count = len({u["ticker"] for u in universe})
    market_count = len({m["ticker"] for m in market})
    valid_price = sum(
        1 for m in market
        if isinstance(m.get("close"), (int, float)) and m.get("close") not in (None, 0)
    )
    if valid_price == 0:
        # Handle numeric strings
        valid_price = 0
        for m in market:
            try:
                if float(str(m.get("close")).replace(" ","").replace(",", ".")) > 0:
                    valid_price += 1
            except Exception:
                pass

    ratio = valid_price / max(universe_count, 1)

    if universe_count < 60 or market_count < 50 or ratio < 0.60:
        qstatus = "BLOQUE"
    elif ratio < 0.90:
        qstatus = "A_VERIFIER"
    else:
        qstatus = "OK"

    quality = {
        "status": qstatus,
        "universe_count": universe_count,
        "market_count": market_count,
        "valid_price_count": valid_price,
        "price_coverage_ratio": round(ratio, 4),
        "duplicates_universe": universe_count != len(universe),
        "duplicates_market": market_count != len(market),
        "mapped_columns": {
            "ticker": str(ticker_live),
            "issuer": str(name_live),
            "close": str(close_col),
            "change_pct": str(change_col),
            "open": str(open_col),
            "high": str(high_col),
            "low": str(low_col),
            "volume_mad": str(volume_col),
            "quantity": str(qty_col),
            "transactions": str(trades_col),
            "market_cap": str(cap_col),
            "bid": str(bid_col),
            "ask": str(ask_col),
        },
    }
    return universe, market, quality

def scrape_ammc(url, kind, max_pages=4):
    rows = []
    seen = set()
    headers = {"User-Agent":"Mozilla/5.0"}
    for page in range(max_pages):
        page_url = url if page == 0 else f"{url}?page={page}"
        try:
            r = requests.get(page_url, headers=headers, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"⚠ AMMC page {page_url} non récupérée: {e}")
            continue
        sp = BeautifulSoup(r.text, "html.parser")
        for a in sp.find_all("a", href=True):
            title = re.sub(r"\s+"," ",a.get_text(" ",strip=True)).strip()
            if len(title) < 8:
                continue
            block = a
            context = title
            for _ in range(5):
                block = getattr(block, "parent", None)
                if block is None:
                    break
                context = re.sub(r"\s+"," ",block.get_text(" ",strip=True)).strip()
                if re.search(r"\b\d{2}/\d{2}/\d{4}\b", context):
                    break
            dm = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", context)
            if not dm:
                continue
            href = requests.compat.urljoin(url, a["href"])
            k = (dm.group(1), title, href)
            if k in seen:
                continue
            seen.add(k)
            rows.append({
                "date": dm.group(1),
                "title": title,
                "url": href,
                "kind": kind,
                "source_page": page_url,
            })
    return rows

def append_history(market, date_str):
    path = DATA/"history.csv"
    fields = [
        "date","ticker","close","change_pct","open","high","low",
        "volume_mad","quantity","transactions","market_cap"
    ]
    existing = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("date") and row.get("ticker"):
                    existing[(row["date"],row["ticker"])] = row
    for m in market:
        existing[(date_str,m["ticker"])] = {
            "date": date_str,
            "ticker": m["ticker"],
            "close": m.get("close"),
            "change_pct": m.get("change_pct"),
            "open": m.get("open"),
            "high": m.get("high"),
            "low": m.get("low"),
            "volume_mad": m.get("volume_mad"),
            "quantity": m.get("quantity"),
            "transactions": m.get("transactions"),
            "market_cap": m.get("market_cap"),
        }
    rows = list(existing.values())
    rows.sort(key=lambda x:(x["date"],x["ticker"]))
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def main():
    now = datetime.now(timezone.utc)
    universe, market, quality = collect_market()

    ammc_press = scrape_ammc(SOURCE_AMMC_PRESS, "AMMC - communiqué")
    ammc_fs = scrape_ammc(SOURCE_AMMC_FS, "AMMC - états financiers")

    candidate = {
        "schema_version": 3,
        "generated_at_utc": now.isoformat(),
        "source_policy": {
            "market_primary": "Bourse de Casablanca via casabourse 0.1.5",
            "market_official_domain": SOURCE_BOURSE,
            "universe_official_page": SOURCE_ACTIONS,
            "fundamentals_primary": "AMMC",
            "fundamentals_status": "publications collectées; ratios non inventés si non parsés",
        },
        "quality": quality,
        "universe": universe,
        "market": market,
        "ammc_press": ammc_press,
        "ammc_financial_statements": ammc_fs,
    }

    # Always publish candidate + diagnostics for audit.
    (DATA/"latest_candidate.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA/"quality_status.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    write_csv(DATA/"universe.csv", universe, ["ticker","issuer","source"])
    write_csv(DATA/"market.csv", market, [
        "ticker","issuer","close","change_pct","open","high","low",
        "volume_mad","quantity","transactions","market_cap","bid","ask","source"
    ])
    write_csv(DATA/"ammc_news.csv", ammc_press + ammc_fs,
              ["date","title","url","kind","source_page"])

    # Human-check sample: representative tickers when present, then fill to 10.
    preferred = ["ATW","BCP","IAM","BOA","CIH","TGC","ADH","AKT","MNG","LBV"]
    by = {m["ticker"]:m for m in market}
    sample = [by[t] for t in preferred if t in by]
    if len(sample) < 10:
        for t in sorted(by):
            if t not in {x["ticker"] for x in sample}:
                sample.append(by[t])
            if len(sample) >= 10:
                break
    write_csv(DATA/"audit_sample.csv", sample[:10], [
        "ticker","issuer","close","change_pct","volume_mad","market_cap","source"
    ])

    # Only replace trusted `latest.json` when quality is not blocked.
    if quality["status"] != "BLOQUE":
        (DATA/"latest.json").write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        append_history(market, now.date().isoformat())
        print("✓ latest.json publié")
    else:
        print("✗ Contrôle qualité BLOQUÉ : latest.json précédent conservé")

    print(json.dumps({
        "quality_status": quality["status"],
        "universe_count": quality["universe_count"],
        "market_count": quality["market_count"],
        "valid_price_count": quality["valid_price_count"],
        "price_coverage_ratio": quality["price_coverage_ratio"],
        "ammc_press": len(ammc_press),
        "ammc_financial_statements": len(ammc_fs),
    }, ensure_ascii=False))

    if quality["status"] == "BLOQUE":
        raise SystemExit(2)

if __name__ == "__main__":
    main()
