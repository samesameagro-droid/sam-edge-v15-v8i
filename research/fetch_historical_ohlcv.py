#!/usr/bin/env python3
"""Download public BingX USDT perpetual 15m candles for the Core V2 research replay.
No API key is required; this script is read-only and never places orders.
"""
from __future__ import annotations
import argparse
import time
from pathlib import Path

import ccxt
import pandas as pd

DEFAULT_COINS = ["ADA","AVAX","BNB","BTC","CRV","DOGE","ETH","FET","LINK","SOL","SUI","UNI","XRP"]
TF = "15m"
TF_MS = 15 * 60 * 1000

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS), help="Comma-separated base coin symbols")
    ap.add_argument("--bars", type=int, default=10000, help="Approximate number of 15m candles per coin (about 104 days)")
    ap.add_argument("--data", default="data", help="Output root folder")
    args = ap.parse_args()
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 20000, "options": {"defaultType": "swap"}})
    markets = exchange.load_markets()
    now_ms = exchange.milliseconds()
    # Exclude the currently forming candle to prevent a partial candle entering replay.
    end_ms = (now_ms // TF_MS) * TF_MS
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
        if not rows:
            report.append((base, "NO_DATA", 0))
            continue
        df = pd.DataFrame(rows, columns=["timestamp_ms","open","high","low","close","volume"])
        df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
        df = df[(df["timestamp_ms"] >= start_ms) & (df["timestamp_ms"] < end_ms)]
        df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True).astype(str)
        df = df[["timestamp","open","high","low","close","volume"]]
        folder = root / base
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"bingx_{base}_15m.csv"
        df.to_csv(path, index=False)
        status = "OK" if len(df) >= 3300 else "SHORT_HISTORY"
        report.append((base, status, len(df)))
        print(f"{status} {symbol}: {len(df)} candles -> {path}")
    print("\nSUMMARY")
    for row in report:
        print(f"{row[0]} | {row[1]} | candles={row[2]}")

if __name__ == "__main__":
    main()
