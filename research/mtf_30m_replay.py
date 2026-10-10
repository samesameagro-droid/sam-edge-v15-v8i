#!/usr/bin/env python3
"""Research-only MTF replay for SAM EDGE V15 cohort 101-128. No orders; research branch only."""
from __future__ import annotations
import argparse, json, io, zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd

ARCHIVE = "https://data.binance.vision/data/futures/um/{kind}/klines/{symbol}/{interval}/{filename}"
INTERVALS = {"1m":1,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240}
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
        x["source"]=f"binance_archive_{kind}"
        return x
    except Exception as e:
        print(f"ARCHIVE_ERROR {symbol} {interval} {period}: {type(e).__name__}: {e}",flush=True)
        return None

def fetch_klines_api(symbol, interval, start_ms, end_ms):
    """Try public Binance Futures endpoints, then public BingX swap klines."""
    interval_ms = INTERVALS[interval] * 60_000

    def normalize_rows(rows, source):
        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(rows)
        try:
            if source == "bingx" and isinstance(rows[0], dict):
                time_col = next((c for c in ("time", "timestamp", "openTime", "open_time") if c in raw.columns), None)
                if time_col is None:
                    raise ValueError(f"No timestamp field in BingX response: {list(raw.columns)}")
                x = pd.DataFrame({
                    "open_ms": pd.to_numeric(raw[time_col], errors="coerce"),
                    "open": pd.to_numeric(raw["open"], errors="coerce"),
                    "high": pd.to_numeric(raw["high"], errors="coerce"),
                    "low": pd.to_numeric(raw["low"], errors="coerce"),
                    "close": pd.to_numeric(raw["close"], errors="coerce"),
                    "volume": pd.to_numeric(raw["volume"], errors="coerce"),
                }).dropna()
            else:
                x = pd.DataFrame({
                    "open_ms": pd.to_numeric(raw.iloc[:, 0], errors="coerce"),
                    "open": pd.to_numeric(raw.iloc[:, 1], errors="coerce"),
                    "high": pd.to_numeric(raw.iloc[:, 2], errors="coerce"),
                    "low": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
                    "close": pd.to_numeric(raw.iloc[:, 4], errors="coerce"),
                    "volume": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
                }).dropna()
            # BingX timestamps are milliseconds; reject any unexpected seconds scale.
            x["open_ms"] = x["open_ms"].astype("int64")
            if not x.empty and int(x.open_ms.max()) < 100_000_000_000:
                x["open_ms"] = x["open_ms"] * 1000
            x["close_ms"] = x["open_ms"] + interval_ms - 1
            x["source"] = f"{source}_rest"
            return x[(x.open_ms >= start_ms) & (x.open_ms < end_ms)].copy()
        except Exception as e:
            print(f"PARSE_ERROR {source} {symbol} {interval}: {type(e).__name__}: {e}", flush=True)
            return pd.DataFrame()

    # First try official Binance USD-M Futures REST hosts.
    endpoints = ["https://fapi.binance.com/fapi/v1/klines"]
    cursor = start_ms
    pieces = []
    binance_failed = False
    while cursor < end_ms:
        rows = None
        host_errors = []
        chunk_end = min(end_ms - 1, cursor + interval_ms * 1499)
        for endpoint in endpoints:
            try:
                r = requests.get(endpoint, params={
                    "symbol": symbol, "interval": interval,
                    "startTime": cursor, "endTime": chunk_end, "limit": 1500
                }, headers=HEADERS, timeout=8)
                r.raise_for_status()
                rows = r.json()
                break
            except Exception as e:
                host_errors.append(f"{endpoint.split('/')[2]}={type(e).__name__}:{e}")
        if rows is None:
            print(f"BINANCE_API_UNAVAILABLE {symbol} {interval}: " + " | ".join(host_errors), flush=True)
            binance_failed = True
            break
        if not rows:
            break
        x = normalize_rows(rows, "binance")
        if x.empty:
            break
        pieces.append(x)
        next_cursor = int(x.open_ms.max()) + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(rows) < 1500:
            break

    # If Binance REST is blocked or incomplete, query BingX's public perpetual-kline API.
    combined = (pd.concat(pieces, ignore_index=True).drop_duplicates("open_ms")
                .sort_values("open_ms").reset_index(drop=True)) if pieces else pd.DataFrame()
    need_bingx = (combined.empty or int(combined.open_ms.max()) < end_ms - interval_ms * 2
                  or bool((combined.open_ms.diff().dropna() > interval_ms * 1.5).any()))
    if need_bingx:
        bingx_symbol = symbol[:-4] + "-USDT" if symbol.endswith("USDT") else symbol
        cursor = start_ms
        bx_pieces = []
        endpoint = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
        while cursor < end_ms:
            chunk_end = min(end_ms - 1, cursor + interval_ms * 999)
            try:
                r = requests.get(endpoint, params={
                    "symbol": bingx_symbol, "interval": interval,
                    "startTime": cursor, "endTime": chunk_end, "limit": 1000
                }, headers=HEADERS, timeout=10)
                r.raise_for_status()
                payload = r.json()
                if payload.get("code") not in (None, 0, "0"):
                    raise ValueError(f"BingX API code={payload.get('code')} msg={payload.get('msg')}")
                rows = payload.get("data", [])
                if not rows:
                    break
                x = normalize_rows(rows, "bingx")
                if x.empty:
                    break
                bx_pieces.append(x)
                next_cursor = int(x.open_ms.max()) + interval_ms
                if next_cursor <= cursor:
                    break
                cursor = next_cursor
            except Exception as e:
                print(f"BINGX_API_ERROR {symbol} {interval}: {type(e).__name__}: {e}", flush=True)
                break
        if bx_pieces:
            bx = (pd.concat(bx_pieces, ignore_index=True).drop_duplicates("open_ms")
                  .sort_values("open_ms").reset_index(drop=True))
            # Prefer exact BingX candles if they cover the requested period.
            if combined.empty or int(bx.open_ms.max()) > int(combined.open_ms.max()):
                combined = bx
            elif not bx.empty:
                combined = (pd.concat([combined, bx], ignore_index=True)
                            .drop_duplicates("open_ms", keep="last")
                            .sort_values("open_ms").reset_index(drop=True))
    return combined[(combined.open_ms >= start_ms) & (combined.open_ms < end_ms)].reset_index(drop=True) if not combined.empty else pd.DataFrame()


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
    with ThreadPoolExecutor(max_workers=4) as pool:
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
    if interval in ("1m","5m") or archive_stale or archive_gappy:
        api = fetch_klines_api(symbol, interval, start_ms, target_end)
        if not api.empty:
            if interval in ("1m","5m"):
                # Intrabar path replay prefers a single REST source for the entire window.
                archive = api.sort_values("open_ms").drop_duplicates("open_ms").reset_index(drop=True)
            else:
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

