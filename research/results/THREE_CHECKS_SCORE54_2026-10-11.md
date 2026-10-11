# SAM EDGE — Three Validation Checks (2026-10-11)

## Scope and source

Research-only run: [GitHub Actions run #44](https://github.com/samesameagro-droid/sam-edge-v15-v8i/actions/runs/38097083330), commit `9d01d9773ffdcf760c01609daf950035708e9ab4`, branch `research/premium-indicator-benchmark`.

The full-event portfolio replay completed and reported `validation_passed=true`, zero candle-data errors, all 24 configured symbols loaded, 840,960 Binance USD-M 15m candles, and 3,105 signal candidates after the $10M rolling 24h quote-volume gate. Period: 2025-09-25 through exclusive end 2026-09-25. Starting equity $100, 1% risk of current equity, max 5 simultaneous positions, max 3 per side, 60-minute same-symbol/side cooldown, estimated 5 bps fee + 2 bps slippage per side. No live orders were placed.

Artifact: [sam-edge-mtf-30m-replay](https://github.com/samesameagro-droid/sam-edge-v15-v8i/actions/runs/38097083330/artifacts/11687060826).

## 1. Score threshold robustness: 52 / 54 / 56

### Score-only filter (full 365-day portfolio replay)

| Score ceiling | Closed trades | Net estimated R | Net expectancy/trade | Max drawdown |
|---:|---:|---:|---:|---:|
| 52 | 993 | -99.9685R | -0.1007R | 70.372% |
| 54 | 1,025 | -106.7930R | -0.1042R | 72.442% |
| 56 | 1,053 | -103.4448R | -0.0982R | 72.208% |

### Score-only filter (60-day holdout)

| Score ceiling | Closed trades | Net estimated R | Gross PF | Net expectancy/trade |
|---:|---:|---:|---:|---:|
| 52 | 145 | -5.6369R | 1.2002 | -0.0389R |
| 54 | 156 | -6.6792R | 1.1882 | -0.0428R |
| 56 | 162 | -7.3341R | 1.1904 | -0.0453R |

**Conclusion:** the score ceiling by itself is not robust or profitable in the full portfolio replay. The attractive retrospective journal-filter result must not be treated as evidence that score <=54 is a production-ready signal rule.

## 2. Full portfolio replay

| Variant | Closed trades | Net estimated R | Gross PF | Net expectancy/trade | Average trades/30d | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Baseline core | 1,277 | -140.0479R | 1.0109 | -0.1097R | 104.96 | 80.729% |
| Precision V2, no score cap (<65) | 491 | -28.8101R | 1.1253 | -0.0587R | 40.36 | 39.370% |
| Precision V2 + score <=54 | 412 | -12.0013R | 1.1983 | -0.0291R | 33.86 | 25.758% |

The precision gate plus score ceiling reduces the loss versus baseline but remains negative over the full year and exceeds the agreed drawdown tolerance. The lower-cost sensitivity (2 bps fee + 1 bps slippage per side) changes Precision V2's full-period net result to +6.0554R; zero-cost simulation is +32.2044R. This indicates material sensitivity to execution costs and means fees/slippage must be measured realistically, not assumed away.

## 3. Out-of-sample / walk-forward check

Development period ends 2026-07-27; holdout is 2026-07-27 to 2026-09-25 (60 days).

- Development-only selection chose score ceiling **51** for the precision-gated strategy. However, development net result was still **-9.8187R** (331 closed trades), so the selected value did not pass a positive-development-expectancy criterion.
- On holdout, Precision V2 + selected score ceiling 51 returned **+2.4778R net estimated**, 57 closed trades, gross PF 1.4255, net expectancy +0.0435R/trade, and 5.731% max drawdown. Frequency was about 28.5 trades per 30 days.
- The score-only walk-forward selection chose ceiling 40, but its holdout result was only +0.7058R across 99 trades, net expectancy +0.0071R/trade and 11.725% max drawdown.
- The holdout is **not pristine** relative to every earlier research experiment. It is a walk-forward evaluation, not final independent proof.

**Conclusion:** the holdout is encouraging for the precision-gated variant, but it conflicts with negative development and full-period results. Do not retune using this holdout or declare production readiness. Collect a genuinely new forward period as the final untouched evaluation.

## Additional data-integrity finding

The multi-timeframe trade-path reconciliation step failed, although its candle-data validation passed. It reproduced 26 of 28 recorded outcomes on both 1m and 5m paths. Two recorded TP outcomes were not reproduced by Binance OHLC paths:

- IMXUSDT LONG, entry 2026-09-25 22:00 UTC: recorded TP, 1m/5m replay TIMEOUT.
- INJUSDT LONG, entry 2026-10-06 17:30 UTC: recorded TP, 1m/5m replay TIMEOUT.

These must remain explicit reconciliation failures until the source trade/price/exit logic is understood. The overall GitHub workflow therefore ended in failure even though the independent full-event portfolio replay itself passed candle/data validation and completed successfully.

## Decision

1. **Do not promote score <=54 as a standalone filter.**
2. Keep SAM EDGE **PAPER ONLY**.
3. Preserve the current rules while resolving the two outcome mismatches.
4. The next optimization should focus on execution-aware precision and multi-timeframe entry validation, with threshold choices made only on development data. Do not select a new rule from the current holdout.
5. Exit the optimization loop only when a candidate has positive net expectancy in development and full-period replay, tolerable drawdown, robustness to neighboring parameters and realistic costs, and positive results on a new untouched forward period.
