# Core V2 / V3 historical simulation protocol

This work stays on branch research/core-v3-historical-sim. The active main branch, Core V2 engine, paper executor, and journal are not modified.

## Data window and source

- Development data: 2025-01-01 through 2026-08-31 UTC.
- Frozen holdout: 2026-09-05 through 2026-09-30 UTC.
- Universe: ADA, AVAX, BNB, BTC, CRV, DOGE, ETH, FET, LINK, SOL, SUI, UNI, XRP.
- Timeframe: 15m, 58,368 candles per coin for the development window.
- BingX public OHLCV did not return the requested deep history. The downloader therefore uses public Binance USD-M Futures monthly kline archives as a proxy. This means historical wick/price differences versus BingX remain a limitation; the final candidate must be replayed against BingX data before deployment.
- Holdout metrics are reported only for frozen V2 and V3 score-cap-40 candidate. Do not tune parameters after inspecting holdout results.

## Tested variants

- V2 executable: existing V2 signal plus Precision V2 gate.
- ADX rising/expansion thresholds: a small pre-registered grid.
- Score caps: <40, <45, <50, <55.
- Combined score-cap-40 plus ADX rising gate.
- V3 candidate currently implemented as score cap <40 with all other V2 Precision V2 gates preserved. This is research-only and not wired into the active executor.

## Replay assumptions

- Signal evaluated on a completed 15m candle.
- Entry at the next 15m candle open.
- Stop based on signal-close structure and ATR, bounded by 0.8–2.5 ATR.
- Target RR = 1:1.25.
- If SL and TP are both touched in the same candle, count SL first.
- Maximum five concurrent positions; four-bar same-symbol cooldown.
- Results are gross R before fees, slippage, funding, or exchange-specific execution effects.
- Unresolved positions at each period boundary are excluded from closed-trade metrics.

## Interpretation gates

Do not promote V3 merely because full-period net R is positive. Require positive out-of-sample expectancy, PF comfortably above 1 after realistic costs, acceptable drawdown, enough test trades, a successful untouched holdout, and BingX-specific execution validation. If no candidate meets those criteria, the correct decision is to keep V2 unchanged and continue research.

## Run

```bash
python -m pip install ccxt pandas numpy requests
python research/fetch_historical_ohlcv.py --start-date 2025-01-01 --end-date 2026-09-30 --data data
python research/core_v3_historical_sim.py --data data --out research/results
```

Outputs are written to research/results/. Raw OHLCV is included only in the GitHub Actions artifact, not committed to the repository.