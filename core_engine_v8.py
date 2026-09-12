
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd

COINS = ['ADA','AVAX','BNB','BTC','CRV','DOGE','ETH','FET','LINK','SOL','SUI','UNI','XRP']
SWING_LOOKBACK = 20
MAX_HOLD_BARS = 96

@dataclass(frozen=True)
class Candidate:
    core: str
    label: str
    rr: float

# V8 is designed as a controlled optimization around the U47/V7O DNA.
# It does NOT use coin-specific rules. All thresholds are universal or
# normalized against ATR / historical indicator behavior.
CORES = [
    Candidate('V8A_V7O_CONTROL', 'V7O control, RR 1.25', 1.25),
    Candidate('V8B_ADX_PCTL', 'ADX expansion by percentile', 1.25),
    Candidate('V8C_ADX_ATR_LOC', 'ADX percentile + ATR location', 1.25),
    Candidate('V8D_ADX_ROOM', 'ADX percentile + target room', 1.25),
    Candidate('V8E_ADX_NOEX', 'ADX percentile + anti-exhaustion', 1.25),
    Candidate('V8F_ADX_VOL', 'ADX percentile + volume expansion', 1.25),
    Candidate('V8G_ADX_HTF', 'ADX percentile + HTF alignment', 1.25),
    Candidate('V8H_ADX_FLEX', 'ADX percentile + flexible 2-of-3 quality', 1.25),
    Candidate('V8I_ADX_ASYM', 'ADX percentile + directional asymmetry', 1.25),
    Candidate('V8J_ADX_STRUCTURE', 'ADX percentile + structural reclaim', 1.25),
    Candidate('V8K_ADX_ROOM_NOEX', 'ADX percentile + room + no exhaustion', 1.25),
    Candidate('V8L_ADX_ROOM_VOL', 'ADX percentile + room + volume', 1.25),
    Candidate('V8M_ADX_ROOM_HTF', 'ADX percentile + room + HTF', 1.25),
    Candidate('V8N_ADX_HYBRID', 'ADX percentile + room + 2-of-3 quality', 1.25),
    Candidate('V8O_UNIVERSAL_MASTER', 'Universal master candidate', 1.25),
]

def wilder(s,n=14):
    return s.ewm(alpha=1/n,adjust=False).mean()

def load_coin(root:Path, coin:str) -> pd.DataFrame:
    files = sorted((root/coin).glob('*.zip'))
    if not files:
        raise FileNotFoundError(f'Missing raw data for {coin}: expected {root/coin}/*.zip')
    fs=[]
    for p in files:
        with zipfile.ZipFile(p) as z:
            cs=[n for n in z.namelist() if n.lower().endswith('.csv')]
            if not cs:
                continue
            q=pd.read_csv(z.open(cs[0]))
        cmap={c.lower():c for c in q.columns}
        need=['open_time','open','high','low','close','volume']
        miss=[c for c in need if c not in cmap]
        if miss:
            raise ValueError(f'{p}: missing columns {miss}')
        q=q[[cmap[c] for c in need]].copy()
        q.columns=need
        q['timestamp']=pd.to_datetime(q.open_time,unit='ms',utc=True)
        fs.append(q.drop(columns='open_time'))
    if not fs:
        raise ValueError(f'No CSV data found under {root/coin}')
    x=pd.concat(fs,ignore_index=True).drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
    for c in ['open','high','low','close','volume']:
        x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.dropna().reset_index(drop=True)

