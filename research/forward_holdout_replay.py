#!/usr/bin/env python3
"""Fixed-rule forward holdout for SAM EDGE; no parameter selection and no orders."""
# Candle fetcher retries recent Binance Vision archive publication and tries official
# Binance USD-M REST host variants; provenance and complete coverage remain mandatory.
from __future__ import annotations
import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from portfolio_event_replay import (
    prepare, build_indices, simulate, stats, SYMBOLS, BAR_MS,
    FEE_BPS, SLIPPAGE_BPS, START_EQUITY, RISK_PCT,
)

HOLDOUT_START = pd.Timestamp("2026-10-10T00:00:00Z")
# Evaluate only completed 15m candles; end is exclusive.
# Use only complete UTC days so delayed daily archives are not mistaken for missing live candles.
HOLDOUT_END = pd.Timestamp.now(tz="UTC").floor("D")
VARIANTS = {
    "PRECISION_V2_BASELINE_LT65": "precision65",
    "PRECISION_V2_SCORE_LE52": "precision_score_52",
    "PRECISION_V2_SCORE_LE54": "precision_score_54",
    "PRECISION_V2_SCORE_LE56": "precision_score_56",
    "SCORE_ONLY_LE54_DIAGNOSTIC": "score_only_54",
}


def main():
    out = Path("research/results/new_forward_holdout")
    out.mkdir(parents=True, exist_ok=True)
    if HOLDOUT_END <= HOLDOUT_START:
        summary = {
            "validation_passed": False,
            "reason": "No completed 15m candles yet in the preregistered holdout window.",
            "holdout_start_utc": HOLDOUT_START.isoformat(),
            "holdout_end_utc_exclusive": HOLDOUT_END.isoformat(),
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        raise SystemExit("No completed candles in the new holdout window.")

    # Load 60 days of warm-up candles so indicators are initialized correctly,
    # but simulate entries only from the preregistered 2026-10-10 start.
    data_start = HOLDOUT_START - pd.Timedelta(days=60)
    start_ms = int(data_start.timestamp() * 1000)
    end_ms = int(HOLDOUT_END.timestamp() * 1000)
    data, coverage, errors, candidates = prepare(start_ms, end_ms)
    summary_base = {
        "source": "Binance USD-M Futures REST/Vision candles; research-only, no orders",
        "holdout_start_utc": HOLDOUT_START.isoformat(),
        "holdout_end_utc_exclusive": HOLDOUT_END.isoformat(),
        "data_warmup_start_utc": data_start.isoformat(),
        "symbols_expected": len(SYMBOLS),
        "symbols_loaded": len(data),
        "total_15m_candles": int(sum(len(x) for x in data.values())),
        "signal_candidates_after_volume_gate": len(candidates),
        "data_errors": errors,
        "coverage": coverage,
        "rule_lock": {
            "selection": "No threshold selection or optimization on this period. 52, 54, and 56 are fixed sensitivity comparisons only.",
            "entry": "Confirmed 15m signal, next 15m open execution.",
            "risk": f"{RISK_PCT*100:.2f}% of current equity per trade; starting equity {START_EQUITY:.2f} USD.",
            "limits": "5 concurrent positions, max 3 per side, 60-minute same-symbol/side cooldown, $10M rolling 24h quote-volume gate.",
            "costs": f"{FEE_BPS} bps fee + {SLIPPAGE_BPS} bps slippage per side, estimated.",
            "scope": "Only entries whose next-open entry timestamp falls within the new holdout window are simulated. No legacy journal outcomes are imported.",
        },
    }
    if errors or len(data) != len(SYMBOLS):
        summary = {**summary_base, "validation_passed": False,
                   "reason": "Candle coverage/provenance validation failed; metrics withheld."}
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        raise SystemExit(f"FORWARD_HOLDOUT_INVALID: {len(errors)} data errors; metrics withheld.")
    if not candidates:
        summary = {**summary_base, "validation_passed": True,
                   "reason": "Data passed, but no eligible candidates in the holdout window.",
                   "fixed_rule_results": {}}
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return

    bar_maps, global_times = build_indices(data)
    result_summary = {}
    trade_rows = []
    for label, mode in VARIANTS.items():
        sim = simulate(
            data, candidates, int(HOLDOUT_START.timestamp()*1000), end_ms, mode,
            force_close=False, fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS,
            bar_maps=bar_maps, global_times=global_times,
        )
        result_summary[label] = stats(sim, (HOLDOUT_END-HOLDOUT_START).total_seconds()/86400)
        result_summary[label]["open_positions_details"] = sim["open_positions"]
        for trade in sim["closed_trades"]:
            trade_rows.append({"variant": label, **trade})
        print(f"FORWARD_HOLDOUT {label}: {json.dumps(result_summary[label], sort_keys=True)}", flush=True)

    pd.DataFrame(trade_rows).to_csv(out / "fixed_rule_trades.csv", index=False)
    summary = {
        **summary_base,
        "validation_passed": True,
        "data_errors": [],
        "fixed_rule_results": result_summary,
        "interpretation": "This is an early forward sample, not a production approval. Do not select a winning threshold from this holdout; collect more new forward data and report uncertainty for small samples.",
        "minimum_sample_warning": "The period began 2026-10-10; fewer than 100 closed trades is insufficient to claim robust profitability.",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"validation_passed": True, "holdout_start_utc": summary["holdout_start_utc"],
                      "holdout_end_utc_exclusive": summary["holdout_end_utc_exclusive"],
                      "fixed_rule_results": result_summary}, indent=2), flush=True)


if __name__ == "__main__":
    main()
