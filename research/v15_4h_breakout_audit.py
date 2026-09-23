from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(".")
JOURNAL = ROOT / "v15_forward_test_master_journal.csv"
OUT = ROOT / "v15_4h_breakout_audit"
OUT.mkdir(exist_ok=True)
BINGX = "https://open-api.bingx.com"
INTERVAL_MS = {"4h": 14_400_000}
SESSION = requests.Session()
RETRY = Retry(total=5, connect=5, read=3, backoff_factor=0.4,
              status_forcelist=(429, 500, 502, 503, 504),
              allowed_methods=frozenset(["GET"]))
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY, pool_connections=20, pool_maxsize=20))
SESSION.headers.update({"X-SOURCE-KEY": "BX-AI-SKILL", "User-Agent": "SAM-EDGE-V15-4H-BREAKOUT-AUDIT/1.0"})


def bingx_klines(asset: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    symbol = asset.replace("/USDT:USDT", "-USDT").replace("/", "-").upper()
    step = INTERVAL_MS["4h"]
    cur = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows, seen = [], set()
    while cur <= end_ms:
        params = {
            "symbol": symbol, "interval": "4h", "startTime": cur,
            "endTime": end_ms, "limit": 1000, "timestamp": int(time.time() * 1000)
        }
        payload = None
        last_err = None
        for path in ("/openApi/swap/v3/quote/klines", "/openApi/swap/v2/quote/klines"):
            try:
                r = SESSION.get(BINGX + path, params=params, timeout=25)
                r.raise_for_status()
                j = r.json()
                if int(j.get("code", -1)) != 0:
                    raise RuntimeError(str(j.get("msg")))
                payload = j.get("data", [])
                break
            except Exception as e:
                last_err = e
        if payload is None:
            raise last_err
        page = []
        for q in payload or []:
            try:
                if isinstance(q, (list, tuple)):
                    row = [int(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[5])]
                else:
                    row = [int(q.get("openTime") or q.get("time") or q.get("timestamp")),
                           float(q["open"]), float(q["high"]), float(q["low"]),
                           float(q["close"]), float(q.get("volume", 0))]
            except Exception:
                continue
            if row[0] not in seen:
                seen.add(row[0]); page.append(row)
        page.sort(key=lambda z: z[0])
        if not page:
            break
        rows.extend(page)
        nxt = page[-1][0] + step
        if nxt <= cur:
            break
        cur = nxt
    if not rows:
        return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
    df = pd.DataFrame(rows, columns=["ms","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    return df.drop(columns="ms").sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def load_journal() -> pd.DataFrame:
    d = pd.read_csv(JOURNAL, parse_dates=["signal_time", "closed_at"])
    return d.sort_values("trade_no").reset_index(drop=True)


def classify_trade(t: pd.Series, h4: pd.DataFrame, lookback: int) -> dict:
    ts = pd.Timestamp(t.signal_time)
    # Only candles whose CLOSE is already known at signal time are eligible.
    completed = h4[h4["timestamp"] + pd.Timedelta(hours=4) <= ts].copy()
    if len(completed) < lookback + 2:
        return {"status": "INSUFFICIENT_4H", "breakout": False, "pullback": False}

    completed["end"] = completed["timestamp"] + pd.Timedelta(hours=4)
    side = str(t.side).upper()

    # The most recently completed candle is the candidate breakout candle.
    b = completed.iloc[-1]
    prior = completed.iloc[-(lookback + 1):-1]
    if side == "LONG":
        level = float(prior["high"].max())
        breakout = float(b["close"]) > level
    else:
        level = float(prior["low"].min())
        breakout = float(b["close"]) < level

    # Fresh breakout means the signal occurs during the FIRST 4h candle
    # immediately following the completed breakout candle.
    next_start = b["end"]
    next_end = next_start + pd.Timedelta(hours=4)
    fresh = breakout and (next_start <= ts < next_end)

    # Breakout + completed-candle pullback:
    # the candle immediately after breakout must retest the breakout level,
    # then the 15m V15 signal occurs in the following 4h candle.
    pullback = False
    if breakout and len(completed) >= lookback + 3:
        pb = completed.iloc[-1]
        # At signal time, pb is the most recently completed candle.
        # The breakout candle is therefore the one immediately before pb.
        bo = completed.iloc[-2]
        prior2 = completed.iloc[-(lookback + 2):-2]
        if side == "LONG":
            level2 = float(prior2["high"].max())
            bo_ok = float(bo["close"]) > level2
            retest = float(pb["low"]) <= level2 * 1.0025
        else:
            level2 = float(prior2["low"].min())
            bo_ok = float(bo["close"]) < level2
            retest = float(pb["high"]) >= level2 * 0.9975
        # Signal is in the candle after the completed pullback candle.
        pullback = bo_ok and retest and (pb["end"] <= ts < pb["end"] + pd.Timedelta(hours=4))

    return {
        "status": "OK",
        "breakout": bool(fresh),
        "pullback": bool(pullback),
        "breakout_level": level,
        "last_completed_4h_open": b["timestamp"].isoformat(),
        "last_completed_4h_close": float(b["close"]),
    }


def stats(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "wr_pct": 0.0, "net_R": 0.0,
                "pf": None, "expectancy_R": 0.0}
    r = rows["R"].astype(float)
    wins, losses = int((r > 0).sum()), int((r < 0).sum())
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    return {
        "trades": len(r), "wins": wins, "losses": losses,
        "wr_pct": round(100 * wins / len(r), 2),
        "net_R": round(float(r.sum()), 4),
        "pf": round(gp / gl, 4) if gl else None,
        "expectancy_R": round(float(r.mean()), 4),
    }


def main():
    d = load_journal()
    assets = sorted(d.coin.unique())
    start = d.signal_time.min() - pd.Timedelta(days=20)
    end = d.signal_time.max() + pd.Timedelta(hours=4)
    data, errors = {}, {}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(bingx_klines, a, start, end): a for a in assets}
        for f in as_completed(futs):
            a = futs[f]
            try:
                x = f.result()
                if x.empty:
                    raise RuntimeError("no BingX 4h data")
                data[a] = x
            except Exception as e:
                errors[a] = f"{type(e).__name__}: {e}"

    rows = []
    for _, t in d.iterrows():
        rec = {
            "trade_no": int(t.trade_no), "coin": t.coin, "side": t.side,
            "signal_time": t.signal_time.isoformat(), "R": float(t.R),
            "result": t.result, "data_error": errors.get(t.coin, "")
        }
        h4 = data.get(t.coin)
        if h4 is None:
            rec.update({"strict_breakout_5": False, "strict_breakout_10": False,
                        "breakout_pullback_5": False, "breakout_pullback_10": False})
            rows.append(rec); continue
        c5 = classify_trade(t, h4, 5)
        c10 = classify_trade(t, h4, 10)
        rec.update({
            "strict_breakout_5": c5["breakout"],
            "breakout_pullback_5": c5["pullback"],
            "strict_breakout_10": c10["breakout"],
            "breakout_pullback_10": c10["pullback"],
            "last_completed_4h_open": c5.get("last_completed_4h_open", ""),
            "last_completed_4h_close": c5.get("last_completed_4h_close", ""),
            "breakout_level_5": c5.get("breakout_level", ""),
            "status_5": c5.get("status", ""),
            "status_10": c10.get("status", ""),
        })
        rows.append(rec)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "trade_labels.csv", index=False)

    variants = {
        "baseline_all": out.index == out.index,
        "strict_breakout_5": out.strict_breakout_5,
        "strict_breakout_10": out.strict_breakout_10,
        "breakout_pullback_5": out.breakout_pullback_5,
        "breakout_pullback_10": out.breakout_pullback_10,
    }
    summary = []
    for name, mask in variants.items():
        q = out[mask]
        summary.append({"variant": name, **stats(q)})
    pd.DataFrame(summary).to_csv(OUT / "summary.csv", index=False)

    # A compact 100-trade readiness snapshot. The audit automatically expands
    # when the production journal reaches trade 100+; no production state is changed.
    latest = int(d.trade_no.max()) if not d.empty else 0
    readiness = {
        "journal_latest_trade": latest,
        "target": 100,
        "remaining_to_100": max(0, 100 - latest),
        "audit_is_research_only": True,
        "production_branch_modified": False,
        "next_step_at_100": [
            "freeze the 100-trade production snapshot",
            "recompute baseline 1-100 statistics from R",
            "compare strict 4H breakout and breakout+pullback variants",
            "run robustness: ATR +/-10%, RR neighbors, stop-buffer neighbors, 1-bar delay",
            "run true event-time portfolio replay/concurrency audit",
            "do not promote any 4H filter into V15 without separate approval"
        ]
    }
    pd.Series(readiness).to_json(OUT / "readiness_100.json", indent=2)

    print("SAM EDGE V15 4H BREAKOUT AUDIT")
    print(f"journal_latest_trade={latest} remaining_to_100={readiness['remaining_to_100']}")
    print(pd.DataFrame(summary).to_string(index=False))
    if errors:
        print(f"data_errors={len(errors)}: {sorted(errors)[:10]}")


if __name__ == "__main__":
    main()
