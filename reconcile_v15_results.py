from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import telegram_result_guard as result_guard

STATE_FILE = Path('paper_v15_state.json')
MASTER_JOURNAL = Path('v15_forward_test_master_journal.csv')


def outcome_key(r: dict) -> str:
    """Stable result identity, including legacy trades with no signal_key."""
    return result_guard.result_key(
        r,
        str(r.get('result', '')).upper().strip(),
        str(r.get('closed_at', '')),
    )



def valid_result_record(r: dict) -> bool:
    result = str(r.get('result', '')).upper().replace(' HIT', '').strip()
    if result not in {'TP', 'SL'}:
        return False
    opened = str(r.get('signal_time') or r.get('opened_at') or '').strip()
    closed = str(r.get('closed_at') or '').strip()
    if not opened or not closed:
        return False
    try:
        from datetime import datetime
        opened_dt = datetime.fromisoformat(opened.replace('Z', '+00:00'))
        closed_dt = datetime.fromisoformat(closed.replace('Z', '+00:00'))
    except ValueError:
        return False
    return closed_dt >= opened_dt

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

    # Also reconcile the authoritative 50-trade master journal. This protects
    # TP/SL delivery when state and master journal briefly diverge.
    master_rows = []
    if MASTER_JOURNAL.exists():
        try:
            with MASTER_JOURNAL.open('r', encoding='utf-8', newline='') as f:
                master_rows = list(csv.DictReader(f))
        except Exception as e:
            print(f'OUTCOME RECONCILE | master journal read error: {type(e).__name__}: {e}')

    candidates = []
    seen = set()
    for r in list(closed) + list(master_rows):
        result = str(r.get('result', '')).upper().strip()
        # Master journal stores terminal outcomes as "TP HIT" / "SL HIT",
        # while paper state stores "TP" / "SL". Normalize before validation
        # so valid master rows are not falsely quarantined on every run.
        normalized = dict(r)
        normalized['result'] = result.replace(' HIT', '').strip()
        if normalized['result'] not in {'TP', 'SL'}:
            continue
        if not valid_result_record(normalized):
            print(f"OUTCOME RECONCILE QUARANTINE | close_before_open_or_invalid_time | {r.get('coin')} | {r.get('side')} | opened={r.get('signal_time') or r.get('opened_at')} | closed={r.get('closed_at')}")
            continue
        key = outcome_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(r)

    history = result_guard.notifiers._delivery_history()
    # Reconciliation is a recovery path for a just-closed trade, not a
    # permanent replay mechanism. The primary close() path already attempts
    # delivery immediately with retries. Replaying old outcomes on every
    # scheduled scan can duplicate Telegram messages when delivery history was
    # not persisted by a cancelled/interrupted runner.
    # With a 10-minute workflow cadence, 20 minutes gives one full recovery
    # window while preventing an old TP/SL from being resent hours later.
    MAX_RECONCILE_AGE_MIN = 20
    now = datetime.now(timezone.utc)
    missing = []
    for r in candidates:
        result = str(r.get('result', '')).upper().strip()
        if result not in {'TP', 'SL'}:
            continue
        key = outcome_key(r)
        if key in history:
            continue
        closed_raw = str(r.get('closed_at') or '').strip()
        try:
            closed_dt = datetime.fromisoformat(closed_raw.replace('Z', '+00:00'))
            if closed_dt.tzinfo is None:
                closed_dt = closed_dt.replace(tzinfo=timezone.utc)
            age_min = (now - closed_dt).total_seconds() / 60.0
        except ValueError:
            print(f'OUTCOME RECONCILE SKIP | invalid closed_at | {r.get("coin")} | {closed_raw}')
            continue
        if age_min > MAX_RECONCILE_AGE_MIN:
            print(f'OUTCOME RECONCILE SKIP | stale_result={age_min:.1f}m > {MAX_RECONCILE_AGE_MIN}m | {r.get("coin")} | {r.get("side")} | {result}')
            continue
        missing.append((key, r))

    print(f'OUTCOME RECONCILE | state_closed={len(closed)} | master_closed={len(master_rows)} | unique_results={len(candidates)} | missing_notifications={len(missing)}')

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
