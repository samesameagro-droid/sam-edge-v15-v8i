from __future__ import annotations
import os
import requests
from dotenv import load_dotenv

load_dotenv()


def send_signal(p, equity):
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID')
    if not token or not chat_id:
        print('TELEGRAM SKIP | token/chat_id not configured')
        return False

    icon = '🟢' if p['side'] == 'LONG' else '🔴'
    risk_pct = float(p.get('risk_cash', 0)) / float(equity) * 100 if equity else 0.0
    text = (
        f'🏆 SAM EDGE V15 | PAPER\n'
        f'{icon} {p["coin"]} | {p["side"]}\n'
        f'CORE: {p.get("core", "V15_ADX4H_CANDLE2H")}\n'
        f'ENTRY: {p["entry"]:.8g}\n'
        f'SL: {p["sl"]:.8g}\n'
        f'TP: {p["tp"]:.8g}\n'
        f'RR: 1.25R\n'
        f'SCORE: {p.get("selection_score", 0):.2f}\n'
        f'RISK: {risk_pct:.2f}%\n'
        f'MODE: PAPER ONLY'
    )
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        r = requests.post(url, json={
            'chat_id': chat_id,
            'text': text,
            'disable_web_page_preview': True,
        }, timeout=30)
        if not r.ok:
            print(f'TELEGRAM HTTP {r.status_code}: {r.text}')
            return False
        data = r.json()
        if not data.get('ok'):
            print(f'TELEGRAM API ERROR: {data}')
            return False
        print(f'TELEGRAM SENT OK | message_id={data.get("result", {}).get("message_id")}')
        return True
    except Exception as e:
        print(f'TELEGRAM ERROR: {e}')
        return False
