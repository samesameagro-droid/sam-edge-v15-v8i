#!/usr/bin/env python3
"""Research-only portfolio replay for SAM EDGE Core V2 / candidate V3 gates.

Reads historical OHLCV ZIP/CSV files in data/<COIN>/*.zip or data/<COIN>/*.csv.
Expected columns: open_time (milliseconds) or timestamp, open, high, low, close, volume.
Does not modify the live/paper engine or journal. Entry is next candle OPEN; SL/TP
are calculated from information available at the signal candle close. If SL and TP
are both touched in one candle, SL wins. All results are reported in R units.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core_engine_v15 import (  # noqa: E402
    CORE_NAME, RR, enrich, signal_mask, failure_shield_snapshot,
    STRUCTURE_STOP_LOOKBACK, STRUCTURE_STOP_ATR_BUFFER,
    STRUCTURE_STOP_MIN_ATR, STRUCTURE_STOP_MAX_ATR,
)

def read_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise ValueError(f"No CSV inside {path}")
            raw = pd.read_csv(z.open(names[0]))
    else:
        raw = pd.read_csv(path)
    cols = {c.lower(): c for c in raw.columns}
    if not all(k in cols for k in ("open", "high", "low", "close", "volume")):
        raise ValueError(f"{path}: missing OHLCV columns")
    out = raw[[cols[k] for k in ("open", "high", "low", "close", "volume")]].copy()
    if "timestamp" in cols:
        ts = pd.to_datetime(raw[cols["timestamp"]], utc=True, errors="coerce")
    elif "open_time" in cols:
        ts = pd.to_datetime(raw[cols["open_time"]], unit="ms", utc=True, errors="coerce")
    else:
        raise ValueError(f"{path}: needs timestamp or open_time")
    out["timestamp"] = ts
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return (out.dropna().drop_duplicates("timestamp")
            .sort_values("timestamp").reset_index(drop=True))

def load_coin(root: Path, coin_dir: Path) -> pd.DataFrame:
    files = sorted(list(coin_dir.glob("*.zip")) + list(coin_dir.glob("*.csv")))
    if not files:
        return pd.DataFrame()
    parts = [read_file(p) for p in files]
    x = (pd.concat(parts, ignore_index=True).drop_duplicates("timestamp")
         .sort_values("timestamp").reset_index(drop=True))
    return x

def score_series(x: pd.DataFrame) -> pd.Series:
    adx_pct = x["h4_adx_pct"]
    adx_delta = x["h4_adx_delta"]
    dist = x["dist_ema20_atr"]
    room = np.where(x["close"] >= x["ema20"], x["dist_res_atr"], x["dist_sup_atr"])
    adx_margin = ((adx_pct - 0.80) / (1 - 0.80)).clip(0, 1)
    delta_margin = ((adx_delta - 0.75) / 2.0).clip(0, 1)
    room_margin = ((room - 0.85) / 1.50).clip(0, 1)
    location = (1 - (dist - 0.60).abs() / 0.60).clip(0, 1)
    volume = (x["volr"] / 2.0).clip(0, 1)
    trigger = (x["body_atr"] / 0.80).clip(0, 1)
    return 100 * (0.30*adx_margin + 0.20*delta_margin + 0.20*room_margin
                  + 0.15*location + 0.10*volume + 0.05*trigger)

def build_events(coin: str, raw: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    x = enrich(raw)
    if len(x) < 3300:
        return x, []
    long_mask, short_mask = signal_mask(x, CORE_NAME)
    score = score_series(x)
    # Match the V15 Precision V2 entry permission as closely as possible.
    touch_long = x["low"] <= x["ema20"] + 0.60*x["atr"]
    touch_short = x["high"] >= x["ema20"] - 0.60*x["atr"]
    rows = []
    for i in range(250, len(x)-1):
        side = "LONG" if bool(long_mask.iloc[i]) else ("SHORT" if bool(short_mask.iloc[i]) else None)
        if side is None:
            continue
        # Current V2 precision gate: pullback touch within previous 1-2 completed
        # candles, volume >= 1.20, distance <= 0.80 ATR, ADX exhaustion veto,
        # score < 65. Current signal candle itself is not counted as the pullback.
        touch = touch_long if side == "LONG" else touch_short
        fresh = bool(touch.iloc[i-1] or touch.iloc[i-2])
        adx_pct = float(x["h4_adx_pct"].iloc[i])
        adx_delta = float(x["h4_adx_delta"].iloc[i])
        volr = float(x["volr"].iloc[i])
        dist = float(x["dist_ema20_atr"].iloc[i])
        precision_pass = (fresh and adx_pct < 0.90 or fresh and adx_delta >= 0) and volr >= 1.20 and dist <= 0.80 and float(score.iloc[i]) < 65
        shield = failure_shield_snapshot(x, i, side)
        # One-bar-delay execution at next candle OPEN; do not use that candle
        # to decide whether the entry signal exists.
        j = i + 1
        entry_ts = pd.Timestamp(x["timestamp"].iloc[j])
        entry = float(x["open"].iloc[j])
        atr = float(x["atr"].iloc[i])
        if not np.isfinite(atr) or atr <= 0:
            continue
        if side == "LONG":
            anchor = float(x["low"].iloc[max(0, i-STRUCTURE_STOP_LOOKBACK+1):i+1].min())
            sl = anchor - STRUCTURE_STOP_ATR_BUFFER*atr
            risk = entry - sl
            if risk < STRUCTURE_STOP_MIN_ATR*atr:
                sl = entry - STRUCTURE_STOP_MIN_ATR*atr
            elif risk > STRUCTURE_STOP_MAX_ATR*atr:
                sl = entry - STRUCTURE_STOP_MAX_ATR*atr
            risk = entry - sl
            tp = entry + RR*risk
        else:
            anchor = float(x["high"].iloc[max(0, i-STRUCTURE_STOP_LOOKBACK+1):i+1].max())
            sl = anchor + STRUCTURE_STOP_ATR_BUFFER*atr
            risk = sl - entry
            if risk < STRUCTURE_STOP_MIN_ATR*atr:
                sl = entry + STRUCTURE_STOP_MIN_ATR*atr
            elif risk > STRUCTURE_STOP_MAX_ATR*atr:
                sl = entry + STRUCTURE_STOP_MAX_ATR*atr
            risk = sl - entry
            tp = entry - RR*risk
        if risk <= 0 or not np.isfinite(risk):
            continue
        rows.append({
            "coin": coin, "side": side, "signal_time": pd.Timestamp(x["timestamp"].iloc[i]).isoformat(),
            "entry_time": entry_ts.isoformat(), "entry_index": j, "entry": entry,
            "sl": float(sl), "tp": float(tp), "risk_price": float(risk),
            "score": float(score.iloc[i]), "adx_pct": adx_pct, "adx_delta": adx_delta,
            "volr": volr, "dist_ema20_atr": dist, "fresh_pullback": fresh,
            "precision_pass": bool(precision_pass), "shield_pass": not bool(shield["veto"]),
            "v3_adx_ok": not (adx_pct >= 0.90 and adx_delta < 0),
            "signal_index": i,
        })
    return x, rows

def replay(event_rows: list[dict], frames: dict[str, pd.DataFrame], variant: str,
           max_active: int = 5, cooldown_bars: int = 4) -> list[dict]:
    """Event-driven portfolio replay. One position per coin; SL wins same-bar ties."""
    if variant == "V2_EXECUTABLE":
        candidates = [e for e in event_rows if e["precision_pass"]]
    elif variant == "V3_SHIELD":
        candidates = [e for e in event_rows if e["precision_pass"] and e["shield_pass"]]
    elif variant == "V3_ADX":
        candidates = [e for e in event_rows if e["precision_pass"] and e["v3_adx_ok"]]
    elif variant == "V3_SHIELD_ADX":
        candidates = [e for e in event_rows if e["precision_pass"] and e["shield_pass"] and e["v3_adx_ok"]]
    else:
        raise ValueError(variant)
    candidates.sort(key=lambda e: (e["entry_time"], e["coin"]))
    by_time: dict[str, list[dict]] = {}
    for e in candidates:
        by_time.setdefault(e["entry_time"], []).append(e)
    bar_maps = {}
    for coin, df in frames.items():
        bar_maps[coin] = {pd.Timestamp(r.timestamp).isoformat(): (float(r.high), float(r.low))
                          for r in df.itertuples(index=False)}
    timeline = sorted(set(by_time) | {t for bm in bar_maps.values() for t in bm})
    active: list[dict] = []
    closed: list[dict] = []
    last_exit_index: dict[str, int] = {}
    index_by_coin = {coin: {pd.Timestamp(t).isoformat(): i for i, t in enumerate(df["timestamp"])}
                     for coin, df in frames.items()}
    for ts in timeline:
        # First manage positions using this bar; positions entered at this same
        # timestamp are added only after this close pass.
        still = []
        for p in active:
            bar = bar_maps.get(p["coin"], {}).get(ts)
            if bar is None or ts <= p["entry_time"]:
                still.append(p); continue
            high, low = bar
            hit_sl = low <= p["sl"] if p["side"] == "LONG" else high >= p["sl"]
            hit_tp = high >= p["tp"] if p["side"] == "LONG" else low <= p["tp"]
            if hit_sl or hit_tp:
                result = "SL" if hit_sl else "TP"
                rr = -1.0 if result == "SL" else RR
                rec = dict(p, closed_at=ts, result=result, R=rr)
                closed.append(rec)
                last_exit_index[p["coin"]] = index_by_coin[p["coin"]].get(ts, 0)
            else:
                still.append(p)
        active = still
        for e in by_time.get(ts, []):
            if len(active) >= max_active or any(p["coin"] == e["coin"] for p in active):
                continue
            idx = index_by_coin[e["coin"]].get(ts, 0)
            if idx - last_exit_index.get(e["coin"], -10**9) < cooldown_bars:
                continue
            active.append(dict(e, variant=variant, entry_time=ts))
    # Do not count unresolved positions as wins/losses; report them separately.
    for p in active:
        closed.append(dict(p, result="OPEN_AT_END", R=0.0))
    return closed

def summarize(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["result"] in ("TP", "SL")]
    wins = sum(r["result"] == "TP" for r in closed)
    losses = sum(r["result"] == "SL" for r in closed)
    rs = [float(r["R"]) for r in closed]
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    eq = np.cumsum(rs) if rs else np.array([])
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:] if len(eq) else np.array([])
    dd = (eq - peak) if len(eq) else np.array([])
    return {
        "closed": len(closed), "wins": wins, "losses": losses,
        "win_rate_pct": round(100*wins/len(closed), 2) if closed else 0.0,
        "net_R": round(sum(rs), 3),
        "expectancy_R": round(sum(rs)/len(rs), 4) if rs else 0.0,
        "profit_factor": round(gross_win/gross_loss, 4) if gross_loss else None,
        "max_drawdown_R": round(float(dd.min()), 3) if len(dd) else 0.0,
        "open_at_end": sum(r["result"] == "OPEN_AT_END" for r in rows),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"), help="Folder containing one subfolder per coin")
    ap.add_argument("--out", default=str(ROOT / "research" / "results"), help="Output folder")
    ap.add_argument("--max-active", type=int, default=5)
    ap.add_argument("--cooldown-bars", type=int, default=4)
    args = ap.parse_args()
    data_root, out_root = Path(args.data), Path(args.out)
    if not data_root.exists():
        raise SystemExit(f"No historical OHLCV directory found: {data_root}. Add data/<COIN>/*.zip or *.csv.")
    frames, events = {}, []
    for folder in sorted(p for p in data_root.iterdir() if p.is_dir()):
        raw = load_coin(data_root, folder)
        if raw.empty:
            continue
        frames[folder.name.upper()] = raw
        enriched, coin_events = build_events(folder.name.upper(), raw)
        if not enriched.empty:
            frames[folder.name.upper()] = enriched
        events.extend(coin_events)
    if not frames or not events:
        raise SystemExit("No usable historical data/signals. Need completed 15m OHLCV with sufficient 1H/4H warmup.")
    out_root.mkdir(parents=True, exist_ok=True)
    report = {
        "data_source": str(data_root), "coins": sorted(frames), "candidate_events": len(events),
        "entry_model": "next 15m candle OPEN; levels based on signal-close structure/ATR",
        "rr": RR, "same_bar_sl_tp_policy": "SL first", "max_active": args.max_active,
        "cooldown_bars": args.cooldown_bars, "variants": {}
    }
    all_rows = []
    for variant in ("V2_EXECUTABLE", "V3_SHIELD", "V3_ADX", "V3_SHIELD_ADX"):
        result = replay(events, frames, variant, args.max_active, args.cooldown_bars)
        closed = [r for r in result if r["result"] != "OPEN_AT_END"]
        all_rows.extend(result)
        report["variants"][variant] = summarize(result)
        pd.DataFrame(result).to_csv(out_root / f"{variant.lower()}_trades.csv", index=False)
    (out_root / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
