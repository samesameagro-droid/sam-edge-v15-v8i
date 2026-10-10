# SAM EDGE Historical Replay — Simulator Audit Gate

Date: 2026-10-10 UTC
Scope: `research/core_v3_historical_sim.py`, `research/core_v3_strategy_family_backtest.py`, and workflow run 38011762599.
Branch: `research/core-v3-historical-sim`
Status: AUDIT FINDINGS — do not promote any candidate to paper/live based on these results.

## Confirmed findings

1. **Entry candle is not evaluated for exits.** Both replay implementations skip exit checks when `ts <= entry_time` (Core V2/V3 replay) or `ts <= p["entry_time"]` (strategy-family replay). Because a position is inserted after the exit-management pass at its entry timestamp, its entry candle is never checked for SL/TP. If price touches SL/TP between the entry open and that candle's close, the simulator ignores it. This can bias results materially.
2. **Core V2/V3 replay does not model stop gaps or transaction costs.** In `core_v3_historical_sim.py`, any stop touch is assigned exactly -1R and TP exactly +RR, irrespective of a gap through the stop; fee, slippage and funding are not deducted. Those numbers must not be compared directly with cost-adjusted family-backtest results.
3. **Drawdown is closed-trade-only.** Both summaries calculate drawdown from cumulative realized trade R. Neither includes mark-to-market PnL on open positions, so reported maximum drawdown is not a full portfolio-equity drawdown and can understate risk.
4. **Strategy-family BREAKOUT_RETEST does not require a retest.** Its signal is a close beyond a prior swing level plus volume/ATR conditions; there is no later retest/reclaim state before entry. Treat it as a breakout signal, not a breakout-retest strategy.
5. **Period-end marks are counted as closed outcomes in family metrics.** `PERIOD_END_MARK` positions are marked to the last close and included in win rate, expectancy and profit factor. This is useful for marked-to-market net R but mixes realized exits with censored/open-at-boundary positions. Report these separately or explicitly label the metric as including marks.
6. **Historical venue mismatch remains.** The downloader falls back to Binance USD-M monthly archives when BingX history is short. This is a useful proxy but is not exchange-exact for the intended BingX execution venue.
7. **Funding is excluded.** Both result families explicitly say historical funding has not been joined; multi-hour/perpetual positions therefore have incomplete costs.

## Required corrective sequence

1. Patch both replay engines to process the entry candle from its OPEN onward, with deterministic conservative SL/TP ordering when both levels are touched. Do not simply change a timestamp comparison unless entry positions are actually inserted before that candle's exit pass.
2. Model stop gaps using adverse opening price; deduct round-trip fees and slippage in the V2/V3 replay using configurable assumptions.
3. Add portfolio equity marking on every timestamp (realized + unrealized PnL) and calculate maximum drawdown from that equity curve. Keep realized-trade drawdown as a separate metric.
4. Rename the current `BREAKOUT_RETEST` family to `BREAKOUT`, or implement an explicit retest state machine before retaining the retest label.
5. Separate TP/SL/TIME realized exits from period-end open-position marks in win rate and profit factor. Report marked net R separately.
6. Record per-coin candle counts, missing intervals, source venue, duplicate timestamps and data coverage; reject or flag inconsistent intervals.
7. Re-run all variants with fee/slippage sensitivity, then test funding where historical data is available. Preserve the untouched holdout and do not tune parameters against it.
8. Compare only after the corrected run is complete. Candidate promotion gate remains: win rate 65–70%, net +15R to +20R on the defined evaluation sample, max portfolio drawdown no worse than -5R, positive expectancy, PF > 1, and no material collapse on test/holdout. These are target gates, not guaranteed outcomes.

## Decision

The workflow's GitHub Actions conclusion `success` means the job completed; it does not validate simulator correctness or prove strategy profitability. The current metrics are exploratory and are not sufficient for real-money deployment.
