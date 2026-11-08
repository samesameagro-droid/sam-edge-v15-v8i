from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd


SHADOW_FILE = Path("v15_1_btc_aware_shadow_journal.csv")
SUMMARY_FILE = Path("v15_1_btc_aware_shadow_summary.json")
BTC_SYMBOL = "BTC/USDT:USDT"
BTC_FILTER_LONG_4H = -0.0025
BTC_FILTER_SHORT_4H = 0.0025
FIELDS = [
    "signal_key", "signal_time", "coin", "side", "entry", "sl", "tp",
    "v15_result", "v15_R", "btc_1h_return", "btc_4h_return",
    "btc_1h_ema20_relation", "btc_4h_ema20_relation",
    "btc_1h_rsi", "btc_4h_rsi", "btc_1h_adx", "btc_4h_adx",
    "btc_4h_regime", "btc_aware_decision", "shadow_result", "shadow_R",
    "loser_avoided", "winner_filtered", "closed_at"
]


def _pct(x):
    return None if x is None or not np.isfinite(float(x)) else float(x)


def _last_completed(df: pd.DataFrame, signal_ts: pd.Timestamp, hours: int):
    if df is None or df.empty:
        return None
    x = df.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True)
    cutoff = signal_ts - pd.Timedelta(hours=hours)
    eligible = x[x["timestamp"] + pd.Timedelta(hours=hours) <= signal_ts]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _indicator_snapshot(df: pd.DataFrame, signal_ts: pd.Timestamp, hours: int):
    x = df.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True)
    x = x.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    x = x[x["timestamp"] + pd.Timedelta(hours=hours) <= signal_ts].copy()
    if len(x) < 25:
        return None
    close = pd.to_numeric(x["close"], errors="coerce")
    ema20 = close.ewm(span=20, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    prev = close.shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev).abs(),
        (x["low"] - prev).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    up = x["high"].diff()
    dn = -x["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=x.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=x.index)
    pdi = 100 * plus.ewm(alpha=1/14, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * minus.ewm(alpha=1/14, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1/14, adjust=False).mean()
    row = x.iloc[-1]
    c = float(row["close"])
    return {
        "close": c,
        "ema20_relation": (c / float(ema20.iloc[-1]) - 1.0) if np.isfinite(ema20.iloc[-1]) else None,
        "rsi": float(rsi.iloc[-1]) if np.isfinite(rsi.iloc[-1]) else None,
        "adx": float(adx.iloc[-1]) if np.isfinite(adx.iloc[-1]) else None,
    }


def classify(side: str, btc_4h_return: float | None):
    if btc_4h_return is None or not np.isfinite(btc_4h_return):
        return "UNKNOWN", "UNKNOWN"
    if side == "LONG":
        decision = "PASS" if btc_4h_return >= BTC_FILTER_LONG_4H else "FILTER"
        regime = "SUPPORTIVE" if btc_4h_return >= 0 else ("NEUTRAL" if btc_4h_return >= BTC_FILTER_LONG_4H else "ADVERSE")
    else:
        decision = "PASS" if btc_4h_return <= BTC_FILTER_SHORT_4H else "FILTER"
        regime = "SUPPORTIVE" if btc_4h_return <= 0 else ("NEUTRAL" if btc_4h_return <= BTC_FILTER_SHORT_4H else "ADVERSE")
    return regime, decision


class BTCAwareShadow:
    def __init__(self, exchange):
        self.exchange = exchange
        self.rows = self._load()
        self._snapshot_cache = {}

    def _load(self):
        if not SHADOW_FILE.exists():
            return []
        try:
            with SHADOW_FILE.open("r", newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except Exception as e:
            print(f"BTC SHADOW LOAD ERROR | {type(e).__name__}: {e}")
            return []

    def _save(self):
        with SHADOW_FILE.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(self.rows)

    def _fetch_btc(self, timeframe: str, limit: int = 100):
        rows = self.exchange.fetch_ohlcv(BTC_SYMBOL, timeframe=timeframe, limit=limit)
        if not rows:
            return None
        return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])

    def snapshot(self, signal_time: str, side: str):
        cache_key = (str(signal_time), str(side).upper())
        if cache_key in self._snapshot_cache:
            return dict(self._snapshot_cache[cache_key])
        ts = pd.to_datetime(signal_time, utc=True)
        h1 = self._fetch_btc("1h", 100)
        h4 = self._fetch_btc("4h", 100)
        if h1 is None or h4 is None:
            raise RuntimeError("BTC OHLCV unavailable for shadow evaluation")

        r1 = _last_completed(h1, ts, 1)
        r4 = _last_completed(h4, ts, 4)
        if r1 is None or r4 is None:
            raise RuntimeError(f"BTC completed candle unavailable at signal_time={signal_time}")

        h1 = h1.sort_values("timestamp")
        h4 = h4.sort_values("timestamp")
        t1 = pd.to_datetime(r1["timestamp"], utc=True)
        t4 = pd.to_datetime(r4["timestamp"], utc=True)
        p1 = h1[h1["timestamp"] <= t1]["close"].tail(2)
        p4 = h4[h4["timestamp"] <= t4]["close"].tail(2)
        ret1 = float(p1.iloc[-1] / p1.iloc[-2] - 1.0) if len(p1) == 2 else None
        ret4 = float(p4.iloc[-1] / p4.iloc[-2] - 1.0) if len(p4) == 2 else None
        s1 = _indicator_snapshot(h1, ts, 1)
        s4 = _indicator_snapshot(h4, ts, 4)
        regime, decision = classify(side, ret4)
        result = {
            "btc_1h_return": _pct(ret1),
            "btc_4h_return": _pct(ret4),
            "btc_1h_ema20_relation": _pct(s1["ema20_relation"]) if s1 else None,
            "btc_4h_ema20_relation": _pct(s4["ema20_relation"]) if s4 else None,
            "btc_1h_rsi": _pct(s1["rsi"]) if s1 else None,
            "btc_4h_rsi": _pct(s4["rsi"]) if s4 else None,
            "btc_1h_adx": _pct(s1["adx"]) if s1 else None,
            "btc_4h_adx": _pct(s4["adx"]) if s4 else None,
            "btc_4h_regime": regime,
            "btc_aware_decision": decision,
        }
        self._snapshot_cache[cache_key] = dict(result)
        return result

    def record_signal(self, p):
        key = str(p.get("signal_key") or "")
        if not key or any(r.get("signal_key") == key for r in self.rows):
            return p
        snap = self.snapshot(p["opened_at"], p["side"])
        p.entry_metrics.update({f"btc_{k}": v for k, v in snap.items()})
        row = {field: "" for field in FIELDS}
        row.update({
            "signal_key": key,
            "signal_time": p["opened_at"],
            "coin": p["coin"],
            "side": p["side"],
            "entry": p["entry"],
            "sl": p["sl"],
            "tp": p["tp"],
            **snap,
        })
        self.rows.append(row)
        self._save()
        print(
            f"BTC-AWARE SHADOW | {p['coin']} | {p['side']} | "
            f"BTC4H={snap['btc_4h_return'] if snap['btc_4h_return'] is not None else 'NA'} | "
            f"REGIME={snap['btc_4h_regime']} | DECISION={snap['btc_aware_decision']} | ACTUAL V15=PASS"
        )
        return p

    def record_result(self, signal_key: str, result: str, rr: float, closed_at: str):
        row = next((r for r in self.rows if r.get("signal_key") == signal_key), None)
        if row is None:
            return
        if row.get("v15_result"):
            return
        decision = row.get("btc_aware_decision", "UNKNOWN")
        row["v15_result"] = result
        row["v15_R"] = f"{rr:.4f}"
        row["closed_at"] = closed_at
        if decision == "PASS":
            row["shadow_result"] = "TAKEN_" + result
            row["shadow_R"] = f"{rr:.4f}"
            row["loser_avoided"] = "0"
            row["winner_filtered"] = "0"
        elif decision == "FILTER":
            row["shadow_result"] = "FILTERED_" + result
            row["shadow_R"] = "0.0000"
            row["loser_avoided"] = "1" if result == "SL" else "0"
            row["winner_filtered"] = "1" if result == "TP" else "0"
        else:
            row["shadow_result"] = "UNKNOWN_" + result
            row["shadow_R"] = "0.0000"
            row["loser_avoided"] = "0"
            row["winner_filtered"] = "0"
        self._save()

    def summary(self):
        closed = [r for r in self.rows if r.get("v15_result") in {"TP", "SL"}]
        if not closed:
            return {"status": "RUNNING", "trades_evaluated": 0}
        def f(r, k):
            try: return float(r.get(k, 0))
            except (TypeError, ValueError): return 0.0
        v15_r = [f(r, "v15_R") for r in closed]
        sh_r = [f(r, "shadow_R") for r in closed]
        filtered = [r for r in closed if r.get("btc_aware_decision") == "FILTER"]
        pass_rows = [r for r in closed if r.get("btc_aware_decision") == "PASS"]
        avoided = sum(r.get("loser_avoided") == "1" for r in closed)
        lost = sum(r.get("winner_filtered") == "1" for r in closed)
        return {
            "status": "RUNNING",
            "trades_evaluated": len(closed),
            "target_new_trades": 50,
            "v15": {
                "net_R": round(sum(v15_r), 4),
                "wins": sum(x > 0 for x in v15_r),
                "losses": sum(x < 0 for x in v15_r),
                "win_rate_pct": round(sum(x > 0 for x in v15_r) / len(v15_r) * 100, 2),
            },
            "btc_aware_shadow": {
                "net_R": round(sum(sh_r), 4),
                "trades_taken": len(pass_rows),
                "trades_filtered": len(filtered),
                "losers_avoided": avoided,
                "winners_filtered": lost,
            },
            "hypothesis_locked": {
                "LONG": "BTC 4H return >= -0.25%",
                "SHORT": "BTC 4H return <= +0.25%",
            },
            "note": "Shadow only. V15 execution is never blocked by BTC-AWARE.",
        }

    def write_summary(self):
        SUMMARY_FILE.write_text(json.dumps(self.summary(), indent=2, ensure_ascii=False), encoding="utf-8")
