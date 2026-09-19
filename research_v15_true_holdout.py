from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from core_engine_v15 import COINS, CORE_NAME, RR, MAX_HOLD_BARS, enrich, signal_mask, trade_levels
from research_v15_compare import fetch_api, fetch_archive

# LOCKED BEFORE HOLDOUT:
# - core: V15_EARLY_RECLAIM_V1
# - score threshold: >= 40
# - no additional room/trigger hard gate
# - RR: engine RR
# - stop: STRUCTURE
# Holdout ends before the September 2026 paper-forward tuning period.
DEV_START = pd.Timestamp("2026-03-19T00:00:00Z")
DEV_END = pd.Timestamp("2026-07-16T23:59:59Z")
HOLDOUT_START = pd.Timestamp("2026-07-17T00:00:00Z")
HOLDOUT_END = pd.Timestamp("2026-09-14T23:59:59Z")
WARMUP_DAYS = 45
SCORE_THRESHOLD = 40.0


def score_series(x: pd.DataFrame) -> pd.Series:
    r = x
    long_mask, short_mask = signal_mask(r, CORE_NAME)
    side = pd.Series(0, index=r.index, dtype=int)
    side[long_mask] = 1
    side[short_mask] = -1

    adx_floor = np.where(side == 1, 0.80, 0.85)
    delta_floor = np.where(side == 1, 0.75, 0.90)
    adx_margin = np.clip((r.h4_adx_pct.to_numpy() - adx_floor) / (1 - adx_floor), 0, 1)
    delta_margin = np.clip((r.h4_adx_delta.to_numpy() - delta_floor) / 2.0, 0, 1)
    room = np.where(side == 1, r.dist_res_atr.to_numpy(), r.dist_sup_atr.to_numpy())
    room_margin = np.clip((room - 0.85) / 1.50, 0, 1)
    dist = r.dist_ema20_atr.to_numpy()
    loc_center = np.clip(1.0 - np.abs(dist - 0.60) / 0.60, 0, 1)
    vol_margin = np.clip(r.volr.to_numpy() / 2.0, 0, 1)
    trigger_margin = np.clip(r.body_atr.to_numpy() / 0.80, 0, 1)
    score = 100.0 * (
        0.30 * adx_margin
        + 0.20 * delta_margin
        + 0.20 * room_margin
        + 0.15 * loc_center
        + 0.10 * vol_margin
        + 0.05 * trigger_margin
    )
    return pd.Series(score, index=r.index).where(side != 0)


def run_trades(x: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    lm, sm = signal_mask(x, CORE_NAME)
    scores = score_series(x)
    idx = sorted(
        [(int(i), 1) for i in np.flatnonzero(lm.to_numpy()) if start <= x.timestamp.iloc[i] <= end]
        + [(int(i), -1) for i in np.flatnonzero(sm.to_numpy()) if start <= x.timestamp.iloc[i] <= end]
    )
    H, L, T = x.high.to_numpy(), x.low.to_numpy(), x.timestamp.to_numpy()
    out, last = [], -1

    for i, side in idx:
        if i <= last:
            continue
        score = float(scores.iloc[i])
        if not np.isfinite(score) or score < SCORE_THRESHOLD:
            continue

        lv = trade_levels(x, i, side, "STRUCTURE")
        if lv is None:
            continue
        e, sl, tp, risk = lv
        ex = None
        result = None
        for j in range(i + 1, min(len(x), i + 1 + MAX_HOLD_BARS)):
            hit_sl = L[j] <= sl if side == 1 else H[j] >= sl
            hit_tp = H[j] >= tp if side == 1 else L[j] <= tp
            # Match V15 paper engine: if both occur in one tracking candle,
            # SL is recorded first.
            if hit_sl and hit_tp:
                result, ex = "SL", j
                break
            if hit_sl:
                result, ex = "SL", j
                break
            if hit_tp:
                result, ex = "TP", j
                break
        if ex is None:
            continue

        out.append({
            "coin": "",
            "signal_time": pd.Timestamp(T[i]),
            "closed_at": pd.Timestamp(T[ex]),
            "side": "LONG" if side == 1 else "SHORT",
            "score": score,
            "entry": e,
            "sl": sl,
            "tp": tp,
            "result": result,
            "R": RR if result == "TP" else -1.0,
            "dist_ema20_atr": float(x.dist_ema20_atr.iloc[i]),
            "move5_atr": float(x.move5_atr.iloc[i]),
            "room_atr": float(x.dist_res_atr.iloc[i] if side == 1 else x.dist_sup_atr.iloc[i]),
            "volume_ratio": float(x.volr.iloc[i]),
            "h4_adx_pct": float(x.h4_adx_pct.iloc[i]),
            "h4_adx_delta": float(x.h4_adx_delta.iloc[i]),
        })
        last = ex
    return pd.DataFrame(out)


def stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "winrate_pct": 0.0, "net_R": 0.0,
                "profit_factor": 0.0, "expectancy_R": 0.0, "max_dd_R": 0.0}
    wins = int((tr.result == "TP").sum())
    losses = int((tr.result == "SL").sum())
    gp = float(tr.loc[tr.R > 0, "R"].sum())
    gl = float(-tr.loc[tr.R < 0, "R"].sum())
    eq = tr.sort_values("signal_time").R.cumsum()
    dd = eq - eq.cummax()
    return {
        "trades": len(tr),
        "wins": wins,
        "losses": losses,
        "winrate_pct": round(100 * wins / len(tr), 2),
        "net_R": round(float(tr.R.sum()), 4),
        "profit_factor": round(gp / gl, 4) if gl else None,
        "expectancy_R": round(float(tr.R.mean()), 4),
        "max_dd_R": round(float(dd.min()), 4),
    }


