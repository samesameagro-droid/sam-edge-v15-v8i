#!/usr/bin/env python3
"""Research-only structural strategy benchmark. Not connected to live/paper execution."""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
import premium_indicator_benchmark as base

START_VALID = pd.Timestamp("2026-04-01", tz="UTC")
END_VALID = pd.Timestamp("2026-09-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-09-05", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-10-01", tz="UTC")

def features(x):
    x=x.copy()
    x["ema20"]=base.ema(x.close,20); x["ema50"]=base.ema(x.close,50)
    x["vol_ratio"]=x.volume/x.volume.rolling(20,min_periods=20).mean()
    x["swing_hi"]=x.high.rolling(20,min_periods=20).max().shift(1)
    x["swing_lo"]=x.low.rolling(20,min_periods=20).min().shift(1)
    # Confirmed close back inside the prior 20-bar range after sweeping its boundary.
    x["sweep_long"]=(x.low<x.swing_lo)&(x.close>x.swing_lo)&(x.close>x.open)&x.regL
    x["sweep_short"]=(x.high>x.swing_hi)&(x.close<x.swing_hi)&(x.close<x.open)&x.regS
    # Trend pullback reclaim of EMA20, with EMA20/50 stack, RSI and 4h regime.
    x["pull_long"]=(x.close>x.ema20)&(x.close.shift(1)<=x.ema20.shift(1))&(x.ema20>x.ema50)&(x.rsi_smooth>50)&x.regL
    x["pull_short"]=(x.close<x.ema20)&(x.close.shift(1)>=x.ema20.shift(1))&(x.ema20<x.ema50)&(x.rsi_smooth<50)&x.regS
    # Breakout/retest proxy: prior bar closed beyond range, current bar retests level and closes back outside.
    x["break_long"]=(x.close.shift(1)>x.swing_hi.shift(1))&(x.low<=x.swing_hi)&(x.close>x.swing_hi)&(x.close>x.open)&x.regL
    x["break_short"]=(x.close.shift(1)<x.swing_lo.shift(1))&(x.high>=x.swing_lo)&(x.close<x.swing_lo)&(x.close<x.open)&x.regS
    return x

def get_signals(x,f):
    if f=="LIQUIDITY_SWEEP": return x.sweep_long.fillna(False),x.sweep_short.fillna(False)
    if f=="TREND_PULLBACK": return x.pull_long.fillna(False),x.pull_short.fillna(False)
    return x.break_long.fillna(False),x.break_short.fillna(False)

def generate_candidates(coin,x,f,fee,slip,stop_atr,rr):
    ls,ss=get_signals(x,f); out=[]; i=250; cooldown=4; max_hold=96
    while i<len(x)-1:
        side="LONG" if bool(ls.iloc[i]) else ("SHORT" if bool(ss.iloc[i]) else None)
        if side is None: i+=1; continue
        atr=float(x.atr.iloc[i])
        if not np.isfinite(atr) or atr<=0: i+=1; continue
        en_i=i+1; en=float(x.open.iloc[en_i]); risk=stop_atr*atr
        sl,tp=(en-risk,en+rr*risk) if side=="LONG" else (en+risk,en-rr*risk)
        end_i=min(en_i+max_hold,len(x)-1); ex_i=end_i; ex=float(x.close.iloc[end_i]); result="TIME"; gross=None
        for j in range(en_i,end_i+1):
            op,hi,lo,cl=[float(x[k].iloc[j]) for k in ("open","high","low","close")]
            if side=="LONG":
                if op<=sl: ex=op; gross=(ex-en)/risk; result="SL_GAP"; ex_i=j; break
                if op>=tp: ex=tp; gross=rr; result="TP"; ex_i=j; break
                if lo<=sl: ex=sl; gross=-1.; result="SL"; ex_i=j; break
                if hi>=tp: ex=tp; gross=rr; result="TP"; ex_i=j; break
            else:
                if op>=sl: ex=op; gross=(en-ex)/risk; result="SL_GAP"; ex_i=j; break
                if op<=tp: ex=tp; gross=rr; result="TP"; ex_i=j; break
                if hi>=sl: ex=sl; gross=-1.; result="SL"; ex_i=j; break
                if lo<=tp: ex=tp; gross=rr; result="TP"; ex_i=j; break
        if gross is None: gross=(ex-en)/risk if side=="LONG" else (en-ex)/risk
        cost=2*(fee+slip)/10000*en/risk
        out.append({"coin":coin,"family":f,"side":side,"signal_time":x.timestamp.iloc[i].isoformat(),
          "entry_time":x.timestamp.iloc[en_i].isoformat(),"exit_time":x.timestamp.iloc[ex_i].isoformat(),
          "entry":en,"sl":sl,"tp":tp,"exit":ex,"gross_R":gross,"cost_R":cost,"net_R":gross-cost,"result":result})
        i=ex_i+cooldown+1
    return out

def replay_portfolio(candidates,start,end,max_positions=5):
    # Event-driven concurrency cap; candidates are pre-generated per coin. One position per coin.
    c=[t.copy() for t in candidates if start<=pd.Timestamp(t["entry_time"])<end]
    c.sort(key=lambda t:(pd.Timestamp(t["entry_time"]),t["coin"]))
    active=[]; accepted=[]
    for t in c:
        entry=pd.Timestamp(t["entry_time"])
        active=[a for a in active if pd.Timestamp(a["exit_time"])>entry]
        if len(active)>=max_positions or any(a["coin"]==t["coin"] for a in active): continue
        accepted.append(t); active.append(t)
    accepted.sort(key=lambda t:(pd.Timestamp(t["exit_time"]),pd.Timestamp(t["entry_time"]),t["coin"]))
    r=np.array([float(t["net_R"]) for t in accepted],dtype=float)
    wins=r[r>0]; losses=r[r<0]; eq=np.cumsum(r); peak=np.maximum.accumulate(np.r_[0.,eq])[1:] if len(eq) else np.array([])
    return {"trades":len(r),"win_rate_pct":round(float((r>0).mean()*100),2) if len(r) else None,
      "net_R":round(float(r.sum()),3),"expectancy_R":round(float(r.mean()),5) if len(r) else None,
      "profit_factor":round(float(wins.sum()/-losses.sum()),4) if len(losses) else (None if not len(wins) else None),
      "max_closed_equity_DD_R":round(float((eq-peak).min()),3) if len(eq) else 0,
      "max_concurrent_positions":max_positions,"note":"event-driven concurrency cap; no funding/correlation model"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",default="data"); ap.add_argument("--out",default="research/results/structural_benchmark")
    ap.add_argument("--fee-bps",type=float,default=5); ap.add_argument("--slippage-bps",type=float,default=2)
    args=ap.parse_args(); root=Path(args.data); files=list(root.glob("*/*.csv"))+list(root.glob("*.csv"))
    data={}
    for p in files:
        try:
            raw=pd.read_csv(p)
            if "timestamp" not in raw and "timestamp_ms" in raw: raw["timestamp"]=pd.to_datetime(raw.timestamp_ms,unit="ms",utc=True)
            if "timestamp" not in raw: continue
            x=features(base.enrich(raw))
            coin=p.stem.split("-")[0].replace("USDT","").replace("_","")
            if len(x)>1000: data[coin]=x
        except Exception as e: print(f"SKIP {p}: {e}",flush=True)
    if not data: raise SystemExit("No usable OHLCV data.")
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True); report=[]
    for family in ["LIQUIDITY_SWEEP","TREND_PULLBACK","BREAKOUT_RETEST"]:
      for stop_atr in [1.0,1.5,2.0]:
       for rr in [1.5,2.0,2.5]:
        trades=[]
        for coin,x in data.items(): trades.extend(generate_candidates(coin,x,family,args.fee_bps,args.slippage_bps,stop_atr,rr))
        row={"family":family,"stop_atr":stop_atr,"rr":rr,
          "train":replay_portfolio(trades,pd.Timestamp("2025-01-01",tz="UTC"),START_VALID),
          "validation":replay_portfolio(trades,START_VALID,END_VALID),
          "holdout":replay_portfolio(trades,HOLDOUT_START,HOLDOUT_END),
          "stress_validation":replay_portfolio([dict(t,net_R=t["gross_R"]-2*(8+5)/10000*t["entry"]/(abs(t["entry"]-t["sl"]))) for t in trades],START_VALID,END_VALID),
          "stress_holdout":replay_portfolio([dict(t,net_R=t["gross_R"]-2*(8+5)/10000*t["entry"]/(abs(t["entry"]-t["sl"]))) for t in trades],HOLDOUT_START,HOLDOUT_END)}
        report.append(row)
        print(f"GRID {family} stop={stop_atr} rr={rr} valid={row['validation']['net_R']}R PF={row['validation']['profit_factor']}",flush=True)
    (out/"structural_grid.json").write_text(json.dumps(report,indent=2))
    pd.DataFrame([{"family":r["family"],"stop_atr":r["stop_atr"],"rr":r["rr"],
      **{f"valid_{k}":v for k,v in r["validation"].items() if k in ["trades","win_rate_pct","net_R","expectancy_R","profit_factor","max_closed_equity_DD_R"]},
      **{f"stress_valid_{k}":v for k,v in r["stress_validation"].items() if k in ["trades","net_R","expectancy_R","profit_factor","max_closed_equity_DD_R"]},
      **{f"holdout_{k}":v for k,v in r["holdout"].items() if k in ["trades","net_R","expectancy_R","profit_factor"]},
      **{f"stress_holdout_{k}":v for k,v in r["stress_holdout"].items() if k in ["trades","net_R","expectancy_R","profit_factor"]}} for r in report]).to_csv(out/"structural_grid.csv",index=False)
    print(json.dumps({"coins":len(data),"bars":sum(len(x) for x in data.values()),"configurations":len(report),"results":report},indent=2),flush=True)
if __name__=="__main__": main()
