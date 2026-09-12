
from __future__ import annotations
import os, requests

TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("BOT_TOKEN", "")).strip()
CHAT_ID=os.getenv("TELEGRAM_CHAT_ID", os.getenv("CHAT_ID", "")).strip()

def send_signal(position,equity):
    if not TOKEN or not CHAT_ID:
        print("[TELEGRAM] skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured")
        return
    side=position["side"]
    emoji="🟢" if side=="LONG" else "🔴"
    txt=(
        f"🏆 SAM EDGE V15 | V8I FROZEN\n"
        f"{emoji} {position['coin']} | {side}\n"
        f"CORE: V8I_ADX_ASYM\n"
        f"ENTRY: {position['entry']:.10g}\n"
        f"SL: {position['sl']:.10g}\n"
        f"TP: {position['tp']:.10g}\n"
        f"RR: 1.25R\n"
        f"RISK: 0.50%\n"
        f"PAPER EQUITY: ${equity:.2f}\n"
        f"MODE: PAPER ONLY"
    )
    url=f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r=requests.post(url,data={"chat_id":CHAT_ID,"text":txt},timeout=15)
    r.raise_for_status()
