"""Runtime compatibility for pandas datetime-resolution changes.

SAM EDGE V15 intentionally keeps its strategy logic unchanged. This module only
normalizes timezone-aware datetime keys passed to pandas.merge_asof so pandas
versions that preserve different datetime resolutions (ms/us/ns) do not reject
otherwise equivalent timestamps.
"""

import pandas as pd

_original_merge_asof = pd.merge_asof


def _normalize_datetime_key(frame, key):
    if key and key in frame.columns and pd.api.types.is_datetime64_any_dtype(frame[key]):
        out = frame.copy()
        out[key] = pd.to_datetime(out[key], utc=True).astype("datetime64[ns, UTC]")
        return out
    return frame


def merge_asof_compat(left, right, *args, **kwargs):
    on = kwargs.get("on")
    if on:
        left = _normalize_datetime_key(left, on)
        right = _normalize_datetime_key(right, on)
    return _original_merge_asof(left, right, *args, **kwargs)


pd.merge_asof = merge_asof_compat
