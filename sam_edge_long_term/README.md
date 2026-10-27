# SAM EDGE LONG TERM — Spot / Daily Engine

This is a **separate engine**. It does not replace, edit, or import the existing SAM EDGE V15/V2 engine. Work is isolated on branch `feature/sam-edge-long-term-spot`; the existing `main` branch is unchanged.

## Current implementation

- Exchange: BingX through CCXT, **Spot markets only**; no API keys and no live orders.
- Main timeframe: 1D; trend context: 1W.
- Universe: active USDT Spot pairs with 24h quote volume above the configured threshold; default scan cap 250 pairs, default volume floor 500,000 USDT.
- Setups: support pullback, range breakout/retest, and **sideways-to-GOD-CANDLE breakout watch**.
- GOD CANDLE candidate criteria: confirmed daily close above the recent 20-day range high, candle body/range and close location thresholds, volume expansion versus 20-day average, range expansion versus ATR, and at least 8 recent daily candles of compressed range.
- Anti-chase rule: if the breakout close is more than 1.25 ATR above the breakout level, label it `WAIT_RETEST`; do not treat it as a fresh market entry.
- SL: structural support / recent low plus ATR buffer. It is not tightened artificially just to improve RR.
- Targets: three chart-derived levels, with proposed partial allocations **TP1 30% / TP2 30% / TP3 40%**. Targets are based on visible prior highs / confirmed swing highs, not fixed R multiples. The third target is the furthest selected chart level; if the chart does not provide three plausible levels, the setup is skipped.
- RR is evaluated after structural SL and chart targets are selected; it is a quality gate only.
- Output: up to 10 ranked candidates in `data_long_term/latest_candidates.json`.
- Status: **PAPER ONLY**. This is a scanner prototype, not a validated trading system or order manager.

## Setup (Python 3.11+)

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r sam_edge_long_term/requirements.txt
python sam_edge_long_term/engine.py
```

Optional scan limit:

```bash
python sam_edge_long_term/engine.py --limit 100
```

## Configuration

- `LT_EXCHANGE=bingx`
- `LT_QUOTE=USDT`
- `LT_MIN_QUOTE_VOLUME=500000`
- `LT_MAX_SYMBOLS=250`
- `LT_DAILY_BARS=300`
- `LT_WEEKLY_BARS=120`
- `LT_MIN_RR=2.0`
- `LT_RISK_FRACTION=0.01` (reserved; position sizing is not implemented yet)
- `LT_ATR_BUFFER=0.35`

## Important limitations

This code has **not yet been proven profitable** and this commit is not a claim of completed validation. Before live use, the project still needs:

1. syntax/runtime checks in the target Python environment and automated unit tests;
2. a historical portfolio replay with realistic fees, spread, slippage, candle-close timing, and no look-ahead;
3. a test of GOD CANDLE signals against failed breakouts and false volume spikes;
4. walk-forward and untouched holdout tests across many coins and market regimes;
5. position sizing based on the account risk limit;
6. persistent position state, duplicate-entry protection, and exact partial-fill handling;
7. Telegram alerts, retry handling, logs, and a kill switch;
8. verified BingX Spot conditional/stop order support before any live execution;
9. paper forward testing.

A daily candle that has already expanded dramatically can be too late to chase. The engine therefore watches for a retest when price is extended. Stops can fill worse than the trigger in fast or illiquid markets. Never commit API keys to source control.
