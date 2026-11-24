"""SAM EDGE AI V1 — research-only ML signal-quality engine."""
from __future__ import annotations
import json, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score, precision_score
from sklearn.pipeline import Pipeline
from strategy_core import add_indicators, LONG_ALLOWLIST, SHORT_ALLOWLIST, ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"sam_edge_ai_v1_results"; OUT.mkdir(exist_ok=True)
START=int(pd.Timestamp("2024-01-01T00:00:00Z").timestamp()*1000)
END=int(pd.Timestamp("2026-11-12T00:00:00Z").timestamp()*1000)
BAR_MS=3600000; LIMIT=1000; HORIZON=48; FEE=.0005; SLIP=.0002
THRESHOLD=.60; MIN_TRAIN=300
FEATURES=["adx","rsi","di_spread","ema_distance_atr","supertrend_distance_atr","atr_pct","body_atr","range_atr","close_location","volume_ratio","return_1","return_3","return_6","return_12","return_24","range_position_20","ema_slope_6_atr","candidate_type"]

def utc(ms): return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()

def fetch_history(ex,symbol):
    since=START; candles={}
    while since<END:
        batch=ex.fetch_ohlcv(symbol,timeframe="1h",since=since,limit=LIMIT)
        if not batch: break
        newest=0
        for row in batch:
            ts=int(row[0])
            if START<=ts<END: candles[ts]=row
            newest=max(newest,ts)
        if newest<since or newest==0: break
        nxt=newest+BAR_MS
        if nxt<=since: break
        since=nxt
        if len(batch)<LIMIT and newest<END-BAR_MS: break
        time.sleep(max(float(ex.rateLimit or 0)/1000,.12))
    rows=[candles[k] for k in sorted(candles)]
    if len(rows)<250: raise RuntimeError(f"Insufficient 1H history: {len(rows)}")
    return pd.DataFrame(rows,columns=["timestamp","open","high","low","close","volume"])

def make_features(df):
    x=df.copy(); atr=x.atr_risk.replace(0,np.nan); rng=(x.high-x.low).replace(0,np.nan)
    x["di_spread"]=x.di_plus-x.di_minus
    x["ema_distance_atr"]=(x.close-x.ema53)/atr
    x["supertrend_distance_atr"]=(x.close-x.supertrend)/atr
    x["atr_pct"]=atr/x.close; x["body_atr"]=(x.close-x.open)/atr
    x["range_atr"]=(x.high-x.low)/atr
    x["close_location"]=((x.close-x.low)/rng).clip(0,1)
    x["volume_ratio"]=x.volume/x.volume.rolling(20,min_periods=10).mean().replace(0,np.nan)
    for n in (1,3,6,12,24): x[f"return_{n}"]=x.close.pct_change(n)
    hi=x.high.shift(1).rolling(20,min_periods=20).max()
    lo=x.low.shift(1).rolling(20,min_periods=20).min()
    x["range_position_20"]=(x.close-lo)/(hi-lo).replace(0,np.nan)
    x["ema_slope_6_atr"]=(x.ema53-x.ema53.shift(6))/atr
    return x

def candidate_events(x,side):
    atr=x.atr_risk; ext=(x.close-x.ema53).abs()/atr.replace(0,np.nan)
    strength=x.adx>20
    if side=="LONG":
        regime=x.st_bull&(x.close>x.ema53)
        touch=(x.low<=x.ema53+.50*atr)&(x.low>=x.ema53-.75*atr)
        pull=regime&strength&touch.shift(1).rolling(8,min_periods=1).max().fillna(0).astype(bool)&(x.close>x.open)&(ext<=2.5)
        prior=x.high.shift(1).rolling(20,min_periods=20).max()
        brk=regime&strength&(x.close>prior+.05*atr)&(x.volume_ratio>=1.2)&(ext<=2.5)
        base=x.long_signal.astype(bool)
    else:
        regime=x.st_bear&(x.close<x.ema53)
        touch=(x.high>=x.ema53-.50*atr)&(x.high<=x.ema53+.75*atr)
        pull=regime&strength&touch.shift(1).rolling(8,min_periods=1).max().fillna(0).astype(bool)&(x.close<x.open)&(ext<=2.5)
        prior=x.low.shift(1).rolling(20,min_periods=20).min()
        brk=regime&strength&(x.close<prior-.05*atr)&(x.volume_ratio>=1.2)&(ext<=2.5)
        base=x.short_signal.astype(bool)
    raw=[]
    for i in range(250,len(x)-HORIZON-1):
        if bool(base.iloc[i]): raw.append((i,0))
        elif bool(pull.iloc[i]): raw.append((i,1))
        elif bool(brk.iloc[i]): raw.append((i,2))
    out=[]; last=-10**9
    for i,k in raw:
        if i-last>8: out.append((i,k)); last=i
    return out

