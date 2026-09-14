"""SAM EDGE V15 pandas datetime compatibility.

This module does not change any trading rule. It only makes datetime merge keys
used by pandas.merge_asof() consistent while preserving their actual timestamps.
"""

import pandas as pd

_ORIGINAL_MERGE_ASOF = pd.merge_asof


def _normalize_datetime_key(frame, key):
    if key and key in frame.columns and pd.api.types.is_datetime64_any_dtype(frame[key]):
        out = frame.copy()
        ts = pd.to_datetime(out[key], utc=True)
        # Explicitly convert the datetime representation to nanoseconds.
        # Important: int64 of a datetime64[ms] series is still milliseconds;
        # assigning those integers to a datetime64[ns] dtype would shift valid
        # dates into 1970. Use pandas' unit-aware conversion instead.
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
        # Defensive fallback for pandas versions that return the normalized
        # integer key. These integers represent nanoseconds after normalization.
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
