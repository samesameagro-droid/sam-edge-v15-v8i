# SAM EDGE V15 — Master Forward Journal (Status Snapshot)

> **Authoritative data source:** `v15_forward_test_master_journal.csv` for closed trades, `v15_forward_test_ongoing.csv` for open trades, and `v15_forward_test_summary.json` / `v15_forward_test_master_state.json` for reconciled state. Do not use any older trade list below this header as current state.

## Cohort definition
- Historical baseline: trades **#1–#100** (control cohort; closed).
- Forward-test cohort: trades **#101–#200**.
- Starting equity for the forward cohort: **100 USDT**.
- Target: **100 newly closed trades** (#101–#200).
- Mode: **paper only**.
- Max active positions configured: **5**.

## Reconciled state (2026-11-01 08:17:44 UTC)
- Total closed in master CSV: **155** (#1–#155).
- Forward cohort closed: **55** (#101–#155).
- Forward cohort remaining: **45** (#156–#200).
- Open positions in state: **0**.
- Ongoing journal rows: **0**.
- Next trade number: **#156**.
- Status: **RUNNING**.

## Results

### Entire history #1–#155
| Metric | Result |
|---|---:|
| Closed trades | 155 |
| Wins / losses | 77 / 78 |
| Win rate | 49.68% |
| Net result | +18.25R |
| Profit factor | 1.234 |
| Maximum drawdown | 13.75R |

### Forward cohort #101–#155
| Metric | Result |
|---|---:|
| Closed trades | 55 |
| TP / SL | 26 / 29 |
| Win rate | 47.27% |
| Net result | +3.50R |
| Profit factor | 1.1207 |
| Expectancy | +0.0636R/trade |

### Forward cohort by side
| Side | Trades | W/L | Win rate | Net R | Profit factor |
|---|---:|---:|---:|---:|---:|
| LONG | 31 | 17 / 14 | 54.84% | +7.25R | 1.5179 |
| SHORT | 24 | 9 / 15 | 37.50% | -3.75R | 0.7500 |

### Recent weakness
- Last 20 closed trades (#136–#155): **6 wins / 14 losses**, **-6.50R**, win rate **30.00%**, PF **0.5357**.
- Last 10 closed trades: **2 wins / 8 losses**, **-5.50R**, PF **0.3125**.
- Latest loss streak: **6** (#150–#155).
- Last 20 contains 19 SHORT trades; SHORT subset is **5 wins / 14 losses**, **-7.75R**, PF **0.4464**.

These results warrant a controlled investigation of SHORT and recent regime behavior. They do **not** prove that disabling SHORT improves the full portfolio; run a shadow counterfactual with chronological slot replacement before changing execution.

## Data integrity notes
- The former version of this Markdown incorrectly reported only #101–#106 closed and #107 TAO as ongoing. That status is stale.
- The authoritative CSV contains 155 closed trades, including #107 STRK closed and #108 TAO closed. There are no open positions in the latest state.
- The master CSV has no duplicate `trade_key`, but all 155 rows lack `score`, `strategy_fingerprint`, `execution_parity`, and `failure_shield` diagnostics. Do not invent missing historical metadata; fix logging for new records.
- Do not promote historical candidate filters on the basis of in-sample counterfactual results alone.

## Audit report
See [SAM EDGE V15 Audit Priorities 1–5](SAM_EDGE_V15_AUDIT_PRIORITIES_2026-11-01.md) for full reconciliation, loss diagnosis, candidate evaluation, OOS validation protocol, and core decision.

## Source files
- [Master closed journal CSV](v15_forward_test_master_journal.csv)
- [Ongoing journal CSV](v15_forward_test_ongoing.csv)
- [Forward summary JSON](v15_forward_test_summary.json)
- [Master state JSON](v15_forward_test_master_state.json)
- [Historical loss opportunity audit](v15_loss_opportunity_audit_100.json)
- [Candidate variant report](v15_1_100trade_variant_report.json)

**Core protection:** This audit does not change the V15 production core or trading parameters. Keep `V15_PRECISION_V2_FINAL` as paper-control baseline until the locked #101–#200 cohort is complete, unless a safety/integrity bug requires stopping execution. Candidate remains shadow-only until a separate OOS test passes.
