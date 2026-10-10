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

# Small, pre-registered ADX grid. The V2 Precision gate already vetoes ADX
# exhaustion; these candidates test ADX strength/expansion rather than duplicating it.
ADX_VARIANTS = {
    "V3_ADX_RISING_050": (0.50, 0.00),
    "V3_ADX_RISING_060": (0.60, 0.00),
    "V3_ADX_RISING_070": (0.70, 0.00),
    "V3_ADX_EXPANSION_050_050": (0.50, 0.50),
    "V3_ADX_EXPANSION_060_050": (0.60, 0.50),
    "V3_ADX_EXPANSION_070_050": (0.70, 0.50),
    "V3_ADX_EXPANSION_080_075": (0.80, 0.75),
}
SCORE_VARIANTS = {
    "V3_SCORE_CAP_40": 40.0,
    "V3_SCORE_CAP_45": 45.0,
    "V3_SCORE_CAP_50": 50.0,
    "V3_SCORE_CAP_55": 55.0,
}
COMBO_VARIANTS = {
    "V3_SCORE40_ADX_RISING_050": (40.0, 0.50, 0.00),
}

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

def score_at(x: pd.DataFrame, i: int, side: str) -> float:
    adx_pct = float(x["h4_adx_pct"].iloc[i])
    adx_delta = float(x["h4_adx_delta"].iloc[i])
    adx_floor = 0.80 if side == "LONG" else 0.85
    delta_floor = 0.75 if side == "LONG" else 0.90
    room = float(x["dist_res_atr"].iloc[i] if side == "LONG" else x["dist_sup_atr"].iloc[i])
    dist = float(x["dist_ema20_atr"].iloc[i])
    adx_margin = float(np.clip((adx_pct - adx_floor) / (1 - adx_floor), 0, 1))
    delta_margin = float(np.clip((adx_delta - delta_floor) / 2.0, 0, 1))
    room_margin = float(np.clip((room - 0.85) / 1.50, 0, 1))
    location = float(np.clip(1 - abs(dist - 0.60) / 0.60, 0, 1))
    volume = float(np.clip(float(x["volr"].iloc[i]) / 2.0, 0, 1))
    trigger = float(np.clip(float(x["body_atr"].iloc[i]) / 0.80, 0, 1))
    return 100 * (0.30*adx_margin + 0.20*delta_margin + 0.20*room_margin
                  + 0.15*location + 0.10*volume + 0.05*trigger)

def build_events(coin: str, raw: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    x = enrich(raw)
    if len(x) < 3300:
        return x, []
    long_mask, short_mask = signal_mask(x, CORE_NAME)
    # Match the V15 Precision V2 entry permission as closely as possible.
    touch_long = x["low"] <= x["ema20"] + 0.60*x["atr"]
    touch_short = x["high"] >= x["ema20"] - 0.60*x["atr"]
    rows = []
    for i in range(250, len(x)-1):
        side = "LONG" if bool(long_mask.iloc[i]) else ("SHORT" if bool(short_mask.iloc[i]) else None)
        if side is None:
            continue
        score_value = score_at(x, i, side)
        # Current V2 precision gate: pullback touch within previous 1-2 completed
        # candles, volume >= 1.20, distance <= 0.80 ATR, ADX exhaustion veto,
        # score < 65. Current signal candle itself is not counted as the pullback.
        touch = touch_long if side == "LONG" else touch_short
        fresh = bool(touch.iloc[i-1] or touch.iloc[i-2])
        adx_pct = float(x["h4_adx_pct"].iloc[i])
        adx_delta = float(x["h4_adx_delta"].iloc[i])
        volr = float(x["volr"].iloc[i])
        dist = float(x["dist_ema20_atr"].iloc[i])
        adx_ok = not (adx_pct >= 0.90 and adx_delta < 0)
        precision_pass = fresh and adx_ok and volr >= 1.20 and dist <= 0.80 and score_value < 65
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
            anchor = float(x["low"].iloc[max(0, i-STRUCTURE_STOP_LOOKBACK):i].min())
            sl = anchor - STRUCTURE_STOP_ATR_BUFFER*atr
            risk = entry - sl
            if risk < STRUCTURE_STOP_MIN_ATR*atr:
                sl = entry - STRUCTURE_STOP_MIN_ATR*atr
            elif risk > STRUCTURE_STOP_MAX_ATR*atr:
                sl = entry - STRUCTURE_STOP_MAX_ATR*atr
            risk = entry - sl
            tp = entry + RR*risk
        else:
            anchor = float(x["high"].iloc[max(0, i-STRUCTURE_STOP_LOOKBACK):i].max())
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
            "score": score_value, "adx_pct": adx_pct, "adx_delta": adx_delta,
            "volr": volr, "dist_ema20_atr": dist, "fresh_pullback": fresh,
            "precision_pass": bool(precision_pass), "shield_pass": not bool(shield["veto"]),
            "v3_strict_adx_ok": (adx_pct >= 0.90 and adx_delta >= 1.00),
            "signal_index": i,
        })
    return x, rows

