# SAM EDGE V15 — 20-Trade Forward Test

## Run

Use the professional runner instead of `main_paper_v15.py` for the 20-trade forward test:

```bash
python run_v15_forward_test_pro.py
```

## Rules

- Universe scanning remains the V15 BingX USDT perpetual scanner.
- One selected forward-test position is active at a time.
- Trade numbers are global across coins: #1, #2, #3 ... #20.
- A WAITLIST signal does not count as a trade.
- A selected position becomes a completed journal trade only after TP or SL.
- Existing `paper_v15_trades.csv` closed trades are migrated on first start, so an already-closed AIN SL is preserved as the first master trade when that runtime file contains it.
- The master journal is `v15_forward_test_master_journal.csv`.
- Summary is `v15_forward_test_summary.json`.
- The runner stops after 20 closed forward-test trades.

## Telegram

`run_v15_forward_test_pro.py` upgrades entry notifications to a professional Telegram photo message. The chart is generated from live BingX 15-minute OHLCV and includes EMA20/EMA50 plus Entry, SL and TP levels.

If a signal was already sent by the old notifier before switching runners, resend the current active trade once with:

```bash
python resend_active_v15_professional.py
```

This is a presentation/notification layer; it does not alter the V15 signal engine, entry, SL, TP, or scoring logic.
