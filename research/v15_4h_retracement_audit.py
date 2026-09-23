from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JOURNAL=Path("v15_forward_test_master_journal.csv")
OUT=Path("v15_4h_retracement_audit"); OUT.mkdir(exist_ok=True)
BINGX="https://open-api.bingx.com"
S=requests.Session()
S.mount("https://",HTTPAdapter(max_retries=Retry(total=5,connect=5,read=3,backoff_factor=.4,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset(["GET"]))))
S.headers.update({"X-SOURCE-KEY":"BX-AI-SKILL","User-Agent":"SAM-EDGE-V15-4H-RETRACE-AUDIT/1.0"})

def klines(asset,start,end):
    symbol=asset.replace("/USDT:USDT","-USDT").replace("/","-").upper()
    cur=int(start.timestamp()*1000); endms=int(end.timestamp()*1000); step=14400000
    rows=[]; seen=set()
    while cur<=endms:
        p={"symbol":symbol,"interval":"4h","startTime":cur,"endTime":endms,"limit":1000,"timestamp":int(time.time()*1000)}
        payload=None; err=None
        for path in ("/openApi/swap/v3/quote/klines","/openApi/swap/v2/quote/klines"):
            try:
                r=S.get(BINGX+path,params=p,timeout=25); r.raise_for_status(); j=r.json()
                if int(j.get("code",-1))!=0: raise RuntimeError(str(j.get("msg")))
                payload=j.get("data",[]); break
            except Exception as e: err=e
        if payload is None: raise err
        page=[]
        for q in payload or []:
            try:
                if isinstance(q,(list,tuple)): z=[int(q[0]),float(q[1]),float(q[2]),float(q[3]),float(q[4]),float(q[5])]
                else: z=[int(q.get("openTime") or q.get("time") or q.get("timestamp")),float(q["open"]),float(q["high"]),float(q["low"]),float(q["close"]),float(q.get("volume",0))]
                if z[0] not in seen: seen.add(z[0]); page.append(z)
            except: pass
        page.sort()
        if not page: break
        rows+=page; nxt=page[-1][0]+step
        if nxt<=cur: break
        cur=nxt
    d=pd.DataFrame(rows,columns=["ms","open","high","low","close","volume"])
    if d.empty:return d
    d["timestamp"]=pd.to_datetime(d.ms,unit="ms",utc=True)
    return d.drop(columns="ms").sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def one(t,h):
    ts=pd.Timestamp(t.signal_time); side=str(t.side).upper(); entry=float(t.entry); sl=float(t.sl)
    # completed candles only: no lookahead
    c=h[h.timestamp+pd.Timedelta(hours=4)<=ts].copy()
    if len(c)<55:return {"status":"INSUFFICIENT"}
    c["ema20"]=ema(c.close,20); c["ema50"]=ema(c.close,50)
    b=c.iloc[-1]; w=c.tail(6)
    swing_hi=float(w.high.max()); swing_lo=float(w.low.min())
    atr=float((pd.concat([(c.high-c.low),(c.high-c.close.shift()).abs(),(c.low-c.close.shift()).abs()],axis=1).max(axis=1)).rolling(14).mean().iloc[-1])
    if side=="LONG":
        retr=(swing_hi-entry)/atr if atr else np.nan
        above20=entry>float(b.ema20); above50=entry>float(b.ema50)
        h4trend=float(b.ema20)>float(b.ema50)
        sl_to_swing=(sl-swing_lo)/atr if atr else np.nan
        close_from_hi=(swing_hi-float(b.close))/atr if atr else np.nan
    else:
        retr=(entry-swing_lo)/atr if atr else np.nan
        above20=entry<float(b.ema20); above50=entry<float(b.ema50)
        h4trend=float(b.ema20)<float(b.ema50)
        sl_to_swing=(swing_hi-sl)/atr if atr else np.nan
        close_from_hi=(float(b.close)-swing_lo)/atr if atr else np.nan
    return {"status":"OK","h4_last_open":b.timestamp.isoformat(),"h4_open":b.open,"h4_high":b.high,"h4_low":b.low,"h4_close":b.close,
      "ema20_4h":b.ema20,"ema50_4h":b.ema50,"h4_trend_aligned":h4trend,"entry_correct_side_ema20":above20,
      "entry_correct_side_ema50":above50,"atr4h":atr,"swing6_high":swing_hi,"swing6_low":swing_lo,
      "retracement_from_6bar_extreme_atr":retr,"last_close_from_6bar_extreme_atr":close_from_hi,
      "sl_vs_6bar_swing_atr":sl_to_swing,"last4h_bull":bool(b.close>b.open),"last4h_bear":bool(b.close<b.open)}

def stats(q):
    r=q.R.astype(float); gp=r[r>0].sum(); gl=-r[r<0].sum()
    return {"n":len(q),"wins":int((r>0).sum()),"losses":int((r<0).sum()),"wr":round(100*(r>0).mean(),2) if len(r) else 0,
      "netR":round(r.sum(),2),"pf":round(gp/gl,3) if gl else None}

def main():
    d=pd.read_csv(JOURNAL,parse_dates=["signal_time","closed_at"]).sort_values("trade_no")
    focus=d[(d.trade_no>=84)&(d.trade_no<=97)].copy()
    start=focus.signal_time.min()-pd.Timedelta(days=20); end=focus.signal_time.max()+pd.Timedelta(hours=4)
    data={}; errors={}
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs={ex.submit(klines,a,start,end):a for a in focus.coin.unique()}
        for f in as_completed(fs):
            a=fs[f]
            try:data[a]=f.result()
            except Exception as e:errors[a]=str(e)
    rows=[]
    for _,t in focus.iterrows():
        rec=t.to_dict(); h=data.get(t.coin)
        rec.update(one(t,h) if h is not None and not h.empty else {"status":"DATA_ERROR","error":errors.get(t.coin,"")})
        rows.append(rec)
    o=pd.DataFrame(rows); o.to_csv(OUT/"trades_84_97.csv",index=False)
    ok=o[o.status=="OK"].copy()
    summary=[{"group":"all_84_97",**stats(ok)}]
    if not ok.empty:
        for label,mask in [
          ("retracement_ge_0.5_ATR",ok.retracement_from_6bar_extreme_atr>=.5),
          ("retracement_ge_1.0_ATR",ok.retracement_from_6bar_extreme_atr>=1.0),
          ("entry_right_side_EMA20",ok.entry_correct_side_ema20==True),
          ("entry_wrong_side_EMA20",ok.entry_correct_side_ema20==False),
          ("H4_trend_aligned",ok.h4_trend_aligned==True),
          ("SL_inside_6bar_swing",ok.sl_vs_6bar_swing_atr>0),
          ("SL_beyond_6bar_swing",ok.sl_vs_6bar_swing_atr<=0)]:
            summary.append({"group":label,**stats(ok[mask])})
    pd.DataFrame(summary).to_csv(OUT/"summary.csv",index=False)
    print(pd.DataFrame(summary).to_string(index=False))
    cols=["trade_no","coin","R","retracement_from_6bar_extreme_atr","entry_correct_side_ema20","entry_correct_side_ema50","h4_trend_aligned","sl_vs_6bar_swing_atr"]
    print("\nDETAIL\n",ok[cols].to_string(index=False))
    if errors: print("errors",errors)
if __name__=="__main__":main()