def replay(event_rows: list[dict], frames: dict[str, pd.DataFrame], variant: str,
           max_active: int = 5, cooldown_bars: int = 4,
           fee_bps_per_side: float = 5.0, slippage_bps_per_side: float = 2.0) -> list[dict]:
    """Audited event replay: entry candle is checked, stop gaps are adverse, costs are deducted.
    Portfolio drawdown is marked-to-market at each candle close in R units.
    """
    if variant == "V2_EXECUTABLE":
        candidates = [e for e in event_rows if e["precision_pass"]]
    elif variant in ADX_VARIANTS:
        adx_floor, delta_floor = ADX_VARIANTS[variant]
        candidates = [e for e in event_rows if e["precision_pass"] and e["adx_pct"] >= adx_floor and e["adx_delta"] >= delta_floor]
    elif variant in SCORE_VARIANTS:
        score_cap = SCORE_VARIANTS[variant]
        candidates = [e for e in event_rows if e["precision_pass"] and e["score"] < score_cap]
    elif variant in COMBO_VARIANTS:
        score_cap, adx_floor, delta_floor = COMBO_VARIANTS[variant]
        candidates = [e for e in event_rows if e["precision_pass"] and e["score"] < score_cap and e["adx_pct"] >= adx_floor and e["adx_delta"] >= delta_floor]
    else:
        raise ValueError(variant)

    candidates.sort(key=lambda e: (e["entry_time"], e["coin"]))
    by_time: dict[str, list[dict]] = {}
    for e in candidates:
        by_time.setdefault(e["entry_time"], []).append(e)
    bar_maps = {
        coin: {pd.Timestamp(r.timestamp).isoformat(): (float(r.open), float(r.high), float(r.low), float(r.close))
               for r in df.itertuples(index=False)}
        for coin, df in frames.items()
    }
    index_by_coin = {coin: {pd.Timestamp(t).isoformat(): i for i, t in enumerate(df["timestamp"])}
                     for coin, df in frames.items()}
    timeline = sorted(set(by_time) | {t for bm in bar_maps.values() for t in bm})
    active: list[dict] = []
    closed: list[dict] = []
    last_exit_index: dict[str, int] = {}
    realized_r = 0.0
    equity_peak = 0.0
    portfolio_max_dd = 0.0
    round_trip_bps = 2.0 * (fee_bps_per_side + slippage_bps_per_side)

    def settle(p: dict, ts: str, exit_px: float, result: str, idx: int, age: int) -> dict:
        direction = 1 if p["side"] == "LONG" else -1
        gross_r = direction * (exit_px - p["entry"]) / p["risk_price"]
        cost_r = ((p["entry"] + exit_px) * round_trip_bps / 10000.0) / p["risk_price"]
        return dict(p, closed_at=ts, exit=float(exit_px), result=result,
                    gross_R=float(gross_r), cost_R=float(cost_r), R=float(gross_r-cost_r),
                    hold_bars=int(age), exit_model="adverse_stop_gap; SL-first intrabar")

    for ts in timeline:
        # Existing positions are managed against this bar's OHLC.
        still = []
        for p in active:
            bar = bar_maps.get(p["coin"], {}).get(ts)
            if bar is None or ts <= p["entry_time"]:
                still.append(p)
                continue
            op, high, low, close = bar
            idx = index_by_coin[p["coin"]].get(ts, p["entry_index"])
            age = idx - p["entry_index"]
            if p["side"] == "LONG":
                gap_stop = op <= p["sl"]
                hit_sl, hit_tp = (low <= p["sl"]), (high >= p["tp"])
                exit_px = min(op, p["sl"]) if gap_stop else p["sl"]
            else:
                gap_stop = op >= p["sl"]
                hit_sl, hit_tp = (high >= p["sl"]), (low <= p["tp"])
                exit_px = max(op, p["sl"]) if gap_stop else p["sl"]
            if hit_sl:
                rec = settle(p, ts, exit_px, "SL", idx, age)
            elif hit_tp:
                rec = settle(p, ts, p["tp"], "TP", idx, age)
            else:
                still.append(p)
                continue
            closed.append(rec)
            realized_r += rec["R"]
            last_exit_index[p["coin"]] = idx
        active = still

        # New entries occur at this candle's OPEN. Inspect the remainder of the
        # entry candle immediately; if both levels are touched, conservatively count SL.
        for e in by_time.get(ts, []):
            if len(active) >= max_active or any(p["coin"] == e["coin"] for p in active):
                continue
            idx = index_by_coin[e["coin"]].get(ts, e["entry_index"])
            if idx - last_exit_index.get(e["coin"], -10**9) < cooldown_bars:
                continue
            p = dict(e, variant=variant, entry_time=ts, entry_index=idx)
            bar = bar_maps.get(e["coin"], {}).get(ts)
            if bar is None:
                continue
            op, high, low, close = bar
            # If opening price is already beyond stop, model an immediate adverse stop.
            if (p["side"] == "LONG" and op <= p["sl"]) or (p["side"] == "SHORT" and op >= p["sl"]):
                rec = settle(p, ts, op, "SL", idx, 0)
                closed.append(rec)
                realized_r += rec["R"]
                last_exit_index[p["coin"]] = idx
                continue
            hit_sl = low <= p["sl"] if p["side"] == "LONG" else high >= p["sl"]
            hit_tp = high >= p["tp"] if p["side"] == "LONG" else low <= p["tp"]
            if hit_sl:
                rec = settle(p, ts, p["sl"], "SL", idx, 0)
                closed.append(rec)
                realized_r += rec["R"]
                last_exit_index[p["coin"]] = idx
            elif hit_tp:
                rec = settle(p, ts, p["tp"], "TP", idx, 0)
                closed.append(rec)
                realized_r += rec["R"]
                last_exit_index[p["coin"]] = idx
            else:
                active.append(p)

        # Mark-to-market portfolio equity at every available timestamp.
        unrealized_r = 0.0
        for p in active:
            bar = bar_maps.get(p["coin"], {}).get(ts)
            if bar is None:
                continue
            close = bar[3]
            direction = 1 if p["side"] == "LONG" else -1
            gross_mark = direction * (close - p["entry"]) / p["risk_price"]
            mark_cost = ((p["entry"] + close) * round_trip_bps / 10000.0) / p["risk_price"]
            unrealized_r += gross_mark - mark_cost
        equity = realized_r + unrealized_r
        equity_peak = max(equity_peak, equity)
        portfolio_max_dd = min(portfolio_max_dd, equity - equity_peak)

    # Keep unresolved positions distinct. Their final mark is reported separately,
    # not classified as a TP/SL and not included in closed-trade win rate.
    for p in active:
        df = frames[p["coin"]]
        sub = df[df["timestamp"] <= pd.Timestamp(timeline[-1])] if timeline else df.iloc[0:0]
        if sub.empty:
            mark_r = 0.0
            close_ts = p["entry_time"]
        else:
            last = sub.iloc[-1]
            mark_r = (1 if p["side"] == "LONG" else -1) * (float(last["close"]) - p["entry"]) / p["risk_price"]
            mark_r -= ((p["entry"] + float(last["close"])) * round_trip_bps / 10000.0) / p["risk_price"]
            close_ts = pd.Timestamp(last["timestamp"]).isoformat()
        closed.append(dict(p, closed_at=close_ts, result="OPEN_AT_END", R=0.0, mark_R=float(mark_r)))
    for rec in closed:
        rec["portfolio_max_drawdown_R"] = float(portfolio_max_dd)
    return closed


