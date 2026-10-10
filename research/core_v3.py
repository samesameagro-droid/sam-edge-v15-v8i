"""SAM EDGE Core V3 research candidate.

IMPORTANT:
- Research-only module. It is NOT imported by main_paper_v15.py.
- Does not modify core_engine_v15.py or the active V2 paper engine.
- Candidate was selected from an initial historical screen: V2 Precision gate plus
  a strict score cap (<40). It remains UNVALIDATED because the Sep 2026 holdout
  was only 8 trades and lost 3.5R in the first replay.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core_engine_v15 import CORE_NAME, signal_mask

CORE_V3_NAME = "SAM_EDGE_CORE_V3_RESEARCH_SCORE40"
V3_SCORE_CAP = 40.0
V2_MIN_VOLUME_RATIO = 1.20
V2_MAX_EMA20_DISTANCE_ATR = 0.80
V2_ADX_EXHAUSTION_PCT = 0.90


def _score_at(x: pd.DataFrame, i: int, side: str) -> float:
    adx_pct = float(x["h4_adx_pct"].iloc[i])
    adx_delta = float(x["h4_adx_delta"].iloc[i])
    adx_floor = 0.80 if side == "LONG" else 0.85
    delta_floor = 0.75 if side == "LONG" else 0.90
    room = float(x["dist_res_atr"].iloc[i] if side == "LONG" else x["dist_sup_atr"].iloc[i])
    dist = float(x["dist_ema20_atr"].iloc[i])
    adx_margin = float(np.clip((adx_pct - adx_floor) / (1 - adx_floor), 0, 1))
    delta_margin = float(np.clip((adx_delta - delta_floor) / 2.0, 0, 1))
    room_margin = float(np.clip((room - 0.85) / 1.50, 0, 1))
    location = float(np.clip(1 - abs(dist - 0.60) / 0.60, 0, 1))
    volume = float(np.clip(float(x["volr"].iloc[i]) / 2.0, 0, 1))
    trigger = float(np.clip(float(x["body_atr"].iloc[i]) / 0.80, 0, 1))
    return 100 * (
        0.30 * adx_margin + 0.20 * delta_margin + 0.20 * room_margin
        + 0.15 * location + 0.10 * volume + 0.05 * trigger
    )


def signal_mask_v3(x: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return V3 LONG/SHORT entries after base signal + V2 gate + V3 score cap.

    x must be the enriched 15m frame produced by core_engine_v15.enrich().
    The pullback touch must occur in either of the two previous completed bars;
    current signal bar is not counted as the pullback touch.
    """
    long_base, short_base = signal_mask(x, CORE_NAME)
    touch_long = x["low"] <= x["ema20"] + 0.60 * x["atr"]
    touch_short = x["high"] >= x["ema20"] - 0.60 * x["atr"]
    long_out = pd.Series(False, index=x.index, dtype=bool)
    short_out = pd.Series(False, index=x.index, dtype=bool)

    for i in range(2, len(x)):
        side = "LONG" if bool(long_base.iloc[i]) else ("SHORT" if bool(short_base.iloc[i]) else None)
        if side is None:
            continue
        touch = touch_long if side == "LONG" else touch_short
        fresh_pullback = bool(touch.iloc[i - 1] or touch.iloc[i - 2])
        volr = float(x["volr"].iloc[i])
        dist = float(x["dist_ema20_atr"].iloc[i])
        adx_pct = float(x["h4_adx_pct"].iloc[i])
        adx_delta = float(x["h4_adx_delta"].iloc[i])
        adx_not_exhausted = not (
            np.isfinite(adx_pct) and np.isfinite(adx_delta)
            and adx_pct >= V2_ADX_EXHAUSTION_PCT and adx_delta < 0
        )
        score = _score_at(x, i, side)
        allowed = (
            fresh_pullback
            and adx_not_exhausted
            and np.isfinite(volr) and volr >= V2_MIN_VOLUME_RATIO
            and np.isfinite(dist) and dist <= V2_MAX_EMA20_DISTANCE_ATR
            and score < V3_SCORE_CAP
        )
        if allowed and side == "LONG":
            long_out.iloc[i] = True
        elif allowed and side == "SHORT":
            short_out.iloc[i] = True

    return long_out, short_out
