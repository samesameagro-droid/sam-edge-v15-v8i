# ST-ADX V3.3 — TradingView mirror and synchronization boundary

## Active system
ST-ADX V3.3 is the active paper-trading engine. The GitHub runner in `.github/workflows/sam_edge_v15_paper.yml` is authoritative for paper entries/fills, open positions, SL/TP and close events, equity, quantity, fees, slippage, portfolio risk, and Telegram notifications.

Paper capital is 100 USDT. Strategy settings are maintained in `strategy_core.py` and the runner. SHORT EMA53 extension cap is 3.25 ATR10; all other V3.2 settings remain unchanged.

Persistent state: `paper_state.json`, `paper_signals.csv`, `paper_trades.csv`.

## TradingView script
Use `ST_ADX_V3_3_TradingView_Mirror.pine` on a **BingX USDT perpetual 1H chart**. It mirrors V3.3 signal conditions and next-candle-open entry convention, including 2 bps adverse entry slippage, and displays indicative ATR10 stop/target levels.

Pine cannot coordinate the 5% aggregate risk ceiling across all 23 coins and cannot read GitHub state. It is a visual signal mirror, not an execution engine or source of truth.

## Source of truth
1. GitHub `paper_state.json`: paper open positions and entry/SL/TP.
2. GitHub `paper_trades.csv`: completed paper trade outcomes.
3. GitHub runner: Telegram notifications.
4. TradingView: visual signal verification only.

The chart should use the same BingX USDT perpetual feed and 1H timeframe. Compare completed candles only. Existing TradingView alerts must be recreated after replacing the Pine script.

## Rollback
V3.2 and SAM EDGE V15 remain historical references. Do not switch the active runner back unless explicitly requested.
