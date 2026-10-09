# Core V3 historical simulation (research branch)

This branch is isolated from the active `main` branch. It does not change Core V2, the live/paper executor, or the trade journal.

## Purpose
Compare the existing executable V2 permissions with candidate V3 gates on the same historical coin data before selecting a V3 design:
- `V2_EXECUTABLE`: V2 signal plus Precision V2 gate.
- `V3_SHIELD`: same entries, with the existing failure shield also required.
- `V3_ADX`: same entries, with ADX-exhaustion rejection.
- `V3_SHIELD_ADX`: both candidate protections.

These are research variants, not claims that any V3 gate is better. Results determine which design should be considered.

## Data status
The accessible repository files checked during setup include the live/paper engine and a compact journal, but no historical OHLCV ZIP/CSV dataset was found through repository code/file search. The simulation cannot honestly be reported as completed until the historical candles are available.

Expected layout:
```text
data/
  BTC/
    candles.zip
  ETH/
    candles.zip
  SOL/
    candles.csv
```

Each coin directory may contain multiple CSV/ZIP files. CSV needs `open_time` (milliseconds) or `timestamp`, plus `open,high,low,close,volume`. Files need enough 15m history for the engine's warm-up (at least 3,300 candles per coin).

## Replay assumptions
- Signal is evaluated on a completed 15m candle.
- Entry occurs at the next 15m candle open (one-bar delay).
- Stop is derived from the prior structure and signal-candle ATR, bounded by the existing 0.8–2.5 ATR limits.
- Target uses current V2 RR 1:1.25.
- If SL and TP are both touched in one candle, SL is assumed first.
- Maximum five concurrent positions and a four-bar same-coin cooldown by default.
- Unresolved positions at the end are reported separately and not counted as wins/losses.
- Results are in R; fee/slippage sensitivity and untouched holdout still need separate runs before a real-money decision.

## Run
```bash
python research/core_v3_historical_sim.py --data data --out research/results
```

Outputs are generated locally under `research/results/`; do not commit large raw data or result artifacts unless deliberately reviewed.
