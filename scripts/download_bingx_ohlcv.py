#!/usr/bin/env python3
"""Download BingX USDT perpetual 15m OHLCV for SAM EDGE research.

Public market-data endpoint; no API key is used. Output layout is compatible
with core_engine_v15.load_coin(): DATA_ROOT/COIN/*.zip containing CSV columns
open_time, open, high, low, close, volume.

Example:
  python scripts/download_bingx_ohlcv.py --start 2024-01-01 --end 2026-10-30
  python scripts/download_bingx_ohlcv.py --start 2024-01-01 --end 2026-10-30 --coins BTC ETH SOL

This only fetches candles. It does not run a backtest or imply profitability.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
DEFAULT_COINS = ["ADA", "AVAX", "BNB", "BTC", "CRV", "DOGE", "ETH",
                 "FET", "LINK", "SOL", "SUI", "UNI", "XRP"]
INTERVAL = "15m"
INTERVAL_MS = 15 * 60 * 1000
MAX_LIMIT = 1000  # observed response cap; paginate backward by endTime
USER_AGENT = "SAM-EDGE-research-data-fetcher/1.1"


def ms_from_date(value: str, end: bool = False) -> int:
    dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000) + (24 * 60 * 60 * 1000 - 1 if end else 0)


def fetch_page(session: requests.Session, symbol: str, start_ms: int, end_ms: int):
    params = {
        "symbol": f"{symbol}-USDT",
        "interval": INTERVAL,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": MAX_LIMIT,
    }
    response = session.get(BASE_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code", 0) not in (0, "0", None):
        raise RuntimeError(f"BingX API error for {symbol}: {payload}")
    data = payload.get("data")
    if data is None:
        raise RuntimeError(f"Unexpected BingX response for {symbol}: {payload}")
    rows = []
    for item in data:
        # Kline arrays: time, open, high, low, close, volume, closeTime, ...
        if isinstance(item, (list, tuple)):
            if len(item) < 6:
                continue
            row = item[:6]
        elif isinstance(item, dict):
            row = [
                item.get("time", item.get("openTime")),
                item.get("open"), item.get("high"), item.get("low"),
                item.get("close"), item.get("volume"),
            ]
        else:
            continue
        if any(v is None for v in row):
            continue
        rows.append([int(float(row[0]))] + [float(v) for v in row[1:6]])
    return rows


def download_coin(session, coin, start_ms, end_ms, pause, max_pages=None):
    all_rows = {}
    cursor_end = end_ms
    pages = 0
    while cursor_end >= start_ms:
        rows = fetch_page(session, coin, start_ms, cursor_end)
        pages += 1
        if not rows:
            break
        valid_rows = [r for r in rows if start_ms <= r[0] <= cursor_end]
        if not valid_rows:
            raise RuntimeError(
                f"BingX returned no candles within requested window for {coin}; "
                f"requested end_ms={cursor_end}, response min_ms={min(r[0] for r in rows)}, "
                f"max_ms={max(r[0] for r in rows)}. API may be ignoring endTime."
            )
        for row in valid_rows:
            all_rows[row[0]] = row
        oldest = min(r[0] for r in valid_rows)
        print(
            f"[{coin}] page={pages} rows={len(valid_rows)} "
            f"page_first_ms={oldest} page_last_ms={max(r[0] for r in valid_rows)}",
            flush=True,
        )
        if oldest <= start_ms:
            break
        next_end = oldest - INTERVAL_MS
        if next_end >= cursor_end:
            raise RuntimeError(
                f"Backward pagination did not advance for {coin}: "
                f"cursor_end={cursor_end}, oldest={oldest}"
            )
        cursor_end = next_end
        if max_pages is not None and pages >= max_pages:
            break
        time.sleep(pause)
    ordered = [all_rows[k] for k in sorted(all_rows)]
    return ordered, pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="UTC start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC end date YYYY-MM-DD (inclusive)")
    parser.add_argument("--coins", nargs="+", default=DEFAULT_COINS)
    parser.add_argument("--out", default="data/ohlcv_15m")
    parser.add_argument("--pause", type=float, default=0.06, help="Seconds between requests")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional smoke-test limit per coin")
    args = parser.parse_args()
    start_ms = ms_from_date(args.start)
    end_ms = ms_from_date(args.end, end=True)
    if start_ms >= end_ms:
        parser.error("--start must be earlier than --end")
    if args.pause < 0:
        parser.error("--pause cannot be negative")

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    manifest = {
        "exchange": "BingX USDT perpetual swaps",
        "endpoint": BASE_URL,
        "interval": INTERVAL,
        "start_utc": args.start,
        "end_utc_inclusive": args.end,
        "symbols": [f"{c.upper()}-USDT" for c in args.coins],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "files": {},
        "warnings": [
            "Candles may be unavailable for newly listed or delisted symbols.",
            "This dataset contains OHLCV only; funding, fees, and slippage must be modeled separately.",
            "Inspect candle coverage, pagination, and gaps before treating results as a valid backtest.",
        ],
    }

    for raw_coin in args.coins:
        coin = raw_coin.upper().replace("-USDT", "")
        print(f"[{coin}] downloading {args.start} through {args.end} UTC ...", flush=True)
        rows, pages = download_coin(session, coin, start_ms, end_ms, args.pause, args.max_pages)
        if not rows:
            print(f"[{coin}] WARNING: no candles returned", flush=True)
            manifest["files"][coin] = {"rows": 0, "pages": pages, "file": None}
            continue
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["open_time", "open", "high", "low", "close", "volume"])
        writer.writerows(rows)
        out_dir = root / coin
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{coin}_15m.zip"
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{coin}_15m.csv", csv_buffer.getvalue())
        gaps = sum(1 for a, b in zip(rows, rows[1:]) if b[0] - a[0] != INTERVAL_MS)
        manifest["files"][coin] = {
            "rows": len(rows), "pages": pages, "file": str(out_path),
            "first_open_time_ms": rows[0][0], "last_open_time_ms": rows[-1][0],
            "gap_count": gaps,
        }
        print(f"[{coin}] rows={len(rows)} pages={pages} gaps={gaps} first_ms={rows[0][0]} last_ms={rows[-1][0]} file={out_path}", flush=True)

    manifest_path = root / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest written: {manifest_path}")
    print("Download complete. Check row counts, first/last timestamps, coverage and gaps before backtesting.")


if __name__ == "__main__":
    main()
