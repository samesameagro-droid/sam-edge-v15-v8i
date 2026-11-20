"""ST-ADX V3.3 controlled universe + portfolio + walk-forward research.

Research-only. Does not modify paper state or submit orders.
Important: current-volume Top-150 is a survivorship-biased historical universe.
This script reports that limitation explicitly; it does not claim point-in-time
historical universe validity.
"""
from __future__ import annotations
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
import ccxt

from research_150_universe_v33 import (
    simulate, metrics, MIN_WARMUP, START, END, utc, TOP_N,
    BAR, LIMIT,
)

# Explicitly exclude BingX synthetic stock/index/FX/commodity contract families.
# Keep the rule visible and auditable; do not silently mix non-crypto markets.
NON_CRYPTO_BASE_PREFIXES = ("NCCO", "NCS", "NCFX")
RETRY_ATTEMPTS = 5
MAX_INTERIOR_GAPS_TO_REPAIR = 500

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research_150_portfolio_v1"
OUT.mkdir(exist_ok=True)
IS_END = pd.Timestamp("2026-09-01T00:00:00Z")
START_DT = pd.to_datetime(START, unit="ms", utc=True)
END_DT = pd.to_datetime(END, unit="ms", utc=True)
MIN_IS_TRADES = 8
MIN_IS_PF = 1.30
MIN_WF_TRADES = 5
MIN_WF_PF = 1.20
RISK_PER_TRADE = 0.01
MAX_AGG_RISK = 0.05
INITIAL_EQUITY = 100.0

def _retry(call, *args, **kwargs):
    last=None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            last=exc
            if attempt+1>=RETRY_ATTEMPTS:
                break
            delay=min(20, 1.5*(2**attempt))
            print(f"RETRY {attempt+1}/{RETRY_ATTEMPTS-1} after {type(exc).__name__}: {exc}; sleep={delay:.1f}s",flush=True)
            time.sleep(delay)
    raise last

def markets_crypto_top150(ex):
    ex.load_markets()
    tickers=_retry(ex.fetch_tickers)
    rows=[]
    excluded=[]
    for sym,m in ex.markets.items():
        base=str(m.get("base") or "").upper()
        if not (m.get("swap") and m.get("linear") and m.get("quote")=="USDT" and m.get("active") is not False):
            continue
        if not base or base in {"USDC","USDT","BUSD","DAI","FDUSD","TUSD"}:
            continue
        if base.startswith(NON_CRYPTO_BASE_PREFIXES):
            excluded.append({"base":base,"symbol":sym,"reason":"non_crypto_synthetic_prefix"})
            continue
        t=tickers.get(sym) or {}
        qv=t.get("quoteVolume")
        if qv is None:
            info=t.get("info") or {}
            qv=info.get("quoteVolume") or info.get("quote_volume") or 0
        try: qv=float(qv or 0)
        except (TypeError,ValueError): qv=0.0
        if qv<=0: continue
        rows.append({"base":base,"symbol":sym,"quote_volume_24h":qv,
                     "market_type":"linear_usdt_perpetual"})
    best={}
    for row in rows:
        if row["base"] not in best or row["quote_volume_24h"]>best[row["base"]]["quote_volume_24h"]:
            best[row["base"]]=row
    ranked=sorted(best.values(),key=lambda x:x["quote_volume_24h"],reverse=True)[:TOP_N]
    return ranked, pd.DataFrame(excluded)

