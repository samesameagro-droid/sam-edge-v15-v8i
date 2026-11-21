# ST-ADX V3.3 Trend Continuation — Research Branch

**Status: RESEARCH ONLY. Not deployed. V3.3 active strategy and workflow are unchanged.**

## Purpose
Test whether mature trends produce additional valid continuation opportunities that the original reversal-oriented RSI windows miss.

## Files
- `continuation_engine.py`: pandas signal module that consumes the columns from `strategy_core.add_indicators`.
- `ST_ADX_V3_3_Continuation_Research.pine`: separate TradingView overlay for visual inspection. It is explicitly marked NOT ACTIVE.

## Candidate logic
1. Trend regime: SuperTrend direction and EMA53 side agree.
2. Strength: ADX > 25 and not falling versus the prior completed candle. This deliberately relaxes V3.3's ADX > 30 plus two consecutive rising bars.
3. Pullback continuation: within the preceding 8 completed candles, price traded into the EMA53 tolerance band; current candle closes directionally back with the trend.
4. Breakout/breakdown: close clears the prior 20-candle high/low by 0.10 ATR; volume is at least 1.20 times its 20-candle average.
5. Anti-chasing: absolute EMA53 distance is at most 2.5 ATR.
6. Cooldown: 8 bars per direction; one event per candidate episode.

## Important limitations
- These are hypotheses, not validated parameters.
- Pullback and breakout candidates may overlap; the first release is a candidate detector, not a position manager.
- No portfolio sizing, fees, funding, slippage, stop/target execution, or exchange reconciliation is implemented in this module.
- Pine and Python implementations must be parity-tested on identical OHLCV before relying on alerts.
- The module does not modify or replace original V3.3 reversal signals.

## Validation plan
1. Compare baseline V3.3 versus baseline + continuation, LONG and SHORT separately.
2. Report added signals, unique trades, win rate, PF, net R, max drawdown, and monthly distribution.
3. Apply the same fee/slippage/funding assumptions as the paper runner.
4. Inspect missed and rejected candidates, including extended trend examples.
5. Require out-of-sample improvement and candle-by-candle Python/Pine parity before integration into the paper runner.
