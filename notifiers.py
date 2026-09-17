from __future__ import annotations

import csv
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
TRADING_JOURNAL_FILE = Path('paper_v15_trading_journal.csv')
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


def _fmt(v, digits=4):
    try:
        return f'{float(v):.{digits}f}'
    except Exception:
        return '-'


def _journal_signal(p, status: str):
    """Persist every valid V15 signal, starting from the first signal such as AIN."""
    key = str(p.get('signal_key') or '').strip()
    if not key:
        return
    try:
        rows = []
        if TRADING_JOURNAL_FILE.exists():
            with TRADING_JOURNAL_FILE.open('r', encoding='utf-8', newline='') as f:
                rows = list(csv.DictReader(f))

        existing = next((r for r in rows if r.get('signal_key') == key), None)
        if existing:
            # Do not overwrite an already recorded outcome.
            if existing.get('result') not in ('', None):
                return
            existing['status'] = status
        else:
            metrics = p.get('entry_metrics') or {}
            existing = {
                'signal_key': key,
                'signal_time': p.get('opened_at', ''),
                'coin': p.get('coin', ''),
                'side': p.get('side', ''),
                'core': p.get('core', ''),
                'status': status,
                'entry': p.get('entry', ''),
                'sl': p.get('sl', ''),
                'tp': p.get('tp', ''),
                'entry_score': p.get('entry_score', p.get('selection_score', '')),
                'rsi_entry': metrics.get('rsi', ''),
                'adx4h_pct_entry': metrics.get('h4_adx_pct', ''),
                'adx4h_delta_entry': metrics.get('h4_adx_delta', ''),
                'volume_ratio_entry': metrics.get('volr', ''),
                'ema20_dist_atr_entry': metrics.get('dist_ema20_atr', ''),
                'result': '',
                'exit': '',
                'R': '',
                'closed_at': '',
                'equity_after': '',
                'post_mortem': '',
            }
            rows.append(existing)

        fields = [
            'signal_key','signal_time','coin','side','core','status','entry','sl','tp',
            'entry_score','rsi_entry','adx4h_pct_entry','adx4h_delta_entry',
            'volume_ratio_entry','ema20_dist_atr_entry','result','exit','R',
            'closed_at','equity_after','post_mortem'
        ]
        # Normalise all rows to the same schema.
        for r in rows:
            for field in fields:
                r.setdefault(field, '')
        with TRADING_JOURNAL_FILE.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f'📒 JOURNAL SIGNAL | {p.get("coin")} | {p.get("side")} | status={status}')
    except Exception as e:
        # Journal failure must never stop signal delivery or the paper engine.
        print(f'JOURNAL SIGNAL ERROR | {p.get("coin")} | {type(e).__name__}: {e}')


def _journal_result(p, result, exit_price, closed_at, equity):
    """Update the original signal row with TP/SL outcome and post-mortem."""
    key = str(p.get('signal_key') or '').strip()
    if not key:
        return
    try:
        rows = []
        if TRADING_JOURNAL_FILE.exists():
            with TRADING_JOURNAL_FILE.open('r', encoding='utf-8', newline='') as f:
                rows = list(csv.DictReader(f))
        row = next((r for r in rows if r.get('signal_key') == key), None)
        if row is None:
            # Defensive fallback if an old signal was closed before journaling existed.
            _journal_signal(p, 'EXECUTED')
            with TRADING_JOURNAL_FILE.open('r', encoding='utf-8', newline='') as f:
                rows = list(csv.DictReader(f))
            row = next((r for r in rows if r.get('signal_key') == key), None)
        if row is None:
            return

        rr = 1.25 if result == 'TP' else -1.0
        row['status'] = 'CLOSED'
        row['result'] = result
        row['exit'] = exit_price
        row['R'] = rr
        row['closed_at'] = closed_at
        row['equity_after'] = equity
        row['post_mortem'] = (
            'FOLLOW_THROUGH_OK_TP' if result == 'TP' else 'FAILED_FOLLOW_THROUGH_SL'
        )
        fields = [
            'signal_key','signal_time','coin','side','core','status','entry','sl','tp',
            'entry_score','rsi_entry','adx4h_pct_entry','adx4h_delta_entry',
            'volume_ratio_entry','ema20_dist_atr_entry','result','exit','R',
            'closed_at','equity_after','post_mortem'
        ]
        for r in rows:
            for field in fields:
                r.setdefault(field, '')
        with TRADING_JOURNAL_FILE.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f'📒 JOURNAL RESULT | {p.get("coin")} | {result} | R={rr:+.2f}')
    except Exception as e:
        print(f'JOURNAL RESULT ERROR | {p.get("coin")} | {type(e).__name__}: {e}')


