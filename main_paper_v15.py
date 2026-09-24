from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from core_engine_v15 import (
    CORE_NAME, RR, enrich, signal_mask, failure_shield_mask, failure_shield_snapshot, trade_levels,
    ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
    EARLY_MOVE5_MAX_ATR, EARLY_DIST_EMA_MAX_ATR, EARLY_ROOM_MIN_ATR,
    EARLY_PULLBACK_LOOKBACK, EARLY_PULLBACK_MAX_ATR, EARLY_RECLAIM_BUFFER_ATR,
    EARLY_VOLUME_RATIO_MIN, EARLY_VOLUME_SLOPE_MIN,
    STRUCTURE_STOP_LOOKBACK, STRUCTURE_STOP_ATR_BUFFER, STRUCTURE_STOP_MIN_ATR, STRUCTURE_STOP_MAX_ATR,
)

load_dotenv()

BUILD = 'V15-EARLY-RECLAIM-V1'
TIMEFRAME = '15m'
TRACK_TIMEFRAME = '5m'
START_EQUITY = float(os.getenv('START_EQUITY', '100'))
RISK_PCT = float(os.getenv('RISK_PCT', '0.005'))
SCAN_SEC = int(os.getenv('SCAN_SEC', '300'))
HISTORY_15M = int(os.getenv('HISTORY_15M', '3600'))
FETCH_CHUNK = int(os.getenv('FETCH_CHUNK', '900'))
REQUEST_TIMEOUT_MS = int(os.getenv('REQUEST_TIMEOUT_MS', '15000'))
MIN_VOLUME_USDT = float(os.getenv('MIN_VOLUME_USDT', '3000000'))
UNIVERSE_LIMIT = int(os.getenv('UNIVERSE_LIMIT', '100'))
MAX_ACTIVE = int(os.getenv('MAX_ACTIVE_POSITIONS', '5'))
BTC_FILTER_ENABLED = os.getenv('BTC_FILTER_ENABLED', '1').strip().lower() in {'1', 'true', 'yes', 'on'}
# BTC is shadow/diagnostic only during the forward test; it never changes V15 execution.
BTC_FILTER_MODE = os.getenv('BTC_FILTER_MODE', 'shadow').strip().lower()
BTC_FILTER_FAIL_CLOSED = os.getenv('BTC_FILTER_FAIL_CLOSED', '1').strip().lower() in {'1', 'true', 'yes', 'on'}

