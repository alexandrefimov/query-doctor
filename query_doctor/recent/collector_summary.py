"""Raw-free Recent summary collector run summary contract."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


SUMMARY_KIND = "query_doctor_recent_history_collector_v1"
MAX_PREVIOUS_SUMMARY_BYTES = 64 * 1024
QUERY_LOG_CONTINUITY_STATUSES = frozenset(
    {"not_applicable", "below_capacity", "confirmed", "gap_detected", "unproven"}
)
STATUS_RECORDED = "recorded"
STATUS_IDLE = "idle"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"
STATUS_UNKNOWN = "unknown"
COLLECTOR_STATUSES = frozenset(
    {
        STATUS_RECORDED,
        STATUS_IDLE,
        STATUS_WARNING,
        STATUS_FAILED,
        STATUS_DISABLED,
        STATUS_UNKNOWN,
    }
)


def collector_observed_at(now: datetime | None = None) -> str:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    else:
        observed = observed.astimezone(timezone.utc)
    return observed.replace(microsecond=0).isoformat()


def parse_collector_observed_at(value: object) -> datetime | None:
    """Read back a collector observation time, or None when it is unusable."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def collector_status(
    *,
    discovery_failed: bool,
    recent_history_status: str,
    recent_history_backend: str,
    candidates_discovered: int,
    summaries_recorded: int,
    profile_jobs_planned: int,
    query_log_continuity_status: str = "not_applicable",
) -> str:
    if discovery_failed:
        return STATUS_FAILED
    if recent_history_backend == "disabled":
        return STATUS_DISABLED
    if recent_history_status == STATUS_WARNING:
        return STATUS_WARNING
    if query_log_continuity_status in {"gap_detected", "unproven"}:
        return STATUS_WARNING
    if candidates_discovered <= 0 and summaries_recorded <= 0 and profile_jobs_planned <= 0:
        return STATUS_IDLE
    return STATUS_RECORDED


def collector_issue_codes(
    *,
    status: str,
    recent_history_status: str,
    query_log_continuity_status: str = "not_applicable",
) -> list[str]:
    issues: list[str] = []
    if status == STATUS_FAILED:
        issues.append("discovery_failed")
    if status == STATUS_DISABLED:
        issues.append("recent_history_disabled")
    if recent_history_status == STATUS_WARNING:
        issues.append("recent_history_warning")
    if query_log_continuity_status == "gap_detected":
        issues.append("impala_query_log_gap_detected")
    elif query_log_continuity_status == "unproven":
        issues.append("impala_query_log_continuity_unproven")
    return issues


def collector_summary_payload(
    *,
    status: str,
    observed_at_iso: str,
    discover_only: bool,
    recent_history_backend: str,
    summaries_inspected: int,
    candidates_discovered: int,
    selected_count: int,
    summaries_recorded: int,
    profile_jobs_planned: int,
    query_log_at_capacity: bool = False,
    query_log_continuity_status: str = "not_applicable",
    issue_codes: Sequence[str] = (),
) -> dict[str, object]:
    safe_status = status if status in COLLECTOR_STATUSES else STATUS_UNKNOWN
    payload: dict[str, object] = {
        "summary_kind": SUMMARY_KIND,
        "status": safe_status,
        "observed_at_iso": str(observed_at_iso or "")[:64],
        "discover_only": bool(discover_only),
        "history_backend": _safe_backend(recent_history_backend),
        "summaries_inspected": _nonnegative_int(summaries_inspected),
        "candidates_discovered": _nonnegative_int(candidates_discovered),
        "selected_count": _nonnegative_int(selected_count),
        "summaries_recorded": _nonnegative_int(summaries_recorded),
        "profile_jobs_planned": _nonnegative_int(profile_jobs_planned),
        "issue_codes": _safe_issue_codes(issue_codes),
        "raw_output": False,
        "sensitive_value_echo": False,
    }
    payload["query_log_at_capacity"] = bool(query_log_at_capacity)
    payload["query_log_continuity_status"] = safe_query_log_continuity_status(
        query_log_continuity_status
    )
    return payload


def query_log_continuity_status(
    *,
    direct_impala: bool,
    query_log_at_capacity: bool,
    oldest_completed_at_iso: object,
    previous_summary: Mapping[str, object] | None,
) -> str:
    if not direct_impala:
        return "not_applicable"
    if not query_log_at_capacity:
        return "below_capacity"
    oldest_completed_at = parse_collector_observed_at(oldest_completed_at_iso)
    previous_observed_at = parse_collector_observed_at(
        previous_summary.get("observed_at_iso") if previous_summary is not None else None
    )
    if oldest_completed_at is None or previous_observed_at is None:
        return "unproven"
    if oldest_completed_at <= previous_observed_at:
        return "confirmed"
    return "gap_detected"


def read_previous_collector_summary(path: Path) -> Mapping[str, object] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_PREVIOUS_SUMMARY_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_PREVIOUS_SUMMARY_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping) or payload.get("summary_kind") != SUMMARY_KIND:
        return None
    if payload.get("raw_output") is not False or payload.get("sensitive_value_echo") is not False:
        return None
    return payload


def collector_summary_payload_json(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True) + "\n"


def write_collector_summary(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(collector_summary_payload_json(payload), encoding="utf-8")


def _safe_backend(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"disabled", "sqlite", "postgres"} else "unknown"


def safe_query_log_continuity_status(value: object) -> str:
    status = str(value or "").strip().lower()
    return status if status in QUERY_LOG_CONTINUITY_STATUSES else "unproven"


def _safe_issue_codes(values: Sequence[str]) -> list[str]:
    codes: list[str] = []
    for value in values:
        code = "".join(
            char if ("a" <= char <= "z" or "0" <= char <= "9" or char in {"_", "-"}) else "_"
            for char in str(value or "").strip().lower()[:64]
        )
        if code and code not in codes:
            codes.append(code)
    return codes[:5]


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
