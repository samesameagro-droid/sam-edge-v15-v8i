from __future__ import annotations
import hashlib,hmac,json,math,os,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import urlencode
import numpy as np,pandas as pd,requests
from core_engine_v15 import CORE_NAME,RR,enrich,signal_mask,trade_levels
BASE_URL="https://open-api-vst.bingx.com"
API_KEY=os.getenv("BINGX_API_KEY","").strip(); SECRET_KEY=os.getenv("BINGX_SECRET_KEY","").strip()
EXECUTION_ENABLED=os.getenv("SAM_EDGE_EXECUTION_ENABLED","0")=="1"
HISTORY=int(os.getenv("HISTORY_15M","3300")); MIN_VOLUME=float(os.getenv("MIN_VOLUME_USDT","10000000"))
UNIVERSE_LIMIT=int(os.getenv("UNIVERSE_LIMIT","50")); RISK_PCT=float(os.getenv("RISK_PCT","0.02"))
LEVERAGE=int(os.getenv("LEVERAGE","5")); MAX_NEW_TRADES=int(os.getenv("MAX_NEW_TRADES","1"))
STATE=Path("bingx_v2_executor_state.json")
S=requests.Session(); S.headers.update({"X-SOURCE-KEY":"BX-AI-SKILL","User-Agent":"SAM-EDGE-PRECISION-V2/1.0"})
def api(path,params=None,method="GET"):
    q=dict(params or {}); q["timestamp"]=int(time.time()*1000); q["recvWindow"]=10000
    query=urlencode(sorted(q.items()),doseq=True); sig=hmac.new(SECRET_KEY.encode(),query.encode(),hashlib.sha256).hexdigest()
    r=S.request(method,BASE_URL+path+"?"+query+"&signature="+sig,headers={"X-BX-APIKEY":API_KEY,"X-SOURCE-KEY":"BX-AI-SKILL"},timeout=20)
    r.raise_for_status(); p=r.json()
    if p.get("code")!=0: raise RuntimeError(f"BingX {path}: {p.get('code')} {p.get('msg')}")
    return p.get("data")
def equity():
    d=api("/openApi/swap/v2/user/balance"); rows=d if isinstance(d,list) else [d]
    for x in rows:
        if isinstance(x,dict) and str(x.get("asset","")).upper()=="USDT":
            for k in ("equity","balance","availableMargin"):
                if x.get(k) is not None:return float(x[k])
    raise RuntimeError("USDT equity not found")
def positions():
    d=api("/openApi/swap/v2/user/positions"); rows=d if isinstance(d,list) else [d]; out=[]
    for p in rows:
        if isinstance(p,dict):
            try:a=abs(float(p.get("positionAmt") or p.get("positionSize") or 0))
            except:a=0
            if a>0:out.append(p)
    return out
