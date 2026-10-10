#!/usr/bin/env python3
"""Research-only CB Pro NXT-inspired strategy benchmark. Not the proprietary indicator."""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from premium_indicator_benchmark import enrich, stat

VARIANTS = ("A_ZONE_EMA_MACD", "B_PLUS_STOCH", "C_PLUS_CONFIRMED_STRUCTURE")

def add_cb_features(x):
    x=x.copy()
    def ema(s,n): return s.ewm(span=n,adjust=False,min_periods=n).mean()
    def rma(s,n): return s.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    x["ema_fast"]=ema(x.close,10); x["ema_slow"]=ema(x.close,20); x["ema60"]=ema(x.close,60)
    x["ema_cross_up"]=(x.ema_fast>x.ema_slow)&(x.ema_fast.shift()<=x.ema_slow.shift())
    x["ema_cross_dn"]=(x.ema_fast<x.ema_slow)&(x.ema_fast.shift()>=x.ema_slow.shift())
    # MACD 10/22/9, matching the visible CBPro_NXT_MACD legend approximately
    m=ema(x.close,10)-ema(x.close,22); sig=ema(m,9)
    x["macd_up"]=(m>sig)&(m.shift()<=sig.shift())
    x["macd_dn"]=(m<sig)&(m.shift()>=sig.shift())
    # Stochastic 14,3,3; only confirmed candle values are used
    lo=x.low.rolling(14,min_periods=14).min(); hi=x.high.rolling(14,min_periods=14).max()
    k=100*(x.close-lo)/(hi-lo).replace(0,np.nan); x["stoch_k"]=k.rolling(3,min_periods=3).mean()
    x["stoch_d"]=x.stoch_k.rolling(3,min_periods=3).mean()
    x["stoch_up"]=(x.stoch_k>x.stoch_d)&(x.stoch_k.shift()<=x.stoch_d.shift())
    x["stoch_dn"]=(x.stoch_k<x.stoch_d)&(x.stoch_k.shift()>=x.stoch_d.shift())
    # Transparent zone proxy: ATR(14) chandelier-like trailing zone, ratchets only in direction of trend.
    atr=x.atr.to_numpy(float); close=x.close.to_numpy(float); high=x.high.to_numpy(float); low=x.low.to_numpy(float)
    n=len(x); zone=np.full(n,np.nan); state=np.zeros(n,dtype=int)
    for i in range(n):
        if not np.isfinite(atr[i]): continue
        if i==0 or not np.isfinite(zone[i-1]):
            zone[i]=close[i]-2*atr[i]; state[i]=1; continue
        prev=zone[i-1]; ps=state[i-1]
        if close[i]>prev:
            state[i]=1; zone[i]=max(prev, close[i]-2*atr[i]) if ps==1 else close[i]-2*atr[i]
        else:
            state[i]=-1; zone[i]=min(prev, close[i]+2*atr[i]) if ps==-1 else close[i]+2*atr[i]
    x["zone_line"]=zone; x["zone_state"]=state
    x["zone_up"]=(x.zone_state==1)&(x.zone_state.shift()!=1)
    x["zone_dn"]=(x.zone_state==-1)&(x.zone_state.shift()!=-1)
    # Confirmed 3-left/3-right pivots. Signal is available only after right bars close.
    left=right=3
    ph=x.high.shift(right).rolling(left+right+1,min_periods=left+right+1).max()
    pl=x.low.shift(right).rolling(left+right+1,min_periods=left+right+1).min()
    x["pivot_high_confirm"]=(x.high.shift(right)==ph)
    x["pivot_low_confirm"]=(x.low.shift(right)==pl)
    x["last_pivot_high"]=x.high.shift(right).where(x.pivot_high_confirm).ffill()
    x["last_pivot_low"]=x.low.shift(right).where(x.pivot_low_confirm).ffill()
    x["structure_long"]=(x.close>x.last_pivot_high)&(x.last_pivot_high.notna())
    x["structure_short"]=(x.close<x.last_pivot_low)&(x.last_pivot_low.notna())
    # Trend proxy to avoid look-ahead; use close relative to EMA60 and EMA60 slope.
    x["trend_long"]=(x.close>x.ema60)&(x.ema60>x.ema60.shift(5))
    x["trend_short"]=(x.close<x.ema60)&(x.ema60<x.ema60.shift(5))
    return x

def make_signals(x, variant):
    base_l=x.zone_up & x.ema_cross_up & x.macd_up & x.trend_long
    base_s=x.zone_dn & x.ema_cross_dn & x.macd_dn & x.trend_short
    if variant in ("B_PLUS_STOCH","C_PLUS_CONFIRMED_STRUCTURE"):
        base_l=base_l & x.stoch_up
        base_s=base_s & x.stoch_dn
    if variant=="C_PLUS_CONFIRMED_STRUCTURE":
        base_l=base_l & x.structure_long
        base_s=base_s & x.structure_short
    return base_l.fillna(False),base_s.fillna(False)

