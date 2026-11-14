# ST-ADX V3.3 — Cumulative Trading Journal

**Status:** PAPER ONLY  
**Strategy:** ST-ADX V3.3  
**Timeframe:** 1H  
**Exchange/market data:** BingX USDT-linear perpetual public OHLCV  
**Starting paper equity:** 100.00 USDT  
**Journal scope:** Only signals generated after the V3.3 configuration became active. No V3.1, V3.2, or legacy SAM EDGE trades are included.

## Current snapshot

Source files checked in GitHub:
- `paper_signals.csv`
- `paper_trades.csv`
- `paper_state.json`

Latest recorded signal rows: **2** (ETH SHORT and AKE LONG).  
Closed trades: **1** (ETH TP).  
Open positions: **1** (AKE LONG).  
Persisted paper equity after the ETH close: **101.69067441 USDT**.  
Realized net PnL: **+1.69067441 USDT** (fees and simulated slippage included; funding excluded).

> Timestamp note: source timestamps are UTC. Displayed journal times are WIB (UTC+7).

## Signal and trade ledger

| No. | Coin | Side | Signal time (WIB) | Entry time (WIB) | Entry price | Stop loss | TP final | Qty | Notional (USDT) | ADX | RSI | ATR10 | Priority | Risk reserved (USDT) | Status/result | Exit time (WIB) | Exit price | Net PnL (USDT) | R |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|
| 1 | ETH | SHORT | 2026-11-14 13:00 | 2026-11-14 14:00 | 1904.129098 | 1929.297071 | 1830.722509 | 0.0236328514 | 45.00 | 33.6974 | 37.4139 | 20.9733 | 2.2500 | CLOSED — TP final | 2026-11-14 22:00 | 1830.722509 | +1.69067441 | +2.6042R |
| 2 | AKE | LONG | 2026-11-14 22:00 | 2026-11-14 23:00 | 0.013272654 | 0.010820462 | 0.020424880 | 412.303832878 | 5.47237 | 38.2746 | 63.9384 | 0.002043493 | 2.6871 | OPEN — ongoing | — | — | — | — |

## Closed-trade performance

| Metric | Value |
|---|---:|
| Closed trades | 1 |
| TP wins | 1 |
| SL losses | 0 |
| Win rate | 100.00% |
| Realized net PnL | +1.69067441 USDT |
| Realized R | +2.6042R |
| Starting equity | 100.00000000 USDT |
| Persisted equity after close | 101.69067441 USDT |
| Open positions | 1 (AKE LONG) |

**Important:** Win rate and R statistics currently represent only one closed trade. This is an early forward-test observation, not evidence of stable profitability. AKE remains open and is excluded from closed-trade statistics and realized PnL.

## Signal details and integrity

### Trade #1 — ETH SHORT — CLOSED TP
- Signal timestamp: `2026-11-14T06:00:00Z` (13:00 WIB).
- Entry timestamp: `2026-11-14T07:00:00Z` (14:00 WIB).
- Reference next-candle open: 1904.51.
- Simulated paper fill: 1904.129098.
- Stop: 1929.2970713217924.
- Final target: 1830.7225091447726.
- Exit timestamp: `2026-11-14T15:00:00Z` (22:00 WIB).
- Exit reason: `TARGET`.
- Exit price: 1830.7225091447726.
- Net PnL after recorded fees and simulated slippage: +1.6906744089493868 USDT.
- Reserved stop risk: 0.649211886456403 USDT.
- R: net PnL divided by reserved stop risk = +2.6042R.

### Trade #2 — AKE LONG — OPEN
- Signal timestamp: `2026-11-14T15:00:00Z` (22:00 WIB).
- Entry timestamp: `2026-11-14T16:00:00Z` (23:00 WIB).
- Reference next-candle open: 0.01327.
- Simulated paper fill: 0.013272654.
- Stop: 0.010820462184360165.
- Final target: 0.020424880128949517.
- Quantity: 412.3038328780461.
- Entry notional: 5.47236611666413 USDT.
- Reserved stop risk: 1.0169067440894939 USDT.
- Status in persisted state: open position.
- No exit price, close time, realized PnL, or R is recorded yet. These fields must remain blank until the position closes.

## Rules for future journal updates

1. Use GitHub's current `paper_signals.csv`, `paper_trades.csv`, and `paper_state.json` as source of truth.
2. Include only actual V3.3 signals recorded in `paper_signals.csv`; do not include watchlist-only candidates.
3. Mark a trade CLOSED only when a matching completed row exists in `paper_trades.csv`. Use the recorded exit reason and exit price.
4. Keep open positions in a separate OPEN/ONGOING status; never include them in closed-trade win rate, realized PnL, or closed-trade R.
5. Preserve all previous V3.3 records and append new records cumulatively. Deduplicate by symbol + signal timestamp + side (and reconcile against trade number).
6. Do not invent score values. The current signal schema records `priority`, not a strategy score; display priority separately and leave score as not recorded.
7. Calculate R as `net_pnl_ex_funding / risk_reserved_usdt` using the matching signal's reserved risk. Do not calculate R for an open position.
8. Convert source UTC timestamps to WIB (UTC+7) for display, while retaining original UTC timestamps in the detailed record.
9. Funding is excluded in the current paper engine; state this limitation in performance summaries.
10. Do not mix historical V3.1/V3.2 or legacy SAM EDGE records into this V3.3 cohort.

---
*This journal is a documentation snapshot generated from the persisted GitHub source files. The paper runner is simulation-only and does not submit live exchange orders.*