def fetch_history_crypto_strict(ex,sym):
    since=START
    got={}
    while since<END:
        batch=_retry(ex.fetch_ohlcv,sym,timeframe="1h",since=since,limit=LIMIT)
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
    if len(got)<MIN_WARMUP:
        raise RuntimeError(f"{sym}: insufficient history ({len(got)} bars)")
    # A newly listed contract is allowed to begin later than START. Only gaps
    # between its first and last observed candles are treated as missing data.
    first,last=min(got),max(got)
    expected=range(first,last+1,BAR)
    missing=[ts for ts in expected if ts not in got]
    if len(missing)>MAX_INTERIOR_GAPS_TO_REPAIR:
        raise RuntimeError(f"{sym}: {len(missing)} interior candle gaps exceed repair cap")
    repaired=0
    for ts in missing:
        row=_retry(ex.fetch_ohlcv,sym,timeframe="1h",since=ts,limit=1)
        if row and int(row[0][0])==ts:
            got[ts]=row[0]
            repaired+=1
        time.sleep(ex.rateLimit/1000 if ex.rateLimit else .15)
    remaining=[ts for ts in range(first,last+1,BAR) if ts not in got]
    if remaining:
        raise RuntimeError(f"{sym}: unresolved interior candle gaps={len(remaining)}; symbol excluded")
    rows=[got[k] for k in sorted(got)]
    df=pd.DataFrame(rows,columns=["timestamp","open","high","low","close","volume"])
    if df.timestamp.diff().dropna().ne(BAR).any():
        raise RuntimeError(f"{sym}: candle continuity validation failed")
    return df,repaired

def trades_with_dates(t):
    if t.empty:
        return t.assign(entry_dt=pd.Series(dtype="datetime64[ns, UTC]"),
                        exit_dt=pd.Series(dtype="datetime64[ns, UTC]"))
    x=t.copy()
    x["entry_dt"]=pd.to_datetime(x.entry_ts,unit="ms",utc=True)
    x["exit_dt"]=pd.to_datetime(x.exit_ts,unit="ms",utc=True)
    return x

def select_from_is(all_trades):
    """Select coin/side using IS only. OOS columns never enter this decision."""
    rows=[]
    for (coin,side),g in all_trades.groupby(["coin","side"]):
        isg=g[g.entry_dt<IS_END]
        m=metrics(isg)
        passed=(m["trades"]>=MIN_IS_TRADES and m["pf"]>=MIN_IS_PF and m["net_R"]>0)
        rows.append({"coin":coin,"side":side,**{f"is_{k}":v for k,v in m.items()},
                     "selected_from_is":bool(passed)})
    return pd.DataFrame(rows)

