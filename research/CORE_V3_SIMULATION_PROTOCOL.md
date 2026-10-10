# SAM EDGE Core V2 vs Core V3 — historical replay

## Status (2026-10-10 UTC)

The first full historical replay has completed on the isolated branch `research/core-v3-historical-sim`. Core V2 and the active paper engine on `main` were not changed.

The first V3 candidate module is `research/core_v3.py`. It is research-only and is not imported by `main_paper_v15.py`.

## Historical data and replay design

- Universe: ADA, AVAX, BNB, BTC, CRV, DOGE, ETH, FET, LINK, SOL, SUI, UNI, XRP.
- Timeframe: 15-minute OHLCV.
- Data request window: 2025-01-01 through 2026-09-30 UTC. Where BingX's public OHLCV endpoint could not supply sufficient deep history, the downloader fell back to Binance USDT-M Futures monthly archive data. This is a cross-source historical proxy, not a perfect BingX execution replay.
- Signals are evaluated on completed candles; entry is next candle OPEN.
- RR 1:1.25. Stop based on previous structure and signal-candle ATR, bounded by 0.8–2.5 ATR.
- If SL and TP are touched in the same candle, SL is counted first.
- Maximum five simultaneous positions, four-bar same-coin cooldown.
- Metrics are gross R, before fees, funding, spread and slippage.

## First replay results

| Variant | Window | Closed | Win rate | Net R | Expectancy R/trade | Profit factor | Max drawdown R |
|---|---:|---:|---:|---:|---:|---:|---:|
| V2 executable | Full | 546 | 45.42% | +12.00R | +0.0220 | 1.0403 | -32.50R |
| V2 executable | Train (2025-01-01–2026-03-31) | 403 | 46.15% | +15.50R | +0.0385 | 1.0714 | -32.50R |
| V2 executable | Test (2026-04-01–2026-08-31) | 143 | 43.36% | -3.50R | -0.0245 | 0.9568 | -14.50R |
| V2 executable | Holdout (2026-09-05–2026-09-30) | 15 | 40.00% | -1.50R | -0.1000 | 0.8333 | -5.00R |
| V3 score cap <40 | Full | 286 | 47.55% | +20.00R | +0.0699 | 1.1333 | -20.25R |
| V3 score cap <40 | Train | 214 | 48.13% | +17.75R | +0.0829 | 1.1599 | -20.25R |
| V3 score cap <40 | Test | 72 | 45.83% | +2.25R | +0.0312 | 1.0577 | -9.75R |
| V3 score cap <40 | Holdout | 8 | 25.00% | -3.50R | -0.4375 | 0.4167 | -6.00R |
| V3 score <40 + ADX rising | Full | 36 | 50.00% | +4.50R | +0.1250 | 1.2500 | -5.00R |
| V3 score <40 + ADX rising | Test | 11 | 36.36% | -2.00R | -0.1818 | 0.7143 | -5.00R |

## Interpretation

1. The V3 score-cap candidate improves the gross full-window summary versus V2: fewer trades, higher expectancy, higher profit factor, and smaller absolute R drawdown.
2. The later test window shows only a small positive result for score-cap V3 (+2.25R across 72 trades, PF 1.0577), so the edge is weak before costs.
3. The untouched September holdout is negative for both V2 and V3; V3 score-cap lost 3.5R in only eight closed trades. This is too small to estimate performance reliably, but it is a warning, not a pass.
4. The stricter score-cap + ADX-rising variant has only 36 full-window trades and is negative in the test window. Do not select it for deployment.
5. These results are gross, use an OHLC-bar approximation for fills, and do not yet include fees, funding, slippage, latency, or an exact BingX order-book execution model. They are not sufficient to justify live trading.

## Core V3 research definition

`research/core_v3.py` defines `SAM_EDGE_CORE_V3_RESEARCH_SCORE40`:
- Existing V2 base signal (15m entry with 1H/4H alignment).
- V2 precision requirements: fresh pullback touch in either of the two previous completed bars, volume ratio >=1.20, EMA20 distance <=0.80 ATR, ADX-exhaustion veto.
- New V3 candidate filter: score <40 (score weights match the research harness).
- RR remains 1:1.25 and structure stop remains unchanged.

This is a frozen research candidate, **not an approved live core**. Do not import it into the active paper/live engine until a corrected event replay, fee/slippage sensitivity, per-coin/core attribution, parameter-neighbour tests, and a larger untouched holdout have passed.

## Re-run

The GitHub Actions workflow `.github/workflows/core_v3_historical_sim.yml` downloads public read-only data and runs the comparison on this branch. The run and downloadable artifact are linked from the repository's Actions page.

Local equivalent:
```bash
python -m pip install ccxt pandas numpy requests
python research/fetch_historical_ohlcv.py --start-date 2025-01-01 --end-date 2026-09-30 --data data
python research/core_v3_historical_sim.py --data data --out research/results
```

Large raw OHLCV files and results should remain on the research branch/artifact, not the active `main` branch.
