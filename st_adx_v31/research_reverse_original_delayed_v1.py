"""Research-only comparison: Original vs Reverse vs Delayed ST-ADX V3.3.
Uses identical crypto universe, OHLCV, costs, risk sizing and baseline coin/side allowlist.
Does not modify strategy_core.py, paper state, or submit orders.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import ccxt

from strategy_core import (
    add_indicators, LONG_ALLOWLIST, SHORT_ALLOWLIST,
    ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER,
)
from research_150_portfolio_v1 import (
    markets_crypto_top150, fetch_history_crypto_strict,
    trades_with_dates, portfolio_replay, START_DT, END_DT, IS_END,
    INITIAL_EQUITY,
)
from research_150_universe_v33 import simulate, bracket, MIN_WARMUP, FEE, SLIP, RISK, NOTIONAL_CAP

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research_reverse_original_delayed_v1"
OUT.mkdir(exist_ok=True)
BAR = 3_600_000
# Delayed entry rule is fixed before evaluating results:
# after a valid original signal, wait up to 6 subsequent 1h candles for price
# to touch EMA53 +/- 0.25 ATR(10); cancel if ST direction invalidates the signal.
DELAY_MAX_BARS = 6
DELAY_EMA_ATR_BAND = 0.25
RISK_PER_TRADE = 0.01
MAX_AGG_RISK = 0.05

def delayed_sim(df: pd.DataFrame, side: str):
    equity=100.0; pos=None; cooldown_until=-1; pending=None; trades=[]
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
            trades.append({"side":side,"entry_ts":pos["ts"],"exit_ts":int(r.timestamp),
                "entry":pos["entry"],"exit":px,"stop":pos["stop"],"target":pos["target"],
                "reason":reason,"net":net,"R":net/risk_cash if risk_cash else 0,
                "equity":equity,"entry_mode":"DELAYED"})
            pos=None; pending=None; cooldown_until=i+1
        if pos is not None or i<cooldown_until:
            continue
        # Evaluate a previously armed signal only on bars AFTER the signal candle.
        if pending is not None:
            if i>pending["expires"]:
                pending=None
            else:
                invalid=(side=="LONG" and int(r.st_direction)>0) or (side=="SHORT" and int(r.st_direction)<0)
                if invalid:
                    pending=None
                else:
                    atr=float(r.atr_risk); ema=float(r.ema53)
                    touched=(np.isfinite(atr) and atr>0 and np.isfinite(ema)
                        and float(r.low)<=ema+DELAY_EMA_ATR_BAND*atr
                        and float(r.high)>=ema-DELAY_EMA_ATR_BAND*atr)
                    if touched:
                        raw=float(nxt.open)
                        entry=raw*(1+SLIP if side=="LONG" else 1-SLIP)
                        dist=atr*ATR_SL_MULTIPLIER
                        stop=entry-dist if side=="LONG" else entry+dist
                        target=entry+atr*ATR_TP_MULTIPLIER if side=="LONG" else entry-atr*ATR_TP_MULTIPLIER
                        unit=abs(entry-stop)+entry*FEE+abs(stop*(1-SLIP if side=="LONG" else 1+SLIP))*FEE+abs(stop*(1-SLIP if side=="LONG" else 1+SLIP)-stop)
                        qty=min(equity*RISK/unit,equity*NOTIONAL_CAP/entry)
                        if qty>0:
                            pos={"ts":int(nxt.timestamp),"entry":entry,"stop":stop,"target":target,
                                "qty":qty,"fee_in":entry*qty*FEE,"risk_cash":qty*unit,"pending_flip":False}
                        pending=None
                        continue
        # Arm only after pending processing, preventing same-candle lookahead.
        sig=bool(r.long_signal if side=="LONG" else r.short_signal)
        if sig and pending is None:
            pending={"signal_ts":int(r.timestamp),"expires":i+DELAY_MAX_BARS}
    return pd.DataFrame(trades), (pos is not None), pending is not None

def opposite(side):
    return "SHORT" if side=="LONG" else "LONG"

def collect_engine(ind, coin, engine, baseline_side):
    if engine=="ORIGINAL":
        t,open_end=simulate(ind,baseline_side)
    elif engine=="REVERSE":
        exec_side=opposite(baseline_side)
        x=ind.copy()
        if baseline_side=="LONG":
            x["short_signal"]=ind["long_signal"].astype(bool)
        else:
            x["long_signal"]=ind["short_signal"].astype(bool)
        t,open_end=simulate(x,exec_side)
    elif engine=="DELAYED":
        t,open_end,_=delayed_sim(ind,baseline_side)
    else:
        raise ValueError(engine)
    if t.empty:
        return t
    t=t.copy()
    t.insert(0,"coin",coin)
    t["engine"]=engine
    t["source_signal_side"]=baseline_side
    t["entry_mode"]=engine
    return t

def summarize_engine(engine, trades):
    x=trades_with_dates(trades) if not trades.empty else trades.copy()
    full,full_closed=portfolio_replay(x,engine+"_FULL")
    is_t=x[x.entry_dt<IS_END] if not x.empty else x
    oos_t=x[x.entry_dt>=IS_END] if not x.empty else x
    is_result,is_closed=portfolio_replay(is_t,engine+"_IS")
    oos_result,oos_closed=portfolio_replay(oos_t,engine+"_OOS")
    return {"engine":engine,"full":full,"is":is_result,"oos":oos_result},full_closed,is_closed,oos_closed

def main():
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":30000,
        "options":{"defaultType":"swap","defaultSubType":"linear"}})
    universe,excluded=markets_crypto_top150(ex)
    baseline_sides={c:"LONG" for c in LONG_ALLOWLIST}
    for c in SHORT_ALLOWLIST:
        if c in baseline_sides:
            # Preserve both baseline directions when a coin is on both allowlists.
            baseline_sides[c]="BOTH"
        else:
            baseline_sides[c]="SHORT"
    rows={k:[] for k in ("ORIGINAL","REVERSE","DELAYED")}
    failures=[]; total_bars=0; repaired=0
    selected=[u for u in universe if u["base"] in baseline_sides]
    for n,item in enumerate(selected,1):
        coin,sym=item["base"],item["symbol"]
        try:
            raw,rep=fetch_history_crypto_strict(ex,sym); repaired+=rep; total_bars+=len(raw)
            ind=add_indicators(raw)
            sides=("LONG","SHORT") if baseline_sides[coin]=="BOTH" else (baseline_sides[coin],)
            for source_side in sides:
                for engine in rows:
                    t=collect_engine(ind,coin,engine,source_side)
                    if not t.empty:
                        rows[engine].append(t)
            print(f"[{n}/{len(selected)}] {sym} bars={len(raw)} repaired={rep}",flush=True)
        except Exception as exc:
            failures.append({"coin":coin,"symbol":sym,"error":str(exc)})
            print(f"[{n}/{len(selected)}] ERROR {sym}: {exc}",flush=True)
    summaries=[]
    for engine,frames in rows.items():
        all_t=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
        if not all_t.empty:
            all_t=trades_with_dates(all_t)
            all_t.to_csv(OUT/f"{engine.lower()}_all_trades.csv",index=False)
        result,closed,is_closed,oos_closed=summarize_engine(engine,all_t)
        closed.to_csv(OUT/f"{engine.lower()}_portfolio_full.csv",index=False)
        is_closed.to_csv(OUT/f"{engine.lower()}_portfolio_is.csv",index=False)
        oos_closed.to_csv(OUT/f"{engine.lower()}_portfolio_oos.csv",index=False)
        summaries.append(result)
    excluded.to_csv(OUT/"excluded_non_crypto_markets.csv",index=False)
    pd.DataFrame(failures).to_csv(OUT/"data_failures.csv",index=False)
    report={"experiment":"ST-ADX V3.3 Original vs Reverse vs Delayed",
        "window_start_utc":START_DT.isoformat(),"window_end_exclusive_utc":END_DT.isoformat(),
        "is_end_exclusive_utc":IS_END.isoformat(),"timeframe":"1h",
        "universe":"same crypto Top-150 snapshot source; only baseline 23 coin-direction pairs",
        "baseline_allowlist":"LONG_ALLOWLIST / SHORT_ALLOWLIST from unchanged strategy_core.py",
        "reverse_definition":"Each eligible original signal is executed in the opposite direction on the same coin; stop and target distances are mirrored using the unchanged 1.2 ATR / 3.5 ATR brackets, recalculated from reverse entry.",
        "delayed_definition":{"wait_bars":DELAY_MAX_BARS,"retest":"EMA53 touch within +/-0.25 ATR(10)","starts":"first candle after signal","cancel":"ST direction invalidates or 6 subsequent bars expire","entry":"next candle open after qualifying retest","sl_tp":"unchanged 1.2 ATR / 3.5 ATR measured at retest candle"},
        "execution":{"fee":FEE,"slippage":SLIP,"per_trade_risk":RISK_PER_TRADE,"max_aggregate_open_risk":MAX_AGG_RISK,"initial_equity":INITIAL_EQUITY},
        "results":summaries,"symbols_tested":len(selected),"data_failures":failures,
        "candles_loaded_total":total_bars,"repaired_interior_candles":repaired,
        "limitations":["Current Top-150 snapshot is survivorship-biased; experiment is restricted to baseline allowlisted coin/direction pairs.",
            "Funding excluded; fee/slippage are inherited from V3.3 research simulator.",
            "Delayed entry uses deterministic OHLC touch approximation; intrabar path is unknown. Stop/target same-candle ambiguity follows existing simulator convention.",
            "Open trades at data end are not force-closed.",
            "Research-only; strategy_core.py, paper state and order execution are not modified."]}
    (OUT/"summary.json").write_text(json.dumps(report,indent=2,default=str))
    print(json.dumps(report,indent=2,default=str),flush=True)

if __name__=="__main__":
    main()
