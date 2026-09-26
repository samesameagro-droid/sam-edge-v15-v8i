# SAM EDGE V15 — Master Forward Journal

This file is the stable index for the post-baseline forward cohort.

## Canonical files

- [Master Journal CSV](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_master_journal.csv) — canonical closed-trade journal. Trade #1–#100 are historical baseline; Trade #101+ is the active forward cohort.
- [Master State JSON](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_master_state.json) — machine-readable cohort state.
- [Forward Summary JSON](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_summary.json) — cohort-only performance summary.
- [Forward Engine](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/run_v15_forward_test.py) — journal/state logic.
- [Professional Runner](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/run_v15_forward_test_pro.py) — scan, reconciliation and Telegram delivery.

## Cohort definition

- Baseline: trades #1–#100 (completed historical period)
- Forward cohort: trades #101–#200
- Starting equity: $100
- Risk: 1% per trade
- Core V2: unchanged
- Paper only
- WAITLIST signals do not count
- Only valid CLOSED TP/SL trades count
- Duplicate trade identities are excluded

## Current recorded cohort

- Closed: 6 / 100
- Next trade number: #107
- Active positions: 1
- Status: RUNNING

## Trade #101–#106

101 ORDI LONG — TP HIT (+1.25R)  
102 ARB LONG — TP HIT (+1.25R)  
103 RUNE LONG — TP HIT (+1.25R)  
104 TIA LONG — SL HIT (-1.00R)  
105 FIL LONG — TP HIT (+1.25R)  
106 IMX LONG — TP HIT (+1.25R)

The CSV itself remains the authoritative row-level source. The JSON files are derived state/summary and explicitly separate the baseline from the new cohort.
