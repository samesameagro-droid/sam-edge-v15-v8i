from __future__ import annotations

import os

import ccxt

from bingx_direct_v15 import fetch_markets, fetch_tickers, fetch_ohlcv
ccxt.bingx.fetch_markets = fetch_markets
ccxt.bingx.fetch_tickers = fetch_tickers
ccxt.bingx.fetch_ohlcv = fetch_ohlcv

import notifiers
import telegram_result_guard
from run_v15_forward_test import ForwardPaperEngine
from v15_1_btc_aware_shadow import BTCAwareShadow

RUN_ONCE = os.getenv("RUN_ONCE", "0").lower() in {"1", "true", "yes", "on"}

# IMPORTANT: this branch is a research shadow only. Production main already
# sends the real V15 Telegram signal/result notifications. Never duplicate them.
def _shadow_send_signal(*args, **kwargs):
    p = args[0] if args else {}
    print(f"BTC SHADOW TELEGRAM SUPPRESS | {p.get('coin', '?')} | {p.get('side', '?')} | V15 production owns delivery")
    return True


def _shadow_result_guard(*args, **kwargs):
    p = args[0] if args else {}
    print(f"BTC SHADOW RESULT SUPPRESS | {p.get('coin', '?')} | V15 production owns delivery")
    return True


notifiers.send_signal = _shadow_send_signal
telegram_result_guard.send_result = _shadow_result_guard

_original_analyze = ForwardPaperEngine.analyze_latest
_original_close = ForwardPaperEngine.close


def _init_shadow(self):
    if not hasattr(self, "btc_shadow"):
        self.btc_shadow = BTCAwareShadow(self.exchange)


def _analyze_latest(self, symbol):
    _init_shadow(self)
    result = _original_analyze(self, symbol)
    if result and result.get("signal"):
        p = result["signal"]["position"]
        try:
            self.btc_shadow.record_signal(p)
        except Exception as e:
            # Shadow failure must never block the actual V15 paper trade.
            print(f"BTC SHADOW ERROR | {symbol} | {type(e).__name__}: {e}")
    return result


def _close(self, key, result, price, ts):
    _init_shadow(self)
    p = self.positions[key]
    signal_key = p.signal_key
    _original_close(self, key, result, price, ts)
    rr = 1.25 if result == "TP" else -1.0
    try:
        self.btc_shadow.record_result(signal_key, result, rr, ts)
        self.btc_shadow.write_summary()
    except Exception as e:
        print(f"BTC SHADOW RESULT ERROR | {signal_key} | {type(e).__name__}: {e}")


ForwardPaperEngine.analyze_latest = _analyze_latest
ForwardPaperEngine.close = _close


if __name__ == "__main__":
    print("SAM EDGE V15.1 | BTC-AWARE SHADOW TEST")
    print("EXECUTION: V15 BASELINE UNCHANGED")
    print("BTC-AWARE: SHADOW ONLY / NO TRADE BLOCK / NO TELEGRAM DUPLICATION")
    engine = ForwardPaperEngine()
    _init_shadow(engine)
    engine.btc_shadow.write_summary()
    if RUN_ONCE:
        engine.scan_once()
    else:
        engine.run()
