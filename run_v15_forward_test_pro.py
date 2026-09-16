from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ccxt
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import notifiers
from run_v15_forward_test import ForwardPaperEngine, TARGET_TRADES

# Professional Telegram layer for the same V15 forward-test engine.
# The trading logic is unchanged. Only the Telegram presentation is upgraded:
# a real BingX 15m chart is generated from live OHLCV and sent as a photo,
# with Entry / SL / TP / score / risk / status in the caption.

CHART_CANDLES = int(os.getenv("TELEGRAM_CHART_CANDLES", "80"))
CHART_FILE = Path(tempfile.gettempdir()) / "sam_edge_v15_signal_chart.png"


def _fmt(x):
    try:
        return f"{float(x):.8g}"
    except Exception:
        return "-"


def _send_photo(photo_path: Path, caption: str, retries: int = 3) -> bool:
    token, chat_id = notifiers._telegram_config()
    if not token or not chat_id:
        print("TELEGRAM SKIP | token/chat_id not configured")
        notifiers._queue_pending(caption)
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    for attempt in range(1, retries + 1):
        try:
            with photo_path.open("rb") as fh:
                r = notifiers.requests.post(
                    url,
                    data={
                        "chat_id": chat_id,
                        "caption": caption,
                        "parse_mode": "HTML",
                    },
                    files={"photo": (photo_path.name, fh, "image/png")},
                    timeout=45,
                )
            if r.ok and r.json().get("ok"):
                print(
                    f"TELEGRAM CHART SENT OK | message_id="
                    f"{r.json().get('result', {}).get('message_id')} | attempt={attempt}"
                )
                return True
            print(f"TELEGRAM PHOTO ERROR | attempt={attempt}: {r.text}")
        except Exception as e:
            print(f"TELEGRAM PHOTO EXCEPTION | attempt={attempt}: {e}")
    return False


def _make_chart(p) -> Path | None:
    try:
        ex = ccxt.bingx({
            "enableRateLimit": True,
            "timeout": 15000,
            "options": {"defaultType": "swap"},
        })
        rows = ex.fetch_ohlcv(p["coin"], timeframe="15m", limit=CHART_CANDLES)
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Asia/Jakarta")
        df["ema20"] = df.close.ewm(span=20, adjust=False).mean()
        df["ema50"] = df.close.ewm(span=50, adjust=False).mean()

        fig, (ax, av) = plt.subplots(
            2, 1, figsize=(12, 7.2), sharex=True,
            gridspec_kw={"height_ratios": [4, 1]},
        )
        fig.patch.set_facecolor("#08111b")
        ax.set_facecolor("#08111b")
        av.set_facecolor("#08111b")

        x = np.arange(len(df))
        width = 0.58
        for i, row in df.iterrows():
            up = row.close >= row.open
            body_low = min(row.open, row.close)
            body_h = max(abs(row.close - row.open), max(row.close, row.open) * 0.00001)
            ax.vlines(i, row.low, row.high, linewidth=0.8, color="#49d6a5" if up else "#ff5c6c")
            ax.add_patch(plt.Rectangle(
                (i - width / 2, body_low), width, body_h,
                facecolor="#49d6a5" if up else "#ff5c6c",
                edgecolor="#49d6a5" if up else "#ff5c6c",
                linewidth=0.5,
            ))

        ax.plot(x, df.ema20, linewidth=1.5, label="EMA20", color="#28b8ff")
        ax.plot(x, df.ema50, linewidth=1.5, label="EMA50", color="#ffc52b")

        entry = float(p["entry"]); sl = float(p["sl"]); tp = float(p["tp"])
        ax.axhline(entry, linewidth=1.6, linestyle="--", color="#ffd21f", label=f"ENTRY {_fmt(entry)}")
        ax.axhline(sl, linewidth=1.6, linestyle="--", color="#ff4d5e", label=f"SL {_fmt(sl)}")
        ax.axhline(tp, linewidth=1.6, linestyle="--", color="#19e6a2", label=f"TP {_fmt(tp)}")

        side = p["side"]
        status = p.get("telegram_status", "EXECUTED")
        ax.set_title(
            f"SAM EDGE V15  |  {p['coin']}  |  15m  |  {side}  |  {status}",
            color="white", fontsize=14, loc="left", pad=12, fontweight="bold",
        )
        ax.text(
            0.99, 0.98,
            f"ENTRY  {_fmt(entry)}\nSL      {_fmt(sl)}\nTP      {_fmt(tp)}\nRR      1.25R\nSCORE   {float(p.get('selection_score', 0)):.2f}",
            transform=ax.transAxes, ha="right", va="top", color="white", fontsize=10,
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#0d1c2a", edgecolor="#2d5575", alpha=0.95),
        )
        ax.grid(True, alpha=0.15)
        ax.tick_params(colors="#b8c7d6")
        for spine in ax.spines.values():
            spine.set_alpha(0.2)
        ax.legend(loc="lower left", ncol=3, fontsize=8, framealpha=0.15)

        av.bar(x, df.volume, width=0.7, alpha=0.65,
               color=["#49d6a5" if c >= o else "#ff5c6c" for c, o in zip(df.close, df.open)])
        av.set_ylabel("VOL", color="#b8c7d6", fontsize=8)
        av.tick_params(colors="#b8c7d6")
        av.grid(True, alpha=0.1)

        step = max(1, len(df) // 8)
        ticks = list(range(0, len(df), step))
        av.set_xticks(ticks)
        av.set_xticklabels([df.time.iloc[i].strftime("%d %b %H:%M") for i in ticks], rotation=0, fontsize=8)
        fig.text(
            0.01, 0.015,
            "BingX Perpetual  •  Paper Trading  •  SAM EDGE V15",
            color="#7f9bb3", fontsize=8,
        )
        plt.tight_layout(rect=(0, 0.025, 1, 1))
        fig.savefig(CHART_FILE, dpi=160, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        return CHART_FILE
    except Exception as e:
        print(f"CHART GENERATION ERROR | {p.get('coin')}: {type(e).__name__}: {e}")
        return None


def professional_send_signal(p, equity):
    side = p["side"]
    icon = "🟢" if side == "LONG" else "🔴"
    status = p.get("telegram_status", "EXECUTED")
    status_icon = "⚡" if status == "EXECUTED" else "🟡"
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
        f"<i>Chart = live BingX 15M OHLCV at signal time.\nPaper only — wait for TP/SL confirmation.</i>"
    )

    chart = _make_chart(p)
    if chart and _send_photo(chart, caption):
        return True

    # Do not lose the signal if chart generation/Telegram photo upload fails.
    return notifiers._send_text(
        "🏆 SAM EDGE V15 | NEW SIGNAL\n"
        f"{p['coin']} | {side}\n"
        f"ENTRY: {_fmt(p['entry'])}\nSL: {_fmt(p['sl'])}\nTP: {_fmt(p['tp'])}\n"
        f"RR: 1.25R | SCORE: {float(p.get('selection_score', 0)):.2f}\n"
        f"STATUS: {status}\nMODE: PAPER ONLY"
    )


# Patch the function imported dynamically by main_paper_v15.scan_once().
notifiers.send_signal = professional_send_signal


if __name__ == "__main__":
    print(f"SAM EDGE V15 PROFESSIONAL FORWARD TEST | TARGET={TARGET_TRADES}")
    ForwardPaperEngine().run()
