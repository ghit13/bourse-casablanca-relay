#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relais cloud V8 — sources officielles uniquement.

Bourse de Casablanca:
- univers actions
- dernier Bulletin de la cote "Marché au comptant" (PDF officiel)
- avis
AMMC:
- communiqués émetteurs
- états financiers

Le script n'utilise plus les pages live-market pour les cours.
"""

from __future__ import annotations
import csv, io, json, re, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests, urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pdfplumber

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

URL_UNIVERSE = "https://www.casablanca-bourse.com/en/marches-produits/actions"
URL_BULLETINS = "https://www.casablanca-bourse.com/market-data/bulletins-de-la-cote"
URL_NOTICES = "https://www.casablanca-bourse.com/marches-produits/avis-et-instructions/avis"
URL_AMMC_PRESS = "https://www.ammc.ma/fr/communiques-presse-emetteurs"
URL_AMMC_FS = "https://www.ammc.ma/fr/liste-etats-financiers-emetteurs"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"

def session():
    s = requests.Session()
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5,
                  status_forcelist=(429,500,502,503,504),
                  allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent":UA,"Accept-Language":"fr-FR,fr;q=0.9,en;q=0.8"})
    return s

S = session()

def clean(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()

def norm(x):
    s = unicodedata.normalize("NFKD", clean(x))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()

def num_fr(x):
    s = clean(x).replace("\u202f","").replace("\xa0","").replace(" ","")
    s = s.replace("MAD","").replace("%","").replace("−","-").replace("—","")
    if s in ("","-","N.T","NT"): return None
    if "," in s:
        s = s.replace(".","").replace(",",".")
    s = re.sub(r"[^0-9.+-]","",s)
    try: return float(s)
    except: return None

def get(url, binary=False):
    try:
        r = S.get(url, timeout=25)
        r.raise_for_status()
        return r.content if binary else r.text
    except requests.exceptions.SSLError:
        host = re.sub(r"^https?://","",url).split("/",1)[0].lower()
        if host not in ("www.casablanca-bourse.com","casablanca-bourse.com","media.casablanca-bourse.com"):
            raise
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = S.get(url, timeout=25, verify=False)
        r.raise_for_status()
        return r.content if binary else r.text

def soup(url):
    return BeautifulSoup(get(url), "html.parser")

def parse_universe():
    sp = soup(URL_UNIVERSE)
    out=[]
    for tr in sp.find_all("tr"):
        tds=tr.find_all("td")
        if len(tds)<4: continue
        ticker=clean(tds[0].get_text(" ",strip=True)).upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}",ticker): continue
        issuer=clean(tds[1].get_text(" ",strip=True))
        comp=clean(tds[2].get_text(" ",strip=True))
        shares=num_fr(tds[3].get_text(" ",strip=True))
        out.append({
            "ticker":ticker,"issuer":issuer,"compartment":comp,
            "shares":int(shares) if shares is not None else None,
            "instrument_url":f"https://www.casablanca-bourse.com/fr/live-market/instruments/{ticker}",
            "source":URL_UNIVERSE
        })
    seen=set(); dedup=[]
    for r in out:
        if r["ticker"] not in seen:
            seen.add(r["ticker"]); dedup.append(r)
    if len(dedup)<70:
        raise RuntimeError(f"Univers officiel incomplet: {len(dedup)} actions.")
    return dedup

def latest_cash_bulletin():
    sp = soup(URL_BULLETINS)
    candidates=[]
    for a in sp.find_all("a", href=True):
        href=a.get("href","")
        if ".pdf" not in href.lower(): continue
        block=a
        context=""
        for _ in range(5):
            block=getattr(block,"parent",None)
            if block is None: break
            context=clean(block.get_text(" ",strip=True))
            if re.search(r"\b\d{2}/\d{2}/\d{4}\b",context): break
        text=norm(context+" "+a.get_text(" ",strip=True))
        if "MARCHE AU COMPTANT" not in text and "MARCHE COMPTANT" not in text:
            continue
        dm=re.search(r"\b(\d{2}/\d{2}/\d{4})\b",context)
        if not dm: continue
        dt=datetime.strptime(dm.group(1),"%d/%m/%Y").date()
        candidates.append((dt,urljoin(URL_BULLETINS,href)))
    if not candidates:
        # fallback: any PDF near a "marché au comptant" text node
        for node in sp.find_all(string=re.compile("comptant", re.I)):
            block=node.parent
            for _ in range(5):
                if block is None: break
                links=block.find_all("a",href=True)
                for a in links:
                    href=a.get("href","")
                    if ".pdf" in href.lower():
                        ctx=clean(block.get_text(" ",strip=True))
                        dm=re.search(r"\b(\d{2}/\d{2}/\d{4})\b",ctx)
                        if dm:
                            dt=datetime.strptime(dm.group(1),"%d/%m/%Y").date()
                            candidates.append((dt,urljoin(URL_BULLETINS,href)))
                block=getattr(block,"parent",None)
    if not candidates:
        raise RuntimeError("Aucun PDF 'Marché au comptant' trouvé sur la page des bulletins.")
    candidates.sort(reverse=True)
    return candidates[0]

def normalize_designation(x):
    n=norm(x)
    for token in ["S A","SA","SOCIETE ANONYME","STE","SOCIETE","MAROC","GROUPE","GROUP"]:
        n=re.sub(rf"\b{re.escape(token)}\b"," ",n)
    return clean(n)

def match_ticker(designation, universe):
    d=normalize_designation(designation)
    best=None
    for u in universe:
        iss=normalize_designation(u["issuer"])
        if not iss or not d: continue
        score=0
        if d==iss: score=100
        elif d in iss or iss in d: score=90
        else:
            ds=set(d.split()); iset=set(iss.split())
            if ds and iset:
                score=100*len(ds&iset)/max(len(ds),len(iset))
        if best is None or score>best[0]:
            best=(score,u["ticker"])
    return best[1] if best and best[0]>=50 else None

def parse_bulletin_pdf(pdf_bytes, universe, bulletin_url, bulletin_date):
    rows=[]
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables=page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row)<16: continue
                    vals=[clean(x) for x in row]
                    # Data rows have capital in col0, designation around col11,
                    # open/close/high/low around cols12-15.
                    designation=vals[11] if len(vals)>11 else ""
                    close=num_fr(vals[13]) if len(vals)>13 else None
                    if not designation or close is None: continue
                    ticker=match_ticker(designation,universe)
                    if not ticker: continue
                    rec={
                        "ticker":ticker,
                        "issuer":next((u["issuer"] for u in universe if u["ticker"]==ticker),designation),
                        "instrument":designation,
                        "status":vals[16] if len(vals)>16 else None,
                        "reference":num_fr(vals[7]) if len(vals)>7 else None,
                        "open":num_fr(vals[12]) if len(vals)>12 else None,
                        "close":close,
                        "quantity":None,
                        "volume_mad":None,
                        "change_pct":None,
                        "high":num_fr(vals[14]) if len(vals)>14 else None,
                        "low":num_fr(vals[15]) if len(vals)>15 else None,
                        "bid":num_fr(vals[17]) if len(vals)>17 else None,
                        "ask":num_fr(vals[18]) if len(vals)>18 else None,
                        "bid_qty":None,"ask_qty":None,
                        "market_cap":None,
                        "transactions":None,
                        "bulletin_date":bulletin_date.isoformat(),
                        "source":bulletin_url
                    }
                    if rec["reference"] not in (None,0):
                        rec["change_pct"]=(rec["close"]/rec["reference"]-1)*100
                    rows.append(rec)
    # deduplicate by ticker
    by={}
    for r in rows: by[r["ticker"]]=r
    out=list(by.values())
    if len(out)<40:
        raise RuntimeError(f"Bulletin PDF parsé mais seulement {len(out)} cours reconnus.")
    return out

def scrape_dated_links(base_url, pages=5, kind="AMMC"):
    out=[];seen=set()
    for page in range(pages):
        url=base_url if page==0 else f"{base_url}?page={page}"
        try: sp=soup(url)
        except: continue
        for a in sp.find_all("a",href=True):
            title=clean(a.get_text(" ",strip=True))
            if len(title)<8: continue
            block=a; context=title
            for _ in range(5):
                block=getattr(block,"parent",None)
                if block is None: break
                context=clean(block.get_text(" ",strip=True))
                if re.search(r"\b\d{2}/\d{2}/\d{4}\b",context): break
            dm=re.search(r"\b(\d{2}/\d{2}/\d{4})\b",context)
            if not dm: continue
            href=urljoin(base_url,a.get("href",""))
            key=(dm.group(1),title,href)
            if key in seen: continue
            seen.add(key)
            out.append({"date":dm.group(1),"title":title,"url":href,"kind":kind,"source_page":url})
    return out

def write_csv(path, rows, fields):
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader()
        for r in rows:w.writerow(r)

def update_history(market, today):
    path=DATA/"history.csv"
    fields=["date","ticker","close","open","high","low","volume_mad","quantity","transactions","change_pct","market_cap"]
    existing=[]
    if path.exists():
        with path.open("r",newline="",encoding="utf-8-sig") as f:
            existing=list(csv.DictReader(f))
    keep={(r.get("date"),r.get("ticker")):r for r in existing if r.get("date") and r.get("ticker")}
    for m in market:
        keep[(today,m["ticker"])]={
            "date":today,"ticker":m["ticker"],"close":m.get("close"),"open":m.get("open"),
            "high":m.get("high"),"low":m.get("low"),"volume_mad":m.get("volume_mad"),
            "quantity":m.get("quantity"),"transactions":m.get("transactions"),
            "change_pct":m.get("change_pct"),"market_cap":m.get("market_cap")
        }
    cutoff=(datetime.now(timezone.utc).date()-timedelta(days=1100)).isoformat()
    final=[v for (d,_),v in keep.items() if d>=cutoff]
    final.sort(key=lambda r:(r["date"],r["ticker"]))
    write_csv(path,final,fields)
    return len(final)

def main():
    generated=datetime.now(timezone.utc)
    universe=parse_universe()
    bdate,burl=latest_cash_bulletin()
    print("Dernier bulletin marché au comptant :",bdate,burl)
    pdf_bytes=get(burl,binary=True)
    market=parse_bulletin_pdf(pdf_bytes,universe,burl,bdate)

    # add shares / derived market cap
    umap={u["ticker"]:u for u in universe}
    for m in market:
        u=umap.get(m["ticker"],{})
        m["compartment"]=u.get("compartment")
        m["shares"]=u.get("shares")
        if m.get("close") is not None and u.get("shares"):
            m["market_cap"]=m["close"]*u["shares"]

    notices=scrape_dated_links(URL_NOTICES,pages=3,kind="Bourse - avis")
    ammc_press=scrape_dated_links(URL_AMMC_PRESS,pages=6,kind="AMMC - communiqué")
    ammc_fs=scrape_dated_links(URL_AMMC_FS,pages=6,kind="AMMC - états financiers")

    latest={
        "schema_version":2,
        "generated_at_utc":generated.isoformat(),
        "sources":{
            "universe":URL_UNIVERSE,
            "bulletins":URL_BULLETINS,
            "latest_cash_bulletin":burl,
            "bourse_notices":URL_NOTICES,
            "ammc_press":URL_AMMC_PRESS,
            "ammc_financial_statements":URL_AMMC_FS,
        },
        "coverage":{
            "universe_count":len(universe),
            "market_rows":len(market),
            "market_valid_close":sum(1 for m in market if m.get("close") is not None),
            "ammc_press_count":len(ammc_press),
            "ammc_financial_statements_count":len(ammc_fs),
            "bourse_notices_count":len(notices),
        },
        "bulletin_date":bdate.isoformat(),
        "universe":universe,
        "market":market,
        "bourse_notices":notices,
        "ammc_press":ammc_press,
        "ammc_financial_statements":ammc_fs,
        "warnings":["Les volumes/transactions peuvent nécessiter une source complémentaire si absents du tableau PDF principal."]
    }
    (DATA/"latest.json").write_text(json.dumps(latest,ensure_ascii=False,indent=2),encoding="utf-8")
    write_csv(DATA/"universe.csv",universe,["ticker","issuer","compartment","shares","instrument_url","source"])
    write_csv(DATA/"market.csv",market,["ticker","issuer","instrument","status","reference","open","close","quantity",
                                        "volume_mad","change_pct","high","low","bid","ask","market_cap","transactions",
                                        "shares","compartment","bulletin_date","source"])
    news=notices+ammc_press+ammc_fs
    write_csv(DATA/"news.csv",news,["date","title","url","kind","source_page"])
    hist=update_history(market,bdate.isoformat())
    status={"ok":True,"generated_at_utc":generated.isoformat(),"bulletin_date":bdate.isoformat(),
            "universe_count":len(universe),"market_valid_close":len(market),"history_rows":hist,"news_rows":len(news)}
    (DATA/"status.json").write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(status,ensure_ascii=False))

if __name__=="__main__":
    main()
