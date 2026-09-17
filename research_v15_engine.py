from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from core_engine_v15 import (
    COINS, CORE_NAME, RR, SWING_LOOKBACK, MAX_HOLD_BARS,
    enrich, signal_mask,
    ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
)

BINANCE_FAPI = "https://fapi.binance.com/fapi/v1/klines"
SESSION = requests.Session()


def fetch_binance_15m(symbol: str, days: int = 180) -> pd.DataFrame:
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    rows_all = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": f"{symbol}USDT",
            "interval": "15m",
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }
        r = SESSION.get(BINANCE_FAPI, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        rows_all.extend(rows)
        last_open = int(rows[-1][0])
        next_cursor = last_open + 15 * 60 * 1000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.08)
        if len(rows) < 1500:
            break

    if not rows_all:
        raise RuntimeError(f"No Binance 15m data for {symbol}")
    a = np.asarray(rows_all, dtype=object)
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(a[:, 0].astype(np.int64), unit="ms", utc=True),
        "open": a[:, 1].astype(float),
        "high": a[:, 2].astype(float),
        "low": a[:, 3].astype(float),
        "close": a[:, 4].astype(float),
        "volume": a[:, 5].astype(float),
    })
    return df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def masks_early(x: pd.DataFrame):
    r = x
    bull = r.h1_trend_bull & r.h4_trend_bull
    bear = r.h1_trend_bear & r.h4_trend_bear
    ema_l = (r.close > r.ema20) & (r.ema20 > r.ema50)
    ema_s = (r.close < r.ema20) & (r.ema20 < r.ema50)
    vwap_l = r.close > r.vwap
    vwap_s = r.close < r.vwap
    di_l = r.pdi > r.mdi
    di_s = r.mdi > r.pdi
    rsi_l = r.rsi.between(44, 66)
    rsi_s = r.rsi.between(34, 56)
    structure_l = bull & ema_l & vwap_l & di_l & rsi_l
    structure_s = bear & ema_s & vwap_s & di_s & rsi_s
    room_l = r.dist_res_atr >= 0.85
    room_s = r.dist_sup_atr >= 0.85

    # Anti-chase guard: a valid setup must not be an already-expanded 15m impulse.
    no_chase = (r.move5_atr <= 2.50) & (r.dist_ema20_atr <= 1.20) & (r.range_atr <= 2.25)

    # Entry trigger: recent pullback/touch of EMA20, then a confirmed 15m reclaim.
    touched_l = r.low.rolling(5).min() <= (r.ema20 + 0.35 * r.atr)
    touched_s = r.high.rolling(5).max() >= (r.ema20 - 0.35 * r.atr)
    reclaim_l = (r.close > r.high.shift(1)) & (r.close > r.ema20)
    reclaim_s = (r.close < r.low.shift(1)) & (r.close < r.ema20)
    trigger_l = touched_l & reclaim_l
    trigger_s = touched_s & reclaim_s

    return (structure_l & room_l & no_chase & trigger_l).fillna(False), (structure_s & room_s & no_chase & trigger_s).fillna(False)


def masks_early_loose(x: pd.DataFrame):
    r = x
    bull = r.h1_trend_bull & r.h4_trend_bull
    bear = r.h1_trend_bear & r.h4_trend_bear
    ema_l = (r.close > r.ema20) & (r.ema20 > r.ema50)
    ema_s = (r.close < r.ema20) & (r.ema20 < r.ema50)
    structure_l = bull & ema_l & (r.close > r.vwap) & (r.pdi > r.mdi) & r.rsi.between(44, 66)
    structure_s = bear & ema_s & (r.close < r.vwap) & (r.mdi > r.pdi) & r.rsi.between(34, 56)
    room_l = r.dist_res_atr >= 0.65
    room_s = r.dist_sup_atr >= 0.65
    no_chase = (r.move5_atr <= 2.75) & (r.dist_ema20_atr <= 1.35) & (r.range_atr <= 2.50)
    touched_l = r.low.rolling(5).min() <= (r.ema20 + 0.50 * r.atr)
    touched_s = r.high.rolling(5).max() >= (r.ema20 - 0.50 * r.atr)
    reclaim_l = (r.close > r.high.shift(1)) & (r.close > r.ema20)
    reclaim_s = (r.close < r.low.shift(1)) & (r.close < r.ema20)
    return (structure_l & room_l & no_chase & touched_l & reclaim_l).fillna(False), (structure_s & room_s & no_chase & touched_s & reclaim_s).fillna(False)


