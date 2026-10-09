#!/usr/bin/env python3
"""Download public BingX USDT perpetual 15m candles for the Core V2 research replay.
No API key is required; this script is read-only and never places orders.
"""
from __future__ import annotations
import argparse
import io
import time
import zipfile
from pathlib import Path

import requests

import ccxt
import pandas as pd

DEFAULT_COINS = ["ADA","AVAX","BNB","BTC","CRV","DOGE","ETH","FET","LINK","SOL","SUI","UNI","XRP"]
TF = "15m"
TF_MS = 15 * 60 * 1000
BINANCE_ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/15m/{symbol}-15m-{month}.zip"

def fetch_binance_archive(base: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fallback historical source for periods BingX's public OHLCV API will not serve."""
    start = pd.to_datetime(start_ms, unit="ms", utc=True)
    end = pd.to_datetime(end_ms - 1, unit="ms", utc=True)
    cur = start.replace(day=1)
    pieces = []
    session = requests.Session()
    session.headers.update({"User-Agent": "SAM-EDGE-Core-V3-Historical-Research/1.0"})
    while cur <= end:
        month = cur.strftime("%Y-%m")
        symbol = f"{base}USDT"
        url = BINANCE_ARCHIVE.format(symbol=symbol, month=month)
        try:
            response = session.get(url, timeout=60)
            if response.status_code == 404:
                cur = (cur + pd.offsets.MonthBegin(1)).normalize()
                continue
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                if not csv_names:
                    raise ValueError(f"archive has no CSV: {url}")
                raw = pd.read_csv(archive.open(csv_names[0]), header=None)
            if raw.shape[1] < 6:
                raise ValueError(f"archive has fewer than 6 columns: {url}")
            if not str(raw.iloc[0, 0]).strip().replace(".", "", 1).isdigit():
                raw = raw.iloc[1:].reset_index(drop=True)
            piece = pd.DataFrame({
                "timestamp_ms": pd.to_numeric(raw.iloc[:, 0], errors="coerce"),
                "open": pd.to_numeric(raw.iloc[:, 1], errors="coerce"),
                "high": pd.to_numeric(raw.iloc[:, 2], errors="coerce"),
                "low": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
                "close": pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
                "volume": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
            }).dropna()
            pieces.append(piece)
            print(f"ARCHIVE {symbol} {month}: {len(piece)} candles")
        except Exception as exc:
            print(f"ARCHIVE ERROR {symbol} {month}: {type(exc).__name__}: {exc}")
        cur = (cur + pd.offsets.MonthBegin(1)).normalize()
    if not pieces:
        return pd.DataFrame(columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
    out = pd.concat(pieces, ignore_index=True).drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
    return out[(out["timestamp_ms"] >= start_ms) & (out["timestamp_ms"] < end_ms)].reset_index(drop=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS), help="Comma-separated base coin symbols")
    ap.add_argument("--bars", type=int, default=10000, help="Approximate number of 15m candles when dates are not supplied")
    ap.add_argument("--start-date", default=None, help="UTC start date inclusive, YYYY-MM-DD")
    ap.add_argument("--end-date", default=None, help="UTC end date inclusive, YYYY-MM-DD")
    ap.add_argument("--data", default="data", help="Output root folder")
    args = ap.parse_args()
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 20000, "options": {"defaultType": "swap"}})
    markets = exchange.load_markets()
    now_ms = exchange.milliseconds()
    # Exclude the currently forming candle to prevent a partial candle entering replay.
    live_end_ms = (now_ms // TF_MS) * TF_MS
    if args.start_date or args.end_date:
        if not (args.start_date and args.end_date):
            raise SystemExit("Pass both --start-date and --end-date, or neither.")
        start_ms = int(pd.Timestamp(args.start_date, tz="UTC").timestamp() * 1000)
        requested_end = int((pd.Timestamp(args.end_date, tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000)
        end_ms = min(requested_end, live_end_ms)
        if start_ms >= end_ms:
            raise SystemExit("Invalid historical date range.")
    else:
        end_ms = live_end_ms
        start_ms = end_ms - args.bars * TF_MS
    root = Path(args.data)
    root.mkdir(parents=True, exist_ok=True)
    report = []
    for base in [x.strip().upper() for x in args.coins.split(",") if x.strip()]:
        symbol = f"{base}/USDT:USDT"
        if symbol not in markets:
            report.append((base, "SKIP_NO_MARKET", 0))
            print(f"SKIP {base}: {symbol} not found in BingX markets")
            continue
        rows = []
        cursor = start_ms
        while cursor < end_ms:
            try:
                batch = exchange.fetch_ohlcv(symbol, timeframe=TF, since=cursor, limit=900)
            except Exception as exc:
                print(f"FETCH ERROR {symbol} since={cursor}: {type(exc).__name__}: {exc}")
                break
            if not batch:
                break
            rows.extend(batch)
            last_ts = int(batch[-1][0])
            next_cursor = last_ts + TF_MS
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(batch) < 2:
                break
            time.sleep(exchange.rateLimit / 1000)
        df = pd.DataFrame(rows, columns=["timestamp_ms","open","high","low","close","volume"])
        if not df.empty:
            df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
            df = df[(df["timestamp_ms"] >= start_ms) & (df["timestamp_ms"] < end_ms)]
        # BingX's public OHLCV endpoint often does not expose deep history. For the
        # fixed historical research window, use public Binance Futures monthly archives
        # if BingX yielded fewer than 3,300 candles; source is recorded in each output.
        source = "bingx_ccxt"
        if (args.start_date and args.end_date) and len(df) < 3300:
            archive_df = fetch_binance_archive(base, start_ms, end_ms)
            if len(archive_df) > len(df):
                df = archive_df
                source = "binance_futures_archive"
        if df.empty:
            report.append((base, "NO_DATA", 0))
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True).astype(str)
        df["data_source"] = source
        df = df[["timestamp","open","high","low","close","volume","data_source"]]
        folder = root / base
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"bingx_{base}_15m.csv"
        df.to_csv(path, index=False)
        status = "OK" if len(df) >= 3300 else "SHORT_HISTORY"
        report.append((base, status, len(df)))
        print(f"{status} {symbol}: {len(df)} candles source={source} -> {path}")
    print("\nSUMMARY")
    for row in report:
        print(f"{row[0]} | {row[1]} | candles={row[2]}")

if __name__ == "__main__":
    main()
