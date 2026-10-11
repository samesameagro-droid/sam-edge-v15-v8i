#!/usr/bin/env python3
"""Research-only score<=54 counterfactual over actually executed, closed journal trades #1-128.
For #1-100, reconstruct the current entry score from Binance 15m OHLCV at each historical
signal timestamp; for #101-128, retain the recorded entry_score as the authoritative score
and independently recompute it as a source/feature consistency diagnostic. No orders placed.
"""
from __future__ import annotations
import argparse, csv, json, math, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# When launched as "python research/script.py", make repository-root modules importable.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core_engine_v15 import (
    enrich, ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
    EARLY_ROOM_MIN_ATR,
)
from research.mtf_30m_replay import fetch_klines

TF_MS = 15 * 60 * 1000
FEE_BPS_PER_SIDE = 5.0
SLIPPAGE_BPS_PER_SIDE = 2.0
RISK_PCT = 0.01
START_EQUITY = 100.0

def parse_ts(value):
    return pd.Timestamp(str(value), tz="UTC") if pd.Timestamp(str(value)).tzinfo is None else pd.Timestamp(str(value)).tz_convert("UTC")

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def load_state_scores(path):
    try:
        state=json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out={}
    for t in state.get("closed", []):
        try:
            score=float(t.get("entry_score"))
            if not math.isfinite(score): continue
            key=(str(t.get("coin","")),str(t.get("side","")).upper(),str(t.get("opened_at","")),round(float(t.get("entry")),12))
            out[key]=score
        except (TypeError,ValueError):
            continue
    return out

def state_score_for(row, scores):
    try:
        key=(row["coin"],row["side"].upper(),row["signal_time"],round(float(row["entry"]),12))
        return scores.get(key)
    except (KeyError,TypeError,ValueError):
        return None

def score_at_row(enriched, signal_time, side):
    ts=parse_ts(signal_time)
    matches=enriched.index[enriched["timestamp"] == ts]
    if len(matches)==0:
        return None, "NO_EXACT_SIGNAL_CANDLE"
    i=int(matches[-1])
    r=enriched.iloc[i]
    try:
        adx_pct=float(r["h4_adx_pct"]); adx_delta=float(r["h4_adx_delta"])
        room=float(r["dist_res_atr"] if side=="LONG" else r["dist_sup_atr"])
        dist=float(r["dist_ema20_atr"]); vol=float(r["volr"]); body=float(r["body_atr"])
        vals=[adx_pct,adx_delta,room,dist,vol,body]
        if not all(math.isfinite(x) for x in vals):
            return None, "NONFINITE_SCORE_FEATURE"
        adx_floor=ADX_LONG_PCT if side=="LONG" else ADX_SHORT_PCT
        delta_floor=ADX_LONG_DELTA if side=="LONG" else ADX_SHORT_DELTA
        adx_margin=float(np.clip((adx_pct-adx_floor)/(1-adx_floor),0,1))
        delta_margin=float(np.clip((adx_delta-delta_floor)/2.0,0,1))
        room_margin=float(np.clip((room-EARLY_ROOM_MIN_ATR)/1.50,0,1))
        loc_center=float(np.clip(1.0-abs(dist-0.60)/0.60,0,1))
        vol_margin=float(np.clip(vol/2.0,0,1))
        trigger_margin=float(np.clip(body/0.80,0,1))
        score=100.0*(0.30*adx_margin+0.20*delta_margin+0.20*room_margin+0.15*loc_center+0.10*vol_margin+0.05*trigger_margin)
        return round(float(score),8), "RECONSTRUCTED_BINANCE_15M"
    except (KeyError,TypeError,ValueError,ZeroDivisionError):
        return None, "SCORE_FEATURE_ERROR"

def gross_stats(rows):
    rs=[float(r["R"]) for r in rows]
    wins=sum(x>0 for x in rs); losses=sum(x<0 for x in rs)
    gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0)
    return {
        "trades":len(rs),"TP":wins,"SL":losses,
        "win_rate_pct":round(100*wins/len(rs),2) if rs else None,
        "gross_R":round(sum(rs),4),
        "gross_profit_factor":round(gp/gl,4) if gl>0 else None,
        "gross_expectancy_R":round(sum(rs)/len(rs),4) if rs else None,
    }

