#!/usr/bin/env python3
"""Research-only MTF replay for SAM EDGE V15 cohort 101-128.
Fetches public Binance USD-M klines; does not place orders or touch production.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import requests
import pandas as pd

API = "https://fapi.binance.com/fapi/v1/klines"
INTERVALS = {"15m": 15, "30m": 30, "1h": 60, "4h": 240}
HEADERS = {"User-Agent": "SAM-EDGE-MTF-Research/1.0"}

def fetch_one_archive(symbol, interval, kind, period):
    if kind == "monthly":
        filename=f"{symbol}-{interval}-{period}.zip"
    else:
        filename=f"{symbol}-{interval}-{period}.zip"
    url=ARCHIVE.format(kind=kind,symbol=symbol,interval=interval,filename=filename)
    try:
        r=requests.get(url,headers=HEADERS,timeout=35)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names: return None
            raw=pd.read_csv(z.open(names[0]),header=None)
        if raw.empty or raw.shape[1]<6: return None
        if not str(raw.iloc[0,0]).strip().replace(".","",1).isdigit():
            raw=raw.iloc[1:].reset_index(drop=True)
        x=pd.DataFrame({"open_ms":pd.to_numeric(raw.iloc[:,0],errors="coerce"),"open":pd.to_numeric(raw.iloc[:,1],errors="coerce"),"high":pd.to_numeric(raw.iloc[:,2],errors="coerce"),"low":pd.to_numeric(raw.iloc[:,3],errors="coerce"),"close":pd.to_numeric(raw.iloc[:,4],errors="coerce"),"volume":pd.to_numeric(raw.iloc[:,5],errors="coerce")}).dropna()
        x["open_ms"]=x["open_ms"].astype("int64")
        x["close_ms"]=x["open_ms"]+INTERVALS[interval]*60_000-1
        return x
    except Exception as e:
        print(f"ARCHIVE_ERROR {symbol} {interval} {period}: {type(e).__name__}: {e}",flush=True)
        return None

def fetch_klines(symbol, interval, start_ms, end_ms):
    start=pd.to_datetime(start_ms,unit="ms",utc=True)
    end=pd.to_datetime(end_ms-1,unit="ms",utc=True)
    periods=[]
    month=start.replace(day=1)
    last_month=end.replace(day=1)
    now=pd.Timestamp.now(tz="UTC")
    while month<=last_month:
        # Completed historical months use monthly archives; current month uses daily archives.
        next_month=(month+pd.offsets.MonthBegin(1)).normalize()
        if month < now.replace(day=1):
            periods.append(("monthly",month.strftime("%Y-%m")))
        else:
            day=max(start.normalize(),month)
            while day<=end.normalize():
                periods.append(("daily",day.strftime("%Y-%m-%d")))
                day=day+pd.Timedelta(days=1)
        month=next_month
    pieces=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(fetch_one_archive,symbol,interval,kind,period) for kind,period in periods]
        for fut in futures:
            piece=fut.result()
            if piece is not None and not piece.empty: pieces.append(piece)
    if not pieces: return pd.DataFrame()
    x=pd.concat(pieces,ignore_index=True).drop_duplicates("open_ms").sort_values("open_ms")
    x=x[(x.open_ms>=start_ms)&(x.open_ms<end_ms)].reset_index(drop=True)
    return x

