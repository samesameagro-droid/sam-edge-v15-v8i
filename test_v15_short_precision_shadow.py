"""Unit checks for the V15.2 SHORT-only shadow diagnostics."""
import pandas as pd

from core_engine_v15 import short_precision_shadow_snapshot


def test_short_core_variant_passes_complete_rejection_setup():
    frame = pd.DataFrame([{
        "close": 98.0, "open": 100.0, "close_pos": 0.20,
        "upper_wick": 0.30, "body_atr": 0.50, "move5_atr": 0.80,
        "dist_ema20_atr": 0.40, "volr": 1.50, "ema20_slope": -0.20,
        "h1_slope21": -0.01,
    }])
    precision = {"fresh_pullback": True}
    btc = {"h1_bear": True, "h4_bear": False}
    result = short_precision_shadow_snapshot(frame, 0, "SHORT", btc, precision)
    assert result["variant_core_v1_pass"] is True
    assert result["variant_btc_h1_v1_pass"] is True
    assert result["variant_btc_h1_without_h4_v1_pass"] is True


def test_short_variant_rejects_extended_or_non_rejection_candle():
    frame = pd.DataFrame([{
        "close": 98.0, "open": 100.0, "close_pos": 0.20,
        "upper_wick": 0.30, "body_atr": 0.50, "move5_atr": 2.10,
        "dist_ema20_atr": 0.40, "volr": 1.50, "ema20_slope": -0.20,
        "h1_slope21": -0.01,
    }])
    result = short_precision_shadow_snapshot(
        frame, 0, "SHORT", {"h1_bear": True, "h4_bear": True},
        {"fresh_pullback": True},
    )
    assert result["variant_core_v1_pass"] is False
    assert result["not_extended"] is False


def test_long_side_is_not_modified_by_short_patch():
    frame = pd.DataFrame([{}])
    result = short_precision_shadow_snapshot(frame, 0, "LONG", {}, {})
    assert result == {"enabled": True, "applicable": False}


if __name__ == "__main__":
    test_short_core_variant_passes_complete_rejection_setup()
    test_short_variant_rejects_extended_or_non_rejection_candle()
    test_long_side_is_not_modified_by_short_patch()
    print("V15.2 SHORT SHADOW UNIT TESTS OK")