def fetch_coin(coin: str, start: datetime, end: datetime):
    try:
        df = fetch_api(coin, start, end)
        if len(df) >= 1000:
            return df, "fapi"
    except Exception as e:
        print(f"{coin}: FAPI error {type(e).__name__}: {e}")
    df = fetch_archive(coin, start, end)
    if df.empty:
        raise RuntimeError(f"no historical data for {coin}")
    return df, "archive"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("v15_true_holdout"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    fetch_start = (DEV_START - pd.Timedelta(days=WARMUP_DAYS)).to_pydatetime()
    fetch_end = HOLDOUT_END.to_pydatetime()

    all_dev, all_hold = [], []
    diagnostics = []

    for coin in COINS:
        try:
            raw, source = fetch_coin(coin, fetch_start, fetch_end)
            x = enrich(raw)
            dev = run_trades(x, DEV_START, DEV_END)
            hold = run_trades(x, HOLDOUT_START, HOLDOUT_END)
            if not dev.empty:
                dev.insert(0, "coin", coin)
                dev.insert(1, "period", "DEVELOPMENT")
                all_dev.append(dev)
            if not hold.empty:
                hold.insert(0, "coin", coin)
                hold.insert(1, "period", "HOLDOUT")
                all_hold.append(hold)
            diagnostics.append({
                "coin": coin, "source": source, "candles": len(x),
                "data_start": str(x.timestamp.iloc[0]), "data_end": str(x.timestamp.iloc[-1]),
                "dev_trades": len(dev), "holdout_trades": len(hold), "error": ""
            })
            print(f"{coin:5s} source={source:7s} candles={len(x):5d} dev={len(dev):2d} holdout={len(hold):2d}")
        except Exception as e:
            diagnostics.append({
                "coin": coin, "source": "", "candles": 0, "data_start": "", "data_end": "",
                "dev_trades": 0, "holdout_trades": 0, "error": f"{type(e).__name__}: {e}"
            })
            print(f"{coin:5s} ERROR {type(e).__name__}: {e}")

    dev = pd.concat(all_dev, ignore_index=True) if all_dev else pd.DataFrame()
    hold = pd.concat(all_hold, ignore_index=True) if all_hold else pd.DataFrame()

    for frame in (dev, hold):
        if not frame.empty:
            frame.sort_values(["signal_time", "coin"], inplace=True)
            frame.to_csv(args.out / ("development_trades.csv" if frame is dev else "holdout_trades.csv"), index=False)

    summary = pd.DataFrame([
        {"period": "DEVELOPMENT", **stats(dev)},
        {"period": "HOLDOUT", **stats(hold)},
    ])
    summary.to_csv(args.out / "summary.csv", index=False)
    pd.DataFrame(diagnostics).to_csv(args.out / "diagnostics.csv", index=False)

    manifest = {
        "core": CORE_NAME,
        "score_threshold": SCORE_THRESHOLD,
        "rr": RR,
        "stop_style": "STRUCTURE",
        "dev_start": str(DEV_START),
        "dev_end": str(DEV_END),
        "holdout_start": str(HOLDOUT_START),
        "holdout_end": str(HOLDOUT_END),
        "warmup_days": WARMUP_DAYS,
        "holdout_rule": "chronological fixed holdout; threshold locked before evaluation",
        "production_changed": False,
    }
    (args.out / "manifest.json").write_text(
        __import__("json").dumps(manifest, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 90)
    print(summary.to_string(index=False))
    print("=" * 90)


if __name__ == "__main__":
    main()
