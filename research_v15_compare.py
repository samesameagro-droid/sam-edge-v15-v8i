from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from core_engine_v15 import (
    COINS, CORE_NAME, LEGACY_CORE_NAME, RR, MAX_HOLD_BARS,
    enrich, signal_mask, trade_levels,
)

API = "https://fapi.binance.com/fapi/v1/klines"
S = requests.Session()
S.headers.update({"User-Agent": "SAM-EDGE-V15-RESEARCH/1.0"})


def fetch(symbol: str, days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows = []
    cursor = start_ms
    for attempt in range(20):
        r = S.get(API, params={"symbol": f"{symbol}USDT", "interval": "15m", "startTime": cursor, "endTime": end_ms, "limit": 1500}, timeout=45)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        last = int(batch[-1][0])
        nxt = last + 15 * 60 * 1000
        if nxt <= cursor or len(batch) < 1500:
            break
        cursor = nxt
        time.sleep(0.05)
    if not rows:
        raise RuntimeError(f"no Binance futures data for {symbol}")
    q = pd.DataFrame(rows, columns=["timestamp","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"])
    q = q[["timestamp","open","high","low","close","volume"]].copy()
    q["timestamp"] = pd.to_datetime(q["timestamp"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q = q.dropna().drop_duplicates("timestamp").sort_values("timestamp")
    return q[(q.timestamp >= pd.Timestamp(start)) & (q.timestamp <= pd.Timestamp(end))].reset_index(drop=True)


def run(x, lm, sm, stop_style):
    idx = sorted([(int(i), 1) for i in np.flatnonzero(lm.to_numpy()) if i >= 250] + [(int(i), -1) for i in np.flatnonzero(sm.to_numpy()) if i >= 250])
    H, L, T = x.high.to_numpy(), x.low.to_numpy(), x.timestamp.to_numpy()
    out, last = [], -1
    for i, side in idx:
        if i <= last:
            continue
        lv = trade_levels(x, i, side, stop_style)
        if lv is None:
            continue
        e, sl, tp, risk = lv
        ex = None; res = None; mfe = 0.0; mae = 0.0
        for j in range(i + 1, min(len(x), i + 1 + MAX_HOLD_BARS)):
            if side == 1:
                mfe = max(mfe, (H[j] - e) / risk); mae = min(mae, (L[j] - e) / risk)
                hit_sl, hit_tp = L[j] <= sl, H[j] >= tp
            else:
                mfe = max(mfe, (e - L[j]) / risk); mae = min(mae, (e - H[j]) / risk)
                hit_sl, hit_tp = H[j] >= sl, L[j] <= tp
            if hit_sl and hit_tp: res, ex = 'SL', j; break
            if hit_sl: res, ex = 'SL', j; break
            if hit_tp: res, ex = 'TP', j; break
        if ex is None:
            continue
        out.append({"entry_time": pd.Timestamp(T[i]), "exit_time": pd.Timestamp(T[ex]), "side": "LONG" if side == 1 else "SHORT", "entry": e, "sl": sl, "tp": tp, "result": res, "R": RR if res == 'TP' else -1.0, "mfe_R": mfe, "mae_R": mae, "dist_ema20_atr": float(x.dist_ema20_atr.iloc[i]), "move5_atr": float(x.move5_atr.iloc[i])})
        last = ex
    return pd.DataFrame(out)


def stats(name, tr):
    if tr.empty:
        return {"variant": name, "trades": 0, "wins": 0, "losses": 0, "winrate_pct": 0.0, "net_R": 0.0, "profit_factor": 0.0, "expectancy_R": 0.0, "max_dd_R": 0.0, "median_dist_ema_atr": np.nan, "median_move5_atr": np.nan, "median_MFE_R": np.nan, "median_MAE_R": np.nan}
    w = int((tr.result == 'TP').sum()); l = int((tr.result == 'SL').sum())
    gp = tr.loc[tr.R > 0, 'R'].sum(); gl = -tr.loc[tr.R < 0, 'R'].sum()
    eq = tr.R.cumsum(); dd = eq - eq.cummax()
    return {"variant": name, "trades": len(tr), "wins": w, "losses": l, "winrate_pct": 100*w/len(tr), "net_R": tr.R.sum(), "profit_factor": gp/gl if gl else float('inf'), "expectancy_R": tr.R.mean(), "max_dd_R": dd.min(), "median_dist_ema_atr": tr.dist_ema20_atr.median(), "median_move5_atr": tr.move5_atr.median(), "median_MFE_R": tr.mfe_R.median(), "median_MAE_R": tr.mae_R.median()}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int, default=180); ap.add_argument('--out', type=Path, default=Path('v15_engine_compare_results.csv')); a = ap.parse_args()
    frames, diags = [], []
    for coin in COINS:
        try:
            raw = fetch(coin, a.days)
            x = enrich(raw)
            old_l, old_s = signal_mask(x, LEGACY_CORE_NAME); new_l, new_s = signal_mask(x, CORE_NAME)
            diag = {"coin": coin, "candles": len(x), "legacy_signal_bars": int(old_l.sum()+old_s.sum()), "early_signal_bars": int(new_l.sum()+new_s.sum()), "error": ""}
            diags.append(diag)
            print(f"{coin:5s} candles={len(x):6d} legacy={diag['legacy_signal_bars']:3d} early={diag['early_signal_bars']:3d}")
            for name, lm, sm, stop in [('LEGACY_BASELINE', old_l, old_s, 'LEGACY'), ('EARLY_RECLAIM_LEGACY_STOP', new_l, new_s, 'LEGACY'), ('EARLY_RECLAIM_STRUCTURE_STOP', new_l, new_s, 'STRUCTURE')]:
                tr = run(x, lm, sm, stop)
                if not tr.empty:
                    tr.insert(0, 'coin', coin); tr.insert(1, 'variant', name); frames.append(tr)
        except Exception as e:
            diags.append({"coin": coin, "candles": 0, "legacy_signal_bars": 0, "early_signal_bars": 0, "error": f"{type(e).__name__}: {e}"})
            print(f"{coin:5s} ERROR {type(e).__name__}: {e}")
    tr = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    names = ['LEGACY_BASELINE', 'EARLY_RECLAIM_LEGACY_STOP', 'EARLY_RECLAIM_STRUCTURE_STOP']
    summary = pd.DataFrame([stats(v, tr[tr.variant == v] if not tr.empty else pd.DataFrame()) for v in names])
    summary.to_csv(a.out, index=False); tr.to_csv(a.out.with_name('v15_engine_compare_trades.csv'), index=False); pd.DataFrame(diags).to_csv(a.out.with_name('v15_engine_compare_diagnostics.csv'), index=False)
    print('\n' + summary.to_string(index=False, float_format=lambda z: f'{z:.3f}'))
    errors = [d for d in diags if d.get('error')]
    if errors == diags:
        raise RuntimeError('ALL COINS FAILED DATA/ENRICHMENT; inspect v15_engine_compare_diagnostics.csv')

if __name__ == '__main__':
    main()
