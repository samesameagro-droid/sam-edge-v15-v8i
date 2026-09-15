from __future__ import annotations

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()


def _telegram_config():
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID')
    return token, chat_id


def _send_text(text: str, retries: int = 3) -> bool:
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        print('TELEGRAM SKIP | token/chat_id not configured')
        return False

    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': text,
        'disable_web_page_preview': True,
    }

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.ok:
                data = r.json()
                if data.get('ok'):
                    print(f'TELEGRAM SENT OK | message_id={data.get("result", {}).get("message_id")} | attempt={attempt}')
                    return True
                print(f'TELEGRAM API ERROR | attempt={attempt}: {data}')
            else:
                print(f'TELEGRAM HTTP {r.status_code} | attempt={attempt}: {r.text}')
        except Exception as e:
            print(f'TELEGRAM ERROR | attempt={attempt}: {e}')
        if attempt < retries:
            time.sleep(2 * attempt)

    print('TELEGRAM DELIVERY FAILED | all retries exhausted')
    return False


def send_signal(p, equity):
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
        f'STATUS: {p.get("telegram_status", "EXECUTED")}\n'
        f'MODE: PAPER ONLY'
    )
    return _send_text(text)


def send_result(p, result, exit_price, closed_at, equity):
    icon = '🎯' if result == 'TP' else '🛑'
    rr_result = '1.25R' if result == 'TP' else '-1.00R'
    text = (
        f'{icon} SAM EDGE V15 | PAPER RESULT\n'
        f'{p["coin"]} | {p["side"]}\n'
        f'RESULT: {result} ({rr_result})\n'
        f'ENTRY: {p["entry"]:.8g}\n'
        f'EXIT: {float(exit_price):.8g}\n'
        f'SL: {p["sl"]:.8g}\n'
        f'TP: {p["tp"]:.8g}\n'
        f'CLOSED: {closed_at}\n'
        f'EQUITY: ${float(equity):.2f}\n'
        f'MODE: PAPER ONLY'
    )
    return _send_text(text)