# Post-100-trade defensive optimization layer. Isolated from signal_mask().
V15_DEFENSIVE_MODE = os.getenv('V15_DEFENSIVE_MODE', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
V15_ENTRY_SCORE_MAX = float(os.getenv('V15_ENTRY_SCORE_MAX', '60'))
V15_DIST_EMA_MAX_ATR = float(os.getenv('V15_DIST_EMA_MAX_ATR', '0.80'))
V15_MIN_VOLUME_RATIO = float(os.getenv('V15_MIN_VOLUME_RATIO', '1.20'))
V15_LOSS_STREAK_PAUSE = int(os.getenv('V15_LOSS_STREAK_PAUSE', '3'))
V15_LOSS_PAUSE_MIN = int(os.getenv('V15_LOSS_PAUSE_MIN', '60'))
V15_MAX_ACTIVE_PER_SIDE = int(os.getenv('V15_MAX_ACTIVE_PER_SIDE', '3'))
V15_SELECTION_MODE = os.getenv('V15_SELECTION_MODE', 'HIGH_SCORE').strip().upper()

# Failure-mode shield is a separate execution permission layer. It is OFF by default.
V15_FAILURE_SHIELD_ENABLED = os.getenv('V15_FAILURE_SHIELD_ENABLED', '0').strip().lower() in {'1','true','yes','on'}

# Immutable fingerprint of the executable V15 gate/level configuration.
# It is persisted with every new paper trade so audit records can be tied to
# the exact rule set that generated them, preventing silent strategy drift.
V15_STRATEGY_FINGERPRINT = hashlib.sha256('|'.join(map(str, [
    CORE_NAME, RR, ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
    EARLY_MOVE5_MAX_ATR, EARLY_DIST_EMA_MAX_ATR, EARLY_ROOM_MIN_ATR,
    EARLY_PULLBACK_LOOKBACK, EARLY_PULLBACK_MAX_ATR, EARLY_RECLAIM_BUFFER_ATR,
    EARLY_VOLUME_RATIO_MIN, EARLY_VOLUME_SLOPE_MIN,
    STRUCTURE_STOP_LOOKBACK, STRUCTURE_STOP_ATR_BUFFER,
    STRUCTURE_STOP_MIN_ATR, STRUCTURE_STOP_MAX_ATR,
    V15_DEFENSIVE_MODE, V15_ENTRY_SCORE_MAX, V15_DIST_EMA_MAX_ATR,
    V15_MIN_VOLUME_RATIO, V15_LOSS_STREAK_PAUSE, V15_LOSS_PAUSE_MIN,
    V15_MAX_ACTIVE_PER_SIDE, V15_SELECTION_MODE, V15_FAILURE_SHIELD_ENABLED,
])).encode()).hexdigest()[:16]

STATE_FILE = Path('paper_v15_state.json')
STATE_BACKUP_FILE = Path('paper_v15_state.backup.json')
JOURNAL_FILE = Path('paper_v15_trades.csv')
SIGNAL_HISTORY = Path('paper_v15_signal_history.json')
UNIVERSE_FILE = Path('paper_v15_universe.json')
SHADOW_E_FILE = Path('v15_1_shadow_e_signals.csv')


@dataclass
class Position:
    coin: str
    side: str
    entry: float
    sl: float
    tp: float
    risk_R: float
    risk_cash: float
    opened_at: str
    core: str = CORE_NAME
    status: str = 'OPEN'
    # Telegram/post-mortem snapshot. Defaults keep old V15 state files compatible.
    entry_score: float = 0.0
    entry_diag: dict = field(default_factory=dict)
    entry_metrics: dict = field(default_factory=dict)
    score_breakdown: dict = field(default_factory=dict)
    signal_key: str = ''
    # Persist BTC context for post-hoc analysis only.
    btc_context: dict = field(default_factory=dict)
    strategy_fingerprint: str = V15_STRATEGY_FINGERPRINT


class PaperEngine:
    def __init__(self):
        self.exchange = ccxt.bingx({
            'enableRateLimit': True,
            'timeout': REQUEST_TIMEOUT_MS,
            'options': {'defaultType': 'swap'},
        })
        self.equity = START_EQUITY
        self.closed = []
        self.positions = {}
        self.signal_history = set()
        self.markets = {}
        self.universe_symbols = []
        self.running = True
        self._btc_filter_context = None
        self._btc_filter_timestamp = None
        self.load_state()
        self.load_signal_history()

    def _restore_state_payload(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('state root must be an object')
        equity = float(payload.get('equity', START_EQUITY))
        closed = payload.get('closed', [])
        raw_positions = payload.get('positions', {})
        if not isinstance(closed, list) or not isinstance(raw_positions, dict):
            raise ValueError('state closed/positions schema invalid')
        positions = {}
        allowed = set(Position.__dataclass_fields__)
        for key, value in raw_positions.items():
            if not isinstance(value, dict):
                raise ValueError(f'position {key} is not an object')
            clean = {k: v for k, v in value.items() if k in allowed}
            positions[str(key)] = Position(**clean)
        self.equity = equity
        self.closed = closed
        self.positions = positions

    def _reconcile_closed_journal(self):
        # Recover a close journaled before a runner died before save_state().
        if not JOURNAL_FILE.exists():
            return
        try:
            with JOURNAL_FILE.open('r', encoding='utf-8', newline='') as f:
                rows = list(pd.read_csv(f, dtype=str).fillna('').to_dict('records'))
        except Exception as e:
            print(f'JOURNAL RECOVERY SKIP | {type(e).__name__}: {e}')
            return
        known = {f"{r.get('coin','')}|{r.get('side','')}|{r.get('opened_at','')}|{r.get('core','')}" for r in self.closed if str(r.get('result','')).upper() in {'TP','SL'}}
        recovered = 0
        for r in rows:
            result = str(r.get('result','')).upper().strip()
            if result not in {'TP','SL'}:
                continue
            key = f"{r.get('coin','')}|{r.get('side','')}|{r.get('opened_at','')}|{r.get('core','')}"
            if not r.get('opened_at') or not r.get('closed_at') or key in known:
                continue
            try:
                opened = datetime.fromisoformat(str(r['opened_at']).replace('Z', '+00:00'))
                closed = datetime.fromisoformat(str(r['closed_at']).replace('Z', '+00:00'))
                if closed < opened:
                    continue
                self.closed.append(r)
                known.add(key)
                recovered += 1
            except (TypeError, ValueError):
                continue
        if recovered:
            stale = [k for k, p in self.positions.items() if f"{p.coin}|{p.side}|{p.opened_at}|{p.core}" in known]
            for k in stale:
                self.positions.pop(k, None)
            try:
                latest = max((r for r in self.closed if r.get('equity_after') not in ('', None)), key=lambda r: str(r.get('closed_at','')))
                self.equity = float(latest['equity_after'])
            except (ValueError, TypeError, KeyError):
                pass
            print(f'JOURNAL RECOVERY | recovered_closed={recovered} | active_removed={len(stale)}')

    def load_state(self):
        if not STATE_FILE.exists() and not STATE_BACKUP_FILE.exists():
            print(f'FRESH V15 STATE | equity=${self.equity:.2f} | active=0 | closed=0')
            return
        errors = []
        for path in (STATE_FILE, STATE_BACKUP_FILE):
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                self._restore_state_payload(payload)
                if path == STATE_BACKUP_FILE:
                    print('STATE RESTORED FROM BACKUP | primary state was invalid or unavailable')
                self._reconcile_closed_journal()
                print(f'STATE RESTORED | equity=${self.equity:.2f} | active={len(self.positions)} | closed={len(self.closed)}')
                return
            except Exception as e:
                errors.append(f'{path.name}: {type(e).__name__}: {e}')
        raise RuntimeError('V15 state restore failed; refusing to trade with unknown state | ' + ' | '.join(errors))
    def load_signal_history(self):
        if not SIGNAL_HISTORY.exists():
            return
        try:
            self.signal_history = set(json.loads(SIGNAL_HISTORY.read_text(encoding='utf-8')))
        except Exception as e:
            print('SIGNAL HISTORY RESTORE ERROR:', e)

    def _atomic_write(self, path: Path, text: str):
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)

    def save_state(self):
        state_text = json.dumps({
            'equity': self.equity,
            'closed': self.closed[-2000:],
            'positions': {k: asdict(v) for k, v in self.positions.items()},
        }, indent=2)
        self._atomic_write(STATE_FILE, state_text)
        self._atomic_write(STATE_BACKUP_FILE, state_text)
        self._atomic_write(SIGNAL_HISTORY, json.dumps(list(self.signal_history)[-20000:]))
        self._atomic_write(UNIVERSE_FILE, json.dumps({
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'mode': 'TOP_VOLUME' if UNIVERSE_LIMIT else 'ALL_ELIGIBLE',
            'limit': UNIVERSE_LIMIT,
            'min_volume_usdt': MIN_VOLUME_USDT,
            'symbols': self.universe_symbols,
        }, indent=2))
    @staticmethod
    def is_crypto_market(m):
        base = str(m.get('base') or '').upper()
        sym = str(m.get('symbol') or '').upper()
        info = m.get('info') if isinstance(m.get('info'), dict) else {}
        hay = ' '.join([
            base, sym, str(info.get('symbol') or ''),
            str(info.get('displayName') or ''), str(info.get('assetClass') or ''),
            str(info.get('category') or ''), str(info.get('productType') or ''),
        ]).upper()
        for tok in ('GOLD','XAU','SILVER','XAG','OIL','WTI','BRENT','FOREX','COMMODITY',
                    'INDEX','SPX','SP500','NAS100','NASDAQ','DOW30','US30','USTEC','GER40'):
            if tok in hay:
                return False
        classes = ' '.join([
            str(info.get('assetClass') or ''), str(info.get('category') or ''),
            str(info.get('productType') or ''),
        ]).lower()
        if any(x in classes for x in ('commodity','forex','index','metal')):
            return False
        if base in {'USDC','USDT','USD','DAI','FDUSD','TUSD','USDP','EUR','GBP','JPY'}:
            return False
        return True

    def discover_universe(self):
        try:
            self.exchange.load_markets(reload=False)
            tickers = self.exchange.fetch_tickers()
        except Exception as e:
            if self.universe_symbols:
                print(f'UNIVERSE FALLBACK | using previous {len(self.universe_symbols)} symbols | {type(e).__name__}: {e}')
                return self.universe_symbols
            if UNIVERSE_FILE.exists():
                try:
                    cached = json.loads(UNIVERSE_FILE.read_text(encoding='utf-8')).get('symbols', [])
                    if cached:
                        self.universe_symbols = list(cached)
                        print(f'UNIVERSE FALLBACK | using cached {len(self.universe_symbols)} symbols | {type(e).__name__}: {e}')
                        return self.universe_symbols
                except Exception:
                    pass
            raise
        eligible = []
        rejected_noncrypto = 0
        for sym, m in self.exchange.markets.items():
            try:
                if not m.get('active', True):
                    continue
                if m.get('swap') is not True:
                    continue
                if m.get('linear') is not True:
                    continue
                if m.get('quote') != 'USDT':
                    continue
                if not self.is_crypto_market(m):
                    rejected_noncrypto += 1
                    continue
                t = tickers.get(sym, {})
                qv = t.get('quoteVolume')
                if qv is None:
                    info = t.get('info', {}) if isinstance(t, dict) else {}
                    qv = info.get('quoteVolume') or info.get('turnover')
                qv = float(qv or 0)
                if qv < MIN_VOLUME_USDT:
                    continue
                eligible.append((qv, sym))
            except Exception:
                continue
        eligible.sort(reverse=True)
        if UNIVERSE_LIMIT > 0:
            eligible = eligible[:UNIVERSE_LIMIT]
        self.markets = self.exchange.markets
        self.universe_symbols = [s for _, s in eligible]
        print(f'UNIVERSE DISCOVERED | {len(self.universe_symbols)} symbols | mode={"TOP_VOLUME" if UNIVERSE_LIMIT else "ALL_ELIGIBLE"} | minVol=${MIN_VOLUME_USDT:,.0f} | rejected_noncrypto={rejected_noncrypto}')
        print('TOP:', ', '.join(self.universe_symbols[:15]))
        return self.universe_symbols

    def fetch_df(self, symbol, timeframe=TIMEFRAME, limit=HISTORY_15M):
        out = []
        remaining = int(limit)
        end_ms = None
        while remaining > 0:
            n = min(FETCH_CHUNK, remaining)
            try:
                if end_ms is None:
                    rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=n)
                else:
                    rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=n, params={'endTime': end_ms})
            except Exception as e:
                print(f'DATA REQUEST ERROR | {symbol} | tf={timeframe} | chunk={n} | {type(e).__name__}: {e}')
                return None
            if not rows:
                break
            out = rows + out
            oldest = int(rows[0][0])
            new_end = oldest - 1
            if end_ms is not None and new_end >= end_ms:
                break
            end_ms = new_end
            remaining -= len(rows)
            if len(rows) < n:
                break
        if not out:
            return None
        df = pd.DataFrame(out, columns=['timestamp','open','high','low','close','volume'])
        df = df.drop_duplicates('timestamp').sort_values('timestamp').tail(limit).reset_index(drop=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        return df

    @staticmethod
    def gate_snapshot(x, i):
        r = x
        bull = r.h1_trend_bull & r.h4_trend_bull
        bear = r.h1_trend_bear & r.h4_trend_bear
        emaL = (r.close > r.ema20) & (r.ema20 > r.ema50)
        emaS = (r.close < r.ema20) & (r.ema20 < r.ema50)
        vwapL = r.close > r.vwap
        vwapS = r.close < r.vwap
        diL = r.pdi > r.mdi
        diS = r.mdi > r.pdi
        rsiL = r.rsi.between(46, 64)
        rsiS = r.rsi.between(36, 54)
        structureL = bull & emaL & vwapL & diL & rsiL
        structureS = bear & emaS & vwapS & diS & rsiS
        roomL = r.dist_res_atr >= EARLY_ROOM_MIN_ATR
        roomS = r.dist_sup_atr >= EARLY_ROOM_MIN_ATR
        noChase = (r.move5_atr <= EARLY_MOVE5_MAX_ATR) & (r.dist_ema20_atr <= EARLY_DIST_EMA_MAX_ATR) & (r.range_atr <= 2.25)
        volOk = (r.volr >= 1.00) & (r.vol_slope >= -0.20)
        touchL = r.low.rolling(6).min() <= (r.ema20 + 0.60*r.atr)
        touchS = r.high.rolling(6).max() >= (r.ema20 - 0.60*r.atr)
        reclaimL = (r.close > r.high.shift(1) + 0.05*r.atr) & (r.close > r.ema20) & (r.close_pos >= 0.60) & (r.body_atr >= 0.20)
        reclaimS = (r.close < r.low.shift(1) - 0.05*r.atr) & (r.close < r.ema20) & (r.close_pos <= 0.40) & (r.body_atr >= 0.20)

        # IMPORTANT: final diagnostic state must be the exact same mask used
        # by analyze_latest() for execution. Do not duplicate the signal gate
        # here, otherwise diagnostic and execution can drift apart again.
        signal_long, signal_short = signal_mask(x, CORE_NAME)
        finalL = signal_long.fillna(False)
        finalS = signal_short.fillna(False)

        long_steps = {
            'trend': bool(bull.iloc[i]), 'ema': bool((bull & emaL).iloc[i]),
            'vwap': bool((bull & emaL & vwapL).iloc[i]), 'di': bool((bull & emaL & vwapL & diL).iloc[i]),
            'rsi': bool(structureL.iloc[i]), 'room': bool((structureL & roomL).iloc[i]),
            'no_chase': bool((structureL & roomL & noChase).iloc[i]), 'adx_context': bool((r.h4_adx_pct >= ADX_LONG_PCT).iloc[i] and (r.h4_adx_delta >= ADX_LONG_DELTA).iloc[i]),
            'early_reclaim': bool(finalL.iloc[i]),
        }
        short_steps = {
            'trend': bool(bear.iloc[i]), 'ema': bool((bear & emaS).iloc[i]),
            'vwap': bool((bear & emaS & vwapS).iloc[i]), 'di': bool((bear & emaS & vwapS & diS).iloc[i]),
            'rsi': bool(structureS.iloc[i]), 'room': bool((structureS & roomS).iloc[i]),
            'no_chase': bool((structureS & roomS & noChase).iloc[i]), 'adx_context': bool((r.h4_adx_pct >= ADX_SHORT_PCT).iloc[i] and (r.h4_adx_delta >= ADX_SHORT_DELTA).iloc[i]),
            'early_reclaim': bool(finalS.iloc[i]),
        }
        return {'timestamp': r.timestamp.iloc[i].isoformat(), 'long': long_steps, 'short': short_steps,
                'final_long': bool(finalL.iloc[i]), 'final_short': bool(finalS.iloc[i])}

    def _get_btc_filter_context(self):
        """Execution-layer BTC context. Does not modify CORE signal_mask()."""
        if not BTC_FILTER_ENABLED:
            return {'enabled': False, 'mode': BTC_FILTER_MODE, 'allowed_long': True, 'allowed_short': True, 'reason': 'disabled'}
        if self._btc_filter_context is not None:
            return self._btc_filter_context
        try:
            btc = self.fetch_df('BTC/USDT:USDT', TIMEFRAME, HISTORY_15M)
            if btc is None or len(btc) < 3300:
                reason = f'BTC data unavailable/insufficient rows={0 if btc is None else len(btc)}'
                ctx = {'enabled': True, 'allowed_long': False, 'allowed_short': False, 'reason': reason}
            else:
                bx = enrich(btc)
                i = len(bx) - 2
                h1_bull = bool(bx.h1_trend_bull.iloc[i])
                h4_bull = bool(bx.h4_trend_bull.iloc[i])
                h1_bear = bool(bx.h1_trend_bear.iloc[i])
                h4_bear = bool(bx.h4_trend_bear.iloc[i])
                ctx = {
                    'enabled': True,
                    'mode': BTC_FILTER_MODE,
                    'allowed_long': h1_bull and h4_bull,
                    'allowed_short': h1_bear and h4_bear,
                    'h1_bull': h1_bull, 'h4_bull': h4_bull,
                    'h1_bear': h1_bear, 'h4_bear': h4_bear,
                    'timestamp': bx.timestamp.iloc[i].isoformat(),
                    'close': float(bx.close.iloc[i]),
                    'reason': '1H+4H trend alignment',
                }
            self._btc_filter_context = ctx
            self._btc_filter_timestamp = datetime.now(timezone.utc).isoformat()
            print(f'BTC FILTER | enabled={ctx.get("enabled")} | LONG={ctx.get("allowed_long")} | SHORT={ctx.get("allowed_short")} | reason={ctx.get("reason")} | ts={ctx.get("timestamp", "-")}')
            return ctx
        except Exception as e:
            ctx = {'enabled': True, 'mode': BTC_FILTER_MODE, 'allowed_long': False, 'allowed_short': False,
                   'reason': f'{type(e).__name__}: {e}'}
            self._btc_filter_context = ctx
            print(f'BTC FILTER ERROR | fail_closed={BTC_FILTER_FAIL_CLOSED} | {type(e).__name__}: {e}')
            if not BTC_FILTER_FAIL_CLOSED:
                ctx['allowed_long'] = True
                ctx['allowed_short'] = True
            return ctx

    def _btc_allows(self, side):
        ctx = self._get_btc_filter_context()
        if not ctx.get('enabled', False):
            return True, ctx
        if BTC_FILTER_MODE == 'shadow':
            return True, ctx
        allowed = bool(ctx.get('allowed_long' if side == 'LONG' else 'allowed_short', False))
        return allowed, ctx

    @staticmethod
    def shadow_e_snapshot(x, i, side):
        # V15.1-E is SHADOW ONLY: never changes V15 execution.
        # Fresh pullback = EMA20 touch-zone on either of the 2 completed
        # 15m candles immediately preceding the current signal candle.
        if side == 'LONG':
            touch = (x.low <= x.ema20 + 0.60*x.atr)
        else:
            touch = (x.high >= x.ema20 - 0.60*x.atr)
        ages = []
        for age in (1, 2):
            k = i - age
            if k >= 0 and bool(touch.iloc[k]):
                ages.append(age)
        touch_age = min(ages) if ages else None
        adx_pct = float(x.h4_adx_pct.iloc[i])
        adx_delta = float(x.h4_adx_delta.iloc[i])
        volr = float(x.volr.iloc[i])
        dist = float(x.dist_ema20_atr.iloc[i])
        fresh = touch_age is not None
        adx_ok = not (adx_pct >= 0.90 and adx_delta < 0)
        vol_ok = volr >= 1.20
        ema_ok = dist <= 0.80
        return {
            'fresh_pullback': fresh,
            'touch_age': touch_age,
            'adx_ok': adx_ok,
            'adx_pct': adx_pct,
            'adx_delta': adx_delta,
            'vol_ok': vol_ok,
            'volr': volr,
            'ema_ok': ema_ok,
            'ema_dist_atr': dist,
            'e_pass': fresh and adx_ok and vol_ok and ema_ok,
        }

    def persist_shadow_e(self, p, shadow):
        row = {
            'signal_time': p.opened_at,
            'coin': p.coin,
            'side': p.side,
            'core': p.core,
            'entry': p.entry,
            'sl': p.sl,
            'tp': p.tp,
            'entry_score': p.entry_score,
            **shadow,
        }
        header = not SHADOW_E_FILE.exists()
        pd.DataFrame([row]).to_csv(SHADOW_E_FILE, mode='a', header=header, index=False)

    def _portfolio_loss_streak(self):
        streak = 0
        for r in reversed(self.closed):
            result = str(r.get('result', '')).upper().replace(' HIT', '').strip()
            if result == 'SL':
                streak += 1
            elif result == 'TP':
                break
        return streak

    def _loss_pause_active(self, signal_ts):
        if not V15_DEFENSIVE_MODE or V15_LOSS_STREAK_PAUSE <= 0 or V15_LOSS_PAUSE_MIN <= 0:
            return False
        streak = self._portfolio_loss_streak()
        if streak < V15_LOSS_STREAK_PAUSE or not self.closed:
            return False
        try:
            last_closed = datetime.fromisoformat(str(self.closed[-1].get('closed_at','')).replace('Z','+00:00'))
            now_dt = datetime.fromisoformat(str(signal_ts).replace('Z','+00:00'))
            age = (now_dt-last_closed).total_seconds()/60.0
            return 0 <= age < V15_LOSS_PAUSE_MIN
        except Exception:
            return False

    def _entry_defense(self, position):
        if not V15_DEFENSIVE_MODE:
            return True, 'disabled'
        m = position.entry_metrics or {}
        score = float(position.entry_score)
        dist = float(m.get('dist_ema20_atr', np.inf))
        volr = float(m.get('volr', 0.0))
        if not np.isfinite(score) or score > V15_ENTRY_SCORE_MAX:
            return False, f'score>{V15_ENTRY_SCORE_MAX:g}'
        if not np.isfinite(dist) or dist > V15_DIST_EMA_MAX_ATR:
            return False, f'distEMA>{V15_DIST_EMA_MAX_ATR:g}ATR'
        if not np.isfinite(volr) or volr < V15_MIN_VOLUME_RATIO:
            return False, f'volr<{V15_MIN_VOLUME_RATIO:g}'
        return True, 'pass'

    def analyze_latest(self, symbol):
        df = self.fetch_df(symbol)
        if df is None:
            return None
        if len(df) < 3300:
            print(f'DATA SKIP | {symbol} | rows={len(df)} < 3300 required_for_V15')
            return None
        x = enrich(df)
        i = len(x) - 2
        base_lm, base_sm = signal_mask(x, CORE_NAME)
        if V15_FAILURE_SHIELD_ENABLED:
            lm, sm = failure_shield_mask(x, base_lm, base_sm)
        else:
            lm, sm = base_lm, base_sm
        diag = self.gate_snapshot(x, i)
        side_for_shield = 'LONG' if bool(base_lm.iloc[i]) else ('SHORT' if bool(base_sm.iloc[i]) else None)
        if side_for_shield:
            diag = dict(diag)
            diag['failure_shield'] = failure_shield_snapshot(x, i, side_for_shield)
            diag['failure_shield']['enabled'] = V15_FAILURE_SHIELD_ENABLED
            diag['execution_parity'] = {
                'base_signal_mask': True,
                'shield_applied': V15_FAILURE_SHIELD_ENABLED,
                'base_long': bool(base_lm.iloc[i]),
                'base_short': bool(base_sm.iloc[i]),
                'final_long': bool(lm.iloc[i]),
                'final_short': bool(sm.iloc[i]),
            }
        side = 'LONG' if bool(lm.iloc[i]) else ('SHORT' if bool(sm.iloc[i]) else None)
        ts = x.timestamp.iloc[i].isoformat()
        if side is None:
            return {'signal': None, 'timestamp': ts, 'diag': diag}
        key = f'{symbol}|{side}|{ts}|{CORE_NAME}'
        if key in self.signal_history:
            return {'signal': None, 'timestamp': ts, 'diag': diag}

        # BTC is SHADOW/DIAGNOSTIC ONLY. The authoritative V15 core signal
        # remains signal_mask() above and is never blocked by BTC.
        btc_allowed, btc_ctx = self._btc_allows(side)
        diag = dict(diag)
        diag['btc_filter_mode'] = BTC_FILTER_MODE
        diag['btc_filter_allowed'] = bool(btc_allowed)
        diag['btc_context'] = btc_ctx

        # Anti-repeat guard: the scanner runs every 5 minutes while the setup
        # timeframe is 15m. A persistent setup can therefore produce a new
        # candle timestamp and look like a brand-new signal even though the
        # same coin/side has just been traded. Do not re-alert/re-enter the
        # same coin+side+core during the cooldown after a closed trade.
        # This is deliberately separate from Telegram delivery deduplication:
        # it prevents duplicate EXECUTABLE signals, not just duplicate messages.
        REENTRY_COOLDOWN_MIN = int(os.getenv('V15_REENTRY_COOLDOWN_MIN', '60'))
        try:
            now_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            for r in reversed(self.closed):
                if str(r.get('coin', '')) != symbol:
                    continue
                if str(r.get('side', '')).upper() != side:
                    continue
                if str(r.get('core', CORE_NAME)) != CORE_NAME:
                    continue
                closed_at = str(r.get('closed_at', '')).strip()
                if not closed_at:
                    continue
                closed_dt = datetime.fromisoformat(closed_at.replace('Z', '+00:00'))
                age_min = (now_dt - closed_dt).total_seconds() / 60.0
                if 0 <= age_min < REENTRY_COOLDOWN_MIN:
                    print(f'REENTRY COOLDOWN | {symbol} | {side} | closed={closed_at} | age={age_min:.1f}m < {REENTRY_COOLDOWN_MIN}m')
                    return {'signal': None, 'timestamp': ts, 'diag': diag}
        except Exception as e:
            print(f'REENTRY COOLDOWN CHECK ERROR | {symbol} | {type(e).__name__}: {e}')
        side_int = 1 if side == 'LONG' else -1
        levels = trade_levels(x, i, side_int, 'STRUCTURE')
        if levels is None:
            return {'signal': None, 'timestamp': ts, 'diag': diag}
        entry, sl, tp, risk = levels
        adx_pct = float(x.h4_adx_pct.iloc[i])
        adx_delta = float(x.h4_adx_delta.iloc[i])
        adx_floor = ADX_LONG_PCT if side == 'LONG' else ADX_SHORT_PCT
        delta_floor = ADX_LONG_DELTA if side == 'LONG' else ADX_SHORT_DELTA
        adx_margin = float(np.clip((adx_pct-adx_floor)/(1-adx_floor), 0, 1))
        delta_margin = float(np.clip((adx_delta-delta_floor)/2.0, 0, 1))
        room = float(x.dist_res_atr.iloc[i] if side == 'LONG' else x.dist_sup_atr.iloc[i])
        room_margin = float(np.clip((room-EARLY_ROOM_MIN_ATR)/1.50, 0, 1))
        dist = float(x.dist_ema20_atr.iloc[i])
        loc_center = float(np.clip(1.0-abs(dist-0.60)/0.60, 0, 1))
        vol_margin = float(np.clip(float(x.volr.iloc[i])/2.0, 0, 1))
        trigger_margin = float(np.clip(float(x.body_atr.iloc[i])/0.80, 0, 1))
        score = 100.0*(0.30*adx_margin + 0.20*delta_margin + 0.20*room_margin + 0.15*loc_center + 0.10*vol_margin + 0.05*trigger_margin)
        score_breakdown = {
            'adx': adx_margin,
            'delta': delta_margin,
            'room': room_margin,
            'location': loc_center,
            'volume': vol_margin,
            'trigger': trigger_margin,
        }
        entry_metrics = {
            'close': float(x.close.iloc[i]),
            'rsi': float(x.rsi.iloc[i]),
            'h4_adx_pct': adx_pct,
            'h4_adx_delta': adx_delta,
            'volr': float(x.volr.iloc[i]),
            'vol_slope': float(x.vol_slope.iloc[i]),
            'dist_ema20_atr': dist,
            'room_atr': room,
            'body_atr': float(x.body_atr.iloc[i]),
            'close_pos': float(x.close_pos.iloc[i]),
            'atr': float(x.atr.iloc[i]),
        }
        p = Position(
            symbol, side, entry, sl, tp, 1.0, self.equity*RISK_PCT, ts,
            entry_score=float(score), entry_diag=diag, entry_metrics=entry_metrics,
            score_breakdown=score_breakdown, signal_key=key, btc_context=btc_ctx,
            strategy_fingerprint=V15_STRATEGY_FINGERPRINT,
        )
        defense_ok, defense_reason = self._entry_defense(p)
        diag['defensive_gate'] = {'pass': defense_ok, 'reason': defense_reason}
        if not defense_ok:
            print(f'DEFENSIVE GATE | {symbol} | {side} | score={score:.1f} | dist={dist:.2f} | volr={float(x.volr.iloc[i]):.2f} | {defense_reason}')
            return {'signal': None, 'timestamp': ts, 'diag': diag}

        shadow_e = self.shadow_e_snapshot(x, i, side)
        self.persist_shadow_e(p, shadow_e)
        return {'signal': {'position': p, 'score': float(score), 'key': key, 'side': side,
                           'timestamp': ts, 'entry': entry, 'sl': sl, 'tp': tp,
                           'entry_metrics': entry_metrics, 'score_breakdown': score_breakdown, 'btc_context': btc_ctx},
                'timestamp': ts, 'diag': diag}

    def track(self, p):
        df = self.fetch_df(p.coin, TRACK_TIMEFRAME, 20)
        if df is None or len(df) < 3:
            return None
        # IMPORTANT: only candles formed AFTER the position opened may close it.
        # The previous implementation scanned the last 20 completed candles
        # without filtering by opened_at. That allowed an old candle, formed
        # before entry, to trigger an immediate historical TP/SL. BLESS #100
        # exposed this exact bug: opened_at=07:45Z while the detected TP candle
        # was 06:55Z. The result notification was sent, but the forward-state
        # validator correctly rejected the impossible close and kept the master
        # journal at 99.
        try:
            opened_at = pd.Timestamp(p.opened_at)
            if opened_at.tzinfo is None:
                opened_at = opened_at.tz_localize("UTC")
            else:
                opened_at = opened_at.tz_convert("UTC")
        except Exception:
            return None

        for _, r in df.iloc[:-1].iterrows():
            candle_ts = pd.Timestamp(r.timestamp)
            if candle_ts.tzinfo is None:
                candle_ts = candle_ts.tz_localize("UTC")
            else:
                candle_ts = candle_ts.tz_convert("UTC")
            if candle_ts <= opened_at:
                continue

            h = float(r.high); l = float(r.low)
            sl = l <= p.sl if p.side == 'LONG' else h >= p.sl
            tp = h >= p.tp if p.side == 'LONG' else l <= p.tp
            if sl and tp: return ('SL', p.sl, candle_ts.isoformat())
            if sl: return ('SL', p.sl, candle_ts.isoformat())
            if tp: return ('TP', p.tp, candle_ts.isoformat())
        return None

    def close(self, key, result, price, ts):
        # IMPORTANT: do not pop the position before persistence succeeds.
        # The old order could silently delete a live trade if CSV/journal
        # persistence raised an exception.
        p = self.positions[key]
        rr = RR if result == 'TP' else -1.0
        risk_now = self.equity * RISK_PCT
        new_equity = self.equity + risk_now * rr
        rec = asdict(p)
        rec.update({'closed_at': ts, 'exit': price, 'result': result, 'R': rr, 'equity_after': new_equity})

        # Persist the result before removing the live position. If this fails,
        # the position remains active and the next scan can retry.
        pd.DataFrame([rec]).to_csv(
            JOURNAL_FILE,
            mode='a',
            header=not JOURNAL_FILE.exists(),
            index=False,
        )
        self.closed.append(rec)
        self.equity = new_equity
        print(f'🎯 {result} | {p.coin} | {p.side} | R={rr:+.2f} | Equity=${self.equity:.2f}')
        try:
            # Use the deterministic result-delivery guard so TP/SL notifications
            # have their own RESULT identity, 5 delivery retries, and are always
            # recoverable by reconcile_v15_results.py on the next workflow run.
            from telegram_result_guard import send_result as send_guarded_result
            sent = send_guarded_result(asdict(p), result, price, ts, self.equity)
            if sent:
                print(f'📨 TELEGRAM RESULT GUARDED | {p.coin} | {p.side} | {result} | delivered')
            else:
                print(f'⚠️ TELEGRAM RESULT GUARDED NOT SENT | {p.coin} | {p.side} | {result} | queued_for_reconcile')
        except Exception as e:
            # Notification failure must never break the trading/paper engine;
            # the closed record remains in state and reconcile retries delivery.
            print(f'TELEGRAM RESULT GUARDED ERROR | {p.coin} | {result} | {type(e).__name__}: {e}')

        # Only now is it safe to remove the live position.
        self.positions.pop(key, None)
    def report(self):
        if not self.closed:
            print(f'PAPER REPORT | closed=0 | active={len(self.positions)} | equity=${self.equity:.2f}')
            return
        r = np.array([float(x['R']) for x in self.closed])
        w = int((r > 0).sum()); l = int((r < 0).sum())
        pf = float(r[r>0].sum() / (-r[r<0].sum())) if l else float('inf')
        eq = np.cumsum(r)
        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        print(f'PAPER REPORT | closed={len(r)} | W/L={w}/{l} | WR={w/len(r)*100:.2f}% | NetR={r.sum():+.2f} | PF={pf:.3f} | DD_R={dd.min():.2f} | Equity=${self.equity:.2f}')

    def scan_once(self):
        t0 = time.perf_counter()
        print('\\n' + '='*100)
        print(f'SAM EDGE V15 | BUILD={BUILD} | CORE={CORE_NAME}')
        print(f'TIMEFRAME={TIMEFRAME} | TRACK={TRACK_TIMEFRAME} | EQUITY=${self.equity:.2f} | RISK={RISK_PCT*100:.2f}% | MAX_ACTIVE={MAX_ACTIVE}')
        print(f'GATE | 4H ADX LONG={ADX_LONG_PCT:.2f}/{ADX_LONG_DELTA:.2f} | SHORT={ADX_SHORT_PCT:.2f}/{ADX_SHORT_DELTA:.2f} | EARLY 15M RECLAIM | NO-CHASE move5<={EARLY_MOVE5_MAX_ATR:.2f} ATR distEMA<={EARLY_DIST_EMA_MAX_ATR:.2f} ATR')
        print(f'BTC CONTEXT | enabled={BTC_FILTER_ENABLED} | mode={BTC_FILTER_MODE} | alignment=1H+4H | execution_blocking=False')
        print(f'FAILURE SHIELD | enabled={V15_FAILURE_SHIELD_ENABLED} | stacked-veto only | core={CORE_NAME}')
        syms = self.discover_universe()
        # Refresh BTC context once per scan; existing positions are never blocked or closed by this filter.
        self._btc_filter_context = None
        self._btc_filter_timestamp = None
        if BTC_FILTER_ENABLED:
            self._get_btc_filter_context()
        for key, p in list(self.positions.items()):
            try:
                res = self.track(p)
                if res:
                    self.close(key, *res)
            except Exception as e:
                print(f'TRACK ERROR | {p.coin} | {e}')
        if V15_DEFENSIVE_MODE and self._loss_pause_active(datetime.now(timezone.utc).isoformat()):
            print(f'PORTFOLIO CIRCUIT BREAKER | loss_streak={self._portfolio_loss_streak()} | pause={V15_LOSS_PAUSE_MIN}m | new_entries=0')
            self.save_state(); self.report()
            print(f'SCAN COMPLETE | circuit-breaker active | universe={len(syms)}')
            return

        free_slots = max(0, MAX_ACTIVE-len(self.positions))
        candidates = []
        scanned = valid_data = request_errors = 0
        diag_total = {
            'LONG': {k: 0 for k in ('trend','ema','vwap','di','rsi','room','no_chase','adx_context','early_reclaim')},
            'SHORT': {k: 0 for k in ('trend','ema','vwap','di','rsi','room','no_chase','adx_context','early_reclaim')},
            'final_long': 0, 'final_short': 0,
        }
        near_miss = []
        for n, sym in enumerate(syms, 1):
            scanned += 1
            if sym in self.positions:
                continue
            before = time.perf_counter()
            try:
                a = self.analyze_latest(sym)
                _ = time.perf_counter() - before
                if a is not None:
                    if a.get('diag'):
                        valid_data += 1
                    d = a.get('diag')
                    if d:
                        for side_key, side_name in (('long','LONG'),('short','SHORT')):
                            for gate, ok in d[side_key].items():
                                if ok: diag_total[side_name][gate] += 1
                        if d['final_long']: diag_total['final_long'] += 1
                        if d['final_short']: diag_total['final_short'] += 1
                        score_diag_long = sum(d['long'].values())
                        score_diag_short = sum(d['short'].values())
                        if score_diag_long or score_diag_short:
                            side = 'LONG' if score_diag_long >= score_diag_short else 'SHORT'
                            near_miss.append((max(score_diag_long, score_diag_short), sym, side, d))
                if a and a.get('signal'):
                    c = a['signal']; candidates.append(c)
                    print(f'CANDIDATE [{n}/{len(syms)}] | {sym} | {c["side"]} | score={c["score"]:.1f} | Entry={c["entry"]:.8g} | SL={c["sl"]:.8g} | TP={c["tp"]:.8g}')
                if n % 25 == 0:
                    print(f'PROGRESS {n}/{len(syms)} | data_ok={valid_data} | candidates={len(candidates)}')
            except Exception as e:
                if isinstance(e, (ccxt.NetworkError, ccxt.RequestTimeout, ConnectionError)):
                    request_errors += 1
                print(f'SCAN ERROR [{n}/{len(syms)}] | {sym} | {type(e).__name__}: {e}')
        data_skips = max(0, scanned-valid_data-request_errors)
        print(f'DATA QUALITY | scanned={scanned} | data_ok={valid_data} | data_skips={data_skips} | request_errors={request_errors}')
        print('GATE DIAG | LONG | ' + ' | '.join(f'{k}={v}' for k,v in diag_total['LONG'].items()))
        print('GATE DIAG | SHORT| ' + ' | '.join(f'{k}={v}' for k,v in diag_total['SHORT'].items()))
        print(f'FINAL {CORE_NAME} | LONG={diag_total["final_long"]} | SHORT={diag_total["final_short"]} | TOTAL={diag_total["final_long"]+diag_total["final_short"]}')
        near_miss.sort(key=lambda z: (z[0],z[1]), reverse=True)
        for rank, (score_diag, sym, side, d) in enumerate(near_miss[:5],1):
            print(f'NEAR MISS #{rank} | {sym} | {side} | gates={score_diag}/9 | ts={d["timestamp"]}')
        if V15_DEFENSIVE_MODE and V15_SELECTION_MODE == 'LOW_SCORE':
            candidates.sort(key=lambda z: (z['score'], z['timestamp']))
        else:
            candidates.sort(key=lambda z: (z['score'], z['timestamp']), reverse=True)

        selected = []
        active_long = sum(1 for p in self.positions.values() if p.side == 'LONG')
        active_short = sum(1 for p in self.positions.values() if p.side == 'SHORT')
        for c in candidates:
            if len(selected) >= free_slots:
                break
            if V15_DEFENSIVE_MODE:
                if c['side'] == 'LONG' and active_long >= V15_MAX_ACTIVE_PER_SIDE:
                    continue
                if c['side'] == 'SHORT' and active_short >= V15_MAX_ACTIVE_PER_SIDE:
                    continue
            selected.append(c)
            if c['side'] == 'LONG': active_long += 1
            else: active_short += 1
        print(f'SELECTION | candidates={len(candidates)} | free_slots={free_slots} | selected={len(selected)}')
        for rank, c in enumerate(candidates[:max(MAX_ACTIVE,10)],1):
            tag='SELECT' if c in selected else 'WAIT'
            print(f'  #{rank:<2} {tag:<6} | {c["position"].coin:<24} | {c["side"]:<5} | score={c["score"]:.1f}')
        from notifiers import send_signal
        # Telegram must contain ONLY executable entries. Candidates that lose
        # active-slot selection stay in Actions logs as WAITLIST and are never
        # journaled or sent to Telegram.
        for c in selected:
            p=c['position']; key=c['key']
            if key in self.signal_history:
                continue
            payload=asdict(p)
            payload['selection_score']=round(c['score'],2)
            payload['telegram_status']='EXECUTED'
            sent=send_signal(payload,self.equity)
            if sent:
                self.signal_history.add(key)
                print(f'📨 TELEGRAM SIGNAL | {p.coin} | {p.side} | status=EXECUTED | score={c["score"]:.1f}')
            else:
                print(f'⚠️ TELEGRAM NOT SENT | {p.coin} | {p.side} | score={c["score"]:.1f}')

            p=c['position']; key=c['key']
            self.positions[p.coin]=p
            print(f'✅ SIGNAL | {p.coin} | {p.side} | score={c["score"]:.1f} | Entry={p.entry:.8g} SL={p.sl:.8g} TP={p.tp:.8g}')
            # Persist each newly opened position immediately so a runner interruption
            # cannot erase an entry created earlier in the same scan.
            self.save_state()
        if len(candidates)>len(selected):
            print(f'WAITLIST | {len(candidates)-len(selected)} valid V15 candidates not executed because active slots are full.')
        self.save_state(); self.report()
        print(f'SCAN COMPLETE | elapsed={time.perf_counter()-t0:.1f}s | universe={scanned} | candidates={len(candidates)} | selected={len(selected)} | next in ~{SCAN_SEC}s')

    def run(self):
        def stop_handler(signum, frame):
            self.running=False
            print('\\nSTOP REQUESTED | saving V15 state...')
            self.save_state()
        signal.signal(signal.SIGINT, stop_handler)
        try:
            signal.signal(signal.SIGTERM, stop_handler)
        except Exception:
            pass
        while self.running:
            try:
                self.scan_once()
            except Exception as e:
                print(f'FATAL SCAN ERROR | {type(e).__name__}: {e}')
            if self.running:
                print(f'SLEEP {SCAN_SEC}s...')
                time.sleep(SCAN_SEC)


if __name__ == '__main__':
    PaperEngine().run()