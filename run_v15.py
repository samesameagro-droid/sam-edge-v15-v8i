"""Official SAM EDGE V15 launcher.

Loads the pandas datetime compatibility layer and installs a direct BingX public
REST adapter for market/ticker/OHLCV calls. The V15 strategy engine is untouched.
"""

import sitecustomize  # noqa: F401

import ccxt
import pandas as pd

from bingx_direct_v15 import fetch_markets, fetch_tickers, fetch_ohlcv

# CCXT's BingX transport is bypassed only for public market-data calls.
# The returned structures follow CCXT's unified shapes, so main_paper_v15.py
# and core_engine_v15.py continue to operate unchanged.
ccxt.bingx.fetch_markets = fetch_markets
ccxt.bingx.fetch_tickers = fetch_tickers
ccxt.bingx.fetch_ohlcv = fetch_ohlcv


# One-time transport check at process start. This does not alter V15 rules or
# the 5-minute scan cadence; it simply proves how many 15m candles BingX returns.
try:
    _probe = fetch_ohlcv(None, 'BTC/USDT:USDT', timeframe='15m', limit=3600)
    if _probe:
        _first = pd.to_datetime(_probe[0][0], unit='ms', utc=True).isoformat()
        _last = pd.to_datetime(_probe[-1][0], unit='ms', utc=True).isoformat()
    else:
        _first = _last = 'NONE'
    print(f'HISTORY TEST | BTC/USDT:USDT | rows={len(_probe)} | first={_first} | last={_last}')
except Exception as _e:
    print(f'HISTORY TEST ERROR | {type(_e).__name__}: {_e}')


# Let the direct adapter own the complete historical pagination. The old runner
# paginated again outside the adapter, which could duplicate/overlap pages and
# leave every symbol below V15's 3300-row minimum. This changes transport only.
def _fetch_df_full_history(self, symbol, timeframe='15m', limit=3600):
    rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not rows:
        return None
    df = pd.DataFrame(
        rows,
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
    )
    df = (
        df.drop_duplicates('timestamp')
          .sort_values('timestamp')
          .tail(limit)
          .reset_index(drop=True)
    )
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df


# main_paper_v15.py keeps the original 3300-row V15 gate; only its data-fetch
# plumbing is redirected to the adapter-managed history above.
import main_paper_v15
main_paper_v15.PaperEngine.fetch_df = _fetch_df_full_history

import runpy

runpy.run_path("main_paper_v15.py", run_name="__main__")
