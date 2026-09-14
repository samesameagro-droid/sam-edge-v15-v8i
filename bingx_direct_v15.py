from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = 'https://open-api.bingx.com'
TIMEOUT = 15
KLINE_PAGE_LIMIT = 500

# BingX .com has already been verified from the user's PC with HTTP 200.
# Do not use the .pro host here because that host produced local certificate
# verification failures. Instead, retry transient DNS/connect failures against
# the known-good .com endpoint.
_SESSION = requests.Session()
_RETRY = Retry(
    total=6,
    connect=6,
    read=3,
    backoff_factor=0.6,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({'GET'}),
    raise_on_status=False,
)
_SESSION.mount('https://', HTTPAdapter(max_retries=_RETRY, pool_connections=20, pool_maxsize=20))
_SESSION.headers.update({
    'X-SOURCE-KEY': 'BX-AI-SKILL',
    'User-Agent': 'SAM-EDGE-V15/1.0',
})


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    q = dict(params or {})
    last_error: Exception | None = None

    # Regenerate timestamp for every request attempt at the adapter layer.
    q.setdefault('timestamp', int(time.time() * 1000))

    for attempt in range(1, 4):
        try:
            r = _SESSION.get(BASE_URL + path, params=q, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            if payload.get('code') != 0:
                raise RuntimeError(
                    f'BingX API error {payload.get("code")}: {payload.get("msg")}'
                )
            return payload.get('data')
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 3:
                # New timestamp plus a short delay helps with transient network/DNS
                # failures while keeping the V15 scan cadence unchanged.
                q['timestamp'] = int(time.time() * 1000)
                time.sleep(0.8 * attempt)

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
        'future': False, 'option': False, 'index': False,
        'active': bool(int(c.get('status', 0)) == 1),
        'contract': True, 'linear': True, 'inverse': False, 'subType': 'linear',
        'taker': float(c.get('takerFeeRate') or c.get('feeRate') or 0),
        'maker': float(c.get('makerFeeRate') or c.get('feeRate') or 0),
        'contractSize': float(c.get('size') or 1), 'expiry': None, 'expiryDatetime': None,
        'strike': None, 'precision': {'amount': 10 ** (-qp), 'price': 10 ** (-pp)},
        'limits': {
            'leverage': {'min': None, 'max': None},
            'amount': {'min': None, 'max': None},
            'price': {'min': None, 'max': None},
            'cost': {'min': None, 'max': None},
        },
        'marginModes': {'cross': None, 'isolated': None},
        'created': c.get('launchTime'), 'info': c, 'feeSide': 'get',
    }


def _is_crypto_contract(c: dict[str, Any]) -> bool:
    asset = str(c.get('asset') or c.get('symbol', '').split('-')[0]).upper()
    sid = str(c.get('symbol') or '').upper()
    info = c if isinstance(c, dict) else {}
    text = ' '.join([
        asset,
        sid,
        str(info.get('displayName') or ''),
        str(info.get('category') or ''),
        str(info.get('productType') or ''),
        str(info.get('assetClass') or ''),
    ]).upper()
    for token in (
        'GOLD', 'XAU', 'SILVER', 'XAG', 'OIL', 'WTI', 'BRENT',
        'FOREX', 'COMMODITY', 'INDEX', 'SPX', 'SP500', 'NAS100',
        'NASDAQ', 'DOW30', 'US30', 'USTEC', 'GER40',
    ):
        if token in text:
            return False
    for suffix in ('2USD', 'USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF'):
        if asset.endswith(suffix):
            return False
    classes = ' '.join(
        str(info.get(k) or '').lower()
        for k in ('category', 'productType', 'assetClass')
    )
    if any(x in classes for x in ('commodity', 'forex', 'index', 'metal')):
        return False
    return True


def fetch_markets(self, params={}):
    data = _get('/openApi/swap/v2/quote/contracts', params)
    return [market_from_contract(c) for c in (data or []) if _is_crypto_contract(c)]


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
    requested = int(limit) if limit is not None else KLINE_PAGE_LIMIT
    requested = max(1, requested)
    market_symbol = symbol.split(':')[0].replace('/', '-').upper()
    base_params = dict(params or {})
    cursor_end = base_params.get('endTime')
    if cursor_end is not None:
        cursor_end = int(cursor_end)

    out: list[list[Any]] = []
    remaining = requested
    seen: set[int] = set()

    while remaining > 0:
        page_limit = min(KLINE_PAGE_LIMIT, remaining)
        q: dict[str, Any] = {
            'symbol': market_symbol,
            'interval': timeframe,
            'limit': page_limit,
        }
        if since is not None and cursor_end is None:
            q['startTime'] = int(since)
        if cursor_end is not None:
            q['endTime'] = cursor_end

        data = _get('/openApi/swap/v3/quote/klines', q)
        page: list[list[Any]] = []
        for row in (data or []):
            if isinstance(row, (list, tuple)) and len(row) >= 6:
                ts = int(row[0])
                if ts in seen:
                    continue
                seen.add(ts)
                page.append([
                    ts,
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                ])
        page.sort(key=lambda r: r[0])
        if not page:
            break

        out.extend(page)
        remaining = requested - len(out)
        oldest = int(page[0][0])
        next_end = oldest - 1
        if cursor_end is not None and next_end >= cursor_end:
            break
        cursor_end = next_end

        if len(page) < page_limit:
            break

    out.sort(key=lambda r: r[0])
    if len(out) > requested:
        out = out[-requested:]
    return out
