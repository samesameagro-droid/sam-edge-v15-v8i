from __future__ import annotations

import json
from pathlib import Path

import notifiers

STATE_FILE = Path('paper_v15_state.json')


def outcome_key(r: dict) -> str:
    base = f"{r.get('coin','')}|{r.get('side','')}|{r.get('opened_at','')}|{r.get('core','')}"
    return f"{base}|RESULT|{str(r.get('result','')).upper()}|{r.get('closed_at','')}"


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

    history = notifiers._delivery_history()
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
        ok = notifiers.send_result(
            r,
            result,
            float(r.get('exit', fallback)),
            str(r.get('closed_at', '')),
            float(r.get('equity_after', state.get('equity', 0))),
        )
        if ok:
            delivered += 1
        else:
            failed += 1
        history = notifiers._delivery_history()

    print(f'OUTCOME RECONCILE COMPLETE | delivered={delivered} | queued_or_failed={failed}')


if __name__ == '__main__':
    main()
