#!/usr/bin/env python3
"""Research-only Core V3 candidate; NOT wired into main_paper_v15.py.

Candidate chosen from the Jan 2025-Aug 2026 Binance Futures historical replay:
V2 base signals + the existing Precision V2 gates, with a stricter entry score
cap of <40. This is only a candidate: the untouched holdout, fees/slippage, and
BingX-specific execution validation have not passed. Do not enable for live trading.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core_engine_v15 import (
    CORE_NAME,
    ADX_LONG_PCT,
    ADX_SHORT_PCT,
    ADX_LONG_DELTA,
    ADX_SHORT_DELTA,
    EARLY_ROOM_MIN_ATR,
    signal_mask,
)

CORE_V3_NAME = "SAM_EDGE_CORE_V3_RESEARCH_SCORE40"
SCORE_CAP = 40.0
PRECISION_VOLUME_MIN = 1.20
PRECISION_EMA_DIST_MAX_ATR = 0.80
PRECISION_PULLBACK_MAX_ATR = 0.60
ADX_EXHAUSTION_PCT = 0.90


def score_at(x: pd.DataFrame, i: int, side: str) -> float:
    """Mirror main_paper_v15.py's entry score formula."""
    adx_pct = float(x.h4_adx_pct.iloc[i])
    adx_delta = float(x.h4_adx_delta.iloc[i])
    adx_floor = ADX_LONG_PCT if side == "LONG" else ADX_SHORT_PCT
    delta_floor = ADX_LONG_DELTA if side == "LONG" else ADX_SHORT_DELTA
    adx_margin = float(np.clip((adx_pct - adx_floor) / (1 - adx_floor), 0, 1))
    delta_margin = float(np.clip((adx_delta - delta_floor) / 2.0, 0, 1))
    room = float(x.dist_res_atr.iloc[i] if side == "LONG" else x.dist_sup_atr.iloc[i])
    room_margin = float(np.clip((room - EARLY_ROOM_MIN_ATR) / 1.50, 0, 1))
    dist = float(x.dist_ema20_atr.iloc[i])
    location = float(np.clip(1.0 - abs(dist - 0.60) / 0.60, 0, 1))
    volume = float(np.clip(float(x.volr.iloc[i]) / 2.0, 0, 1))
    trigger = float(np.clip(float(x.body_atr.iloc[i]) / 0.80, 0, 1))
    return 100.0 * (
        0.30 * adx_margin
        + 0.20 * delta_margin
        + 0.20 * room_margin
        + 0.15 * location
        + 0.10 * volume
        + 0.05 * trigger
    )


def gate_snapshot(x: pd.DataFrame, i: int, side: str) -> dict:
    """Return explicit Core V3 gate diagnostics for a completed 15m signal candle."""
    score = score_at(x, i, side)
    adx_pct = float(x.h4_adx_pct.iloc[i])
    adx_delta = float(x.h4_adx_delta.iloc[i])
    volr = float(x.volr.iloc[i])
    dist = float(x.dist_ema20_atr.iloc[i])

    if side == "LONG":
        touch = x.low <= x.ema20 + PRECISION_PULLBACK_MAX_ATR * x.atr
    else:
        touch = x.high >= x.ema20 - PRECISION_PULLBACK_MAX_ATR * x.atr
    fresh_pullback = any(
        i - age >= 0 and bool(touch.iloc[i - age])
        for age in (1, 2)
    )
    adx_ok = not (adx_pct >= ADX_EXHAUSTION_PCT and adx_delta < 0)
    volume_ok = np.isfinite(volr) and volr >= PRECISION_VOLUME_MIN
    ema_ok = np.isfinite(dist) and dist <= PRECISION_EMA_DIST_MAX_ATR
    score_ok = np.isfinite(score) and score < SCORE_CAP
    passed = bool(fresh_pullback and adx_ok and volume_ok and ema_ok and score_ok)
    blockers = []
    if not fresh_pullback:
        blockers.append("no_fresh_pullback")
    if not adx_ok:
        blockers.append("adx_exhaustion")
    if not volume_ok:
        blockers.append("volume_below_1.20")
    if not ema_ok:
        blockers.append("ema_distance_above_0.80_atr")
    if not score_ok:
        blockers.append("score_not_below_40")
    return {
        "core": CORE_V3_NAME,
        "side": side,
        "score": float(score),
        "score_cap": SCORE_CAP,
        "adx_pct": adx_pct,
        "adx_delta": adx_delta,
        "volr": volr,
        "dist_ema20_atr": dist,
        "fresh_pullback": bool(fresh_pullback),
        "adx_ok": bool(adx_ok),
        "volume_ok": bool(volume_ok),
        "ema_distance_ok": bool(ema_ok),
        "score_ok": bool(score_ok),
        "pass": passed,
        "blockers": blockers,
    }


def signal_mask_v3(x: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return LONG/SHORT masks for research replay; input must already be enriched."""
    long_base, short_base = signal_mask(x, CORE_NAME)
    long_v3 = long_base.copy().fillna(False).astype(bool)
    short_v3 = short_base.copy().fillna(False).astype(bool)
    for i in np.flatnonzero(long_v3.to_numpy()):
        if not gate_snapshot(x, int(i), "LONG")["pass"]:
            long_v3.iloc[i] = False
    for i in np.flatnonzero(short_v3.to_numpy()):
        if not gate_snapshot(x, int(i), "SHORT")["pass"]:
            short_v3.iloc[i] = False
    return long_v3, short_v3
