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
DELIVERY_FILE = Path('telegram_delivery_history.json')
MAX_PENDING = 200
MAX_DELIVERED = 20000


def _telegram_config():
    # GitHub Actions secrets are injected as environment variables. Strip accidental
    # leading/trailing whitespace so a copied token/chat id cannot become malformed.
    token = (os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN') or '').strip()
    chat_id = (os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID') or '').strip()
    return token, chat_id


def _load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        value = json.loads(path.read_text(encoding='utf-8'))
        return value
    except Exception:
        return default


def _delivery_history() -> set[str]:
    rows = _load_json(DELIVERY_FILE, [])
    if not isinstance(rows, list):
        return set()
    return {str(x) for x in rows if x}


def _mark_delivered(signal_key: str | None):
    if not signal_key:
        return
    try:
        rows = list(_delivery_history())
        if signal_key not in rows:
            rows.append(signal_key)
        DELIVERY_FILE.write_text(
            json.dumps(rows[-MAX_DELIVERED:], ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception as e:
        print(f'TELEGRAM DELIVERY HISTORY ERROR: {e}')


def _normalise_pending(rows):
    """Accept both the old string queue format and the new keyed format."""
    out = []
    seen = set()
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, str):
            key, text = None, item
        elif isinstance(item, dict):
            key = str(item.get('key') or '').strip() or None
            text = str(item.get('text') or '')
            if not text:
                continue
        else:
            continue
        dedup = f'KEY:{key}' if key else f'TEXT:{text}'
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append({'key': key, 'text': text})
    return out[-MAX_PENDING:]


def _queue_pending(text: str, signal_key: str | None = None):
    try:
        rows = _normalise_pending(_load_json(PENDING_FILE, []))
        if signal_key and signal_key in _delivery_history():
            return
        # Replace any existing pending item for the same signal key.
        if signal_key:
            rows = [r for r in rows if r.get('key') != signal_key]
        elif any(r.get('key') is None and r.get('text') == text for r in rows):
            return
        rows.append({'key': signal_key, 'text': text})
        PENDING_FILE.write_text(
            json.dumps(rows[-MAX_PENDING:], ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception as e:
        print(f'TELEGRAM QUEUE ERROR: {e}')


def _remove_pending(text: str, signal_key: str | None = None):
    try:
        rows = _normalise_pending(_load_json(PENDING_FILE, []))
        if not rows:
            return
        if signal_key:
            rows = [r for r in rows if r.get('key') != signal_key]
        else:
            rows = [r for r in rows if r.get('text') != text]
        if rows:
            PENDING_FILE.write_text(
                json.dumps(rows[-MAX_PENDING:], ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        else:
            PENDING_FILE.unlink(missing_ok=True)
    except Exception as e:
        print(f'TELEGRAM QUEUE CLEANUP ERROR: {e}')


def _send_text(text: str, retries: int = 3, signal_key: str | None = None) -> bool:
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        print('TELEGRAM SKIP | token/chat_id not configured')
        _queue_pending(text, signal_key)
        return False

    if signal_key and signal_key in _delivery_history():
        print(f'TELEGRAM DEDUP | already delivered | key={signal_key}')
        _remove_pending(text, signal_key)
        return True

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
                    message_id = data.get('result', {}).get('message_id')
                    print(f'TELEGRAM SENT OK | message_id={message_id} | attempt={attempt}')
                    _mark_delivered(signal_key)
                    _remove_pending(text, signal_key)
                    return True
                print(f'TELEGRAM API ERROR | attempt={attempt}: {data}')
            else:
                print(f'TELEGRAM HTTP {r.status_code} | attempt={attempt}: {r.text}')
                if r.status_code == 401:
                    # Authentication failure will not be fixed by retrying the same token.
                    print('TELEGRAM AUTH FAILURE | 401 Unauthorized | check/replace TELEGRAM_BOT_TOKEN in GitHub Secrets')
                    break
        except Exception as e:
            print(f'TELEGRAM ERROR | attempt={attempt}: {e}')
        if attempt < retries:
            time.sleep(2 * attempt)

    _queue_pending(text, signal_key)
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
    signal_key = str(p.get('signal_key') or '').strip() or None
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
    return _send_text(text, signal_key=signal_key)


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


def retry_pending_messages() -> list[str]:
    """Retry queued Telegram messages; return signal keys successfully delivered."""
    if not PENDING_FILE.exists():
        return []
    rows = _normalise_pending(_load_json(PENDING_FILE, []))
    if not rows:
        return []

    delivered_keys: list[str] = []
    for item in list(rows):
        key = item.get('key')
        text = item.get('text', '')
        if not text:
            continue
        if key and key in _delivery_history():
            _remove_pending(text, key)
            delivered_keys.append(key)
            continue
        if _send_text(text, signal_key=key):
            if key:
                delivered_keys.append(key)
    return delivered_keys
