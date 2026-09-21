# V15.1 BTC-AWARE SHADOW TEST

This research branch runs the unchanged V15 execution engine and records a BTC-aware hard-filter hypothesis in parallel.

Locked hypothesis:
- LONG: BTC completed 4H return >= -0.25% => PASS
- SHORT: BTC completed 4H return <= +0.25% => PASS
- otherwise FILTER

The BTC decision is never allowed to block, alter, rank, size, or modify a real V15 paper position in this phase.

Validation target: 50 NEW closed trades after this branch starts.

The BTC features are point-in-time: only completed BTC 1H/4H candles whose close is available at the V15 signal timestamp are used.