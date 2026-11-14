"""Isolated ST-ADX V3.3 top-150 BingX USDT perpetual research.
Does not read/write paper_state.json and never submits orders.
"""
from __future__ import annotations
import csv, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt
import numpy as np
import pandas as pd
from strategy_core import (
    add_indicators, LONG_ALLOWLIST, SHORT_ALLOWLIST,
    ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER,
)
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research_150_v33"
OUT.mkdir(exist_ok=True)
START = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp()*1000)
END = int(pd.Timestamp("2026-11-11T00:00:00Z").timestamp()*1000)
BAR = 3_600_000
LIMIT = 1000
TOP_N = 150
FEE, SLIP, RISK, NOTIONAL_CAP = .0005, .0002, .01, .45
MIN_WARMUP = 250
# Report all results; these are transparent screening flags, not automatic trade approval.
MIN_TRADES = 12
MIN_OOS_TRADES = 4
MIN_PF = 1.30
MIN_OOS_PF = 1.10
BASELINE = LONG_ALLOWLIST | SHORT_ALLOWLIST

def utc(ms): return datetime.fromtimestamp(ms/1000, timezone.utc).isoformat()

def markets_top150(ex):
    ex.load_markets()
    tickers = ex.fetch_tickers()
    rows=[]
    for sym,m in ex.markets.items():
        if not (m.get("swap") and m.get("linear") and m.get("quote")=="USDT" and m.get("active") is not False):
            continue
        t=tickers.get(sym) or {}
        qv=t.get("quoteVolume")
        if qv is None:
            info=t.get("info") or {}
            qv=info.get("quoteVolume") or info.get("quote_volume") or 0
        try: qv=float(qv or 0)
        except (TypeError,ValueError): qv=0
        if qv<=0: continue
        base=m.get("base")
        if not base or base in {"USDC","USDT","BUSD","DAI","FDUSD","TUSD"}: continue
        rows.append({"base":base,"symbol":sym,"quote_volume_24h":qv})
    # Deduplicate base; keep the highest-volume linear USDT swap.
    best={}
    for r in rows:
        if r["base"] not in best or r["quote_volume_24h"]>best[r["base"]]["quote_volume_24h"]:
            best[r["base"]]=r
    ranked=sorted(best.values(),key=lambda x:x["quote_volume_24h"],reverse=True)[:TOP_N]
    if len(ranked)<TOP_N:
        print(f"WARNING: only {len(ranked)} eligible markets found",flush=True)
    return ranked

def fetch_history(ex,sym):
    since=START
    got={}
    while since<END:
        batch=ex.fetch_ohlcv(sym,timeframe="1h",since=since,limit=LIMIT)
        if not batch: break
        newest=0
        for row in batch:
            ts=int(row[0])
            if START<=ts<END: got[ts]=row
            newest=max(newest,ts)
        if newest<since or newest==0: break
        next_since=newest+BAR
        if next_since<=since: break
        since=next_since
        if len(batch)<LIMIT and newest<END-BAR: break
        time.sleep(ex.rateLimit/1000 if ex.rateLimit else .15)
    rows=[got[k] for k in sorted(got)]
    if len(rows)<MIN_WARMUP: raise RuntimeError(f"{sym}: insufficient history ({len(rows)} bars)")
    df=pd.DataFrame(rows,columns=["timestamp","open","high","low","close","volume"])
    gaps=int((df.timestamp.diff().dropna()!=BAR).sum())
    return df,gaps

def bracket(side, o,h,l, stop,target):
    if side=="LONG":
        if o<=stop:return o,"STOP_GAP"
        if o>=target:return o,"TARGET_GAP"
        hs,ht=l<=stop,h>=target
    else:
        if o>=stop:return o,"STOP_GAP"
        if o<=target:return o,"TARGET_GAP"
        hs,ht=h>=stop,l<=target
    if hs and ht:
        hf=abs(o-h)<abs(o-l)
        if side=="LONG": return (target,"TARGET_BOTH_TOUCHED") if hf else (stop,"STOP_BOTH_TOUCHED")
        return (stop,"STOP_BOTH_TOUCHED") if hf else (target,"TARGET_BOTH_TOUCHED")
    if hs:return stop,"STOP"
    if ht:return target,"TARGET"
    return None

