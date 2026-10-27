import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


def sample_ohlcv(n=240):
    close = np.linspace(10, 20, n)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
        "open": close - 0.1,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.full(n, 1000.0),
    })


class IndicatorTests(unittest.TestCase):
    def test_indicators_add_expected_columns(self):
        result = engine.indicators(sample_ohlcv())
        for column in ("ema20", "ema50", "ema200", "atr", "vol_sma20", "rsi"):
            self.assertIn(column, result.columns)
        self.assertTrue(np.isfinite(result["atr"].iloc[-1]))
        self.assertGreater(result["atr"].iloc[-1], 0)

    def test_swing_levels_only_return_price_pairs(self):
        lows, highs = engine.swing_levels(sample_ohlcv(), left_right=3)
        self.assertTrue(all(len(item) == 2 for item in lows + highs))
        self.assertTrue(all(isinstance(item[0], int) for item in lows + highs))
        self.assertTrue(all(isinstance(item[1], float) for item in lows + highs))

    def test_rsi_handles_monotonic_uptrend_without_nan(self):
        frame = sample_ohlcv(240)
        frame["open"] = frame["close"] - 0.05
        frame["high"] = frame["close"] + 0.1
        frame["low"] = frame["close"] - 0.1
        result = engine.indicators(frame)
        self.assertEqual(float(result["rsi"].iloc[-1]), 100.0)

    def test_trend_label_is_known_for_sufficient_history(self):
        label = engine.trend_label(sample_ohlcv())
        self.assertIn(label, {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"})

    def test_target_selection_requires_three_distinct_overhead_levels(self):
        self.assertEqual(
            engine.select_three_targets([10.2, 10.5, 11.0, 12.0], 10.0),
            [10.2, 11.0, 12.0],
        )
        self.assertIsNone(engine.select_three_targets([10.2, 10.5], 10.0))

    def test_target_selection_merges_near_duplicate_resistance(self):
        targets = engine.select_three_targets([10.2, 10.205, 10.8, 11.5], 10.0)
        self.assertIsNotNone(targets)
        self.assertEqual(len(targets), 3)
        self.assertTrue(targets[0] < targets[1] < targets[2])

    def test_candidate_tp_allocations_are_explicit(self):
        self.assertEqual(engine.Candidate.__dataclass_fields__["tp_allocations_pct"].name,
                         "tp_allocations_pct")


if __name__ == "__main__":
    unittest.main()