def portfolio_replay(selected_trades, label, initial_equity=INITIAL_EQUITY):
    """Chronological portfolio replay; overlapping open stop-risk capped at 5%.

    Each trade's R is normalized by its own modeled risk cash in the isolated
    V3.3 simulator. Portfolio cash risk is reset to 1% of current portfolio
    equity at entry. Trades breaching aggregate open-risk are rejected.
    """
    if selected_trades.empty:
        return {"label":label,"trades":0,"wins":0,"win_rate_pct":0.0,
                "pf":0.0,"net_usdt":0.0,"max_dd_pct":0.0,
                "ending_equity":initial_equity,"risk_rejections":0,"max_stop_risk_stress_dd_pct":0.0}, pd.DataFrame()
    x=selected_trades.sort_values(["entry_dt","coin","side"]).reset_index(drop=True)
    open_pos=[]
    equity=float(initial_equity)
    peak=equity
    maxdd=0.0
    max_stress_dd=0.0
    closed=[]
    rejected=0
    for _,tr in x.iterrows():
        # Realize all positions exited before this entry timestamp.
        due=[p for p in open_pos if p["exit_dt"]<=tr.entry_dt]
        for p in sorted(due,key=lambda z:z["exit_dt"]):
            pnl=p["R"]*p["risk_cash"]
            equity+=pnl
            peak=max(peak,equity)
            maxdd=max(maxdd,(peak-equity)/peak if peak>0 else 0)
            closed.append({**p,"pnl_usdt":pnl,"equity_after":equity})
            open_pos.remove(p)
        risk_open=sum(p["risk_cash"] for p in open_pos)
        same_coin_open=any(p["coin"]==tr.coin for p in open_pos)
        risk_cash=max(0.0,equity*RISK_PER_TRADE)
        if same_coin_open or risk_cash<=0 or risk_open+risk_cash>equity*MAX_AGG_RISK+1e-9:
            rejected+=1
            continue
        open_pos.append({"coin":tr.coin,"side":tr.side,"entry_dt":tr.entry_dt,
                         "entry_month":tr.entry_dt.strftime("%Y-%m"),
                         "exit_dt":tr.exit_dt,"R":float(tr.R),
                         "risk_cash":risk_cash,"reason":tr.reason})
        stress_equity=equity-sum(p["risk_cash"] for p in open_pos)
        max_stress_dd=max(max_stress_dd,(peak-stress_equity)/peak*100 if peak>0 else 0.0)
    for p in sorted(open_pos,key=lambda z:z["exit_dt"]):
        pnl=p["R"]*p["risk_cash"]
        equity+=pnl
        peak=max(peak,equity)
        maxdd=max(maxdd,(peak-equity)/peak if peak>0 else 0)
        closed.append({**p,"pnl_usdt":pnl,"equity_after":equity})
    c=pd.DataFrame(closed)
    if c.empty:
        return {"label":label,"trades":0,"wins":0,"win_rate_pct":0.0,
                "pf":0.0,"net_usdt":0.0,"max_dd_pct":0.0,
                "ending_equity":initial_equity,"risk_rejections":rejected,
                "max_stop_risk_stress_dd_pct":round(max_stress_dd,3)},c
    wins=c[c.pnl_usdt>0]
    losses=c[c.pnl_usdt<0]
    gp=float(wins.pnl_usdt.sum()); gl=abs(float(losses.pnl_usdt.sum()))
    eq=initial_equity+c.pnl_usdt.cumsum()
    dd=((eq.cummax()-eq)/eq.cummax().replace(0,np.nan)).max()
    result={"label":label,"trades":int(len(c)),"wins":int(len(wins)),
            "win_rate_pct":round(len(wins)/len(c)*100,2),
            "pf":round(gp/gl,4) if gl else (999.0 if gp else 0.0),
            "net_usdt":round(float(c.pnl_usdt.sum()),4),
            "max_dd_pct":round(float(dd)*100,3) if pd.notna(dd) else 0.0,
            "ending_equity":round(float(initial_equity+c.pnl_usdt.sum()),4),
            "risk_rejections":int(rejected),
            "max_stop_risk_stress_dd_pct":round(float(max_stress_dd),3)}
    return result,c

