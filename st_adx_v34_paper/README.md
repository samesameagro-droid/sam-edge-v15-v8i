# ST-ADX V3.4 PAPER Forward Test (isolated)

- Strategy: V3.4, 1H entries.
- Only V3.4 change vs V3.3: LONG requires BTC 1H SuperTrend bullish on the exact same fully closed signal candle (`st_direction == -1`). SHORT unchanged.
- All indicator periods, RSI ranges, ADX threshold/rising rule, SL/TP, 3.25 ATR10 SHORT extension, allowlists, priorities, fees, slippage, sizing, and risk caps are inherited unchanged from V3.3.
- PAPER only. No exchange order endpoints are called. Public BingX OHLCV and ticker snapshots are read-only.
- Initial PAPER equity: 100 USDT.
- Evaluation target: 50 started trades, all must close before `evaluation_complete=true`. Once 50 trade slots (closed + open) are reached, no new entries are accepted; existing PAPER positions continue to be managed to closure.
- State and journals are isolated in this folder: `paper_state.json`, `paper_signals.csv`, `paper_trades.csv`. No V3.3 state/journal is reused.
- Telegram labels explicitly say V3.4 PAPER ONLY.
- The existing V3.3 runner and workflow on `main` are untouched by this branch.

## Pre-forward parity evidence

1. Re-ran the exact V3.4 full historical portfolio replay on the same 23-coin 1H dataset. Baseline reproduced exactly: 124 trades, 54.03% WR, PF 3.3417814942, net +180.6397199621 USDT. V3.4 reproduced exactly: 102 trades, 59.8039% WR, PF 4.7902258248, net +185.4912369095 USDT.
2. Indicator parity compared the live-style pandas `add_indicators()` against the independent rolling-300 Numba replay engine on 115 deterministic windows across all 23 coins, including raw signal windows and non-signal checkpoints. Result: zero mismatches across ATR10, ST direction, LONG signal, SHORT signal, ADX, RSI, and SHORT extension.
3. BTC gate truth table: direction -1 passes; +1, 0, and missing data fail closed.
4. The branch workflow runs preflight checks before the PAPER runner job. PAPER job is gated on preflight success.

This is an isolated deployment candidate. It does not change the `main` branch until parity has passed and the branch has been reviewed.
