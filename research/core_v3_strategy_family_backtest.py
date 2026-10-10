#!/usr/bin/env python3
"""Research-only comparison of four SAM EDGE strategy families.

This is a separate experiment; it does not import or modify the live V2 signal
logic. Uses completed 15m bars and completed HTF features from core_engine_v15.enrich.
Outputs cost-aware event-driven replay with 96-bar time stop. Research only.
"""
from __future__ import annotations
import argparse, json, sys, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core_engine_v15 import enrich, STRUCTURE_STOP_LOOKBACK, STRUCTURE_STOP_ATR_BUFFER

FEE_BPS_PER_SIDE = 5.0       # explicit baseline assumption; replace with account tier
SLIPPAGE_BPS_PER_SIDE = 2.0  # explicit assumption; stress-tested separately
RR = 1.50
MAX_HOLD_BARS = 96
MAX_ACTIVE = 5
COOLDOWN_BARS = 4

def read_ohlcv(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names: raise ValueError(f"No CSV in {path}")
            raw = pd.read_csv(z.open(names[0]))
    else:
        raw = pd.read_csv(path)
    cols = {str(c).lower(): c for c in raw.columns}
    needed = ["open", "high", "low", "close", "volume"]
    if any(c not in cols for c in needed): raise ValueError(f"Missing OHLCV in {path}")
    out = raw[[cols[c] for c in needed]].copy()
    if "timestamp" in cols:
        out["timestamp"] = pd.to_datetime(raw[cols["timestamp"]], utc=True, errors="coerce")
    elif "open_time" in cols:
        out["timestamp"] = pd.to_datetime(raw[cols["open_time"]], unit="ms", utc=True, errors="coerce")
    else: raise ValueError(f"Missing timestamp/open_time in {path}")
    for c in needed: out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.dropna().drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

def load_data(root: Path) -> dict[str, pd.DataFrame]:
    frames = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(list(folder.glob("*.zip")) + list(folder.glob("*.csv")))
        if not files: continue
        raw = pd.concat([read_ohlcv(p) for p in files], ignore_index=True)
        raw = raw.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        if len(raw) < 3300: continue
        x = enrich(raw)
        # Bollinger features use only the current completed candle and trailing history.
        mid = x["close"].rolling(20).mean()
        sd = x["close"].rolling(20).std(ddof=0)
        x["bb_mid"] = mid
        x["bb_upper"] = mid + 2.0 * sd
        x["bb_lower"] = mid - 2.0 * sd
        x["bb_width_pct"] = (x["bb_upper"] - x["bb_lower"]) / mid.replace(0, np.nan)
        x["bb_width_rank"] = x["bb_width_pct"].rolling(200, min_periods=100).rank(pct=True)
        frames[folder.name.upper()] = x
    return frames

def signals(x: pd.DataFrame, family: str) -> tuple[pd.Series, pd.Series]:
    # Only completed-bar information; execution is the following candle open.
    bull4 = x["h4_trend_bull"].fillna(False).astype(bool)
    bear4 = x["h4_trend_bear"].fillna(False).astype(bool)
    bull1 = x["h1_trend_bull"].fillna(False).astype(bool)
    bear1 = x["h1_trend_bear"].fillna(False).astype(bool)
    long = pd.Series(False, index=x.index)
    short = pd.Series(False, index=x.index)
    if family == "TREND_PULLBACK":
        touch_l = x["low"] <= x["ema20"] + 0.20*x["atr"]
        touch_s = x["high"] >= x["ema20"] - 0.20*x["atr"]
        long = bull4 & bull1 & touch_l.shift(1).fillna(False) & (x["close"] > x["ema20"]) & (x["close"] > x["open"]) & (x["close_pos"] >= .60) & (x["volr"] >= 1.05) & (x["dist_ema20_atr"] <= 1.0) & (x["h4_adx_pct"] >= .35)
        short = bear4 & bear1 & touch_s.shift(1).fillna(False) & (x["close"] < x["ema20"]) & (x["close"] < x["open"]) & (x["close_pos"] <= .40) & (x["volr"] >= 1.05) & (x["dist_ema20_atr"] <= 1.0) & (x["h4_adx_pct"] >= .35)
    elif family == "BREAKOUT":
        # True breakout-retest: prior completed bar closes beyond its prior 20-bar level;
        # current completed bar revisits that level and closes back in breakout direction.
        prior_break_long = x["close"].shift(1) > x["swing_h"].shift(1)
        prior_break_short = x["close"].shift(1) < x["swing_l"].shift(1)
        long_level = x["swing_h"].shift(1)
        short_level = x["swing_l"].shift(1)
        long = (bull4 & prior_break_long & (x["low"] <= long_level + 0.15*x["atr"]) &
                (x["close"] > long_level) & (x["volr"] >= 1.10) &
                (x["atr_rank"] >= .40) & (x["close_pos"] >= .60))
        short = (bear4 & prior_break_short & (x["high"] >= short_level - 0.15*x["atr"]) &
                 (x["close"] < short_level) & (x["volr"] >= 1.10) &
                 (x["atr_rank"] >= .40) & (x["close_pos"] <= .40))
    elif family == "SWEEP_RECLAIM":
        prev_low = x["swing_l"]
        prev_high = x["swing_h"]
        long = (x["low"] < prev_low) & (x["close"] > prev_low) & (x["lower_wick"] >= .35) & (x["close_pos"] >= .60) & (x["volr"] >= 1.05) & (~bear4 | (x["h4_adx_pct"] < .45))
        short = (x["high"] > prev_high) & (x["close"] < prev_high) & (x["upper_wick"] >= .35) & (x["close_pos"] <= .40) & (x["volr"] >= 1.05) & (~bull4 | (x["h4_adx_pct"] < .45))
    elif family == "RANGE_MEAN_REVERSION":
        range_regime = (~bull4 & ~bear4) | (x["h4_adx_pct"] < .45)
        long = range_regime & (x["close"] < x["bb_lower"]) & (x["rsi"] <= 30) & (x["close_pos"] >= .25) & (x["atr_rank"] < .75)
        short = range_regime & (x["close"] > x["bb_upper"]) & (x["rsi"] >= 70) & (x["close_pos"] <= .75) & (x["atr_rank"] < .75)
    else:
        raise ValueError(family)
    return long.fillna(False), short.fillna(False)

def build_events(coin: str, x: pd.DataFrame, family: str) -> list[dict]:
    lm, sm = signals(x, family)
    out=[]
    for i in range(250, len(x)-1):
        side = "LONG" if bool(lm.iloc[i]) else ("SHORT" if bool(sm.iloc[i]) else None)
        if side is None: continue
        atr = float(x["atr"].iloc[i])
        if not np.isfinite(atr) or atr <= 0: continue
        j=i+1
        entry=float(x["open"].iloc[j])
        target_rr = 2.0 if family == "BREAKOUT" else RR
        if side=="LONG":
            anchor=float(x["low"].iloc[max(0,i-STRUCTURE_STOP_LOOKBACK):i+1].min())
            sl=anchor-STRUCTURE_STOP_ATR_BUFFER*atr
            risk=entry-sl
            if risk < .8*atr: sl=entry-.8*atr
            elif risk > 2.5*atr: sl=entry-2.5*atr
            risk=entry-sl; tp=entry+target_rr*risk
        else:
            anchor=float(x["high"].iloc[max(0,i-STRUCTURE_STOP_LOOKBACK):i+1].max())
            sl=anchor+STRUCTURE_STOP_ATR_BUFFER*atr
            risk=sl-entry
            if risk < .8*atr: sl=entry+.8*atr
            elif risk > 2.5*atr: sl=entry+2.5*atr
            risk=sl-entry; tp=entry-target_rr*risk
        if not np.isfinite(risk) or risk<=0: continue
        out.append({"coin":coin,"family":family,"side":side,"signal_index":i,"entry_index":j,
                    "signal_time":x.timestamp.iloc[i].isoformat(),"entry_time":x.timestamp.iloc[j].isoformat(),
                    "entry":entry,"sl":sl,"tp":tp,"risk_price":risk,"target_rr":target_rr})
    return out

def replay(events: list[dict], frames: dict[str,pd.DataFrame], start, end, fee_bps, slip_bps) -> list[dict]:
    start_ts = pd.Timestamp(start, tz="UTC") if start else None
    end_ts = pd.Timestamp(end, tz="UTC") if end else None
    candidates=[e.copy() for e in events if (start_ts is None or pd.Timestamp(e["entry_time"])>=start_ts) and (end_ts is None or pd.Timestamp(e["entry_time"])<end_ts)]
    candidates.sort(key=lambda e:(e["entry_time"],e["coin"]))
    by_time={}
    for e in candidates: by_time.setdefault(e["entry_time"],[]).append(e)
    bar_maps={}; index_maps={}
    for coin,df in frames.items():
        bar_maps[coin]={pd.Timestamp(r.timestamp).isoformat(): (float(r.open),float(r.high),float(r.low),float(r.close)) for r in df.itertuples(index=False)}
        index_maps[coin]={pd.Timestamp(t).isoformat():i for i,t in enumerate(df["timestamp"])}
    timeline=sorted(set(by_time)|{t for b in bar_maps.values() for t in b})
    active=[]; done=[]; last_exit={}
    for ts in timeline:
        if end_ts is not None and pd.Timestamp(ts)>=end_ts: break
        kept=[]
        for p in active:
            bar=bar_maps.get(p["coin"],{}).get(ts)
            if bar is None or ts<=p["entry_time"]:
                kept.append(p); continue
            op,hi,lo,cl=bar
            idx=index_maps[p["coin"]].get(ts,p["entry_index"])
            age=idx-p["entry_index"]
            hit_sl=(lo<=p["sl"]) if p["side"]=="LONG" else (hi>=p["sl"])
            hit_tp=(hi>=p["tp"]) if p["side"]=="LONG" else (lo<=p["tp"])
            exit_px=None; result=None
            if hit_sl:
                # Conservative gap handling: worse of stop and open if opening beyond stop.
                exit_px=min(p["sl"],op) if p["side"]=="LONG" else max(p["sl"],op)
                result="SL"
            elif hit_tp:
                exit_px=p["tp"]; result="TP"
            elif age>=MAX_HOLD_BARS:
                exit_px=cl; result="TIME"
            if result is None:
                kept.append(p); continue
            direction=1 if p["side"]=="LONG" else -1
            gross_r=direction*(exit_px-p["entry"])/p["risk_price"]
            cost_r=((p["entry"]+exit_px)*(fee_bps+slip_bps)/10000.0)/p["risk_price"]
            net_r=gross_r-cost_r
            done.append(dict(p,closed_at=ts,exit=exit_px,result=result,gross_R=gross_r,cost_R=cost_r,R=net_r,hold_bars=age))
            last_exit[p["coin"]]=idx
        active=kept
        for e in by_time.get(ts,[]):
            if len(active)>=MAX_ACTIVE or any(p["coin"]==e["coin"] for p in active): continue
            idx=index_maps[e["coin"]].get(ts,e["entry_index"])
            if idx-last_exit.get(e["coin"],-10**9)<COOLDOWN_BARS: continue
            # Entry is at this candle's OPEN; check the remainder of the entry candle.
            bar=bar_maps.get(e["coin"],{}).get(ts)
            if bar is None: continue
            op,hi,lo,cl=bar
            p=dict(e,entry_index=idx)
            if (p["side"]=="LONG" and op<=p["sl"]) or (p["side"]=="SHORT" and op>=p["sl"]):
                exit_px=op; result="SL"
            elif (lo<=p["sl"] if p["side"]=="LONG" else hi>=p["sl"]):
                exit_px=min(p["sl"],op) if p["side"]=="LONG" else max(p["sl"],op); result="SL"
            elif (hi>=p["tp"] if p["side"]=="LONG" else lo<=p["tp"]):
                exit_px=p["tp"]; result="TP"
            else:
                active.append(p); continue
            direction=1 if p["side"]=="LONG" else -1
            gross_r=direction*(exit_px-p["entry"])/p["risk_price"]
            cost_r=((p["entry"]+exit_px)*(fee_bps+slip_bps)/10000.0)/p["risk_price"]
            done.append(dict(p,closed_at=ts,exit=exit_px,result=result,gross_R=gross_r,cost_R=cost_r,R=gross_r-cost_r,hold_bars=0,entry_bar_exit=True))
            last_exit[p["coin"]]=idx
    # Mark positions still open at the period end using last available close; do not drop them.
    for p in active:
        df=frames[p["coin"]]
        cutoff=(df["timestamp"]<end_ts) if end_ts is not None else pd.Series(True,index=df.index)
        sub=df[cutoff]
        if sub.empty: continue
        row=sub.iloc[-1]; exit_px=float(row["close"])
        direction=1 if p["side"]=="LONG" else -1
        gross_r=direction*(exit_px-p["entry"])/p["risk_price"]
        cost_r=((p["entry"]+exit_px)*(fee_bps+slip_bps)/10000.0)/p["risk_price"]
        done.append(dict(p,closed_at=row["timestamp"].isoformat(),exit=exit_px,result="PERIOD_END_MARK",gross_R=gross_r,cost_R=cost_r,R=gross_r-cost_r,hold_bars=int(sub.index[-1]-p["entry_index"])))
    return done

def summarize(rows: list[dict]) -> dict:
    realized=[r for r in rows if r["result"] in ("TP","SL","TIME")]
    period_marks=[r for r in rows if r["result"]=="PERIOD_END_MARK"]
    rs=np.array([float(r["R"]) for r in realized],dtype=float)
    wins=sum(r["R"]>0 for r in realized); losses=sum(r["R"]<=0 for r in realized)
    eq=np.cumsum(rs) if len(rs) else np.array([])
    peak=np.maximum.accumulate(np.r_[0.0,eq])[1:] if len(eq) else np.array([])
    dd=eq-peak if len(eq) else np.array([])
    gross_win=sum(float(r["R"]) for r in realized if r["R"]>0)
    gross_loss=-sum(float(r["R"]) for r in realized if r["R"]<0)
    dates=[pd.Timestamp(r["entry_time"]).date() for r in realized]
    active_days=(max(dates)-min(dates)).days+1 if dates else 0
    return {"realized_closed":len(realized),"wins_net_positive":wins,"losses_or_nonpositive":losses,
      "win_rate_pct":round(100*wins/len(realized),2) if realized else 0,
      "net_R_realized":round(float(rs.sum()),3) if len(rs) else 0,
      "expectancy_R_realized":round(float(rs.mean()),4) if len(rs) else 0,
      "profit_factor_realized":round(gross_win/gross_loss,4) if gross_loss>0 else None,
      "max_drawdown_realized_R":round(float(dd.min()),3) if len(dd) else 0,
      "trades_per_calendar_day":round(len(realized)/active_days,3) if active_days else 0,
      "time_exits":sum(r["result"]=="TIME" for r in realized),
      "period_end_open_positions_marked":len(period_marks),
      "period_end_marked_net_R":round(sum(float(r["R"]) for r in period_marks),3),
      "funding":"not included; historical funding data not yet joined"}

fee_bps_global=FEE_BPS_PER_SIDE
slippage_bps_global=SLIPPAGE_BPS_PER_SIDE

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",default=str(ROOT/"data"))
    ap.add_argument("--out",default=str(ROOT/"research"/"results"))
    ap.add_argument("--fee-bps",type=float,default=FEE_BPS_PER_SIDE)
    ap.add_argument("--slippage-bps",type=float,default=SLIPPAGE_BPS_PER_SIDE)
    args=ap.parse_args()
    global fee_bps_global, slippage_bps_global
    fee_bps_global=args.fee_bps; slippage_bps_global=args.slippage_bps
    frames=load_data(Path(args.data))
    if not frames: raise SystemExit("No sufficient OHLCV data found")
    families=["TREND_PULLBACK","BREAKOUT","SWEEP_RECLAIM","RANGE_MEAN_REVERSION"]
    all_events={f:[] for f in families}
    for coin,x in frames.items():
        for f in families: all_events[f].extend(build_events(coin,x,f))
    periods={
      "full":(None,"2026-10-10"),
      "train":("2025-01-01","2026-04-01"),
      "test":("2026-04-01","2026-09-01"),
      "known_holdout_reference":("2026-09-05","2026-10-01"),
      "recent_forward_validation":("2026-10-01","2026-10-10")}
    source_counts={}
    for folder in sorted(Path(args.data).iterdir()):
      if not folder.is_dir(): continue
      for file in list(folder.glob("*.csv")):
        try:
          src=pd.read_csv(file,usecols=["data_source"])["data_source"].value_counts().to_dict()
          for k,v in src.items(): source_counts[k]=source_counts.get(k,0)+int(v)
        except Exception: pass
    report={"title":"SAM EDGE Core V3 strategy-family comparison — true breakout-retest RR2",
      "data_source":args.data,"data_source_counts":source_counts,"coins":sorted(frames),
      "timeframe":"15m execution; completed 1H/4H context","RR_default":RR,"RR_by_family":{"BREAKOUT":2.0,"TREND_PULLBACK":RR,"SWEEP_RECLAIM":RR,"RANGE_MEAN_REVERSION":RR},"max_hold_bars":MAX_HOLD_BARS,
      "max_active_positions":MAX_ACTIVE,"cooldown_bars":COOLDOWN_BARS,
      "execution":"next 15m open; conservative same-bar SL priority; stop gaps modeled using adverse open",
      "costs":{"fee_bps_per_side":args.fee_bps,"slippage_bps_per_side":args.slippage_bps,"funding":"excluded; not yet joined"},
      "caution":"Research only. BREAKOUT is a true breakout-retest and uses RR 2.0; other families use RR 1.5. recent_forward_validation is a short recent window, not a statistically sufficient holdout. Funding and full marked-to-market portfolio drawdown are not included.",
      "results":{}}
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    for f in families:
      report["results"][f]={}
      for pname,(st,en) in periods.items():
        rows=replay(all_events[f],frames,st,en,args.fee_bps,args.slippage_bps)
        report["results"][f][pname]=summarize(rows)
        pd.DataFrame(rows).to_csv(out/f"families_{f.lower()}_{pname}.csv",index=False)
    (out/"core_v3_strategy_family_summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__": main()
