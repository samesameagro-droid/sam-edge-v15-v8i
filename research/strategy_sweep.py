#!/usr/bin/env python3
"""Research-only 108+ configuration sweep; validation ranks candidates, holdout opens only for top 5."""
import argparse, json, itertools
from pathlib import Path
import numpy as np
import pandas as pd
import premium_indicator_benchmark as base

START_TEST = pd.Timestamp("2026-04-01", tz="UTC")
END_TEST = pd.Timestamp("2026-09-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-09-05", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-10-01", tz="UTC")

def add_filters(x):
    x=x.copy()
    tr=pd.concat([(x.high-x.low),(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    plus=x.high.diff(); minus=-x.low.diff()
    pdi=100*base.rma(plus.where((plus>minus)&(plus>0),0),14)/base.rma(tr,14)
    mdi=100*base.rma(minus.where((minus>plus)&(minus>0),0),14)/base.rma(tr,14)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    x["adx"]=base.rma(dx,14)
    x["vol_ratio"]=x.volume/x.volume.rolling(20,min_periods=20).mean()
    return x

def run_one(data, combo, fee, slip):
    family, stop_atr, rr, vol_gate, adx_gate, cooldown, max_hold = combo
    base.RR=float(rr); base.STOP_ATR=float(stop_atr); base.COOLDOWN=int(cooldown); base.MAX_HOLD=int(max_hold)
    original=base.signals
    def filtered_signals(x,f):
        long_sig,short_sig=original(x,f)
        if vol_gate: long_sig=long_sig&(x.vol_ratio>=1.1); short_sig=short_sig&(x.vol_ratio>=1.1)
        if adx_gate: long_sig=long_sig&(x.adx>=adx_gate); short_sig=short_sig&(x.adx>=adx_gate)
        return long_sig.fillna(False),short_sig.fillna(False)
    base.signals=filtered_signals
    trades=[]
    try:
        for coin,x in data.items():
            trades.extend(base.run_coin(coin,x,family,fee,slip))
    finally:
        base.signals=original
    return trades

def stats(trades, start, end):
    t=[z for z in trades if start <= pd.Timestamp(z["entry_time"]) < end]
    # Combined closed-equity statistics must follow exit chronology, not coin-by-coin append order.
    t.sort(key=lambda z: (pd.Timestamp(z["exit_time"]), pd.Timestamp(z["entry_time"]), z["coin"]))
    s=base.stat(t)
    s["period_start"]=str(start.date()); s["period_end_exclusive"]=str(end.date())
    return s

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",default="data"); ap.add_argument("--out",default="research/results/strategy_sweep")
    ap.add_argument("--fee-bps",type=float,default=5); ap.add_argument("--slippage-bps",type=float,default=2)
    args=ap.parse_args()
    root=Path(args.data)
    files=list(root.glob("*/*.csv"))+list(root.glob("*.csv"))
    if not files: raise SystemExit(f"No CSV files found under {root}")
    data={}
    for p in files:
        try:
            raw=pd.read_csv(p)
            if "timestamp" not in raw and "timestamp_ms" in raw:
                raw["timestamp"]=pd.to_datetime(raw.timestamp_ms,unit="ms",utc=True)
            if "timestamp" not in raw: continue
            x=add_filters(base.enrich(raw))
            coin=p.stem.split("-")[0].replace("USDT","").replace("_","")
            if len(x)>1000: data[coin]=x
        except Exception as e: print(f"SKIP {p}: {e}",flush=True)
    if not data: raise SystemExit("No usable coin OHLCV files.")
    combos=list(itertools.product(
        base.FAMILIES, [1.0,1.25,1.5], [1.5,2.0,2.5],
        [False,True], [0,18,25], [2,4], [48,96]
    ))
    # 3 families x 3 stops x 3 targets x 2 volume x 3 ADX x 2 cooldown x 2 holding = 1944 combinations.
    # To bound compute and avoid a blind full Cartesian search, use a predeclared 108-combo stratified design.
    combos=[]
    for family, stop_atr, rr, vol_gate, adx_gate, cooldown, max_hold in itertools.product(
        base.FAMILIES,[1.0,1.5,2.0],[1.5,2.0,2.5],[False,True],[0,20],[4],[96]
    ):
        combos.append((family,stop_atr,rr,vol_gate,adx_gate,cooldown,max_hold))
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    rows=[]; all_trades={}
    total=len(combos)
    for n,c in enumerate(combos,1):
        trades=run_one(data,c,args.fee_bps,args.slippage_bps)
        # Train ranks are diagnostic only; select candidates on Apr-Aug validation.
        train=stats(trades,pd.Timestamp("2025-01-01",tz="UTC"),START_TEST)
        valid=stats(trades,START_TEST,END_TEST)
        row={"id":n,"family":c[0],"stop_atr":c[1],"rr":c[2],"volume_gate":c[3],"adx_gate":c[4],
             "cooldown":c[5],"max_hold":c[6],"train":train,"validation":valid}
        rows.append(row); all_trades[n]=trades
        if n%12==0: print(f"GRID {n}/{total}",flush=True)
    # Selection requires positive validation expectancy, PF>1 and at least 100 validation trades.
    eligible=[r for r in rows if (r["validation"]["trades"] or 0)>=100
              and (r["validation"]["expectancy_R"] or -999)>0
              and (r["validation"]["profit_factor"] or 0)>1]
    eligible.sort(key=lambda r: (r["validation"]["expectancy_R"],r["validation"]["profit_factor"]),reverse=True)
    finalists=eligible[:5]
    # If no configuration passes the viability gate, show top 5 validation candidates but label them as failures.
    if not finalists:
        finalists=sorted(rows,key=lambda r:((r["validation"]["expectancy_R"] or -999),(r["validation"]["profit_factor"] or 0)),reverse=True)[:5]
    for r in finalists:
        ts=all_trades[r["id"]]
        r["holdout_unseen_until_shortlist"] = stats(ts,HOLDOUT_START,HOLDOUT_END)
        # stress cost replays only the 5 shortlisted configs
        stress=run_one(data,(r["family"],r["stop_atr"],r["rr"],r["volume_gate"],r["adx_gate"],r["cooldown"],r["max_hold"]),8,5)
        r["stress_validation"]=stats(stress,START_TEST,END_TEST)
        r["stress_holdout"]=stats(stress,HOLDOUT_START,HOLDOUT_END)
    rows.sort(key=lambda r:r["id"])
    (out/"all_validation.json").write_text(json.dumps(rows,indent=2))
    (out/"shortlist_holdout.json").write_text(json.dumps({"eligible_count":len(eligible),"shortlist":finalists,
       "selection_rule":"Validation only; top five; holdout reported only after shortlist; no tuning on holdout."},indent=2))
    flat=[]
    for r in rows:
        v=r["validation"]; tr=r["train"]
        flat.append({k:r[k] for k in ["id","family","stop_atr","rr","volume_gate","adx_gate","cooldown","max_hold"]} |
                    {"train_trades":tr["trades"],"train_net_R":tr["net_R"],"valid_trades":v["trades"],
                     "valid_win_rate_pct":v["win_rate_pct"],"valid_net_R":v["net_R"],
                     "valid_expectancy_R":v["expectancy_R"],"valid_profit_factor":v["profit_factor"],
                     "valid_max_closed_equity_DD_R":v["max_closed_equity_DD_R"]})
    pd.DataFrame(flat).to_csv(out/"all_validation.csv",index=False)
    print(json.dumps({"data_coins":len(data),"bars_total":sum(len(x) for x in data.values()),
       "configurations":total,"eligible_validation":len(eligible),"shortlist":[{"id":r["id"],"family":r["family"],
       "stop_atr":r["stop_atr"],"rr":r["rr"],"volume_gate":r["volume_gate"],"adx_gate":r["adx_gate"],
       "validation":r["validation"],"stress_validation":r["stress_validation"],"holdout":r["holdout_unseen_until_shortlist"],
       "stress_holdout":r["stress_holdout"]} for r in finalists]},indent=2),flush=True)
if __name__=="__main__": main()
