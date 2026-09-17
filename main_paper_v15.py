from __future__ import annotations

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
    CORE_NAME, RR, enrich, signal_mask, trade_levels,
    ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
    EARLY_MOVE5_MAX_ATR, EARLY_DIST_EMA_MAX_ATR, EARLY_ROOM_MIN_ATR,
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

STATE_FILE = Path('paper_v15_state.json')
JOURNAL_FILE = Path('paper_v15_trades.csv')
SIGNAL_HISTORY = Path('paper_v15_signal_history.json')
UNIVERSE_FILE = Path('paper_v15_universe.json')


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
        self.load_state()
        self.load_signal_history()

    def load_state(self):
        if not STATE_FILE.exists():
            print(f'FRESH V15 STATE | equity=${self.equity:.2f} | active=0 | closed=0')
            return
        try:
            d = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            self.equity = float(d.get('equity', START_EQUITY))
            self.closed = d.get('closed', [])
            self.positions = {k: Position(**v) for k, v in d.get('positions', {}).items()}
            print(f'STATE RESTORED | equity=${self.equity:.2f} | active={len(self.positions)} | closed={len(self.closed)}')
        except Exception as e:
            print('STATE RESTORE ERROR:', e)

    def load_signal_history(self):
        if not SIGNAL_HISTORY.exists():
            return
        try:
            self.signal_history = set(json.loads(SIGNAL_HISTORY.read_text(encoding='utf-8')))
        except Exception as e:
            print('SIGNAL HISTORY RESTORE ERROR:', e)

    def save_state(self):
        STATE_FILE.write_text(json.dumps({
            'equity': self.equity,
            'closed': self.closed[-2000:],
            'positions': {k: asdict(v) for k, v in self.positions.items()},
        }, indent=2), encoding='utf-8')
        SIGNAL_HISTORY.write_text(json.dumps(list(self.signal_history)[-20000:]), encoding='utf-8')
        UNIVERSE_FILE.write_text(json.dumps({
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'mode': 'TOP_VOLUME' if UNIVERSE_LIMIT else 'ALL_ELIGIBLE',
            'limit': UNIVERSE_LIMIT,
            'min_volume_usdt': MIN_VOLUME_USDT,
            'symbols': self.universe_symbols,
        }, indent=2), encoding='utf-8')

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
        self.exchange.load_markets(reload=False)
        tickers = self.exchange.fetch_tickers()
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
            'no_chase': bool((structureL & roomL & noChase).iloc[i]), 'adx': bool((structureL & roomL & noChase & volOk & touchL & (r.h4_adx_pct >= ADX_LONG_PCT) & (r.h4_adx_delta >= ADX_LONG_DELTA)).iloc[i]),
            'early_reclaim': bool(finalL.iloc[i]),
        }
        short_steps = {
            'trend': bool(bear.iloc[i]), 'ema': bool((bear & emaS).iloc[i]),
            'vwap': bool((bear & emaS & vwapS).iloc[i]), 'di': bool((bear & emaS & vwapS & diS).iloc[i]),
            'rsi': bool(structureS.iloc[i]), 'room': bool((structureS & roomS).iloc[i]),
            'no_chase': bool((structureS & roomS & noChase).iloc[i]), 'adx': bool((structureS & roomS & noChase & volOk & touchS & (r.h4_adx_pct >= ADX_SHORT_PCT) & (r.h4_adx_delta >= ADX_SHORT_DELTA)).iloc[i]),
            'early_reclaim': bool(finalS.iloc[i]),
        }
        return {'timestamp': r.timestamp.iloc[i].isoformat(), 'long': long_steps, 'short': short_steps,
                'final_long': bool(finalL.iloc[i]), 'final_short': bool(finalS.iloc[i])}

    def analyze_latest(self, symbol):
        df = self.fetch_df(symbol)
        if df is None:
            return None
        if len(df) < 3300:
            print(f'DATA SKIP | {symbol} | rows={len(df)} < 3300 required_for_V15')
            return None
        x = enrich(df)
        i = len(x) - 2
        lm, sm = signal_mask(x, CORE_NAME)
        diag = self.gate_snapshot(x, i)
        side = 'LONG' if bool(lm.iloc[i]) else ('SHORT' if bool(sm.iloc[i]) else None)
        ts = x.timestamp.iloc[i].isoformat()
        if side is None:
            return {'signal': None, 'timestamp': ts, 'diag': diag}
        key = f'{symbol}|{side}|{ts}|{CORE_NAME}'
        if key in self.signal_history:
            return {'signal': None, 'timestamp': ts, 'diag': diag}
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
            score_breakdown=score_breakdown, signal_key=key,
        )
        return {'signal': {'position': p, 'score': float(score), 'key': key, 'side': side,
                           'timestamp': ts, 'entry': entry, 'sl': sl, 'tp': tp,
                           'entry_metrics': entry_metrics, 'score_breakdown': score_breakdown},
                'timestamp': ts, 'diag': diag}

    def track(self, p):
        df = self.fetch_df(p.coin, TRACK_TIMEFRAME, 20)
        if df is None or len(df) < 3:
            return None
        for _, r in df.iloc[:-1].iterrows():
            h = float(r.high); l = float(r.low)
            sl = l <= p.sl if p.side == 'LONG' else h >= p.sl
            tp = h >= p.tp if p.side == 'LONG' else l <= p.tp
            if sl and tp: return ('SL', p.sl, r.timestamp.isoformat())
            if sl: return ('SL', p.sl, r.timestamp.isoformat())
            if tp: return ('TP', p.tp, r.timestamp.isoformat())
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
        syms = self.discover_universe()
        for key, p in list(self.positions.items()):
            try:
                res = self.track(p)
                if res:
                    self.close(key, *res)
            except Exception as e:
                print(f'TRACK ERROR | {p.coin} | {e}')
        free_slots = max(0, MAX_ACTIVE-len(self.positions))
        candidates = []
        scanned = valid_data = request_errors = 0
        diag_total = {
            'LONG': {k: 0 for k in ('trend','ema','vwap','di','rsi','room','no_chase','adx','early_reclaim')},
            'SHORT': {k: 0 for k in ('trend','ema','vwap','di','rsi','room','no_chase','adx','early_reclaim')},
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
        candidates.sort(key=lambda z: (z['score'], z['timestamp']), reverse=True)
        selected = candidates[:free_slots]
        print(f'SELECTION | candidates={len(candidates)} | free_slots={free_slots} | selected={len(selected)}')
        for rank, c in enumerate(candidates[:max(MAX_ACTIVE,10)],1):
            tag='SELECT' if c in selected else 'WAIT'
            print(f'  #{rank:<2} {tag:<6} | {c["position"].coin:<24} | {c["side"]:<5} | score={c["score"]:.1f}')
        from notifiers import send_signal
        selected_keys={c['key'] for c in selected}
        for c in candidates:
            p=c['position']; key=c['key']
            if key in self.signal_history:
                continue
            payload=asdict(p)
            payload['selection_score']=round(c['score'],2)
            payload['telegram_status']='EXECUTED' if key in selected_keys else 'VALID V15 SIGNAL - WAITLIST'
            sent=send_signal(payload,self.equity)
            if sent:
                self.signal_history.add(key)
                print(f'📨 TELEGRAM SIGNAL | {p.coin} | {p.side} | status={payload["telegram_status"]} | score={c["score"]:.1f}')
            else:
                print(f'⚠️ TELEGRAM NOT SENT | {p.coin} | {p.side} | score={c["score"]:.1f}')
        for c in selected:
            p=c['position']; key=c['key']
            self.positions[p.coin]=p
            print(f'✅ SIGNAL | {p.coin} | {p.side} | score={c["score"]:.1f} | Entry={p.entry:.8g} SL={p.sl:.8g} TP={p.tp:.8g}')
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
