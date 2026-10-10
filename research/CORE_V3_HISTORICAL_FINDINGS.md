# Core V2 / V3 Historical Replay Findings

## Dataset and scope

- Symbols: ADA, AVAX, BNB, BTC, CRV, DOGE, ETH, FET, LINK, SOL, SUI, UNI, XRP.
- Candle size: 15 minutes.
- Development data: 2025-01-01 through 2026-08-31 UTC; 58,368 candles per symbol.
- BingX's public OHLCV endpoint did not serve the requested deep history, so the replay used public Binance USD-M Futures monthly kline archives as a proxy. This is not exchange-exact for BingX and must be validated against BingX before deployment.
- Portfolio replay assumptions: enter at next candle OPEN; stop from signal-close structure/ATR; RR 1:1.25; if SL and TP are touched in one candle, SL is counted first; maximum 5 concurrent positions; 4-bar same-symbol cooldown.
- All results are gross R before fees, slippage, funding, and order-execution effects.

## Main findings

| Variant | Trades full | Full net R | Full PF | Train net R / PF | Test trades | Test net R / PF |
|---|---:|---:|---:|---:|---:|---:|
| V2 executable | 546 | +12.00R | 1.040 | +15.50R / 1.071 | 143 | -3.50R / 0.957 |
| V3 score cap <40 | 286 | +20.00R | 1.133 | +17.75R / 1.160 | 72 | +2.25R / 1.058 |
| Score cap <45 | 360 | +22.50R | 1.118 | +23.00R / 1.168 | 95 | -0.50R / 0.991 |
| Score cap <50 | 418 | +16.25R | 1.072 | +25.00R / 1.156 | 110 | -8.75R / 0.865 |
| Score cap <55 | 458 | +16.75R | 1.068 | +25.50R / 1.144 | 119 | -8.75R / 0.875 |
| ADX expansion (pct >=0.60, delta >=0.50) | 115 | +2.00R | 1.032 | -8.25R / 0.841 | 28 | +10.25R / 1.932 |

Train window: 2025-01-01 through 2026-03-31. Test window: 2026-04-01 through 2026-08-31.

## Interpretation

1. V2 is not robust in the held-out test period: net -3.50R and PF 0.957 before costs.
2. The best broadly sampled candidate so far is the score cap <40: positive train and test net R, but test PF is only 1.058 and expectancy is only +0.031R/trade before costs. That is too thin to approve for live trading.
3. The ADX expansion candidate has attractive test results but negative train results, indicating regime dependence / overfit risk. Do not select it just because test metrics are higher.
4. Failure Shield produced the same results as V2. It is structurally redundant after Precision V2 because Precision already requires EMA distance <=0.80 ATR and volume ratio >=1.20, conditions that prevent the shield's main vetoes from firing.
5. The strict ADX filter (ADX percentile >=0.90 and delta >=1.00) produced only one trade in the short smoke test and is too restrictive.

## Candidate V3 status

The file research/core_engine_v3_candidate.py implements the score-cap <40 candidate while preserving the V2 signal structure and Precision V2 gates. It is isolated under the research folder and is not imported by the active paper/live engine.

**Status: RESEARCH CANDIDATE ONLY — NOT APPROVED FOR LIVE.** The next gate is a frozen holdout replay (2026-09-05 onward) on the same source, then fees/slippage sensitivity and a BingX-specific replay. Do not tune against the holdout after inspecting its results. Do not merge into main until those checks pass.
