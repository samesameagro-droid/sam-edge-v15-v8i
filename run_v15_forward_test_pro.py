from __future__ import annotations

import os
import tempfile
from pathlib import Path
from dataclasses import asdict

import ccxt
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Use the same direct BingX public-data adapter as the official V15 launcher.
from bingx_direct_v15 import fetch_markets, fetch_tickers, fetch_ohlcv
ccxt.bingx.fetch_markets = fetch_markets
ccxt.bingx.fetch_tickers = fetch_tickers
ccxt.bingx.fetch_ohlcv = fetch_ohlcv

import notifiers
from run_v15_forward_test import ForwardPaperEngine, TARGET_TRADES

CHART_CANDLES = int(os.getenv("TELEGRAM_CHART_CANDLES", "80"))
CHART_FILE = Path(tempfile.gettempdir()) / "sam_edge_v15_signal_chart.png"
MIGRATION_FLAG = Path("v15_telegram_professional_migration.done")


def _fetch_df_full_history(self, symbol, timeframe="15m", limit=3600):
    rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").tail(limit).reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


ForwardPaperEngine.fetch_df = _fetch_df_full_history


def _fmt(x):
    try:
        return f"{float(x):.8g}"
    except Exception:
        return "-"


def _signal_key(p) -> str:
    return f"{p['coin']}|{p['side']}|{p.get('opened_at', '')}|{p.get('core', 'V15_ADX4H_CANDLE2H')}"


def _send_photo(photo_path: Path, caption: str, retries: int = 3, signal_key: str | None = None) -> bool:
    """
    At-most-once Telegram photo delivery.

    A timeout/5xx after Telegram accepted the upload is ambiguous. Never retry
    an ambiguous send and never fall back to a second text message for it.
    """
    token, chat_id = notifiers._telegram_config()
    if not token or not chat_id:
        print("TELEGRAM SKIP | token/chat_id not configured")
        return False
    if signal_key and signal_key in notifiers._delivery_history():
        print(f"TELEGRAM DEDUP | chart already delivered | key={signal_key}")
        notifiers._remove_pending(caption, signal_key)
        return True

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with photo_path.open("rb") as fh:
            r = notifiers.requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": (photo_path.name, fh, "image/png")},
                timeout=45,
            )
        if r.ok and r.json().get("ok"):
            message_id = r.json().get('result', {}).get('message_id')
            print(f"TELEGRAM CHART SENT OK | message_id={message_id}")
            if signal_key:
                notifiers._mark_delivered(signal_key)
                notifiers._remove_pending(caption, signal_key)
            return True

        if r.status_code == 429:
            print(f"TELEGRAM PHOTO RATE LIMITED | NOT SENT | {r.text}")
            return False

        if 400 <= r.status_code < 500:
            print(f"TELEGRAM PHOTO HTTP {r.status_code} | definite rejection | {r.text}")
            return False

        # 5xx is ambiguous; suppress fallback/retry so one signal cannot become
        # two Telegram messages if Telegram accepted the upload before failing.
        print(f"TELEGRAM PHOTO HTTP {r.status_code} | AMBIGUOUS DELIVERY — NOT RETRIED")
        if signal_key:
            notifiers._mark_delivered(signal_key)
            notifiers._remove_pending(caption, signal_key)
        return True

    except Exception as e:
        # Timeout/connection reset can happen after Telegram has accepted the
        # upload. Treat it as delivered for dedup purposes and do not retry.
        print(f"TELEGRAM PHOTO AMBIGUOUS TRANSPORT ERROR — NOT RETRIED | {type(e).__name__}: {e}")
        if signal_key:
            notifiers._mark_delivered(signal_key)
            notifiers._remove_pending(caption, signal_key)
        return True