def enrich(df):
    x=df.copy()
    prev=x.close.shift(1)
    tr=pd.concat([(x.high-x.low),(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1)
    x['atr']=wilder(tr,14)
    for p in [20,21,50,200]:
        x[f'ema{p}']=x.close.ewm(span=p,adjust=False).mean()

    d=x.close.diff()
    gain=wilder(d.clip(lower=0))
    loss=wilder(-d.clip(upper=0))
    x['rsi']=100-(100/(1+gain/loss.replace(0,np.nan)))

    x['volr']=x.volume/x.volume.rolling(20).mean()
    x['vol_slope']=x.volr-x.volr.shift(2)

    up=x.high.diff()
    dn=-x.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=x.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=x.index)
    x['pdi']=100*wilder(plus)/x.atr.replace(0,np.nan)
    x['mdi']=100*wilder(minus)/x.atr.replace(0,np.nan)
    dx=100*(x.pdi-x.mdi).abs()/(x.pdi+x.mdi).replace(0,np.nan)
    x['adx']=wilder(dx)

    # Historical percentile of ADX: portable across coins with different volatility scales.
    x['adx_pct']=x.adx.rolling(200,min_periods=100).rank(pct=True)
    x['adx_delta']=x.adx-x.adx.shift(3)
    x['adx_accel']=x.adx_delta-x.adx_delta.shift(3)

    day=x.timestamp.dt.floor('D')
    typ=(x.high+x.low+x.close)/3
    vc=x.volume.groupby(day).cumsum()
    x['vwap']=(typ*x.volume).groupby(day).cumsum()/vc.replace(0,np.nan)

    x['swing_h']=x.high.shift(1).rolling(20).max()
    x['swing_l']=x.low.shift(1).rolling(20).min()

    rng=(x.high-x.low).replace(0,np.nan)
    x['body_atr']=(x.close-x.open).abs()/x.atr.replace(0,np.nan)
    x['close_pos']=(x.close-x.low)/rng
    x['range_atr']=(x.high-x.low)/x.atr.replace(0,np.nan)
    x['move5_atr']=abs(x.close-x.close.shift(5))/x.atr.replace(0,np.nan)
    x['dist_ema20_atr']=abs(x.close-x.ema20)/x.atr.replace(0,np.nan)
    x['dist_res_atr']=(x.swing_h-x.close)/x.atr.replace(0,np.nan)
    x['dist_sup_atr']=(x.close-x.swing_l)/x.atr.replace(0,np.nan)
    x['atr_pct']=x.atr/x.close
    x['atr_rank']=x.atr_pct.rolling(200,min_periods=100).rank(pct=True)

    x['ema20_slope']=(x.ema20-x.ema20.shift(3))/x.atr.replace(0,np.nan)
    x['ema50_slope']=(x.ema50-x.ema50.shift(3))/x.atr.replace(0,np.nan)

    x['upper_wick']=(x.high-x[['open','close']].max(axis=1))/rng
    x['lower_wick']=(x[['open','close']].min(axis=1)-x.low)/rng

    # Generic structural features.
    x['break_hi']=x.close > x.swing_h
    x['break_lo']=x.close < x.swing_l
    x['reclaim_hi']=(x.close > x.swing_h.shift(1)) & (x.low <= x.swing_h.shift(1))
    x['reclaim_lo']=(x.close < x.swing_l.shift(1)) & (x.high >= x.swing_l.shift(1))

    base=x.set_index('timestamp')
    for rule,pfx in [('1h','h1'),('4h','h4')]:
        h=base.resample(rule,label='right',closed='right').agg(
            open=('open','first'),high=('high','max'),low=('low','min'),
            close=('close','last'),volume=('volume','sum')).dropna()
        for p in [21,50,200]:
            h[f'ema{p}']=h.close.ewm(span=p,adjust=False).mean()
        h['trend_bull']=(h.ema21>h.ema50)&(h.ema50>h.ema200)
        h['trend_bear']=(h.ema21<h.ema50)&(h.ema50<h.ema200)
        h['slope21']=(h.ema21-h.ema21.shift(3))/h.atr if 'atr' in h else (h.ema21-h.ema21.shift(3))/h.close
        h['bull'] = h.close > h.ema21
        h['bear'] = h.close < h.ema21
        h=h.reset_index()[['timestamp','ema21','ema50','ema200','trend_bull','trend_bear','slope21','bull','bear']]
        h=h.rename(columns={
            'ema21':f'{pfx}_ema21','ema50':f'{pfx}_ema50','ema200':f'{pfx}_ema200',
            'trend_bull':f'{pfx}_trend_bull','trend_bear':f'{pfx}_trend_bear',
            'slope21':f'{pfx}_slope21','bull':f'{pfx}_bull','bear':f'{pfx}_bear'
        })
        x=pd.merge_asof(x.sort_values('timestamp'),h.sort_values('timestamp'),
                         on='timestamp',direction='backward',allow_exact_matches=False)

    return x

def candle_quality(r):
    bull=((r.close>r.open)&(r.body_atr>=0.45)&(r.close_pos>=0.72)&
          (r.upper_wick<=0.25)&(r.range_atr<=2.2))
    bear=((r.close<r.open)&(r.body_atr>=0.45)&(r.close_pos<=0.28)&
          (r.lower_wick<=0.25)&(r.range_atr<=2.2))
    return bull, bear

def signal_mask(x,core):
    r=x
    p=x.shift(1)

    bull=r.h1_trend_bull&r.h4_trend_bull
    bear=r.h1_trend_bear&r.h4_trend_bear

    cL,cS=candle_quality(r)

    # Adaptive ADX gate: top 30% of recent ADX and actual expansion.
    adx_gate=(r.adx_pct>=0.70)&(r.adx_delta>=0.50)
    adx_strict=(r.adx_pct>=0.80)&(r.adx_delta>=0.75)

    # Portable location / room metrics.
    roomL=r.dist_res_atr>=0.85
    roomS=r.dist_sup_atr>=0.85
    roomL_strict=r.dist_res_atr>=1.10
    roomS_strict=r.dist_sup_atr>=1.10

    locL=(r.dist_ema20_atr<=1.60)&(r.dist_ema20_atr>=0.15)
    locS=(r.dist_ema20_atr<=1.60)&(r.dist_ema20_atr>=0.15)

    noex=(r.move5_atr<=3.50)&(r.dist_ema20_atr<=1.60)&(r.range_atr<=2.25)

    vol=(r.volr>=1.05)
    volex=(r.volr>=1.15)&(r.vol_slope>=0)

    htfL=(r.h1_slope21>0)&(r.h4_slope21>0)
    htfS=(r.h1_slope21<0)&(r.h4_slope21<0)

    emaL=(r.close>r.ema20)&(r.ema20>r.ema50)
    emaS=(r.close<r.ema20)&(r.ema20<r.ema50)

    # Pullback/reclaim without requiring a rigid exact candle.
    reclaimL=(r.close>r.ema20)&(p.close<=p.ema20)
    reclaimS=(r.close<r.ema20)&(p.close>=p.ema20)

    # Core market structure.
    structureL=bull&emaL&(r.close>r.vwap)&(r.pdi>r.mdi)&r.rsi.between(46,64)
    structureS=bear&emaS&(r.close<r.vwap)&(r.mdi>r.pdi)&r.rsi.between(36,54)

    qL=pd.concat([locL,noex,volex],axis=1).sum(axis=1)
    qS=pd.concat([locS,noex,volex],axis=1).sum(axis=1)

    if core=='V8A_V7O_CONTROL':
        long=structureL&adx_gate&roomL&noex&((cL.astype(int)+volex.astype(int)+htfL.astype(int))>=2)
        short=structureS&adx_gate&roomS&noex&((cS.astype(int)+volex.astype(int)+htfS.astype(int))>=2)

    elif core=='V8B_ADX_PCTL':
        long=structureL&adx_gate&roomL&noex
        short=structureS&adx_gate&roomS&noex

    elif core=='V8C_ADX_ATR_LOC':
        long=structureL&adx_gate&locL&noex
        short=structureS&adx_gate&locS&noex

    elif core=='V8D_ADX_ROOM':
        long=structureL&adx_gate&roomL_strict&noex
        short=structureS&adx_gate&roomS_strict&noex

    elif core=='V8E_ADX_NOEX':
        long=structureL&adx_gate&roomL&(r.move5_atr<=3.0)&(r.dist_ema20_atr<=1.35)
        short=structureS&adx_gate&roomS&(r.move5_atr<=3.0)&(r.dist_ema20_atr<=1.35)

    elif core=='V8F_ADX_VOL':
        long=structureL&adx_gate&roomL&noex&volex
        short=structureS&adx_gate&roomS&noex&volex

    elif core=='V8G_ADX_HTF':
        long=structureL&adx_gate&roomL&noex&htfL
        short=structureS&adx_gate&roomS&noex&htfS

    elif core=='V8H_ADX_FLEX':
        long=structureL&adx_gate&roomL&noex&(qL>=2)
        short=structureS&adx_gate&roomS&noex&(qS>=2)

    elif core=='V8I_ADX_ASYM':
        long=structureL&(r.adx_pct>=0.65)&(r.adx_delta>=0.40)&roomL&noex&cL
        short=structureS&(r.adx_pct>=0.75)&(r.adx_delta>=0.70)&roomS&noex&cS

    elif core=='V8J_ADX_STRUCTURE':
        long=structureL&adx_gate&roomL&noex&(reclaimL|r.reclaim_hi)
        short=structureS&adx_gate&roomS&noex&(reclaimS|r.reclaim_lo)

    elif core=='V8K_ADX_ROOM_NOEX':
        long=structureL&adx_strict&roomL_strict&noex
        short=structureS&adx_strict&roomS_strict&noex

    elif core=='V8L_ADX_ROOM_VOL':
        long=structureL&adx_gate&roomL_strict&volex&noex
        short=structureS&adx_gate&roomS_strict&volex&noex

    elif core=='V8M_ADX_ROOM_HTF':
        long=structureL&adx_gate&roomL_strict&htfL&noex
        short=structureS&adx_gate&roomS_strict&htfS&noex

    elif core=='V8N_ADX_HYBRID':
        qualityL=pd.concat([locL,volex,htfL,cL],axis=1).sum(axis=1)
        qualityS=pd.concat([locS,volex,htfS,cS],axis=1).sum(axis=1)
        long=structureL&adx_gate&roomL&noex&(qualityL>=2)
        short=structureS&adx_gate&roomS&noex&(qualityS>=2)

    elif core=='V8O_UNIVERSAL_MASTER':
        # Master candidate: adaptive ADX + normalized location + room + one of
        # volume/HTF/candle quality. Designed to be selective but not microscopic.
        qualityL=pd.concat([volex,htfL,cL],axis=1).sum(axis=1)
        qualityS=pd.concat([volex,htfS,cS],axis=1).sum(axis=1)
        long=structureL&adx_gate&roomL&locL&noex&(qualityL>=1)
        short=structureS&adx_gate&roomS&locS&noex&(qualityS>=1)
    else:
        raise ValueError(core)

    return long.fillna(False), short.fillna(False)

def backtest_core(x,core,rr):
    long,short=signal_mask(x,core)
    idx=sorted(
        [(int(i),1) for i in np.flatnonzero(long.to_numpy()) if i>=250] +
        [(int(i),-1) for i in np.flatnonzero(short.to_numpy()) if i>=250]
    )
    H,L,T,C=x.high.to_numpy(),x.low.to_numpy(),x.timestamp.to_numpy(),x.close.to_numpy()
    out=[]
    last=-1
    for i,side in idx:
        if i<=last:
            continue
        a=float(x.atr.iloc[i]); e=float(C[i])
        if not np.isfinite(a) or a<=0:
            continue
        if side==1:
            sl=float(x.low.iloc[max(0,i-SWING_LOOKBACK):i].min())-a
            risk=e-sl
            tp=e+rr*risk
        else:
            sl=float(x.high.iloc[max(0,i-SWING_LOOKBACK):i].max())+a
            risk=sl-e
            tp=e-rr*risk
        if not np.isfinite(risk) or risk<=0:
            continue

        ex=None
        result=None
        for j in range(i+1,min(len(x),i+1+MAX_HOLD_BARS)):
            hit_sl=L[j]<=sl if side==1 else H[j]>=sl
            hit_tp=H[j]>=tp if side==1 else L[j]<=tp
            # Conservative tie handling: if both in same bar, count SL first.
            if hit_sl and hit_tp:
                result='SL'; ex=j; break
            if hit_sl:
                result='SL'; ex=j; break
            if hit_tp:
                result='TP'; ex=j; break
        if ex is None:
            continue
        out.append({
            'entry_time':pd.Timestamp(T[i]),'exit_time':pd.Timestamp(T[ex]),
            'side':'LONG' if side==1 else 'SHORT','entry':e,'sl':sl,'tp':tp,
            'result':result,'R':(-1.0 if result=='SL' else rr),
            'core':core,'rr':rr
        })
        last=ex
    return pd.DataFrame(out)
