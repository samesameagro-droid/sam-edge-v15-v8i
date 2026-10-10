#!/usr/bin/env python3
"""Research-only MTF replay for SAM EDGE V15 cohort 101-128.
Fetches public Binance USD-M klines; does not place orders or touch production.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import requests
import pandas as pd

API = "https://fapi.binance.com/fapi/v1/klines"
INTERVALS = {"15m": 15, "30m": 30, "1h": 60, "4h": 240}
HEADERS = {"User-Agent": "SAM-EDGE-MTF-Research/1.0"}

def fetch_klines(symbol, interval, start_ms, end_ms):
    rows=[]; cursor=start_ms
    while cursor < end_ms:
        params={"symbol":symbol,"interval":interval,"startTime":cursor,"endTime":end_ms,"limit":1000}
        r=requests.get(API,params=params,headers=HEADERS,timeout=25)
        if r.status_code != 200:
            raise RuntimeError(f"Binance {symbol} {interval}: HTTP {r.status_code}: {r.text[:160]}")
        batch=r.json()
        if not batch: break
        rows.extend(batch)
        nxt=int(batch[-1][0])+INTERVALS[interval]*60_000
        if nxt <= cursor: break
        cursor=nxt
        if len(batch)<1000: break
        time.sleep(0.08)
    if not rows: return pd.DataFrame()
    x=pd.DataFrame(rows,columns=["open_ms","open","high","low","close","volume","close_ms","quote_volume","trades","taker_base","taker_quote","ignore"])
    for c in ["open","high","low","close","volume"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    x["open_ms"]=pd.to_numeric(x["open_ms"],errors="coerce").astype("int64")
    x["close_ms"]=pd.to_numeric(x["close_ms"],errors="coerce").astype("int64")
    return x.drop_duplicates("open_ms").sort_values("open_ms").reset_index(drop=True)

def classify(df, entry_ms):
    if df.empty: return {"state":"NO_DATA","close":None,"ema20":None,"ema50":None,"structure":None}
    # Only candles fully closed by the signal/entry timestamp are eligible (no look-ahead).
    x=df[df.close_ms < entry_ms].copy()
    if len(x)<55: return {"state":"INSUFFICIENT","close":None,"ema20":None,"ema50":None,"structure":None,"bars":len(x)}
    x["ema20"]=x.close.ewm(span=20,adjust=False).mean()
    x["ema50"]=x.close.ewm(span=50,adjust=False).mean()
    last=x.iloc[-1]
    # A simple, reproducible directional regime, not a claimed perfect market-structure detector.
    if last.close > last.ema20 > last.ema50: state="BULL"
    elif last.close < last.ema20 < last.ema50: state="BEAR"
    else: state="MIXED"
    # Recent slope context, used as an additional descriptive field only.
    slope20=float(last.ema20/x.iloc[-5].ema20-1) if len(x)>=5 and x.iloc[-5].ema20 else 0.0
    return {"state":state,"close":float(last.close),"ema20":float(last.ema20),"ema50":float(last.ema50),"ema20_slope4":slope20,"bars":len(x),"bar_open_utc":pd.to_datetime(last.open_ms,unit="ms",utc=True).isoformat()}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--state",default="paper_v15_state_v2.json")
    ap.add_argument("--out",default="research/results/mtf_30m_replay")
    ap.add_argument("--fee-bps",type=float,default=5.0,help="fee bps per side")
    ap.add_argument("--slippage-bps",type=float,default=2.0,help="slippage bps per side")
    args=ap.parse_args()
    state=json.loads(Path(args.state).read_text())
    trades=[t for t in state.get("closed",[]) if t.get("core")=="V15_PRECISION_V2_FINAL" and t.get("result") in ("TP","SL") and t.get("side") in ("LONG","SHORT")]
    if not trades: raise SystemExit("No eligible TP/SL trades found in state file.")
    start=pd.Timestamp(min(t["opened_at"] for t in trades))-pd.Timedelta(days=14)
    end=pd.Timestamp(max(t.get("closed_at",t["opened_at"]) for t in trades))+pd.Timedelta(days=1)
    start_ms=int(start.timestamp()*1000); end_ms=int(end.timestamp()*1000)
    symbols=sorted({t["coin"].split("/")[0].replace(":USDT","") + "USDT" for t in trades})
    cache={}; errors=[]
    for sym in symbols:
        for tf in ("15m","30m","1h","4h"):
            try:
                cache[(sym,tf)]=fetch_klines(sym,tf,start_ms,end_ms)
                print(f"DATA {sym} {tf}: {len(cache[(sym,tf)])} bars",flush=True)
            except Exception as e:
                errors.append({"symbol":sym,"tf":tf,"error":str(e)})
                cache[(sym,tf)]=pd.DataFrame()
                print(f"DATA_ERROR {sym} {tf}: {e}",flush=True)
    out=[]
    for t in trades:
        sym=t["coin"].split("/")[0].replace(":USDT","")+"USDT"
        entry_ms=int(pd.Timestamp(t["opened_at"]).timestamp()*1000)
        row={"coin":t["coin"],"side":t["side"],"opened_at":t["opened_at"],"closed_at":t.get("closed_at"),"result":t["result"],"R_gross":float(t.get("R",-1 if t["result"]=="SL" else 1.25)),"entry":t.get("entry"),"sl":t.get("sl"),"tp":t.get("tp"),"entry_score":t.get("entry_score")}
        for tf in ("4h","1h","30m","15m"):
            z=classify(cache.get((sym,tf),pd.DataFrame()),entry_ms)
            row[tf+"_state"]=z["state"]; row[tf+"_close"]=z.get("close"); row[tf+"_ema20"]=z.get("ema20"); row[tf+"_ema50"]=z.get("ema50"); row[tf+"_slope4"]=z.get("ema20_slope4"); row[tf+"_bars"]=z.get("bars")
        stop_pct=abs(float(t["entry"])-float(t["sl"]))/float(t["entry"])
        cost_R=(2*(args.fee_bps+args.slippage_bps)/10000)/stop_pct if stop_pct>0 else None
        row["estimated_cost_R"]=cost_R
        row["R_net_est"]=row["R_gross"]-cost_R if cost_R is not None else None
        # Rule candidates: the actual 15m entry is retained, only directional MTF veto is simulated.
        row["keep_30m_gate"]=not (t["side"]=="LONG" and row["30m_state"]=="BEAR") and not (t["side"]=="SHORT" and row["30m_state"]=="BULL")
        row["keep_1h30m_gate"]=row["keep_30m_gate"] and not (t["side"]=="LONG" and row["1h_state"]=="BEAR") and not (t["side"]=="SHORT" and row["1h_state"]=="BULL")
        row["keep_strict_4h1h30m"]=row["keep_1h30m_gate"] and not (t["side"]=="LONG" and row["4h_state"]=="BEAR") and not (t["side"]=="SHORT" and row["4h_state"]=="BULL")
        out.append(row)
    df=pd.DataFrame(out)
    dest=Path(args.out); dest.mkdir(parents=True,exist_ok=True)
    df.to_csv(dest/"trade_mtf_context.csv",index=False)
    def stats(mask):
        z=df[mask].copy()
        if z.empty: return {"n":0,"wins":0,"losses":0,"win_rate_pct":None,"gross_R":0,"net_est_R":0,"profit_factor_gross":None}
        wins=int((z.R_gross>0).sum()); losses=int((z.R_gross<0).sum())
        pos=float(z.loc[z.R_gross>0,"R_gross"].sum()); neg=float(-z.loc[z.R_gross<0,"R_gross"].sum())
        return {"n":len(z),"wins":wins,"losses":losses,"win_rate_pct":round(100*wins/len(z),2),"gross_R":round(float(z.R_gross.sum()),4),"net_est_R":round(float(z.R_net_est.sum()),4),"profit_factor_gross":round(pos/neg,4) if neg else None,"excluded_SL":int(((~mask)&(df.R_gross<0)).sum()),"excluded_TP":int(((~mask)&(df.R_gross>0)).sum())}
    allmask=pd.Series(True,index=df.index)
    summary={"source":"Binance USD-M Futures public klines via /fapi/v1/klines","source_caveat":"Binance proxy, not exact BingX candles/fills; verify symbol history and timestamp availability.","lookahead":"Only candles whose close_ms is strictly before entry timestamp are used for context.","trade_count":len(df),"data_errors":errors,"baseline":stats(allmask),"gate_30m":stats(df.keep_30m_gate),"gate_1h_plus_30m":stats(df.keep_1h30m_gate),"gate_4h_plus_1h_plus_30m":stats(df.keep_strict_4h1h30m),"definition":"Bull = close > EMA20 > EMA50; bear = close < EMA20 < EMA50; otherwise mixed. This tests directional veto only; it does not generate new countertrend SHORT signals."}
    (dest/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
