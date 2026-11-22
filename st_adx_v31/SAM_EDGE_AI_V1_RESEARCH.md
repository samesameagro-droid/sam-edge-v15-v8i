# SAM EDGE AI V1 — Research Prototype

**Status: research-only / PAPER ONLY. Not deployed. V3.3 remains unchanged.**

## Objective
Build a machine-learning signal-quality model that estimates whether a candidate setup has historically reached a positive net outcome under a fixed, explicit trade simulation. The model may reject candidates; it is not an autonomous execution system and it cannot guarantee profit.

## First prototype
- Instrument: BingX USDT linear perpetual contracts from the existing V3.3 directional allowlist.
- Resolution: 1H completed candles.
- Data window: 2024-01-01 through 2026-11-11, subject to exchange history availability.
- Candidate sources: V3.3 signals, EMA53 pullback/retest, and 20-bar breakout/breakdown.
- Features: ADX, RSI, DI spread, EMA/SuperTrend distance normalized by ATR, volatility, candle anatomy, volume ratio, multi-horizon returns, range position, EMA slope, and candidate type.
- Label: next-candle-open entry; 1.2 ATR stop; 3.5 ATR target; 48-bar maximum holding horizon; same-candle stop/target ambiguity resolved conservatively as stop first.
- Costs: 0.05% fee per side and 2 bps adverse stop slippage. Funding excluded.
- Model: Random Forest classifier with median imputation and class weighting.
- Fixed score threshold: 0.60. It is not optimized on the OOS set.
- Chronological split with a 48-hour label embargo: train before 2025-12-30; validation 2026-01-02 through 2026-06-28; OOS 2026-07-02 through 2026-11-11.

## Outputs
- candidate_dataset.csv: labeled candidate events and features.
- oos_scored_candidates.csv: OOS events with model scores.
- data_failures.csv: markets that failed data retrieval.
- summary.json: validation and OOS metrics, including all-candidate baseline vs AI-filtered results.

## Guardrails
- No exchange order endpoints, Telegram, paper-state access, or active workflow changes.
- No model promotion based on training performance.
- Require positive OOS expectancy after costs, adequate sample size, PF improvement, drawdown review, month-by-month stability, and a separate simultaneous portfolio replay before any paper-runner integration.
- Model scores are ranking scores until calibration is separately demonstrated.

## Known limitations
- Current allowlist is inherited from V3.3 and is not a point-in-time historical universe.
- Per-coin simulations are independent; summed R is not portfolio equity.
- Funding and intrabar path reconstruction are excluded.
- Labels and features are research hypotheses; inspect false positives/negatives before any promotion.
