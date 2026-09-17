from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd

LEGACY_CORE_NAME = 'V15_ADX4H_CANDLE2H'
CORE_NAME = 'V15_EARLY_RECLAIM_V1'
RR = 1.25
SWING_LOOKBACK = 20
MAX_HOLD_BARS = 96
COINS = ['ADA','AVAX','BNB','BTC','CRV','DOGE','ETH','FET','LINK','SOL','SUI','UNI','XRP']

# Keep the proven HTF regime strength requirements; change only the trigger timing.
ADX_LONG_PCT = 0.80
ADX_LONG_DELTA = 0.75
ADX_SHORT_PCT = 0.85
ADX_SHORT_DELTA = 0.90

# Early-entry guardrails: they explicitly reject already-expanded candles.
EARLY_MOVE5_MAX_ATR = 2.50
EARLY_DIST_EMA_MAX_ATR = 1.20
EARLY_ROOM_MIN_ATR = 0.85
EARLY_PULLBACK_LOOKBACK = 6
EARLY_PULLBACK_MAX_ATR = 0.60
EARLY_RECLAIM_BUFFER_ATR = 0.05
EARLY_VOLUME_RATIO_MIN = 1.00
EARLY_VOLUME_SLOPE_MIN = -0.20

# Structure-aware stop: tighter than the old 20-bar swing + 1 ATR stop, but bounded.
STRUCTURE_STOP_LOOKBACK = 8
STRUCTURE_STOP_ATR_BUFFER = 0.65
STRUCTURE_STOP_MIN_ATR = 0.80
STRUCTURE_STOP_MAX_ATR = 2.50

@dataclass(frozen=True)
class Candidate:
    core: str
    label: str
    rr: float


def wilder(s, n=14):
    return s.ewm(alpha=1/n, adjust=False).mean()


def _normalize_timestamp(s):
    return pd.to_datetime(s, utc=True).astype('datetime64[ns, UTC]')


def _ohlcv_resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = df.copy()
    x['timestamp'] = _normalize_timestamp(x['timestamp'])
    b = x.set_index('timestamp')
    out = b.resample(rule, label='left', closed='left').agg(
        open=('open','first'), high=('high','max'), low=('low','min'),
        close=('close','last'), volume=('volume','sum')
    ).dropna().reset_index()
    out['timestamp'] = _normalize_timestamp(out['timestamp'])
    return out


