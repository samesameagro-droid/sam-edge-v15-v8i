from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from core_engine_v15 import (
    CORE_NAME, RR, SWING_LOOKBACK, enrich, signal_mask,
    ADX_LONG_PCT, ADX_LONG_DELTA, ADX_SHORT_PCT, ADX_SHORT_DELTA,
)

load_dotenv()

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
        self.exchange.load_markets(reload=True)
        tickers = self.exchange.fetch_tickers()
        eligible = []
        rejected_noncrypto = 0
        for sym, m in self.exchange.markets.items():
            try:
                if not m.get('active', True): continue
                if m.get('swap') is not True: continue
                if m.get('linear') is not True: continue
                if m.get('quote') != 'USDT': continue
                if not self.is_crypto_market(m):
                    rejected_noncrypto += 1
                    continue
                t = tickers.get(sym, {})
                qv = t.get('quoteVolume')
                if qv is None:
                    info = t.get('info', {}) if isinstance(t, dict) else {}
                    qv = info.get('quoteVolume') or info.get('turnover')
                qv = float(qv or 0)
                if qv < MIN_VOLUME_USDT: continue
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
            if end_ms is None:
                rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=n)
            else:
                rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=n, params={'endTime': end_ms})
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

    def analyze_latest(self, symbol):
        df = self.fetch_df(symbol)
        if df is None or len(df) < 3300:
            return None
        x = enrich(df)
        i = len(x) - 2  # last CLOSED 15m candle
        lm, sm = signal_mask(x, CORE_NAME)
        side = 'LONG' if bool(lm.iloc[i]) else ('SHORT' if bool(sm.iloc[i]) else None)
        ts = x.timestamp.iloc[i].isoformat()
        if side is None:
            return {'signal': None, 'timestamp': ts}
        key = f'{symbol}|{side}|{ts}|{CORE_NAME}'
        if key in self.signal_history:
            return {'signal': None, 'timestamp': ts}

        entry = float(x.close.iloc[i]); atr = float(x.atr.iloc[i])
        if not np.isfinite(atr) or atr <= 0:
            return None
        if side == 'LONG':
            sl = float(x.low.iloc[max(0, i-SWING_LOOKBACK):i].min()) - atr
            risk = entry - sl
            tp = entry + RR * risk
            adx_margin = np.clip((float(x.h4_adx_pct.iloc[i])-ADX_LONG_PCT)/(1-ADX_LONG_PCT), 0, 1)
            delta_margin = np.clip((float(x.h4_adx_delta.iloc[i])-ADX_LONG_DELTA)/2.0, 0, 1)
            room = float(x.dist_res_atr.iloc[i])
            candle = float(x.c2h_body_atr.iloc[i])
        else:
            sl = float(x.high.iloc[max(0, i-SWING_LOOKBACK):i].max()) + atr
            risk = sl - entry
            tp = entry - RR * risk
            adx_margin = np.clip((float(x.h4_adx_pct.iloc[i])-ADX_SHORT_PCT)/(1-ADX_SHORT_PCT), 0, 1)
            delta_margin = np.clip((float(x.h4_adx_delta.iloc[i])-ADX_SHORT_DELTA)/2.0, 0, 1)
            room = float(x.dist_sup_atr.iloc[i])
            candle = float(x.c2h_body_atr.iloc[i])
        if not np.isfinite(risk) or risk <= 0:
            return None

        dist = float(x.dist_ema20_atr.iloc[i])
        room_margin = np.clip((room-0.85)/1.50, 0, 1)
        loc_center = np.clip(1.0-abs(dist-0.70)/0.70, 0, 1)
        candle_margin = np.clip((candle-0.55)/0.80, 0, 1)
        score = 100.0*(0.35*adx_margin + 0.25*delta_margin + 0.20*room_margin + 0.10*loc_center + 0.10*candle_margin)

        p = Position(symbol, side, entry, float(sl), float(tp), 1.0, self.equity*RISK_PCT, ts)
        return {
            'signal': {'position': p, 'score': float(score), 'key': key, 'side': side,
                       'timestamp': ts, 'entry': entry, 'sl': float(sl), 'tp': float(tp)},
            'timestamp': ts,
        }

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
        p = self.positions.pop(key)
        rr = RR if result == 'TP' else -1.0
        risk_now = self.equity * RISK_PCT
        self.equity += risk_now * rr
        rec = asdict(p)
        rec.update({'closed_at': ts, 'exit': price, 'result': result, 'R': rr, 'equity_after': self.equity})
        self.closed.append(rec)
        pd.DataFrame([rec]).to_csv(JOURNAL_FILE, mode='a', header=not JOURNAL_FILE.exists(), index=False)
        print(f'🎯 {result} | {p.coin} | {p.side} | R={rr:+.2f} | Equity=${self.equity:.2f}')

    def report(self):
        if not self.closed:
            print(f'PAPER REPORT | closed=0 | active={len(self.positions)} | equity=${self.equity:.2f}')
            return
        r = np.array([float(x['R']) for x in self.closed])
        w = int((r > 0).sum()); l = int((r < 0).sum())
        pf = float(r[r>0].sum() / (-r[r<0].sum())) if l else float('inf')
        dd = np.maximum.accumulate(np.cumsum(r)) - np.cumsum(r)
        print(f'PAPER REPORT | closed={len(r)} | W/L={w}/{l} | WR={w/len(r)*100:.2f}% | NetR={r.sum():+.2f} | PF={pf:.3f} | DD_R={dd.max():.2f} | Equity=${self.equity:.2f}')

    def scan_once(self):
        t0 = time.perf_counter()
        print('\n' + '='*100)
        print('SAM EDGE V15 | UNIVERSAL PAPER FORWARD | V15 ADX4H + CANDLE2H')
        print(f'TIMEFRAME={TIMEFRAME} | TRACK={TRACK_TIMEFRAME} | EQUITY=${self.equity:.2f} | RISK={RISK_PCT*100:.2f}% | MAX_ACTIVE={MAX_ACTIVE}')
        print(f'GATE | 4H ADX LONG={ADX_LONG_PCT:.2f}/{ADX_LONG_DELTA:.2f} | SHORT={ADX_SHORT_PCT:.2f}/{ADX_SHORT_DELTA:.2f} | 2H CANDLE=STRICT')

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
        scanned = 0
        for n, sym in enumerate(syms, 1):
            scanned += 1
            if sym in self.positions:
                continue
            try:
                a = self.analyze_latest(sym)
                if a and a.get('signal'):
                    c = a['signal']; candidates.append(c)
                    print(f'CANDIDATE [{n}/{len(syms)}] | {sym} | {c["side"]} | score={c["score"]:.1f} | Entry={c["entry"]:.8g}')
                if n % 25 == 0:
                    print(f'PROGRESS {n}/{len(syms)} | candidates={len(candidates)}')
            except Exception as e:
                print(f'SCAN ERROR [{n}/{len(syms)}] | {sym} | {type(e).__name__}: {e}')

        candidates.sort(key=lambda z: (z['score'], z['timestamp']), reverse=True)
        selected = candidates[:free_slots]
        print(f'SELECTION | candidates={len(candidates)} | free_slots={free_slots} | selected={len(selected)}')
        for rank, c in enumerate(candidates[:max(MAX_ACTIVE, 10)], 1):
            tag = 'SELECT' if c in selected else 'WAIT'
            print(f'  #{rank:<2} {tag:<6} | {c["position"].coin:<24} | {c["side"]:<5} | score={c["score"]:.1f}')

        for c in selected:
            p = c['position']; key = c['key']
            self.positions[p.coin] = p
            self.signal_history.add(key)
            print(f'✅ SIGNAL | {p.coin} | {p.side} | score={c["score"]:.1f} | Entry={p.entry:.8g} SL={p.sl:.8g} TP={p.tp:.8g}')
            try:
                from notifiers import send_signal
                payload = asdict(p)
                payload['selection_score'] = round(c['score'], 2)
                send_signal(payload, self.equity)
            except Exception as e:
                print('TELEGRAM ERROR:', e)

        if len(candidates) > len(selected):
            print(f'WAITLIST | {len(candidates)-len(selected)} valid V15 candidates not executed because active slots are full.')

        self.save_state()
        self.report()
        print(f'SCAN COMPLETE | elapsed={time.perf_counter()-t0:.1f}s | universe={scanned} | candidates={len(candidates)} | selected={len(selected)} | next in ~{SCAN_SEC}s')

    def run(self):
        def stop_handler(signum, frame):
            self.running = False
            print('\nSTOP REQUESTED | saving V15 state...')
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
                for _ in range(SCAN_SEC):
                    if not self.running: break
                    time.sleep(1)
        print('SAM EDGE V15 STOPPED.')


if __name__ == '__main__':
    PaperEngine().run()
