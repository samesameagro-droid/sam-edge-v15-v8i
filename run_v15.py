"""Official SAM EDGE V15 launcher.

Loads the pandas datetime compatibility layer and installs a direct BingX public
REST adapter for market/ticker/OHLCV calls. The V15 strategy engine is untouched.
"""

import sitecustomize  # noqa: F401

import ccxt

from bingx_direct_v15 import fetch_markets, fetch_tickers, fetch_ohlcv

# CCXT's BingX transport is bypassed only for public market-data calls.
# The returned structures follow CCXT's unified shapes, so main_paper_v15.py
# and core_engine_v15.py continue to operate unchanged.
ccxt.bingx.fetch_markets = fetch_markets
ccxt.bingx.fetch_tickers = fetch_tickers
ccxt.bingx.fetch_ohlcv = fetch_ohlcv

import runpy

runpy.run_path("main_paper_v15.py", run_name="__main__")