def _atr_ohlc(h: pd.DataFrame, n=14):
    prev = h.close.shift(1)
    tr = pd.concat([(h.high-h.low), (h.high-prev).abs(), (h.low-prev).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def _strict_candle_features(df: pd.DataFrame, rule: str, prefix: str) -> pd.DataFrame:
    h = _ohlcv_resample(df, rule)
    h['atr'] = _atr_ohlc(h, 14)
    rng = (h.high-h.low).replace(0, np.nan)
    h['body_atr'] = (h.close-h.open).abs()/h.atr.replace(0,np.nan)
    h['close_pos'] = (h.close-h.low)/rng
    h['range_atr'] = (h.high-h.low)/h.atr.replace(0,np.nan)
    h['upper_wick'] = (h.high-h[['open','close']].max(axis=1))/rng
    h['lower_wick'] = (h[['open','close']].min(axis=1)-h.low)/rng
    bull = ((h.close>h.open)&(h.body_atr>=0.55)&(h.close_pos>=0.78)&
            (h.upper_wick<=0.20)&(h.range_atr<=2.0))
    bear = ((h.close<h.open)&(h.body_atr>=0.55)&(h.close_pos<=0.22)&
            (h.lower_wick<=0.20)&(h.range_atr<=2.0))
    delta = pd.Timedelta(rule)
    out = pd.DataFrame({
        'timestamp': _normalize_timestamp(h.timestamp + delta),
        f'{prefix}_bull_strict': bull.astype(bool),
        f'{prefix}_bear_strict': bear.astype(bool),
        f'{prefix}_body_atr': h.body_atr,
        f'{prefix}_close_pos': h.close_pos,
        f'{prefix}_range_atr': h.range_atr,
    })
    return out


def _adx_4h_features(df: pd.DataFrame) -> pd.DataFrame:
    h = _ohlcv_resample(df, '4h')
    h['atr'] = _atr_ohlc(h, 14)
    up = h.high.diff(); dn = -h.low.diff()
    plus = pd.Series(np.where((up>dn)&(up>0), up, 0.0), index=h.index)
    minus = pd.Series(np.where((dn>up)&(dn>0), dn, 0.0), index=h.index)
    h['pdi'] = 100*wilder(plus)/h.atr.replace(0,np.nan)
    h['mdi'] = 100*wilder(minus)/h.atr.replace(0,np.nan)
    dx = 100*(h.pdi-h.mdi).abs()/(h.pdi+h.mdi).replace(0,np.nan)
    h['adx'] = wilder(dx)
    h['adx_pct'] = h.adx.rolling(200, min_periods=100).rank(pct=True)
    h['adx_delta'] = h.adx-h.adx.shift(3)
    h['adx_accel'] = h.adx_delta-h.adx_delta.shift(3)
    h['timestamp'] = _normalize_timestamp(h.timestamp + pd.Timedelta('4h'))
    return h[['timestamp','adx','adx_pct','adx_delta','adx_accel']]


def load_coin(root: Path, coin: str) -> pd.DataFrame:
    files = sorted((root/coin).glob('*.zip'))
    if not files:
        raise FileNotFoundError(f'Missing raw data for {coin}: expected {root/coin}/*.zip')
    fs=[]
    for p in files:
        with zipfile.ZipFile(p) as z:
            cs=[n for n in z.namelist() if n.lower().endswith('.csv')]
            if not cs: continue
            q=pd.read_csv(z.open(cs[0]))
        cmap={c.lower():c for c in q.columns}
        need=['open_time','open','high','low','close','volume']
        miss=[c for c in need if c not in cmap]
        if miss: raise ValueError(f'{p}: missing columns {miss}')
        q=q[[cmap[c] for c in need]].copy(); q.columns=need
        q['timestamp']=_normalize_timestamp(pd.to_datetime(q.open_time,unit='ms',utc=True))
        fs.append(q.drop(columns='open_time'))
    x=pd.concat(fs,ignore_index=True).drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
    for c in ['open','high','low','close','volume']:
        x[c]=pd.to_numeric(x[c],errors='coerce')
    x['timestamp'] = _normalize_timestamp(x['timestamp'])
    return x.dropna().reset_index(drop=True)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy()
    x['timestamp']=_normalize_timestamp(x['timestamp'])
    x=x.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)

    prev=x.close.shift(1)
    tr=pd.concat([(x.high-x.low),(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1)
    x['atr']=wilder(tr,14)
    for p in [20,50,200]:
        x[f'ema{p}']=x.close.ewm(span=p,adjust=False).mean()

    d=x.close.diff(); gain=wilder(d.clip(lower=0)); loss=wilder(-d.clip(upper=0))
    x['rsi']=100-(100/(1+gain/loss.replace(0,np.nan)))

    x['volr']=x.volume/x.volume.rolling(20).mean()
    x['vol_slope']=x.volr-x.volr.shift(2)

    up=x.high.diff(); dn=-x.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=x.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=x.index)
    x['pdi']=100*wilder(plus)/x.atr.replace(0,np.nan)
    x['mdi']=100*wilder(minus)/x.atr.replace(0,np.nan)
    dx=100*(x.pdi-x.mdi).abs()/(x.pdi+x.mdi).replace(0,np.nan)
    x['adx']=wilder(dx)
    x['adx_pct']=x.adx.rolling(200,min_periods=100).rank(pct=True)
    x['adx_delta']=x.adx-x.adx.shift(3)
    x['adx_accel']=x.adx_delta-x.adx_delta.shift(3)

    day=x.timestamp.dt.floor('D'); typ=(x.high+x.low+x.close)/3
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
    x['break_hi']=x.close>x.swing_h; x['break_lo']=x.close<x.swing_l
    x['reclaim_hi']=(x.close>x.swing_h.shift(1))&(x.low<=x.swing_h.shift(1))
    x['reclaim_lo']=(x.close<x.swing_l.shift(1))&(x.high>=x.swing_l.shift(1))

    base=x.set_index('timestamp')

    for rule,pfx in [('1h','h1'),('4h','h4')]:
        h=base.resample(rule,label='right',closed='right').agg(
            open=('open','first'), high=('high','max'), low=('low','min'),
            close=('close','last'), volume=('volume','sum')
        ).dropna()

        for p in [21,50,200]:
            h[f'ema{p}']=h.close.ewm(span=p,adjust=False).mean()

        h['trend_bull']=(h.ema21>h.ema50)&(h.ema50>h.ema200)
        h['trend_bear']=(h.ema21<h.ema50)&(h.ema50<h.ema200)
        h['slope21']=(h.ema21-h.ema21.shift(3))/h.close
        h['bull']=h.close>h.ema21
        h['bear']=h.close<h.ema21

        h=h.reset_index()[[
            'timestamp','ema21','ema50','ema200',
            'trend_bull','trend_bear','slope21','bull','bear'
        ]]

        h=h.rename(columns={
            'ema21':f'{pfx}_ema21',
            'ema50':f'{pfx}_ema50',
            'ema200':f'{pfx}_ema200',
            'trend_bull':f'{pfx}_trend_bull',
            'trend_bear':f'{pfx}_trend_bear',
            'slope21':f'{pfx}_slope21',
            'bull':f'{pfx}_bull',
            'bear':f'{pfx}_bear'
        })

        x['timestamp']=_normalize_timestamp(x['timestamp'])
        h['timestamp']=_normalize_timestamp(h['timestamp'])

        x=pd.merge_asof(
            x.sort_values('timestamp'),
            h.sort_values('timestamp'),
            on='timestamp',
            direction='backward',
            allow_exact_matches=False
        )

    a4=_adx_4h_features(df).rename(columns={
        'adx':'h4_adx',
        'adx_pct':'h4_adx_pct',
        'adx_delta':'h4_adx_delta',
        'adx_accel':'h4_adx_accel'
    })

    c2=_strict_candle_features(df,'2h','c2h')

    x['timestamp']=_normalize_timestamp(x['timestamp'])
    a4['timestamp']=_normalize_timestamp(a4['timestamp'])
    c2['timestamp']=_normalize_timestamp(c2['timestamp'])

    x=pd.merge_asof(
        x.sort_values('timestamp'),
        a4.sort_values('timestamp'),
        on='timestamp',
        direction='backward',
        allow_exact_matches=False
    )

    x=pd.merge_asof(
        x.sort_values('timestamp'),
        c2.sort_values('timestamp'),
        on='timestamp',
        direction='backward',
        allow_exact_matches=False
    )

    return x


def signal_mask(x: pd.DataFrame, core: str = CORE_NAME):
    if core == LEGACY_CORE_NAME:
        r=x
        bull=r.h1_trend_bull&r.h4_trend_bull
        bear=r.h1_trend_bear&r.h4_trend_bear
        emaL=(r.close>r.ema20)&(r.ema20>r.ema50)
        emaS=(r.close<r.ema20)&(r.ema20<r.ema50)
        structureL=bull&emaL&(r.close>r.vwap)&(r.pdi>r.mdi)&r.rsi.between(46,64)
        structureS=bear&emaS&(r.close<r.vwap)&(r.mdi>r.pdi)&r.rsi.between(36,54)
        roomL=r.dist_res_atr>=0.85
        roomS=r.dist_sup_atr>=0.85
        noex=(r.move5_atr<=3.50)&(r.dist_ema20_atr<=1.60)&(r.range_atr<=2.25)
        adxL=(r.h4_adx_pct>=ADX_LONG_PCT)&(r.h4_adx_delta>=ADX_LONG_DELTA)
        adxS=(r.h4_adx_pct>=ADX_SHORT_PCT)&(r.h4_adx_delta>=ADX_SHORT_DELTA)
        return (structureL&adxL&roomL&noex&r.c2h_bull_strict).fillna(False), (structureS&adxS&roomS&noex&r.c2h_bear_strict).fillna(False)
    if core != CORE_NAME:
        raise ValueError(f'Unsupported core: {core}')

    r=x
    bull=r.h1_trend_bull&r.h4_trend_bull
    bear=r.h1_trend_bear&r.h4_trend_bear
    emaL=(r.close>r.ema20)&(r.ema20>r.ema50)
    emaS=(r.close<r.ema20)&(r.ema20<r.ema50)
    structureL=bull&emaL&(r.close>r.vwap)&(r.pdi>r.mdi)&r.rsi.between(46,64)
    structureS=bear&emaS&(r.close<r.vwap)&(r.mdi>r.pdi)&r.rsi.between(36,54)
    roomL=r.dist_res_atr>=EARLY_ROOM_MIN_ATR
    roomS=r.dist_sup_atr>=EARLY_ROOM_MIN_ATR
    no_chase=(r.move5_atr<=EARLY_MOVE5_MAX_ATR)&(r.dist_ema20_atr<=EARLY_DIST_EMA_MAX_ATR)&(r.range_atr<=2.25)
    vol_ok=(r.volr>=EARLY_VOLUME_RATIO_MIN)&(r.vol_slope>=EARLY_VOLUME_SLOPE_MIN)
    touchL=r.low.rolling(EARLY_PULLBACK_LOOKBACK).min() <= (r.ema20+EARLY_PULLBACK_MAX_ATR*r.atr)
    touchS=r.high.rolling(EARLY_PULLBACK_LOOKBACK).max() >= (r.ema20-EARLY_PULLBACK_MAX_ATR*r.atr)
    reclaimL=(r.close > r.high.shift(1)+EARLY_RECLAIM_BUFFER_ATR*r.atr)&(r.close>r.ema20)&(r.close_pos>=0.60)&(r.body_atr>=0.20)
    reclaimS=(r.close < r.low.shift(1)-EARLY_RECLAIM_BUFFER_ATR*r.atr)&(r.close<r.ema20)&(r.close_pos<=0.40)&(r.body_atr>=0.20)
    long=(structureL&roomL&no_chase&vol_ok&touchL&reclaimL)
    short=(structureS&roomS&no_chase&vol_ok&touchS&reclaimS)
    return long.fillna(False), short.fillna(False)


def trade_levels(x: pd.DataFrame, i: int, side: int, stop_style: str = 'STRUCTURE'):
    entry=float(x.close.iloc[i]); atr=float(x.atr.iloc[i])
    if not np.isfinite(atr) or atr<=0:
        return None
    if stop_style.upper() == 'LEGACY':
        if side==1:
            sl=float(x.low.iloc[max(0,i-SWING_LOOKBACK):i].min())-atr
        else:
            sl=float(x.high.iloc[max(0,i-SWING_LOOKBACK):i].max())+atr
    else:
        if side==1:
            anchor=float(x.low.iloc[max(0,i-STRUCTURE_STOP_LOOKBACK):i].min())
            sl=anchor-STRUCTURE_STOP_ATR_BUFFER*atr
        else:
            anchor=float(x.high.iloc[max(0,i-STRUCTURE_STOP_LOOKBACK):i].max())
            sl=anchor+STRUCTURE_STOP_ATR_BUFFER*atr
        risk=entry-sl if side==1 else sl-entry
        if risk < STRUCTURE_STOP_MIN_ATR*atr:
            sl=entry-STRUCTURE_STOP_MIN_ATR*atr if side==1 else entry+STRUCTURE_STOP_MIN_ATR*atr
        elif risk > STRUCTURE_STOP_MAX_ATR*atr:
            sl=entry-STRUCTURE_STOP_MAX_ATR*atr if side==1 else entry+STRUCTURE_STOP_MAX_ATR*atr
    risk=entry-sl if side==1 else sl-entry
    if not np.isfinite(risk) or risk<=0:
        return None
    tp=entry+RR*risk if side==1 else entry-RR*risk
    return entry,float(sl),float(tp),float(risk)


def backtest_core(x, core=CORE_NAME, rr=RR, stop_style='STRUCTURE'):
    long,short=signal_mask(x,core)
    idx=sorted([(int(i),1) for i in np.flatnonzero(long.to_numpy()) if i>=250]+[(int(i),-1) for i in np.flatnonzero(short.to_numpy()) if i>=250])
    H,L,T,C=x.high.to_numpy(),x.low.to_numpy(),x.timestamp.to_numpy(),x.close.to_numpy()
    out=[]; last=-1
    for i,side in idx:
        if i<=last: continue
        levels=trade_levels(x,i,side,stop_style)
        if levels is None: continue
        e,sl,tp,risk=levels
        ex=None; result=None
        for j in range(i+1,min(len(x),i+1+MAX_HOLD_BARS)):
            hit_sl=L[j]<=sl if side==1 else H[j]>=sl
            hit_tp=H[j]>=tp if side==1 else L[j]<=tp
            if hit_sl and hit_tp: result='SL'; ex=j; break
            if hit_sl: result='SL'; ex=j; break
            if hit_tp: result='TP'; ex=j; break
        if ex is None: continue
        out.append({'entry_time':pd.Timestamp(T[i]),'exit_time':pd.Timestamp(T[ex]),'side':'LONG' if side==1 else 'SHORT',
                    'entry':e,'sl':sl,'tp':tp,'result':result,'R':(-1.0 if result=='SL' else rr),'core':core,'rr':rr})
        last=ex
    return pd.DataFrame(out)


def latest_signal(x: pd.DataFrame):
    i=len(x)-2
    if i < 250: return None
    lm,sm=signal_mask(x,CORE_NAME)
    side='LONG' if bool(lm.iloc[i]) else ('SHORT' if bool(sm.iloc[i]) else None)
    if side is None: return None
    levels=trade_levels(x,i,1 if side=='LONG' else -1,'STRUCTURE')
    if levels is None: return None
    entry,sl,tp,risk=levels
    return {'side':side,'timestamp':x.timestamp.iloc[i].isoformat(),'entry':entry,'sl':sl,'tp':tp,
            'atr':float(x.atr.iloc[i]),'risk_atr':risk/float(x.atr.iloc[i]),
            'adx4h_pct':float(x.h4_adx_pct.iloc[i]),'adx4h_delta':float(x.h4_adx_delta.iloc[i]),
            'move5_atr':float(x.move5_atr.iloc[i]),'dist_ema20_atr':float(x.dist_ema20_atr.iloc[i]),
            'volume_ratio':float(x.volr.iloc[i])}
