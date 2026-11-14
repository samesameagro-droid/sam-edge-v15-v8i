"""Pine-aligned indicator and signal core for ST-ADX V3.3 RSI/extension-filtered paper strategy."""
from __future__ import annotations
from typing import Iterable
import numpy as np
import pandas as pd
ST_LENGTH=12
ST_MULTIPLIER=2.8
DMI_LENGTH=40
ADX_SMOOTHING=40
ADX_MIN=30.0
ADX_RISING_BARS=2
RSI_LENGTH=12
EMA_LENGTH=53
RSI_LONG_MIN=60.0
RSI_LONG_MAX=70.0
RSI_SHORT_MIN=35.0
RSI_SHORT_MAX=40.0
SHORT_MAX_EMA_EXT_ATR=3.25
RISK_ATR_LENGTH=10
ATR_SL_MULTIPLIER=1.2
ATR_TP_MULTIPLIER=3.5
COOLDOWN_BARS=1
LONG_ALLOWLIST={"ENA","XRP","ZEC","TIA","ORDI","AKE","WIF","APR","XLM","VIRTUAL","DYDX"}
SHORT_ALLOWLIST={"ARB","AVAX","BTC","ENA","ETH","LINK","NEAR","SOL","SUI","UNI","XRP","DOT","LTC","ADA"}
def wilder_rma(values: Iterable[float], length: int) -> np.ndarray:
    x=np.asarray(values,dtype=float); out=np.full(x.shape,np.nan,dtype=float)
    valid=np.flatnonzero(np.isfinite(x))
    if len(valid)<length:return out
    first=int(valid[0]); seed=first+length-1
    if seed>=len(x) or not np.all(np.isfinite(x[first:seed+1])):
        run=0; seed=-1
        for i in range(first,len(x)):
            run=run+1 if np.isfinite(x[i]) else 0
            if run>=length:seed=i;break
        if seed<0:return out
        first=seed-length+1
    out[seed]=float(np.mean(x[first:seed+1])); alpha=1.0/float(length)
    for i in range(seed+1,len(x)):
        out[i]=out[i-1]+alpha*(x[i]-out[i-1]) if np.isfinite(x[i]) else out[i-1]
    return out
def pine_ema(values: Iterable[float], length: int) -> np.ndarray:
    return pd.Series(np.asarray(values,dtype=float)).ewm(span=length,adjust=False,min_periods=1).mean().to_numpy(float)
def _true_range(high,low,close):
    prev=np.roll(close,1);prev[0]=np.nan
    tr=np.maximum(high-low,np.maximum(np.abs(high-prev),np.abs(low-prev)))
    if len(tr):tr[0]=high[0]-low[0]
    return tr
def _pine_supertrend(high,low,close,atr,factor,length):
    n=len(close);st=np.full(n,np.nan);direction=np.ones(n,dtype=int)
    upper=np.full(n,np.nan);lower=np.full(n,np.nan);hl2=(high+low)/2.0
    for i in range(n):
        if not np.isfinite(atr[i]):continue
        bu=hl2[i]+factor*atr[i];bl=hl2[i]-factor*atr[i]
        if i==0 or not np.isfinite(upper[i-1]):upper[i]=bu;lower[i]=bl
        else:
            pu=upper[i-1];pl=lower[i-1];pc=close[i-1]
            lower[i]=bl if (bl>pl or pc<pl) else pl
            upper[i]=bu if (bu<pu or pc>pu) else pu
        if i==0 or not np.isfinite(atr[i-1]) or not np.isfinite(st[i-1]):direction[i]=1
        elif np.isclose(st[i-1],upper[i-1],rtol=0.0,atol=0.0):direction[i]=-1 if close[i]>upper[i] else 1
        else:direction[i]=1 if close[i]<lower[i] else -1
        st[i]=lower[i] if direction[i]==-1 else upper[i]
    return st,direction
