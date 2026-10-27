# Build status — SAM EDGE LONG TERM

## Implemented in the current prototype
- [x] Separate branch and directory; existing SAM EDGE V15 files untouched
- [x] BingX Spot market discovery via CCXT
- [x] 1D candles with 1W context
- [x] Expanded default scan cap and configurable minimum 24h quote volume
- [x] Support-pullback and breakout/retest watch setups
- [x] Sideways compression + volume/candle/ATR expansion detector for GOD CANDLE candidates
- [x] Anti-chase status: wait for retest if breakout is extended
- [x] Structural SL with ATR buffer
- [x] Three chart-derived target fields, proposed 30/30/40 allocations
- [x] RR used as a quality gate after levels are chosen
- [x] Ranked paper-only JSON candidates

## Not completed / not validated
- [ ] Run syntax and runtime checks in target Python 3.11 environment
- [ ] Automated unit tests
- [ ] Historical portfolio replay with fees, slippage, realistic fills, and no look-ahead
- [ ] Walk-forward plus untouched holdout test
- [ ] Validate the breakout detector across failed breakouts, coin delistings, and changing market regimes
- [ ] Risk-based position sizing and portfolio-level exposure limits
- [ ] Position state, partial-fill management, and duplicate-entry protection
- [ ] Telegram alerts and monitoring
- [ ] Verified live Spot SL/TP order handling (disabled; not implemented)
- [ ] Paper forward test and report

**Safety status:** PAPER ONLY. Do not treat scanner output as a verified entry instruction.