def label_event(x,i,side):
    sig=x.iloc[i]; entbar=x.iloc[i+1]; atr=float(sig.atr_risk)
    if not np.isfinite(atr) or atr<=0: raise ValueError("Invalid ATR")
    raw=float(entbar.open); entry=raw*(1+SLIP if side=="LONG" else 1-SLIP)
    sd=atr*ATR_SL_MULTIPLIER; td=atr*ATR_TP_MULTIPLIER
    stop=entry-sd if side=="LONG" else entry+sd
    target=entry+td if side=="LONG" else entry-td
    exit_px=None; reason="TIME"; last=min(i+HORIZON+1,len(x)-1)
    for j in range(i+1,last+1):
        r=x.iloc[j]; o,h,l=float(r.open),float(r.high),float(r.low)
        if side=="LONG":
            if o<=stop: exit_px=o*(1-SLIP); reason="STOP_GAP"; break
            if o>=target: exit_px=o; reason="TARGET_GAP"; break
            hs,ht=l<=stop,h>=target
        else:
            if o>=stop: exit_px=o*(1+SLIP); reason="STOP_GAP"; break
            if o<=target: exit_px=o; reason="TARGET_GAP"; break
            hs,ht=h>=stop,l<=target
        if hs: exit_px=stop*(1-SLIP if side=="LONG" else 1+SLIP); reason="STOP"; break
        if ht: exit_px=target; reason="TARGET"; break
    if exit_px is None:
        close=float(x.iloc[last].close); exit_px=close*(1-SLIP if side=="LONG" else 1+SLIP)
    qty=1.0/(sd+entry*FEE+stop*FEE+stop*SLIP)
    gross=(exit_px-entry)*qty if side=="LONG" else (entry-exit_px)*qty
    net=gross-entry*qty*FEE-exit_px*qty*FEE
    risk=qty*(sd+entry*FEE+stop*FEE+stop*SLIP)
    return {"label":int(net>0),"realized_R":net/risk if risk else 0.0,"outcome":reason,"signal_time":int(sig.timestamp),"entry_time":int(entbar.timestamp)}

def build_dataset(ex,markets):
    samples=[]; failures=[]
    for n,(base,symbol) in enumerate(markets,1):
        try:
            raw=fetch_history(ex,symbol); feat=make_features(add_indicators(raw))
            for side in ("LONG","SHORT"):
                if (side=="LONG" and base not in LONG_ALLOWLIST) or (side=="SHORT" and base not in SHORT_ALLOWLIST): continue
                for i,kind in candidate_events(feat,side):
                    vals=feat.iloc[i][[k for k in FEATURES if k!="candidate_type"]].to_numpy(dtype=float)
                    if not np.isfinite(vals).all(): continue
                    outcome=label_event(feat,i,side)
                    row={k:float(feat.iloc[i][k]) for k in FEATURES if k!="candidate_type"}
                    row["candidate_type"]=float(kind)
                    row.update({"coin":base,"symbol":symbol,"side":side,"candidate_type_name":("V3.3","PULLBACK","BREAKOUT")[kind],**outcome})
                    samples.append(row)
            print(f"[{n}/{len(markets)}] {symbol} candles={len(raw)} total_samples={len(samples)}",flush=True)
        except Exception as e:
            failures.append({"coin":base,"symbol":symbol,"error":str(e)})
            print(f"[{n}/{len(markets)}] ERROR {symbol}: {e}",flush=True)
    return pd.DataFrame(samples),pd.DataFrame(failures)

def metrics(frame):
    if frame.empty: return {"trades":0,"win_rate_pct":0.0,"pf":0.0,"net_R":0.0,"max_drawdown_pct":0.0}
    r=frame.realized_R.astype(float); wins=r[r>0]; losses=r[r<0]
    eq=r.cumsum(); peak=eq.cummax(); dd=((peak-eq)/peak.replace(0,np.nan)).max()
    return {"trades":int(len(r)),"win_rate_pct":round(float((r>0).mean()*100),2),"pf":round(float(wins.sum()/abs(losses.sum())),4) if len(losses) else (999.0 if len(wins) else 0.0),"net_R":round(float(r.sum()),4),"max_drawdown_pct":round(float(dd)*100,3) if pd.notna(dd) else 0.0}

