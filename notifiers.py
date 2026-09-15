from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
PENDING_FILE = Path('pending_telegram_signals.json')


def _telegram_config():
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID')
    return token, chat_id


def _queue_pending(text: str):
    try:
        rows = []
        if PENDING_FILE.exists():
            rows = json.loads(PENDING_FILE.read_text(encoding='utf-8'))
            if not isinstance(rows, list):
                rows = []
        if text not in rows:
            rows.append(text)
        PENDING_FILE.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f'TELEGRAM QUEUE ERROR: {e}')


def _remove_pending(text: str):
    try:
        if not PENDING_FILE.exists():
            return
        rows = json.loads(PENDING_FILE.read_text(encoding='utf-8'))
        if not isinstance(rows, list):
            return
        rows = [x for x in rows if x != text]
        if rows:
            PENDING_FILE.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            PENDING_FILE.unlink(missing_ok=True)
    except Exception as e:
        print(f'TELEGRAM QUEUE CLEANUP ERROR: {e}')


def _send_text(text: str, retries: int = 3) -> bool:
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        print('TELEGRAM SKIP | token/chat_id not configured')
        _queue_pending(text)
        return False

    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.ok:
                data = r.json()
                if data.get('ok'):
                    print(f'TELEGRAM SENT OK | message_id={data.get("result", {}).get("message_id")} | attempt={attempt}')
                    _remove_pending(text)
                    return True
                print(f'TELEGRAM API ERROR | attempt={attempt}: {data}')
            else:
                print(f'TELEGRAM HTTP {r.status_code} | attempt={attempt}: {r.text}')
        except Exception as e:
            print(f'TELEGRAM ERROR | attempt={attempt}: {e}')
        if attempt < retries:
            time.sleep(2 * attempt)

    _queue_pending(text)
    print('TELEGRAM DELIVERY FAILED | queued for next V15 run')
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


def retry_pending_messages() -> int:
    if not PENDING_FILE.exists():
        return 0
    try:
        rows = json.loads(PENDING_FILE.read_text(encoding='utf-8'))
        if not isinstance(rows, list):
            return 0
    except Exception:
        return 0

    sent = 0
    for text in list(rows):
        if _send_text(text):
            sent += 1
    return sent
