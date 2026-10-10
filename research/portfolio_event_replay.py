#!/usr/bin/env python3
"""Research-only full-event Binance USD-M portfolio replay; no orders are placed."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core_engine_v15 import (
    CORE_NAME, LEGACY_CORE_NAME, RR, MAX_HOLD_BARS, enrich, signal_mask, trade_levels,
    ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
    EARLY_ROOM_MIN_ATR,
)
from mtf_30m_replay import fetch_klines, INTERVALS

SYMBOLS = ["1000PEPEUSDT","AAVEUSDT","ARBUSDT","DOTUSDT","ETHUSDT","FILUSDT",
"IMXUSDT","INJUSDT","JUPUSDT","LDOUSDT","LINKUSDT","LTCUSDT","NEARUSDT",
"ORDIUSDT","QNTUSDT","RUNEUSDT","SANDUSDT","STRKUSDT","SUIUSDT","TAOUSDT",
"TIAUSDT","WLDUSDT","XRPUSDT","ZROUSDT"]
TF = "15m"
BAR_MS = INTERVALS[TF] * 60_000
MIN_24H_QUOTE_VOLUME = 10_000_000.0
START_EQUITY = 100.0
RISK_PCT = 0.01
MAX_ACTIVE = 5
MAX_ACTIVE_PER_SIDE = 3
COOLDOWN_MS = 60 * 60_000
FEE_BPS = 5.0
SLIPPAGE_BPS = 2.0
SCORE_THRESHOLDS = [40,45,48,50,51,52,53,54,55,56,58,60,62,65]


def c01(v):
    return float(np.clip(v, 0.0, 1.0))


def score_at(x, i, side):
    ap, ad = float(x.h4_adx_pct.iloc[i]), float(x.h4_adx_delta.iloc[i])
    af, dfloor = (ADX_LONG_PCT, ADX_LONG_DELTA) if side == "LONG" else (ADX_SHORT_PCT, ADX_SHORT_DELTA)
    room = float(x.dist_res_atr.iloc[i] if side == "LONG" else x.dist_sup_atr.iloc[i])
    dist = float(x.dist_ema20_atr.iloc[i])
    return 100.0 * (
        .30*c01((ap-af)/(1-af)) + .20*c01((ad-dfloor)/2.0)
        + .20*c01((room-EARLY_ROOM_MIN_ATR)/1.50)
        + .15*c01(1.0-abs(dist-.60)/.60)
        + .10*c01(float(x.volr.iloc[i])/2.0)
        + .05*c01(float(x.body_atr.iloc[i])/.80)
    )


def precision_pass_at(x, i, side, score):
    if i < 2:
        return False
    if side == "LONG":
        touch = x.low <= x.ema20 + .60*x.atr
    else:
        touch = x.high >= x.ema20 - .60*x.atr
    fresh = bool(touch.iloc[i-1] or touch.iloc[i-2])
    adx_pct, adx_delta = float(x.h4_adx_pct.iloc[i]), float(x.h4_adx_delta.iloc[i])
    return fresh and not (adx_pct >= .90 and adx_delta < 0) and float(x.volr.iloc[i]) >= 1.20 and float(x.dist_ema20_atr.iloc[i]) <= .80 and score < 65.0


def prepare(start_ms, end_ms):
    data, coverage, errors, candidates = {}, [], [], []
    allowed = {"binance_rest","binance_archive_monthly","binance_archive_daily"}
    for symbol in SYMBOLS:
        try:
            raw = fetch_klines(symbol, TF, start_ms, end_ms)
        except Exception as e:
            errors.append({"symbol":symbol,"error":f"fetch {type(e).__name__}: {e}"})
            continue
        if raw.empty:
            errors.append({"symbol":symbol,"error":"no Binance USD-M 15m candles"})
            continue
        sources = set(raw.source.dropna().astype(str)) if "source" in raw else set()
        if not sources or not sources.issubset(allowed) or raw.source.isna().any():
            errors.append({"symbol":symbol,"error":"invalid/missing candle provenance","sources":sorted(sources)})
        raw = raw.sort_values("open_ms").drop_duplicates("open_ms").reset_index(drop=True)
        gaps = int((raw.open_ms.diff().dropna() > BAR_MS*1.5).sum())
        if gaps:
            errors.append({"symbol":symbol,"error":"internal 15m gaps","gap_count":gaps})
        if len(raw) < 250:
            errors.append({"symbol":symbol,"error":"less than 250 warm-up candles","candles":len(raw)})
            continue
        latest = int(raw.open_ms.max())
        expected = ((end_ms-1)//BAR_MS)*BAR_MS
        if latest < expected-BAR_MS:
            errors.append({"symbol":symbol,"error":"stale candle coverage",
                "latest_open_utc":pd.to_datetime(latest,unit="ms",utc=True).isoformat(),
                "expected_open_utc":pd.to_datetime(expected,unit="ms",utc=True).isoformat()})
        x = pd.DataFrame({
            "timestamp":pd.to_datetime(raw.open_ms,unit="ms",utc=True),
            "open":pd.to_numeric(raw.open,errors="coerce"),
            "high":pd.to_numeric(raw.high,errors="coerce"),
            "low":pd.to_numeric(raw.low,errors="coerce"),
            "close":pd.to_numeric(raw.close,errors="coerce"),
            "volume":pd.to_numeric(raw.volume,errors="coerce"),
        }).dropna().reset_index(drop=True)
        x["quote_volume_24h"] = (x.close*x.volume).rolling(96,min_periods=96).sum()
        x = enrich(x)
        data[symbol] = x
        coverage.append({"symbol":symbol,"candles":len(x),
            "first_open_utc":x.timestamp.iloc[0].isoformat(),
            "last_open_utc":x.timestamp.iloc[-1].isoformat(),
            "source_counts":raw.source.value_counts().to_dict(),"gap_count":gaps})
        n_before, n_after, n_volume_reject = 0, 0, 0
        signals_by_core = {}
        for core_name in [CORE_NAME, LEGACY_CORE_NAME]:
            long_mask, short_mask = signal_mask(x, core_name)
            core_before, core_after = int((long_mask|short_mask).sum()), 0
            n_before += core_before
            for i in range(250,len(x)-1):
                if not bool(long_mask.iloc[i]) and not bool(short_mask.iloc[i]):
                    continue
                if pd.isna(x.quote_volume_24h.iloc[i]) or float(x.quote_volume_24h.iloc[i]) < MIN_24H_QUOTE_VOLUME:
                    n_volume_reject += 1
                    continue
                side = "LONG" if bool(long_mask.iloc[i]) else "SHORT"
                levels = trade_levels(x,i,1 if side=="LONG" else -1,"STRUCTURE")
                if levels is None:
                    continue
                close_entry, close_sl, close_tp, _ = levels
                atr = float(x.atr.iloc[i])
                if not np.isfinite(atr) or atr <= 0:
                    continue
                score = score_at(x,i,side)
                precision = precision_pass_at(x,i,side,score)
                # No lookahead: confirm signal on bar i, then execute at bar i+1 open.
                entry = float(x.open.iloc[i+1])
                stop_dist, target_dist = abs(float(close_entry)-float(close_sl)), abs(float(close_tp)-float(close_entry))
                sl, tp = (entry-stop_dist,entry+target_dist) if side=="LONG" else (entry+stop_dist,entry-target_dist)
                risk = abs(entry-sl)
                if not np.isfinite(risk) or risk <= 0:
                    continue
                candidates.append({"symbol":symbol,"core":core_name,"side":side,
                    "signal_bar_open_ms":int(x.timestamp.iloc[i].value//1_000_000),
                    "entry_time_ms":int(x.timestamp.iloc[i+1].value//1_000_000),
                    "entry":entry,"sl":float(sl),"tp":float(tp),"risk":float(risk),
                    "entry_close_proxy":float(close_entry),"stop_dist":float(stop_dist),"target_dist":float(target_dist),
                    "score":float(score),"precision_pass":bool(precision),
                    "quote_volume_24h":float(x.quote_volume_24h.iloc[i])})
                core_after += 1
                n_after += 1
            signals_by_core[core_name] = {"signals_before_volume_gate":core_before,"signals_after_volume_gate":core_after}
        coverage[-1].update({"signals_before_volume_gate":n_before,
            "signals_after_volume_gate":n_after,"signals_rejected_by_volume_gate":n_volume_reject,
            "signals_by_core":signals_by_core})
        print(f"PORTFOLIO_DATA {symbol}: candles={len(x)} candidates={n_after} gaps={gaps}",flush=True)
    return data,coverage,errors,candidates


def build_indices(data):
    maps={s:{int(ts.value//1_000_000):i for i,ts in enumerate(x.timestamp)} for s,x in data.items()}
    times=sorted({int(ts.value//1_000_000) for x in data.values() for ts in x.timestamp})
    return maps,times


def simulate(data,candidates,start_ms,end_ms,mode,force_close=False,entry_mode="next_open",distance_factor=1.0,exclude_symbol=None,fee_bps=FEE_BPS,slippage_bps=SLIPPAGE_BPS,bar_maps=None,global_times=None):
    def eligible(c):
        if exclude_symbol is not None and c["symbol"] == exclude_symbol: return False
        if not (start_ms <= c["entry_time_ms"] < end_ms): return False
        core = c.get("core", CORE_NAME)
        if mode=="baseline": return core==CORE_NAME
        if mode=="precision65": return core==CORE_NAME and c["precision_pass"]
        if mode.startswith("precision_score_"):
            return core==CORE_NAME and c["precision_pass"] and c["score"] <= float(mode.rsplit("_",1)[1])
        if mode=="legacy_baseline": return core==LEGACY_CORE_NAME
        if mode=="legacy_precision65": return core==LEGACY_CORE_NAME and c["precision_pass"]
        if mode=="combined_baseline": return True
        if mode=="combined_precision65": return c["precision_pass"]
        if mode=="without_current_core": return core==LEGACY_CORE_NAME and c["precision_pass"]
        if mode=="without_legacy_core": return core==CORE_NAME and c["precision_pass"]
        raise ValueError(mode)
    by_time={}
    for c in candidates:
        if eligible(c): by_time.setdefault(c["entry_time_ms"],[]).append(c)
    if bar_maps is None or global_times is None:
        bar_maps, global_times = build_indices(data)
    maps=bar_maps
    times=[t for t in global_times if start_ms<=t<end_ms]
    active,closed,last_exit=[],[],{}
    equity=START_EQUITY
    curve=[equity]
    max_seen=0

    def close(p,t,result,exit_price,held):
        nonlocal equity
        if result=="TP": gross=abs(p["tp"]-p["entry"])/p["risk"]
        elif result=="SL": gross=-1.0
        else: gross=(1.0 if p["side"]=="LONG" else -1.0)*(float(exit_price)-p["entry"])/p["risk"]
        stop_pct=p["risk"]/p["entry"] if p["entry"] else 0.0
        cost=(2*(fee_bps+slippage_bps)/10000)/stop_pct if stop_pct>0 else 0.0
        net=gross-cost
        pnl=p["risk_cash"]*net
        equity += pnl
        closed.append({"symbol":p["symbol"],"core":p.get("core",CORE_NAME),"side":p["side"],
            "signal_time_utc":pd.to_datetime(p["signal_bar_open_ms"],unit="ms",utc=True).isoformat(),
            "entry_time_utc":pd.to_datetime(p["entry_time_ms"],unit="ms",utc=True).isoformat(),
            "exit_time_utc":pd.to_datetime(t+BAR_MS,unit="ms",utc=True).isoformat(),
            "result":result,"entry":p["entry"],"sl":p["sl"],"tp":p["tp"],
            "exit_price":float(exit_price),"entry_score":p["score"],
            "risk_cash_usd":p["risk_cash"],"R_gross":gross,"R_net_est":net,
            "estimated_cost_R":cost,"pnl_usd_est":pnl,"equity_after":equity,
            "hold_bars":int(held),"quote_volume_24h":p["quote_volume_24h"]})
        curve.append(equity)
        last_exit[(p["symbol"],p["side"])]=t+BAR_MS

    for t in times:
        remain=[]
        # Resolve positions from earlier bars first, freeing slots for this scan.
        for p in active:
            if t <= p["entry_time_ms"]:
                remain.append(p); continue
            i=maps.get(p["symbol"],{}).get(t)
            if i is None:
                remain.append(p); continue
            b=data[p["symbol"]].iloc[i]
            stop=(float(b.low)<=p["sl"]) if p["side"]=="LONG" else (float(b.high)>=p["sl"])
            target=(float(b.high)>=p["tp"]) if p["side"]=="LONG" else (float(b.low)<=p["tp"])
            held=int((t-p["entry_time_ms"])//BAR_MS)+1
            if stop and target: close(p,t,"SL",p["sl"],held)
            elif stop: close(p,t,"SL",p["sl"],held)
            elif target: close(p,t,"TP",p["tp"],held)
            elif held>=MAX_HOLD_BARS: close(p,t,"TIMEOUT",float(b.close),held)
            else: remain.append(p)
        active=remain
        ranked=sorted(by_time.get(t,[]),key=lambda c:(-c["score"],c["symbol"],c["side"]))
        new=[]
        for c in ranked:
            if len(active)+len(new)>=MAX_ACTIVE: break
            if any(p["symbol"]==c["symbol"] for p in active+new): continue
            if sum(p["side"]==c["side"] for p in active+new)>=MAX_ACTIVE_PER_SIDE: continue
            last=last_exit.get((c["symbol"],c["side"]))
            if last is not None and 0<=t-last<COOLDOWN_MS: continue
            entry=float(c["entry"] if entry_mode=="next_open" else c["entry_close_proxy"])
            stop_dist=float(c["stop_dist"])*distance_factor
            target_dist=float(c["target_dist"])*distance_factor
            sl,tp=(entry-stop_dist,entry+target_dist) if c["side"]=="LONG" else (entry+stop_dist,entry-target_dist)
            new.append({**c,"entry":entry,"sl":sl,"tp":tp,"risk":stop_dist,"risk_cash":equity*RISK_PCT})
        active.extend(new)
        max_seen=max(max_seen,len(active))
        # Entry is at this bar's open, so test the newly opened position on this bar too.
        remain=[]
        for p in active:
            if p["entry_time_ms"]!=t:
                remain.append(p); continue
            i=maps.get(p["symbol"],{}).get(t)
            if i is None:
                remain.append(p); continue
            b=data[p["symbol"]].iloc[i]
            stop=(float(b.low)<=p["sl"]) if p["side"]=="LONG" else (float(b.high)>=p["sl"])
            target=(float(b.high)>=p["tp"]) if p["side"]=="LONG" else (float(b.low)<=p["tp"])
            if stop and target: close(p,t,"SL",p["sl"],1)
            elif stop: close(p,t,"SL",p["sl"],1)
            elif target: close(p,t,"TP",p["tp"],1)
            else: remain.append(p)
        active=remain
    if force_close and times:
        t=times[-1]
        for p in active[:]:
            x=data[p["symbol"]]
            valid=x[x.timestamp < pd.to_datetime(end_ms,unit="ms",utc=True)]
            if valid.empty: continue
            b=valid.iloc[-1]
            held=max(1,int((t-p["entry_time_ms"])//BAR_MS)+1)
            close(p,t,"CUTOFF",float(b.close),held)
            active.remove(p)
    open_rows=[]
    for p in active:
        x=data[p["symbol"]]
        b=x[x.timestamp < pd.to_datetime(end_ms,unit="ms",utc=True)].iloc[-1]
        sign=1.0 if p["side"]=="LONG" else -1.0
        open_rows.append({"symbol":p["symbol"],"core":p.get("core",CORE_NAME),"side":p["side"],
            "entry_time_utc":pd.to_datetime(p["entry_time_ms"],unit="ms",utc=True).isoformat(),
            "entry":p["entry"],"sl":p["sl"],"tp":p["tp"],"entry_score":p["score"],
            "unrealized_R_mark_to_market":sign*(float(b.close)-p["entry"])/p["risk"]})
    return {"closed_trades":closed,"open_positions":open_rows,"equity_end_closed_only":equity,
            "equity_curve":curve,"max_active_positions_seen":max_seen}


def stats(sim,period_days):
    tr=pd.DataFrame(sim["closed_trades"])
    if tr.empty:
        return {"closed_trades":0,"TP":0,"SL":0,"TIMEOUT":0,"CUTOFF":0,"gross_R":0.0,"net_est_R":0.0,
            "profit_factor_gross":None,"expectancy_net_R":0.0,"average_trades_per_30d":0.0,
            "open_positions":len(sim["open_positions"]),"max_active_positions_seen":sim["max_active_positions_seen"]}
    g=tr.R_gross.astype(float); n=tr.R_net_est.astype(float)
    pos=float(g[g>0].sum()); neg=float(-g[g<0].sum())
    eq=pd.Series(sim["equity_curve"],dtype=float); dd=((eq-eq.cummax())/eq.cummax().replace(0,np.nan)*100).min()
    return {"closed_trades":len(tr),"TP":int((tr.result=="TP").sum()),"SL":int((tr.result=="SL").sum()),
        "TIMEOUT":int((tr.result=="TIMEOUT").sum()),"CUTOFF":int((tr.result=="CUTOFF").sum()),
        "profitable_trades":int((g>0).sum()),"win_rate_closed_pct":round(100*float((g>0).sum())/len(tr),2),
        "tp_rate_closed_pct":round(100*float((tr.result=="TP").sum())/len(tr),2),
        "gross_R":round(float(g.sum()),4),"net_est_R":round(float(n.sum()),4),
        "profit_factor_gross":round(pos/neg,4) if neg>0 else None,
        "expectancy_net_R":round(float(n.mean()),4),"equity_end_closed_only_usd":round(float(sim["equity_end_closed_only"]),4),
        "max_drawdown_pct":round(float(dd),3) if pd.notna(dd) else None,
        "average_trades_per_30d":round(len(tr)/max(period_days,1)*30,2),
        "open_positions":len(sim["open_positions"]),
        "open_unrealized_R_mark_to_market":round(sum(p["unrealized_R_mark_to_market"] for p in sim["open_positions"]),4),
        "max_active_positions_seen":sim["max_active_positions_seen"]}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=180)
    ap.add_argument("--out",type=Path,default=Path("research/results/portfolio_event_replay"))
    args=ap.parse_args()
    end=pd.Timestamp.now(tz="UTC").floor("D")
    end_ms=int(end.timestamp()*1000)
    start=end-pd.Timedelta(days=args.days)
    start_ms=int(start.timestamp()*1000)
    holdout_days=min(60,max(30,args.days//3))
    split=end-pd.Timedelta(days=holdout_days)
    split_ms=int(split.timestamp()*1000)
    data,coverage,errors,candidates=prepare(start_ms,end_ms)
    dest=args.out; dest.mkdir(parents=True,exist_ok=True)
    if errors or len(data)!=len(SYMBOLS):
        fail={"source":"Binance USD-M Futures only","validation_passed":False,"data_errors":errors,
            "symbols_expected":len(SYMBOLS),"symbols_loaded":len(data),"coverage":coverage,
            "start_utc":start.isoformat(),"end_utc":end.isoformat()}
        (dest/"summary.json").write_text(json.dumps(fail,indent=2))
        raise SystemExit(f"PORTFOLIO_REPLAY_INVALID: {len(errors)} data errors; metrics withheld.")
    if not candidates:
        fail={"source":"Binance USD-M Futures only","validation_passed":False,
            "data_errors":[{"error":"No strategy signals passed the $10M 24h volume gate"}],"coverage":coverage}
        (dest/"summary.json").write_text(json.dumps(fail,indent=2))
        raise SystemExit("PORTFOLIO_REPLAY_INVALID: no eligible signals.")

    source_totals={}
    for row in coverage:
        for source,count in row["source_counts"].items(): source_totals[source]=source_totals.get(source,0)+int(count)
    period_days=(end-start).total_seconds()/86400.0
    bar_maps,global_times=build_indices(data)
    full_modes={
        "BASELINE_CORE":("baseline",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_SCORE_LT65":("precision65",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_GATE_SCORE_LE54":("precision_score_54",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_GATE_SCORE_LE60":("precision_score_60",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_DISTANCE_MINUS10":("precision65",0.9,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_DISTANCE_PLUS10":("precision65",1.1,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_SIGNAL_CLOSE_PROXY":("precision65",1.0,"signal_close",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_LOWER_COST":("precision65",1.0,"next_open",2.0,1.0),
        "PRECISION_V2_ZERO_COST":("precision65",1.0,"next_open",0.0,0.0),
        "LEGACY_CORE_BASELINE":("legacy_baseline",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "LEGACY_CORE_PRECISION65":("legacy_precision65",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "COMBINED_CORE_PRECISION65":("combined_precision65",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "COMBINED_WITHOUT_CURRENT_CORE":("without_current_core",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "COMBINED_WITHOUT_LEGACY_CORE":("without_legacy_core",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
    }
    full_results={}; full_rows=[]
    for name,spec in full_modes.items():
        mode,factor,entry_mode,fee,slip=spec
        sim=simulate(data,candidates,start_ms,end_ms,mode,distance_factor=factor,entry_mode=entry_mode,fee_bps=fee,slippage_bps=slip,bar_maps=bar_maps,global_times=global_times)
        full_results[name]=stats(sim,period_days)
        full_rows.extend({"variant":name,**t} for t in sim["closed_trades"])
        print(f"FULL_RESULT {name}: {json.dumps(full_results[name],sort_keys=True)}",flush=True)

    dev_days=(split-start).total_seconds()/86400.0
    sweep=[]
    for threshold in SCORE_THRESHOLDS:
        sim=simulate(data,candidates,start_ms,split_ms,f"precision_score_{threshold}",force_close=True,bar_maps=bar_maps,global_times=global_times)
        sweep.append({"score_ceiling":threshold,**stats(sim,dev_days)})
    eligible=[r for r in sweep if r["closed_trades"]>=10 and r["profit_factor_gross"] is not None]
    chosen=max(eligible,key=lambda r:(r["net_est_R"],r["profit_factor_gross"],-r["closed_trades"])) if eligible else None
    chosen_threshold=int(chosen["score_ceiling"]) if chosen else 65
    hold_days=(end-split).total_seconds()/86400.0
    hold_modes={
        "BASELINE_CORE":("baseline",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_SCORE_LT65":("precision65",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "WALK_FORWARD_SELECTED_SCORE":(f"precision_score_{chosen_threshold}",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_DISTANCE_MINUS10":("precision65",0.9,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_DISTANCE_PLUS10":("precision65",1.1,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_SIGNAL_CLOSE_PROXY":("precision65",1.0,"signal_close",FEE_BPS,SLIPPAGE_BPS),
        "PRECISION_V2_LOWER_COST":("precision65",1.0,"next_open",2.0,1.0),
        "LEGACY_CORE_PRECISION65":("legacy_precision65",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "COMBINED_CORE_PRECISION65":("combined_precision65",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "COMBINED_WITHOUT_CURRENT_CORE":("without_current_core",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
        "COMBINED_WITHOUT_LEGACY_CORE":("without_legacy_core",1.0,"next_open",FEE_BPS,SLIPPAGE_BPS),
    }
    hold_results={}; hold_rows=[]
    for name,spec in hold_modes.items():
        mode,factor,entry_mode,fee,slip=spec
        sim=simulate(data,candidates,split_ms,end_ms,mode,distance_factor=factor,entry_mode=entry_mode,fee_bps=fee,slippage_bps=slip,bar_maps=bar_maps,global_times=global_times)
        hold_results[name]=stats(sim,hold_days)
        hold_rows.extend({"variant":name,**t} for t in sim["closed_trades"])
        print(f"HOLDOUT_RESULT {name}: {json.dumps(hold_results[name],sort_keys=True)}",flush=True)

    # Parameter-neighbor sweep on the later period is diagnostic only; do not re-select a production threshold from it.
    holdout_sweep=[]
    for threshold in SCORE_THRESHOLDS:
        sim=simulate(data,candidates,split_ms,end_ms,f"precision_score_{threshold}",bar_maps=bar_maps,global_times=global_times)
        holdout_sweep.append({"score_ceiling":threshold,**stats(sim,hold_days)})
    loo_rows=[]
    for symbol in SYMBOLS:
        sim=simulate(data,candidates,split_ms,end_ms,"precision65",exclude_symbol=symbol,bar_maps=bar_maps,global_times=global_times)
        loo_rows.append({"excluded_symbol":symbol,**stats(sim,hold_days)})
    pd.DataFrame(full_rows).to_csv(dest/"full_period_portfolio_trades.csv",index=False)
    pd.DataFrame(hold_rows).to_csv(dest/"holdout_portfolio_trades.csv",index=False)
    pd.DataFrame(sweep).to_csv(dest/"walk_forward_development_sweep.csv",index=False)
    pd.DataFrame(holdout_sweep).to_csv(dest/"holdout_score_neighbor_sweep_exploratory.csv",index=False)
    pd.DataFrame(loo_rows).to_csv(dest/"holdout_leave_one_coin_out.csv",index=False)
    pd.DataFrame(coverage).to_csv(dest/"data_coverage.csv",index=False)
    summary={"source":"Binance USD-M Futures Vision archives / Binance REST only",
        "validation_passed":True,"data_errors":[],"start_utc":start.isoformat(),"end_utc":end.isoformat(),
        "development_end_utc":split.isoformat(),"holdout_start_utc":split.isoformat(),
        "days":args.days,"symbols_expected":len(SYMBOLS),"symbols_loaded":len(data),
        "total_15m_candles":int(sum(len(x) for x in data.values())),
        "signal_candidates_after_volume_gate":len(candidates),"source_totals":source_totals,"coverage":coverage,
        "portfolio_assumptions":{"execution":"Primary variant confirms a closed 15m signal and enters at next 15m open. Signal-close proxy is a comparison only; stop/target distances are derived from the signal bar.",
            "position_sizing":"1% of current equity per trade; starting equity $100; realized PnL compounded.",
            "limits":{"max_active_positions":MAX_ACTIVE,"max_active_per_side":MAX_ACTIVE_PER_SIDE,"same-symbol reentry_cooldown_minutes":60},
            "selection":"At each scan time, eligible signals ranked by entry score descending; symbol/side are deterministic tie-breakers.",
            "exit":f"SL first if SL and TP both touch in one 15m bar; timeout at {MAX_HOLD_BARS} bars; open positions at data end are marked to market and excluded from closed-trade metrics.",
            "costs":{"base_fee_bps_per_side":FEE_BPS,"base_slippage_bps_per_side":SLIPPAGE_BPS,"sensitivity_cases":"zero cost and 2 bps fee + 1 bps slippage per side","model":"estimated round-trip cost converted to R using stop distance"},
            "distance_sensitivity":"Stop and target distances scaled together by -10% and +10%; this is a distance robustness test, not a guarantee of ATR-optimal stops.",
            "volume_filter":"Approximate quote volume = rolling sum(close * volume) across 96 completed 15m bars; minimum $10M.",
            "BTC_filter":"Shadow-only; no directional veto. Defensive mode and failure shield are OFF. Precision V2 gate is modeled separately.",
            "core_comparison":"Current V15_PRECISION_V2_FINAL and legacy V15_ADX4H_CANDLE2H are replayed separately and in a combined portfolio. Combined-without-one-core tests measure incremental contribution under the same generic precision gate; this is a research comparison, not a claim that legacy core is production-enabled.",
            "limitation":"Historical OHLC simulation, not a guarantee of live fills; 15m bars cannot reveal exact intrabar order beyond conservative SL-first ambiguity."},
        "full_period_variants":full_results,"walk_forward_development_sweep":sweep,
        "walk_forward_selection":{"rule":"Choose highest estimated net R using development period only among score ceilings with >=10 closed trades; holdout outcomes are not used for selection.",
            "selected_score_ceiling":chosen_threshold if chosen else None,"fallback_used":chosen is None},
        "walk_forward_holdout":hold_results,
        "holdout_score_neighbor_sweep_exploratory":holdout_sweep,
        "holdout_leave_one_coin_out_precision65":loo_rows,
        "holdout_pristine":False,
        "holdout_caveat":"The Aug-Oct 2026 evaluation window overlaps the prior 28-trade cohort and earlier in-sample threshold exploration. The selected threshold itself was chosen only from the Apr-Aug development period, but this is not a fully untouched final holdout for the overall project. Collect a new post-2026-10-10 forward holdout before production approval.",
        "interpretation":"The walk-forward score selection is development-only. Holdout parameter-neighbor and leave-one-coin-out tables are robustness diagnostics, not a basis for re-tuning on the same holdout. Small holdout samples imply uncertainty."}
    (dest/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps({"validation_passed":True,"data_errors":[],"full_period_variants":full_results,
        "walk_forward_selection":summary["walk_forward_selection"],"walk_forward_holdout":hold_results},indent=2),flush=True)


if __name__=="__main__":
    main()
