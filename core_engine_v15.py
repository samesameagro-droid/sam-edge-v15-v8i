from __future__ import annotations
import numpy as np
import pandas as pd

CORE_NAME='V15_ADX4H_CANDLE2H'
RR=1.25
SWING_LOOKBACK=20
ADX_LONG_PCT=0.80
ADX_LONG_DELTA=0.75
ADX_SHORT_PCT=0.85
ADX_SHORT_DELTA=0.90

def wilder(s,n=14): return s.ewm(alpha=1/n,adjust=False).mean()

def _resample(df,rule):
    b=df.set_index('timestamp')
    return b.resample(rule,label='left',closed='left').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna().reset_index()

def _atr(h,n=14):
    prev=h.close.shift(1)
    tr=pd.concat([(h.high-h.low),(h.high-prev).abs(),(h.low-prev).abs()],axis=1).max(axis=1)
    return wilder(tr,n)

def _norm(s): return pd.to_datetime(s,utc=True).astype('datetime64[ns, UTC]')

def _adx_4h_features(df):
    h=_resample(df,'4h'); h['atr']=_atr(h)
    up=h.high.diff(); dn=-h.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=h.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=h.index)
    h['pdi']=100*wilder(plus)/h.atr.replace(0,np.nan); h['mdi']=100*wilder(minus)/h.atr.replace(0,np.nan)
    dx=100*(h.pdi-h.mdi).abs()/(h.pdi+h.mdi).replace(0,np.nan)
    h['adx']=wilder(dx); h['adx_pct']=h.adx.rolling(200,min_periods=100).rank(pct=True)
    h['adx_delta']=h.adx-h.adx.shift(3); h['adx_accel']=h.adx_delta-h.adx_delta.shift(3)
    h['timestamp']=_norm(h.timestamp+pd.Timedelta('4h'))
    return h[['timestamp','adx','adx_pct','adx_delta','adx_accel']]

def _strict_candle_features(df,rule='2h',prefix='c2h'):
    h=_resample(df,rule); h['atr']=_atr(h); rng=(h.high-h.low).replace(0,np.nan)
    h['body_atr']=(h.close-h.open).abs()/h.atr.replace(0,np.nan)
    h['close_pos']=(h.close-h.low)/rng; h['range_atr']=(h.high-h.low)/h.atr.replace(0,np.nan)
    h['upper_wick']=(h.high-h[['open','close']].max(axis=1))/rng; h['lower_wick']=(h[['open','close']].min(axis=1)-h.low)/rng
    bull=(h.close>h.open)&(h.body_atr>=0.55)&(h.close_pos>=0.78)&(h.upper_wick<=0.20)&(h.range_atr<=2.0)
    bear=(h.close<h.open)&(h.body_atr>=0.55)&(h.close_pos<=0.22)&(h.lower_wick<=0.20)&(h.range_atr<=2.0)
    return pd.DataFrame({'timestamp':_norm(h.timestamp+pd.Timedelta(rule)),f'{prefix}_bull_strict':bull.astype(bool),f'{prefix}_bear_strict':bear.astype(bool),f'{prefix}_body_atr':h.body_atr,f'{prefix}_close_pos':h.close_pos,f'{prefix}_range_atr':h.range_atr})

