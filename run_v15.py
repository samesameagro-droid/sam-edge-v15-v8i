"""Official SAM EDGE V15 launcher.

Loads the pandas datetime compatibility layer explicitly before importing the
V15 runner/core, then starts the normal 5-minute paper-forward loop.
"""

import sitecustomize  # noqa: F401

import ccxt


# BingX currently documents open-api.bingx.com as the primary production
# endpoint and open-api.bingx.pro as the network-failure fallback. Keep the
# V15 strategy untouched: this patch only makes market-data connectivity more
# resilient when the primary host is unreachable from the local/GitHub runner.
_ORIGINAL_LOAD_MARKETS = ccxt.bingx.load_markets


def _load_markets_with_fallback(self, reload=False, params={}):
    try:
        return _ORIGINAL_LOAD_MARKETS(self, reload=reload, params=params)
    except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
        if getattr(self, 'hostname', None) == 'bingx.pro':
            raise
        print(f'BINGX PRIMARY NETWORK ERROR | {type(exc).__name__} | switching to open-api.bingx.pro')
        self.hostname = 'bingx.pro'
        self.headers = dict(getattr(self, 'headers', {}) or {})
        self.headers['X-SOURCE-KEY'] = 'BX-AI-SKILL'
        return _ORIGINAL_LOAD_MARKETS(self, reload=True, params=params)


ccxt.bingx.load_markets = _load_markets_with_fallback

# Also send BingX's current source identifier on subsequent REST requests.
# This is transport/auth metadata only and does not affect V15 calculations.
_ORIGINAL_BINGX_INIT = ccxt.bingx.__init__


def _bingx_init_with_source_header(self, config={}):
    _ORIGINAL_BINGX_INIT(self, config)
    self.headers = dict(getattr(self, 'headers', {}) or {})
    self.headers['X-SOURCE-KEY'] = 'BX-AI-SKILL'


ccxt.bingx.__init__ = _bingx_init_with_source_header

import runpy

runpy.run_path("main_paper_v15.py", run_name="__main__")
