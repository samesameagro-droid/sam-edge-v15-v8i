#!/usr/bin/env python3
"""Six-month, independent SHORT comparison for SAM EDGE V15.

Uses public BingX USDT-perpetual 15m OHLCV, V15's deployed core/precision gate,
and the V15.2 SHORT shadow rules. No live state, orders, or Telegram access.
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from core_engine_v15 import (
    COINS, CORE_NAME, RR, MAX_HOLD_BARS, enrich, signal_mask, trade_levels,
    short_precision_shadow_snapshot,
)

TIMEFRAME = "15m"
MAX_ACTIVE = 5
PRECISION_SCORE_MAX = 65.0
FEE_RATE = 0.0005       # assumed taker fee per side; override via CLI
SLIPPAGE_RATE = 0.0002  # assumed adverse slippage per side; override via CLI
OUT = Path("artifacts")
WARMUP_BARS = 3600


def utc_ms(dt):
    return int(dt.timestamp() * 1000)


def fetch_history(exchange, symbol, since_ms, until_ms):
    rows, cursor = [], since_ms
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        last = int(batch[-1][0])
        nxt = last + 15 * 60 * 1000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 2:
            break
        time.sleep(exchange.rateLimit / 1000)
    if not rows:
        raise RuntimeError(f"No OHLCV returned for {symbol}")
    df = pd.DataFrame(rows, columns=["timestamp_ms","open","high","low","close","volume"])
    df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
    df["timestamp"] = pd.to_datetime(df.timestamp_ms, unit="ms", utc=True)
    df = df[df.timestamp < pd.Timestamp(until_ms, unit="ms", tz="UTC")]
    return df[["timestamp","open","high","low","close","volume"]].reset_index(drop=True)


def score_at(x, i, side):
    adx_pct = float(x.h4_adx_pct.iloc[i])
    adx_delta = float(x.h4_adx_delta.iloc[i])
    adx_floor, delta_floor = 0.85, 0.90
    adx_margin = float(np.clip((adx_pct-adx_floor)/(1-adx_floor), 0, 1))
    delta_margin = float(np.clip((adx_delta-delta_floor)/2.0, 0, 1))
    room = float(x.dist_sup_atr.iloc[i])
    room_margin = float(np.clip((room-0.85)/1.50, 0, 1))
    dist = float(x.dist_ema20_atr.iloc[i])
    loc_center = float(np.clip(1.0-abs(dist-0.60)/0.60, 0, 1))
    vol_margin = float(np.clip(float(x.volr.iloc[i])/2.0, 0, 1))
    trigger_margin = float(np.clip(float(x.body_atr.iloc[i])/0.80, 0, 1))
    return 100.0*(0.30*adx_margin + 0.20*delta_margin + 0.20*room_margin +
                  0.15*loc_center + 0.10*vol_margin + 0.05*trigger_margin)


def precision_snapshot(x, i):
    touch = x.high >= x.ema20 - 0.60*x.atr
    fresh = any(bool(touch.iloc[i-age]) for age in (1,2) if i-age >= 0)
    adx_pct, adx_delta = float(x.h4_adx_pct.iloc[i]), float(x.h4_adx_delta.iloc[i])
    volr, dist = float(x.volr.iloc[i]), float(x.dist_ema20_atr.iloc[i])
    adx_ok = not (adx_pct >= 0.90 and adx_delta < 0)
    return {"fresh_pullback":fresh, "adx_ok":adx_ok, "vol_ok":volr>=1.20,
            "ema_ok":dist<=0.80, "e_pass":fresh and adx_ok and volr>=1.20 and dist<=0.80}


def build_candidates(coin, x, btc):
    _, regular_short = signal_mask(x, CORE_NAME)
    # Align only already-completed BTC HTF features; enrich() uses strict as-of joins.
    bx = btc[["timestamp","h1_trend_bear","h4_trend_bear"]].rename(
        columns={"h1_trend_bear":"btc_h1_bear","h4_trend_bear":"btc_h4_bear"})
    z = pd.merge_asof(x.sort_values("timestamp"), bx.sort_values("timestamp"),
                      on="timestamp", direction="backward", allow_exact_matches=False)
    out = {"regular": [], "shadow_core": [], "shadow_btc_h1": [], "shadow_btc_h1_no_h4": []}
    for i in range(300, len(z)-1):
        if not all(np.isfinite(float(z[c].iloc[i])) for c in
                   ["atr","h4_adx_pct","h4_adx_delta","dist_sup_atr","dist_ema20_atr","volr","body_atr"]):
            continue
        precision = precision_snapshot(z, i)
        score = score_at(z, i, "SHORT")
        base_short = bool(regular_short.iloc[i])
        if base_short and precision["e_pass"] and score < PRECISION_SCORE_MAX:
            out["regular"].append(make_candidate(coin, z, i, score, "regular"))
        btc_ctx = {"h1_bear":bool(z.btc_h1_bear.iloc[i]), "h4_bear":bool(z.btc_h4_bear.iloc[i])}
        snap = short_precision_shadow_snapshot(z, i, "SHORT", btc_ctx, precision)
        if snap.get("variant_core_v1_pass"):
            out["shadow_core"].append(make_candidate(coin, z, i, score, "shadow_core"))
        if snap.get("variant_btc_h1_v1_pass"):
            out["shadow_btc_h1"].append(make_candidate(coin, z, i, score, "shadow_btc_h1"))
        if snap.get("variant_btc_h1_without_h4_v1_pass"):
            out["shadow_btc_h1_no_h4"].append(make_candidate(coin, z, i, score, "shadow_btc_h1_no_h4"))
    return out


def make_candidate(coin, x, i, score, strategy):
    # Signal is confirmed at candle close; model entry at next candle open.
    entry_i = i+1
    entry = float(x.open.iloc[entry_i])
    atr = float(x.atr.iloc[i])
    anchor = float(x.high.iloc[max(0,i-8):i].max())
    sl = anchor + 0.65*atr
    risk = sl-entry
    if risk < 0.80*atr: sl = entry + 0.80*atr
    elif risk > 2.50*atr: sl = entry + 2.50*atr
    risk = sl-entry
    if not np.isfinite(risk) or risk <= 0: return None
    tp = entry - RR*risk
    # Adverse entry slippage for a short.
    entry *= (1.0-SLIPPAGE_RATE)
    risk = sl-entry
    tp = entry - RR*risk
    return {"coin":coin,"strategy":strategy,"signal_time":x.timestamp.iloc[i].isoformat(),
            "entry_time":x.timestamp.iloc[entry_i].isoformat(),"entry_i":entry_i,
            "entry":entry,"sl":sl,"tp":tp,"risk":risk,"score":score}


def simulate_one(x, c, fee_rate, slippage_rate):
    start = c["entry_i"]
    end = min(len(x), start+MAX_HOLD_BARS)
    result, exit_i, exit_px = "TIMEOUT", end-1, float(x.close.iloc[end-1])
    for j in range(start, end):
        hi, lo = float(x.high.iloc[j]), float(x.low.iloc[j])
        hit_sl, hit_tp = hi >= c["sl"], lo <= c["tp"]
        if hit_sl and hit_tp:
            result, exit_i, exit_px = "SL", j, c["sl"]  # conservative same-bar ordering
            break
        if hit_sl:
            result, exit_i, exit_px = "SL", j, c["sl"]
            break
        if hit_tp:
            result, exit_i, exit_px = "TP", j, c["tp"]
            break
    # Adverse cover slippage and taker fees on both sides.
    exit_px *= (1.0+slippage_rate)
    gross_r = (c["entry"]-exit_px)/c["risk"]
    cost_r = (c["entry"]*fee_rate + exit_px*fee_rate)/c["risk"]
    net_r = gross_r-cost_r
    return {**{k:v for k,v in c.items() if k!="entry_i"},
            "exit_time":x.timestamp.iloc[exit_i].isoformat(),"exit":exit_px,
            "result":result,"gross_R":round(gross_r,5),"cost_R":round(cost_r,5),
            "R":round(net_r,5)}


def portfolio_replay(candidates, frames, fee_rate, slippage_rate):
    ready=[]
    for c in candidates:
        if c is None: continue
        tr=simulate_one(frames[c["coin"]],c,fee_rate,slippage_rate)
        ready.append(tr)
    ready.sort(key=lambda r:(r["entry_time"],-r["score"],r["coin"]))
    active, accepted, skipped, coin_exit = [], [], 0, {}
    for tr in ready:
        t=pd.Timestamp(tr["entry_time"])
        active=[a for a in active if pd.Timestamp(a["exit_time"]) > t]
        if tr["coin"] in coin_exit and pd.Timestamp(coin_exit[tr["coin"]]) > t:
            continue
        if len(active)>=MAX_ACTIVE:
            skipped+=1
            continue
        accepted.append(tr); active.append(tr); coin_exit[tr["coin"]]=tr["exit_time"]
    return accepted, skipped, len(ready)


def metrics(rows):
    rs=[float(r["R"]) for r in rows]
    wins=sum(v>0 for v in rs); losses=sum(v<0 for v in rs)
    gp=sum(v for v in rs if v>0); gl=-sum(v for v in rs if v<0)
    eq=np.cumsum(rs); peak=np.maximum.accumulate(np.r_[0.0,eq])[1:] if len(eq) else np.array([])
    dd=(peak-eq) if len(eq) else np.array([])
    return {"trades":len(rs),"wins":wins,"losses":losses,"timeouts":sum(r["result"]=="TIMEOUT" for r in rows),
            "win_rate_pct":round(100*wins/len(rs),2) if rs else None,
            "net_R":round(sum(rs),4),"profit_factor":round(gp/gl,4) if gl else ("infinity" if gp else None),
            "expectancy_R":round(float(np.mean(rs)),4) if rs else None,
            "max_drawdown_R":round(float(dd.max()),4) if len(dd) else 0.0}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--months",type=int,default=6)
    ap.add_argument("--fee-rate",type=float,default=FEE_RATE)
    ap.add_argument("--slippage-rate",type=float,default=SLIPPAGE_RATE)
    args=ap.parse_args()
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    end=now.replace(minute=(now.minute//15)*15)
    start=end-timedelta(days=30.4375*args.months)
    fetch_start=start-timedelta(minutes=15*WARMUP_BARS)
    exchange=ccxt.bingx({"enableRateLimit":True,"timeout":30000,"options":{"defaultType":"swap"}})
    exchange.load_markets()
    coins=list(dict.fromkeys(["BTC"]+COINS))
    frames={}
    fetch_errors={}
    for coin in coins:
        symbol=f"{coin}/USDT:USDT"
        try:
            raw=fetch_history(exchange,symbol,utc_ms(fetch_start),utc_ms(end))
            if len(raw)<WARMUP_BARS+100:
                raise RuntimeError(f"Insufficient rows: {len(raw)}")
            frames[coin]=enrich(raw)
            print(f"DATA OK | {coin} | rows={len(raw)} | {raw.timestamp.iloc[0]} -> {raw.timestamp.iloc[-1]}")
        except Exception as e:
            fetch_errors[coin]=f"{type(e).__name__}: {e}"
            print(f"DATA ERROR | {coin} | {fetch_errors[coin]}")
    if "BTC" not in frames: raise SystemExit("BTC history unavailable; cannot evaluate BTC-conditioned Shadow variants.")
    # Clip evaluation window after warm-up; retain full frame for indicator context and exits.
    btc=frames["BTC"]
    all_candidates={k:[] for k in ["regular","shadow_core","shadow_btc_h1","shadow_btc_h1_no_h4"]}
    for coin in COINS:
        if coin not in frames: continue
        z=frames[coin]
        # candidate builder currently scans all rows; restrict signals to requested six-month window.
        cands=build_candidates(coin,z,btc)
        for name, arr in cands.items():
            all_candidates[name].extend(c for c in arr if c and pd.Timestamp(c["signal_time"])>=pd.Timestamp(start))
    OUT.mkdir(exist_ok=True)
    report={"created_at":datetime.now(timezone.utc).isoformat(),"period_start":start.isoformat(),
            "period_end":end.isoformat(),"months":args.months,"timeframe":TIMEFRAME,
            "exchange":"BingX USDT perpetual public OHLCV","coins_requested":COINS,
            "coins_loaded":sorted(k for k in frames if k!="BTC"),"fetch_errors":fetch_errors,
            "model":{"regular":"V15 core signal_mask SHORT + current V15 precision gate (e_pass and score < 65)",
                     "shadow":"independent V15.2 SHORT precision predicates; no regular signal prerequisite",
                     "entry":"next 15m candle open after signal candle close","RR":RR,
                     "max_hold_bars":MAX_HOLD_BARS,"portfolio_max_active":MAX_ACTIVE,
                     "same_bar_SL_TP":"SL first (conservative)","fee_rate_per_side":args.fee_rate,
                     "slippage_rate_per_side":args.slippage_rate,
                     "note":"Per-strategy portfolio replay, max 5 active positions, one active position per coin. No parameter fitting."},
            "results":{}}
    for name,cands in all_candidates.items():
        rows,skipped,total=portfolio_replay(cands,frames,args.fee_rate,args.slippage_rate)
        pd.DataFrame(rows).to_csv(OUT/f"short_6m_{name}_trades.csv",index=False)
        report["results"][name]={**metrics(rows),"candidate_events":total,
                                 "skipped_capacity":skipped}
    report["comparison_note"]="This is a historical simulation, not evidence of future profitability. Check fetch coverage and out-of-sample results before any live promotion."
    (OUT/"short_6m_comparison.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
