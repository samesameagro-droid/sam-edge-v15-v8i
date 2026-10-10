# SAM EDGE Core V3 — Indicator Research and Validation Plan

Status: research specification only. Do not import this plan into the active paper/live engine. Do not modify `main` or the existing Core V2 baseline.

## Objective

Search for a robust 15m crypto perpetual strategy using 1H structure and completed 4H regime context. User's aspirational acceptance targets are 65–70% win rate, +15R to +20R net over a predeclared evaluation window, and portfolio maximum drawdown no worse than -5R. These are hypotheses/acceptance targets, not promised outcomes. A candidate that misses them must be reported honestly rather than relabeled as successful.

## First: fix replay validity before optimizing indicators

The existing historical harness is a screening tool, not yet a final execution-grade backtest. Before using it to select a strategy:
- Enforce the engine's MAX_HOLD_BARS=96 and specify the timeout exit rule.
- Build positions and exits on an event-driven bar timeline with no look-ahead. Use next-bar entry only after signal candle close.
- Resolve same-bar SL/TP ambiguity conservatively; where OHLC cannot establish order, count SL first or use finer-grained data.
- Model entry/exit fees, spread, slippage, funding, stop gaps, and realistic order type assumptions; publish baseline and stressed costs.
- Reconcile stop/target calculations with actual executable code and calculate R from the actual filled entry and risk distance.
- Calculate portfolio drawdown with simultaneous open-position exposure, not only a sequence of closed trade outcomes. Include correlated exposure and open-position mark-to-market.
- Report unresolved positions at each period boundary; never silently drop or count them as zero-R closed trades.
- Verify higher-timeframe bars are only joined after they have closed.
- Add unit tests for LONG/SHORT, gaps, simultaneous stops/targets, timeouts, cooldown, overlapping positions, period boundaries, and no-look-ahead.
- Record data provenance, gaps, delisted coins, survivorship bias, and source differences between BingX and Binance data.

## Indicator families and the distinct question each answers

Do not count correlated indicators as independent confirmations. Each retained feature must answer a distinct question and demonstrate incremental out-of-sample value.

1. **Trend / direction:** market structure (HH/HL, LH/LL, swing break), EMA 20/50/200 slope and alignment, anchored/session VWAP, Ichimoku cloud as an alternative trend representation. Prefer structure plus one trend representation, not several redundant moving-average filters.
2. **Momentum / directional strength:** RSI, MACD histogram/slope, ADX with +DI/-DI, rate-of-change. Test alternatives and small combinations; RSI/MACD/ROC overlap substantially.
3. **Volatility / regime:** ATR percentile and ATR expansion, Bollinger Band width/squeeze, realized volatility, range compression/expansion. Use to distinguish trend, range, breakout and unstable high-volatility regimes.
4. **Volume / participation:** relative volume, OBV or Chaikin Money Flow, volume delta/CVD if reliable trade data exists. Raw volume alone is not order flow and can be distorted by venue/source.
5. **Location / structure:** previous swing highs/lows, support/resistance room in ATR, range edges, VWAP deviation, Volume Profile POC/HVN/LVN if reproducible from available data. Avoid look-ahead when constructing levels.
6. **Liquidity / execution context:** sweep-and-reclaim of prior highs/lows, breakout/retest, spread/depth and funding if historically available. Do not claim to test order flow using OHLCV alone.
7. **Risk / exit (not entry signals):** ATR/structure stops, time stop, volatility-adjusted sizing, partial/trailing exits, daily loss cap, correlated-position cap. Compare exits separately from entry filters.

## Candidate playbooks to test separately

### A. Trend pullback continuation
- 4H completed-bar regime: directional trend and acceptable volatility.
- 1H structure agrees with direction.
- 15m pullback toward EMA/VWAP or a tested support/resistance zone.
- Trigger requires reclaim/close confirmation, with volume participation and sufficient room to the next opposing structure.
- Reject extended entries, late impulse candles, poor reward-to-risk, and weak liquidity.

### B. Breakout / retest
- 1H range or structure level defined using only past bars.
- 15m close beyond the level, with volatility expansion and participation.
- Prefer retest/hold over chasing a large breakout candle.
- Reject low-volume breaks and entries directly into nearby opposing structure.

### C. Liquidity sweep / reclaim reversal
- Sweep of a previously established swing/range boundary.
- Close back inside the range and a structural confirmation trigger.
- Use only in explicitly identified range/exhaustion regimes; do not counter-trend indiscriminately.
- Test long and short separately.

### D. Range mean reversion
- Only when regime filter identifies a range, not simply because RSI is overbought/oversold.
- Location near range edge plus a reversal/reclaim trigger; target the range interior or opposing boundary.
- Disable when trend strength/volatility expansion indicates a potential breakout.

Do not merge these playbooks immediately. First test each independently, then test whether a simple regime selector can allocate among them without leakage.

## Experiment design

1. Freeze the data and evaluation windows before tuning. Preserve the existing September holdout untouched; because it has only a handful of trades, it is a warning sample, not sufficient evidence.
2. Establish corrected cost-aware baselines.
3. Run one-family-at-a-time ablations: base price/structure; +regime; +momentum; +volume; +location; +execution filter; exits separately.
4. Use a small, pre-registered parameter grid and parameter-neighbour checks. Do not search unlimited thresholds and then report only the winner.
5. Use rolling walk-forward folds, a final untouched holdout, per-coin and long/short attribution, and a portfolio replay across all simultaneous positions.
6. Run sensitivity tests: fees/slippage at baseline and stressed levels, entry delayed one bar, ATR/stop perturbation ±10%, neighboring parameters, leave-one-coin-out, and Monte Carlo/block-bootstrap of trade order and correlated positions.
7. Publish all variants tried, not just the best. Correct for multiple testing and report uncertainty around win rate and expectancy.
8. Forward test the frozen candidate in paper mode only after historical gates pass; compare signal, fill, fees, and slippage against the simulator.

## Scorecard and gates

Every candidate report must include: closed trades; win rate and confidence interval; net R after costs; expectancy per trade; profit factor; portfolio max drawdown; longest losing streak; exposure/time in market; trades per day and zero-trade days; long/short split; per-coin results; regime split; open positions at period end; and cost stress results.

A candidate fails if any primary target is missed, if results depend on one coin/regime, if modest parameter changes destroy performance, if stressed costs remove the edge, or if the final holdout is negative without a defensible explanation. Do not promote a candidate because it has a high win rate alone.

## Decision rule

The 65–70% win rate target is not to be forced by adding filters until trades become too rare. A strategy with a high win rate can still lose money if average wins are too small; with a fixed 1.25R target and -1R losses, break-even win rate before costs is approximately 44.4%. The objective is a robust positive net expectancy with controlled portfolio drawdown, not a cosmetic win-rate number. If all requested targets cannot coexist, report the trade-off and evidence rather than falsifying the results.

## Sources to consult and verify

- Binance Academy — VWAP: institutional execution benchmark and intraday context.
- Gate Research — comparative crypto tests of MACD, RSI, ADX/DMI, and Bollinger Bands; performance depends on market regime.
- Research on backtest overfitting, transaction costs, nested walk-forward selection, and multiple testing.
- Public practitioner discussions (YouTube, X, Threads, Reddit) are hypothesis generators only; no claimed performance should be accepted without reproducible code/data and out-of-sample validation.
