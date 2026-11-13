# ST-ADX V3.1 — Cumulative Trading Journal

**Scope:** ST-ADX V3.1 paper forward only. SAM EDGE historical journal is excluded and remains archived separately.  
**Starting paper equity:** 100,000 USDT.  
**Latest persisted snapshot checked:** 2026-11-13 22:01 UTC (2026-11-14 05:01 WIB).  
**Primary source files:** `paper_trades.csv`, `paper_signals.csv`, `paper_state.json`.

## Closed trades

R is calculated as `net_pnl_ex_funding / risk_reserved_usdt` from the corresponding entry record in `paper_signals.csv`. Profit is net of recorded entry/exit fees and simulated slippage, excluding funding. Score was not recorded by the ST-ADX CSV schema; therefore it is shown as `—`, not inferred from priority.

| Trade | Koin | Side | Entry (WIB) | SL | TP final | Score | Hasil | R | Close (WIB) | Profit (USDT) |
|---:|---|---|---|---:|---:|---:|---|---:|---|---:|
| 1 | ETH | SHORT | 2026-11-12 20:00 | 2039.1143 | 1969.5596 | — | TP | +2.47R | 2026-11-13 10:00 | +1,108.68 |
| 2 | ETH | SHORT | 2026-11-13 12:00 | 1952.2196 | 1841.7521 | — | SL | -1.00R | 2026-11-13 17:00 | -722.05 |
| 3 | ETH | SHORT | 2026-11-14 00:00 | 1954.3271 | 1845.7863 | — | SL | -1.00R | 2026-11-14 02:00 | -704.45 |

## Current open position — not counted as a closed trade

| Koin | Side | Entry (WIB) | SL | TP final | Score | Status | Floating PnL |
|---|---|---:|---:|---:|---|---|---|
| BTC | SHORT | 2026-11-14 00:00 | 69477.6592 | 66251.1379 | — | OPEN | Not calculated in persisted state |

Actual paper entry: 68,653.86648 USDT (raw next-candle open: 68,667.60). The position is not assigned a closed-trade number until it closes. No TP1/TP2 partial exits are recorded or intended.

## Cumulative performance — closed trades only

| Metric | Value |
|---|---:|
| Closed trades | 3 |
| TP wins | 1 |
| SL losses | 2 |
| Win rate | 33.33% |
| Gross/net realized result (fees included) | -317.83 USDT |
| Cumulative R | +0.47R |
| Starting equity | 100,000.00 USDT |
| Persisted equity | 99,682.17 USDT |
| Return on starting equity | -0.32% |
| Open positions | 1 (BTC SHORT) |

**Interpretation:** the cumulative R is positive because the first TP winner returned about +2.47 times its reserved stop-risk, while the two losses were -1R each. The cash result remains negative by 317.83 USDT. These are only three closed paper trades and are not enough to establish strategy performance.

## Data integrity notes
- Trade rows above are copied from the three valid CLOSED rows in `paper_trades.csv`; no signal-only or watchlist row is counted as a completed trade.
- The current BTC SHORT is separated as OPEN and excluded from win rate, realized profit, and closed-trade count.
- `paper_signals.csv` has no score field. Its `priority` field is not a score and has not been substituted.
- Times are converted from source UTC timestamps to WIB (UTC+7).
- Funding is excluded, consistent with the paper engine configuration.
- SAM EDGE V15 trades, baseline journal, and historical backtests are not mixed into this ST-ADX journal.
