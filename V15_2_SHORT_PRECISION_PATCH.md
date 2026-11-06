# SAM EDGE V15.2 — SHORT Precision Shadow Patch

## Purpose

Improve SHORT selectivity without disturbing the existing LONG engine or the current V15 paper-forward execution rules.

## Safety mode

This patch is **SHADOW ONLY**:
- It does not veto or create any entry.
- It does not change LONG or SHORT signal masks.
- It does not change entry, stop-loss, take-profit, RR, sizing, active-position limits, or Telegram delivery.
- Existing Telegram eligibility remains controlled by the existing V15 validation gates.

## Diagnostic variants

For every closed, executed SHORT with a saved shadow snapshot, the forward runner appends an outcome-linked row to `v15_short_precision_shadow.csv`.

- `short_core_v1`: fresh EMA20 pullback in the preceding two completed 15m candles, bearish rejection candle (close position <= 0.35, upper wick >= 0.20, body >= 0.20 ATR), no extension (5-bar move <= 1.50 ATR and EMA20 distance <= 0.80 ATR), volume ratio >= 1.20, and negative 15m EMA20 plus 1H EMA21 slope.
- `short_btc_h1_v1`: core V1 plus BTC 1H bearish.
- `short_btc_h1_without_h4_v1`: core V1 plus BTC 1H bearish while BTC 4H is not bearish.

These are research variants, not live execution conditions. The BTC variants are being logged separately because the existing journal showed materially different outcomes across BTC regime groups; those groups are not yet large enough to justify a hard rule.

## Outcome report

`analyze_v15_short_shadow.py` generates `v15_short_precision_shadow_summary.json` after each scheduled paper run. It reports sample size, win rate, net R, profit factor, expectancy, drawdown, and how many winners/losers each variant excludes.

Initial promotion guardrails:
- At least 30 closed SHORT observations for a preliminary read.
- Profit factor >= 1.20 and expectancy >= +0.10R.
- Independent out-of-sample confirmation before any execution promotion.
- A small sample or a filter that only removes trades is not sufficient evidence.

## Baseline at patch design

The current Trade #101+ cohort had 56 closed SHORT trades: 25 wins, 31 losses, +0.25R, PF 1.0081. In the BTC 1H+4H bearish subgroup there were 41 trades: 14 wins, 27 losses, -9.50R. These figures motivate investigation; they do not prove causality.

## Files

- `core_engine_v15.py`: pure SHORT shadow snapshot helper.
- `main_paper_v15.py`: attaches diagnostic snapshot to the existing position record.
- `run_v15_forward_test.py`: writes outcome-linked shadow audit when SHORT closes.
- `analyze_v15_short_shadow.py`: summarizes variants.
- `test_v15_short_precision_shadow.py`: unit tests.
