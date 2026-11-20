# ST-ADX V3.3 Signal Only

Dedicated manual-alert path based on the unchanged `st_adx_v31/strategy_core.py` V3.3 baseline. Public BingX USDT-linear perpetual OHLCV only. Sends Telegram alerts only; no exchange order endpoint, no paper-position management, no TP1/TP2, no BEP, and no trailing stop. Entry reference uses the next 1H candle open with the existing 2 bps adverse entry assumption; SL 1.2 ATR10 and final TP 3.5 ATR10. Telegram alert is informational and is not a guaranteed execution price.

The first run initializes deduplication cursors and deliberately skips historical signals. Subsequent runs evaluate only newly confirmed 1H candles. `signal_state.json` is runtime state and must be persisted by the workflow to prevent duplicate alerts.

Workflow: `.github/workflows/st_adx_v33_signal.yml`. Manual trigger is `workflow_dispatch`; scheduled fallback is hourly. For a faster cadence, cron-job.org must be separately pointed to this workflow's dispatch endpoint after the workflow is merged and its permissions are verified. The existing V3.4 workflow is intentionally untouched.