def simulate(coin,x,variant,fee,slip):
    long_sig,short_sig=make_signals(x,variant); trades=[]; i=250
    while i<len(x)-1:
        side="LONG" if bool(long_sig.iloc[i]) else ("SHORT" if bool(short_sig.iloc[i]) else None)
        if side is None: i+=1; continue
        entry_i=i+1; entry=float(x.open.iloc[entry_i]); atr=float(x.atr.iloc[i])
        z=float(x.zone_line.iloc[i]) if np.isfinite(x.zone_line.iloc[i]) else np.nan
        if not np.isfinite(atr) or atr<=0 or not np.isfinite(z) or (side=="LONG" and z>=entry) or (side=="SHORT" and z<=entry):
            i+=1; continue
        # The zone line is initial SL; targets are 1R and 2R. 50% exits at TP1, 50% at TP2.
        sl=z; risk=abs(entry-sl); tp1=entry+(risk if side=="LONG" else -risk); tp2=entry+(2*risk if side=="LONG" else -2*risk)
        end=min(entry_i+96,len(x)-1); exit_i=end; exit_px=float(x.close.iloc[end]); reason="TIME"
        runner=0.0; first_hit=False; realized=0.0
        for j in range(entry_i,end+1):
            op=float(x.open.iloc[j]); hi=float(x.high.iloc[j]); low=float(x.low.iloc[j]); cl=float(x.close.iloc[j])
            # Conservative ambiguity rule: stop checked before targets.
            stop_hit=(low<=sl) if side=="LONG" else (hi>=sl)
            if stop_hit:
                exit_px=min(sl,op) if side=="LONG" and op<sl else (max(sl,op) if side=="SHORT" and op>sl else sl)
                exit_i=j; reason="SL"
                if first_hit: realized += 0.5*((exit_px-entry)/risk if side=="LONG" else (entry-exit_px)/risk)
                else: realized=((exit_px-entry)/risk if side=="LONG" else (entry-exit_px)/risk)
                break
            target1_hit=(hi>=tp1) if side=="LONG" else (low<=tp1)
            target2_hit=(hi>=tp2) if side=="LONG" else (low<=tp2)
            if not first_hit and target1_hit:
                first_hit=True; realized += 0.5
            if first_hit and target2_hit:
                realized += 1.0; exit_px=tp2; exit_i=j; reason="TP2"; break
            # Trailing stop only ratchets from fully closed candle's zone, applied from next candle.
            if j<end and np.isfinite(x.zone_line.iloc[j]):
                nz=float(x.zone_line.iloc[j])
                if side=="LONG" and nz>sl and nz<cl: sl=nz
                elif side=="SHORT" and nz<sl and nz>cl: sl=nz
        else:
            move=(exit_px-entry)/risk if side=="LONG" else (entry-exit_px)/risk
            realized += 0.5*move if first_hit else move
        if reason=="TIME":
            # runner is half if TP1 already hit; otherwise full position remains
            pass
        cost=2*(fee+slip)/10000*entry/risk
        net=realized-cost
        trades.append({"coin":coin,"variant":variant,"side":side,"signal_time":x.timestamp.iloc[i].isoformat(),"entry_time":x.timestamp.iloc[entry_i].isoformat(),"exit_time":x.timestamp.iloc[exit_i].isoformat(),"entry":entry,"initial_sl":z,"tp1":tp1,"tp2":tp2,"exit":exit_px,"gross_R":round(realized,6),"cost_R":round(cost,6),"net_R":round(net,6),"result":reason,"bars_held":exit_i-entry_i+1})
        i=exit_i+5
    return trades

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",default="data"); ap.add_argument("--out",default="research/results/cbpro_nxt_benchmark"); ap.add_argument("--fee-bps",type=float,default=5); ap.add_argument("--slippage-bps",type=float,default=2); args=ap.parse_args()
    root=Path(args.data); out=Path(args.out); out.mkdir(parents=True,exist_ok=True); frames={}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        pieces=[]
        for f in folder.glob("*.csv"):
            d=pd.read_csv(f)
            if "timestamp" in d: pieces.append(d)
        if not pieces: continue
        raw=pd.concat(pieces,ignore_index=True).drop_duplicates("timestamp").sort_values("timestamp")
        if len(raw)<3300: continue
        frames[folder.name.upper()]=add_cb_features(enrich(raw))
    if not frames: raise SystemExit("No usable OHLCV data found")
    windows={"full":(None,None),"train_2025_to_2026Q1":("2025-01-01","2026-04-01"),"test_2026Q2_to_aug":("2026-04-01","2026-09-01"),"holdout_2026_sep":("2026-09-05","2026-10-01")}
    alltr=[]
    for coin,x in frames.items():
        for v in VARIANTS:
            ts=simulate(coin,x,v,args.fee_bps,args.slippage_bps); alltr.extend(ts)
    df=pd.DataFrame(alltr)
    if not df.empty:
        df["entry_time_dt"]=pd.to_datetime(df.entry_time,utc=True)
        df.to_csv(out/"trades.csv",index=False)
    summary={"benchmark":"CB Pro NXT-inspired public-rule reconstruction; NOT proprietary CB Pro NXT code","data_coins":len(frames),"variants":{},"costs":{"fee_bps_per_side":args.fee_bps,"slippage_bps_per_side":args.slippage_bps},"caveats":["OHLCV proxy from Binance archive, not BingX fills","funding not included","confirmed-pivot structure variant is intentionally delayed to avoid ZigZag look-ahead","Zone is an explicit ATR proxy, not the proprietary zone formula","TP1/TP2 modeled as 50% at 1R and 50% at 2R, stop-first on ambiguous bars"]}
    for v in VARIANTS:
        subset=df[df.variant==v] if not df.empty else pd.DataFrame()
        summary["variants"][v]={}
        for name,(start,end) in windows.items():
            z=subset.copy()
            if start: z=z[z.entry_time_dt>=pd.Timestamp(start,tz="UTC")]
            if end: z=z[z.entry_time_dt<pd.Timestamp(end,tz="UTC")]
            summary["variants"][v][name]=stat(z.to_dict("records")) if not z.empty else stat([])
            if not z.empty:
                summary["variants"][v][name]["long_trades"]=int((z.side=="LONG").sum())
                summary["variants"][v][name]["short_trades"]=int((z.side=="SHORT").sum())
    (out/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
