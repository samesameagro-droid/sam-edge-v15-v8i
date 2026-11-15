# ST-ADX V3.4 — Exploratory Backtest Report

**Status: RESEARCH ONLY — NOT DEPLOYED**  
**Baseline:** ST-ADX V3.3  
**Market/data:** BingX USDT perpetual, 1H OHLCV  
**Universe:** 23 coins  
**Data window:** 2026-01-01 00:00 UTC to 2026-11-14 04:00 UTC  
**Starting equity:** 100 USDT

## 1. Data audit

The merged OOS dataset contains 7,613 hourly candles per coin across 23 coins (175,099 candles total). The supplied audit reports zero duplicate timestamps and zero missing hourly gaps for all 23 symbols. The final candle timestamp is 2026-11-14 04:00 UTC.

Important limitation: the AKE signal from 2026-11-14 15:00 UTC and LTC signal from 2026-11-15 05:00 UTC discussed in the November 15 screenshots occur after this dataset ends. This test therefore cannot directly determine whether the proposed V3.4 filters would have blocked those exact two trades.

## 2. Method

- Reconstructed the V3.3 indicator and signal conditions from `strategy_core.py`: SuperTrend 12/2.8, DMI/ADX 40/40, ADX > 30 and rising for two consecutive changes, RSI 12, EMA53, ATR10; LONG RSI 60–70; SHORT RSI 35–40 and existing SHORT EMA extension cap 3.25 ATR.
- Entry at the next 1H candle open, with 2 bps adverse entry slippage.
- Stop = 1.2 ATR10; final target = 3.5 ATR10.
- Position risk sizing targets 1% of current equity including estimated fees and adverse stop slippage; maximum aggregate open stop-risk 5%; per-position notional cap 45% of equity.
- Fee 0.05% per side; 2 bps adverse stop slippage; target exit no extra slippage; funding excluded.
- Intrabar stop/target collisions use the same nearest-to-open heuristic as the paper runner. ST flip exits are modeled at the next candle open.
- Trades are replayed chronologically across the portfolio. Risk-cap rejections are excluded from the trade count.
- Split: Jan–Aug 2026 as development/train; Sep 1–Nov 14 04:00 UTC as the later holdout period.

**Parity caveat:** this is an independent historical replay built from the checked-in indicator/signal rules and OHLCV data. It is not yet certified candle-by-candle parity with the previous V3.3 backtest engine or the live GitHub paper runner. Results should be treated as exploratory, not as a deployment-grade performance claim.

## 3. Variants tested

- **V3.3 baseline:** current entry logic unchanged.
- **V3.4A:** add a LONG EMA53 extension ceiling of 2.5 ATR10; retain the existing SHORT ceiling of 3.25 ATR10.
- **V3.4B:** V3.4A plus a pullback/retest requirement within the preceding 12 completed 1H candles. LONG: at least one candle low reaches EMA53 + 1.0 ATR10 or closer and closes at/above EMA53. SHORT: at least one candle high reaches EMA53 - 1.0 ATR10 or closer and closes at/below EMA53.
- **V3.4C:** V3.4B plus 4H SuperTrend direction alignment.

The 4H alignment condition did not change the candidate/trade set relative to V3.4B in this dataset.

## 4. Full-window portfolio results

| Variant | Closed trades | Wins | Losses | Win rate | Profit factor | Net PnL (USDT) | Max drawdown | Ending equity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V3.3 baseline | 127 | 68 | 59 | 53.54% | 3.207 | +182.45 | 6.89% | 282.45 |
| V3.4A | 66 | 36 | 30 | 54.55% | 3.026 | +71.06 | 5.00% | 171.06 |
| V3.4B | 22 | 14 | 8 | 63.64% | 3.447 | +23.88 | 4.90% | 123.88 |
| V3.4C | 22 | 14 | 8 | 63.64% | 3.447 | +23.88 | 4.90% | 123.88 |

The full-window figures include the development period and are not an independent validation set. Higher win rate/PF for V3.4B/C comes with a substantial reduction in trade count and net profit.

## 5. Later holdout results (2026-09-01 to 2026-11-14 04:00 UTC)

| Variant | Closed trades | Wins | Losses | Win rate | Profit factor | Net PnL (USDT) | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| V3.3 baseline | 13 | 4 | 9 | 30.77% | 1.082 | +0.63 | 3.99% |
| V3.4A | 6 | 1 | 5 | 16.67% | 0.705 | -1.19 | 3.00% |
| V3.4B | 2 | 0 | 2 | 0.00% | 0.000 | -1.99 | 1.99% |
| V3.4C | 2 | 0 | 2 | 0.00% | 0.000 | -1.99 | 1.99% |

Holdout trade counts are very small, especially V3.4B/C. These results are insufficient to estimate a stable edge. Nevertheless, none of the V3.4 candidates demonstrated improved holdout profitability versus V3.3 in this sample.

## 6. Decision

**Do not replace V3.3 with V3.4 based on this test. Do not deploy V3.4A/B/C to the live paper runner yet.**

- V3.4A reduced drawdown and trade count but lost money in the later holdout.
- V3.4B/C show attractive full-window metrics but only two holdout trades, both losses. This is too little evidence and raises over-filtering/selection risk.
- 4H alignment added no incremental filtering in this dataset.
- The exact AKE/LTC incidents on Nov 14–15 are outside the data window and require additional candles before they can be replayed directly.

## 7. Required next validation

1. Extend all 23 coin datasets beyond 2026-11-15 so the AKE and LTC entries are included.
2. Run candle-by-candle parity against the exact production runner, including same-bar stop/target handling, ST-flip exits, cooldown, and portfolio risk sequencing.
3. Extend the holdout window or use rolling walk-forward folds; require a materially larger sample before choosing filters.
4. Report LONG and SHORT separately, by coin and by month, and inspect rejected signals to quantify opportunity cost.
5. Preserve V3.3 as the active baseline until a candidate passes the parity and out-of-sample checks.

---
*Research-only report. No production strategy, TradingView mirror, paper runner, or live exchange configuration was changed by this experiment.*
