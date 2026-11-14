# ST-ADX V3.3 — GitHub BingX Paper Forward

- Active paper strategy: **ST-ADX V3.3**. Workflow path remains `.github/workflows/sam_edge_v15_paper.yml` so the existing cron-job.org `workflow_dispatch` endpoint remains compatible.
- Public BingX USDT-linear perpetual OHLCV only; no exchange API key, private endpoint, or order endpoint.
- Timeframe: 1H; SuperTrend 12/2.8; DMI/ADX 40/40; ADX > 30 and rising on both consecutive changes; RSI 12; EMA53; ATR10 SL 1.2x / TP 3.5x.
- Signal filters: LONG RSI 60–70 inclusive; SHORT RSI 35–40 inclusive; reject SHORT when (EMA53 - close) / ATR10 > **3.25**. No additional LONG EMA-extension cap.
- Universe: 23 unique coins / 25 directional models. LONG: ENA, XRP, ZEC, TIA, ORDI, AKE, WIF, APR, XLM, VIRTUAL, DYDX. SHORT: ARB, AVAX, BTC, ENA, ETH, LINK, NEAR, SOL, SUI, UNI, XRP, DOT, LTC, ADA.
- Dynamic portfolio risk: target 1% estimated all-in stop risk per position; hard aggregate open-risk ceiling 5% of current equity. Notional cap 45% of current equity per position. No fixed position-count cap. Risk reserve includes stop loss, 0.05% entry/exit fees and 2 bps adverse stop slippage. Funding excluded.
- Initial paper capital: **100 USDT**. State was migrated from 100,000 to 100 USDT while preserving signal timestamps; at migration there were no open positions and no closed trades in the paper journal.
- Each GitHub Actions invocation runs one cycle and exits. Existing cron-job.org scheduler may continue dispatching this same workflow every 5 minutes; workflow filename and `workflow_dispatch` trigger are intentionally unchanged.
- State/journal: `paper_state.json`, `paper_signals.csv`, `paper_trades.csv`.
- TradingView mirror: `ST_ADX_V3_3_TradingView_Mirror.pine`. Pine is a visual signal mirror only; GitHub paper runner remains authoritative for portfolio risk, positions, fills, equity and Telegram.
- SAM EDGE V15 and V3.2 historical files are retained as archive/reference. They are not the active strategy.

**PAPER ONLY.** No live exchange orders are sent. Backtest performance is not a guarantee of future results.
