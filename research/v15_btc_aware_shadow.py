from __future__ import annotations
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from core_engine_v15 import COINS, CORE_NAME, RR, MAX_HOLD_BARS, enrich, signal_mask, trade_levels
from research_v15_compare import fetch_api, fetch_archive

START=pd.Timestamp("2026-09-14T00:00:00Z"); END=pd.Timestamp("2026-09-21T12:00:00Z")
WARMUP=pd.Timedelta(days=38)
OUT=Path("v15_btc_aware_shadow"); OUT.mkdir(exist_ok=True)
S=requests.Session(); S.mount("https://",HTTPAdapter(max_retries=Retry(total=4,backoff_factor=.3,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset(["GET"]))))
S.headers.update({"X-SOURCE-KEY":"BX-AI-SKILL","User-Agent":"SAM-EDGE-V15-BTC-SHADOW/1.0"})

def bx(symbol,interval,start,end):
    symbol=symbol.replace("/USDT:USDT","-USDT").replace("/","-").upper()
    step=900000 if interval=="15m" else 300000
    cur=int(start.timestamp()*1000); endms=int(end.timestamp()*1000); rows=[]; seen=set()
    while cur<=endms:
        p={"symbol":symbol,"interval":interval,"startTime":cur,"endTime":endms,"limit":1000,"timestamp":int(time.time()*1000)}
        dat=None
        for path in ["/openApi/swap/v3/quote/klines","/openApi/swap/v2/quote/klines"]:
            try:
                j=S.get("https://open-api.bingx.com"+path,params=p,timeout=20).json()
                if int(j.get("code",-1))==0: dat=j.get("data",[]); break
            except Exception: pass
        if dat is None: break
        page=[]
        for q in dat:
            try:
                if isinstance(q,dict): z=[int(q.get("openTime") or q.get("time") or q.get("timestamp")),float(q["open"]),float(q["high"]),float(q["low"]),float(q["close"])]
                else: z=[int(q[0]),float(q[1]),float(q[2]),float(q[3]),float(q[4])]
                if z[0] not in seen: seen.add(z[0]); page.append(z)
            except Exception: continue
        page.sort()
        if not page: break
        rows+=page; nxt=page[-1][0]+step
        if nxt<=cur: break
        cur=nxt
    return pd.DataFrame(rows,columns=["ms","open","high","low","close"]) .assign(timestamp=lambda z:pd.to_datetime(z.ms,unit="ms",utc=True)).drop(columns="ms") if rows else pd.DataFrame()

def stats(a):
    if len(a)==0:return {"trades":0,"wins":0,"losses":0,"wr_pct":0,"net_R":0,"pf":None,"expectancy_R":0,"max_dd_R":0}
    r=np.array([float(x["R"]) for x in a]); w=int((r>0).sum()); gp=r[r>0].sum(); gl=-r[r<0].sum(); eq=r.cumsum(); dd=eq-np.maximum.accumulate(eq)
    return {"trades":len(r),"wins":w,"losses":len(r)-w,"wr_pct":round(100*w/len(r),2),"net_R":round(float(r.sum()),2),"pf":round(float(gp/gl),3) if gl else None,"expectancy_R":round(float(r.mean()),4),"max_dd_R":round(float(dd.min()),2)}