def summarize(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["result"] in ("TP", "SL")]
    open_rows = [r for r in rows if r["result"] == "OPEN_AT_END"]
    wins = sum(float(r["R"]) > 0 for r in closed)
    losses = sum(float(r["R"]) <= 0 for r in closed)
    rs = [float(r["R"]) for r in closed]
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    eq = np.cumsum(rs) if rs else np.array([])
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:] if len(eq) else np.array([])
    dd = (eq - peak) if len(eq) else np.array([])
    return {
        "closed_trades": len(closed), "wins_net_positive": wins, "losses_or_nonpositive": losses,
        "win_rate_pct": round(100*wins/len(closed), 2) if closed else 0.0,
        "net_R_closed_trades": round(sum(rs), 3),
        "expectancy_R_per_closed_trade": round(sum(rs)/len(rs), 4) if rs else 0.0,
        "profit_factor": round(gross_win/gross_loss, 4) if gross_loss else None,
        "max_drawdown_realized_R": round(float(dd.min()), 3) if len(dd) else 0.0,
        "max_drawdown_portfolio_marked_R": round(float(rows[0].get("portfolio_max_drawdown_R", 0.0)), 3) if rows else 0.0,
        "open_at_end": len(open_rows),
        "open_marked_R_sum": round(sum(float(r.get("mark_R", 0.0)) for r in open_rows), 3),
        "costs": "round-trip fees and slippage deducted per trade; funding not included",
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"), help="Folder containing one subfolder per coin")
    ap.add_argument("--out", default=str(ROOT / "research" / "results"), help="Output folder")
    ap.add_argument("--max-active", type=int, default=5)
    ap.add_argument("--cooldown-bars", type=int, default=4)
    ap.add_argument("--fee-bps-per-side", type=float, default=5.0, help="Fee assumption in basis points per side")
    ap.add_argument("--slippage-bps-per-side", type=float, default=2.0, help="Slippage assumption in basis points per side")
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
        "cooldown_bars": args.cooldown_bars,
        "train_window": "2025-01-01 through 2026-03-31",
        "test_window": "2026-04-01 through 2026-08-31",
        "holdout_window": "2026-09-05 through 2026-09-30",
        "cost_assumptions": {"fee_bps_per_side": args.fee_bps_per_side, "slippage_bps_per_side": args.slippage_bps_per_side, "funding": "excluded"},
        "note": "Audited replay: entry candle checked; stop gaps adverse; fees/slippage deducted; portfolio drawdown marked-to-market. Research only.",
        "variants": {}
    }
    periods = {
        "full": (None, pd.Timestamp("2026-09-01", tz="UTC")),
        "train": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-04-01", tz="UTC")),
        "test": (pd.Timestamp("2026-04-01", tz="UTC"), pd.Timestamp("2026-09-01", tz="UTC")),
        "holdout": (pd.Timestamp("2026-09-05", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC")),
    }
    variants = ["V2_EXECUTABLE", *ADX_VARIANTS.keys(), *SCORE_VARIANTS.keys(), *COMBO_VARIANTS.keys()]
    all_rows = []
    for variant in variants:
        report["variants"][variant] = {}
        for period, (start, end) in periods.items():
            if period == "holdout" and variant not in {"V2_EXECUTABLE", "V3_SCORE_CAP_40"}:
                continue
            period_events = events
            period_frames = frames
            if start is not None or end is not None:
                period_events = [
                    e for e in events
                    if (start is None or pd.Timestamp(e["entry_time"]) >= start)
                    and (end is None or pd.Timestamp(e["entry_time"]) < end)
                ]
                if end is not None:
                    period_frames = {
                        coin: df[df["timestamp"] < end].copy()
                        for coin, df in frames.items()
                    }
            result = replay(period_events, period_frames, variant, args.max_active, args.cooldown_bars, args.fee_bps_per_side, args.slippage_bps_per_side)
            report["variants"][variant][period] = summarize(result)
            pd.DataFrame(result).to_csv(out_root / f"{variant.lower()}_{period}_trades.csv", index=False)
            if period == "full":
                all_rows.extend(result)
    pd.DataFrame(all_rows).to_csv(out_root / "all_variants_full_trades.csv", index=False)
    (out_root / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