def simulate(df, side):
    # Same signal timing, entry slippage, fees, bracket ambiguity and next-bar ST flip
    # conventions as the V3.3 forward runner. Per-coin stats use 1% risk sizing.
    equity=100.0; pos=None; cooldown_until=-1; trades=[]
    for i in range(MIN_WARMUP,len(df)-1):
        r=df.iloc[i]; nxt=df.iloc[i+1]
        if pos:
            if pos["pending_flip"]:
                raw=float(r.open); reason="ST_FLIP"
            else:
                hit=bracket(side,float(r.open),float(r.high),float(r.low),pos["stop"],pos["target"])
                if hit: raw,reason=hit
                else:
                    if (side=="LONG" and int(r.st_direction)>0) or (side=="SHORT" and int(r.st_direction)<0):
                        pos["pending_flip"]=True
                    continue
            slip=0.0 if reason.startswith("TARGET") else SLIP
            px=raw*(1-slip if side=="LONG" else 1+slip)
            gross=(px-pos["entry"])*pos["qty"] if side=="LONG" else (pos["entry"]-px)*pos["qty"]
            fee_out=abs(px*pos["qty"])*FEE
            net=gross-pos["fee_in"]-fee_out
            equity+=net
            risk_cash=pos["risk_cash"]
            trades.append({"side":side,"entry_ts":pos["ts"],"exit_ts":int(r.timestamp),"entry":pos["entry"],"exit":px,"stop":pos["stop"],"target":pos["target"],"reason":reason,"net":net,"R":net/risk_cash if risk_cash else 0,"equity":equity})
            pos=None; cooldown_until=i+1
        if pos is None and i>=cooldown_until:
            sig=bool(r.long_signal if side=="LONG" else r.short_signal)
            if not sig: continue
            atr=float(r.atr_risk)
            if not np.isfinite(atr) or atr<=0: continue
            raw=float(nxt.open)
            entry=raw*(1+SLIP if side=="LONG" else 1-SLIP)
            dist=atr*ATR_SL_MULTIPLIER
            stop=entry-dist if side=="LONG" else entry+dist
            target=entry+atr*ATR_TP_MULTIPLIER if side=="LONG" else entry-atr*ATR_TP_MULTIPLIER
            unit=abs(entry-stop)+entry*FEE+abs(stop*(1-SLIP if side=="LONG" else 1+SLIP))*FEE+abs(stop*(1-SLIP if side=="LONG" else 1+SLIP)-stop)
            qty=min(equity*RISK/unit,equity*NOTIONAL_CAP/entry)
            if qty<=0: continue
            pos={"ts":int(nxt.timestamp),"entry":entry,"stop":stop,"target":target,"qty":qty,"fee_in":entry*qty*FEE,"risk_cash":qty*unit,"pending_flip":False}
    # Deliberately do not force-close an open trade at dataset end.
    return pd.DataFrame(trades), (pos is not None)

def metrics(t):
    if t.empty:return {"trades":0,"wins":0,"win_rate_pct":0.0,"pf":0.0,"net_usdt":0.0,"net_R":0.0,"max_dd_pct":0.0}
    wins=t[t.net>0]; losses=t[t.net<0]
    gp=float(wins.net.sum()); gl=abs(float(losses.net.sum()))
    eq=100+ t.net.cumsum()
    peak=eq.cummax()
    dd=((peak-eq)/peak.replace(0,np.nan)).max()
    return {"trades":int(len(t)),"wins":int(len(wins)),"win_rate_pct":round(len(wins)/len(t)*100,2),"pf":round(gp/gl,4) if gl else (999.0 if gp else 0.0),"net_usdt":round(float(t.net.sum()),4),"net_R":round(float(t["R"].sum()),4),"max_dd_pct":round(float(dd)*100,3) if pd.notna(dd) else 0.0}

