"""ST-ADX V3.3 signal-only scanner. Public BingX OHLCV; no orders or paper positions."""
from __future__ import annotations
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt
import pandas as pd
import requests
from strategy_core import (
    add_indicators, LONG_ALLOWLIST, SHORT_ALLOWLIST,
    ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER,
)

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "signal_state.json"
BAR_MS = 3_600_000
GRACE_MS = 20 * 60_000
FEE = 0.0005
SLIP = 0.0002
ASSETS = sorted(LONG_ALLOWLIST | SHORT_ALLOWLIST)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("ST_ADX_V33_SIGNAL_ONLY")


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"initialized": False, "last_signal_ts": {}}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE)


def telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secret")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API rejected message: {payload}")


def resolve_symbol(exchange, base: str) -> str:
    for symbol in (f"{base}/USDT:USDT", f"{base}/USDT"):
        market = exchange.markets.get(symbol)
        if market and market.get("swap") and market.get("linear") and market.get("quote") == "USDT" and market.get("active") is not False:
            return symbol
    for symbol, market in exchange.markets.items():
        if market.get("base") == base and market.get("quote") == "USDT" and market.get("swap") and market.get("linear") and market.get("active") is not False:
            return symbol
    raise RuntimeError(f"No active BingX USDT-linear perpetual market for {base}")


def fetch_frame(exchange, base: str):
    symbol = resolve_symbol(exchange, base)
    rows = None
    for attempt, delay in enumerate((0, 2, 4, 8), start=1):
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=300)
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(delay)
    if not rows or len(rows) < 120:
        raise RuntimeError(f"Insufficient 1H candles for {base}")
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    if frame["timestamp"].diff().dropna().le(0).any():
        raise RuntimeError(f"Invalid timestamp sequence for {base}")
    return symbol, add_indicators(frame)


def build_message(symbol: str, side: str, signal_row, entry_row) -> str:
    atr = float(signal_row.atr_risk)
    raw_entry = float(entry_row.open)
    entry = raw_entry * (1 + SLIP if side == "LONG" else 1 - SLIP)
    stop_dist = atr * ATR_SL_MULTIPLIER
    target_dist = atr * ATR_TP_MULTIPLIER
    stop = entry - stop_dist if side == "LONG" else entry + stop_dist
    target = entry + target_dist if side == "LONG" else entry - target_dist
    rr = ATR_TP_MULTIPLIER / ATR_SL_MULTIPLIER
    return (
        "📡 ST-ADX V3.3 — MANUAL SIGNAL\n"
        f"{symbol} | {side} | BingX USDT Perpetual | 1H\n"
        f"Waktu candle sinyal: {utc(int(signal_row.timestamp))}\n"
        f"Entry referensi: {entry:.8g}\n"
        f"Stop Loss: {stop:.8g} ({ATR_SL_MULTIPLIER:.1f} ATR10)\n"
        f"Take Profit final: {target:.8g} ({ATR_TP_MULTIPLIER:.1f} ATR10)\n"
        f"Risk:Reward: 1:{rr:.2f}\n"
        f"ADX: {float(signal_row.adx):.2f} | RSI: {float(signal_row.rsi):.2f} | ATR10: {atr:.8g}\n"
        "Aturan: TP tunggal; tanpa TP1/TP2, partial close, BEP, atau trailing stop.\n"
        "MANUAL EXECUTION ONLY — NO ORDER SENT. Entry adalah harga referensi, bukan fill terjamin."
    )


def main() -> int:
    exchange = ccxt.bingx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "swap", "defaultSubType": "linear"},
    })
    exchange.load_markets()
    try:
        now = int(exchange.fetch_time() or exchange.milliseconds())
    except Exception:
        now = int(exchange.milliseconds())

    state = load_state()
    frames = {}
    symbols = {}
    for base in ASSETS:
        symbols[base], frames[base] = fetch_frame(exchange, base)
        time.sleep(0.15)

    closed_rows = {}
    for base, frame in frames.items():
        closed = frame[frame.timestamp + BAR_MS <= now]
        if closed.empty:
            raise RuntimeError(f"No confirmed closed 1H candle for {base}")
        closed_rows[base] = closed

    if not state.get("initialized"):
        state["last_signal_ts"] = {
            base: int(rows.iloc[-1].timestamp) for base, rows in closed_rows.items()
        }
        state["initialized"] = True
        save_state(state)
        log.info("INITIALIZED: historical signals skipped; waiting for next confirmed 1H candle")
        return 0

    sent = 0
    errors = []
    for base in ASSETS:
        rows = closed_rows[base]
        signal_row = rows.iloc[-1]
        signal_ts = int(signal_row.timestamp)
        last_ts = int(state["last_signal_ts"].get(base, -1))
        if signal_ts <= last_ts:
            continue
        # Advance the cursor only after the confirmed candle has been evaluated.
        state["last_signal_ts"][base] = signal_ts
        age_ms = now - (signal_ts + BAR_MS)
        if age_ms < 0 or age_ms > GRACE_MS:
            log.info("%s skipped: confirmed candle age %ss exceeds alert grace", base, round(age_ms / 1000))
            continue

        side = None
        if base in LONG_ALLOWLIST and bool(signal_row.long_signal):
            side = "LONG"
        elif base in SHORT_ALLOWLIST and bool(signal_row.short_signal):
            side = "SHORT"
        if side is None:
            continue

        entry_rows = frames[base][frames[base].timestamp == signal_ts + BAR_MS]
        if entry_rows.empty:
            log.warning("%s %s signal has no next-candle open; skipped", base, side)
            continue
        message = build_message(symbols[base], side, signal_row, entry_rows.iloc[0])
        try:
            telegram(message)
            sent += 1
            log.info("TELEGRAM SENT: %s %s at signal candle %s", base, side, utc(signal_ts))
        except Exception as exc:
            errors.append(f"{base} {side}: {exc}")
            # Roll back this symbol cursor so a later invocation can retry.
            state["last_signal_ts"][base] = last_ts
            log.exception("Telegram delivery failed for %s %s", base, side)

    save_state(state)
    log.info("SCAN COMPLETE | signals_sent=%d | delivery_errors=%d", sent, len(errors))
    if errors:
        raise RuntimeError("Telegram delivery errors: " + "; ".join(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
