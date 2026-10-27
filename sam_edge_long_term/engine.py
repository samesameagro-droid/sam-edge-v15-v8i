"""
SAM EDGE LONG TERM — Spot / Daily chart-based engine.
Standalone module: does not import or modify the existing SAM EDGE V15 engine.
Default mode is ANALYSIS/PAPER ONLY; it never places live orders.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

EXCHANGE_ID = os.getenv("LT_EXCHANGE", "bingx")
QUOTE = os.getenv("LT_QUOTE", "USDT")
MIN_QUOTE_VOLUME = float(os.getenv("LT_MIN_QUOTE_VOLUME", "1000000"))
MAX_SYMBOLS = int(os.getenv("LT_MAX_SYMBOLS", "80"))
DAILY_BARS = int(os.getenv("LT_DAILY_BARS", "300"))
WEEKLY_BARS = int(os.getenv("LT_WEEKLY_BARS", "120"))
MIN_RR = float(os.getenv("LT_MIN_RR", "2.0"))
RISK_FRACTION = float(os.getenv("LT_RISK_FRACTION", "0.01"))
ATR_BUFFER = float(os.getenv("LT_ATR_BUFFER", "0.35"))
PAPER_ONLY = True  # Intentional safety lock. This engine does not submit orders.
OUT_DIR = Path(os.getenv("LT_OUTPUT_DIR", "data_long_term"))
OUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Candidate:
    symbol: str
    setup: str
    status: str
    price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit: float
    risk_pct: float
    reward_pct: float
    rr: float
    daily_trend: str
    weekly_trend: str
    support: float
    resistance: float
    atr_pct: float
    quote_volume_24h: float
    rationale: str
    invalidation: str
    timestamp_utc: str


def make_exchange():
    exchange_cls = getattr(ccxt, EXCHANGE_ID)
    exchange = exchange_cls({"enableRateLimit": True, "timeout": 20000})
    # Spot symbols only. Do not enable swap/defaultType futures here.
    return exchange


def ohlcv_df(exchange, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not raw or len(raw) < 60:
            return None
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        # Exclude the currently-forming candle to avoid look-ahead/repainting.
        return df.iloc[:-1].reset_index(drop=True)
    except Exception as exc:
        print(f"[WARN] OHLCV {symbol} {timeframe}: {exc}")
        return None


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema20"] = d.close.ewm(span=20, adjust=False).mean()
    d["ema50"] = d.close.ewm(span=50, adjust=False).mean()
    d["ema200"] = d.close.ewm(span=200, adjust=False).mean()
    prev_close = d.close.shift(1)
    tr = pd.concat([
        d.high - d.low,
        (d.high - prev_close).abs(),
        (d.low - prev_close).abs(),
    ], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["vol_sma20"] = d.volume.rolling(20).mean()
    delta = d.close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))
    return d


def trend_label(df: pd.DataFrame) -> str:
    d = indicators(df).dropna(subset=["ema20", "ema50"])
    if len(d) < 3:
        return "UNKNOWN"
    x = d.iloc[-1]
    if x.close > x.ema20 > x.ema50 and x.ema20 >= d.iloc[-2].ema20:
        return "BULLISH"
    if x.close < x.ema20 and x.ema20 < x.ema50:
        return "BEARISH"
    return "NEUTRAL"


def swing_levels(df: pd.DataFrame, left_right: int = 3):
    """Confirmed pivot levels only; pivots need right-side candles to be confirmed."""
    d = df.reset_index(drop=True)
    lows, highs = [], []
    for i in range(left_right, len(d) - left_right):
        lo = float(d.low.iloc[i])
        hi = float(d.high.iloc[i])
        if lo <= float(d.low.iloc[i-left_right:i+left_right+1].min()):
            lows.append((i, lo))
        if hi >= float(d.high.iloc[i-left_right:i+left_right+1].max()):
            highs.append((i, hi))
    return lows, highs


def nearest_levels(df: pd.DataFrame, price: float):
    lows, highs = swing_levels(df)
    supports = [v for _, v in lows if v < price]
    resistances = [v for _, v in highs if v > price]
    support = max(supports) if supports else float(df.low.tail(20).min())
    resistance = min(resistances) if resistances else float(df.high.tail(60).max())
    return support, resistance


def analyse_symbol(exchange, symbol: str, quote_volume: float) -> Optional[Candidate]:
    daily = ohlcv_df(exchange, symbol, "1d", DAILY_BARS)
    weekly = ohlcv_df(exchange, symbol, "1w", WEEKLY_BARS)
    if daily is None or weekly is None:
        return None
    d = indicators(daily).dropna().reset_index(drop=True)
    if len(d) < 50:
        return None
    wtrend = trend_label(weekly)
    dtrend = trend_label(daily)
    x = d.iloc[-1]
    prev = d.iloc[-2]
    price, atr = float(x.close), float(x.atr)
    if not np.isfinite(atr) or atr <= 0 or price <= 0:
        return None

    support, resistance = nearest_levels(daily, price)
    atr_pct = 100 * atr / price
    vol_ratio = float(x.volume / x.vol_sma20) if x.vol_sma20 and x.vol_sma20 > 0 else 0
    rsi = float(x.rsi) if np.isfinite(x.rsi) else 50.0

    # Spot LONG only. Require weekly trend not bearish; avoid buying stretched candles.
    if wtrend == "BEARISH":
        return None

    # Setup 1: bullish pullback near confirmed support with a bullish daily candle.
    near_support = price <= support + max(0.8 * atr, support * 0.025)
    bullish_candle = x.close > x.open and x.close > prev.close
    pullback_ok = near_support and bullish_candle and rsi >= 35 and rsi <= 68
    # Setup 2: close above a recent range high, volume expansion; next candle validation is still needed.
    range_high = float(daily.high.iloc[-21:-1].max())
    breakout_ok = price > range_high and vol_ratio >= 1.15 and rsi < 75

    if pullback_ok:
        setup = "SUPPORT_PULLBACK"
        entry_low, entry_high = price * 0.995, price * 1.005
        rationale = "Harga dekat support terkonfirmasi, candle harian bullish, RSI tidak terlalu panas."
    elif breakout_ok and dtrend == "BULLISH":
        setup = "BREAKOUT_RETEST_WATCH"
        # Do not chase: a breakout is a watchlist setup until retest/confirmation.
        entry_low, entry_high = range_high * 0.985, range_high * 1.005
        if price > entry_high:
            return None
        rationale = "Breakout range 20 hari dengan dukungan volume; tunggu retest, jangan mengejar harga."
    else:
        return None

    # Structure-based SL below the nearest relevant swing/support with ATR buffer.
    stop = min(support, float(d.low.tail(10).min())) - ATR_BUFFER * atr
    if stop <= 0 or stop >= entry_low:
        return None

    # Chart-based target: nearest meaningful resistance. If no nearby confirmed resistance,
    # use the visible 60-bar high as a fallback only when above entry.
    target = resistance
    if target <= entry_high:
        target = float(d.high.tail(60).max())
    if target <= entry_high:
        return None

    risk = entry_high - stop
    reward = target - entry_high
    rr = reward / risk if risk > 0 else 0
    # RR is a quality filter, not the method for choosing the target.
    if rr < MIN_RR:
        return None

    status = "PAPER_CANDIDATE" if setup == "SUPPORT_PULLBACK" else "WAIT_RETEST"
    return Candidate(
        symbol=symbol, setup=setup, status=status, price=price,
        entry_low=round(entry_low, 10), entry_high=round(entry_high, 10),
        stop_loss=round(stop, 10), take_profit=round(target, 10),
        risk_pct=round(100 * (entry_high - stop) / entry_high, 2),
        reward_pct=round(100 * (target - entry_high) / entry_high, 2),
        rr=round(rr, 2), daily_trend=dtrend, weekly_trend=wtrend,
        support=round(support, 10), resistance=round(resistance, 10),
        atr_pct=round(atr_pct, 2), quote_volume_24h=round(quote_volume, 2),
        rationale=rationale,
        invalidation=f"Daily close below support/SL zone {stop:.8g}; re-evaluate structure.",
        timestamp_utc=datetime.now(timezone.utc).isoformat()
    )


def main():
    parser = argparse.ArgumentParser(description="SAM EDGE LONG TERM — Spot Daily paper scanner")
    parser.add_argument("--limit", type=int, default=MAX_SYMBOLS, help="Maximum liquid spot symbols to scan")
    args = parser.parse_args()

    exchange = make_exchange()
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()
    universe = []
    for symbol, market in markets.items():
        if not market.get("spot") or market.get("active") is False:
            continue
        if not symbol.endswith("/" + QUOTE) or market.get("base") in {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"}:
            continue
        ticker = tickers.get(symbol) or {}
        qv = ticker.get("quoteVolume")
        if qv is None:
            continue
        try:
            qv = float(qv)
        except (TypeError, ValueError):
            continue
        if qv >= MIN_QUOTE_VOLUME:
            universe.append((symbol, qv))
    universe.sort(key=lambda item: item[1], reverse=True)
    universe = universe[:max(1, args.limit)]
    print(f"ENGINE=SAM_EDGE_LONG_TERM | EXCHANGE={EXCHANGE_ID} | MARKET=SPOT | TF=1D | PAPER_ONLY={PAPER_ONLY}")
    print(f"Eligible spot symbols: {len(universe)} (min 24h quote volume {MIN_QUOTE_VOLUME:,.0f} {QUOTE})")

    candidates = []
    for symbol, qv in universe:
        c = analyse_symbol(exchange, symbol, qv)
        if c:
            candidates.append(asdict(c))
            print(f"[{c.status}] {c.symbol} setup={c.setup} entry={c.entry_low}-{c.entry_high} SL={c.stop_loss} TP={c.take_profit} RR={c.rr}")

    candidates.sort(key=lambda c: (c["rr"], c["quote_volume_24h"]), reverse=True)
    candidates = candidates[:3]
    payload = {
        "engine": "SAM_EDGE_LONG_TERM",
        "mode": "PAPER_ONLY",
        "market": "SPOT",
        "timeframe": "1D",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "count": len(candidates),
        "candidates": candidates,
        "warning": "Signals only. No orders are submitted. Validate with historical and forward tests before live use."
    }
    path = OUT_DIR / "latest_candidates.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {path}. Candidates={len(candidates)}")


if __name__ == "__main__":
    main()