def side_result(base,side,trades,open_at_end):
    if trades.empty:
        return {"coin":base,"side":side,"classification": "NO_SIGNAL","open_at_end":open_at_end,
                **metrics(trades),"is_trades":0,"is_pf":0,"oos_trades":0,"oos_pf":0,"screen_flag":"INSUFFICIENT_SAMPLE"}
    entry=pd.to_datetime(trades.entry_ts,unit="ms",utc=True)
    # Predeclared chronological split: IS through Aug; OOS Sep 1-Nov 10.
    is_t=trades[entry<pd.Timestamp("2026-09-01T00:00:00Z")]
    oos_t=trades[entry>=pd.Timestamp("2026-09-01T00:00:00Z")]
    m=metrics(trades); mi=metrics(is_t); mo=metrics(oos_t)
    flag="PASS_SCREEN" if m["trades"]>=MIN_TRADES and m["pf"]>=MIN_PF and mo["trades"]>=MIN_OOS_TRADES and mo["pf"]>=MIN_OOS_PF and m["net_R"]>0 else "REVIEW"
    return {"coin":base,"side":side,"classification":"CANDIDATE" if flag=="PASS_SCREEN" else "REVIEW",
            "open_at_end":open_at_end,**m,"is_trades":mi["trades"],"is_pf":mi["pf"],
            "oos_trades":mo["trades"],"oos_pf":mo["pf"],"oos_net_R":mo["net_R"],"screen_flag":flag}

def main():
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":30000,"options":{"defaultType":"swap","defaultSubType":"linear"}})
    try: snapshot_ms=ex.fetch_time() or ex.milliseconds()
    except Exception: snapshot_ms=ex.milliseconds()
    universe=markets_top150(ex)
    pd.DataFrame(universe).to_csv(OUT/"universe_snapshot.csv",index=False)
    results=[]; failures=[]; gaps_total=0
    print(f"SNAPSHOT {utc(snapshot_ms)} | eligible={len(universe)} | window={utc(START)} to {utc(END)}",flush=True)
    for n,item in enumerate(universe,1):
        base,sym=item["base"],item["symbol"]
        try:
            df,gaps=fetch_history(ex,sym); gaps_total+=gaps
            ind=add_indicators(df)
            for side in ("LONG","SHORT"):
                trades,open_end=simulate(ind,side)
                if not trades.empty:
                    trades.insert(0,"coin",base)
                    trades.to_csv(OUT/f"trades_{base}_{side.lower()}.csv",index=False)
                results.append(side_result(base,side,trades,open_end))
            print(f"[{n}/{len(universe)}] {sym} bars={len(df)} gaps={gaps} done",flush=True)
        except Exception as e:
            failures.append({"coin":base,"symbol":sym,"error":str(e)})
            print(f"[{n}/{len(universe)}] ERROR {sym}: {e}",flush=True)
    r=pd.DataFrame(results)
    # Direction is assigned only where that side passes the predeclared screen.
    classes=[]
    for base,g in r.groupby("coin"):
        good=set(g.loc[g.screen_flag=="PASS_SCREEN","side"])
        label="BOTH" if good=={"LONG","SHORT"} else "LONG" if good=={"LONG"} else "SHORT" if good=={"SHORT"} else "REJECT"
        classes.append({"coin":base,"classification":label,
                        "long_flag":str(g.loc[g.side=="LONG","screen_flag"].iloc[0]),
                        "short_flag":str(g.loc[g.side=="SHORT","screen_flag"].iloc[0])})
    c=pd.DataFrame(classes)
    r.to_csv(OUT/"directional_results.csv",index=False)
    c.to_csv(OUT/"coin_classification.csv",index=False)
    pd.DataFrame(failures).to_csv(OUT/"data_failures.csv",index=False)
    summary={"strategy":"ST-ADX V3.3","universe_snapshot_utc":utc(snapshot_ms),
             "universe_requested":TOP_N,"universe_loaded":len(universe),
             "history_start_utc":utc(START),"history_end_exclusive_utc":utc(END),
             "bars_expected_per_coin_approx":int((END-START)/BAR),
             "total_gap_candles":gaps_total,"data_failures":len(failures),
             "long_only":int((c.classification=="LONG").sum()),
             "short_only":int((c.classification=="SHORT").sum()),
             "both":int((c.classification=="BOTH").sum()),
             "reject":int((c.classification=="REJECT").sum()),
             "baseline_23_in_snapshot":int(c.coin.isin(BASELINE).sum()),
             "screen":{"min_trades":MIN_TRADES,"min_oos_trades":MIN_OOS_TRADES,"min_pf":MIN_PF,"min_oos_pf":MIN_OOS_PF},
             "notes":["Current top-150 snapshot introduces survivorship/selection bias when applied retrospectively.",
                      "Per-coin directional screening is not a combined portfolio replay.",
                      "Open positions at dataset end are reported but not force-closed.",
                      "Funding is excluded; paper/live execution differences remain."]}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2),flush=True)
    if failures: print("WARNING: data failures exist; inspect data_failures.csv",flush=True)

if __name__=="__main__": main()
