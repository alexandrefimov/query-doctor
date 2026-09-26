"""Raw-free suspicion scoring for Recent query summaries."""

from __future__ import annotations

from dataclasses import dataclass

from query_doctor.cm.models import CMQuerySummary
from query_doctor.cm.query_discovery import (
    CANCELLED_QUERY_STATUSES,
    FAILED_QUERY_STATUSES,
    is_running_query_summary,
)


GIB = 1024**3
TIB = 1024**4
# A query that reads this much and returns this few rows is worth a profile
# even when it is short. The thresholds match large_scan_waste, the same rule
# applied after the profile is fetched.
LARGE_READ_SMALL_RESULT_MIN_BYTES = 10 * GIB
LARGE_READ_SMALL_RESULT_MAX_ROWS = 100_000
# Only these query types return rows to a client; a DML or CTAS that reads a
# lot and returns nothing is ordinary ETL.
ROW_RETURNING_QUERY_TYPES = frozenset({"QUERY", "SELECT"})


@dataclass(frozen=True)
class SummarySuspicionScore:
    score: int
    level: str
    reasons: tuple[str, ...]


def score_recent_summary_suspicion(summary: CMQuerySummary) -> SummarySuspicionScore:
    """Score a query summary as a profile-fetch candidate.

    This is deliberately a profile-budget signal, not a diagnosis. It uses only
    bounded summary fields and emits reason-code labels that are safe to retain
    and aggregate without raw SQL or profile text.
    """

    score = 0
    reasons: list[str] = []
    status = (summary.status or "").strip().lower()
    if status in FAILED_QUERY_STATUSES:
        score += 100
        reasons.append("failed_or_error_status")
    elif status in CANCELLED_QUERY_STATUSES:
        score += 70
        reasons.append("cancelled_status")

    if is_running_query_summary(summary):
        score += 35
        reasons.append("running_status")

    duration_sec = summary.duration_sec
    if duration_sec is not None:
        if duration_sec >= 3600:
            score += 70
            reasons.append("duration_ge_1h")
        elif duration_sec >= 1800:
            score += 50
            reasons.append("duration_ge_30m")
        elif duration_sec >= 600:
            score += 30
            reasons.append("duration_ge_10m")
        elif duration_sec >= 120:
            score += 15
            reasons.append("duration_ge_2m")

    admission_wait_ms = _nonnegative_int(summary.admission_wait_ms)
    if admission_wait_ms is not None:
        if admission_wait_ms >= 300_000:
            score += 25
            reasons.append("admission_wait_ge_5m")
        elif admission_wait_ms >= 60_000:
            score += 15
            reasons.append("admission_wait_ge_1m")

    memory_peak = max(
        value
        for value in (
            _nonnegative_int(summary.memory_aggregate_peak),
            _nonnegative_int(summary.memory_per_node_peak),
            0,
        )
        if value is not None
    )
    if memory_peak >= 100 * GIB:
        score += 25
        reasons.append("memory_peak_ge_100gib")
    elif memory_peak >= 10 * GIB:
        score += 10
        reasons.append("memory_peak_ge_10gib")

    bytes_read = _nonnegative_int(summary.bytes_read)
    if bytes_read is not None:
        if bytes_read >= TIB:
            score += 25
            reasons.append("bytes_read_ge_1tib")
        elif bytes_read >= 100 * GIB:
            score += 10
            reasons.append("bytes_read_ge_100gib")

    rows_produced = _nonnegative_int(summary.rows_produced)
    if (
        bytes_read is not None
        and rows_produced is not None
        and bytes_read >= LARGE_READ_SMALL_RESULT_MIN_BYTES
        and rows_produced <= LARGE_READ_SMALL_RESULT_MAX_ROWS
        and (summary.query_type or "").strip().upper() in ROW_RETURNING_QUERY_TYPES
    ):
        score += 20
        reasons.append("large_read_small_result")

    return SummarySuspicionScore(
        score=score,
        level=suspicion_level(score),
        reasons=tuple(reasons),
    )


def suspicion_level(score: int) -> str:
    if score >= 100:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 35:
        return "medium"
    if score >= 10:
        return "low"
    return "none"


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
