# V15 4H Breakout Audit (Research Only)

This branch is intentionally isolated from `main`. It does not modify the live V15 paper engine, workflow, journal, or production state.

## Purpose
Test the user's hypothesis while keeping 15m execution unchanged:

1. **Baseline**: all closed V15 trades in the authoritative journal.
2. **Strict 4H breakout**: the latest completed 4H candle closes beyond the prior 5 or 10 completed 4H structural high/low, and the V15 signal occurs in the immediately following 4H candle.
3. **4H breakout + pullback**: the breakout candle is followed by a completed 4H retest of the breakout level; the V15 15m signal occurs in the next 4H candle.

All 4H classification is based only on candles that were completed before the 15m signal, avoiding look-ahead leakage.

The script reads the current production journal, fetches historical BingX 4H OHLCV, labels every closed trade, and writes a comparison summary.

BingX documents `/openApi/swap/v3/quote/klines` with `4h`, `startTime`, `endTime`, and `limit` support.

## Running
GitHub Actions -> **V15 research - 4H breakout audit** -> Run workflow.

The audit automatically uses the newest journal, so when trade #100 closes it can be rerun without code changes.

## Promotion rule
Research results never change V15 automatically. Any future 4H gate must be reviewed separately and explicitly approved before it can touch `main`.