def _reason_lines(p):
    """Build human-readable reasons from the exact V15 entry snapshot."""
    side = str(p.get('side', 'LONG')).upper()
    diag = p.get('entry_diag') or {}
    gates = diag.get('long' if side == 'LONG' else 'short', {})
    names = {
        'trend': 'HTF 1H + 4H searah',
        'ema': 'struktur EMA mendukung',
        'vwap': 'harga berada di sisi VWAP yang sesuai',
        'di': '+DI/-DI mendukung arah',
        'rsi': 'RSI berada di zona setup',
        'room': 'ruang ke resistance/support mencukupi',
        'no_chase': 'belum masuk kondisi chase',
        'adx': 'ADX 4H memenuhi filter kekuatan',
        'early_reclaim': 'terjadi Early Reclaim sebagai trigger',
    }
    passed = [names[k] for k in names if gates.get(k)]
    failed = [names[k] for k in names if not gates.get(k)]
    parts = []
    if passed:
        parts.append(' + '.join(passed[:5]))
    if failed:
        parts.append('Gate gagal: ' + ', '.join(failed[:3]))
    return parts


def _score_breakdown(p):
    b = p.get('score_breakdown') or {}
    if not b:
        return ''
    labels = [
        ('adx', 'ADX', 30),
        ('delta', 'ADX Delta', 20),
        ('room', 'Room', 20),
        ('location', 'Lokasi EMA20', 15),
        ('volume', 'Volume', 10),
        ('trigger', 'Trigger Body', 5),
    ]
    lines = []
    for key, label, weight in labels:
        val = float(b.get(key, 0) or 0)
        contrib = val * weight
        lines.append(f'├ {label:<13} {contrib:>5.2f}/{weight}')
    return '\n'.join(lines)


def send_signal(p, equity):
    # Journal first so the signal is recorded even if Telegram is temporarily down.
    status = str(p.get('telegram_status', 'EXECUTED')).upper()
    _journal_signal(p, status)

    is_long = p['side'] == 'LONG'
    side_icon = '🟢' if is_long else '🔴'
    side_label = 'LONG' if is_long else 'SHORT'
    core = str(p.get('core', 'V15_ADX4H_CANDLE2H'))
    signal_key = str(p.get('signal_key') or '').strip() or None
    risk_pct = float(p.get('risk_cash', 0)) / float(equity) * 100 if equity else 0.0
    score = float(p.get('selection_score', 0) or 0)
    opened_at = p.get('opened_at', '-')
    metrics = p.get('entry_metrics') or {}
    reasons = _reason_lines(p)
    breakdown = _score_breakdown(p)
    reason_text = '\n'.join(f'• {x}' for x in reasons) if reasons else '• Setup memenuhi gate V15.'

    text = (
        '🏆 <b>SAM EDGE V15</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'⚡ <b>NEW PAPER SIGNAL</b>\n\n'
        f'{side_icon} <b>{_esc(p["coin"])}</b>  |  <b>{side_label}</b>\n'
        f'🧠 Core: <code>{_esc(core)}</code>\n'
        f'🕒 Signal: <code>{_esc(opened_at)}</code>\n'
        f'📌 Status: <b>{_esc(status)}</b>\n\n'
        '🧠 <b>WHY ENTRY?</b>\n'
        f'{reason_text}\n\n'
        '📊 <b>SCORE BREAKDOWN</b>\n'
        f'{breakdown or "• Score breakdown tidak tersedia pada snapshot ini."}\n'
        f'└ <b>Total Score: {score:.2f}</b>\n\n'
        '📐 <b>ENTRY SNAPSHOT</b>\n'
        f'├ RSI       <b>{_fmt(metrics.get("rsi"), 2)}</b>\n'
        f'├ ADX 4H %  <b>{_fmt(metrics.get("h4_adx_pct"), 3)}</b>\n'
        f'├ ADX Δ     <b>{_fmt(metrics.get("h4_adx_delta"), 3)}</b>\n'
        f'├ Vol Ratio <b>{_fmt(metrics.get("volr"), 2)}x</b>\n'
        f'└ EMA20 dist <b>{_fmt(metrics.get("dist_ema20_atr"), 2)} ATR</b>\n\n'
        '💰 <b>TRADE LEVELS</b>\n'
        '├ Entry     <code>' + f'{p["entry"]:.8g}' + '</code>\n'
        '├ Stop Loss <code>' + f'{p["sl"]:.8g}' + '</code>\n'
        '└ Take Profit <code>' + f'{p["tp"]:.8g}' + '</code>\n\n'
        '🛡 <b>RISK &amp; QUALITY</b>\n'
        f'├ Risk      <b>{risk_pct:.2f}%</b>\n'
        '├ R:R       <b>1.25R</b>\n'
        f'└ Score     <b>{score:.2f}</b>\n\n'
        '🛡 <b>EXECUTION</b>\n'
        '• Paper trading only\n'
        '• Follow predefined Entry / SL / TP\n'
        '• No chasing after the signal\n\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        '<i>SAM EDGE V15 • Structured Signal Engine</i>'
    )
    return _send_text(text, signal_key=signal_key)


