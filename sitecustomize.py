"""SAM EDGE V15 pandas datetime compatibility.

This module does not change any trading rule. It only forces datetime merge keys
used by pandas.merge_asof() onto one identical, timezone-aware nanosecond unit.
"""

import pandas as pd

_ORIGINAL_MERGE_ASOF = pd.merge_asof


def _normalize_datetime_key(frame, key):
    if key and key in frame.columns and pd.api.types.is_datetime64_any_dtype(frame[key]):
        out = frame.copy()
        ts = pd.to_datetime(out[key], utc=True)
        # Build the Series explicitly from int64 nanoseconds so pandas cannot
        # silently preserve the source ms/us resolution.
        out[key] = pd.Series(ts.astype("int64"), index=out.index).astype("datetime64[ns, UTC]")
        return out
    return frame


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

    return _ORIGINAL_MERGE_ASOF(left, right, *args, **kwargs)


pd.merge_asof = merge_asof_compat