def add_indicators(ohlcv: pd.DataFrame) -> pd.DataFrame:
    required={"timestamp","open","high","low","close","volume"};missing=required-set(ohlcv.columns)
    if missing:raise ValueError(f"OHLCV missing columns: {sorted(missing)}")
    df=ohlcv.copy().reset_index(drop=True)
    for col in ("open","high","low","close","volume"):df[col]=pd.to_numeric(df[col],errors="coerce")
    if df[list(required)].isna().any().any():raise ValueError("OHLCV contains invalid values")
    high=df.high.to_numpy(float);low=df.low.to_numpy(float);close=df.close.to_numpy(float);op=df.open.to_numpy(float)
    tr=_true_range(high,low,close);atr_st=wilder_rma(tr,ST_LENGTH);atr_risk=wilder_rma(tr,RISK_ATR_LENGTH)
    up=np.full(len(df),np.nan);down=np.full(len(df),np.nan)
    if len(df)>1:up[1:]=high[1:]-high[:-1];down[1:]=low[:-1]-low[1:]
    plus=np.where((up>down)&(up>0),up,0.0);minus=np.where((down>up)&(down>0),down,0.0)
    if len(df):plus[0]=np.nan;minus[0]=np.nan
    tr_dmi=wilder_rma(tr,DMI_LENGTH);plus_sm=wilder_rma(plus,DMI_LENGTH);minus_sm=wilder_rma(minus,DMI_LENGTH)
    with np.errstate(divide="ignore",invalid="ignore"):
        pdi=100.0*plus_sm/tr_dmi;mdi=100.0*minus_sm/tr_dmi
    pdi=pd.Series(pdi).ffill().to_numpy(float);mdi=pd.Series(mdi).ffill().to_numpy(float);sm=pdi+mdi
    with np.errstate(divide="ignore",invalid="ignore"):dx=100.0*np.abs(pdi-mdi)/sm
    dx=np.where((sm==0)&np.isfinite(sm),0.0,dx);adx=wilder_rma(dx,ADX_SMOOTHING)
    delta=np.full(len(df),np.nan)
    if len(df)>1:delta[1:]=close[1:]-close[:-1]
    gain=np.where(delta>0,delta,0.0);loss=np.where(delta<0,-delta,0.0)
    if len(df):gain[0]=np.nan;loss[0]=np.nan
    ag=wilder_rma(gain,RSI_LENGTH);al=wilder_rma(loss,RSI_LENGTH)
    with np.errstate(divide="ignore",invalid="ignore"):rs=ag/al;rsi=100.0-100.0/(1.0+rs)
    rsi=np.where((al==0)&(ag>0),100.0,rsi);rsi=np.where((ag==0)&(al>0),0.0,rsi)
    ema=pine_ema(close,EMA_LENGTH);st,st_dir=_pine_supertrend(high,low,close,atr_st,ST_MULTIPLIER,ST_LENGTH)
    bull=st_dir<0;bear=st_dir>0;prev_bull=np.r_[False,bull[:-1]];prev_bear=np.r_[False,bear[:-1]]
    flip_bull=bull&~prev_bull;flip_bear=bear&~prev_bear
    # ADX Rising 2: both sequential changes in the 3-point window must be positive.
    rising=np.zeros(len(df),dtype=bool)
    for i in range(ADX_RISING_BARS,len(df)):
        w=adx[i-ADX_RISING_BARS:i+1]
        if np.all(np.isfinite(w)):rising[i]=bool(np.all(np.diff(w)>0))
    strong=(adx>ADX_MIN)&rising;bull_candle=(close>op)|flip_bull;bear_candle=(close<op)|flip_bear
    above=close>ema;below=close<ema
    df["tr"]=tr;df["atr_st"]=atr_st;df["atr_risk"]=atr_risk;df["di_plus"]=pdi;df["di_minus"]=mdi
    df["adx"]=adx;df["adx_rising"]=rising;df["rsi"]=rsi;df["ema53"]=ema;df["supertrend"]=st;df["st_direction"]=st_dir
    df["st_bull"]=bull;df["st_bear"]=bear;df["st_flip_bull"]=flip_bull;df["st_flip_bear"]=flip_bear
    # V3.3 anti-chasing filters: bounded RSI windows and a short-side EMA53 extension cap.
    long_rsi_ok=(rsi>=RSI_LONG_MIN)&(rsi<=RSI_LONG_MAX)
    short_rsi_ok=(rsi>=RSI_SHORT_MIN)&(rsi<=RSI_SHORT_MAX)
    short_extension_atr=np.divide(ema-close,atr_risk,out=np.full(len(df),np.inf),where=atr_risk>0)
    short_extension_ok=short_extension_atr<=SHORT_MAX_EMA_EXT_ATR
    df["short_extension_atr"]=short_extension_atr
    df["long_signal"]=bull&strong&long_rsi_ok&bull_candle&(above|flip_bull)
    df["short_signal"]=bear&strong&short_rsi_ok&short_extension_ok&bear_candle&(below|flip_bear)
    return df
