"""Research-only continuation signals for ST-ADX V3.3.

This module is deliberately separate from strategy_core.py and the active paper
runner. It consumes the indicator columns produced by strategy_core.add_indicators
and emits candidate signals; it does not place orders or alter V3.3 signals.
"""
from __future__ import annotations

import pandas as pd

# Conservative starting hypotheses, not optimized production parameters.
PULLBACK_LOOKBACK = 8
BREAKOUT_LOOKBACK = 20
ADX_FLOOR = 25.0
MAX_EXTENSION_ATR = 2.5
PULLBACK_ABOVE_ATR = 0.50
PULLBACK_BELOW_ATR = 0.75
BREAKOUT_BUFFER_ATR = 0.10
VOLUME_MULTIPLIER = 1.20
COOLDOWN_BARS = 8


def add_continuation_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with continuation candidate/setup/trigger columns.

    Required columns are created by strategy_core.add_indicators, plus OHLCV.
    All rolling breakout references exclude the current candle. Signal columns
    are only candidates on completed candles; callers must still enforce
    bar-close execution and portfolio risk limits.
    """
    required = {
        "open", "high", "low", "close", "volume", "ema53", "atr_risk",
        "adx", "st_bull", "st_bear",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing continuation inputs: {sorted(missing)}")

    out = df.copy()
    atr = out["atr_risk"].where(out["atr_risk"] > 0)
    ext = (out["close"] - out["ema53"]).abs() / atr

    # Trend must agree with both SuperTrend and EMA53. ADX need not rise for
    # two consecutive bars: that old gate can suppress valid mature trends.
    strength = (out["adx"] > ADX_FLOOR) & (out["adx"] >= out["adx"].shift(1))
    long_regime = out["st_bull"].astype(bool) & (out["close"] > out["ema53"])
    short_regime = out["st_bear"].astype(bool) & (out["close"] < out["ema53"])

    # Pullback/retest: a recent candle traded into the EMA53 tolerance band;
    # current candle closes back in trend direction with directional body.
    long_touch = (
        (out["low"] <= out["ema53"] + PULLBACK_ABOVE_ATR * atr)
        & (out["low"] >= out["ema53"] - PULLBACK_BELOW_ATR * atr)
    )
    short_touch = (
        (out["high"] >= out["ema53"] - PULLBACK_ABOVE_ATR * atr)
        & (out["high"] <= out["ema53"] + PULLBACK_BELOW_ATR * atr)
    )
    long_retest = long_touch.shift(1).rolling(PULLBACK_LOOKBACK, min_periods=1).max().fillna(0).astype(bool)
    short_retest = short_touch.shift(1).rolling(PULLBACK_LOOKBACK, min_periods=1).max().fillna(0).astype(bool)
    long_pullback = (
        long_regime & strength & long_retest & (out["close"] > out["open"])
        & (ext <= MAX_EXTENSION_ATR)
    )
    short_pullback = (
        short_regime & strength & short_retest & (out["close"] < out["open"])
        & (ext <= MAX_EXTENSION_ATR)
    )

    # Breakout/breakdown: compare close with prior completed range; require
    # volume confirmation and avoid entries already extended far from EMA.
    prior_high = out["high"].shift(1).rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK).max()
    prior_low = out["low"].shift(1).rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK).min()
    volume_avg = out["volume"].rolling(20, min_periods=20).mean()
    volume_ok = out["volume"] >= volume_avg * VOLUME_MULTIPLIER
    long_breakout = (
        long_regime & strength & (out["close"] > prior_high + BREAKOUT_BUFFER_ATR * atr)
        & volume_ok & (ext <= MAX_EXTENSION_ATR)
    )
    short_breakdown = (
        short_regime & strength & (out["close"] < prior_low - BREAKOUT_BUFFER_ATR * atr)
        & volume_ok & (ext <= MAX_EXTENSION_ATR)
    )

    candidates = {
        "long_cont_pullback_candidate": long_pullback,
        "short_cont_pullback_candidate": short_pullback,
        "long_cont_breakout_candidate": long_breakout,
        "short_cont_breakdown_candidate": short_breakdown,
    }
    for name, values in candidates.items():
        out[name] = values.fillna(False).astype(bool)

    # One trigger per candidate episode plus a directional cooldown. Separate
    # event streams allow a new setup after a fresh reset, not every candle.
    for side, names in {
        "long": ("long_cont_pullback_candidate", "long_cont_breakout_candidate"),
        "short": ("short_cont_pullback_candidate", "short_cont_breakdown_candidate"),
    }.items():
        combined = out[list(names)].any(axis=1)
        raw = combined & ~combined.shift(1, fill_value=False)
        allowed = []
        last = -10**9
        for i, value in enumerate(raw.to_numpy(bool)):
            fire = bool(value and i - last > COOLDOWN_BARS)
            allowed.append(fire)
            if fire:
                last = i
        out[f"{side}_continuation_trigger"] = allowed

    out["long_continuation_trigger"] = out["long_continuation_trigger"].fillna(False).astype(bool)
    out["short_continuation_trigger"] = out["short_continuation_trigger"].fillna(False).astype(bool)
    return out
