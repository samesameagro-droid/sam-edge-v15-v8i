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
MIN_QUOTE_VOLUME = float(os.getenv("LT_MIN_QUOTE_VOLUME", "500000"))
MAX_SYMBOLS = int(os.getenv("LT_MAX_SYMBOLS", "250"))
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
    take_profit: float  # TP3 / maximum chart-based target
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    tp_allocations_pct: str
    setup_score: int
    sideways_bars: int
    breakout_volume_ratio: float
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

    # Spot LONG only. Weekly bearish regime is excluded.
    if wtrend == "BEARISH":
        return None

    # Detect compression/sideways before a possible expansion candle.
    box = daily.iloc[-16:-1]
    range_high = float(daily.high.iloc[-21:-1].max())
    candle_range = max(float(x.high - x.low), 1e-12)
    body_ratio = abs(float(x.close - x.open)) / candle_range
    close_location = (float(x.close) - float(x.low)) / candle_range
    sideways_bars = 0
    for n in range(15, 7, -1):
        sample = daily.iloc[-(n + 1):-1]
        if len(sample) == n and (float(sample.high.max()) - float(sample.low.min())) / atr <= 4.5:
            sideways_bars = n
            break

    vol_ratio = float(x.volume / x.vol_sma20) if x.vol_sma20 and x.vol_sma20 > 0 else 0.0
    god_candle = (
        float(x.close) > range_high
        and body_ratio >= 0.60
        and close_location >= 0.75
        and vol_ratio >= 1.8
        and candle_range >= 1.5 * atr
        and sideways_bars >= 8
    )
    # Don't chase a vertical candle: if extended >1.25 ATR above breakout level, wait for retest.
    extended_breakout = float(x.close) - range_high > 1.25 * atr
    bullish_candle = x.close > x.open and x.close > prev.close
    near_support = float(x.low) <= support + max(0.8 * atr, support * 0.025)
    pullback_ok = near_support and bullish_candle and 35 <= rsi <= 68
    breakout_ok = price > range_high and vol_ratio >= 1.15 and rsi < 82

    if god_candle:
        setup = "GOD_CANDLE_BREAKOUT_RETEST" if extended_breakout else "GOD_CANDLE_BREAKOUT"
        entry_low, entry_high = range_high * 0.99, range_high * 1.015
        status = "WAIT_RETEST"
        rationale = (
            f"Sideways {sideways_bars} candle, breakout body {body_ratio:.0%}, "
            f"volume {vol_ratio:.2f}x SMA20, candle range {candle_range / atr:.2f} ATR. "
            "Wait for retest if price is extended; do not chase."
        )
        if not extended_breakout and price <= entry_high:
            status = "PAPER_CANDIDATE"
    elif pullback_ok:
        setup = "SUPPORT_PULLBACK"
        entry_low, entry_high = price * 0.995, price * 1.005
        status = "PAPER_CANDIDATE"
        rationale = "Price tests confirmed support with a bullish daily candle; RSI is not overheated."
    elif breakout_ok and dtrend == "BULLISH":
        setup = "BREAKOUT_RETEST_WATCH"
        entry_low, entry_high = range_high * 0.99, range_high * 1.015
        status = "WAIT_RETEST"
        rationale = "20-day range breakout with volume confirmation; wait for retest, do not chase."
    else:
        return None

    # Structure-based SL below the nearest relevant swing/support with ATR buffer.
    stop = min(support, float(d.low.tail(10).min())) - ATR_BUFFER * atr
    if stop <= 0 or stop >= entry_low:
        return None

    # Derive three targets from confirmed swing highs / visible historical highs.
    _, swing_highs = swing_levels(daily, left_right=3)
    levels = sorted({float(v) for _, v in swing_highs if float(v) > entry_high * 1.01})
    for lookback in (60, 120, 200):
        level = float(d.high.tail(min(lookback, len(d))).max())
        if level > entry_high * 1.01 and not any(abs(level - old) / level < 0.008 for old in levels):
            levels.append(level)
    levels = sorted(set(levels))
    if len(levels) < 3:
        return None
    # Select chart levels distributed across available overhead resistance, not fixed R multiples.
    indexes = sorted(set([0, len(levels) // 2, len(levels) - 1]))
    targets = [levels[i] for i in indexes]
    if len(targets) < 3:
        targets = levels[:3]
    tp1, tp2, tp3 = targets[0], targets[1], targets[2]
    risk = entry_high - stop
    rr = (tp3 - entry_high) / risk if risk > 0 else 0.0
    # RR is a quality gate after chart-derived target selection.
    if rr < MIN_RR:
        return None

    score = 0
    score += 20 if wtrend == "BULLISH" else 8
    score += 15 if dtrend == "BULLISH" else 6
    score += 20 if god_candle else 0
    score += 15 if vol_ratio >= 1.8 else (8 if vol_ratio >= 1.2 else 0)
    score += 10 if near_support else 0
    score += 10 if 40 <= rsi <= 65 else 4
    score += 10 if rr >= 3 else (6 if rr >= 2 else 0)

    return Candidate(
        symbol=symbol, setup=setup, status=status, price=price,
        entry_low=round(entry_low, 10), entry_high=round(entry_high, 10),
        stop_loss=round(stop, 10), take_profit=round(tp3, 10),
        take_profit_1=round(tp1, 10), take_profit_2=round(tp2, 10),
        take_profit_3=round(tp3, 10), tp_allocations_pct="30/30/40",
        setup_score=min(score, 100), sideways_bars=sideways_bars,
        breakout_volume_ratio=round(vol_ratio, 2),
        risk_pct=round(100 * (entry_high - stop) / entry_high, 2),
        reward_pct=round(100 * (tp3 - entry_high) / entry_high, 2),
        rr=round(rr, 2), daily_trend=dtrend, weekly_trend=wtrend,
        support=round(support, 10), resistance=round(resistance, 10),
        atr_pct=round(atr_pct, 2), quote_volume_24h=round(quote_volume, 2),
        rationale=rationale,
        invalidation=f"Daily close below structural SL {stop:.8g}; re-evaluate structure.",
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
    print("Setups: support pullback, breakout/retest, sideways-to-GOD-CANDLE. TP1/TP2/TP3 allocation 30/30/40.")

    candidates = []
    for symbol, qv in universe:
        c = analyse_symbol(exchange, symbol, qv)
        if c:
            candidates.append(asdict(c))
            print(f"[{c.status}] {c.symbol} score={c.setup_score} setup={c.setup} entry={c.entry_low}-{c.entry_high} SL={c.stop_loss} TP1={c.take_profit_1} TP2={c.take_profit_2} TP3={c.take_profit_3} alloc={c.tp_allocations_pct} RR={c.rr} vol={c.breakout_volume_ratio}x")

    candidates.sort(key=lambda c: (c["setup_score"], c["quote_volume_24h"], c["rr"]), reverse=True)
    candidates = candidates[:10]
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