def walk_forward(all_trades):
    """Walk-forward selection by prior 6 months, then one continuous OOS portfolio.

    Monthly selections are made without using that month's test data. All
    selected monthly test trades are combined and replayed once, so equity
    compounds continuously and positions can cross month boundaries.
    """
    monthly=[]; selected_test_frames=[]
    month_starts=pd.date_range("2026-07-01","2026-11-01",freq="MS",tz="UTC")
    for month in month_starts:
        train_start=month-pd.DateOffset(months=6)
        train=all_trades[(all_trades.entry_dt>=train_start)&(all_trades.entry_dt<month)]
        selected=set()
        for (coin,side),g in train.groupby(["coin","side"]):
            m=metrics(g)
            if m["trades"]>=MIN_WF_TRADES and m["pf"]>=MIN_WF_PF and m["net_R"]>0:
                selected.add((coin,side))
        test_end=month+pd.DateOffset(months=1)
        test=all_trades[(all_trades.entry_dt>=month)&(all_trades.entry_dt<test_end)]
        if selected and not test.empty:
            test=test[[(r.coin,r.side) in selected for r in test.itertuples()]].copy()
        else:
            test=test.iloc[0:0].copy()
        if not test.empty:
            selected_test_frames.append(test)
        monthly.append({"month":month.strftime("%Y-%m"),
                        "train_start":train_start.isoformat(),
                        "train_end_exclusive":month.isoformat(),
                        "test_start":month.isoformat(),
                        "test_end_exclusive":test_end.isoformat(),
                        "selected_coin_sides":len(selected),
                        "selected_test_entries":int(len(test)),
                        "test_window_complete":bool(test_end<=END_DT)})
    combined=pd.concat(selected_test_frames,ignore_index=True) if selected_test_frames else all_trades.iloc[0:0].copy()
    wf_result,wf_closed=portfolio_replay(combined,"WALK_FORWARD_CONTINUOUS")
    if not wf_closed.empty:
        grouped=wf_closed.groupby("entry_month")
        for row in monthly:
            g=grouped.get_group(row["month"]) if row["month"] in grouped.groups else wf_closed.iloc[0:0]
            wins=g[g.pnl_usdt>0]; losses=g[g.pnl_usdt<0]
            gp=float(wins.pnl_usdt.sum()); gl=abs(float(losses.pnl_usdt.sum()))
            row.update({"closed_trades":int(len(g)),
                        "win_rate_pct":round(len(wins)/len(g)*100,2) if len(g) else 0.0,
                        "pf":round(gp/gl,4) if gl else (999.0 if gp else 0.0),
                        "net_usdt":round(float(g.pnl_usdt.sum()),4),
                        "equity_after_last_exit_for_entry_month_cohort":round(float(g.sort_values("exit_dt").equity_after.iloc[-1]),4) if len(g) else None})
    else:
        for row in monthly:
            row.update({"closed_trades":0,"win_rate_pct":0.0,"pf":0.0,
                        "net_usdt":0.0,"equity_after_last_exit_for_entry_month_cohort":None})
    return pd.DataFrame(monthly),wf_result,wf_closed

