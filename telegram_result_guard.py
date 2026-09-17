from __future__ import annotations

import html
from typing import Any

import notifiers


def result_key(p: dict[str, Any], result: str, closed_at: str) -> str:
    base = f"{p.get('coin','')}|{p.get('side','')}|{p.get('opened_at','')}|{p.get('core','')}"
    return f"{base}|RESULT|{str(result).upper()}|{closed_at}"


def signal_key(p: dict[str, Any]) -> str:
    existing = str(p.get('signal_key') or '').strip()
    if existing:
        return existing
    return f"{p.get('coin','')}|{p.get('side','')}|{p.get('opened_at','')}|{p.get('core','')}"


def _fmt(v: Any, digits: int = 8) -> str:
    try:
        return f"{float(v):.{digits}g}"
    except Exception:
        return "-"


def _metadata_lines(p: dict[str, Any]) -> list[str]:
    metrics = p.get('entry_metrics') or {}
    diag = p.get('entry_diag') or {}
    side = 'long' if str(p.get('side')).upper() == 'LONG' else 'short'
    gates = diag.get(side) if isinstance(diag, dict) else None
    lines: list[str] = []
    if isinstance(gates, dict) and gates:
        passed = sum(bool(v) for v in gates.values())
        total = len(gates)
        lines.append(f"• Entry gate snapshot: <b>{passed}/{total}</b> aktif.")
    else:
        lines.append("• Entry gate snapshot: <i>tidak tersedia pada legacy state.</i>")

    score = p.get('entry_score', p.get('selection_score'))
    if score not in (None, '', 0, 0.0):
        lines.append(f"• Entry Score: <b>{_fmt(score, 2)}</b>")
    else:
        lines.append("• Entry Score: <i>tidak tersedia pada legacy state.</i>")

    rsi = metrics.get('rsi')
    adx = metrics.get('h4_adx_pct')
    if rsi not in (None, ''):
        lines.append(f"• RSI saat Entry: <b>{_fmt(rsi, 2)}</b>")
    if adx not in (None, ''):
        lines.append(f"• ADX 4H % saat Entry: <b>{_fmt(adx, 3)}</b>")
    return lines


def send_result(p: dict[str, Any], result: str, exit_price: float, closed_at: str, equity: float) -> bool:
    result = str(result).upper().strip()
    if result not in {'TP', 'SL'}:
        return False

    # Always use a deterministic RESULT identity. This is different from the
    # entry/signal identity, so a delivered entry cannot suppress the outcome.
    key = result_key(p, result, closed_at)

    # Keep the trading journal useful even for legacy positions that predate the
    # signal_key metadata. The normal notifier journal remains the source of truth.
    q = dict(p)
    q['signal_key'] = signal_key(p)
    try:
        notifiers._journal_result(q, result, exit_price, closed_at, equity)
    except Exception as e:
        print(f"RESULT JOURNAL PATCH ERROR | {p.get('coin')} | {e}")

    history = notifiers._delivery_history()
    if key in history:
        print(f"TELEGRAM RESULT DEDUP | {key}")
        return True

    token, chat_id = notifiers._telegram_config()
    if not token or not chat_id:
        text = _build_text(q, result, exit_price, closed_at, equity)
        notifiers._queue_pending(text, key)
        print(f"TELEGRAM RESULT QUEUED | {p.get('coin')} | {result} | missing config")
        return False

    text = _build_text(q, result, exit_price, closed_at, equity)
    ok = notifiers._send_text(text, retries=5, signal_key=key)
    if ok:
        print(f"TELEGRAM RESULT GUARDED DELIVERY OK | {p.get('coin')} | {result} | key={key}")
    else:
        print(f"TELEGRAM RESULT GUARDED DELIVERY FAILED | {p.get('coin')} | {result} | queued")
    return ok


def _build_text(p: dict[str, Any], result: str, exit_price: float, closed_at: str, equity: float) -> str:
    is_tp = result == 'TP'
    icon = '🎯' if is_tp else '🛑'
    label = 'TAKE PROFIT' if is_tp else 'STOP LOSS'
    rr = '+1.25R' if is_tp else '-1.00R'
    coin = html.escape(str(p.get('coin', '-')), quote=False)
    side = html.escape(str(p.get('side', '-')), quote=False)
    core = html.escape(str(p.get('core', '-')), quote=False)
    entry = _fmt(p.get('entry'))
    sl = _fmt(p.get('sl'))
    tp = _fmt(p.get('tp'))
    exit_px = _fmt(exit_price)

    why = (
        'Follow-through mencapai target TP yang telah ditetapkan saat entry.'
        if is_tp else
        'Harga bergerak berlawanan dengan skenario entry hingga level invalidation/SL.'
    )

    lines = [
        '🏆 <b>SAM EDGE V15</b>',
        '━━━━━━━━━━━━━━━━━━━━',
        f'{icon} <b>PAPER TRADE CLOSED</b>',
        '',
        f'🪙 <b>{coin}</b>  |  <b>{side}</b>',
        f'📌 Result: <b>{label}</b>',
        f'📈 R-Multiple: <b>{rr}</b>',
        f'🕒 Closed: <code>{html.escape(str(closed_at), quote=False)}</code>',
        '',
        f'🔎 <b>WHY {"PROFIT" if is_tp else "LOSS"}?</b>',
        f'• {why}',
        *_metadata_lines(p),
        '',
        '💰 <b>TRADE LEVELS</b>',
        f'├ Entry     <code>{entry}</code>',
        f'├ Stop Loss <code>{_fmt(p.get("sl"))}</code>',
        f'├ Take Profit <code>{_fmt(p.get("tp"))}</code>',
        f'└ Exit      <code>{exit_px}</code>',
        '',
        f'💵 Equity After: <b>${float(equity):.4f}</b>',
        f'🧠 Core: <code>{core}</code>',
        '',
        '━━━━━━━━━━━━━━━━━━━━',
        '<i>SAM EDGE V15 • Result Delivery Guard • Paper Trading</i>',
    ]
    return '\n'.join(lines)
