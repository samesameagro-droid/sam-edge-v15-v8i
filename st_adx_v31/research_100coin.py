from __future__ import annotations
import csv, json, math, os, sys, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from strategy_core import add_indicators, ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER, COOLDOWN_BARS

BASE_URL = "https://fapi.binance.com"
START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END = int(datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp() * 1000) - 1
BAR_MS = 60 * 60 * 1000
TRAIN_END = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
MAX_SYMBOLS = 100
FEE_BASE, SLIP_BASE = 0.0005, 0.0002
FEE_STRESS, SLIP_STRESS = 0.00075, 0.0005

class GlobalPacer:
    def __init__(self, interval=0.18):
        self.interval, self.lock, self.next_time = interval, threading.Lock(), 0.0
    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_time - now)
            self.next_time = max(now, self.next_time) + self.interval
        if delay:
            time.sleep(delay)

PACER = GlobalPacer()
_tls = threading.local()

def session():
    if not hasattr(_tls, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": "ST-ADX-V3.1-100Coin-Research/1.0"})
        _tls.session = s
    return _tls.session

def get_json(path, params=None, attempts=6):
    last = None
    for n in range(attempts):
        PACER.wait()
        try:
            r = session().get(BASE_URL + path, params=params, timeout=25)
            if r.status_code in (418, 429):
                time.sleep(max(2.0, float(r.headers.get("Retry-After", min(60, 2 ** (n + 1))))))
                last = RuntimeError(f"Binance rate limited ({r.status_code})")
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if n == attempts - 1:
                break
            time.sleep(min(30, 1.5 ** (n + 1)))
    raise RuntimeError(f"GET {path} failed after {attempts} attempts: {last}")

def get_universe():
    info = get_json("/fapi/v1/exchangeInfo")
    eligible = {
        x["symbol"] for x in info.get("symbols", [])
        if x.get("status") == "TRADING"
        and x.get("contractType") == "PERPETUAL"
        and x.get("quoteAsset") == "USDT"
        and x.get("marginAsset") == "USDT"
    }
    tickers = get_json("/fapi/v1/ticker/24hr")
    ranked = []
    for t in tickers:
        sym = t.get("symbol")
        if sym not in eligible:
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        if qv > 0 and math.isfinite(qv):
            ranked.append({"symbol": sym, "quote_volume_24h_usdt": qv})
    ranked.sort(key=lambda x: x["quote_volume_24h_usdt"], reverse=True)
    ranked = ranked[:MAX_SYMBOLS]
    if len(ranked) < MAX_SYMBOLS:
        raise RuntimeError(f"Only {len(ranked)} eligible USDT perpetuals found; need {MAX_SYMBOLS}.")
    return [x["symbol"] for x in ranked], ranked

def fetch_history(symbol):
    rows, cursor = [], START
    while cursor <= END:
        raw = get_json("/fapi/v1/klines", {
            "symbol": symbol, "interval": "1h", "startTime": cursor,
            "endTime": END, "limit": 1000
        })
        if not raw:
            break
        rows.extend(raw)
        nxt = int(raw[-1][0]) + BAR_MS
        if nxt <= cursor or len(raw) < 1000:
            break
        cursor = nxt
    if not rows:
        return symbol, pd.DataFrame(columns=["timestamp","open","high","low","close","volume"]), "NO_HISTORY"
    df = pd.DataFrame(rows, columns=["timestamp","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"])
    df = df[["timestamp","open","high","low","close","volume"]].copy()
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df = df[(df.timestamp >= START) & (df.timestamp <= END)].reset_index(drop=True)
    if len(df) < 120:
        return symbol, df, f"INSUFFICIENT_HISTORY_{len(df)}"
    gaps = int(np.sum(df.timestamp.diff().dropna().to_numpy(dtype=np.int64) != BAR_MS))
    return symbol, df, f"OK_GAPS_{gaps}"

def bracket(pos, bar):
    o, h, l = float(bar.open), float(bar.high), float(bar.low)
    stop, target, side = float(pos["stop"]), float(pos["target"]), pos["side"]
    if side == "LONG":
        if o <= stop: return o, "STOP_GAP"
        if o >= target: return o, "TARGET_GAP"
        hs, ht = l <= stop, h >= target
    else:
        if o >= stop: return o, "STOP_GAP"
        if o <= target: return o, "TARGET_GAP"
        hs, ht = h >= stop, l <= target
    if hs and ht:
        high_first = abs(o - h) < abs(o - l)
        if side == "LONG":
            return (target, "TARGET_BOTH_TOUCHED") if high_first else (stop, "STOP_BOTH_TOUCHED")
        return (stop, "STOP_BOTH_TOUCHED") if high_first else (target, "TARGET_BOTH_TOUCHED")
    if hs: return stop, "STOP"
    if ht: return target, "TARGET"
    return None

def close_trade(pos, bar, raw_exit, reason, fee, slip, symbol):
    side = pos["side"]
    exit_px = raw_exit if reason.startswith("TARGET") else raw_exit * (1.0 - slip if side == "LONG" else 1.0 + slip)
    gross = (exit_px - pos["entry_price"]) if side == "LONG" else (pos["entry_price"] - exit_px)
    entry_fee, exit_fee = pos["entry_price"] * fee, abs(exit_px) * fee
    net = gross - entry_fee - exit_fee
    risk_unit = pos["signal_atr"] * ATR_SL_MULTIPLIER
    return {
        "symbol": symbol, "side": side,
        "signal_time_utc": datetime.fromtimestamp(pos["signal_ts"]/1000, timezone.utc).isoformat(),
        "entry_time_utc": datetime.fromtimestamp(pos["entry_ts"]/1000, timezone.utc).isoformat(),
        "entry_price": pos["entry_price"], "stop": pos["stop"], "target": pos["target"],
        "exit_time_utc": datetime.fromtimestamp(int(bar.timestamp)/1000, timezone.utc).isoformat(),
        "exit_price": exit_px, "exit_reason": reason,
        "gross_pnl_per_unit": gross, "entry_fee_per_unit": entry_fee,
        "exit_fee_per_unit": exit_fee, "net_pnl_per_unit": net,
        "return_pct_on_entry_notional": net / pos["entry_price"] * 100.0,
        "R_net": net / risk_unit if risk_unit > 0 else 0.0,
        "adx_at_signal": pos["adx"], "rsi_at_signal": pos["rsi"],
        "atr10_at_signal": pos["signal_atr"],
    }

def replay_symbol(symbol, raw, fee, slip):
    if len(raw) < 120:
        return [], None
    f = add_indicators(raw)
    active, last_exit_i = None, -10**9
    trades = []
    for i in range(len(f)):
        bar, closed_this_bar = f.iloc[i], False
        if active is not None:
            if active["pending_flip"]:
                trades.append(close_trade(active, bar, float(bar.open), "ST_FLIP", fee, slip, symbol))
                active, last_exit_i, closed_this_bar = None, i, True
            else:
                hit = bracket(active, bar)
                if hit is not None:
                    trades.append(close_trade(active, bar, float(hit[0]), str(hit[1]), fee, slip, symbol))
                    active, last_exit_i, closed_this_bar = None, i, True
                else:
                    if active["side"] == "LONG" and int(bar.st_direction) > 0:
                        active["pending_flip"] = True
                    elif active["side"] == "SHORT" and int(bar.st_direction) < 0:
                        active["pending_flip"] = True
        if active is None and not closed_this_bar and i < len(f)-1 and (i-last_exit_i) >= COOLDOWN_BARS:
            side = "LONG" if bool(bar.long_signal) else ("SHORT" if bool(bar.short_signal) else None)
            if side:
                atr = float(bar.atr_risk)
                if atr > 0 and math.isfinite(atr):
                    nxt = f.iloc[i+1]
                    raw_entry = float(nxt.open)
                    entry = raw_entry * (1.0+slip if side=="LONG" else 1.0-slip)
                    sd, td = atr*ATR_SL_MULTIPLIER, atr*ATR_TP_MULTIPLIER
                    active = {
                        "side": side, "signal_ts": int(bar.timestamp), "entry_ts": int(nxt.timestamp),
                        "entry_price": entry, "stop": entry-sd if side=="LONG" else entry+sd,
                        "target": entry+td if side=="LONG" else entry-td,
                        "signal_atr": atr, "adx": float(bar.adx), "rsi": float(bar.rsi),
                        "pending_flip": False,
                    }
    return trades, active

def metric(rows):
    if not rows:
        return {"trades":0,"wins":0,"losses":0,"win_rate_pct":0.0,"profit_factor":None,
                "net_return_pct_per_notional_sum":0.0,"avg_return_pct_per_trade":0.0,"avg_R_net":0.0}
    returns = np.array([float(x["return_pct_on_entry_notional"]) for x in rows], dtype=float)
    wins, losses = returns[returns>0], returns[returns<0]
    pf = float(wins.sum()/abs(losses.sum())) if len(losses) and abs(losses.sum())>0 else (None if len(wins) else 0.0)
    return {"trades":int(len(rows)),"wins":int(np.sum(returns>0)),"losses":int(np.sum(returns<=0)),
            "win_rate_pct":round(float(np.mean(returns>0)*100),4),
            "profit_factor":round(pf,5) if pf is not None else None,
            "net_return_pct_per_notional_sum":round(float(returns.sum()),5),
            "avg_return_pct_per_trade":round(float(returns.mean()),5),
            "avg_R_net":round(float(np.mean([x["R_net"] for x in rows])),5)}

def write_csv(path, rows):
    if not rows:
        path.write_text("no_rows\n", encoding="utf-8")
        return
    fields = list(dict.fromkeys(k for row in rows for k in row.keys()))
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def exit_ms(row):
    return int(datetime.fromisoformat(row["exit_time_utc"].replace("Z","+00:00")).timestamp()*1000)

def main():
    out = Path(os.getenv("ST_ADX_100_OUT", ROOT/"research_output"))
    out.mkdir(parents=True, exist_ok=True)
    print("ST-ADX V3.1 | 100-COIN HISTORICAL RESEARCH | 1H", flush=True)
    symbols, ranked = get_universe()
    snapshot = datetime.now(timezone.utc).isoformat()
    print(f"Universe snapshot: {len(symbols)} symbols | top 24h quote volume | {snapshot}", flush=True)
    frames, universe_rows, failures = {}, [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fetch_history, s): s for s in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                _, df, status = fut.result()
                if status.startswith("OK"):
                    frames[sym] = df
                    first, last = int(df.timestamp.iloc[0]), int(df.timestamp.iloc[-1])
                    qv = next(x["quote_volume_24h_usdt"] for x in ranked if x["symbol"]==sym)
                    universe_rows.append({"symbol":sym,"quote_volume_24h_usdt":qv,"bars":len(df),
                        "first_bar_utc":datetime.fromtimestamp(first/1000,timezone.utc).isoformat(),
                        "last_bar_utc":datetime.fromtimestamp(last/1000,timezone.utc).isoformat(),
                        "coverage_days":round((last-first)/86400000,2),"status":status})
                    print(f"DATA {sym}: {len(df)} bars | {status}", flush=True)
                else:
                    failures.append({"symbol":sym,"status":status})
                    print(f"SKIP {sym}: {status}", flush=True)
            except Exception as e:
                failures.append({"symbol":sym,"status":f"ERROR:{type(e).__name__}:{str(e)[:180]}"})
                print(f"ERROR {sym}: {type(e).__name__}: {e}", flush=True)
    if len(frames) < 60:
        raise RuntimeError(f"Only {len(frames)} symbols returned usable history; aborting instead of presenting a weak 100-coin test.")

    base_trades, stress_trades, open_rows, coin_side_rows = [], [], [], []
    for idx, sym in enumerate(symbols,1):
        if sym not in frames:
            continue
        df = frames[sym]
        bt, bo = replay_symbol(sym, df, FEE_BASE, SLIP_BASE)
        st, so = replay_symbol(sym, df, FEE_STRESS, SLIP_STRESS)
        base_trades.extend(bt)
        stress_trades.extend(st)
        if bo:
            open_rows.append({"symbol":sym,"side":bo["side"],
                "signal_time_utc":datetime.fromtimestamp(bo["signal_ts"]/1000,timezone.utc).isoformat(),
                "entry_time_utc":datetime.fromtimestamp(bo["entry_ts"]/1000,timezone.utc).isoformat(),
                "entry_price":bo["entry_price"],"stop":bo["stop"],"target":bo["target"],
                "pending_flip":bo["pending_flip"]})
        for side in ("LONG","SHORT"):
            sr = [x for x in bt if x["side"]==side]
            train = [x for x in sr if exit_ms(x)<TRAIN_END]
            later = [x for x in sr if exit_ms(x)>=TRAIN_END]
            coin_side_rows.append({"symbol":sym,"side":side,
                **{f"all_{k}":v for k,v in metric(sr).items()},
                **{f"train_{k}":v for k,v in metric(train).items()},
                **{f"later_{k}":v for k,v in metric(later).items()}})
        print(f"REPLAY {idx:03d}/{len(symbols)} {sym}: {len(bt)} closed trades", flush=True)

    base_trades.sort(key=lambda x:x["entry_time_utc"])
    stress_trades.sort(key=lambda x:x["entry_time_utc"])
    train_base = [x for x in base_trades if exit_ms(x)<TRAIN_END]
    later_base = [x for x in base_trades if exit_ms(x)>=TRAIN_END]
    train_stress = [x for x in stress_trades if exit_ms(x)<TRAIN_END]
    later_stress = [x for x in stress_trades if exit_ms(x)>=TRAIN_END]
    candidates = []
    for row in coin_side_rows:
        if row["train_trades"]>=8 and row["train_profit_factor"] is not None and row["train_profit_factor"]>1.0:
            candidates.append({"symbol":row["symbol"],"side":row["side"],
                "train_trades":row["train_trades"],"train_pf":row["train_profit_factor"],
                "train_wr_pct":row["train_win_rate_pct"],"later_trades":row["later_trades"],
                "later_pf":row["later_profit_factor"],"later_wr_pct":row["later_win_rate_pct"]})
    candidates.sort(key=lambda x:(x["train_pf"] or 0,x["train_trades"]),reverse=True)

    summary = {
      "model":"ST-ADX V3.1 sequential ADX Rising 2 | unrestricted LONG+SHORT | 1H",
      "run_utc":snapshot,"data_source":"Binance USD-M Futures public REST API",
      "universe_rule":"Snapshot top 100 active USDT-margined perpetual contracts by current 24h quote volume",
      "period_utc":{"start":"2026-01-01T00:00:00+00:00","end":"2026-11-10T23:00:00+00:00"},
      "train_period":"2026-01-01 through 2026-05-31 UTC",
      "later_period":"2026-06-01 through 2026-11-10 UTC",
      "symbols_requested":len(symbols),"symbols_with_usable_history":len(frames),"symbols_skipped":len(failures),
      "indicators":{"supertrend_atr":12,"supertrend_multiplier":2.8,"dmi_length":40,"adx_smoothing":40,
        "adx_min":30,"adx_rising":"strict consecutive increase across both changes in 3 ADX values",
        "rsi_length":12,"long_rsi_gt":50,"short_rsi_lt":50,"ema_length":53,"risk_atr_length":10,
        "sl_atr":ATR_SL_MULTIPLIER,"tp_atr":ATR_TP_MULTIPLIER,"cooldown_bars":COOLDOWN_BARS},
      "execution_assumptions":{"entry":"next 1H candle open after signal candle close",
        "fee_per_side_baseline":FEE_BASE,"adverse_slippage_per_side_baseline":SLIP_BASE,
        "fee_per_side_stress":FEE_STRESS,"adverse_slippage_per_side_stress":SLIP_STRESS,
        "funding":"excluded","portfolio_limit":"independent per-symbol replay; no shared 2-position portfolio cap"},
      "bias_warning":"Universe is selected from current top-100 volume then replayed historically. This creates survivorship/current-volume selection bias; newly listed symbols have shorter history. Research only, not live performance.",
      "all_100_baseline":metric(base_trades),"train_baseline":metric(train_base),"later_baseline":metric(later_base),
      "all_100_stress":metric(stress_trades),"train_stress":metric(train_stress),"later_stress":metric(later_stress),
      "open_positions_at_end":len(open_rows)
    }
    write_csv(out/"universe_100.csv",sorted(universe_rows,key=lambda x:x["quote_volume_24h_usdt"],reverse=True))
    write_csv(out/"skipped_symbols.csv",failures)
    write_csv(out/"coin_side_metrics.csv",coin_side_rows)
    write_csv(out/"trades_baseline.csv",base_trades)
    write_csv(out/"trades_stress.csv",stress_trades)
    write_csv(out/"open_positions_end.csv",open_rows)
    write_csv(out/"train_selected_candidates.csv",candidates)
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    md = ["# ST-ADX V3.1 — 100-Coin Historical Research","",
      f"Run UTC: {snapshot}",f"Universe: {len(frames)} usable of {len(symbols)} requested current top-volume USDT perpetuals.",
      "Period: 2026-01-01 to 2026-11-10 UTC | 1H | unrestricted LONG + SHORT.","",
      "## Main results — baseline costs",f"- All period: {summary['all_100_baseline']}",
      f"- TRAIN (Jan–May): {summary['train_baseline']}",f"- LATER (Jun–Nov 10): {summary['later_baseline']}","",
      "## Stress costs (0.075% fee/side + 5 bps adverse slippage/side)",
      f"- All period: {summary['all_100_stress']}",f"- TRAIN: {summary['train_stress']}",
      f"- LATER: {summary['later_stress']}","",
      "## TRAIN-only candidate pairs (minimum 8 TRAIN trades and TRAIN PF > 1.0)",
      f"Candidates: {len(candidates)}","","| Symbol | Side | TRAIN trades | TRAIN PF | LATER trades | LATER PF |",
      "|---|---:|---:|---:|---:|---:|"]
    for c in candidates:
        md.append(f"| {c['symbol']} | {c['side']} | {c['train_trades']} | {c['train_pf']} | {c['later_trades']} | {c['later_pf']} |")
    md += ["","## Limitations",
      "- Current top-100 volume snapshot is applied retrospectively, so universe selection has survivorship/current-volume bias.",
      "- Symbols listed after 1 Jan have shorter history; see universe_100.csv.",
      "- Funding is excluded; fee/slippage assumptions are not actual exchange fills.",
      "- Independent per-symbol replay, not a single account portfolio; summed returns are not account return.",
      "- Open trades at period end are listed separately and excluded from closed-trade metrics.",
      "- No live orders or Telegram messages are sent by this research workflow.",""]
    (out/"SUMMARY.md").write_text("\n".join(md),encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)
    print(f"TRAIN-only qualifying coin-side candidates: {len(candidates)}",flush=True)
    print(f"OUTPUT_DIR={out}",flush=True)

if __name__=="__main__":
    main()
