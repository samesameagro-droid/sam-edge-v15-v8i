from __future__ import annotations
import time, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import ccxt, numpy as np, pandas as pd
from core_engine_v15 import CORE_NAME, enrich, signal_mask, trade_levels

JOURNAL = Path("paper_v15_trading_journal.csv")
EXCHANGE = ccxt.bingx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
TIMEFRAME = "15m"
LIMIT = 1000

def fetch_history(symbol, start, end):
    rows=[]; since=int(start.timestamp()*1000); end_ms=int(end.timestamp()*1000)
    while since < end_ms:
        batch=EXCHANGE.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=LIMIT)
        if not batch: break
        rows.extend(batch)
        last=int(batch[-1][0])
        nxt=last+15*60*1000
        if nxt<=since: break
        since=nxt
        if len(batch)<LIMIT: break
        time.sleep(0.12)
    if not rows: return pd.DataFrame()
    x=pd.DataFrame(rows,columns=["timestamp","open","high","low","close","volume"])
    x["timestamp"]=pd.to_datetime(x.timestamp,unit="ms",utc=True)
    return x.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

def metrics(rows):
    if not rows: return {"trades":0,"wins":0,"losses":0,"wr":0,"net_R":0,"pf":0,"expectancy":0,"max_dd_R":0}
    r=np.array([float(x["R"]) for x in rows])
    w=int((r>0).sum()); l=int((r<0).sum())
    gp=float(r[r>0].sum()) if w else 0; gl=float(-r[r<0].sum()) if l else 0
    eq=np.cumsum(r); dd=eq-np.maximum.accumulate(eq)
    return {"trades":len(r),"wins":w,"losses":l,"wr":100*w/len(r),"net_R":float(r.sum()),"pf":gp/gl if gl else float("inf"),"expectancy":float(r.mean()),"max_dd_R":float(dd.min())}

