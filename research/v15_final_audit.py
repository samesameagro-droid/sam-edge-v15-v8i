from __future__ import annotations

import csv
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core_engine_v15 import enrich, signal_mask, trade_levels, RR, STRUCTURE_STOP_ATR_BUFFER, STRUCTURE_STOP_MIN_ATR, STRUCTURE_STOP_MAX_ATR

ROOT = Path(".")
JOURNAL = ROOT / "v15_forward_test_master_journal.csv"
OUT = ROOT / "v15_final_audit"
OUT.mkdir(exist_ok=True)
START = pd.Timestamp("2026-09-14T00:00:00Z")
END = pd.Timestamp("2026-09-21T12:00:00Z")
BTC_START = pd.Timestamp("2026-09-14T00:00:00Z")
BTC_END = pd.Timestamp("2026-09-21T12:00:00Z")
BINGX = "https://open-api.bingx.com"
SESSION = requests.Session()
RETRY = Retry(total=5, connect=5, read=3, backoff_factor=0.4,
              status_forcelist=(429, 500, 502, 503, 504),
              allowed_methods=frozenset(["GET"]))
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY, pool_connections=20, pool_maxsize=20))
SESSION.headers.update({"X-SOURCE-KEY": "BX-AI-SKILL", "User-Agent": "SAM-EDGE-V15-FINAL-AUDIT/1.0"})
INTERVAL_MS = {"5m":300000, "15m":900000}

