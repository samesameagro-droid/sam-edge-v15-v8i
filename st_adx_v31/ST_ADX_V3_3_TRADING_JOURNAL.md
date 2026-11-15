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

Latest recorded signal rows: **3** (ETH SHORT, AKE LONG, LTC SHORT).  
Closed trades: **2** (ETH TP final; AKE SL).  
Open positions: **1** (LTC SHORT).  
Persisted paper equity: **100.67376766 USDT**.  
Realized net PnL: **+0.67376766 USDT** (fees and simulated slippage included; funding excluded).

> Timestamp note: source timestamps are UTC. Displayed journal times are WIB (UTC+7). Completed rows in `paper_trades.csv` take precedence over stale `PAPER_OPEN` labels in the signal file.

## Signal and trade ledger

| No. | Coin | Side | Signal time (WIB) | Entry time (WIB) | Entry price | Stop loss | TP final | Qty | Notional (USDT) | ADX | RSI | ATR10 | Score | Priority | Risk reserved (USDT) | Status/result | Exit time (WIB) | Exit price | Net PnL (USDT) | R |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|
| 1 | ETH | SHORT | 2026-11-14 13:00 | 2026-11-14 14:00 | 1904.129098 | 1929.297071 | 1830.722509 | 0.0236328514 | 45.00 | 33.6974 | 37.4139 | 20.9733 | Not recorded | 2.2500 | 0.64921189 | CLOSED — TP final | 2026-11-14 22:00 | 1830.722509 | +1.69067441 | +2.6042R |
| 2 | AKE | LONG | 2026-11-14 22:00 | 2026-11-14 23:00 | 0.013272654 | 0.010820462 | 0.020424880 | 412.303832878 | 5.47237 | 38.2746 | 63.9384 | 0.002043493 | Not recorded | 2.6871 | 1.01690674 | CLOSED — SL | 2026-11-15 10:00 | 0.010818298 | -1.01690674 | -1.0000R |
| 3 | LTC | SHORT | 2026-11-15 12:00 | 2026-11-15 13:00 | 44.541090 | 45.530818 | 41.654382 | 0.9644234756 | 42.95647 | 30.8843 | 39.4999 | 0.824773712 | Not recorded | 2.0849 | 1.00673768 | OPEN — ongoing | — | — | — | — |

## Closed-trade performance

| Metric | Value |
|---|---:|
| Closed trades | 2 |
| TP wins | 1 |
| SL losses | 1 |
| Win rate | 50.00% |
| Realized net PnL | +0.67376766 USDT |
| Realized R (sum) | +1.6042R |
| Starting equity | 100.00000000 USDT |
| Persisted paper equity | 100.67376766 USDT |
| Open positions | 1 (LTC SHORT) |

**Interpretation:** The current forward sample contains only two closed trades. The 50% win rate and positive cumulative R are descriptive of this small sample, not evidence of stable profitability. LTC remains open and is excluded from closed-trade statistics and realized PnL.

## Signal details and integrity

### Trade #1 — ETH SHORT — CLOSED TP final
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

### Trade #2 — AKE LONG — CLOSED SL
- Signal timestamp: `2026-11-14T15:00:00Z` (22:00 WIB).
- Entry timestamp: `2026-11-14T16:00:00Z` (23:00 WIB).
- Reference next-candle open: 0.01327.
- Simulated paper fill: 0.013272654.
- Stop: 0.010820462184360165.
- Final target: 0.020424880128949517.
- Exit timestamp: `2026-11-15T03:00:00Z` (10:00 WIB).
- Exit reason: `STOP`.
- Exit price: 0.010818298091923294.
- Net PnL after recorded fees and simulated slippage: -1.0169067440894939 USDT.
- Reserved stop risk: 1.0169067440894939 USDT.
- R: -1.0000R.

### Trade #3 — LTC SHORT — OPEN
- Signal timestamp: `2026-11-15T05:00:00Z` (12:00 WIB).
- Entry timestamp: `2026-11-15T06:00:00Z` (13:00 WIB).
- Reference next-candle open: 44.55.
- Simulated paper fill: 44.54109.
- Stop: 45.53081845455161.
- Final target: 41.65438200755781.
- Quantity: 0.9644234755728891.
- Entry notional: 42.956472823604855 USDT.
- ADX: 30.884323721415903; RSI: 39.49987719371729; ATR10: 0.8247737121263391.
- Priority: 2.0849.
- Reserved stop risk: 1.006737676648599 USDT.
- Status in persisted state: open position.
- No exit price, close time, realized PnL, or R is recorded yet. These fields must remain blank until the position closes.

## Rules for future journal updates

1. Use GitHub's current `paper_signals.csv`, `paper_trades.csv`, and `paper_state.json` as source of truth.
2. Include only actual V3.3 signals recorded in `paper_signals.csv`; do not include watchlist-only candidates.
3. Mark a trade CLOSED only when a matching completed row exists in `paper_trades.csv`. Use the recorded exit reason and exit price; completed trades override stale signal status labels.
4. Keep open positions in a separate OPEN/ONGOING status; never include them in closed-trade win rate, realized PnL, or closed-trade R.
5. Preserve all previous V3.3 records and append new records cumulatively. Deduplicate by symbol + signal timestamp + side (and reconcile against trade number).
6. Do not invent score values. The current signal schema records `priority`, not a strategy score; display priority separately and leave score as not recorded.
7. Calculate R as `net_pnl_ex_funding / risk_reserved_usdt` using the matching signal's reserved risk. Do not calculate R for an open position.
8. Convert source UTC timestamps to WIB (UTC+7) for display, while retaining original UTC timestamps in the detailed record.
9. Funding is excluded in the current paper engine; state this limitation in performance summaries.
10. Do not mix historical V3.1/V3.2 or legacy SAM EDGE records into this V3.3 cohort.

---
*This journal is a documentation snapshot generated from the persisted GitHub source files. The paper runner is simulation-only and does not submit live exchange orders.*