def _make_chart(p) -> Path | None:
    try:
        rows = p.get("_ohlcv")
        if rows is None:
            ex = ccxt.bingx({"enableRateLimit": True, "timeout": 15000, "options": {"defaultType": "swap"}})
            rows = ex.fetch_ohlcv(p["coin"], timeframe="15m", limit=CHART_CANDLES)
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Asia/Jakarta")
        df["ema20"] = df.close.ewm(span=20, adjust=False).mean()
        df["ema50"] = df.close.ewm(span=50, adjust=False).mean()

        fig, (ax, av) = plt.subplots(2, 1, figsize=(12, 7.2), sharex=True, gridspec_kw={"height_ratios": [4, 1]})
        fig.patch.set_facecolor("#08111b")
        ax.set_facecolor("#08111b")
        av.set_facecolor("#08111b")
        x = np.arange(len(df))
        width = 0.58
        for i, row in df.iterrows():
            up = row.close >= row.open
            body_low = min(row.open, row.close)
            body_h = max(abs(row.close - row.open), max(row.close, row.open) * 0.00001)
            c = "#49d6a5" if up else "#ff5c6c"
            ax.vlines(i, row.low, row.high, linewidth=0.8, color=c)
            ax.add_patch(plt.Rectangle((i - width / 2, body_low), width, body_h, facecolor=c, edgecolor=c, linewidth=0.5))

        ax.plot(x, df.ema20, linewidth=1.5, label="EMA20", color="#28b8ff")
        ax.plot(x, df.ema50, linewidth=1.5, label="EMA50", color="#ffc52b")
        entry, sl, tp = float(p["entry"]), float(p["sl"]), float(p["tp"])
        ax.axhline(entry, linewidth=1.6, linestyle="--", color="#ffd21f", label=f"ENTRY {_fmt(entry)}")
        ax.axhline(sl, linewidth=1.6, linestyle="--", color="#ff4d5e", label=f"SL {_fmt(sl)}")
        ax.axhline(tp, linewidth=1.6, linestyle="--", color="#19e6a2", label=f"TP {_fmt(tp)}")

        status = p.get("telegram_status", "EXECUTED")
        ax.set_title(f"SAM EDGE V15  |  {p['coin']}  |  15m  |  {p['side']}  |  {status}", color="white", fontsize=14, loc="left", pad=12, fontweight="bold")
        ax.text(0.99, 0.98, f"ENTRY  {_fmt(entry)}\nSL      {_fmt(sl)}\nTP      {_fmt(tp)}\nRR      1.25R\nSCORE   {float(p.get('selection_score', 0)):.2f}", transform=ax.transAxes, ha="right", va="top", color="white", fontsize=10, bbox=dict(boxstyle="round,pad=0.5", facecolor="#0d1c2a", edgecolor="#2d5575", alpha=0.95))
        ax.grid(True, alpha=0.15)
        ax.tick_params(colors="#b8c7d6")
        for spine in ax.spines.values(): spine.set_alpha(0.2)
        ax.legend(loc="lower left", ncol=3, fontsize=8, framealpha=0.15)

        av.bar(x, df.volume, width=0.7, alpha=0.65, color=["#49d6a5" if c >= o else "#ff5c6c" for c, o in zip(df.close, df.open)])
        av.set_ylabel("VOL", color="#b8c7d6", fontsize=8)
        av.tick_params(colors="#b8c7d6")
        av.grid(True, alpha=0.1)
        step = max(1, len(df) // 8)
        ticks = list(range(0, len(df), step))
        av.set_xticks(ticks)
        av.set_xticklabels([df.time.iloc[i].strftime("%d %b %H:%M") for i in ticks], fontsize=8)
        fig.text(0.01, 0.015, "BingX Perpetual  •  Paper Trading  •  SAM EDGE V15", color="#7f9bb3", fontsize=8)
        plt.tight_layout(rect=(0, 0.025, 1, 1))
        fig.savefig(CHART_FILE, dpi=160, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        return CHART_FILE
    except Exception as e:
        print(f"CHART GENERATION ERROR | {p.get('coin')}: {type(e).__name__}: {e}")
        return None


def _recent_signal_duplicate(signal_key: str, cooldown_minutes: int = 60) -> bool:
    """Block repeated Telegram entries for the same coin/side/core within a short window."""
    try:
        from datetime import datetime, timezone
        parts = signal_key.split("|")
        if len(parts) < 4:
            return False
        prefix = "|".join(parts[:2]) + "|"
        signal_ts = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
        if signal_ts.tzinfo is None:
            signal_ts = signal_ts.replace(tzinfo=timezone.utc)
        history = notifiers._delivery_history()
        for h in history:
            hs = str(h)
            # Only ENTRY identities are considered here. RESULT keys contain
            # an extra RESULT segment and must not suppress a new entry.
            hp = hs.split("|")
            if len(hp) != 4 or "|".join(hp[:2]) + "|" != prefix:
                continue
            if hp[3] != parts[3]:
                continue
            try:
                old_ts = datetime.fromisoformat(hp[2].replace("Z", "+00:00"))
                if old_ts.tzinfo is None:
                    old_ts = old_ts.replace(tzinfo=timezone.utc)
                age = (signal_ts - old_ts).total_seconds() / 60.0
                if 0 <= age < cooldown_minutes:
                    print(f"TELEGRAM SIGNAL DEDUP | recent same setup | key={signal_key} | prior={hs} | age={age:.1f}m")
                    return True
            except ValueError:
                continue
    except Exception as e:
        print(f"TELEGRAM SIGNAL DEDUP CHECK ERROR | {type(e).__name__}: {e}")
    return False


def professional_send_signal(p, equity):
    side = p["side"]
    icon = "🟢" if side == "LONG" else "🔴"
    status = p.get("telegram_status", "EXECUTED")
    status_icon = "⚡" if status == "EXECUTED" else "🟡"
    signal_key = _signal_key(p)
    if signal_key in notifiers._delivery_history() or _recent_signal_duplicate(signal_key):
        print(f"TELEGRAM SIGNAL DEDUP | {signal_key}")
        return True
    risk_pct = float(p.get("risk_cash", 0)) / float(equity) * 100 if equity else 0.0
    caption = (
        f"<b>🏆 SAM EDGE V15 | NEW SIGNAL</b>\n"
        f"{icon} <b>{p['coin']} · {side}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{status_icon} <b>STATUS:</b> {status}\n"
        f"<b>CORE:</b> {p.get('core', 'V15_ADX4H_CANDLE2H')}\n"
        f"<b>TIMEFRAME:</b> 15M\n\n"
        f"<b>TRADE PLAN</b>\n"
        f"ENTRY  <code>{_fmt(p['entry'])}</code>\n"
        f"SL     <code>{_fmt(p['sl'])}</code>\n"
        f"TP     <code>{_fmt(p['tp'])}</code>\n"
        f"RR     <b>1.25R</b>\n"
        f"SCORE  <b>{float(p.get('selection_score', 0)):.2f}</b>\n"
        f"RISK   <b>{risk_pct:.2f}%</b>\n\n"
        f"<i>Live BingX 15M chart attached. Paper trading only.</i>"
    )
    chart = _make_chart(p)
    if chart and _send_photo(chart, caption, signal_key=signal_key):
        return True
    return notifiers._send_text(
        "🏆 SAM EDGE V15 | NEW SIGNAL\n"
        f"{p['coin']} | {side}\nENTRY: {_fmt(p['entry'])}\nSL: {_fmt(p['sl'])}\nTP: {_fmt(p['tp'])}\n"
        f"RR: 1.25R | SCORE: {float(p.get('selection_score', 0)):.2f}\nSTATUS: {status}\nMODE: PAPER ONLY",
        signal_key=signal_key,
    )


def _dedupe_forward_state(engine):
    """Remove accidental duplicate closed records using the V15 trade identity."""
    from datetime import datetime

    def valid_closed(r, signal_field):
        try:
            signal = datetime.fromisoformat(str(r.get(signal_field, '')).replace('Z', '+00:00'))
            closed = datetime.fromisoformat(str(r.get('closed_at', '')).replace('Z', '+00:00'))
            return closed >= signal and str(r.get('result', '')).upper().strip() in {'TP', 'SL'}
        except Exception:
            return False

    # Remove duplicates and chronologically impossible closed trades.
    seen = set()
    deduped = []
    invalid_closed = []
    for r in engine.closed:
        key = f"{r.get('coin','')}|{r.get('side','')}|{r.get('opened_at','')}|{r.get('core','')}"
        if key in seen:
            continue
        seen.add(key)
        if not valid_closed(r, 'opened_at'):
            invalid_closed.append(key)
            continue
        deduped.append(r)
    if invalid_closed or len(deduped) != len(engine.closed):
        print(f"FORWARD STATE SANITIZE | removed_duplicates={len(engine.closed)-len(deduped)-len(invalid_closed)} | quarantined_invalid={len(invalid_closed)}")
        engine.closed = deduped

    seen_master = set()
    master = []
    invalid_master = 0
    for r in engine.master_rows:
        key = r.get('trade_key') or f"{r.get('coin','')}|{r.get('side','')}|{r.get('signal_time','')}|{r.get('core','')}"
        if key in seen_master:
            continue
        seen_master.add(key)
        if not valid_closed(r, 'signal_time'):
            invalid_master += 1
            continue
        master.append(r)
    if invalid_master or len(master) != len(engine.master_rows):
        print(f"MASTER JOURNAL SANITIZE | removed_duplicates={len(engine.master_rows)-len(master)-invalid_master} | quarantined_invalid={invalid_master}")
        engine.master_rows = master

    # Rebuild equity only from valid closed trades so invalid historical SLs
    # cannot permanently distort equity/PF/DD.
    try:
        equity = float(os.getenv('START_EQUITY', '100'))
        risk_pct = float(os.getenv('RISK_PCT', '0.005'))
        valid_rows = sorted(engine.closed, key=lambda r: str(r.get('closed_at', '')))
        for r in valid_rows:
            equity += equity * risk_pct * float(r.get('R', 0) or 0)
        engine.equity = equity
        print(f"FORWARD EQUITY REBUILT | valid_closed={len(valid_rows)} | equity=${equity:.2f}")
    except Exception as e:
        print(f"FORWARD EQUITY REBUILD ERROR | {type(e).__name__}: {e}")


def _reconcile_master_from_state(engine):
    """Authoritatively rebuild master artifacts from the persisted paper closed history."""
    valid = []
    seen = set()
    for r in engine.closed:
        try:
            key = f"{r.get('coin','')}|{r.get('side','')}|{r.get('opened_at','')}|{r.get('core','')}"
            if key in seen:
                continue
            if str(r.get('result','')).upper().strip() not in {'TP','SL'}:
                continue
            if not engine._valid_closed_record({**r, 'signal_time': r.get('opened_at','')}):
                continue
            seen.add(key)
            valid.append(r)
        except Exception:
            continue
    valid.sort(key=lambda r: str(r.get('closed_at') or r.get('opened_at') or ''))
    engine.master_rows = []
    for r in valid:
        engine._append_closed_record(r, len(engine.master_rows) + 1)
    engine._refresh_summary_fields()
    if engine.master_rows:
        engine._write_master()
    engine._write_summary()
    print(f"MASTER RECONCILED FROM PAPER STATE | closed={len(engine.master_rows)} | active={len(engine.positions)}")


def resend_active_professional(engine):
    """One-time migration: upgrade already-active legacy Telegram signals to chart cards."""
    if MIGRATION_FLAG.exists():
        return
    for _, pos in engine.positions.items():
        p = asdict(pos)
        p["selection_score"] = 0.0
        p["telegram_status"] = "EXECUTED · ACTIVE"
        caption = (
            f"<b>🏆 SAM EDGE V15 | ACTIVE TRADE</b>\n"
            f"{'🟢' if p['side'] == 'LONG' else '🔴'} <b>{p['coin']} · {p['side']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>STATUS:</b> ACTIVE / PAPER ONLY\n"
            f"<b>CORE:</b> {p.get('core', 'V15_ADX4H_CANDLE2H')}\n"
            f"<b>TIMEFRAME:</b> 15M\n\n"
            f"<b>TRADE PLAN</b>\n"
            f"ENTRY  <code>{_fmt(p['entry'])}</code>\n"
            f"SL     <code>{_fmt(p['sl'])}</code>\n"
            f"TP     <code>{_fmt(p['tp'])}</code>\n"
            f"RR     <b>1.25R</b>\n\n"
            f"<i>Live BingX 15M chart. Trade remains active until TP or SL is confirmed.</i>"
        )
        chart = _make_chart(p)
        if chart and _send_photo(chart, caption, signal_key=_signal_key(p)):
            print(f"ACTIVE PROFESSIONAL CHART SENT | {p['coin']} | {p['side']}")
    MIGRATION_FLAG.write_text("professional Telegram migration completed\n", encoding="utf-8")


# Patch the function imported dynamically by main_paper_v15.scan_once().
notifiers.send_signal = professional_send_signal


if __name__ == "__main__":
    print(f"SAM EDGE V15 PROFESSIONAL FORWARD TEST | TARGET={TARGET_TRADES}")
    engine = ForwardPaperEngine()
    _dedupe_forward_state(engine)
    _reconcile_master_from_state(engine)
    one_shot = os.getenv("RUN_ONCE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if one_shot:
        delivered = notifiers.retry_pending_messages()
        for key in delivered:
            engine.signal_history.add(key)
        if delivered:
            print(f"TELEGRAM PENDING RETRY | delivered={len(delivered)} keyed signals")
        resend_active_professional(engine)
        engine.scan_once()
    else:
        engine.run()