def klines(symbol):
    n=HISTORY; step=900000; cur=int(time.time()*1000)-n*step; out=[]
    while len(out)<n:
        lim=min(1000,n-len(out)); d=api("/openApi/swap/v3/quote/klines",{"symbol":symbol,"interval":"15m","startTime":cur,"limit":lim})
        if not d:d=api("/openApi/swap/v2/quote/klines",{"symbol":symbol,"interval":"15m","startTime":cur,"limit":lim})
        if not d:break
        page=[]
        for r in d:
            try:
                if isinstance(r,(list,tuple)):page.append([int(r[0]),*map(float,r[1:6])])
                else:page.append([int(r.get("openTime") or r.get("time") or r.get("timestamp")),float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"]),float(r["volume"])])
            except:pass
        page.sort()
        if not page:break
        have={r[0] for r in out}; out.extend(r for r in page if r[0] not in have); cur=page[-1][0]+step
        if len(page)<lim:break
    out=sorted({r[0]:r for r in out}.values())[-n:]
    df=pd.DataFrame(out,columns=["timestamp","open","high","low","close","volume"]); df["timestamp"]=pd.to_datetime(df["timestamp"],unit="ms",utc=True); return df
def analyze(symbol):
    x=enrich(klines(symbol))
    if len(x)<800:return None
    i=len(x)-2; lm,sm=signal_mask(x,CORE_NAME)
    side="LONG" if bool(lm.iloc[i]) else ("SHORT" if bool(sm.iloc[i]) else None)
    if not side:return None
    if float(x.volr.iloc[i])<1.20 or float(x.dist_ema20_atr.iloc[i])>0.80:return None
    fresh=False
    for j in (i-1,i-2):
        if j>=0:fresh |= bool(x.low.iloc[j]<=x.ema20.iloc[j]+.60*x.atr.iloc[j]) if side=="LONG" else bool(x.high.iloc[j]>=x.ema20.iloc[j]-.60*x.atr.iloc[j])
    if not fresh or bool(x.h4_adx_pct.iloc[i]>=.90 and x.h4_adx_delta.iloc[i]<0):return None
    lev=trade_levels(x,i,1 if side=="LONG" else -1,"STRUCTURE")
    if lev is None:return None
    e,sl,tp,r=lev; ap=float(x.h4_adx_pct.iloc[i]); ad=float(x.h4_adx_delta.iloc[i]); room=float(x.dist_res_atr.iloc[i] if side=="LONG" else x.dist_sup_atr.iloc[i]); loc=float(x.dist_ema20_atr.iloc[i]); vol=float(x.volr.iloc[i]); trig=float(x.body_atr.iloc[i])
    score=100*(.30*((ap-(.80 if side=="LONG" else .85))/.20)+.20*((ad-(.75 if side=="LONG" else .90))/3)+.20*min(room/2,1)+.15*max(0,1-loc/1.2)+.10*min(vol/2,1)+.05*min(trig/.8,1))
    if not np.isfinite(score) or score>=65:return None
    return {"symbol":symbol,"side":side,"timestamp":x.timestamp.iloc[i].isoformat(),"entry":e,"sl":sl,"tp":tp,"risk_distance":r,"score":float(score)}
def floor(v,d):
    p=10**int(d); return math.floor(float(v)*p+1e-12)/p
def order(path,params):return api(path,params,"POST")
def main():
    if not API_KEY or not SECRET_KEY:raise RuntimeError("BingX GitHub Secrets belum tersedia")
    eq=equity(); ps=positions(); existing={str(p.get("symbol","")).upper() for p in ps}
    print(f"SAM EDGE V2 | VST | equity={eq:.6f} | EXECUTION={EXECUTION_ENABLED} | risk={RISK_PCT:.2%} | lev={LEVERAGE}x | RR={RR}")
    cs=api("/openApi/swap/v2/quote/contracts"); cs={str(c["symbol"]).upper():c for c in cs if str(c.get("symbol","")).upper().endswith("-USDT")}
    td=api("/openApi/swap/v2/quote/ticker"); td=td if isinstance(td,list) else [td]
    universe=[s for s,v in sorted([(str(t["symbol"]).upper(),float(t.get("quoteVolume") or 0)) for t in td if str(t.get("symbol","")).upper().endswith("-USDT")],key=lambda z:z[1],reverse=True) if v>=MIN_VOLUME and s in cs and s not in existing][:UNIVERSE_LIMIT]
    cand=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fs={pool.submit(analyze,s):s for s in universe}
        for f in as_completed(fs):
            try:
                c=f.result()
                if c:cand.append(c);print(f"CANDIDATE {c['symbol']} {c['side']} score={c['score']:.2f} entry={c['entry']:.8g} SL={c['sl']:.8g} TP={c['tp']:.8g}")
            except Exception as e:print(f"SCAN ERROR {fs[f]} | {type(e).__name__}: {e}")
    cand.sort(key=lambda z:z["score"]); print(f"SCAN COMPLETE | universe={len(universe)} | candidates={len(cand)}")
    for c in cand[:MAX_NEW_TRADES]:
        if any(str(p.get("symbol","")).upper()==c["symbol"] for p in positions()):continue
        m=cs[c["symbol"]]; qp=int(m.get("quantityPrecision") or 0); pp=int(m.get("pricePrecision") or 0)
        risk_cash=eq*RISK_PCT; qty=floor(risk_cash/abs(c["entry"]-c["sl"]),qp)
        minq=float(m.get("tradeMinQuantity") or m.get("minQty") or 0); minn=float(m.get("tradeMinUSDT") or m.get("minNotional") or 2)
        if qty<=0 or qty<minq or qty*c["entry"]<minn:print(f"SKIP SIZE {c['symbol']} qty={qty}");continue
        sl=floor(c["sl"],pp);tp=floor(c["tp"],pp)
        print(f"PLAN {c['symbol']} {c['side']} risk_cash={risk_cash:.6f} qty={qty} notional={qty*c['entry']:.6f} margin~={(qty*c['entry'])/LEVERAGE:.6f}")
        if not EXECUTION_ENABLED:continue
        order("/openApi/swap/v2/trade/leverage",{"symbol":c["symbol"],"side":c["side"],"leverage":LEVERAGE})
        order("/openApi/swap/v2/trade/order",{"symbol":c["symbol"],"side":"BUY" if c["side"]=="LONG" else "SELL","positionSide":c["side"],"type":"MARKET","quantity":qty,"newClientOrderId":f"SAMV2_{int(time.time()*1000)}"})
        actual=None
        for _ in range(12):
            time.sleep(.5); actual=next((p for p in positions() if str(p.get("symbol","")).upper()==c["symbol"] and str(p.get("positionSide","")).upper()==c["side"]),None)
            if actual:break
        if not actual:raise RuntimeError("Entry sent but position not confirmed")
        aq=abs(float(actual.get("positionAmt") or actual.get("positionSize"))); ae=float(actual.get("avgPrice") or actual.get("avgEntryPrice") or c["entry"])
        if c["side"]=="LONG" and not sl<ae<tp:raise RuntimeError("Invalid LONG protection")
        if c["side"]=="SHORT" and not tp<ae<sl:raise RuntimeError("Invalid SHORT protection")
        base={"symbol":c["symbol"],"side":"SELL" if c["side"]=="LONG" else "BUY","positionSide":c["side"],"workingType":"MARK_PRICE","quantity":aq,"closePosition":"true"}
        order("/openApi/swap/v2/trade/order",{**base,"type":"STOP_MARKET","stopPrice":sl,"newClientOrderId":f"SAMV2_SL_{int(time.time()*1000)}"})
        order("/openApi/swap/v2/trade/order",{**base,"type":"TAKE_PROFIT_MARKET","stopPrice":tp,"newClientOrderId":f"SAMV2_TP_{int(time.time()*1000)}"})
        rec={**c,"equity_at_signal":eq,"risk_cash":risk_cash,"qty_actual":aq,"entry_actual":ae,"sl":sl,"tp":tp,"time":time.time()}
        with STATE.open("a",encoding="utf-8") as f:f.write(json.dumps(rec)+"\n")
        print(f"EXECUTED {c['symbol']} {c['side']} qty={aq} entry={ae} SL={sl} TP={tp}")
if __name__=="__main__":main()
