"""Fixed, raw-free reason codes for Recent history persistence failures."""

from __future__ import annotations

import logging
from typing import Literal


_LOG = logging.getLogger(__name__)
_POSTGRES_REASONS = {
    "23502": "postgres_not_null_violation",
    "23505": "postgres_unique_violation",
    "40001": "postgres_serialization_failure",
    "40P01": "postgres_deadlock",
    "42501": "postgres_permission_denied",
    "42P01": "postgres_relation_missing",
    "53300": "postgres_too_many_connections",
    "57014": "postgres_query_canceled",
}


def _failure_reason(error: BaseException) -> str:
    # Backends wrap driver errors with an explicit cause. Never stringify either.
    for _ in range(4):
        state = getattr(error, "sqlstate", None)
        if isinstance(state, str):
            if state in _POSTGRES_REASONS:
                return _POSTGRES_REASONS[state]
            if len(state) == 5 and state.startswith("08"):
                return "postgres_connection_failure"
        if isinstance(error, PermissionError):
            return "storage_permission_denied"
        if isinstance(error, TimeoutError):
            return "storage_timeout"
        if error.__cause__ is None:
            break
        error = error.__cause__
    return "history_store_error"


def log_history_persistence_failure(
    error: BaseException,
    *,
    operation: Literal["store_initialize", "summary_upsert"],
) -> None:
    _LOG.warning(
        "recent_history_persistence_failed operation=%s reason_code=%s",
        operation,
        _failure_reason(error),
    )
