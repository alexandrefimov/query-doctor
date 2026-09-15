"""Raw-free configured operator-readiness projection for Online History."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from query_doctor.recent.batch_config import expand_optional_path
from query_doctor.recent.collector_summary import COLLECTOR_STATUSES
from query_doctor.recent.operator_readiness import (
    SUMMARY_KIND as OPERATOR_READINESS_SUMMARY_KIND,
    recent_summary_collector_next_step,
    recent_profile_remediation_next_step,
    unsafe_summary_payload,
)
from query_doctor.recent.profile_worker import (
    recent_profile_backlog_next_step,
    recent_profile_worker_next_step,
)
from query_doctor.web.config import optional_config_string


OPERATOR_READINESS_SUMMARY_CONFIG_KEY = "recent_history_operator_readiness_summary_json"
MAX_OPERATOR_READINESS_ISSUE_CODES = 3
PROFILE_BACKLOG_HEALTH_KEYS = (
    "pending_jobs",
    "retry_pending_jobs",
    "leased_jobs",
    "stale_leased_jobs",
    "failed_jobs",
)
OPERATOR_READINESS_ISSUE_CODES = frozenset(
    {
        "postgres_readiness_summary_missing",
        "postgres_readiness_summary_unsafe",
        "postgres_readiness_summary_kind_drift",
        "postgres_readiness_summary_status_not_ready",
        "postgres_readiness_summary_raw_free_flags_failed",
        "postgres_readiness_summary_schema_initialized_not_ready",
        "profile_worker_summary_missing",
        "profile_worker_summary_unsafe",
        "profile_worker_summary_kind_drift",
        "profile_worker_summary_status_not_ready",
        "profile_worker_summary_raw_free_flags_failed",
        "profile_worker_summary_backlog_health_missing",
        "profile_worker_backlog_failed_jobs",
        "collector_summary_unsafe",
        "collector_summary_kind_drift",
        "collector_summary_status_not_ready",
        "collector_summary_raw_free_flags_failed",
        "retention_summary_missing",
        "retention_summary_unsafe",
        "retention_summary_kind_drift",
        "retention_summary_status_not_ready",
        "retention_summary_raw_free_flags_failed",
        "profile_remediation_summary_unsafe",
        "profile_remediation_summary_kind_drift",
        "profile_remediation_summary_status_not_ready",
        "profile_remediation_summary_raw_free_flags_failed",
        "summary_kind_drift",
        "summary_raw_free_flags_failed",
        "summary_unavailable",
        "summary_unsafe",
        "unknown_issue",
    }
)


def operator_readiness_summary_from_config(
    config_values: dict[str, object],
    *,
    cwd: Path,
) -> dict[str, object] | None:
    configured_path = expand_optional_path(
        optional_config_string(config_values, OPERATOR_READINESS_SUMMARY_CONFIG_KEY),
        cwd=cwd,
    )
    if configured_path is None:
        return None
    try:
        payload = json.loads(configured_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _operator_readiness_unavailable("summary_unavailable")
    if not isinstance(payload, Mapping):
        return _operator_readiness_unavailable("summary_unavailable")
    return project_operator_readiness_summary(payload)


def project_operator_readiness_summary(payload: Mapping[str, object]) -> dict[str, object]:
    if unsafe_summary_payload(payload):
        return _operator_readiness_unavailable(
            "summary_unsafe",
            status="blocked",
        )
    if payload.get("summary_kind") != OPERATOR_READINESS_SUMMARY_KIND:
        return _operator_readiness_unavailable(
            "summary_kind_drift",
            status="blocked",
        )
    if payload.get("raw_output") is True or payload.get("sensitive_value_echo") is True:
        return _operator_readiness_unavailable(
            "summary_raw_free_flags_failed",
            status="blocked",
        )
    operations = _mapping(payload.get("operations"))
    return {
        "status": _operator_readiness_status(payload.get("status")),
        "accepted_summary_count": _safe_nonnegative_int(payload.get("accepted_summary_count")),
        "evidence_summary_count": _safe_nonnegative_int(payload.get("evidence_summary_count")),
        "collector_summary_present": payload.get("collector_summary_present") is True,
        "retention_summary_present": payload.get("retention_summary_present") is True,
        "remediation_summary_present": payload.get("remediation_summary_present") is True,
        "issue_count": _safe_list_count(payload.get("issue_codes")),
        "issue_codes": _safe_operator_issue_codes(payload.get("issue_codes")),
        "operations": {
            "postgres_readiness": _project_postgres_readiness_operations(
                _mapping(operations.get("postgres_readiness"))
            ),
            "profile_worker": _project_profile_worker_operations(
                _mapping(operations.get("profile_worker"))
            ),
            "collector_summary": _project_collector_summary_operations(
                _mapping(operations.get("collector_summary"))
            ),
            "retention": _project_retention_operations(_mapping(operations.get("retention"))),
            "profile_remediation": _project_profile_remediation_operations(
                _mapping(operations.get("profile_remediation"))
            ),
        },
    }


def _operator_readiness_unavailable(
    issue_code: str,
    *,
    status: str = "unavailable",
) -> dict[str, object]:
    return {
        "status": status,
        "accepted_summary_count": 0,
        "evidence_summary_count": 0,
        "collector_summary_present": False,
        "retention_summary_present": False,
        "remediation_summary_present": False,
        "issue_count": 1,
        "operations": {},
        "issue_codes": _safe_operator_issue_codes([issue_code]),
    }


def _project_postgres_readiness_operations(payload: Mapping[str, object]) -> dict[str, object]:
    accepted = payload.get("accepted") is True
    return {
        "accepted": accepted,
        "status": _operator_readiness_status(payload.get("status")) if accepted else "unknown",
        "schema_initialized": payload.get("schema_initialized") is True,
        "check_count": _safe_nonnegative_int(payload.get("check_count")),
        "issue_count": _safe_nonnegative_int(payload.get("issue_count")),
    }


def _project_profile_worker_operations(payload: Mapping[str, object]) -> dict[str, object]:
    accepted = payload.get("accepted") is True
    jobs_claimed = _safe_nonnegative_int(payload.get("jobs_claimed"))
    jobs_completed = _safe_nonnegative_int(payload.get("jobs_completed"))
    jobs_retried = _safe_nonnegative_int(payload.get("jobs_retried"))
    jobs_failed = _safe_nonnegative_int(payload.get("jobs_failed"))
    jobs_lease_lost = _safe_nonnegative_int(payload.get("jobs_lease_lost"))
    analysis_cache_records = _safe_nonnegative_int(payload.get("analysis_cache_records"))
    profile_artifact_records = _safe_nonnegative_int(payload.get("profile_artifact_records"))
    profile_backlog_health = _project_profile_backlog_health(
        _mapping(payload.get("profile_backlog_health"))
    )
    return {
        "accepted": accepted,
        "status": _operator_readiness_status(payload.get("status")) if accepted else "unknown",
        "jobs_claimed": jobs_claimed,
        "jobs_completed": jobs_completed,
        "jobs_retried": jobs_retried,
        "jobs_failed": jobs_failed,
        "jobs_lease_lost": jobs_lease_lost,
        "analysis_cache_records": analysis_cache_records,
        "profile_artifact_records": profile_artifact_records,
        "profile_backlog_health_present": bool(profile_backlog_health),
        "profile_backlog_health": profile_backlog_health,
        "issue_count": _safe_nonnegative_int(payload.get("issue_count")),
        "next_step": recent_profile_worker_next_step(
            jobs_claimed=jobs_claimed,
            jobs_completed=jobs_completed,
            jobs_retried=jobs_retried,
            jobs_failed=jobs_failed,
            jobs_lease_lost=jobs_lease_lost,
            analysis_cache_records=analysis_cache_records,
            profile_artifact_records=profile_artifact_records,
        ),
        "profile_backlog_next_step": recent_profile_backlog_next_step(**profile_backlog_health)
        if profile_backlog_health
        else "",
    }


def _project_profile_backlog_health(payload: Mapping[str, object]) -> dict[str, int]:
    if not all(key in payload for key in PROFILE_BACKLOG_HEALTH_KEYS):
        return {}
    return {
        "pending_jobs": _safe_nonnegative_int(payload.get("pending_jobs")),
        "retry_pending_jobs": _safe_nonnegative_int(payload.get("retry_pending_jobs")),
        "leased_jobs": _safe_nonnegative_int(payload.get("leased_jobs")),
        "stale_leased_jobs": _safe_nonnegative_int(payload.get("stale_leased_jobs")),
        "failed_jobs": _safe_nonnegative_int(payload.get("failed_jobs")),
    }


def _project_collector_summary_operations(payload: Mapping[str, object]) -> dict[str, object]:
    present = payload.get("present") is True
    accepted = payload.get("accepted") is True
    status = _collector_status(payload.get("status")) if present else "unknown"
    summaries_recorded = _safe_nonnegative_int(payload.get("summaries_recorded"))
    profile_jobs_planned = _safe_nonnegative_int(payload.get("profile_jobs_planned"))
    return {
        "present": present,
        "accepted": accepted and status in {"recorded", "idle"},
        "status": status,
        "observed_at_iso": _safe_observed_at(payload.get("observed_at_iso")) if present else "",
        "discover_only": payload.get("discover_only") is True,
        "history_backend": _collector_history_backend(payload.get("history_backend")),
        "summaries_inspected": _safe_nonnegative_int(payload.get("summaries_inspected")),
        "candidates_discovered": _safe_nonnegative_int(payload.get("candidates_discovered")),
        "selected_count": _safe_nonnegative_int(payload.get("selected_count")),
        "summaries_recorded": summaries_recorded,
        "profile_jobs_planned": profile_jobs_planned,
        "issue_count": _safe_nonnegative_int(payload.get("issue_count")),
        "next_step": recent_summary_collector_next_step(
            status=status,
            summaries_recorded=summaries_recorded,
            profile_jobs_planned=profile_jobs_planned,
        )
        if present
        else "",
    }


def _project_retention_operations(payload: Mapping[str, object]) -> dict[str, object]:
    accepted = payload.get("accepted") is True
    return {
        "present": payload.get("present") is True,
        "accepted": accepted,
        "status": _operator_readiness_status(payload.get("status")) if accepted else "unknown",
        "summaries_deleted": _safe_nonnegative_int(payload.get("summaries_deleted")),
        "profile_jobs_deleted": _safe_nonnegative_int(payload.get("profile_jobs_deleted")),
        "analysis_cache_deleted": _safe_nonnegative_int(payload.get("analysis_cache_deleted")),
        "profile_artifacts_deleted": _safe_nonnegative_int(
            payload.get("profile_artifacts_deleted")
        ),
        "total_deleted": _safe_nonnegative_int(payload.get("total_deleted")),
        "issue_count": _safe_nonnegative_int(payload.get("issue_count")),
    }


def _project_profile_remediation_operations(payload: Mapping[str, object]) -> dict[str, object]:
    accepted = payload.get("accepted") is True
    status = _operator_readiness_status(payload.get("status")) if accepted else "unknown"
    selected_failed_jobs = _safe_nonnegative_int(payload.get("selected_failed_jobs"))
    requeued_jobs = _safe_nonnegative_int(payload.get("requeued_jobs"))
    return {
        "present": payload.get("present") is True,
        "accepted": accepted,
        "status": status,
        "mode": _profile_remediation_mode(payload.get("mode"), status=status),
        "matched_failed_jobs": _safe_nonnegative_int(payload.get("matched_failed_jobs")),
        "selected_failed_jobs": selected_failed_jobs,
        "requeued_jobs": requeued_jobs,
        "skipped_due_to_limit": _safe_nonnegative_int(payload.get("skipped_due_to_limit")),
        "issue_count": _safe_nonnegative_int(payload.get("issue_count")),
        "next_step": recent_profile_remediation_next_step(
            status=status,
            selected_failed_jobs=selected_failed_jobs,
            requeued_jobs=requeued_jobs,
        ),
    }


def _profile_remediation_mode(value: object, *, status: str) -> str:
    mode = _safe_label(value)
    if mode in {"dry_run", "apply"}:
        return mode
    if status == "applied":
        return "apply"
    if status == "dry_run":
        return "dry_run"
    return "unknown"


def _operator_readiness_status(value: object) -> str:
    status = _safe_label(value)
    if status in {"ready", "blocked", "unavailable", "done", "pruned", "dry_run", "applied"}:
        return status
    return "unknown"


def _collector_status(value: object) -> str:
    status = _safe_label(value)
    return status if status in COLLECTOR_STATUSES else "unknown"


def _collector_history_backend(value: object) -> str:
    backend = _safe_label(value)
    return backend if backend in {"disabled", "sqlite", "postgres"} else "unknown"


def _safe_observed_at(value: object) -> str:
    return str(value or "").strip()[:64]


def project_operator_readiness_issue_code(value: object) -> str:
    code = _safe_label(value)
    if code in OPERATOR_READINESS_ISSUE_CODES:
        return code
    return "unknown_issue"


def _safe_operator_issue_codes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    issue_codes: list[str] = []
    for item in value:
        code = project_operator_readiness_issue_code(item)
        if code not in issue_codes:
            issue_codes.append(code)
        if len(issue_codes) >= MAX_OPERATOR_READINESS_ISSUE_CODES:
            break
    return issue_codes


def _safe_label(value: object) -> str:
    text = str(value or "").strip().lower()[:64]
    if not text:
        return "unknown"
    return "".join(
        char if ("a" <= char <= "z" or "0" <= char <= "9" or char in {"_", "-"}) else "_"
        for char in text
    )


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _safe_list_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
