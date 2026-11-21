"""Incremental V3.3 + continuation research replay.

Uses the existing isolated V3.3 top-150 BingX research fetcher and simulator.
Does not touch paper_state, active strategy files, or workflows.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from research_150_universe_v33 import (
    ccxt, markets_top150, fetch_history, add_indicators, simulate, metrics,
    utc, TOP_N, START, END,
)
from continuation_engine import add_continuation_signals

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research_v33_continuation"
OUT.mkdir(exist_ok=True)


def run():
    ex = ccxt.bingx({
        "enableRateLimit": True,
        "timeout": 30000,
        "options": {"defaultType": "swap", "defaultSubType": "linear"},
    })
    try:
        snapshot = ex.fetch_time() or ex.milliseconds()
    except Exception:
        snapshot = ex.milliseconds()

    universe = markets_top150(ex)
    rows, failures, all_trades = [], [], []
    for n, item in enumerate(universe, 1):
        base, symbol = item["base"], item["symbol"]
        try:
            raw, gaps = fetch_history(ex, symbol)
            base_df = add_indicators(raw)
            research_df = add_continuation_signals(base_df)

            for side in ("LONG", "SHORT"):
                base_col = "long_signal" if side == "LONG" else "short_signal"
                cont_col = "long_continuation_trigger" if side == "LONG" else "short_continuation_trigger"
                combined_col = f"{side.lower()}_combined_signal"
                research_df[combined_col] = (
                    research_df[base_col].fillna(False).astype(bool)
                    | research_df[cont_col].fillna(False).astype(bool)
                )

                baseline_df = base_df.copy()
                baseline_df[base_col] = baseline_df[base_col].fillna(False).astype(bool)
                baseline_trades, baseline_open = simulate(baseline_df, side)

                combined_df = research_df.copy()
                combined_df[base_col] = combined_df[combined_col]
                combined_trades, combined_open = simulate(combined_df, side)

                bm, cm = metrics(baseline_trades), metrics(combined_trades)
                row = {
                    "coin": base, "symbol": symbol, "side": side, "bars": len(raw),
                    "gaps": gaps, "baseline_open_at_end": baseline_open,
                    "combined_open_at_end": combined_open,
                    "baseline_trades": bm["trades"], "combined_trades": cm["trades"],
                    "added_closed_trades": cm["trades"] - bm["trades"],
                    "baseline_win_rate_pct": bm["win_rate_pct"],
                    "combined_win_rate_pct": cm["win_rate_pct"],
                    "baseline_pf": bm["pf"], "combined_pf": cm["pf"],
                    "baseline_net_usdt": bm["net_usdt"], "combined_net_usdt": cm["net_usdt"],
                    "baseline_net_R": bm["net_R"], "combined_net_R": cm["net_R"],
                    "baseline_max_dd_pct": bm["max_dd_pct"], "combined_max_dd_pct": cm["max_dd_pct"],
                }
                rows.append(row)
                for variant, trades in (("BASELINE", baseline_trades), ("BASELINE_PLUS_CONTINUATION", combined_trades)):
                    if not trades.empty:
                        t = trades.copy()
                        t.insert(0, "variant", variant)
                        t.insert(0, "coin", base)
                        t.insert(1, "symbol", symbol)
                        all_trades.append(t)
            print(f"[{n}/{len(universe)}] {symbol} | gaps={gaps} complete", flush=True)
        except Exception as exc:
            failures.append({"coin": base, "symbol": symbol, "error": str(exc)})
            print(f"[{n}/{len(universe)}] ERROR {symbol}: {exc}", flush=True)

    result = pd.DataFrame(rows)
    result.to_csv(OUT / "coin_side_comparison.csv", index=False)
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_csv(OUT / "trade_log.csv", index=False)
    pd.DataFrame(failures).to_csv(OUT / "data_failures.csv", index=False)

    summary = {
        "research": "ST-ADX V3.3 baseline vs baseline plus continuation",
        "snapshot_utc": utc(snapshot),
        "universe_requested": TOP_N,
        "universe_loaded": len(universe),
        "start_utc": utc(START),
        "end_exclusive_utc": utc(END),
        "symbols_with_failures": len(failures),
        "directional_rows": len(result),
        "aggregate": {},
        "caveats": [
            "Per-coin simulations are independent, not a combined portfolio replay.",
            "Uses existing V3.3 simulator conventions: 0.05% fee per side and 2 bps adverse slippage; funding remains excluded.",
            "Continuation candidates are merged with baseline entries; trades are replayed independently per coin and side.",
            "Top-150 current-volume universe has survivorship/selection bias.",
            "No live paper state, strategy file, or workflow is changed.",
        ],
    }
    if not result.empty:
        for side, group in result.groupby("side"):
            summary["aggregate"][side] = {
                "baseline_trades": int(group.baseline_trades.sum()),
                "combined_trades": int(group.combined_trades.sum()),
                "added_closed_trades_net": int(group.added_closed_trades.sum()),
                "baseline_net_R_sum": round(float(group.baseline_net_R.sum()), 4),
                "combined_net_R_sum": round(float(group.combined_net_R.sum()), 4),
                "baseline_pf_weighted_by_coin_not_valid_portfolio": None,
                "combined_pf_weighted_by_coin_not_valid_portfolio": None,
                "median_coin_pf_baseline": round(float(group.baseline_pf.median()), 4),
                "median_coin_pf_combined": round(float(group.combined_pf.median()), 4),
            }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    run()