def run_coin(coin,x,btc15):
    lm,sm=signal_mask(x,CORE_NAME); out=[]
    for i in sorted(set(np.flatnonzero(lm.to_numpy()).tolist()+np.flatnonzero(sm.to_numpy()).tolist())):
        ts=pd.Timestamp(x.timestamp.iloc[i])
        if ts<START or ts>END: continue
        side=1 if bool(lm.iloc[i]) else -1
        lv=trade_levels(x,i,side,"STRUCTURE")
        if lv is None: continue
        e,sl,tp,risk=lv
        ex=None; result=None
        for j in range(i+1,min(len(x),i+1+MAX_HOLD_BARS)):
            hs=float(x.high.iloc[j]); ls=float(x.low.iloc[j])
            hit_sl=ls<=sl if side==1 else hs>=sl
            hit_tp=hs>=tp if side==1 else ls<=tp
            if hit_sl: result,ex="SL",j; break
            if hit_tp: result,ex="TP",j; break
        if ex is None: continue
        prior=btc15[btc15.timestamp<=ts]
        if prior.empty: continue
        b=prior.iloc[-1]; bc=float(b.close)
        def ret(m):
            q=btc15[btc15.timestamp<=ts+pd.Timedelta(minutes=m)]
            return float(q.iloc[-1].close)/bc-1 if not q.empty else np.nan
        pre1=ret(-60); pre4=ret(-240)
        # Since ret() uses <= target, for negative horizons it returns the prior candle.
        btc1=ret(60); btc4=ret(240)
        out.append({"coin":coin,"signal_time":ts,"closed_at":pd.Timestamp(x.timestamp.iloc[ex]),"side":"LONG" if side==1 else "SHORT","R":RR if result=="TP" else -1.0,"btc_pre1h":pre1,"btc_pre4h":pre4,"btc_after1h":btc1,"btc_after4h":btc4})
    return out

def btc_rule(row,rule):
    # All features are available at entry. No post-entry information is used.
    p4=row["btc_pre4h"]; p1=row["btc_pre1h"]
    if rule=="BASE": return True
    if rule=="NO_LONG_BTC_DOWN_1H": return not(row["side"]=="LONG" and p1 < -0.005)
    if rule=="NO_LONG_BTC_DOWN_4H": return not(row["side"]=="LONG" and p4 < -0.005)
    if rule=="LONG_BTC_4H_NONNEG": return not(row["side"]=="LONG" and p4 < 0)
    if rule=="LONG_BTC_4H_ACCEL": return not(row["side"]=="LONG" and p4 < 0)
    if rule=="NO_LONG_BTC_DD_1H": return not(row["side"]=="LONG" and p1 < -0.003)
    return True

def main():
    btc=bx("BTC/USDT:USDT","15m",START-WARMUP,END)
    alltr=[]
    for coin in COINS:
        try:
            raw=fetch_api(coin,(START-WARMUP).to_pydatetime(),END.to_pydatetime())
            if len(raw)<1000: raw=fetch_archive(coin,(START-WARMUP).to_pydatetime(),END.to_pydatetime())
            x=enrich(raw); alltr+=run_coin(coin,x,btc)
        except Exception as e: print("ERR",coin,e)
    alltr=sorted(alltr,key=lambda z:z["signal_time"]); 
    pd.DataFrame(alltr).to_csv(OUT/"events.csv",index=False)
    rules=["BASE","NO_LONG_BTC_DOWN_1H","NO_LONG_BTC_DOWN_4H","LONG_BTC_4H_NONNEG","NO_LONG_BTC_DD_1H"]
    result={}
    for rule in rules:
        q=[x for x in alltr if btc_rule(x,rule)]
        result[rule]=stats(q)
    # Separate LONG/SHORT and BTC pre-entry buckets.
    def grp(fn):
        return stats([x for x in alltr if fn(x)])
    buckets={
      "LONG_pre4h<-0.5%":grp(lambda x:x["side"]=="LONG" and x["btc_pre4h"]<-.005),
      "LONG_pre4h[-0.5%,0)":grp(lambda x:x["side"]=="LONG" and -.005<=x["btc_pre4h"]<0),
      "LONG_pre4h>=0":grp(lambda x:x["side"]=="LONG" and x["btc_pre4h"]>=0),
      "LONG_pre1h<-0.5%":grp(lambda x:x["side"]=="LONG" and x["btc_pre1h"]<-.005),
      "LONG_pre1h>=-0.5%":grp(lambda x:x["side"]=="LONG" and x["btc_pre1h"]>=-.005),
    }
    (OUT/"summary.json").write_text(json.dumps({"source":"BingX BTC + historical coin data","period":[str(START),str(END)],"events":len(alltr),"rules":result,"btc_entry_buckets":buckets,"warning":"Research only; rules use only information available before entry and do not alter production."},indent=2,default=str))
    print(json.dumps({"events":len(alltr),"rules":result,"buckets":buckets},indent=2,default=str))
if __name__=="__main__": main()
