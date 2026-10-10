#!/usr/bin/env python3
"""Research-only MTF replay for SAM EDGE V15 cohort 101-128. No orders; research branch only."""
from __future__ import annotations
import argparse, json, io, zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd

ARCHIVE = "https://data.binance.vision/data/futures/um/{kind}/klines/{symbol}/{interval}/{filename}"
INTERVALS = {"15m":15,"30m":30,"1h":60,"4h":240}
HEADERS = {"User-Agent":"SAM-EDGE-MTF-Research/1.0"}

def fetch_one_archive(symbol, interval, kind, period):
    filename=f"{symbol}-{interval}-{period}.zip"
    url=ARCHIVE.format(kind=kind,symbol=symbol,interval=interval,filename=filename)
    try:
        r=requests.get(url,headers=HEADERS,timeout=35)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names: return None
            raw=pd.read_csv(z.open(names[0]),header=None)
        if raw.empty or raw.shape[1]<6: return None
        if not str(raw.iloc[0,0]).strip().replace(".","",1).isdigit():
            raw=raw.iloc[1:].reset_index(drop=True)
        x=pd.DataFrame({
            "open_ms":pd.to_numeric(raw.iloc[:,0],errors="coerce"),
            "open":pd.to_numeric(raw.iloc[:,1],errors="coerce"),
            "high":pd.to_numeric(raw.iloc[:,2],errors="coerce"),
            "low":pd.to_numeric(raw.iloc[:,3],errors="coerce"),
            "close":pd.to_numeric(raw.iloc[:,4],errors="coerce"),
            "volume":pd.to_numeric(raw.iloc[:,5],errors="coerce")
        }).dropna()
        x["open_ms"]=x["open_ms"].astype("int64")
        x["close_ms"]=x["open_ms"]+INTERVALS[interval]*60_000-1
        return x
    except Exception as e:
        print(f"ARCHIVE_ERROR {symbol} {interval} {period}: {type(e).__name__}: {e}",flush=True)
        return None

def fetch_klines_api(symbol, interval, start_ms, end_ms):
    """Fallback to Binance USD-M Futures REST when archive files are missing."""
    interval_ms = INTERVALS[interval] * 60_000
    cursor = start_ms
    pieces = []
    # Try alternate official USD-M Futures hosts when a hostname is geo-blocked.
    endpoints = [
        "https://fapi.binance.com/fapi/v1/klines",
        "https://fapi1.binance.com/fapi/v1/klines",
        "https://fapi2.binance.com/fapi/v1/klines",
        "https://fapi3.binance.com/fapi/v1/klines",
    ]
    while cursor < end_ms:
        rows = None
        host_errors = []
        for endpoint in endpoints:
            try:
                r = requests.get(endpoint, params={
                    "symbol": symbol, "interval": interval,
                    "startTime": cursor, "endTime": end_ms - 1, "limit": 1500
                }, headers=HEADERS, timeout=20)
                r.raise_for_status()
                rows = r.json()
                break
            except Exception as e:
                host_errors.append(f"{endpoint.split('/')[2]}={type(e).__name__}:{e}")
        if rows is None:
            print(f"REST_FALLBACK_ERROR {symbol} {interval}: " + " | ".join(host_errors), flush=True)
            break
        if not rows:
            break
        raw = pd.DataFrame(rows)
        x = pd.DataFrame({
            "open_ms": pd.to_numeric(raw.iloc[:, 0], errors="coerce"),
            "open": pd.to_numeric(raw.iloc[:, 1], errors="coerce"),
            "high": pd.to_numeric(raw.iloc[:, 2], errors="coerce"),
            "low": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
            "close": pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
            "volume": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
        }).dropna()
        if x.empty:
            break
        x["open_ms"] = x["open_ms"].astype("int64")
        x["close_ms"] = x["open_ms"] + interval_ms - 1
        pieces.append(x)
        next_cursor = int(x["open_ms"].max()) + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(rows) < 1500:
            break
    if not pieces:
        return pd.DataFrame()
    return (pd.concat(pieces, ignore_index=True)
            .drop_duplicates("open_ms").sort_values("open_ms").reset_index(drop=True))