def atr_at(df, timestamp_ms, period=14):
    """SMA ATR from candles fully closed strictly before timestamp_ms."""
    if df.empty:
        return None
    x=df[df.close_ms < timestamp_ms].sort_values("open_ms").copy()
    if len(x) < period + 1:
        return None
    prev=x.close.shift(1)
    tr=pd.concat([
        x.high-x.low,
        (x.high-prev).abs(),
        (x.low-prev).abs()
    ],axis=1).max(axis=1)
    value=tr.rolling(period).mean().iloc[-1]
    return float(value) if pd.notna(value) and value>0 else None


def simulate_path(df, start_ms, end_ms, side, sl, tp):
    """Minute-OHLC path until end_ms; ambiguous same-bar stop/target is conservatively SL."""
    if df.empty:
        return {"result":"NO_DATA","touch_ms":None,"ambiguous":False,"bars":0}
    x=df[(df.open_ms >= start_ms) & (df.close_ms < end_ms)].sort_values("open_ms")
    if x.empty:
        return {"result":"NO_BARS","touch_ms":None,"ambiguous":False,"bars":0}
    for r in x.itertuples():
        if side=="LONG":
            stop_hit=float(r.low)<=float(sl)
            target_hit=float(r.high)>=float(tp)
        else:
            stop_hit=float(r.high)>=float(sl)
            target_hit=float(r.low)<=float(tp)
        if stop_hit and target_hit:
            return {"result":"SL","touch_ms":int(r.open_ms),"ambiguous":True,"bars":len(x)}
        if stop_hit:
            return {"result":"SL","touch_ms":int(r.open_ms),"ambiguous":False,"bars":len(x)}
        if target_hit:
            return {"result":"TP","touch_ms":int(r.open_ms),"ambiguous":False,"bars":len(x)}
    return {"result":"TIMEOUT","touch_ms":None,"ambiguous":False,"bars":len(x)}


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
    tasks=[(sym,tf) for sym in symbols for tf in ("15m","30m","1h","4h")]
    # Fetch symbol/timeframe series concurrently so one slow public endpoint cannot
    # serialize the entire 96-series replay.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(fetch_klines,sym,tf,start_ms,end_ms):(sym,tf) for sym,tf in tasks}
        for future,key in futures.items():
            sym,tf=key
            try:
                cache[key]=future.result()
            except Exception as e:
                cache[key]=pd.DataFrame()
                errors.append({"symbol":sym,"tf":tf,"error":"Candle fetch exception","detail":f"{type(e).__name__}: {e}"})
            print(f"DATA {sym} {tf}: {len(cache[key])} candles",flush=True)
    for sym,tf in tasks:
        interval_ms = INTERVALS[tf] * 60_000
        series=cache[(sym,tf)]
        if series.empty:
            errors.append({"symbol":sym,"tf":tf,"error":"No archive or REST candles returned"})
            continue
        opens = set(series.open_ms.astype("int64").tolist())
        gaps = int((series.open_ms.diff().dropna() > interval_ms * 1.5).sum())
        if gaps:
            errors.append({"symbol":sym,"tf":tf,"error":"Internal candle gaps","gap_count":gaps})
        # Validate the exact last fully closed candle available before every cohort entry.
        for trade in [t for t in trades if t["coin"].split("/")[0].replace(":USDT","")+"USDT" == sym]:
            entry_ms = int(pd.Timestamp(trade["opened_at"]).timestamp() * 1000)
            required_open = ((entry_ms - interval_ms) // interval_ms) * interval_ms
            if required_open not in opens:
                errors.append({
                    "symbol":sym,"tf":tf,"error":"Missing exact last closed candle before entry",
                    "entry_utc":pd.to_datetime(entry_ms,unit="ms",utc=True).isoformat(),
                    "required_candle_open_utc":pd.to_datetime(required_open,unit="ms",utc=True).isoformat()
                })
    # Fetch 1m candles only around each symbol's observed trade windows for
    # independent OHLC path validation and sensitivity tests.
    path_errors=[]
    path_ranges={}
    for sym in symbols:
        sym_trades=[t for t in trades if t["coin"].split("/")[0].replace(":USDT","")+"USDT" == sym]
        path_start=int(min(pd.Timestamp(t["opened_at"]).timestamp()*1000 for t in sym_trades))-60_000
        path_end=int(max(pd.Timestamp(t["closed_at"]).timestamp()*1000 for t in sym_trades))+20*60_000
        path_ranges[sym]=(path_start,path_end)
    with ThreadPoolExecutor(max_workers=8) as pool:
        path_futures={pool.submit(fetch_klines,sym,tf,path_ranges[sym][0],path_ranges[sym][1]):(sym,tf)
                      for sym in symbols for tf in ("1m","5m")}
        for future,key in path_futures.items():
            sym,tf=key
            try:
                cache[key]=future.result()
            except Exception as e:
                cache[key]=pd.DataFrame()
                path_errors.append({"symbol":sym,"tf":tf,"error":"Path candle fetch exception","detail":f"{type(e).__name__}: {e}"})
            print(f"DATA {sym} {tf} path: {len(cache[key])} candles",flush=True)
    for sym in symbols:
        for tf in ("1m","5m"):
            series=cache[(sym,tf)]
            if series.empty:
                path_errors.append({"symbol":sym,"tf":tf,"error":"No path candles"})
                continue
            interval_ms=INTERVALS[tf]*60_000
            opens=set(series.open_ms.astype("int64").tolist())
            for trade in [t for t in trades if t["coin"].split("/")[0].replace(":USDT","")+"USDT" == sym]:
                entry_ms=int(pd.Timestamp(trade["opened_at"]).timestamp()*1000)
                exit_ms=int(pd.Timestamp(trade["closed_at"]).timestamp()*1000)
                first_open=((entry_ms//interval_ms)*interval_ms)+interval_ms
                horizon_end=exit_ms+5*60_000
                last_open=((horizon_end-1)//interval_ms)*interval_ms
                if first_open not in opens:
                    path_errors.append({"symbol":sym,"tf":tf,"error":"Missing first tracking candle after entry","entry_utc":trade["opened_at"],"required_open_utc":pd.to_datetime(first_open,unit="ms",utc=True).isoformat()})
                if last_open not in opens:
                    path_errors.append({"symbol":sym,"tf":tf,"error":"Missing exit tracking candle","exit_utc":trade["closed_at"],"required_open_utc":pd.to_datetime(last_open,unit="ms",utc=True).isoformat()})
                segment=series[(series.open_ms>=first_open)&(series.open_ms<=last_open)]
                gaps=int((segment.open_ms.diff().dropna()>interval_ms*1.5).sum())
                if gaps:
                    path_errors.append({"symbol":sym,"tf":tf,"error":"Timestamp gaps inside tracking window","entry_utc":trade["opened_at"],"exit_utc":trade["closed_at"],"gap_count":gaps})
    out=[]
    for t in trades:
        sym=t["coin"].split("/")[0].replace(":USDT","")+"USDT"
        entry_ms=int(pd.Timestamp(t["opened_at"]).timestamp()*1000)
        row={"coin":t["coin"],"side":t["side"],"opened_at":t["opened_at"],"closed_at":t.get("closed_at"),"result":t["result"],"R_gross":float(t.get("R",-1 if t["result"]=="SL" else 1.25)),"entry":t.get("entry"),"sl":t.get("sl"),"tp":t.get("tp"),"entry_score":t.get("entry_score")}
        for tf in ("4h","1h","30m","15m"):
            z=classify(cache.get((sym,tf),pd.DataFrame()),entry_ms)
            row[tf+"_state"]=z["state"]; row[tf+"_close"]=z.get("close"); row[tf+"_ema20"]=z.get("ema20"); row[tf+"_ema50"]=z.get("ema50"); row[tf+"_slope4"]=z.get("ema20_slope4"); row[tf+"_bars"]=z.get("bars")
            if z["state"] in ("NO_DATA","INSUFFICIENT"):
                errors.append({"symbol":sym,"tf":tf,"error":"Insufficient closed candles for classification","entry_utc":t["opened_at"],"state":z["state"],"bars":z.get("bars")})
        # Catch obvious stale/wrong-contract contexts without treating normal volatility as an error.
        context_close = row.get("15m_close")
        if context_close and t.get("entry") and float(t["entry"]) > 0:
            deviation_pct = abs(float(context_close) - float(t["entry"])) / float(t["entry"]) * 100
            row["entry_vs_15m_context_close_pct"] = round(deviation_pct, 3)
            if deviation_pct > 20:
                errors.append({"symbol":sym,"tf":"15m","error":"Extreme entry/context price mismatch","entry_utc":t["opened_at"],"entry_price":float(t["entry"]),"last_closed_15m_close":float(context_close),"deviation_pct":round(deviation_pct,3)})
        # Minute-OHLC replay: same recorded entry/SL/TP, conservative SL-first on same-bar ambiguity.
        bars1m=cache.get((sym,"1m"),pd.DataFrame())
        exit_ms=int(pd.Timestamp(t["closed_at"]).timestamp()*1000)
        base_path=simulate_path(bars1m,entry_ms+1,exit_ms+5*60_000,t["side"],float(t["sl"]),float(t["tp"]))
        path_window=bars1m[(bars1m.open_ms>entry_ms)&(bars1m.open_ms<exit_ms+5*60_000)] if not bars1m.empty else pd.DataFrame()
        row["recorded_exit"]=t.get("exit")
        row["path_window_min_low"]=float(path_window.low.min()) if not path_window.empty else None
        row["path_window_max_high"]=float(path_window.high.max()) if not path_window.empty else None
        row["path_window_first_open"]=float(path_window.iloc[0].open) if not path_window.empty else None
        row["path_window_last_close"]=float(path_window.iloc[-1].close) if not path_window.empty else None
        exit_minute=bars1m[bars1m.open_ms==exit_ms] if not bars1m.empty else pd.DataFrame()
        row["exit_minute_open"]=float(exit_minute.iloc[0].open) if not exit_minute.empty else None
        row["exit_minute_high"]=float(exit_minute.iloc[0].high) if not exit_minute.empty else None
        row["exit_minute_low"]=float(exit_minute.iloc[0].low) if not exit_minute.empty else None
        row["ohlc_path_result"]=base_path["result"]
        row["ohlc_path_ambiguous"]=base_path["ambiguous"]
        row["ohlc_path_touch_utc"]=pd.to_datetime(base_path["touch_ms"],unit="ms",utc=True).isoformat() if base_path["touch_ms"] is not None else None
        row["ohlc_path_bars"]=base_path["bars"]
        row["ohlc_path_matches_recorded"]=(base_path["result"]==t["result"] and not base_path["ambiguous"])
        bars5m=cache.get((sym,"5m"),pd.DataFrame())
        path5=simulate_path(bars5m,entry_ms+1,exit_ms+5*60_000,t["side"],float(t["sl"]),float(t["tp"]))
        row["ohlc_path_5m_result"]=path5["result"]
        row["ohlc_path_5m_ambiguous"]=path5["ambiguous"]
        row["ohlc_path_5m_matches_recorded"]=(path5["result"]==t["result"] and not path5["ambiguous"])
        window5=bars5m[(bars5m.open_ms>entry_ms)&(bars5m.open_ms<exit_ms+5*60_000)] if not bars5m.empty else pd.DataFrame()
        row["path_5m_min_low"]=float(window5.low.min()) if not window5.empty else None
        row["path_5m_max_high"]=float(window5.high.max()) if not window5.empty else None
        if base_path["result"] in ("NO_DATA","NO_BARS"):
            path_errors.append({"symbol":sym,"tf":"1m","error":"No path bars for recorded trade","entry_utc":t["opened_at"],"exit_utc":t["closed_at"],"result":base_path["result"]})
        # ATR-distance sensitivity: scale original stop/target distances by +/-10%.
        entry=float(t["entry"]); original_sl=float(t["sl"]); original_tp=float(t["tp"])
        for label,factor in (("atr_minus10",0.9),("atr_plus10",1.1)):
            stop_dist=abs(entry-original_sl)*factor
            target_dist=abs(original_tp-entry)*factor
            if t["side"]=="LONG":
                scenario_sl,scenario_tp=entry-stop_dist,entry+target_dist
            else:
                scenario_sl,scenario_tp=entry+stop_dist,entry-target_dist
            scenario=simulate_path(bars1m,entry_ms+1,exit_ms+5*60_000,t["side"],scenario_sl,scenario_tp)
            row[label+"_result"]=scenario["result"]
            row[label+"_ambiguous"]=scenario["ambiguous"]
            scenario5=simulate_path(bars5m,entry_ms+1,exit_ms+5*60_000,t["side"],scenario_sl,scenario_tp)
            row[label+"_5m_result"]=scenario5["result"]
            row[label+"_5m_ambiguous"]=scenario5["ambiguous"]
        # One 15m-bar delay: enter at next 15m open and preserve the original ATR multiples.
        delayed_ms=entry_ms+15*60_000
        duration_ms=exit_ms-entry_ms
        if duration_ms<=15*60_000:
            row["one_bar_delay_result"]="MISSED_BEFORE_DELAY"
            row["one_bar_delay_ambiguous"]=False
        else:
            df15=cache.get((sym,"15m"),pd.DataFrame())
            delayed_candle=df15[df15.open_ms==delayed_ms]
            atr_original=t.get("entry_metrics",{}).get("atr") or atr_at(df15,entry_ms)
            atr_delayed=atr_at(df15,delayed_ms)
            if delayed_candle.empty or not atr_original or not atr_delayed:
                row["one_bar_delay_result"]="NO_ATR_OR_ENTRY_BAR"
                row["one_bar_delay_ambiguous"]=False
            else:
                delayed_entry=float(delayed_candle.iloc[0].open)
                stop_mult=abs(entry-original_sl)/float(atr_original)
                target_mult=abs(original_tp-entry)/float(atr_original)
                stop_dist=stop_mult*atr_delayed
                target_dist=target_mult*atr_delayed
                if t["side"]=="LONG":
                    delayed_sl,delayed_tp=delayed_entry-stop_dist,delayed_entry+target_dist
                else:
                    delayed_sl,delayed_tp=delayed_entry+stop_dist,delayed_entry-target_dist
                delayed=simulate_path(bars1m,delayed_ms+1,delayed_ms+duration_ms+5*60_000,t["side"],delayed_sl,delayed_tp)
                row["one_bar_delay_result"]=delayed["result"]
                row["one_bar_delay_ambiguous"]=delayed["ambiguous"]
                delayed5=simulate_path(bars5m,delayed_ms+1,delayed_ms+duration_ms+5*60_000,t["side"],delayed_sl,delayed_tp)
                row["one_bar_delay_5m_result"]=delayed5["result"]
                row["one_bar_delay_5m_ambiguous"]=delayed5["ambiguous"]
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
    score_thresholds=[40,45,48,50,51,52,53,54,54.5,55,56,58,60,62,65]
    score_sweep=[{"max_entry_score":threshold,**stats(df.entry_score <= threshold)}
                 for threshold in score_thresholds]
    eligible=[item for item in score_sweep if item.get("excluded_SL",0)>=5]
    best_score_filter=(sorted(eligible,key=lambda item:(
        item.get("wins",0),item.get("excluded_SL",0),item.get("net_est_R",0),
        -item.get("excluded_TP",0)),reverse=True)[0] if eligible else None)
    def outcome_stats(column, mask):
        z=df[mask].copy()
        outcomes=z[column].fillna("NO_DATA")
        tp_count=int((outcomes=="TP").sum())
        sl_count=int((outcomes=="SL").sum())
        timeout_count=int((outcomes=="TIMEOUT").sum())
        other_count=int((~outcomes.isin(["TP","SL","TIMEOUT"])).sum())
        target_r=(z.tp.astype(float)-z.entry.astype(float)).abs()/(z.entry.astype(float)-z.sl.astype(float)).abs()
        gross=float(target_r[outcomes=="TP"].sum()-sl_count)
        return {"n":len(z),"TP":tp_count,"SL":sl_count,"TIMEOUT":timeout_count,
                "other_or_unresolved":other_count,"ambiguous_same_minute":int(z.get(column.replace("_result","_ambiguous"),pd.Series(False,index=z.index)).fillna(False).sum()),
                "gross_R_timeout_assumed_zero":round(gross,4)}
    score54_mask=df.entry_score<=54
    path_matches=int(df.ohlc_path_matches_recorded.sum())
    path5_matches=int(df.ohlc_path_5m_matches_recorded.sum())
    path_validation={"trades":len(df),"matches_recorded_outcome_1m":path_matches,
                     "mismatches_1m":int(len(df)-path_matches),
                     "matches_recorded_outcome_5m":path5_matches,
                     "mismatches_5m":int(len(df)-path5_matches),
                     "ambiguous_same_minute_1m":int(df.ohlc_path_ambiguous.fillna(False).sum()),
                     "ambiguous_same_bar_5m":int(df.ohlc_path_5m_ambiguous.fillna(False).sum()),
                     "path_data_errors":len(path_errors),
                     "validation_passed_1m":path_matches==len(df) and not path_errors,
                     "validation_passed_5m":path5_matches==len(df) and not path_errors}
    path_scenarios={
        "recorded_entry_sl_tp_5m_all_trades":outcome_stats("ohlc_path_5m_result",allmask),
        "score_max_54_recorded_entry_5m":outcome_stats("ohlc_path_5m_result",score54_mask),
        "recorded_entry_sl_tp_all_trades":outcome_stats("ohlc_path_result",allmask),
        "score_max_54_recorded_entry":outcome_stats("ohlc_path_result",score54_mask),
        "atr_distance_minus_10pct_all_trades":outcome_stats("atr_minus10_result",allmask),
        "atr_distance_minus_10pct_5m_all_trades":outcome_stats("atr_minus10_5m_result",allmask),
        "atr_distance_plus_10pct_all_trades":outcome_stats("atr_plus10_result",allmask),
        "atr_distance_plus_10pct_5m_all_trades":outcome_stats("atr_plus10_5m_result",allmask),
        "one_15m_bar_delay_all_trades":outcome_stats("one_bar_delay_result",allmask),
        "one_15m_bar_delay_5m_all_trades":outcome_stats("one_bar_delay_5m_result",allmask),
        "one_15m_bar_delay_score_max_54":outcome_stats("one_bar_delay_result",score54_mask),
        "one_15m_bar_delay_score_max_54_5m":outcome_stats("one_bar_delay_5m_result",score54_mask)
    }
    loo=[]
    for coin in sorted(df.coin.unique()):
        z=df[df.coin!=coin]
        keep=z.entry_score<=54
        kept=z[keep]
        loo.append({"left_out_coin":coin,"trades_kept":len(kept),
                    "TP_kept":int((kept.R_gross>0).sum()),"SL_kept":int((kept.R_gross<0).sum()),
                    "SL_avoided":int(((~keep)&(z.R_gross<0)).sum()),
                    "TP_lost":int(((~keep)&(z.R_gross>0)).sum()),
                    "net_est_R":round(float(kept.R_net_est.sum()),4)})
    loo_summary={"coin_omissions_tested":len(loo),
                 "min_SL_avoided":min((x["SL_avoided"] for x in loo),default=0),
                 "max_SL_avoided":max((x["SL_avoided"] for x in loo),default=0),
                 "min_TP_kept":min((x["TP_kept"] for x in loo),default=0),
                 "max_TP_kept":max((x["TP_kept"] for x in loo),default=0),
                 "target_at_least_5_SL_avoided_passes_all_single_coin_omissions":all(x["SL_avoided"]>=5 for x in loo),
                 "details":loo}
    source_coverage=[]
    for (sym,tf),series in sorted(cache.items()):
        counts=series["source"].value_counts().to_dict() if "source" in series.columns else {}
        source_coverage.append({"symbol":sym,"tf":tf,"candle_count":len(series),"source_counts":counts})
    summary={"source":"Per-candle source recorded in source_coverage; observed current run uses BingX REST klines where available","source_caveat":"Binance Futures REST returned HTTP 451 in this runner; source_coverage is authoritative for candle provenance. Public exchange candles are not exact private execution fills.","lookahead":"Context uses only candles whose close_ms is strictly before each trade entry. Minute path replay uses only 1m candles fully closed before the tested horizon.","trade_count":len(df),"validation_passed":not errors,"data_errors":errors,"path_replay_validation":path_validation,"path_replay_data_errors":path_errors,"path_sensitivity_scenarios":path_scenarios,"leave_one_coin_out_score_max_54":loo_summary,"source_coverage":source_coverage,"baseline":stats(allmask),"gate_30m":stats(df.keep_30m_gate),"gate_1h_plus_30m":stats(df.keep_1h30m_gate),"gate_4h_plus_1h_plus_30m":stats(df.keep_strict_4h1h30m),"score_filter_sweep_in_sample":score_sweep,"best_score_filter_meeting_5_SL_target_in_sample":best_score_filter,"score_filter_caveat":"Thresholds are selected on the same 28-trade cohort and are exploratory only; they must be confirmed on a later untouched holdout/walk-forward before production use. OHLC sensitivity timeouts are counted as 0R only for the displayed gross-R diagnostic.","definition":"Bull = close > EMA20 > EMA50; bear = close < EMA20 < EMA50; otherwise mixed. Directional veto test only; it does not generate new countertrend SHORT signals. Estimated fees/slippage are approximate in R. Gate statistics are diagnostic only unless validation_passed=true."}
    (dest/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    if errors:
        raise SystemExit(f"REPLAY_INVALID: {len(errors)} data coverage/gap errors; do not use gate metrics.")
if __name__=="__main__": main()
