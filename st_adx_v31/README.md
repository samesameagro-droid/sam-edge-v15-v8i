# ST-ADX V3.3 — GitHub BingX Paper Forward

- Active paper strategy: **ST-ADX V3.3**. Workflow path remains `.github/workflows/sam_edge_v15_paper.yml` so the existing cron-job.org `workflow_dispatch` endpoint remains compatible.
- Public BingX USDT-linear perpetual OHLCV only; no exchange API key, private endpoint, or order endpoint.
- Timeframe: 1H; SuperTrend 12/2.8; DMI/ADX 40/40; ADX > 30 and rising on both consecutive changes; RSI 12; EMA53; ATR10 SL 1.2x / TP 3.5x.
- Signal filters: LONG RSI 60–70 inclusive; SHORT RSI 35–40 inclusive; reject SHORT when (EMA53 - close) / ATR10 > **3.25**. No additional LONG EMA-extension cap.
- Universe: 23 unique coins / 25 directional models. LONG: ENA, XRP, ZEC, TIA, ORDI, AKE, WIF, APR, XLM, VIRTUAL, DYDX. SHORT: ARB, AVAX, BTC, ENA, ETH, LINK, NEAR, SOL, SUI, UNI, XRP, DOT, LTC, ADA.
- Dynamic portfolio risk: target 1% estimated all-in stop risk per position; hard aggregate open-risk ceiling 5% of current equity. Notional cap 45% of current equity per position. No fixed position-count cap. Risk reserve includes stop loss, 0.05% entry/exit fees and 2 bps adverse stop slippage. Funding excluded.
- Initial paper capital: **100 USDT**. State was migrated from 100,000 to 100 USDT while preserving signal timestamps; at migration there were no open positions and no closed trades in the paper journal.
- Entry signals remain based on the closed 1H strategy candle. While a workflow run is active, open paper positions are checked against BingX ticker `last` every 3 seconds for up to 210 seconds; TP/SL closes are written to `paper_trades.csv` and sent to Telegram with up to 3 delivery attempts.
- The external cron-job.org dispatcher is still configured separately and this repository cannot change its cadence. The documented current cadence is every 5 minutes, so this is near-real-time polling during an active run, **not a guaranteed instant alert**; dispatch gaps, GitHub queue/startup time, API latency, and Telegram delivery can still delay a notification. For true exchange-side protection, a separate LIVE execution service with BingX protective orders and private position/order reconciliation is required. This runner remains PAPER ONLY.
- Each GitHub Actions invocation runs one scan plus a bounded exit-monitor window and exits. Workflow filename and `workflow_dispatch` trigger remain unchanged.
- State/journal: `paper_state.json`, `paper_signals.csv`, `paper_trades.csv`.
- TradingView mirror: `ST_ADX_V3_3_TradingView_Mirror.pine`. Pine is a visual signal mirror only; GitHub paper runner remains authoritative for portfolio risk, positions, fills, equity and Telegram.
- SAM EDGE V15 and V3.2 historical files are retained as archive/reference. They are not the active strategy.

**PAPER ONLY.** No live exchange orders are sent. Backtest performance is not a guarantee of future results.

- Cumulative V3.3 forward journal: `ST_ADX_V3_3_TRADING_JOURNAL.md` (human-readable) and `st_adx_v3_3_trading_journal.csv` (structured ledger). These are separate from the historical V3.1 journal.
- Exploratory V3.4 backtest (research only; V3.3 remains active): `ST_ADX_V3_4_EXPLORATORY_BACKTEST.md`.
