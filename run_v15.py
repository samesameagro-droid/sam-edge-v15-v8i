"""Official SAM EDGE V15 launcher.

Loads the pandas datetime compatibility layer and installs a direct BingX public
REST adapter for market/ticker/OHLCV calls. The V15 strategy engine is untouched.
"""

import os
import sitecustomize  # noqa: F401
from dataclasses import asdict

import ccxt
import pandas as pd

from bingx_direct_v15 import fetch_markets, fetch_tickers, fetch_ohlcv

# CCXT's BingX transport is bypassed only for public market-data calls.
ccxt.bingx.fetch_markets = fetch_markets
ccxt.bingx.fetch_tickers = fetch_tickers
ccxt.bingx.fetch_ohlcv = fetch_ohlcv

# One-time transport check at process start.
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


def _fetch_df_full_history(self, symbol, timeframe='15m', limit=3600):
    rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df = df.drop_duplicates('timestamp').sort_values('timestamp').tail(limit).reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df


import main_paper_v15
main_paper_v15.PaperEngine.fetch_df = _fetch_df_full_history

_engine = main_paper_v15.PaperEngine()


def _retry_pending_telegram():
    from notifiers import retry_pending_messages
    n = retry_pending_messages()
    if n:
        print(f'📨 TELEGRAM PENDING QUEUE | delivered={n}')


def _retry_unsent_active_signals():
    """Retry entry alerts for active positions whose first Telegram delivery failed."""
    from notifiers import send_signal

    for coin, p in list(_engine.positions.items()):
        key = f'{p.coin}|{p.side}|{p.opened_at}|{p.core}'
        if key in _engine.signal_history:
            continue
        payload = asdict(p)
        payload['selection_score'] = 0.0
        payload['telegram_status'] = 'EXECUTED'
        sent = send_signal(payload, _engine.equity)
        if sent:
            _engine.signal_history.add(key)
            print(f'📨 TELEGRAM RETRY OK | {p.coin} | {p.side}')
        else:
            print(f'⚠️ TELEGRAM RETRY FAILED | {p.coin} | {p.side}')
    _engine.save_state()


RUN_ONCE = os.getenv('RUN_ONCE', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
if RUN_ONCE:
    print('RUN MODE | ONE-SHOT SCAN | scheduler=cron-job.org')
    _retry_pending_telegram()
    _engine.scan_once()
    _retry_pending_telegram()
    _retry_unsent_active_signals()
else:
    while _engine.running:
        try:
            _retry_pending_telegram()
            _engine.scan_once()
            _retry_pending_telegram()
            _retry_unsent_active_signals()
        except Exception as e:
            print(f'FATAL SCAN ERROR | {type(e).__name__}: {e}')
        if _engine.running:
            scan_sec = int(os.getenv('SCAN_SEC', '300'))
            print(f'SLEEP {scan_sec}s...')
            import time
            time.sleep(scan_sec)
