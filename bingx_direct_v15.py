from __future__ import annotations

import time
from typing import Any

import requests

BASE_URLS = ('https://open-api.bingx.com', 'https://open-api.bingx.pro')
TIMEOUT = 15
RETRIES_PER_HOST = 2


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    q = dict(params or {})
    q.setdefault('timestamp', int(time.time() * 1000))
    last_error: Exception | None = None
    for base in BASE_URLS:
        for attempt in range(1, RETRIES_PER_HOST + 1):
            try:
                r = requests.get(
                    base + path,
                    params=q,
                    timeout=TIMEOUT,
                    headers={'X-SOURCE-KEY': 'BX-AI-SKILL'},
                )
                r.raise_for_status()
                payload = r.json()
                if payload.get('code') != 0:
                    raise RuntimeError(
                        f'BingX API error {payload.get("code")}: {payload.get("msg")}'
                    )
                return payload.get('data')
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < RETRIES_PER_HOST:
                    time.sleep(0.7)
    raise last_error if last_error is not None else RuntimeError('BingX request failed')


def market_from_contract(c: dict[str, Any]) -> dict[str, Any]:
    asset = str(c.get('asset') or c.get('symbol', '').split('-')[0]).upper()
    sid = str(c.get('symbol') or f'{asset}-USDT').upper()
    qp = int(c.get('quantityPrecision') or 0)
    pp = int(c.get('pricePrecision') or 0)
    return {
        'id': sid, 'lowercaseId': None, 'symbol': f'{asset}/USDT:USDT',
        'base': asset, 'quote': 'USDT', 'settle': 'USDT',
        'baseId': asset, 'quoteId': 'USDT', 'settleId': 'USDT',
        'type': 'swap', 'spot': False, 'margin': False, 'swap': True,
        'future': False, 'option': False, 'index': False, 'active': bool(int(c.get('status', 0)) == 1),
        'contract': True, 'linear': True, 'inverse': False, 'subType': 'linear',
        'taker': float(c.get('takerFeeRate') or c.get('feeRate') or 0),
        'maker': float(c.get('makerFeeRate') or c.get('feeRate') or 0),
        'contractSize': float(c.get('size') or 1), 'expiry': None, 'expiryDatetime': None,
        'strike': None, 'precision': {'amount': 10 ** (-qp), 'price': 10 ** (-pp)},
        'limits': {'leverage': {'min': None, 'max': None}, 'amount': {'min': None, 'max': None}, 'price': {'min': None, 'max': None}, 'cost': {'min': None, 'max': None}},
        'marginModes': {'cross': None, 'isolated': None}, 'created': c.get('launchTime'),
        'info': c, 'feeSide': 'get',
    }


def fetch_markets(self, params={}):
    data = _get('/openApi/swap/v2/quote/contracts', params)
    return [market_from_contract(c) for c in (data or [])]


def _unified_symbol(sid: str) -> str:
    sid = sid.upper()
    if '/' in sid:
        return sid
    base, quote = sid.split('-', 1)
    return f'{base}/{quote}:USDT'


def fetch_tickers(self, symbols=None, params={}):
    data = _get('/openApi/swap/v2/quote/ticker', params)
    rows = data if isinstance(data, list) else [data]
    out = {}
    wanted = set(symbols or [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get('symbol') or '').upper()
        if not sid:
            continue
        sym = _unified_symbol(sid)
        if wanted and sym not in wanted:
            continue
        out[sym] = {
            'symbol': sym, 'info': row,
            'timestamp': int(row.get('closeTime') or row.get('openTime') or int(time.time() * 1000)),
            'datetime': None,
            'high': float(row['highPrice']) if row.get('highPrice') is not None else None,
            'low': float(row['lowPrice']) if row.get('lowPrice') is not None else None,
            'bid': float(row['bidPrice']) if row.get('bidPrice') is not None else None,
            'ask': float(row['askPrice']) if row.get('askPrice') is not None else None,
            'last': float(row['lastPrice']) if row.get('lastPrice') is not None else None,
            'close': float(row['lastPrice']) if row.get('lastPrice') is not None else None,
            'previousClose': float(row['openPrice']) if row.get('openPrice') is not None else None,
            'change': float(row['priceChange']) if row.get('priceChange') is not None else None,
            'percentage': float(row['priceChangePercent']) if row.get('priceChangePercent') is not None else None,
            'average': None,
            'baseVolume': float(row['volume']) if row.get('volume') is not None else None,
            'quoteVolume': float(row['quoteVolume']) if row.get('quoteVolume') is not None else None,
        }
    return out


def fetch_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params={}):
    market_symbol = symbol.split(':')[0].replace('/', '-').upper()
    q = dict(params or {})
    q.update({'symbol': market_symbol, 'interval': timeframe})
    if since is not None:
        q['startTime'] = int(since)
    if limit is not None:
        q['limit'] = min(int(limit), 1440)
    data = _get('/openApi/swap/v3/quote/klines', q)
    out = []
    for row in (data or []):
        if isinstance(row, (list, tuple)) and len(row) >= 6:
            out.append([int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
    out.sort(key=lambda r: r[0])
    if limit is not None:
        out = out[-int(limit):]
    return out