def bingx_klines(asset: str, interval: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    symbol = asset.replace("/USDT:USDT", "-USDT").replace("/", "-").upper()
    step = INTERVAL_MS[interval]
    cur = int(start.timestamp()*1000)
    end_ms = int(end.timestamp()*1000)
    rows = []
    seen = set()
    while cur <= end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000, "timestamp": int(time.time()*1000)}
        payload = None
        last_err = None
        for path in ("/openApi/swap/v3/quote/klines", "/openApi/swap/v2/quote/klines"):
            try:
                r = SESSION.get(BINGX + path, params=params, timeout=20)
                r.raise_for_status()
                j = r.json()
                if int(j.get("code", -1)) != 0:
                    raise RuntimeError(str(j.get("msg")))
                payload = j.get("data", [])
                break
            except Exception as e:
                last_err = e
        if payload is None:
            raise last_err
        page = []
        for q in payload or []:
            if isinstance(q, (list, tuple)) and len(q) >= 6:
                try:
                    row = [int(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[5])]
                except Exception:
                    continue
            elif isinstance(q, dict):
                try:
                    row = [int(q.get("openTime") or q.get("time") or q.get("timestamp")),
                           float(q["open"]), float(q["high"]), float(q["low"]), float(q["close"]), float(q.get("volume", 0))]
                except Exception:
                    continue
            else:
                continue
            if row[0] not in seen:
                seen.add(row[0]); page.append(row)
        page.sort(key=lambda z: z[0])
        if not page:
            break
        rows.extend(page)
        nxt = page[-1][0] + step
        if nxt <= cur:
            break
        cur = nxt
    if not rows:
        return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
    df = pd.DataFrame(rows, columns=["ms","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    return df.drop(columns="ms").sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

def load_journal():
    return pd.read_csv(JOURNAL, parse_dates=["signal_time","closed_at"]).sort_values("trade_no").reset_index(drop=True)

def rstats(df):
    if len(df) == 0:
        return {"trades":0,"wins":0,"losses":0,"wr_pct":0.0,"net_R":0.0,"pf":None,"expectancy_R":0.0,"max_dd_R":0.0}
    r = df["R"].astype(float).to_numpy()
    w = int((r > 0).sum()); l = int((r < 0).sum())
    gp = float(r[r>0].sum()); gl = float(-r[r<0].sum())
    eq = np.cumsum(r); peak = np.maximum.accumulate(eq); dd = eq - peak
    return {"trades":len(r),"wins":w,"losses":l,"wr_pct":round(100*w/len(r),2),"net_R":round(float(r.sum()),4),
            "pf":round(gp/gl,4) if gl else None,"expectancy_R":round(float(r.mean()),4),"max_dd_R":round(float(dd.min()),4)}

def simulate_path(bars5, side, entry, sl, tp, start, max_end):
    if bars5.empty: return None
    q = bars5[(bars5.timestamp >= start) & (bars5.timestamp <= max_end)]
    if q.empty: return None
    for _, b in q.iterrows():
        hs = float(b.high) >= sl if side == "LONG" else float(b.high) >= sl
        # For long sl is below; for short sl is above.
        hit_sl = float(b.low) <= sl if side == "LONG" else float(b.high) >= sl
        hit_tp = float(b.high) >= tp if side == "LONG" else float(b.low) <= tp
        if hit_sl and hit_tp:
            return "SL", b.timestamp
        if hit_sl:
            return "SL", b.timestamp
        if hit_tp:
            return "TP", b.timestamp
    return None

def level_scaled(x, i, side_int, atr_factor=1.0, rr=RR, stop_buffer=STRUCTURE_STOP_ATR_BUFFER):
    entry = float(x.close.iloc[i]); atr = float(x.atr.iloc[i]) * atr_factor
    if not np.isfinite(atr) or atr <= 0: return None
    if side_int == 1:
        anchor = float(x.low.iloc[max(0, i-8):i].min())
        sl = anchor - stop_buffer*atr
    else:
        anchor = float(x.high.iloc[max(0, i-8):i].max())
        sl = anchor + stop_buffer*atr
    risk = entry-sl if side_int == 1 else sl-entry
    if risk < STRUCTURE_STOP_MIN_ATR*atr:
        sl = entry-STRUCTURE_STOP_MIN_ATR*atr if side_int == 1 else entry+STRUCTURE_STOP_MIN_ATR*atr
    elif risk > STRUCTURE_STOP_MAX_ATR*atr:
        sl = entry-STRUCTURE_STOP_MAX_ATR*atr if side_int == 1 else entry+STRUCTURE_STOP_MAX_ATR*atr
    risk = entry-sl if side_int == 1 else sl-entry
    if risk <= 0 or not np.isfinite(risk): return None
    tp = entry+rr*risk if side_int == 1 else entry-rr*risk
    return entry, float(sl), float(tp), float(risk)

def prepare_coin(asset):
    # Enough warmup for the 200-window 4H ADX percentile while keeping the request finite.
    return asset, bingx_klines(asset, "15m", START-pd.Timedelta(days=38), END)

def main():
    d = load_journal()
    assets = sorted(set(d.coin))
    data = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(prepare_coin, a): a for a in assets}
        for f in as_completed(futs):
            a = futs[f]
            try:
                _, raw = f.result()
                if raw.empty: raise RuntimeError("no BingX 15m data")
                data[a] = {"raw15": raw, "x15": enrich(raw)}
            except Exception as e:
                errors[a] = f"{type(e).__name__}: {e}"

    # 5m windows aggregated by coin for exact tracking sensitivity.
    windows = {}
    for a, g in d.groupby("coin"):
        windows[a] = (g.signal_time.min()-pd.Timedelta(hours=1), g.closed_at.max()+pd.Timedelta(hours=1))
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(bingx_klines, a, "5m", s, e): a for a,(s,e) in windows.items() if a in data}
        for f in as_completed(futs):
            a=futs[f]
            try:
                data[a]["bars5"] = f.result()
            except Exception as e:
                errors[a+":5m"] = f"{type(e).__name__}: {e}"

    # Per-trade robustness: ATR +/-10%, RR neighbors, stop-buffer neighbors, strict one-bar delay.
    rows=[]
    for _, t in d.iterrows():
        a=t.coin; side=t.side; side_i=1 if side=="LONG" else -1
        rec={"trade_no":int(t.trade_no),"coin":a,"side":side,"base_R":float(t.R)}
        if a not in data:
            rows.append(rec); continue
        x=data[a]["x15"]; bars5=data[a].get("bars5", pd.DataFrame())
        ts=pd.Timestamp(t.signal_time)
        ix=np.where(x.timestamp.to_numpy()==ts.to_datetime64())[0]
        if len(ix)==0:
            # closest exact-or-next timestamp, with strict tolerance
            dif=(x.timestamp-ts).abs()
            j=int(dif.argmin())
            if dif.iloc[j] > pd.Timedelta("2m"):
                rows.append(rec); continue
            i=j
        else:
            i=int(ix[0])
        base_levels=trade_levels(x,i,side_i,"STRUCTURE")
        if base_levels is None:
            rows.append(rec); continue
        max_end=pd.Timestamp(t.closed_at)+pd.Timedelta(hours=1)
        for label,factor in [("atr_m10",0.90),("atr_p10",1.10)]:
            lv=level_scaled(x,i,side_i,atr_factor=factor)
            sim=simulate_path(bars5,side,*lv[:3],pd.Timestamp(ts)+pd.Timedelta(minutes=15),max_end) if lv else None
            rec[label]=sim[0] if sim else "NO_RESULT"
        for label,rr in [("rr_115",1.15),("rr_135",1.35)]:
            entry,sl,tp,risk=base_levels
            tp2=entry+rr*risk if side_i==1 else entry-rr*risk
            sim=simulate_path(bars5,side,entry,sl,tp2,pd.Timestamp(ts)+pd.Timedelta(minutes=15),max_end)
            rec[label]=sim[0] if sim else "NO_RESULT"
        for label,buf in [("stopbuf_060",0.60),("stopbuf_070",0.70)]:
            lv=level_scaled(x,i,side_i,atr_factor=1.0,stop_buffer=buf)
            sim=simulate_path(bars5,side,*lv[:3],pd.Timestamp(ts)+pd.Timedelta(minutes=15),max_end) if lv else None
            rec[label]=sim[0] if sim else "NO_RESULT"
        # Strict 1-bar delay: require the same signal to remain valid.
        i2=i+1
        delayed="EXPIRED"
        if i2 < len(x):
            lm,sm=signal_mask(x)
            still=bool(lm.iloc[i2]) if side_i==1 else bool(sm.iloc[i2])
            if still:
                lv=trade_levels(x,i2,side_i,"STRUCTURE")
                if lv:
                    sim=simulate_path(bars5,side,*lv[:3],pd.Timestamp(x.timestamp.iloc[i2])+pd.Timedelta(minutes=15),max_end)
                    delayed=sim[0] if sim else "NO_RESULT"
        rec["delay_1bar_strict"]=delayed

        # Execution-delay robustness: shift the actual V15 decision by one 15m bar
        # without requiring the signal to persist, recomputing the normal V15
        # STRUCTURE stop/TP from the delayed candle.
        delayed_exec="EXPIRED"
        if i2 < len(x):
            lv2=trade_levels(x,i2,side_i,"STRUCTURE")
            if lv2:
                sim=simulate_path(bars5,side,*lv2[:3],pd.Timestamp(x.timestamp.iloc[i2])+pd.Timedelta(minutes=15),max_end)
                delayed_exec=sim[0] if sim else "NO_RESULT"
        rec["delay_1bar_execution"]=delayed_exec
        rows.append(rec)

    sens=pd.DataFrame(rows)
    sens.to_csv(OUT/"sensitivity_by_trade.csv",index=False)

    def outcome_metric(col):
        q=sens[sens[col].isin(["TP","SL"])]
        if q.empty:return rstats(pd.DataFrame({"R":[]}))
        rrmap={"TP":1.25,"SL":-1}
        return rstats(pd.DataFrame({"R":[rrmap[x] for x in q[col]]}))

    sensitivity={c:outcome_metric(c) for c in ["atr_m10","atr_p10","rr_115","rr_135","stopbuf_060","stopbuf_070","delay_1bar_strict","delay_1bar_execution"]}

    # Leave-one-coin/core concentration analysis from the authoritative 53 closed events.
    overall=rstats(d)
    loo_coin=[]
    for coin in sorted(d.coin.unique()):
        s=rstats(d[d.coin!=coin]); loo_coin.append({"removed_coin":coin,**s})
    loo_core=[]
    for core in sorted(d.core.unique()):
        s=rstats(d[d.core!=core]); loo_core.append({"removed_core":core,**s})
    pd.DataFrame(loo_coin).sort_values("net_R").to_csv(OUT/"leave_one_coin.csv",index=False)
    pd.DataFrame(loo_core).to_csv(OUT/"leave_one_core.csv",index=False)

    # True event-time portfolio replay/concurrency audit.
    ev=[]
    for _,t in d.iterrows():
        ev.append((pd.Timestamp(t.signal_time),+1,f"OPEN {t.coin}",int(t.trade_no)))
        ev.append((pd.Timestamp(t.closed_at),-1,f"CLOSE {t.coin}",int(t.trade_no)))
    ev.sort(key=lambda z:(z[0],z[1]))
    active=0; peak=0; peak_ts=None
    for ts,delta,_,_ in ev:
        active+=delta
        if active>peak: peak=active; peak_ts=ts
    eq=0; peak_eq=0; maxdd=0
    for _,t in d.sort_values("closed_at").iterrows():
        eq+=float(t.R); peak_eq=max(peak_eq,eq); maxdd=max(maxdd,peak_eq-eq)
    event_replay={"closed_trades":len(d),"max_concurrent":peak,"max_concurrent_time":peak_ts.isoformat() if peak_ts is not None else None,
                  "event_time_max_dd_R":round(maxdd,4),"journal_net_R":overall["net_R"]}

    # BTC context on BingX 15m/5m, aligned to each trade.
    btc15=bingx_klines("BTC/USDT:USDT","15m",BTC_START,BTC_END)
    btc5=bingx_klines("BTC/USDT:USDT","5m",BTC_START,BTC_END)
    btc=[]; mismatch=0
    for _,t in d.iterrows():
        ts=pd.Timestamp(t.signal_time); side=t.side
        p=btc15
        if p.empty: continue
        # use nearest candle at/just before signal
        prior=p[p.timestamp<=ts]
        if prior.empty: continue
        b0=prior.iloc[-1]; c0=float(b0.close)
        def ret(minutes):
            target=ts+pd.Timedelta(minutes=minutes)
            q=p[p.timestamp>=target]
            if q.empty:return np.nan
            return float(q.iloc[0].close)/c0-1
        def pre(minutes):
            target=ts-pd.Timedelta(minutes=minutes)
            q=p[p.timestamp<=target]
            if q.empty:return np.nan
            return c0/float(q.iloc[-1].close)-1
        pre1=pre(60); pre4=pre(240); aft1=ret(60); aft4=ret(240)
        endq=p[p.timestamp<=pd.Timestamp(t.closed_at)]
        full=float(endq.iloc[-1].close)/c0-1 if not endq.empty else np.nan
        hold5=btc5[(btc5.timestamp>=ts)&(btc5.timestamp<=pd.Timestamp(t.closed_at))]
        if not hold5.empty:
            if side=="LONG":
                adverse=float(hold5.low.min())/c0-1
                favorable=float(hold5.high.max())/c0-1
            else:
                adverse=1-float(hold5.high.max())/c0
                favorable=1-float(hold5.low.min())/c0
        else: adverse=favorable=np.nan
        btc.append({"trade_no":int(t.trade_no),"coin":t.coin,"side":side,"R":float(t.R),
                    "btc_pre1h":pre1,"btc_pre4h":pre4,"btc_after1h":aft1,"btc_after4h":aft4,
                    "btc_trade_return":full,"btc_adverse":adverse,"btc_favorable":favorable})
    btcdf=pd.DataFrame(btc)
    btcdf.to_csv(OUT/"btc_by_trade.csv",index=False)

    def btc_group(mask):
        q=btcdf.loc[mask.reindex(btcdf.index, fill_value=False)]
        if q.empty:return {"n":0}
        return {"n":len(q),"wins":int((q.R>0).sum()),"losses":int((q.R<0).sum()),
                "wr_pct":round(100*(q.R>0).mean(),2),"net_R":round(float(q.R.sum()),2)}
    # LONG-focused because 50/53 trades were LONG.
    long=btcdf[btcdf.side=="LONG"].copy()
    btc_groups={}
    if not long.empty:
        btc_groups["pre1h_drop_gt_0.5pct"]=btc_group(long.btc_pre1h < -0.005)
        btc_groups["pre1h_flat_or_up"]=btc_group(long.btc_pre1h >= -0.005)
        btc_groups["after1h_drop_gt_0.5pct"]=btc_group(long.btc_after1h < -0.005)
        btc_groups["after1h_not_drop"]=btc_group(long.btc_after1h >= -0.005)
        btc_groups["trade_btc_return_negative"]=btc_group(long.btc_trade_return < 0)
        btc_groups["trade_btc_return_nonnegative"]=btc_group(long.btc_trade_return >= 0)
    slbtc=btcdf[btcdf.R<0]
    tpbtc=btcdf[btcdf.R>0]
    btc_summary={
        "n_aligned":len(btcdf),
        "missing_alignment":len(d)-len(btcdf),
        "mean_pre1h_wins":round(float(tpbtc.btc_pre1h.mean()),5) if not tpbtc.empty else None,
        "mean_pre1h_losses":round(float(slbtc.btc_pre1h.mean()),5) if not slbtc.empty else None,
        "mean_after1h_wins":round(float(tpbtc.btc_after1h.mean()),5) if not tpbtc.empty else None,
        "mean_after1h_losses":round(float(slbtc.btc_after1h.mean()),5) if not slbtc.empty else None,
        "long_groups":btc_groups,
    }

    # Confidence interval for observed WR and simple binomial test vs 44.44% break-even.
    n=len(d); w=int((d.R>0).sum()); p=w/n; z=1.96
    den=1+z*z/n; cen=(p+z*z/(2*n))/den; half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    p0=4/9
    def logchoose(nn,kk):
        return math.lgamma(nn+1)-math.lgamma(kk+1)-math.lgamma(nn-kk+1)
    pv=sum(math.exp(logchoose(n,k)+k*math.log(p0)+(n-k)*math.log(1-p0)) for k in range(w,n+1))
    uncertainty={"wr_wilson_95":[round(cen-half,4),round(cen+half,4)],"break_even_wr":round(p0,4),"one_sided_binomial_p":pv}

    summary={"generated_at":datetime.now(timezone.utc).isoformat(),"source":"GitHub main production state/journal + BingX public klines",
             "overall":overall,"first_50":rstats(d.iloc[:50]),"post_50":rstats(d.iloc[50:]),
             "event_replay":event_replay,"sensitivity":sensitivity,"leave_one_coin":loo_coin,
             "leave_one_core":loo_core,"btc":btc_summary,"uncertainty":uncertainty,
             "data_errors":errors,
             "notes":["Sensitivity tests are robustness studies on the exact 53-trade event set; they do not change production V15.",
                      "BTC analysis is association/context, not causal proof. It uses BingX BTC candles aligned to each trade.",
                      "Strict 1-bar delay requires the same V15 side signal to still be valid on the next 15m bar.",
                      "Execution-delay test shifts the decision one 15m bar later and recomputes the normal V15 STRUCTURE levels."]}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
    print(json.dumps({"overall":overall,"event_replay":event_replay,"sensitivity":sensitivity,"btc":btc_summary,"uncertainty":uncertainty,"data_errors":errors},indent=2,default=str))

if __name__=="__main__":
    main()