def stop_prices(x, i: int, side: int, style: str):
    entry = float(x.close.iloc[i])
    atr = float(x.atr.iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if style == "baseline":
        if side == 1:
            sl = float(x.low.iloc[max(0, i-SWING_LOOKBACK):i].min()) - atr
        else:
            sl = float(x.high.iloc[max(0, i-SWING_LOOKBACK):i].max()) + atr
    elif style == "structure":
        look = 8
        if side == 1:
            anchor = float(x.low.iloc[max(0, i-look):i].min())
            sl = anchor - 0.65 * atr
        else:
            anchor = float(x.high.iloc[max(0, i-look):i].max())
            sl = anchor + 0.65 * atr
        risk = abs(entry-sl)
        if risk < 0.80 * atr:
            sl = entry - 0.80 * atr if side == 1 else entry + 0.80 * atr
        elif risk > 2.50 * atr:
            sl = entry - 2.50 * atr if side == 1 else entry + 2.50 * atr
    else:
        raise ValueError(style)
    risk = entry-sl if side == 1 else sl-entry
    if not np.isfinite(risk) or risk <= 0:
        return None
    tp = entry + RR*risk if side == 1 else entry - RR*risk
    return entry, sl, tp, risk


def run_backtest(x: pd.DataFrame, long_mask, short_mask, stop_style: str):
    idx = sorted([(int(i), 1) for i in np.flatnonzero(long_mask.to_numpy()) if i >= 250] +
                 [(int(i), -1) for i in np.flatnonzero(short_mask.to_numpy()) if i >= 250])
    h, l, t = x.high.to_numpy(), x.low.to_numpy(), x.timestamp.to_numpy()
    out = []
    last = -1
    for i, side in idx:
        if i <= last:
            continue
        sp = stop_prices(x, i, side, stop_style)
        if sp is None:
            continue
        entry, sl, tp, risk = sp
        ex = None; result = None; mfe = 0.0; mae = 0.0
        for j in range(i+1, min(len(x), i+1+MAX_HOLD_BARS)):
            if side == 1:
                mfe = max(mfe, (h[j]-entry)/risk)
                mae = min(mae, (l[j]-entry)/risk)
                hit_sl = l[j] <= sl
                hit_tp = h[j] >= tp
            else:
                mfe = max(mfe, (entry-l[j])/risk)
                mae = min(mae, (entry-h[j])/risk)
                hit_sl = h[j] >= sl
                hit_tp = l[j] <= tp
            if hit_sl and hit_tp:
                result = "SL"; ex = j; break
            if hit_sl:
                result = "SL"; ex = j; break
            if hit_tp:
                result = "TP"; ex = j; break
        if ex is None:
            continue
        out.append({
            "entry_time": pd.Timestamp(t[i]), "exit_time": pd.Timestamp(t[ex]),
            "side": "LONG" if side == 1 else "SHORT", "entry": entry,
            "sl": sl, "tp": tp, "result": result,
            "R": RR if result == "TP" else -1.0,
            "mfe_R": mfe, "mae_R": mae,
            "dist_ema20_atr": float(x.dist_ema20_atr.iloc[i]),
            "move5_atr": float(x.move5_atr.iloc[i]),
            "range_atr": float(x.range_atr.iloc[i]),
            "h4_adx_pct": float(x.h4_adx_pct.iloc[i]),
        })
        last = ex
    return pd.DataFrame(out)


def summarize(name, frames):
    if not frames:
        return {"variant": name, "trades": 0, "wins": 0, "losses": 0, "winrate_pct": 0.0, "net_R": 0.0, "profit_factor": 0.0, "expectancy_R": 0.0, "max_dd_R": 0.0, "median_entry_dist_ema_atr": np.nan, "median_entry_move5_atr": np.nan}
    tr = pd.concat(frames, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
    wins = int((tr.result == "TP").sum())
    losses = int((tr.result == "SL").sum())
    net = float(tr.R.sum())
    gp = float(tr.loc[tr.R > 0, "R"].sum())
    gl = float(-tr.loc[tr.R < 0, "R"].sum())
    pf = gp/gl if gl else float("inf")
    eq = tr.R.cumsum(); dd = eq-eq.cummax()
    return {
        "variant": name, "trades": len(tr), "wins": wins, "losses": losses,
        "winrate_pct": 100*wins/len(tr), "net_R": net, "profit_factor": pf,
        "expectancy_R": float(tr.R.mean()), "max_dd_R": float(dd.min()),
        "median_entry_dist_ema_atr": float(tr.dist_ema20_atr.median()),
        "median_entry_move5_atr": float(tr.move5_atr.median()),
        "median_MFE_R": float(tr.mfe_R.median()), "median_MAE_R": float(tr.mae_R.median()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--out", type=Path, default=Path("v15_engine_research_results.csv"))
    args = ap.parse_args()

    results = []; trade_frames = []
    for coin in COINS:
        try:
            raw = fetch_binance_15m(coin, args.days)
            x = enrich(raw)
            base_l, base_s = signal_mask(x, CORE_NAME)
            early_l, early_s = masks_early(x)
            loose_l, loose_s = masks_early_loose(x)
            for variant, lm, sm, stop in [
                ("V15_BASELINE", base_l, base_s, "baseline"),
                ("V15_EARLY", early_l, early_s, "baseline"),
                ("V15_EARLY_STRUCTURE_STOP", early_l, early_s, "structure"),
                ("V15_EARLY_LOOSE", loose_l, loose_s, "baseline"),
                ("V15_EARLY_LOOSE_STRUCTURE_STOP", loose_l, loose_s, "structure"),
            ]:
                tr = run_backtest(x, lm, sm, stop)
                if not tr.empty:
                    tr.insert(0, "coin", coin)
                    tr.insert(1, "variant", variant)
                    trade_frames.append(tr)
                results.append({"coin": coin, **summarize(variant, [tr] if not tr.empty else [])})
            print(f"{coin:5s} | candles={len(x):6d} | base={int(base_l.sum()+base_s.sum()):3d} | early={int(early_l.sum()+early_s.sum()):3d} | loose={int(loose_l.sum()+loose_s.sum()):3d}")
        except Exception as e:
            print(f"{coin:5s} | ERROR | {type(e).__name__}: {e}")

    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    all_trades.to_csv(args.out.with_name("v15_engine_research_trades.csv"), index=False)
    summary_rows = []
    for v in ["V15_BASELINE", "V15_EARLY", "V15_EARLY_STRUCTURE_STOP", "V15_EARLY_LOOSE", "V15_EARLY_LOOSE_STRUCTURE_STOP"]:
        tr = all_trades[all_trades.variant == v] if not all_trades.empty else pd.DataFrame()
        summary_rows.append(summarize(v, [tr] if not tr.empty else []))
    pd.DataFrame(summary_rows).to_csv(args.out, index=False)

    print("\n" + "="*100)
    print(pd.DataFrame(summary_rows).to_string(index=False, float_format=lambda z: f"{z:.3f}"))
    print("="*100)
    print(f"SUMMARY: {args.out.resolve()}")
    print(f"TRADES : {args.out.with_name('v15_engine_research_trades.csv').resolve()}")


if __name__ == "__main__":
    main()
