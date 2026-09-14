from __future__ import annotations

import os

import main_paper_v15 as v15

# V15 keeps its original strategy/core rules. This runner changes only the
# execution/signal timeframe to 5m and provides enough history for the
# existing 4H ADX percentile regime (200 x 4H candles ≈ 33 days).
v15.TIMEFRAME = os.getenv("TIMEFRAME", "5m")
v15.HISTORY_15M = int(os.getenv("HISTORY_5M", "10000"))
v15.FETCH_CHUNK = int(os.getenv("FETCH_CHUNK", "1000"))

# main_paper_v15.fetch_df() has defaults bound at import time. Override the
# method so the 5m runner actually uses the 5m history above for signal scans.
_original_fetch_df = v15.PaperEngine.fetch_df


def _fetch_df(self, symbol, timeframe=None, limit=None):
    if timeframe is None:
        timeframe = v15.TIMEFRAME
    if limit is None:
        limit = v15.HISTORY_15M
    return _original_fetch_df(self, symbol, timeframe, limit)


v15.PaperEngine.fetch_df = _fetch_df


if __name__ == "__main__":
    v15.PaperEngine().scan_once()
