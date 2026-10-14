"""Pure, testable Precision V2 entry-gate policy."""
from __future__ import annotations

import math
from typing import Any, Mapping


def precision_gate_decision(
    shadow_e: Mapping[str, Any],
    score: Any,
    enabled: bool = True,
    score_max: float = 65.0,
) -> dict[str, Any]:
    """Evaluate Precision V2 without conflating score ranking and eligibility.

    A candidate is eligible only when the E checks pass and its finite score
    is strictly below the configured upper bound. If the gate is disabled,
    it does not veto the candidate, but diagnostics still report score quality.
    """
    try:
        numeric_score = float(score)
    except (TypeError, ValueError, OverflowError):
        numeric_score = float("nan")

    try:
        numeric_max = float(score_max)
    except (TypeError, ValueError, OverflowError):
        numeric_max = float("nan")

    score_finite = math.isfinite(numeric_score)
    score_in_range = score_finite and 0.0 <= numeric_score <= 100.0
    max_finite = math.isfinite(numeric_max)
    score_ok = score_in_range and max_finite and numeric_score < numeric_max
    e_ok = bool(shadow_e.get("e_pass", False))
    is_enabled = bool(enabled)
    passed = (not is_enabled) or (e_ok and score_ok)

    return {
        "enabled": is_enabled,
        "pass": passed,
        "e_pass": e_ok,
        "score_ok": score_ok,
        "score_finite": score_finite,
        "score_in_range": score_in_range,
        "score_max": numeric_max,
        "score_gate_applied": is_enabled,
        "score_role": "upper_bound_quality_gate",
    }
