"""SAM EDGE V15 runtime compatibility hooks.

Keeps the existing pandas datetime compatibility patch and installs the
metadata-safe TP/SL Telegram result guard before the V15 engine runs.
"""

import pandas as pd

_ORIGINAL_MERGE_ASOF = pd.merge_asof


def _normalize_datetime_key(frame, key):
    if key and key in frame.columns and pd.api.types.is_datetime64_any_dtype(frame[key]):
        out = frame.copy()
        ts = pd.to_datetime(out[key], utc=True)
        try:
            out[key] = pd.Series(ts, index=out.index).dt.as_unit("ns")
        except AttributeError:
            out[key] = pd.to_datetime(pd.Series(ts, index=out.index), utc=True).astype("datetime64[ns, UTC]")
        return out
    return frame


def _restore_datetime_key(frame, key):
    if not key or key not in frame.columns:
        return frame
    out = frame.copy()
    s = out[key]
    if pd.api.types.is_datetime64_any_dtype(s):
        out[key] = pd.to_datetime(s, utc=True)
    elif pd.api.types.is_integer_dtype(s) or pd.api.types.is_float_dtype(s):
        out[key] = pd.to_datetime(s, unit="ns", utc=True)
    else:
        out[key] = pd.to_datetime(s, utc=True)
    return out


def merge_asof_compat(left, right, *args, **kwargs):
    on = kwargs.get("on")
    left_on = kwargs.get("left_on")
    right_on = kwargs.get("right_on")
    if on:
        left = _normalize_datetime_key(left, on)
        right = _normalize_datetime_key(right, on)
    else:
        if left_on:
            left = _normalize_datetime_key(left, left_on)
        if right_on:
            right = _normalize_datetime_key(right, right_on)
    result = _ORIGINAL_MERGE_ASOF(left, right, *args, **kwargs)
    if on:
        result = _restore_datetime_key(result, on)
    else:
        if left_on:
            result = _restore_datetime_key(result, left_on)
        if right_on and right_on != left_on:
            result = _restore_datetime_key(result, right_on)
    return result


pd.merge_asof = merge_asof_compat

# Install the result-delivery guard. This is intentionally isolated from the
# trading strategy: if the guard cannot load, the original notifier remains in
# place and the paper engine can still run.
try:
    import notifiers
    from telegram_result_guard import send_result as _guarded_send_result
    notifiers.send_result = _guarded_send_result
    print("TELEGRAM RESULT GUARD | installed")
except Exception as e:
    print(f"TELEGRAM RESULT GUARD | install failed: {type(e).__name__}: {e}")