def main():
    if not JOURNAL.exists(): raise SystemExit("journal missing")
    j=pd.read_csv(JOURNAL)
    j=j[j.status.astype(str).str.upper().eq("CLOSED")].copy()
    j["signal_time"]=pd.to_datetime(j.signal_time,utc=True)
    j["closed_at"]=pd.to_datetime(j.closed_at,utc=True)
    j=j.sort_values("signal_time").reset_index(drop=True)
    if len(j)!=100: raise SystemExit(f"expected exactly 100 closed journal rows, got {len(j)}")
    start=j.signal_time.min()-timedelta(days=55)
    end=j.closed_at.max()+timedelta(days=2)
    cache={}; rows=[]
    for sym in sorted(j.coin.unique()):
        print(f"FETCH {sym}")
        try:
            raw=fetch_history(sym,start,end)
            if len(raw)<3300: raise RuntimeError(f"only {len(raw)} candles")
            cache[sym]=enrich(raw)
            print(f"  candles={len(raw)} range={raw.timestamp.iloc[0]}..{raw.timestamp.iloc[-1]}")
        except Exception as e:
            print(f"  ERROR {type(e).__name__}: {e}")
    variants=["V15_BASELINE","V15.1-A_FRESH_PULLBACK","V15.1-B_FRESH_PLUS_ADX_DETERIORATION","V15.1-C_FRESH_PLUS_VOL120","V15.1-D_FRESH_PLUS_EMA080","V15.1-E_COMBINATION"]
    kept={v:[] for v in variants}; detail=[]
    for n,tr in j.iterrows():
        x=cache.get(tr.coin)
        if x is None: continue
        ts=tr.signal_time
        ix=x.index[x.timestamp==ts]
        if len(ix)==0:
            # tolerate exact candle alignment by nearest timestamp within 1 minute
            z=(x.timestamp-ts).abs().dt.total_seconds()
            i=int(z.idxmin()) if len(z) else -1
            if i<0 or z.iloc[i]>60: continue
        else: i=int(ix[0])
        lm,sm=signal_mask(x,CORE_NAME)
        base_side=bool(lm.iloc[i]) or bool(sm.iloc[i])
        side=tr.side
        exact_side=bool(lm.iloc[i]) if side=="LONG" else bool(sm.iloc[i])
        # Fresh pullback: a touch of EMA20 zone must occur on one of the two
        # completed candles immediately preceding the reclaim/entry candle.
        touch_age=None
        if side=="LONG":
            touch=((x.low <= x.ema20+0.60*x.atr)).fillna(False)
        else:
            touch=((x.high >= x.ema20-0.60*x.atr)).fillna(False)
        for age in (1,2):
            k=i-age
            if k>=0 and bool(touch.iloc[k]):
                touch_age=age; break
        fresh=touch_age is not None
        adx_pct=float(x.h4_adx_pct.iloc[i]); adx_delta=float(x.h4_adx_delta.iloc[i])
        adx_ok=not (adx_pct>=0.90 and adx_delta<0)
        vol_ok=float(x.volr.iloc[i])>=1.20
        ema_ok=float(x.dist_ema20_atr.iloc[i])<=0.80
        exact_ok=base_side and exact_side
        flags={
          "V15_BASELINE": exact_ok,
          "V15.1-A_FRESH_PULLBACK": exact_ok and fresh,
          "V15.1-B_FRESH_PLUS_ADX_DETERIORATION": exact_ok and fresh and adx_ok,
          "V15.1-C_FRESH_PLUS_VOL120": exact_ok and fresh and vol_ok,
          "V15.1-D_FRESH_PLUS_EMA080": exact_ok and fresh and ema_ok,
          "V15.1-E_COMBINATION": exact_ok and fresh and adx_ok and vol_ok and ema_ok,
        }
        rec={"trade_no":n+1,"coin":tr.coin,"side":side,"signal_time":ts.isoformat(),"actual_result":tr.result,
             "R":float(tr.R),"touch_age":touch_age,"adx_pct":adx_pct,"adx_delta":adx_delta,
             "volr":float(x.volr.iloc[i]),"ema_dist":float(x.dist_ema20_atr.iloc[i]),"entry_score":float(tr.entry_score)}
        for v,ok in flags.items():
            rec[v+"_kept"]=ok
            if ok: kept[v].append({"R":float(tr.R),"result":tr.result})
        detail.append(rec)
    summary=[]
    base_set=set(range(1,101))
    for v in variants:
        a=kept[v]
        m=metrics(a)
        filtered=100-len(a)
        avoided_losses=sum(1 for d in detail if not d.get(v+"_kept",False) and d["actual_result"]=="SL")
        removed_wins=sum(1 for d in detail if not d.get(v+"_kept",False) and d["actual_result"]=="TP")
        m.update({"variant":v,"filtered_out":filtered,"losses_avoided_from_original_100":avoided_losses,
                  "wins_removed_from_original_100":removed_wins,
                  "losses_remaining_from_original_100":sum(1 for z in a if z["result"]=="SL")})
        summary.append(m)
    pd.DataFrame(summary).to_csv("v15_1_100trade_variant_summary.csv",index=False)
    pd.DataFrame(detail).to_csv("v15_1_100trade_variant_detail.csv",index=False)
    report={"journal_rows":len(j),"symbols_loaded":len(cache),"symbols_total":int(j.coin.nunique()),
             "variants":summary,
             "definition":{"fresh_pullback":"EMA20 touch zone on immediately preceding 1-2 completed 15m candles","adx_deterioration":"reject when 4H ADX percentile >= 0.90 and ADX delta < 0","volume":"volr >= 1.20","ema_distance":"dist_ema20_atr <= 0.80","E":"A+B+C+D"}}
    Path("v15_1_100trade_variant_report.json").write_text(json.dumps(report,indent=2,default=str))
    print("\n"+pd.DataFrame(summary).to_string(index=False,float_format=lambda z:f"{z:.3f}"))
    print(f"Loaded {len(cache)}/{j.coin.nunique()} symbols. Detail rows={len(detail)}")

if __name__=="__main__": main()
