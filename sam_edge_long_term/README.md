# SAM EDGE LONG TERM — Spot / Daily Engine

This folder is a **separate engine**. It does not replace, edit, or import the existing SAM EDGE V15/V2 engine. Work is isolated on branch `feature/sam-edge-long-term-spot`; the existing `main` branch is unchanged.

## Current status

- Exchange data: BingX via CCXT, **Spot markets only**
- Main timeframe: 1D; trend context: 1W
- Strategy concepts: support pullback, confirmed range breakout/retest watch
- Dynamic SL: recent structure/support plus ATR buffer
- Dynamic TP: nearest confirmed chart resistance; RR is checked **after** chart levels are selected
- Universe: active USDT spot markets filtered by 24-hour quote volume
- Output: up to 3 candidates in `data_long_term/latest_candidates.json`
- Trading mode: **PAPER ONLY**. This version does not create, modify, or cancel orders and requires no API key.

## Setup (Python 3.11+)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r sam_edge_long_term/requirements.txt
python sam_edge_long_term/engine.py
```

Optional scan limit:

```bash
python sam_edge_long_term/engine.py --limit 40
```

## Configuration

Set environment variables if needed:

- `LT_EXCHANGE=bingx`
- `LT_QUOTE=USDT`
- `LT_MIN_QUOTE_VOLUME=1000000`
- `LT_MAX_SYMBOLS=80`
- `LT_DAILY_BARS=300`
- `LT_WEEKLY_BARS=120`
- `LT_MIN_RR=2.0`
- `LT_RISK_FRACTION=0.01` (reserved for the future position-sizing module; no live sizing/order is done yet)
- `LT_ATR_BUFFER=0.35`

## How target selection works

The engine first chooses a chart-based resistance target. It then calculates the resulting risk/reward ratio. `LT_MIN_RR` is only a minimum trade-quality gate; it does **not** force TP to 1:3, 1:5, or 1:7. The engine may skip a setup if the chart target does not provide sufficient reward relative to the structural stop.

## Important limitations before live trading

This is a first scanner/analysis module, not a complete unattended trading bot. Before live use, it needs:
1. unit tests and replay/backtest with fees and slippage;
2. verification of BingX Spot symbol/volume data and supported order types;
3. persistent position state and duplicate-entry protection;
4. tested OCO/conditional order handling for stop-loss and take-profit;
5. Telegram notifications, retries, logging, and a kill switch;
6. forward testing in paper mode.

Do not put API keys in source files. Do not enable live order execution until the above checks pass. A stop order may execute worse than its trigger during fast markets or low liquidity.
