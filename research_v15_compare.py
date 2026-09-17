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

BASE = "https://data.binance.vision/data/futures/um/monthly/klines/{s}USDT/15m/{s}USDT-15m-{m}.zip"
S = requests.Session()


def months(start, end):
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    while cur <= end:
        yield cur.strftime('%Y-%m')
        cur = datetime(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1, tzinfo=timezone.utc)


def fetch(symbol, days):
    end=datetime.now(timezone.utc); start=end-timedelta(days=days); parts=[]
    for m in months(start,end):
        try:
            r=S.get(BASE.format(s=symbol,m=m),timeout=60)
            if r.status_code==404: continue
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                names=[n for n in z.namelist() if n.endswith('.csv')]
                if not names: continue
                q=pd.read_csv(z.open(names[0]),header=None)
            q=pd.DataFrame({'timestamp':pd.to_datetime(q.iloc[:,0],unit='ms',utc=True),'open':pd.to_numeric(q.iloc[:,1],errors='coerce'),'high':pd.to_numeric(q.iloc[:,2],errors='coerce'),'low':pd.to_numeric(q.iloc[:,3],errors='coerce'),'close':pd.to_numeric(q.iloc[:,4],errors='coerce'),'volume':pd.to_numeric(q.iloc[:,5],errors='coerce')}).dropna()
            parts.append(q)
        except Exception as e:
            print(f'DATA ERROR {symbol} {m}: {type(e).__name__}: {e}')
        time.sleep(.05)
    if not parts: raise RuntimeError('no archive data')
    q=pd.concat(parts,ignore_index=True).drop_duplicates('timestamp').sort_values('timestamp')
    return q[(q.timestamp>=pd.Timestamp(start))&(q.timestamp<=pd.Timestamp(end))].reset_index(drop=True)


def run(x,lm,sm,stop_style):
    idx=sorted([(int(i),1) for i in np.flatnonzero(lm.to_numpy()) if i>=250]+[(int(i),-1) for i in np.flatnonzero(sm.to_numpy()) if i>=250])
    H,L,T=x.high.to_numpy(),x.low.to_numpy(),x.timestamp.to_numpy(); out=[]; last=-1
    for i,side in idx:
        if i<=last: continue
        lv=trade_levels(x,i,side,stop_style)
        if lv is None: continue
        e,sl,tp,risk=lv; ex=None; res=None; mfe=0.; mae=0.
        for j in range(i+1,min(len(x),i+1+MAX_HOLD_BARS)):
            if side==1:
                mfe=max(mfe,(H[j]-e)/risk); mae=min(mae,(L[j]-e)/risk); hit_sl=L[j]<=sl; hit_tp=H[j]>=tp
            else:
                mfe=max(mfe,(e-L[j])/risk); mae=min(mae,(e-H[j])/risk); hit_sl=H[j]>=sl; hit_tp=L[j]<=tp
            if hit_sl and hit_tp: res='SL'; ex=j; break
            if hit_sl: res='SL'; ex=j; break
            if hit_tp: res='TP'; ex=j; break
        if ex is None: continue
        out.append({'entry_time':pd.Timestamp(T[i]),'exit_time':pd.Timestamp(T[ex]),'side':'LONG' if side==1 else 'SHORT','entry':e,'sl':sl,'tp':tp,'result':res,'R':RR if res=='TP' else -1.,'mfe_R':mfe,'mae_R':mae,'dist_ema20_atr':float(x.dist_ema20_atr.iloc[i]),'move5_atr':float(x.move5_atr.iloc[i])})
        last=ex
    return pd.DataFrame(out)


def stats(name,tr):
    if tr.empty: return {'variant':name,'trades':0,'wins':0,'losses':0,'winrate_pct':0,'net_R':0,'profit_factor':0,'expectancy_R':0,'max_dd_R':0,'median_dist_ema_atr':np.nan,'median_move5_atr':np.nan,'median_MFE_R':np.nan,'median_MAE_R':np.nan}
    w=(tr.result=='TP').sum(); l=(tr.result=='SL').sum(); gp=tr.loc[tr.R>0,'R'].sum(); gl=-tr.loc[tr.R<0,'R'].sum(); eq=tr.R.cumsum(); dd=eq-eq.cummax()
    return {'variant':name,'trades':len(tr),'wins':int(w),'losses':int(l),'winrate_pct':100*w/len(tr),'net_R':tr.R.sum(),'profit_factor':gp/gl if gl else float('inf'),'expectancy_R':tr.R.mean(),'max_dd_R':dd.min(),'median_dist_ema_atr':tr.dist_ema20_atr.median(),'median_move5_atr':tr.move5_atr.median(),'median_MFE_R':tr.mfe_R.median(),'median_MAE_R':tr.mae_R.median()}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--days',type=int,default=180); ap.add_argument('--out',type=Path,default=Path('v15_engine_compare_results.csv')); a=ap.parse_args()
    frames=[]; diags=[]
    for coin in COINS:
        try:
            x=enrich(fetch(coin,a.days)); old_l,old_s=signal_mask(x,LEGACY_CORE_NAME); new_l,new_s=signal_mask(x,CORE_NAME)
            diags.append({'coin':coin,'candles':len(x),'legacy_signal_bars':int(old_l.sum()+old_s.sum()),'early_signal_bars':int(new_l.sum()+new_s.sum())})
            for name,lm,sm,stop in [('LEGACY_BASELINE',old_l,old_s,'LEGACY'),('EARLY_RECLAIM_LEGACY_STOP',new_l,new_s,'LEGACY'),('EARLY_RECLAIM_STRUCTURE_STOP',new_l,new_s,'STRUCTURE')]:
                tr=run(x,lm,sm,stop)
                if not tr.empty: tr.insert(0,'coin',coin); tr.insert(1,'variant',name); frames.append(tr)
            print(f'{coin:5s} candles={len(x):6d} legacy={int(old_l.sum()+old_s.sum()):3d} early={int(new_l.sum()+new_s.sum()):3d}')
        except Exception as e:
            diags.append({'coin':coin,'candles':0,'legacy_signal_bars':0,'early_signal_bars':0,'error':f'{type(e).__name__}: {e}'})
            print(f'{coin:5s} ERROR {type(e).__name__}: {e}')
    tr=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    summary=pd.DataFrame([stats(v,tr[tr.variant==v] if not tr.empty else pd.DataFrame()) for v in ['LEGACY_BASELINE','EARLY_RECLAIM_LEGACY_STOP','EARLY_RECLAIM_STRUCTURE_STOP']])
    summary.to_csv(a.out,index=False); tr.to_csv(a.out.with_name('v15_engine_compare_trades.csv'),index=False); pd.DataFrame(diags).to_csv(a.out.with_name('v15_engine_compare_diagnostics.csv'),index=False)
    print('\n'+summary.to_string(index=False,float_format=lambda z:f'{z:.3f}'))

if __name__=='__main__': main()
