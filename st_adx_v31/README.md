# ST-ADX V3.1 — GitHub BingX Paper Forward

- Reuses the existing repository, workflow file path, cron-job.org workflow_dispatch trigger, and Telegram Secrets.
- Public BingX USDT-linear perpetual OHLCV only; no exchange API key, private endpoint, or order endpoint.
- Strategy: 1H; SuperTrend 12/2.8; DMI/ADX 40/40; ADX > 30 and rising on both consecutive changes; RSI 12; EMA 53; ATR10 SL 1.2x / TP 3.5x.
- Universe: 23 unique coins / 25 directional models. LONG: ENA, XRP, ZEC, TIA, ORDI, AKE, WIF, APR, XLM, VIRTUAL, DYDX. SHORT: ARB, AVAX, BTC, ENA, ETH, LINK, NEAR, SOL, SUI, UNI, XRP, DOT, LTC, ADA.
- The 11 new directional models are individual research candidates selected at PF >= 1.8 in both baseline and stress tests; this is a paper-forward validation expansion, not live-trading approval.
- Risk target 1% equity; notional cap 45% per position; max 2 active positions; fee 0.05% each side; simulated adverse slippage 2 bps; funding excluded.
- Each GitHub Actions invocation runs one cycle and exits; cron-job.org may keep dispatching this workflow every 5 minutes. Persistent state makes repeated invocations idempotent.
- New paper equity: 100,000 USDT. First cycle arms the system and ignores historical candles.
- State/journal: paper_state.json, paper_signals.csv, paper_trades.csv.
- SAM EDGE V15 source, its completed forward-test journal and summary, and its old workflow are preserved in this repository for rollback.

PAPER ONLY. Not a live-trading approval. Paper fills use OHLC assumptions and exclude funding.
