from __future__ import annotations

import argparse
import io
import time
import zipfile
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
ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}USDT/15m/{symbol}USDT-15m-{month}.zip"
S = requests.Session()
S.headers.update({"User-Agent": "SAM-EDGE-V15-RESEARCH/2.0"})


def month_range(start: datetime, end: datetime):
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    while cur <= end:
        yield cur.strftime("%Y-%m")
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            cur = datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)


def _clean(rows, start, end):
    if not rows:
        return pd.DataFrame()
    q = pd.DataFrame(rows, columns=["timestamp","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"])
    q = q[["timestamp","open","high","low","close","volume"]].copy()
    q["timestamp"] = pd.to_datetime(q["timestamp"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q = q.dropna().drop_duplicates("timestamp").sort_values("timestamp")
    return q[(q.timestamp >= pd.Timestamp(start)) & (q.timestamp <= pd.Timestamp(end))].reset_index(drop=True)


def fetch_api(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows = []
    cursor = start_ms
    for _ in range(30):
        r = S.get(API, params={
            "symbol": f"{symbol}USDT", "interval": "15m",
            "startTime": cursor, "endTime": end_ms, "limit": 1500,
        }, timeout=45)
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
        time.sleep(0.08)
    return _clean(rows, start, end)


def fetch_archive(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    chunks = []
    for month in month_range(start, end):
        url = ARCHIVE.format(symbol=symbol, month=month)
        r = S.get(url, timeout=90)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = [n for n in z.namelist() if n.lower().endswith('.csv')]
            if not names:
                continue
            raw = pd.read_csv(z.open(names[0]), header=None)
        if raw.shape[1] < 6:
            continue
        # Binance Data Vision files may be headered or headerless depending on period.
        first = str(raw.iloc[0, 0]).strip().lower()
        if first in {'open_time', 'open time', 'timestamp'} or not str(raw.iloc[0, 0]).replace('.', '', 1).isdigit():
            raw = raw.iloc[1:].reset_index(drop=True)
        q = pd.DataFrame({
            'timestamp': pd.to_datetime(pd.to_numeric(raw.iloc[:, 0], errors='coerce'), unit='ms', utc=True),
            'open': pd.to_numeric(raw.iloc[:, 1], errors='coerce'),
            'high': pd.to_numeric(raw.iloc[:, 2], errors='coerce'),
            'low': pd.to_numeric(raw.iloc[:, 3], errors='coerce'),
            'close': pd.to_numeric(raw.iloc[:, 4], errors='coerce'),
            'volume': pd.to_numeric(raw.iloc[:, 5], errors='coerce'),
        }).dropna()
        chunks.append(q)
        time.sleep(0.05)
    if not chunks:
        return pd.DataFrame()
    out = pd.concat(chunks, ignore_index=True).drop_duplicates('timestamp').sort_values('timestamp')
    return out[(out.timestamp >= pd.Timestamp(start)) & (out.timestamp <= pd.Timestamp(end))].reset_index(drop=True)


def fetch(symbol: str, days: int) -> tuple[pd.DataFrame, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        df = fetch_api(symbol, start, end)
        if len(df) >= max(500, days * 24 * 4 - 100):
            return df, 'fapi'
        print(f"{symbol}: FAPI returned only {len(df)} rows; falling back to Data Vision archive")
    except Exception as e:
        print(f"{symbol}: FAPI ERROR {type(e).__name__}: {e}; falling back to Data Vision archive")
    df = fetch_archive(symbol, start, end)
    if df.empty:
        raise RuntimeError(f"no Binance historical data from FAPI or Data Vision for {symbol}")
    return df, 'archive'


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
        out.append({
            'entry_time': pd.Timestamp(T[i]), 'exit_time': pd.Timestamp(T[ex]),
            'side': 'LONG' if side == 1 else 'SHORT', 'entry': e,
            'sl': sl, 'tp': tp, 'result': res,
            'R': RR if res == 'TP' else -1.0,
            'mfe_R': mfe, 'mae_R': mae,
            'dist_ema20_atr': float(x.dist_ema20_atr.iloc[i]),
            'move5_atr': float(x.move5_atr.iloc[i]),
            'range_atr': float(x.range_atr.iloc[i]),
            'h4_adx_pct': float(x.h4_adx_pct.iloc[i]),
        })
        last = ex
    return pd.DataFrame(out)


def stats(name, tr):
    if tr.empty:
        return {'variant': name, 'trades': 0, 'wins': 0, 'losses': 0, 'winrate_pct': 0.0, 'net_R': 0.0, 'profit_factor': 0.0, 'expectancy_R': 0.0, 'max_dd_R': 0.0, 'median_dist_ema_atr': np.nan, 'median_move5_atr': np.nan, 'median_MFE_R': np.nan, 'median_MAE_R': np.nan}
    w = int((tr.result == 'TP').sum()); l = int((tr.result == 'SL').sum())
    gp = tr.loc[tr.R > 0, 'R'].sum(); gl = -tr.loc[tr.R < 0, 'R'].sum()
    eq = tr.R.cumsum(); dd = eq - eq.cummax()
    return {'variant': name, 'trades': len(tr), 'wins': w, 'losses': l, 'winrate_pct': 100*w/len(tr), 'net_R': tr.R.sum(), 'profit_factor': gp/gl if gl else float('inf'), 'expectancy_R': tr.R.mean(), 'max_dd_R': dd.min(), 'median_dist_ema_atr': tr.dist_ema20_atr.median(), 'median_move5_atr': tr.move5_atr.median(), 'median_MFE_R': tr.mfe_R.median(), 'median_MAE_R': tr.mae_R.median()}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int, default=180); ap.add_argument('--out', type=Path, default=Path('v15_engine_compare_results.csv')); a = ap.parse_args()
    frames, diags = [], []
    for coin in COINS:
        try:
            raw, source = fetch(coin, a.days)
            if len(raw) < 1000:
                raise RuntimeError(f'insufficient candles: {len(raw)}')
            x = enrich(raw)
            old_l, old_s = signal_mask(x, LEGACY_CORE_NAME)
            new_l, new_s = signal_mask(x, CORE_NAME)
            diag = {
                'coin': coin, 'data_source': source, 'candles': len(x),
                'start': str(x.timestamp.iloc[0]), 'end': str(x.timestamp.iloc[-1]),
                'legacy_long_bars': int(old_l.sum()), 'legacy_short_bars': int(old_s.sum()),
                'early_long_bars': int(new_l.sum()), 'early_short_bars': int(new_s.sum()),
                'legacy_signal_bars': int(old_l.sum()+old_s.sum()),
                'early_signal_bars': int(new_l.sum()+new_s.sum()), 'error': ''
            }
            diags.append(diag)
            print(f"{coin:5s} source={source:7s} candles={len(x):6d} legacy={diag['legacy_signal_bars']:3d} early={diag['early_signal_bars']:3d}")
            configs = [
                ('LEGACY_BASELINE', old_l, old_s, 'LEGACY'),
                ('EARLY_RECLAIM_LEGACY_STOP', new_l, new_s, 'LEGACY'),
                ('EARLY_RECLAIM_STRUCTURE_STOP', new_l, new_s, 'STRUCTURE'),
            ]
            for name, lm, sm, stop in configs:
                tr = run(x, lm, sm, stop)
                if not tr.empty:
                    tr.insert(0, 'coin', coin); tr.insert(1, 'variant', name); frames.append(tr)
        except Exception as e:
            diags.append({'coin': coin, 'data_source': '', 'candles': 0, 'start': '', 'end': '', 'legacy_long_bars': 0, 'legacy_short_bars': 0, 'early_long_bars': 0, 'early_short_bars': 0, 'legacy_signal_bars': 0, 'early_signal_bars': 0, 'error': f'{type(e).__name__}: {e}'})
            print(f"{coin:5s} ERROR {type(e).__name__}: {e}")

    tr = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    names = ['LEGACY_BASELINE', 'EARLY_RECLAIM_LEGACY_STOP', 'EARLY_RECLAIM_STRUCTURE_STOP']
    summary = pd.DataFrame([stats(v, tr[tr.variant == v] if not tr.empty else pd.DataFrame()) for v in names])
    summary.to_csv(a.out, index=False)
    tr.to_csv(a.out.with_name('v15_engine_compare_trades.csv'), index=False)
    pd.DataFrame(diags).to_csv(a.out.with_name('v15_engine_compare_diagnostics.csv'), index=False)
    print('\n' + '='*110); print(summary.to_string(index=False, float_format=lambda z: f'{z:.3f}')); print('='*110)
    if not tr.empty:
        print(f"TOTAL CLOSED TRADES={len(tr)}")
    errors = [d for d in diags if d.get('error')]
    if errors == diags:
        raise RuntimeError('ALL COINS FAILED DATA/ENRICHMENT; inspect v15_engine_compare_diagnostics.csv')

if __name__ == '__main__':
    main()
