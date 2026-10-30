#!/usr/bin/env python3
"""SAM EDGE Adaptive Pressure Engine (APE) — research-only prototype.

Input: one OHLCV CSV with timestamp/open/high/low/close/volume.
No exchange execution, no order placement, no live-core changes.

Features are causal at candle close:
- directional efficiency (net displacement / path length)
- robust relative volume (current volume / rolling median)
- candle close-location value
- volatility expansion (ATR / rolling ATR median)
- failed-breakout/reclaim flags based only on prior rolling range

The script emits candidate events and a forward-return event study. This is NOT
a full trading backtest: fixed-horizon labels do not model SL/TP path ordering,
portfolio concurrency, funding, or execution latency. Use the full portfolio
replay before considering any production integration.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

EPS = 1e-12

def load_ohlcv(path: str) -> pd.DataFrame:
    d = pd.read_csv(path)
    d.columns = [str(c).strip().lower() for c in d.columns]
    if "timestamp" not in d.columns:
        if "open_time" in d.columns:
            d["timestamp"] = pd.to_datetime(d["open_time"], unit="ms", utc=True)
        else:
            raise ValueError("CSV must contain timestamp or open_time")
    else:
        d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        if c not in d.columns:
            raise ValueError(f"Missing required column: {c}")
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp","open","high","low","close","volume"])
    return d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

def build_features(d: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    x = d.copy()
    c, h, l, o, v = (x[k] for k in ["close","high","low","open","volume"])
    prev = c.shift(1)
    tr = pd.concat([(h-l), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14, min_periods=14).mean()
    net = (c - c.shift(lookback)).abs()
    path = c.diff().abs().rolling(lookback, min_periods=lookback).sum()
    x["directional_efficiency"] = (net / (path + EPS)).clip(0,1)
    x["signed_efficiency"] = (c - c.shift(lookback)) / (path + EPS)
    vol_med = v.shift(1).rolling(lookback, min_periods=lookback).median()
    x["relative_volume"] = v / (vol_med + EPS)
    x["close_location"] = ((c-l) / ((h-l) + EPS)).clip(0,1)
    x["body_atr"] = (c-o).abs() / (x["atr"] + EPS)
    atr_med = x["atr"].shift(1).rolling(lookback*3, min_periods=lookback*2).median()
    x["volatility_expansion"] = x["atr"] / (atr_med + EPS)
    # Prior range excludes the current candle to avoid look-ahead.
    prior_high = h.shift(1).rolling(lookback, min_periods=lookback).max()
    prior_low = l.shift(1).rolling(lookback, min_periods=lookback).min()
    x["failed_breakout_up"] = (h > prior_high) & (c < prior_high) & (x["close_location"] < 0.55)
    x["failed_breakout_down"] = (l < prior_low) & (c > prior_low) & (x["close_location"] > 0.45)
    # Research scores: transparent feature composition, not trained probabilities.
    eff = x["directional_efficiency"].fillna(0)
    vol = np.log1p(x["relative_volume"].clip(lower=0)).clip(0, np.log(4)) / np.log(4)
    expansion = ((x["volatility_expansion"]-0.8)/1.2).clip(0,1).fillna(0)
    x["trend_pressure_long"] = 100*(0.45*eff*(x["signed_efficiency"]>0) + 0.30*vol*x["close_location"] + 0.25*expansion*(x["signed_efficiency"]>0))
    x["trend_pressure_short"] = 100*(0.45*eff*(x["signed_efficiency"]<0) + 0.30*vol*(1-x["close_location"]) + 0.25*expansion*(x["signed_efficiency"]<0))
    # Reclaim candidates deliberately remain separate from continuation candidates.
    x["ape_long_candidate"] = (x["failed_breakout_down"] & (x["relative_volume"] >= 1.0))
    x["ape_short_candidate"] = (x["failed_breakout_up"] & (x["relative_volume"] >= 1.0))
    return x

def event_study(x: pd.DataFrame, horizon: int = 12, cost_bps_round_trip: float = 14.0) -> dict:
    # Next-open entry avoids entering at the signal candle's close.
    entry = x["open"].shift(-1)
    future_close = x["close"].shift(-horizon)
    # The horizon return is from next open to close horizon bars later.
    ret = future_close / (entry + EPS) - 1
    cost = cost_bps_round_trip / 10000.0
    rows = []
    configs = [
        ("trend_long", (x["trend_pressure_long"] >= 65) & (x["signed_efficiency"] > 0), 1),
        ("trend_short", (x["trend_pressure_short"] >= 65) & (x["signed_efficiency"] < 0), -1),
        ("reclaim_long", x["ape_long_candidate"], 1),
        ("reclaim_short", x["ape_short_candidate"], -1),
    ]
    for name, mask, direction in configs:
        valid = mask & entry.notna() & future_close.notna() & x["atr"].gt(0)
        r = (ret * direction - cost).where(valid).dropna()
        if len(r) == 0:
            rows.append({"setup":name,"n":0,"win_rate_pct":None,"mean_net_return_pct":None,"median_net_return_pct":None,"profit_factor":None})
            continue
        wins = r[r > 0].sum()
        losses = -r[r < 0].sum()
        rows.append({
            "setup":name, "n":int(len(r)), "win_rate_pct":round(float((r>0).mean()*100),2),
            "mean_net_return_pct":round(float(r.mean()*100),4),
            "median_net_return_pct":round(float(r.median()*100),4),
            "profit_factor":round(float(wins/losses),4) if losses > 0 else None
        })
    return {"horizon_bars":horizon,"round_trip_cost_bps":cost_bps_round_trip,
            "warning":"Fixed-horizon event study only; not a portfolio backtest and not proof of profitability.",
            "results":rows}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True,help="OHLCV CSV")
    ap.add_argument("--output",default="ape_event_study.json")
    ap.add_argument("--horizon",type=int,default=12)
    ap.add_argument("--cost-bps",type=float,default=14.0)
    args=ap.parse_args()
    raw=load_ohlcv(args.input)
    x=build_features(raw)
    report=event_study(x,args.horizon,args.cost_bps)
    report["rows_input"]=int(len(raw))
    report["start"]=str(raw["timestamp"].min())
    report["end"]=str(raw["timestamp"].max())
    Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    main()
