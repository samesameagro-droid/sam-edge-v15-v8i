# SAM EDGE V16 — Three Controlled Research Experiments

Status: **protocol prepared; not yet backtested**. This file defines reproducible hypotheses and acceptance criteria. It does not claim any edge or change the production V15 engine.

## Objective

Test three additions independently against the same V15 baseline on identical candles, symbols, fees, slippage, funding, position sizing, stop/target model, and date splits. Do not select a variant by win rate alone.

## Immutable controls for all runs

- Execution timeframe: 15m; context: 1h and 4h; tracker: 5m only if the baseline simulator uses it.
- Same universe and volume threshold as the baseline run; freeze symbol membership per timestamp to avoid survivorship/look-ahead bias.
- Same entry fill assumptions, fees, slippage, funding, position sizing, max concurrent positions, and max holding period.
- Use only closed candles for signal generation. Every swing/pivot must be unavailable until its confirmation candle closes.
- Same fixed baseline stop/target policy for the first comparison. Do not silently mix strategy changes with stop changes.
- Chronological splits: train 60%, validation 20%, untouched final holdout 20%. If the existing project already has a documented split, preserve it and record exact dates.
- Record all signals, rejected signals and rejection reasons, fills, costs, MAE/MFE, R, equity curve, and drawdown.
- Run per-symbol and long/short breakdowns; deduplicate trades and enforce portfolio-level position limits.

## E0 — Control: current V15

Run the exact production baseline without modifications. Save commit SHA, config fingerprint, data range, trade count, and metrics. If this cannot be reproduced, stop and fix the harness before evaluating variants.

## E1 — Regime Filter Only

**Hypothesis:** matching the existing V15 trigger to the prevailing higher-timeframe regime may reduce low-quality counter-regime entries.

**Change from E0:** add only a regime eligibility gate; preserve the V15 trigger and risk logic.
- Trend-up: 4h close above 4h EMA200 and EMA50 slope positive.
- Trend-down: 4h close below 4h EMA200 and EMA50 slope negative.
- Range/uncertain: neither condition; block trend-following entries rather than invent a directional bias.
- LONG allowed only in trend-up; SHORT allowed only in trend-down.
- ADX/volatility may be logged for analysis but are not extra entry filters in this experiment.

**Primary comparison:** net expectancy after costs, profit factor, max portfolio drawdown, trades/month, and missed profitable baseline trades. This deliberately tests a simple regime gate before adding more indicators.

## E2 — Confirmed Structure + Break/Retest

**Hypothesis:** requiring a confirmed structural break and a successful retest may avoid chasing late candles.

**Change from E0:** replace the entry trigger with a confirmed BOS + retest; preserve regime/risk settings from E0 so this remains an independent experiment.
- Define swing highs/lows using a fixed, predeclared pivot rule; a pivot becomes usable only after its right-side confirmation bars close.
- LONG: candle closes above the last confirmed swing high by a predeclared buffer (initial candidate 0.10 ATR); then price retests the broken level within the next 1–8 closed bars and closes back above it.
- SHORT: mirror the logic below the last confirmed swing low.
- Require the signal candle to close before entry. No intrabar hindsight fills.
- Invalidation: beyond the retest swing, with a predeclared ATR safety buffer. Reject if target room cannot support the required net R:R after estimated costs.
- A break without retest expires; do not chase it.

**Sensitivity check:** retest window 1–5 and 1–8 bars, but select using validation only. Do not optimize on the final holdout.

## E3 — Liquidity Sweep + Reclaim

**Hypothesis:** a failed break of a previously confirmed swing, followed by a close back inside the level, may provide a more selective reversal trigger near liquidity pools.

**Change from E0:** replace the entry trigger with sweep/reclaim; preserve baseline risk settings.
- LONG: low pierces the last confirmed swing low by a predeclared buffer, then candle closes back above that swing low. The level must have been known before the sweep candle opened.
- SHORT: high pierces the last confirmed swing high, then candle closes back below it.
- Require rejection/displacement: candle body at least 0.35 ATR and close in the upper 30% of its range for LONG / lower 30% for SHORT. These are initial hypotheses and must be frozen before the run.
- Reject signals when the next opposing confirmed structure leaves insufficient net R:R or estimated costs consume the expected edge.
- Stop beyond the sweep extreme plus a fixed ATR buffer. Never tighten a stop after seeing the result.
- One sweep event can produce at most one trade; no duplicate entries from repeated candles at the same level.

## Reporting template

For E0–E3, report:
- trades, trades/month, win rate, gross R, fees/funding/slippage, net R, expectancy/trade, profit factor, maximum portfolio drawdown in R and %, average win/loss, exposure, and longest losing streak;
- results for train, validation, and untouched holdout separately;
- long vs short, each symbol, and trend/range/volatility regime;
- a full trade ledger and a list of blocked/rejected signals;
- sensitivity and robustness results, including one-bar delayed entry and costs increased by 10%.

## Adoption gate

Do not promote a variant merely because its training result is strongest. A candidate must:
1. have positive net expectancy after all costs on validation and untouched holdout;
2. have profit factor above 1.10 on the untouched holdout (minimum bar, not a guarantee);
3. not exceed the baseline's maximum portfolio drawdown materially;
4. retain positive expectancy under +10% cost stress and one-bar delayed entry;
5. have enough holdout trades for interpretation; fewer than 30 holdout trades is explicitly **inconclusive**, not a pass;
6. be reviewed for concentration in a single coin, side, or short period.

If none passes, retain V15 and report “no validated improvement.” Do not enable live trading based on this experiment alone.

## Reproducibility checklist

- [ ] Pin baseline commit and experiment code commit.
- [ ] Verify data timestamps, gaps, duplicate candles, and symbol delistings.
- [ ] Confirm no future data in pivots, HTF resampling, indicator warm-up, or fills.
- [ ] Match E0 results to the existing baseline before running E1–E3.
- [ ] Save config JSON, data manifest/checksums, trade ledger, and metrics CSV.
- [ ] Keep this work on a research branch; do not alter the live/paper production defaults.