def recall_fraction(all_rows,selected):
    positives=int(all_rows.label.sum())
    return float(selected.label.sum()/positives) if positives else 0.0

def main():
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":30000,"options":{"defaultType":"swap","defaultSubType":"linear"}})
    ex.load_markets(); bases=sorted(LONG_ALLOWLIST|SHORT_ALLOWLIST); markets=[]
    for base in bases:
        sym=next((s for s,m in ex.markets.items() if m.get("base")==base and m.get("quote")=="USDT" and m.get("swap") and m.get("linear") and m.get("active") is not False),None)
        if sym: markets.append((base,sym))
    data,failures=build_dataset(ex,markets)
    data.to_csv(OUT/"candidate_dataset.csv",index=False); failures.to_csv(OUT/"data_failures.csv",index=False)
    if data.empty: raise RuntimeError("No candidate samples; inspect data_failures.csv")
    data["signal_dt"]=pd.to_datetime(data.signal_time,unit="ms",utc=True)
    train=data[data.signal_dt<"2025-12-30"]
    val=data[(data.signal_dt>="2026-01-02")&(data.signal_dt<"2026-06-29")]
    test=data[(data.signal_dt>="2026-07-02")&(data.signal_dt<"2026-11-12")]
    if len(train)<MIN_TRAIN or train.label.nunique()<2: raise RuntimeError(f"Insufficient training set n={len(train)} classes={train.label.nunique()}")
    model=Pipeline([("imputer",SimpleImputer(strategy="median")),("rf",RandomForestClassifier(n_estimators=400,max_depth=8,min_samples_leaf=15,class_weight="balanced_subsample",random_state=33,n_jobs=-1))])
    model.fit(train[FEATURES],train.label)
    positive_col=list(model.named_steps["rf"].classes_).index(1)
    def evaluate(part,name):
        if part.empty: return {"window":name,"samples":0,"all_candidates":metrics(part),"ai_filtered":metrics(part)}
        scored=part.copy(); scored["ai_score"]=model.predict_proba(part[FEATURES])[:,positive_col]
        chosen=scored[scored.ai_score>=THRESHOLD]
        auc=round(float(roc_auc_score(scored.label,scored.ai_score)),4) if scored.label.nunique()==2 else None
        return {"window":name,"samples":len(scored),"positive_rate_pct":round(float(scored.label.mean()*100),2),"roc_auc":auc,"fixed_threshold":THRESHOLD,"all_candidates":metrics(scored),"ai_filtered":metrics(chosen),"ai_precision_pct":round(float(precision_score(chosen.label,np.ones(len(chosen)),zero_division=0)*100),2) if len(chosen) else 0.0,"ai_recall_pct":round(recall_fraction(scored,chosen)*100,2)},scored
    val_result,val_scored=evaluate(val,"VALIDATION_2026-01_to_2026-06")
    test_result,test_scored=evaluate(test,"OOS_2026-07_to_2026-11-11")
    if not test_scored.empty: test_scored.to_csv(OUT/"oos_scored_candidates.csv",index=False)
    report={"engine":"SAM EDGE AI V1 research prototype","data_window_utc":[utc(START),utc(END)],"markets_requested":len(bases),"markets_loaded":len(markets),"samples":{"train":len(train),"validation":len(val),"oos":len(test)},"positive_rate_pct":{"train":round(float(train.label.mean()*100),2),"validation":round(float(val.label.mean()*100),2) if len(val) else None,"oos":round(float(test.label.mean()*100),2) if len(test) else None},"validation":val_result,"out_of_sample":test_result,"limitations":["Independent per-coin events, not simultaneous portfolio replay.","Funding excluded; fee 0.05% per side and 2 bps adverse stop slippage approximated.","V3.3 directional allowlist inherited; not point-in-time universe.","Fixed 1.2 ATR stop, 3.5 ATR target and 48-bar horizon are research assumptions.","Model scores are not calibrated real-world probabilities.","No live execution, Telegram, or paper-state integration."]}
    (OUT/"summary.json").write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2),flush=True)

if __name__=="__main__": main()