def fetch_klines(symbol, interval, start_ms, end_ms):
    start = pd.to_datetime(start_ms, unit="ms", utc=True)
    end = pd.to_datetime(end_ms - 1, unit="ms", utc=True)
    periods = []
    month = start.replace(day=1)
    last_month = end.replace(day=1)
    now = pd.Timestamp.now(tz="UTC")
    while month <= last_month:
        next_month = (month + pd.offsets.MonthBegin(1)).normalize()
        if month < now.replace(day=1):
            periods.append(("monthly", month.strftime("%Y-%m")))
        else:
            day = max(start.normalize(), month)
            while day <= end.normalize():
                periods.append(("daily", day.strftime("%Y-%m-%d")))
                day = day + pd.Timedelta(days=1)
        month = next_month

    pieces = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_one_archive, symbol, interval, kind, period)
                   for kind, period in periods]
        for fut in futures:
            piece = fut.result()
            if piece is not None and not piece.empty:
                pieces.append(piece)

    archive = pd.DataFrame()
    if pieces:
        archive = (pd.concat(pieces, ignore_index=True)
                   .drop_duplicates("open_ms").sort_values("open_ms").reset_index(drop=True))
        archive = archive[(archive.open_ms >= start_ms) & (archive.open_ms < end_ms)].reset_index(drop=True)

    interval_ms = INTERVALS[interval] * 60_000
    target_end = min(end_ms, int(pd.Timestamp.now(tz="UTC").timestamp() * 1000))
    expected_last_open = ((target_end - 1) // interval_ms) * interval_ms
    archive_stale = (archive.empty or int(archive.open_ms.max()) < expected_last_open - interval_ms)
    archive_gappy = (not archive.empty and bool((archive.open_ms.diff().dropna() > interval_ms * 1.5).any()))

    # Do not silently trust a partial archive: fill/replace with the REST series when
    # its latest candle is stale or the archive contains internal timestamp gaps.
    if archive_stale or archive_gappy:
        api = fetch_klines_api(symbol, interval, start_ms, target_end)
        if not api.empty:
            archive = (pd.concat([archive, api], ignore_index=True)
                       .drop_duplicates("open_ms", keep="last")
                       .sort_values("open_ms").reset_index(drop=True))

    return archive[(archive.open_ms >= start_ms) & (archive.open_ms < end_ms)].reset_index(drop=True)

def classify(df, entry_ms):
    if df.empty: return {"state":"NO_DATA","close":None,"ema20":None,"ema50":None}
    x=df[df.close_ms < entry_ms].copy()
    if len(x)<55: return {"state":"INSUFFICIENT","close":None,"ema20":None,"ema50":None,"bars":len(x)}
    x["ema20"]=x.close.ewm(span=20,adjust=False).mean()
    x["ema50"]=x.close.ewm(span=50,adjust=False).mean()
    last=x.iloc[-1]
    if last.close>last.ema20>last.ema50: state="BULL"
    elif last.close<last.ema20<last.ema50: state="BEAR"
    else: state="MIXED"
    slope=float(last.ema20/x.iloc[-5].ema20-1) if len(x)>=5 and x.iloc[-5].ema20 else 0.0
    return {"state":state,"close":float(last.close),"ema20":float(last.ema20),"ema50":float(last.ema50),"ema20_slope4":slope,"bars":len(x),"bar_open_utc":pd.to_datetime(last.open_ms,unit="ms",utc=True).isoformat()}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--state",default="paper_v15_state_v2.json")
    ap.add_argument("--out",default="research/results/mtf_30m_replay")
    ap.add_argument("--fee-bps",type=float,default=5.0)
    ap.add_argument("--slippage-bps",type=float,default=2.0)
    args=ap.parse_args()
    state=json.loads(Path(args.state).read_text())
    trades=[t for t in state.get("closed",[]) if t.get("core")=="V15_PRECISION_V2_FINAL" and t.get("result") in ("TP","SL") and t.get("side") in ("LONG","SHORT")]
    if not trades: raise SystemExit("No eligible TP/SL trades found in state file.")
    start=pd.Timestamp(min(t["opened_at"] for t in trades))-pd.Timedelta(days=14)
    end=pd.Timestamp(max(t.get("closed_at",t["opened_at"]) for t in trades))+pd.Timedelta(days=1)
    start_ms=int(start.timestamp()*1000); end_ms=int(end.timestamp()*1000)
    symbols=sorted({t["coin"].split("/")[0].replace(":USDT","")+"USDT" for t in trades})
    cache={}; errors=[]
    latest_entry_by_symbol = {
        sym: max(int(pd.Timestamp(t["opened_at"]).timestamp() * 1000)
                 for t in trades
                 if t["coin"].split("/")[0].replace(":USDT","")+"USDT" == sym)
        for sym in symbols
    }
    for sym in symbols:
        for tf in ("15m","30m","1h","4h"):
            cache[(sym,tf)]=fetch_klines(sym,tf,start_ms,end_ms)
            print(f"DATA {sym} {tf}: {len(cache[(sym,tf)])} candles",flush=True)
            interval_ms = INTERVALS[tf] * 60_000
            # Require coverage only through the last cohort entry for this symbol.
            last_entry_ms = latest_entry_by_symbol[sym]
            expected_last_open = ((last_entry_ms - 1) // interval_ms) * interval_ms
            if cache[(sym,tf)].empty:
                errors.append({"symbol":sym,"tf":tf,"error":"No archive or REST candles returned"})
            else:
                latest_open = int(cache[(sym,tf)].open_ms.max())
                if latest_open < expected_last_open:
                    errors.append({"symbol":sym,"tf":tf,"error":"Stale coverage before last cohort entry","latest_open_utc":pd.to_datetime(latest_open,unit="ms",utc=True).isoformat(),"required_latest_open_utc":pd.to_datetime(expected_last_open,unit="ms",utc=True).isoformat(),"last_entry_utc":pd.to_datetime(last_entry_ms,unit="ms",utc=True).isoformat()})
                opens = cache[(sym,tf)].open_ms
                gaps = int((opens.diff().dropna() > interval_ms * 1.5).sum())
                if gaps:
                    errors.append({"symbol":sym,"tf":tf,"error":"Internal candle gaps","gap_count":gaps})
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
        row["keep_30m_gate"]=not (t["side"]=="LONG" and row["30m_state"]=="BEAR") and not (t["side"]=="SHORT" and row["30m_state"]=="BULL")
        row["keep_1h30m_gate"]=row["keep_30m_gate"] and not (t["side"]=="LONG" and row["1h_state"]=="BEAR") and not (t["side"]=="SHORT" and row["1h_state"]=="BULL")
        row["keep_strict_4h1h30m"]=row["keep_1h30m_gate"] and not (t["side"]=="LONG" and row["4h_state"]=="BEAR") and not (t["side"]=="SHORT" and row["4h_state"]=="BULL")
        out.append(row)
    df=pd.DataFrame(out); dest=Path(args.out); dest.mkdir(parents=True,exist_ok=True)
    df.to_csv(dest/"trade_mtf_context.csv",index=False)
    def stats(mask):
        z=df[mask].copy()
        if z.empty: return {"n":0,"wins":0,"losses":0,"win_rate_pct":None,"gross_R":0,"net_est_R":0,"profit_factor_gross":None}
        wins=int((z.R_gross>0).sum()); losses=int((z.R_gross<0).sum())
        pos=float(z.loc[z.R_gross>0,"R_gross"].sum()); neg=float(-z.loc[z.R_gross<0,"R_gross"].sum())
        return {"n":len(z),"wins":wins,"losses":losses,"win_rate_pct":round(100*wins/len(z),2),"gross_R":round(float(z.R_gross.sum()),4),"net_est_R":round(float(z.R_net_est.sum()),4),"profit_factor_gross":round(pos/neg,4) if neg else None,"excluded_SL":int(((~mask)&(df.R_gross<0)).sum()),"excluded_TP":int(((~mask)&(df.R_gross>0)).sum())}
    allmask=pd.Series(True,index=df.index)
    summary={"source":"Binance USD-M Futures public archive plus official Binance Futures REST host fallback","source_caveat":"Binance proxy, not exact BingX candles/fills; coverage is validated against each symbol's latest cohort entry.","lookahead":"Only candles whose close_ms is strictly before entry timestamp are used for context.","trade_count":len(df),"validation_passed":not errors,"data_errors":errors,"baseline":stats(allmask),"gate_30m":stats(df.keep_30m_gate),"gate_1h_plus_30m":stats(df.keep_1h30m_gate),"gate_4h_plus_1h_plus_30m":stats(df.keep_strict_4h1h30m),"definition":"Bull = close > EMA20 > EMA50; bear = close < EMA20 < EMA50; otherwise mixed. Directional veto test only; it does not generate new countertrend SHORT signals. Estimated fees/slippage are approximate in R. Gate statistics are diagnostic only unless validation_passed=true."}
    (dest/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    if errors:
        raise SystemExit(f"REPLAY_INVALID: {len(errors)} data coverage/gap errors; do not use gate metrics.")
if __name__=="__main__": main()