def enrich(df):
    x=df.copy(); x['timestamp']=_norm(x.timestamp); x=x.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    prev=x.close.shift(1); tr=pd.concat([(x.high-x.low),(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1); x['atr']=wilder(tr)
    for p in (20,50,200): x[f'ema{p}']=x.close.ewm(span=p,adjust=False).mean()
    d=x.close.diff(); gain=wilder(d.clip(lower=0)); loss=wilder(-d.clip(upper=0)); x['rsi']=100-(100/(1+gain/loss.replace(0,np.nan)))
    up=x.high.diff(); dn=-x.low.diff(); plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=x.index); minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=x.index)
    x['pdi']=100*wilder(plus)/x.atr.replace(0,np.nan); x['mdi']=100*wilder(minus)/x.atr.replace(0,np.nan)
    day=x.timestamp.dt.floor('D'); typ=(x.high+x.low+x.close)/3; vc=x.volume.groupby(day).cumsum(); x['vwap']=(typ*x.volume).groupby(day).cumsum()/vc.replace(0,np.nan)
    x['swing_h']=x.high.shift(1).rolling(20).max(); x['swing_l']=x.low.shift(1).rolling(20).min(); rng=(x.high-x.low).replace(0,np.nan)
    x['range_atr']=(x.high-x.low)/x.atr.replace(0,np.nan); x['move5_atr']=abs(x.close-x.close.shift(5))/x.atr.replace(0,np.nan); x['dist_ema20_atr']=abs(x.close-x.ema20)/x.atr.replace(0,np.nan); x['dist_res_atr']=(x.swing_h-x.close)/x.atr.replace(0,np.nan); x['dist_sup_atr']=(x.close-x.swing_l)/x.atr.replace(0,np.nan)
    base=x.set_index('timestamp')
    for rule,pfx in (('1h','h1'),('4h','h4')):
        h=base.resample(rule,label='right',closed='right').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna()
        for p in (21,50,200): h[f'ema{p}']=h.close.ewm(span=p,adjust=False).mean()
        h['trend_bull']=(h.ema21>h.ema50)&(h.ema50>h.ema200); h['trend_bear']=(h.ema21<h.ema50)&(h.ema50<h.ema200); h['slope21']=(h.ema21-h.ema21.shift(3))/h.close; h['bull']=h.close>h.ema21; h['bear']=h.close<h.ema21
        h=h.reset_index()[['timestamp','ema21','ema50','ema200','trend_bull','trend_bear','slope21','bull','bear']]
        h=h.rename(columns={'ema21':f'{pfx}_ema21','ema50':f'{pfx}_ema50','ema200':f'{pfx}_ema200','trend_bull':f'{pfx}_trend_bull','trend_bear':f'{pfx}_trend_bear','slope21':f'{pfx}_slope21','bull':f'{pfx}_bull','bear':f'{pfx}_bear'})
        x['timestamp']=_norm(x.timestamp); h['timestamp']=_norm(h.timestamp)
        x=pd.merge_asof(x.sort_values('timestamp'),h.sort_values('timestamp'),on='timestamp',direction='backward',allow_exact_matches=False)
    a4=_adx_4h_features(df); c2=_strict_candle_features(df)
    x['timestamp']=_norm(x.timestamp); a4['timestamp']=_norm(a4.timestamp); c2['timestamp']=_norm(c2.timestamp)
    x=pd.merge_asof(x.sort_values('timestamp'),a4.sort_values('timestamp'),on='timestamp',direction='backward',allow_exact_matches=False)
    x=pd.merge_asof(x.sort_values('timestamp'),c2.sort_values('timestamp'),on='timestamp',direction='backward',allow_exact_matches=False)
    return x

def signal_mask(x,core=CORE_NAME):
    if core!=CORE_NAME: raise ValueError(f'Unsupported core: {core}')
    bull=x.h1_trend_bull&x.h4_trend_bull; bear=x.h1_trend_bear&x.h4_trend_bear
    emaL=(x.close>x.ema20)&(x.ema20>x.ema50); emaS=(x.close<x.ema20)&(x.ema20<x.ema50)
    structureL=bull&emaL&(x.close>x.vwap)&(x.pdi>x.mdi)&x.rsi.between(46,64); structureS=bear&emaS&(x.close<x.vwap)&(x.mdi>x.pdi)&x.rsi.between(36,54)
    roomL=x.dist_res_atr>=0.85; roomS=x.dist_sup_atr>=0.85; noex=(x.move5_atr<=3.50)&(x.dist_ema20_atr<=1.60)&(x.range_atr<=2.25)
    adxL=(x.h4_adx_pct>=ADX_LONG_PCT)&(x.h4_adx_delta>=ADX_LONG_DELTA); adxS=(x.h4_adx_pct>=ADX_SHORT_PCT)&(x.h4_adx_delta>=ADX_SHORT_DELTA)
    return (structureL&adxL&roomL&noex&x.c2h_bull_strict).fillna(False),(structureS&adxS&roomS&noex&x.c2h_bear_strict).fillna(False)
