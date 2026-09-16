from __future__ import annotations

import html
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
PENDING_FILE = Path('pending_telegram_signals.json')


def _telegram_config():
    # GitHub Actions secrets are injected as environment variables. Strip accidental
    # leading/trailing whitespace so a copied token/chat id cannot become malformed.
    token = (os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN') or '').strip()
    chat_id = (os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID') or '').strip()
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
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }

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
                if r.status_code == 401:
                    # 401 is an authentication failure; retrying the same token cannot fix it.
                    print('TELEGRAM AUTH FAILURE | 401 Unauthorized | check/replace TELEGRAM_BOT_TOKEN in GitHub Secrets')
                    break
        except Exception as e:
            print(f'TELEGRAM ERROR | attempt={attempt}: {e}')
        if attempt < retries:
            time.sleep(2 * attempt)

    _queue_pending(text)
    print('TELEGRAM DELIVERY FAILED | queued for next V15 run')
    return False


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def send_signal(p, equity):
    is_long = p['side'] == 'LONG'
    side_icon = '🟢' if is_long else '🔴'
    side_label = 'LONG' if is_long else 'SHORT'
    status = str(p.get('telegram_status', 'EXECUTED')).upper()
    core = str(p.get('core', 'V15_ADX4H_CANDLE2H'))
    risk_pct = float(p.get('risk_cash', 0)) / float(equity) * 100 if equity else 0.0
    score = float(p.get('selection_score', 0) or 0)
    opened_at = p.get('opened_at', '-')

    text = (
        '🏆 <b>SAM EDGE V15</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'⚡ <b>NEW PAPER SIGNAL</b>\n\n'
        f'{side_icon} <b>{_esc(p["coin"])}</b>  |  <b>{side_label}</b>\n'
        f'🧠 Core: <code>{_esc(core)}</code>\n'
        f'🕒 Signal: <code>{_esc(opened_at)}</code>\n'
        f'📌 Status: <b>{_esc(status)}</b>\n\n'
        '💰 <b>TRADE LEVELS</b>\n'
        '├ Entry     <code>' + f'{p["entry"]:.8g}' + '</code>\n'
        '├ Stop Loss <code>' + f'{p["sl"]:.8g}' + '</code>\n'
        '└ Take Profit <code>' + f'{p["tp"]:.8g}' + '</code>\n\n'
        '📊 <b>RISK &amp; QUALITY</b>\n'
        f'├ Risk      <b>{risk_pct:.2f}%</b>\n'
        '├ R:R       <b>1.25R</b>\n'
        f'└ Score     <b>{score:.2f}</b>\n\n'
        '🛡 <b>EXECUTION</b>\n'
        '• Paper trading only\n'
        '• Follow the predefined Entry / SL / TP\n'
        '• No chasing after the signal\n\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        '<i>SAM EDGE V15 • Structured Signal Engine</i>'
    )
    return _send_text(text)


def send_result(p, result, exit_price, closed_at, equity):
    is_tp = result == 'TP'
    icon = '🎯' if is_tp else '🛑'
    rr_result = '1.25R' if is_tp else '-1.00R'
    result_label = 'TAKE PROFIT' if is_tp else 'STOP LOSS'
    text = (
        '🏆 <b>SAM EDGE V15</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'{icon} <b>PAPER TRADE CLOSED</b>\n\n'
        f'🪙 <b>{_esc(p["coin"])}</b>  |  <b>{_esc(p["side"])}</b>\n'
        f'📌 Result: <b>{result_label}</b>\n'
        f'📈 R-Multiple: <b>{rr_result}</b>\n\n'
        '💰 <b>TRADE LEVELS</b>\n'
        '├ Entry  <code>' + f'{p["entry"]:.8g}' + '</code>\n'
        '├ Exit   <code>' + f'{float(exit_price):.8g}' + '</code>\n'
        '├ SL     <code>' + f'{p["sl"]:.8g}' + '</code>\n'
        '└ TP     <code>' + f'{p["tp"]:.8g}' + '</code>\n\n'
        f'🕒 Closed: <code>{_esc(closed_at)}</code>\n'
        f'💵 Equity: <b>${float(equity):.2f}</b>\n\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        '<i>SAM EDGE V15 • Paper Forward Record</i>'
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
