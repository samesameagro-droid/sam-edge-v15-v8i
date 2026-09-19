# V15 Holdout Audit — Final Research Decision

## Scope
This records the final decision after auditing the locked chronological V15_EARLY_RECLAIM_V1 holdout.

- Development: 2026-03-19 through 2026-07-16
- Holdout: 2026-07-17 through 2026-09-14
- Score hypothesis tested: >= 40
- RR: 1.25R
- Stop: STRUCTURE
- Data: Binance Futures historical archive/API
- Production changed: false

## Verified result

| Period | Trades | Wins | Losses | Win rate | Net R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Development | 149 | 72 | 77 | 48.32% | +13.00R | 1.1688 | -18.50R |
| Holdout | 69 | 28 | 41 | 40.58% | -6.00R | 0.8537 | -16.75R |

## Root-cause findings

1. **Score >= 40 is rejected as an execution threshold.** The 40-49.99 bucket was negative in both development (-6.50R) and holdout (-8.75R).
2. **The failure is not simply low ADX.** Holdout trades with ADX percentile >= 0.75 still produced -1.75R overall unless paired with stronger ADX acceleration.
3. **ADX acceleration is the clearest stable regime variable.** h4_adx_pct >= 0.75 AND h4_adx_delta >= 5 was positive in both development (+14.50R/26 trades) and holdout (+6.50R/16 trades).
4. A stricter research combination, ADX pct >= 0.75 AND ADX delta >= 5 AND score >= 60, remained positive in both samples:
   - Development: 18 trades, 12W/6L, +9.00R
   - Holdout: 14 trades, 10W/4L, +8.50R
   This is promising but too selective for immediate production promotion and remains cross-exchange evidence.
5. **Static long/short bias is rejected.** Shorts carried development edge but failed in holdout; longs did the opposite. Direction must remain regime-dependent.
6. **The holdout is not an exact live-exchange replay.** Research uses Binance Futures data; the live paper engine uses BingX. Therefore the holdout is validation evidence, not a direct BingX performance guarantee.

## Final production decision

**DO NOT change the V15 production signal engine or merge score >= 40 into execution.**

Keep main unchanged.

Keep V15 paper-forward operation as the current production baseline because the current BingX forward journal is a separate prospective sample and should not be invalidated by a cross-exchange historical experiment.

## V15.1 research candidate

The next candidate to test prospectively is a **regime-acceleration guard**, not a new score threshold:

- preserve the existing V15 structural/reclaim gates;
- use ADX percentile and ADX delta as a regime-quality filter;
- treat score as diagnostic/ranking information, not as the execution gate;
- do not impose a permanent long-only or short-only bias;
- validate on BingX prospectively before any production promotion.

Candidate research condition:
h4_adx_pct >= 0.75 AND h4_adx_delta >= 5

The score >= 60 combination is retained as a research comparator only, not a production rule.

## Promotion gate

No V15.1 rule should replace V15 until it has a locked prospective BingX sample with enough closed trades to evaluate:
- positive net R;
- PF > 1;
- acceptable drawdown;
- stable results across both LONG and SHORT;
- no dependence on a single coin;
- no post-hoc tuning on the validation sample.

This decision intentionally separates research evidence from production execution.