def send_result(p, result, exit_price, closed_at, equity):
    _journal_result(p, result, exit_price, closed_at, equity)

    is_tp = result == 'TP'
    icon = '🎯' if is_tp else '🛑'
    rr_result = '1.25R' if is_tp else '-1.00R'
    result_label = 'TAKE PROFIT' if is_tp else 'STOP LOSS'
    metrics = p.get('entry_metrics') or {}
    score = float(p.get('entry_score', 0) or 0)
    gates = p.get('entry_diag') or {}
    side_key = 'long' if p.get('side') == 'LONG' else 'short'
    gate_map = gates.get(side_key, {})
    passed = sum(1 for v in gate_map.values() if v)
    total = len(gate_map) or 9

    if is_tp:
        why = (
            '• Setup tervalidasi sampai target.\n'
            f'• Saat ENTRY, {passed}/{total} gate {p.get("side")} aktif.\n'
            '• Trigger Early Reclaim berhasil mendapat follow-through sesuai TP.'
        )
        post = 'Follow-through sesuai skenario V15 → TP tercapai.'
    else:
        why = (
            '• Setup saat ENTRY tetap dicatat berdasarkan snapshot V15, bukan diubah setelah kejadian.\n'
            f'• Saat ENTRY, {passed}/{total} gate {p.get("side")} aktif.\n'
            '• Setelah ENTRY, harga bergerak berlawanan sampai SL; ini dikategorikan sebagai <b>failed follow-through</b>. '
            'SL menjadi invalidation sesuai aturan trade.'
        )
        post = 'Follow-through gagal → invalidation/SL tersentuh.'

    text = (
        '🏆 <b>SAM EDGE V15</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'{icon} <b>PAPER TRADE CLOSED</b>\n\n'
        f'🪙 <b>{_esc(p["coin"])}</b>  |  <b>{_esc(p["side"])}</b>\n'
        f'📌 Result: <b>{result_label}</b>\n'
        f'📈 R-Multiple: <b>{rr_result}</b>\n\n'
        f'🔎 <b>WHY {"PROFIT" if is_tp else "LOSS"}?</b>\n'
        f'{why}\n\n'
        f'🧾 <b>POST-MORTEM</b>\n'
        f'• {post}\n'
        f'• Entry Score: <b>{score:.2f}</b>\n'
        f'• RSI saat Entry: <b>{_fmt(metrics.get("rsi"), 2)}</b>\n'
        f'• ADX 4H % saat Entry: <b>{_fmt(metrics.get("h4_adx_pct"), 3)}</b>\n\n'
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
    key = str(p.get('signal_key') or '').strip() or None
    # Outcome notifications use their own key so they cannot be blocked by the
    # signal-delivery dedup history.
    outcome_key = f'{key}|RESULT|{result}|{closed_at}' if key else None
    return _send_text(text, signal_key=outcome_key)


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
