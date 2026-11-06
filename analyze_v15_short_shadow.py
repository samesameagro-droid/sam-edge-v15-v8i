#!/usr/bin/env python3
"""Summarize outcome-linked, SHORT-only shadow variants without changing execution."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT = Path("v15_short_precision_shadow.csv")
OUT = Path("v15_short_precision_shadow_summary.json")
VARIANTS = {
    "baseline_all_short": None,
    "short_core_v1": "variant_core_v1_pass",
    "short_btc_h1_v1": "variant_btc_h1_v1_pass",
    "short_btc_h1_without_h4_v1": "variant_btc_h1_without_h4_v1_pass",
}


def truth(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def stats(rows):
    rs = [float(r["R"]) for r in rows if r.get("R") not in ("", None)]
    wins = sum(x > 0 for x in rs)
    losses = sum(x < 0 for x in rs)
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = -sum(x for x in rs if x < 0)
    running = peak = max_dd = 0.0
    for value in rs:
        running += value
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return {
        "trades": len(rs),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100.0 * wins / len(rs), 2) if rs else None,
        "net_R": round(sum(rs), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else (None if not gross_win else "infinity"),
        "expectancy_R": round(sum(rs) / len(rs), 4) if rs else None,
        "max_drawdown_R": round(max_dd, 4),
    }


def main():
    rows = []
    if AUDIT.exists():
        with AUDIT.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    results = {}
    baseline = rows
    baseline_stats = stats(baseline)
    for name, gate in VARIANTS.items():
        selected = baseline if gate is None else [r for r in baseline if truth(r.get(gate, ""))]
        excluded = [] if gate is None else [r for r in baseline if not truth(r.get(gate, ""))]
        results[name] = {
            **stats(selected),
            "excluded_winners": sum(float(r.get("R") or 0) > 0 for r in excluded),
            "excluded_losses": sum(float(r.get("R") or 0) < 0 for r in excluded),
            "gate_column": gate,
        }
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "SHADOW_ONLY_NO_EXECUTION_IMPACT",
        "source": str(AUDIT),
        "sample_size": len(rows),
        "baseline": baseline_stats,
        "variants": results,
        "promotion_policy": {
            "minimum_closed_short_sample": 30,
            "minimum_profit_factor": 1.20,
            "minimum_expectancy_R": 0.10,
            "require_out_of_sample_confirmation": True,
            "note": "These are research thresholds, not a guarantee. Do not promote a variant based on a small or in-sample-only sample.",
        },
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"SHORT SHADOW SUMMARY | samples={len(rows)} | baseline_net_R={baseline_stats['net_R']} | output={OUT}")


if __name__ == "__main__":
    main()
