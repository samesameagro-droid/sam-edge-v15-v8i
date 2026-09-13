# SAM EDGE V15 - GitHub Actions

The workflow `.github/workflows/sam_edge_v15_paper.yml` runs the V15 paper engine every 5 minutes and can also be started manually from GitHub Actions.

Required repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow uses Python 3.11, scans the top 100 eligible BingX USDT linear swaps above a $3,000,000 24h quote-volume floor, executes on 15M, uses the V15 4H ADX + 2H strict candle gates, tracks paper TP/SL on 5M, and persists V15 paper state and journal files back to the repository.

The workflow is PAPER ONLY. It does not place exchange orders.