def main():
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":30000,
                   "options":{"defaultType":"swap","defaultSubType":"linear"}})
    universe,excluded_markets=markets_crypto_top150(ex)
    pd.DataFrame(universe).to_csv(OUT/"universe_snapshot.csv",index=False)
    excluded_markets.to_csv(OUT/"excluded_non_crypto_markets.csv",index=False)
    trades=[]; failures=[]; repaired_total=0
    for n,item in enumerate(universe,1):
        base,sym=item["base"],item["symbol"]
        try:
            df,repaired=fetch_history_crypto_strict(ex,sym); repaired_total+=repaired
            from strategy_core import add_indicators
            ind=add_indicators(df)
            for side in ("LONG","SHORT"):
                t,open_end=simulate(ind,side)
                if not t.empty:
                    t.insert(0,"coin",base)
                    t["open_at_data_end"]=bool(open_end)
                    trades.append(t)
            print(f"[{n}/{len(universe)}] {sym} candles={len(df)} repaired_gaps={repaired}",flush=True)
        except Exception as e:
            failures.append({"coin":base,"symbol":sym,"error":str(e)})
            print(f"[{n}/{len(universe)}] ERROR {sym}: {e}",flush=True)
    if not trades:
        raise RuntimeError("No trades generated; inspect data_failures.csv")
    all_t=pd.concat(trades,ignore_index=True)
    all_t=trades_with_dates(all_t)
    all_t.to_csv(OUT/"all_coin_side_trades.csv",index=False)
    # Strict IS-only selection; OOS is not used to select candidates.
    selection=select_from_is(all_t)
    selection.to_csv(OUT/"is_only_directional_selection.csv",index=False)
    selected_keys=set((r.coin,r.side) for r in selection[selection.selected_from_is].itertuples())
    selected=all_t[[ (r.coin,r.side) in selected_keys for r in all_t.itertuples() ]]
    selected.to_csv(OUT/"selected_trades_for_replay.csv",index=False)
    long_allow={"ENA","XRP","ZEC","TIA","ORDI","AKE","WIF","APR","XLM","VIRTUAL","DYDX"}
    short_allow={"ARB","AVAX","BTC","ENA","ETH","LINK","NEAR","SOL","SUI","UNI","XRP","DOT","LTC","ADA"}
    baseline=all_t[
        ((all_t.side=="LONG") & all_t.coin.isin(long_allow)) |
        ((all_t.side=="SHORT") & all_t.coin.isin(short_allow))
    ]
    base_result,base_closed=portfolio_replay(baseline,"V3_3_BASELINE_23COIN")
    baseline_oos=baseline[baseline.entry_dt>=IS_END]
    base_oos_result,base_oos_closed=portfolio_replay(baseline_oos,"V3_3_BASELINE_23COIN_OOS_FROM_2026_09_01")
    cand_result,cand_closed=portfolio_replay(selected,"IS_SELECTED_DIRECTIONAL_UNIVERSE")
    base_closed.to_csv(OUT/"baseline_portfolio_closed.csv",index=False)
    base_oos_closed.to_csv(OUT/"baseline_portfolio_oos_closed.csv",index=False)
    cand_closed.to_csv(OUT/"selected_portfolio_closed.csv",index=False)
    # Later holdout metrics for selected set, strictly reported after IS selection.
    holdout=selected[selected.entry_dt>=IS_END]
    holdout_result,holdout_closed=portfolio_replay(holdout,"SELECTED_OOS_FROM_2026_09_01")
    holdout_closed.to_csv(OUT/"selected_oos_closed.csv",index=False)
    wf,wf_result,wf_closed=walk_forward(all_t)
    wf.to_csv(OUT/"walk_forward_monthly.csv",index=False)
    wf_closed.to_csv(OUT/"walk_forward_continuous_closed.csv",index=False)
    pd.DataFrame(failures).to_csv(OUT/"data_failures.csv",index=False)
    summary={
      "strategy_core":"unchanged ST-ADX V3.3",
      "universe_requested":TOP_N,"universe_loaded":len(universe),
      "history_start_utc":utc(START),"history_end_exclusive_utc":utc(END),
      "is_end_exclusive_utc":IS_END.isoformat(),
      "selection":{"minimum_is_trades":MIN_IS_TRADES,"minimum_is_pf":MIN_IS_PF,
                   "uses_oos_for_selection":False},
      "portfolio":{"initial_equity":INITIAL_EQUITY,"risk_per_trade":RISK_PER_TRADE,
                   "max_aggregate_open_risk":MAX_AGG_RISK},
      "baseline_23coin_full":base_result,"baseline_23coin_oos":base_oos_result,
      "selected_directional_full":cand_result,"selected_directional_oos":holdout_result,
      "walk_forward_continuous":wf_result,
      "walk_forward_months":wf.to_dict(orient="records"),
      "universe_filter":"USDT linear perpetuals; excluded NCCO/NCS/NCFX synthetic non-crypto families",
      "excluded_non_crypto_markets":int(len(excluded_markets)),
      "repaired_interior_candles":int(repaired_total),"data_failures":len(failures),
      "limitations":[
        "Universe is selected from a current Top-150 volume snapshot, not point-in-time historical constituents; survivorship/selection bias remains.",
        "Crypto-only screening excludes known BingX synthetic non-crypto prefixes NCCO, NCS and NCFX; this is an explicit heuristic and must be reviewed against exchange metadata.",
        "Portfolio replay uses standalone trade R normalized by modeled risk; one open position per coin and 5% aggregate initial stop-risk cap are enforced.",
        "Drawdown is measured on realized closed-trade equity. max_stop_risk_stress_dd_pct is a simultaneous open-stop-risk stress estimate, not intrabar mark-to-market equity; candle-by-candle parity remains unverified.",
        "Funding is excluded; fee/slippage assumptions inherited from V3.3 research simulator.",
        "Open positions at data end are not forced closed by source simulator; inspect open_at_data_end flags."
      ]
    }
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps(summary,indent=2,default=str),flush=True)

if __name__=="__main__":
    main()
