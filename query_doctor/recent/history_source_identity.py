"""Validate explicit non-secret identities shared by history producers/workers."""

import re


def normalize_history_source_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", value):
        raise ValueError("recent_history_source_id must be a bounded non-empty source label")
    return value
