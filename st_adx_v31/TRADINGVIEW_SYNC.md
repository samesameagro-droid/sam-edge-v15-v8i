# ST-ADX V3.1 — TradingView mirror and synchronization boundary

## Active system
ST-ADX V3.1 is the only active paper-trading engine. The GitHub runner in `.github/workflows/sam_edge_v15_paper.yml` is authoritative for:
- paper entries and fills;
- open positions, SL/TP and close events;
- equity, quantity, fees, slippage and portfolio risk;
- Telegram entry and close notifications.

Persistent state:
- `paper_state.json`
- `paper_signals.csv`
- `paper_trades.csv`

## TradingView script
Use `ST_ADX_V3_1_TradingView_Mirror.pine` on a **BingX USDT perpetual 1H chart**. It mirrors the ST-ADX V3.1 signal conditions and the next-candle-open entry convention (including 2 bps adverse entry slippage), then displays indicative ATR10 stop/target levels.

## Important technical limitation
Pine Script cannot make arbitrary HTTP requests or read a GitHub raw JSON file. Consequently, a Pine dashboard cannot directly import `paper_state.json` or know whether GitHub has actually opened/closed a paper position. The TradingView script is therefore a **signal-calculation mirror**, not the authoritative portfolio dashboard. Do not treat its local chart state as the GitHub open-position state.

The chart and GitHub can also differ slightly if the chart is not using the same BingX USDT perpetual feed, or if candle timestamps/OHLCV differ. For comparison, use the same BingX contract and 1H timeframe, and compare only completed candles.

## Source of truth
When there is a discrepancy:
1. GitHub `paper_state.json` decides whether a paper position is open and its actual entry/SL/TP.
2. GitHub `paper_trades.csv` decides completed trade outcomes.
3. Telegram messages are emitted by the same GitHub runner.
4. TradingView is for visual signal verification only.

## Historical archive
SAM EDGE V15 scripts, logs and journals remain historical reference material. They are not the active strategy and must not be used as the ST-ADX TradingView script.