def portfolio_stats(rows):
    # Preserve original timestamps, omit rejected entries, risk 1% of realized equity
    # at entry, and book PnL at the recorded close time. Existing trades only; no waitlist.
    events=[]
    for r in rows:
        try:
            en=parse_ts(r["signal_time"]); cl=parse_ts(r["closed_at"])
            rr=float(r["R"]); entry=float(r["entry"]); sl=float(r["sl"])
            stop_pct=abs(entry-sl)/abs(entry) if entry else 0
            cost=(2*(FEE_BPS_PER_SIDE+SLIPPAGE_BPS_PER_SIDE)/10000)/stop_pct if stop_pct>0 else 0.0
            net_r=rr-cost
            events.append((en.value,1,"ENTRY",r,net_r))
            events.append((cl.value,0,"CLOSE",r,net_r))
            r["estimated_cost_R"]=round(cost,6)
            r["R_net_est"]=round(net_r,6)
        except (KeyError,TypeError,ValueError,ZeroDivisionError):
            continue
    # At identical timestamps, close old positions before sizing new entries.
    events.sort(key=lambda e:(e[0],e[1],e[3]["trade_no"]))
    equity=START_EQUITY; risk_cash={}
    curve=[equity]; pnl_sum=0.0
    for _,_,kind,r,net_r in events:
        key=r["trade_key"]
        if kind=="ENTRY":
            risk_cash[key]=equity*RISK_PCT
        else:
            pnl=risk_cash.get(key,equity*RISK_PCT)*net_r
            equity+=pnl; pnl_sum+=pnl; curve.append(equity)
    peak=START_EQUITY; max_dd=0.0
    for e in curve:
        peak=max(peak,e)
        if peak>0: max_dd=max(max_dd,(peak-e)/peak*100)
    nets=[float(r["R_net_est"]) for r in rows if r.get("R_net_est") not in ("",None)]
    gp=sum(x for x in nets if x>0); gl=-sum(x for x in nets if x<0)
    return {
        "start_equity_usd":START_EQUITY,"end_equity_usd":round(equity,4),
        "net_pnl_usd":round(equity-START_EQUITY,4),"net_est_R_sum":round(sum(nets),4),
        "net_profit_factor_est":round(gp/gl,4) if gl>0 else None,
        "max_drawdown_pct":round(max_dd,3),
        "fee_bps_per_side":FEE_BPS_PER_SIDE,"slippage_bps_per_side":SLIPPAGE_BPS_PER_SIDE,
        "risk_pct_of_realized_equity_at_entry":RISK_PCT,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--journal",required=True)
    ap.add_argument("--state",required=True)
    ap.add_argument("--out",default="research/results/score_filter_trade_journal")
    ap.add_argument("--first-trade",type=int,default=1)
    ap.add_argument("--last-trade",type=int,default=128)
    ap.add_argument("--score-max",type=float,default=54.0)
    args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    raw=read_csv(args.journal)
    valid=[]; seen=set(); rejected_invalid=[]
    for r in raw:
        try: n=int(r.get("trade_no","0"))
        except ValueError: continue
        if not (args.first_trade<=n<=args.last_trade): continue
        result=str(r.get("result","")).upper().replace(" HIT","").strip()
        if result not in {"TP","SL"} or not r.get("signal_time") or not r.get("closed_at"):
            rejected_invalid.append({"trade_no":n,"reason":"not closed TP/SL or missing timestamps"}); continue
        try:
            if parse_ts(r["closed_at"])<parse_ts(r["signal_time"]):
                rejected_invalid.append({"trade_no":n,"reason":"close precedes signal"}); continue
            float(r["R"]); float(r["entry"]); float(r["sl"])
        except Exception:
            rejected_invalid.append({"trade_no":n,"reason":"invalid timestamp/price/R"}); continue
        key=str(r.get("trade_key") or f'{r.get("coin")}|{r.get("side")}|{r.get("signal_time")}')
        if key in seen:
            rejected_invalid.append({"trade_no":n,"reason":"duplicate trade key"}); continue
        seen.add(key)
        r["trade_no"]=str(n); r["result_norm"]=result
        valid.append(r)
    valid.sort(key=lambda r:int(r["trade_no"]))
    state_scores=load_state_scores(args.state)
    symbols=sorted({str(r["coin"]).split("/")[0].replace(":USDT","") for r in valid if r.get("coin")})
    times_by_base=defaultdict(list)
    for r in valid:
        times_by_base[str(r["coin"]).split("/")[0]].append(parse_ts(r["signal_time"]))
    if not valid: raise SystemExit("No valid closed TP/SL trades in requested range.")
    candle_data={}; fetch_errors=[]
    def get_one(base):
        symbol=f"{base}USDT"
        try:
            # Fetch only the history window needed for this symbol's own trades,
            # including 50 days of warm-up for rolling 4H ADX percentile features.
            start_ms=int((min(times_by_base[base])-pd.Timedelta(days=50)).timestamp()*1000)
            end_ms=int((max(times_by_base[base])+pd.Timedelta(minutes=15)).timestamp()*1000)
            df=fetch_klines(symbol,"15m",start_ms,end_ms)
            if df is None or df.empty:
                return base,None,"NO_BINANCE_CANDLES"
            x=pd.DataFrame({
                "timestamp":pd.to_datetime(df["open_ms"],unit="ms",utc=True),
                "open":pd.to_numeric(df["open"],errors="coerce"),
                "high":pd.to_numeric(df["high"],errors="coerce"),
                "low":pd.to_numeric(df["low"],errors="coerce"),
                "close":pd.to_numeric(df["close"],errors="coerce"),
                "volume":pd.to_numeric(df["volume"],errors="coerce"),
            }).dropna().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
            if len(x)<1500:
                return base,x,f"SHORT_HISTORY_{len(x)}"
            return base,x,f"OK_{len(x)}"
        except Exception as e:
            return base,None,f"FETCH_ERROR_{type(e).__name__}:{e}"
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures=[pool.submit(get_one,b) for b in symbols]
        for fut in as_completed(futures):
            base,df,status=fut.result()
            if df is not None and not df.empty: candle_data[base]=df
            if not status.startswith("OK_"): fetch_errors.append({"base":base,"status":status})
            print(f"CANDLE {base}: {status}",flush=True)
    enriched={}
    for base,df in candle_data.items():
        try:
            enriched[base]=enrich(df)
        except Exception as e:
            fetch_errors.append({"base":base,"status":f"ENRICH_ERROR_{type(e).__name__}:{e}"})
    diagnostics=[]; recorded_score_validation=[]
    for r in valid:
        base=str(r["coin"]).split("/")[0]
        score_recorded=state_score_for(r,state_scores)
        score_calc=None; calc_status="NO_CANDLE_DATA"
        if base in enriched:
            score_calc,calc_status=score_at_row(enriched[base],r["signal_time"],str(r["side"]).upper())
        r["score_recorded"]=score_recorded if score_recorded is not None else ""
        r["score_reconstructed"]=score_calc if score_calc is not None else ""
        # Use one consistent Binance-reconstructed score for every trade where candles exist.
        # Recorded scores are retained for validation and only used as fallback if reconstruction fails.
        if score_recorded is not None and score_calc is not None:
            recorded_score_validation.append(abs(float(score_recorded)-float(score_calc)))
        if score_calc is not None:
            r["score_used"]=score_calc; r["score_source"]="RECONSTRUCTED_CURRENT_FORMULA_BINANCE"
        elif score_recorded is not None:
            r["score_used"]=score_recorded; r["score_source"]="RECORDED_SCORE_FALLBACK_NO_BINANCE"
        else:
            r["score_used"]=""; r["score_source"]="MISSING_SCORE"
        r["score_reconstruction_status"]=calc_status
        r["filter_decision"]="PASS" if r["score_used"]!="" and float(r["score_used"])<=args.score_max else ("REJECT" if r["score_used"]!="" else "UNSCORABLE")
        diagnostics.append({
            "trade_no":r["trade_no"],"coin":r["coin"],"side":r["side"],"signal_time":r["signal_time"],
            "recorded_score":r["score_recorded"],"reconstructed_score":r["score_reconstructed"],
            "score_used":r["score_used"],"score_source":r["score_source"],"reconstruction_status":calc_status,
            "result":r["result"],"R":r["R"],"filter_decision":r["filter_decision"],
        })
    known=[r for r in valid if r["score_used"]!=""]
    passed=[r for r in known if float(r["score_used"])<=args.score_max]
    rejected=[r for r in known if float(r["score_used"])>args.score_max]
    unscorable=[r for r in valid if r["score_used"]==""]
    # Apply estimated costs to each sample independently; then calculate equity event replay.
    for group in (valid,known,passed,rejected,unscorable):
        portfolio_stats(group)
    all_stats={**gross_stats(valid),**portfolio_stats(valid)}
    known_stats={**gross_stats(known),**portfolio_stats(known)}
    passed_stats={**gross_stats(passed),**portfolio_stats(passed)}
    rejected_stats={**gross_stats(rejected),**portfolio_stats(rejected)}
    summary={
        "source":"GitHub main executed master journal + paper state; Binance USD-M Futures candles",
        "trade_range":[args.first_trade,args.last_trade],"score_filter":f"score <= {args.score_max:g} (inclusive)",
        "journal_rows_in_range":sum(args.first_trade<=int(r.get("trade_no","0") or 0)<=args.last_trade for r in raw),
        "valid_unique_closed_tp_sl_trades":len(valid),"invalid_or_duplicate_rows":rejected_invalid,
        "scores_available":len(known),"scores_missing":len(unscorable),
        "recorded_scores_used":sum(r["score_source"]=="RECORDED_ENTRY_SCORE" for r in valid),
        "reconstructed_scores_used":sum(r["score_source"]=="RECONSTRUCTED_CURRENT_FORMULA_BINANCE" for r in valid),
        "recorded_score_validation_on_available_candles":{
            "count":len(recorded_score_validation),
            "mean_absolute_error":round(float(np.mean(recorded_score_validation)),4) if recorded_score_validation else None,
            "median_absolute_error":round(float(np.median(recorded_score_validation)),4) if recorded_score_validation else None,
            "max_absolute_error":round(float(np.max(recorded_score_validation)),4) if recorded_score_validation else None,
        },
        "baseline_all_executed_trades":all_stats,
        "known_score_subset":known_stats,
        "score_filter_pass":passed_stats,
        "score_filter_reject":rejected_stats,
        "unscorable_trades":[{"trade_no":r["trade_no"],"coin":r["coin"],"signal_time":r["signal_time"],"reason":r["score_reconstruction_status"]} for r in unscorable],
        "candle_fetch_or_enrichment_errors":fetch_errors,
        "methodology":{
            "population":"Only unique journal trades #1-128 with a terminal TP or SL and valid timestamps. No WAITLIST or ongoing trades.",
            "historical_scores":"Every trade with available candles is re-scored with the same current entry-score formula at the exact historical 15m signal candle using Binance OHLCV, regardless of the core that originally executed it. The legacy core name is ignored for score calculation.",
            "newer_scores":"Recorded entry_score for #101-128 is retained as a validation reference; the score filter uses the consistently reconstructed Binance score when available.",
            "filter":"Accept if score <= maximum, inclusive. Unscorable rows are not counted as passes.",
            "portfolio":"Only retained historical executions are simulated; no waitlist replacement trades are invented. Risk is 1% of realized equity at entry. Closes at identical timestamps are processed before entries.",
            "costs":"Estimated 5 bps fee + 2 bps slippage per side, converted to R using entry-to-SL distance. Gross journal R is also reported.",
            "limitations":"This is a retrospective filter overlay on the executed-trade sample, not a full signal-level replay that regenerates every candidate and reselects trades after freeing portfolio slots."
        },
        "data_complete_for_all_128":len(valid)==(args.last_trade-args.first_trade+1) and len(unscorable)==0,
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    pd.DataFrame(valid).to_csv(out/"trades_score54_filtered.csv",index=False)
    pd.DataFrame(diagnostics).to_csv(out/"score_reconstruction_diagnostics.csv",index=False)
    print(json.dumps({
        "trades":len(valid),"scored":len(known),"unscorable":len(unscorable),
        "all_executed":all_stats,"score_filter_pass":passed_stats,"score_filter_reject":rejected_stats,
        "data_complete_for_all_128":summary["data_complete_for_all_128"],
        "summary_path":str(out/"summary.json")
    },indent=2),flush=True)

if __name__=="__main__":
    main()
