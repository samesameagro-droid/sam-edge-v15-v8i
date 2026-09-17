from __future__ import annotations

import json
from pathlib import Path

import telegram_result_guard as result_guard

STATE_FILE = Path('paper_v15_state.json')


def outcome_key(r: dict) -> str:
    """Stable result identity, including legacy trades with no signal_key."""
    return result_guard.result_key(
        r,
        str(r.get('result', '')).upper().strip(),
        str(r.get('closed_at', '')),
    )


def main() -> None:
    if not STATE_FILE.exists():
        print('OUTCOME RECONCILE | no paper_v15_state.json')
        return

    try:
        state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'OUTCOME RECONCILE ERROR | state read: {type(e).__name__}: {e}')
        raise SystemExit(1)

    closed = state.get('closed', []) if isinstance(state, dict) else []
    if not isinstance(closed, list):
        closed = []

    history = result_guard.notifiers._delivery_history()
    missing = []
    for r in closed:
        result = str(r.get('result', '')).upper().strip()
        if result not in {'TP', 'SL'}:
            continue
        key = outcome_key(r)
        if key not in history:
            missing.append((key, r))

    print(f'OUTCOME RECONCILE | closed={len(closed)} | missing_notifications={len(missing)}')

    delivered = 0
    failed = 0
    for key, r in missing:
        result = str(r.get('result', '')).upper().strip()
        fallback = r.get('tp' if result == 'TP' else 'sl')
        exit_price = float(r.get('exit', fallback))
        closed_at = str(r.get('closed_at', ''))
        equity = float(r.get('equity_after', state.get('equity', 0)))

        # Use the guarded deterministic result sender. This is critical for
        # legacy positions whose signal_key was empty: the guard derives the
        # same stable identity from coin/side/opened_at/core/result/closed_at.
        ok = result_guard.send_result(
            r,
            result,
            exit_price,
            closed_at,
            equity,
        )
        if ok:
            delivered += 1
        else:
            failed += 1
        history = result_guard.notifiers._delivery_history()

    print(f'OUTCOME RECONCILE COMPLETE | delivered={delivered} | queued_or_failed={failed}')


if __name__ == '__main__':
    main()
