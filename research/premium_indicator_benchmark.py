#!/usr/bin/env python3
"""Research-only, cost-aware benchmark of public indicator concepts; never imported by live SAM EDGE."""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

RR, STOP_ATR, MAX_HOLD, COOLDOWN = 2.0, 1.5, 96, 4
FAMILIES = ["UT_BOT_EMA4H", "SSL_QQE_EMA4H", "SUPERTREND_SSL_QQE"]

def ema(s,n): return s.ewm(span=n,adjust=False,min_periods=n).mean()
def rma(s,n): return s.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def enrich(raw):
    x=raw.copy()
    x["timestamp"]=pd.to_datetime(x["timestamp"],utc=True)
    for c in ["open","high","low","close","volume"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    x=x.dropna(subset=["timestamp","open","high","low","close","volume"]).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    prev=x.close.shift()
    tr=pd.concat([x.high-x.low,(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1)
    x["atr"]=rma(tr,14)
    d=x.close.diff(); gain=rma(d.clip(lower=0),14); loss=rma((-d).clip(lower=0),14)
    x["rsi_smooth"]=ema(100-100/(1+gain/loss.replace(0,np.nan)),5)
    h=x.set_index("timestamp").resample("4h",label="right",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last")).dropna(subset=["close"])
    h["e50"],h["e200"]=ema(h.close,50),ema(h.close,200)
    h["regL"]=(h.close>h.e200)&(h.e50>h.e200); h["regS"]=(h.close<h.e200)&(h.e50<h.e200)
    x=pd.merge_asof(x.sort_values("timestamp"),h[["regL","regS"]].reset_index().sort_values("timestamp"),on="timestamp",direction="backward")
    x["regL"]=x.regL.fillna(False).astype(bool); x["regS"]=x.regS.fillna(False).astype(bool)
    # UT Bot ATR trailing stop (key=1, ATR=14)
    c=x.close.to_numpy(float); a=x.atr.to_numpy(float); ts=np.full(len(x),np.nan)
    for i in range(len(x)):
        if not np.isfinite(a[i]): continue
        if i==0 or not np.isfinite(ts[i-1]): ts[i]=c[i]-a[i]; continue
        p=ts[i-1]
        if c[i]>p and c[i-1]>p: ts[i]=max(p,c[i]-a[i])
        elif c[i]<p and c[i-1]<p: ts[i]=min(p,c[i]+a[i])
        elif c[i]>p: ts[i]=c[i]-a[i]
        else: ts[i]=c[i]+a[i]
    x["utL"]=(x.close>ts)&(x.close.shift()<=pd.Series(ts,index=x.index).shift())
    x["utS"]=(x.close<ts)&(x.close.shift()>=pd.Series(ts,index=x.index).shift())
    # SSL Hybrid-style high/low EMA channel
    x["sslH"],x["sslL"]=ema(x.high,10),ema(x.low,10)
    state=np.zeros(len(x),dtype=int)
    for i in range(len(x)):
        if x.close.iloc[i]>x.sslH.iloc[i]: state[i]=1
        elif x.close.iloc[i]<x.sslL.iloc[i]: state[i]=-1
        else: state[i]=state[i-1] if i else 0
    x["ssl"]=state; x["sslLflip"]=(x.ssl==1)&(x.ssl.shift()==-1); x["sslSflip"]=(x.ssl==-1)&(x.ssl.shift()==1)
    # Classic Supertrend (ATR10, multiplier 3)
    at=rma(tr,10); mid=(x.high+x.low)/2
    up=(mid+3*at).to_numpy(float); lo=(mid-3*at).to_numpy(float); fu=up.copy(); fl=lo.copy(); dr=np.ones(len(x),int)
    for i in range(1,len(x)):
        if not np.isfinite(fu[i-1]) or not np.isfinite(fl[i-1]): continue
        if up[i]>fu[i-1] and c[i-1]<=fu[i-1]: fu[i]=fu[i-1]
        if lo[i]<fl[i-1] and c[i-1]>=fl[i-1]: fl[i]=fl[i-1]
        if dr[i-1]==-1 and c[i]>fu[i]: dr[i]=1
        elif dr[i-1]==1 and c[i]<fl[i]: dr[i]=-1
        else: dr[i]=dr[i-1]
    x["st"]=dr; x["stL"]=(x.st==1)&(x.st.shift()==-1); x["stS"]=(x.st==-1)&(x.st.shift()==1)
    return x

def signals(x,f):
    if f=="UT_BOT_EMA4H": return x.utL&x.regL, x.utS&x.regS
    if f=="SSL_QQE_EMA4H": return x.sslLflip&(x.rsi_smooth>50)&x.regL, x.sslSflip&(x.rsi_smooth<50)&x.regS
    return x.stL&(x.ssl==1)&(x.rsi_smooth>50)&x.regL, x.stS&(x.ssl==-1)&(x.rsi_smooth<50)&x.regS

def run_coin(coin,x,f,fee,slip):
    ls,ss=signals(x,f); out=[]; i=250
    while i<len(x)-1:
        side="LONG" if bool(ls.iloc[i]) else ("SHORT" if bool(ss.iloc[i]) else None)
        if not side: i+=1; continue
        atr=float(x.atr.iloc[i])
        if not np.isfinite(atr) or atr<=0: i+=1; continue
        en_i=i+1; en=float(x.open.iloc[en_i]); risk=STOP_ATR*atr
        sl,tp=(en-risk,en+RR*risk) if side=="LONG" else (en+risk,en-RR*risk)
        end_i=min(en_i+MAX_HOLD,len(x)-1); ex_i=end_i; ex=float(x.close.iloc[end_i]); result="TIME"; gross=None
        for j in range(en_i,end_i+1):
            op,hi,lo,cl=[float(x[k].iloc[j]) for k in ("open","high","low","close")]
            if side=="LONG":
                if lo<=sl: ex=min(sl,op) if op<sl else sl; gross=(ex-en)/risk; result="SL"; ex_i=j; break
                if hi>=tp: ex=tp; gross=RR; result="TP"; ex_i=j; break
            else:
                if hi>=sl: ex=max(sl,op) if op>sl else sl; gross=(en-ex)/risk; result="SL"; ex_i=j; break
                if lo<=tp: ex=tp; gross=RR; result="TP"; ex_i=j; break
        if gross is None: gross=(ex-en)/risk if side=="LONG" else (en-ex)/risk
        cost=2*(fee+slip)/10000*en/risk
        out.append({"coin":coin,"family":f,"side":side,"signal_time":x.timestamp.iloc[i].isoformat(),"entry_time":x.timestamp.iloc[en_i].isoformat(),"exit_time":x.timestamp.iloc[ex_i].isoformat(),"entry":en,"sl":sl,"tp":tp,"exit":ex,"gross_R":gross,"cost_R":cost,"net_R":gross-cost,"result":result,"bars_held":ex_i-en_i+1,"source":str(x.data_source.iloc[en_i]) if "data_source" in x else "unknown"})
        i=ex_i+COOLDOWN+1
    return out

def stat(ts):
    if not ts: return {"trades":0,"win_rate_pct":None,"net_R":0,"expectancy_R":None,"profit_factor":None,"max_closed_equity_DD_R":0}
    r=np.array([t["net_R"] for t in ts]); w=r[r>0]; l=r[r<0]; eq=np.cumsum(r); peak=np.maximum.accumulate(np.r_[0,eq])[1:]
    return {"trades":len(r),"win_rate_pct":round(100*float((r>0).mean()),2),"net_R":round(float(r.sum()),3),"expectancy_R":round(float(r.mean()),5),"profit_factor":round(float(w.sum()/-l.sum()),4) if len(l) else None,"max_closed_equity_DD_R":round(float((eq-peak).min()),3)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",default="data"); ap.add_argument("--out",default="research/results/premium_indicator_benchmark"); ap.add_argument("--fee-bps",type=float,default=5); ap.add_argument("--slippage-bps",type=float,default=2); args=ap.parse_args()
    root,out=Path(args.data),Path(args.out); out.mkdir(parents=True,exist_ok=True); frames={}; sources={}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files=list(folder.glob("*.csv")); pieces=[]
        for f in files:
            d=pd.read_csv(f)
            if "timestamp" in d: 
                if "data_source" not in d: d["data_source"]="unknown"
                pieces.append(d)
        if not pieces: continue
        raw=pd.concat(pieces,ignore_index=True).drop_duplicates("timestamp").sort_values("timestamp")
        if len(raw)<3300: continue
        frames[folder.name.upper()]=enrich(raw); sources[folder.name.upper()]=raw.data_source.value_counts().to_dict()
    if not frames: raise SystemExit("No usable OHLCV files found")
    windows={"full":(None,None),"train_2025_to_2026Q1":("2025-01-01","2026-04-01"),"test_Apr_Aug_2026":("2026-04-01","2026-09-01"),"holdout_Sep05_30_2026":("2026-09-05","2026-10-01")}
    summary={"experiment":"public_indicator_concept_benchmark","RR":RR,"stop_ATR":STOP_ATR,"max_hold_bars":MAX_HOLD,"cooldown_bars":COOLDOWN,"fee_bps_per_side":args.fee_bps,"slippage_bps_per_side":args.slippage_bps,"coins":sorted(frames),"source_counts_by_coin":sources,"families":{}}
    allrows=[]
    for f in FAMILIES:
        rows=[]
        for coin,x in frames.items(): rows+=run_coin(coin,x,f,args.fee_bps,args.slippage_bps)
        rows.sort(key=lambda t:t["exit_time"]); allrows+=rows
        summary["families"][f]={name:stat([t for t in rows if (a is None or t["entry_time"]>=a) and (b is None or t["entry_time"]<b)]) for name,(a,b) in windows.items()}
        pd.DataFrame(rows).to_csv(out/(f.lower()+"_trades.csv"),index=False)
    pd.DataFrame(allrows).to_csv(out/"all_trades.csv",index=False)
    mix={}
    for counts in sources.values():
        for k,v in counts.items(): mix[k]=mix.get(k,0)+int(v)
    summary["source_mix_total_candles"]=mix
    (out/"summary.json").write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
