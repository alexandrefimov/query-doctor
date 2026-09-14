"""Raw-free retained readiness evidence audit for Recent history operations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from query_doctor.recent.collector_summary import (
    COLLECTOR_STATUSES,
    STATUS_IDLE as COLLECTOR_STATUS_IDLE,
    STATUS_RECORDED as COLLECTOR_STATUS_RECORDED,
    SUMMARY_KIND as COLLECTOR_SUMMARY_KIND,
    parse_collector_observed_at,
    safe_query_log_continuity_status,
)
from query_doctor.recent.history_store import safe_label
from query_doctor.recent.postgres_readiness import SUMMARY_KIND as POSTGRES_READINESS_SUMMARY_KIND
from query_doctor.recent.profile_worker import (
    RECENT_PROFILE_WORKER_SUMMARY_KIND,
    recent_profile_backlog_next_step,
)
from query_doctor.recent.profile_worker import recent_profile_worker_next_step


SUMMARY_KIND = "query_doctor_recent_history_operator_readiness_v1"
RETENTION_SUMMARY_KIND = "query_doctor_recent_history_retention_v1"
REMEDIATION_SUMMARY_KIND = "query_doctor_recent_profile_remediation_v1"
STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"
CHECK_READY = "ready"
CHECK_BLOCKED = "blocked"
COLLECTOR_READY_STATUSES = frozenset({COLLECTOR_STATUS_RECORDED, COLLECTOR_STATUS_IDLE})
PROFILE_BACKLOG_HEALTH_KEYS = (
    "pending_jobs",
    "retry_pending_jobs",
    "leased_jobs",
    "stale_leased_jobs",
    "failed_jobs",
)

_FORBIDDEN_KEYS = {
    "artifact_filename",
    "case_dir",
    "command_output",
    "dsn",
    "local_path",
    "model",
    "password",
    "profile_text",
    "query_id",
    "raw_metadata",
    "raw_profile",
    "raw_sql",
    "secret",
    "stderr",
    "stdout",
}
_FORBIDDEN_VALUE_MARKERS = (
    "postgres://",
    "postgresql://",
    "jdbc:",
    "password=",
    "secret",
    "/users/",
    "/private/",
    "/var/folders/",
    "\\users\\",
)


@dataclass(frozen=True)
class RecentHistoryOperatorReadinessResult:
    status: str
    checks: tuple[dict[str, str], ...]
    issue_codes: tuple[str, ...]
    evidence_summary_count: int
    accepted_summary_count: int
    collector_summary_present: bool = False
    retention_summary_present: bool = False
    remediation_summary_present: bool = False
    operations_summary: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "summary_kind": SUMMARY_KIND,
            "status": self.status,
            "checks": list(self.checks),
            "issue_codes": list(self.issue_codes),
            "evidence_summary_count": self.evidence_summary_count,
            "accepted_summary_count": self.accepted_summary_count,
            "collector_summary_present": self.collector_summary_present,
            "retention_summary_present": self.retention_summary_present,
            "remediation_summary_present": self.remediation_summary_present,
            "operations": dict(self.operations_summary),
            "raw_output": False,
            "sensitive_value_echo": False,
        }


def audit_recent_history_operator_readiness(
    *,
    postgres_readiness_summary: Mapping[str, Any] | None,
    profile_worker_summary: Mapping[str, Any] | None,
    collector_summary: Mapping[str, Any] | None = None,
    retention_summary: Mapping[str, Any] | None = None,
    remediation_summary: Mapping[str, Any] | None = None,
    max_evidence_age_minutes: int | None = None,
    now: datetime | None = None,
) -> RecentHistoryOperatorReadinessResult:
    checks: list[dict[str, str]] = []
    issues: list[str] = []
    accepted = 0

    postgres_accepted = audit_required_summary(
        checks,
        issues,
        check_id="postgres_readiness_summary",
        summary=postgres_readiness_summary,
        expected_kind=POSTGRES_READINESS_SUMMARY_KIND,
        expected_status="ready",
        extra_requirements=(("schema_initialized", True),),
    )
    accepted += postgres_accepted
    worker_accepted = audit_required_summary(
        checks,
        issues,
        check_id="profile_worker_summary",
        summary=profile_worker_summary,
        expected_kind=RECENT_PROFILE_WORKER_SUMMARY_KIND,
        expected_status="done",
    )
    if worker_accepted and not profile_backlog_health_present(profile_worker_summary):
        checks.append(
            readiness_check(
                "profile_backlog_health",
                CHECK_BLOCKED,
                "Profile backlog health is missing from retained worker summary",
            )
        )
        issues.append("profile_worker_summary_backlog_health_missing")
        worker_accepted = 0
    elif worker_accepted:
        checks.append(
            readiness_check(
                "profile_backlog_health",
                CHECK_READY,
                "Profile backlog health accepted",
            )
        )
        audit_profile_backlog_health(checks, issues, profile_worker_summary)
    accepted += worker_accepted
    collector_present = collector_summary is not None
    if collector_present:
        collector_accepted, collector_projectable = audit_optional_collector_summary(
            checks,
            issues,
            collector_summary,
        )
        accepted += collector_accepted
    else:
        collector_accepted = 0
        collector_projectable = False
        checks.append(
            readiness_check(
                "collector_summary",
                CHECK_READY,
                "Optional collector summary absent",
            )
        )
    if max_evidence_age_minutes is not None:
        audit_collector_summary_freshness(
            checks,
            issues,
            collector_summary,
            max_age_minutes=max_evidence_age_minutes,
            now=now,
        )
        audit_profile_worker_summary_freshness(
            checks,
            issues,
            profile_worker_summary,
            max_age_minutes=max_evidence_age_minutes,
            now=now,
        )
    retention_present = retention_summary is not None
    if retention_present:
        retention_accepted = audit_required_summary(
            checks,
            issues,
            check_id="retention_summary",
            summary=retention_summary,
            expected_kind=RETENTION_SUMMARY_KIND,
            expected_status="pruned",
        )
        accepted += retention_accepted
    else:
        retention_accepted = 0
        checks.append(
            readiness_check("retention_summary", CHECK_READY, "Optional retention summary absent")
        )
    remediation_present = remediation_summary is not None
    if remediation_present:
        remediation_accepted = audit_optional_remediation_summary(
            checks,
            issues,
            remediation_summary,
        )
        accepted += remediation_accepted
    else:
        remediation_accepted = 0
        checks.append(
            readiness_check(
                "profile_remediation_summary",
                CHECK_READY,
                "Optional profile remediation summary absent",
            )
        )

    operations_summary = build_operations_summary(
        postgres_readiness_summary if postgres_accepted else None,
        profile_worker_summary if worker_accepted else None,
        collector_summary if collector_projectable else None,
        retention_summary if retention_accepted else None,
        remediation_summary if remediation_accepted else None,
        collector_present=collector_present,
        retention_present=retention_present,
        remediation_present=remediation_present,
    )

    return RecentHistoryOperatorReadinessResult(
        status=STATUS_BLOCKED if issues else STATUS_READY,
        checks=tuple(checks),
        issue_codes=tuple(dict.fromkeys(issues)),
        evidence_summary_count=(
            2 + int(collector_present) + int(retention_present) + int(remediation_present)
        ),
        accepted_summary_count=accepted,
        collector_summary_present=collector_present,
        retention_summary_present=retention_present,
        remediation_summary_present=remediation_present,
        operations_summary=operations_summary,
    )


def audit_collector_summary_freshness(
    checks: list[dict[str, str]],
    issues: list[str],
    summary: Mapping[str, Any] | None,
    *,
    max_age_minutes: int,
    now: datetime | None = None,
) -> None:
    """Block on evidence that has stopped moving.

    Every other check reads a retained payload and asks whether its contents are
    acceptable. A producer that stops writing keeps the last acceptable payload
    on disk, so those checks keep passing while nothing is being collected. This
    is the one check that compares the evidence against the clock.
    """

    audit_summary_freshness(
        checks,
        issues,
        summary,
        check_id="collector_summary_freshness",
        producer_label="collector",
        max_age_minutes=max_age_minutes,
        now=now,
    )


def audit_profile_worker_summary_freshness(
    checks: list[dict[str, str]],
    issues: list[str],
    summary: Mapping[str, Any] | None,
    *,
    max_age_minutes: int,
    now: datetime | None = None,
) -> None:
    audit_summary_freshness(
        checks,
        issues,
        summary,
        check_id="profile_worker_summary_freshness",
        producer_label="profile worker",
        max_age_minutes=max_age_minutes,
        now=now,
    )


def audit_summary_freshness(
    checks: list[dict[str, str]],
    issues: list[str],
    summary: Mapping[str, Any] | None,
    *,
    check_id: str,
    producer_label: str,
    max_age_minutes: int,
    now: datetime | None = None,
) -> None:

    if summary is None:
        checks.append(
            readiness_check(
                check_id,
                CHECK_BLOCKED,
                f"No {producer_label} summary to age-check against the accepted evidence age",
            )
        )
        issues.append(f"{check_id}_absent")
        return
    observed_at = parse_collector_observed_at(summary.get("observed_at_iso"))
    if observed_at is None:
        checks.append(
            readiness_check(
                check_id,
                CHECK_BLOCKED,
                f"Retained {producer_label} summary carries no readable observation time",
            )
        )
        issues.append(f"{check_id}_observed_at_unreadable")
        return
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    else:
        effective_now = effective_now.astimezone(timezone.utc)
    age_minutes = (effective_now - observed_at).total_seconds() / 60
    if age_minutes > max_age_minutes:
        checks.append(
            readiness_check(
                check_id,
                CHECK_BLOCKED,
                f"Retained {producer_label} summary is older than the accepted evidence age",
            )
        )
        issues.append(f"{check_id}_stale")
        return
    checks.append(
        readiness_check(
            check_id,
            CHECK_READY,
            f"Retained {producer_label} summary is within the accepted age",
        )
    )


def audit_profile_backlog_health(
    checks: list[dict[str, str]],
    issues: list[str],
    summary: Mapping[str, Any],
) -> None:
    backlog = safe_profile_backlog_health(summary.get("profile_backlog_health"))
    if backlog.get("stale_leased_jobs", 0):
        checks.append(
            readiness_check(
                "profile_backlog_stale_leases",
                CHECK_BLOCKED,
                "Profile backlog contains stale leases",
            )
        )
        issues.append("profile_worker_backlog_stale_leases")
    else:
        checks.append(
            readiness_check(
                "profile_backlog_stale_leases",
                CHECK_READY,
                "Profile backlog contains no stale leases",
            )
        )
    if backlog.get("failed_jobs", 0):
        checks.append(
            readiness_check(
                "profile_backlog_failed_jobs",
                CHECK_BLOCKED,
                "Profile backlog contains terminal failed jobs",
            )
        )
        issues.append("profile_worker_backlog_failed_jobs")
    else:
        checks.append(
            readiness_check(
                "profile_backlog_failed_jobs",
                CHECK_READY,
                "Profile backlog contains no terminal failed jobs",
            )
        )


def audit_optional_collector_summary(
    checks: list[dict[str, str]],
    issues: list[str],
    summary: Mapping[str, Any],
) -> tuple[int, bool]:
    check_id = "collector_summary"
    if unsafe_summary_payload(summary):
        checks.append(
            readiness_check(
                check_id, CHECK_BLOCKED, "Retained summary contains unsafe fields or values"
            )
        )
        issues.append(f"{check_id}_unsafe")
        return 0, False
    if summary.get("summary_kind") != COLLECTOR_SUMMARY_KIND:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary kind is not accepted")
        )
        issues.append(f"{check_id}_kind_drift")
        return 0, False
    if summary.get("raw_output") is True or summary.get("sensitive_value_echo") is True:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary raw-free flags failed")
        )
        issues.append(f"{check_id}_raw_free_flags_failed")
        return 0, False
    status = collector_summary_status(summary.get("status"))
    if status not in COLLECTOR_READY_STATUSES:
        checks.append(
            readiness_check(
                check_id, CHECK_BLOCKED, "Retained collector summary status is not ready"
            )
        )
        issues.append(f"{check_id}_status_not_ready")
        return 0, True
    checks.append(readiness_check(check_id, CHECK_READY, "Retained summary accepted"))
    return 1, True


def audit_optional_remediation_summary(
    checks: list[dict[str, str]],
    issues: list[str],
    summary: Mapping[str, Any],
) -> int:
    check_id = "profile_remediation_summary"
    if unsafe_summary_payload(summary):
        checks.append(
            readiness_check(
                check_id, CHECK_BLOCKED, "Retained summary contains unsafe fields or values"
            )
        )
        issues.append(f"{check_id}_unsafe")
        return 0
    if summary.get("summary_kind") != REMEDIATION_SUMMARY_KIND:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary kind is not accepted")
        )
        issues.append(f"{check_id}_kind_drift")
        return 0
    if summary.get("status") not in {"dry_run", "applied"}:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary status is not ready")
        )
        issues.append(f"{check_id}_status_not_ready")
        return 0
    if summary.get("raw_output") is True or summary.get("sensitive_value_echo") is True:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary raw-free flags failed")
        )
        issues.append(f"{check_id}_raw_free_flags_failed")
        return 0
    checks.append(readiness_check(check_id, CHECK_READY, "Retained summary accepted"))
    return 1


def audit_required_summary(
    checks: list[dict[str, str]],
    issues: list[str],
    *,
    check_id: str,
    summary: Mapping[str, Any] | None,
    expected_kind: str,
    expected_status: str,
    extra_requirements: Sequence[tuple[str, object]] = (),
) -> int:
    if summary is None:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Required retained summary is missing")
        )
        issues.append(f"{check_id}_missing")
        return 0
    if unsafe_summary_payload(summary):
        checks.append(
            readiness_check(
                check_id, CHECK_BLOCKED, "Retained summary contains unsafe fields or values"
            )
        )
        issues.append(f"{check_id}_unsafe")
        return 0
    if summary.get("summary_kind") != expected_kind:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary kind is not accepted")
        )
        issues.append(f"{check_id}_kind_drift")
        return 0
    if summary.get("status") != expected_status:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary status is not ready")
        )
        issues.append(f"{check_id}_status_not_ready")
        return 0
    if summary.get("raw_output") is True or summary.get("sensitive_value_echo") is True:
        checks.append(
            readiness_check(check_id, CHECK_BLOCKED, "Retained summary raw-free flags failed")
        )
        issues.append(f"{check_id}_raw_free_flags_failed")
        return 0
    for field_name, expected_value in extra_requirements:
        if summary.get(field_name) != expected_value:
            checks.append(
                readiness_check(
                    check_id, CHECK_BLOCKED, "Retained summary required field is not ready"
                )
            )
            issues.append(f"{check_id}_{field_name}_not_ready")
            return 0
    checks.append(readiness_check(check_id, CHECK_READY, "Retained summary accepted"))
    return 1


def readiness_check(check_id: str, status: str, summary: str) -> dict[str, str]:
    return {
        "id": safe_label(check_id, default="unknown"),
        "status": safe_label(status, default=CHECK_BLOCKED),
        "summary": safe_summary(summary),
    }


def build_operations_summary(
    postgres_readiness_summary: Mapping[str, Any] | None,
    profile_worker_summary: Mapping[str, Any] | None,
    collector_summary: Mapping[str, Any] | None,
    retention_summary: Mapping[str, Any] | None,
    remediation_summary: Mapping[str, Any] | None,
    *,
    collector_present: bool,
    retention_present: bool,
    remediation_present: bool,
) -> dict[str, object]:
    return {
        "postgres_readiness": postgres_readiness_operations(postgres_readiness_summary),
        "profile_worker": profile_worker_operations(profile_worker_summary),
        "collector_summary": collector_summary_operations(
            collector_summary,
            present=collector_present,
        ),
        "retention": retention_operations(retention_summary, present=retention_present),
        "profile_remediation": profile_remediation_operations(
            remediation_summary,
            present=remediation_present,
        ),
    }


def collector_summary_operations(
    summary: Mapping[str, Any] | None,
    *,
    present: bool,
) -> dict[str, object]:
    if summary is None:
        return {"present": present, "accepted": False}
    status = collector_summary_status(summary.get("status"))
    profile_jobs_planned = safe_nonnegative_int(summary.get("profile_jobs_planned"))
    summaries_recorded = safe_nonnegative_int(summary.get("summaries_recorded"))
    candidates_discovered = safe_nonnegative_int(summary.get("candidates_discovered"))
    selected_count = safe_nonnegative_int(summary.get("selected_count"))
    return {
        "present": True,
        "accepted": status in COLLECTOR_READY_STATUSES,
        "status": status,
        "observed_at_iso": safe_observed_at(summary.get("observed_at_iso")),
        "discover_only": summary.get("discover_only") is True,
        "history_backend": collector_history_backend(summary.get("history_backend")),
        "summaries_inspected": safe_nonnegative_int(summary.get("summaries_inspected")),
        "candidates_discovered": candidates_discovered,
        "selected_count": selected_count,
        "summaries_recorded": summaries_recorded,
        "profile_jobs_planned": profile_jobs_planned,
        "query_log_at_capacity": summary.get("query_log_at_capacity") is True,
        "query_log_continuity_status": safe_query_log_continuity_status(
            summary.get("query_log_continuity_status")
        ),
        "issue_count": safe_list_count(summary.get("issue_codes")),
        "next_step": recent_summary_collector_next_step(
            status=status,
            summaries_recorded=summaries_recorded,
            profile_jobs_planned=profile_jobs_planned,
        ),
    }


def collector_summary_status(value: object) -> str:
    status = safe_label(value, default="unknown")
    return status if status in COLLECTOR_STATUSES else "unknown"


def collector_history_backend(value: object) -> str:
    backend = safe_label(value, default="unknown")
    return backend if backend in {"disabled", "sqlite", "postgres"} else "unknown"


def safe_observed_at(value: object) -> str:
    return str(value or "").strip()[:64]


def recent_summary_collector_next_step(
    *,
    status: str,
    summaries_recorded: int,
    profile_jobs_planned: int,
) -> str:
    if status == "recorded" and profile_jobs_planned > 0:
        return "Run the Recent profile worker to process planned profile jobs."
    if status == "recorded" and summaries_recorded > 0:
        return "Refresh Online History to inspect newly retained summaries."
    if status == "recorded":
        return "Inspect collector selection counters before the next scheduled run."
    if status == "idle":
        return "No Recent summaries matched the collector selection window."
    if status == "warning":
        return "Review collector warning reason codes before relying on scheduled intake."
    if status == "failed":
        return "Fix the collector discovery or runtime failure, then rerun the collector."
    if status == "disabled":
        return "Enable Recent history Postgres storage before scheduling collector intake."
    return "Rerun the collector after verifying configured Recent history inputs."


def postgres_readiness_operations(summary: Mapping[str, Any] | None) -> dict[str, object]:
    if summary is None:
        return {"accepted": False}
    checks = summary.get("checks")
    issue_codes = summary.get("issue_codes")
    return {
        "accepted": True,
        "status": safe_label(summary.get("status"), default="unknown"),
        "schema_initialized": summary.get("schema_initialized") is True,
        "check_count": safe_list_count(checks),
        "issue_count": safe_list_count(issue_codes),
    }


def profile_worker_operations(summary: Mapping[str, Any] | None) -> dict[str, object]:
    if summary is None:
        return {"accepted": False}
    issue_codes = summary.get("issue_codes")
    jobs_claimed = safe_nonnegative_int(summary.get("jobs_claimed"))
    jobs_completed = safe_nonnegative_int(summary.get("jobs_completed"))
    jobs_retried = safe_nonnegative_int(summary.get("jobs_retried"))
    jobs_failed = safe_nonnegative_int(summary.get("jobs_failed"))
    jobs_lease_lost = safe_nonnegative_int(summary.get("jobs_lease_lost"))
    analysis_cache_records = safe_nonnegative_int(summary.get("analysis_cache_records"))
    profile_artifact_records = safe_nonnegative_int(summary.get("profile_artifact_records"))
    backlog_health = safe_profile_backlog_health(summary.get("profile_backlog_health"))
    backlog_next_step = recent_profile_backlog_next_step(**backlog_health) if backlog_health else ""
    return {
        "accepted": True,
        "status": safe_label(summary.get("status"), default="unknown"),
        "observed_at_iso": safe_observed_at(summary.get("observed_at_iso")),
        "jobs_claimed": jobs_claimed,
        "jobs_completed": jobs_completed,
        "jobs_retried": jobs_retried,
        "jobs_failed": jobs_failed,
        "jobs_lease_lost": jobs_lease_lost,
        "analysis_cache_records": analysis_cache_records,
        "profile_artifact_records": profile_artifact_records,
        "profile_backlog_health_present": bool(backlog_health),
        "profile_backlog_health": backlog_health,
        "issue_count": safe_list_count(issue_codes),
        "next_step": recent_profile_worker_next_step(
            jobs_claimed=jobs_claimed,
            jobs_completed=jobs_completed,
            jobs_retried=jobs_retried,
            jobs_failed=jobs_failed,
            jobs_lease_lost=jobs_lease_lost,
            analysis_cache_records=analysis_cache_records,
            profile_artifact_records=profile_artifact_records,
        ),
        "profile_backlog_next_step": backlog_next_step,
    }


def retention_operations(
    summary: Mapping[str, Any] | None,
    *,
    present: bool,
) -> dict[str, object]:
    if summary is None:
        return {"present": present, "accepted": False}
    retention = summary.get("retention")
    retention_values = retention if isinstance(retention, Mapping) else {}
    return {
        "present": True,
        "accepted": True,
        "status": safe_label(summary.get("status"), default="unknown"),
        "summaries_deleted": safe_nonnegative_int(retention_values.get("summaries_deleted")),
        "profile_jobs_deleted": safe_nonnegative_int(retention_values.get("profile_jobs_deleted")),
        "analysis_cache_deleted": safe_nonnegative_int(
            retention_values.get("analysis_cache_deleted")
        ),
        "profile_artifacts_deleted": safe_nonnegative_int(
            retention_values.get("profile_artifacts_deleted")
        ),
        "total_deleted": safe_nonnegative_int(retention_values.get("total_deleted")),
        "issue_count": safe_list_count(summary.get("issue_codes")),
    }


def profile_remediation_operations(
    summary: Mapping[str, Any] | None,
    *,
    present: bool,
) -> dict[str, object]:
    if summary is None:
        return {"present": present, "accepted": False}
    remediation = summary.get("remediation")
    remediation_values = remediation if isinstance(remediation, Mapping) else {}
    status = safe_label(summary.get("status"), default="unknown")
    mode = safe_label(summary.get("mode"), default=status)
    matched_failed_jobs = safe_nonnegative_int(remediation_values.get("matched_failed_jobs"))
    selected_failed_jobs = safe_nonnegative_int(remediation_values.get("selected_failed_jobs"))
    requeued_jobs = safe_nonnegative_int(remediation_values.get("requeued_jobs"))
    skipped_due_to_limit = safe_nonnegative_int(remediation_values.get("skipped_due_to_limit"))
    return {
        "present": True,
        "accepted": True,
        "status": status,
        "mode": profile_remediation_mode(mode, status=status),
        "matched_failed_jobs": matched_failed_jobs,
        "selected_failed_jobs": selected_failed_jobs,
        "requeued_jobs": requeued_jobs,
        "skipped_due_to_limit": skipped_due_to_limit,
        "issue_count": safe_list_count(summary.get("issue_codes")),
        "next_step": recent_profile_remediation_next_step(
            status=status,
            selected_failed_jobs=selected_failed_jobs,
            requeued_jobs=requeued_jobs,
        ),
    }


def profile_remediation_mode(value: str, *, status: str) -> str:
    if value in {"dry_run", "apply"}:
        return value
    if status == "applied":
        return "apply"
    if status == "dry_run":
        return "dry_run"
    return "unknown"


def recent_profile_remediation_next_step(
    *,
    status: str,
    selected_failed_jobs: int,
    requeued_jobs: int,
) -> str:
    if selected_failed_jobs <= 0:
        return "No terminal failed profile jobs matched the remediation filters."
    if status == "dry_run":
        return "Review the bounded count, then rerun remediation with --apply."
    if status == "applied" and requeued_jobs > 0:
        return "Run the Recent profile worker to process the requeued jobs."
    return "Rerun remediation dry-run to inspect the current failed backlog."


def profile_backlog_health_present(summary: Mapping[str, Any] | None) -> bool:
    if summary is None:
        return False
    values = summary.get("profile_backlog_health")
    if not isinstance(values, Mapping):
        return False
    return all(key in values for key in PROFILE_BACKLOG_HEALTH_KEYS)


def safe_profile_backlog_health(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    if not all(key in value for key in PROFILE_BACKLOG_HEALTH_KEYS):
        return {}
    return {
        "pending_jobs": safe_nonnegative_int(value.get("pending_jobs")),
        "retry_pending_jobs": safe_nonnegative_int(value.get("retry_pending_jobs")),
        "leased_jobs": safe_nonnegative_int(value.get("leased_jobs")),
        "stale_leased_jobs": safe_nonnegative_int(value.get("stale_leased_jobs")),
        "failed_jobs": safe_nonnegative_int(value.get("failed_jobs")),
    }


def safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def safe_list_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def safe_summary(value: object) -> str:
    text = str(value or "").strip()
    return text[:160] if text else "not available"


def unsafe_summary_payload(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).strip().lower()
            if key_text in _FORBIDDEN_KEYS:
                return True
            if unsafe_summary_payload(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(unsafe_summary_payload(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _FORBIDDEN_VALUE_MARKERS)
    return False


def operator_readiness_payload_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True) + "\n"


def format_recent_history_operator_readiness(payload: Mapping[str, Any]) -> str:
    lines = [f"Recent history operator readiness: {payload.get('status', 'unknown')}"]
    lines.append(
        f"- evidence summaries: {payload.get('accepted_summary_count', 0)}/{payload.get('evidence_summary_count', 0)}"
    )
    checks = payload.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue
            lines.append(
                "[{status}] {check_id}: {summary}".format(
                    status=check.get("status", "unknown"),
                    check_id=check.get("id", "unknown"),
                    summary=check.get("summary", ""),
                )
            )
    issues = payload.get("issue_codes")
    if isinstance(issues, list) and issues:
        lines.append("issues: " + ",".join(str(issue) for issue in issues))
    lines.extend(format_operations_lines(payload.get("operations")))
    return "\n".join(lines) + "\n"


def format_operations_lines(value: object) -> list[str]:
    operations = value if isinstance(value, Mapping) else {}
    lines: list[str] = []
    postgres = operations.get("postgres_readiness")
    if isinstance(postgres, Mapping) and postgres.get("accepted") is True:
        schema = "ready" if postgres.get("schema_initialized") is True else "not_ready"
        lines.append(
            "- postgres readiness: "
            f"schema={schema} checks={postgres.get('check_count', 0)} "
            f"issues={postgres.get('issue_count', 0)}"
        )
    collector = operations.get("collector_summary")
    if isinstance(collector, Mapping) and collector.get("present") is True:
        lines.append(
            "- collector summary: "
            f"status={collector.get('status', 'unknown')} "
            f"inspected={collector.get('summaries_inspected', 0)} "
            f"discovered={collector.get('candidates_discovered', 0)} "
            f"selected={collector.get('selected_count', 0)} "
            f"recorded={collector.get('summaries_recorded', 0)} "
            f"planned={collector.get('profile_jobs_planned', 0)} "
            f"issues={collector.get('issue_count', 0)}"
        )
        observed_at = collector.get("observed_at_iso")
        if isinstance(observed_at, str) and observed_at:
            lines.append(f"- collector observed: {observed_at}")
        lines.append(
            "- query log continuity: "
            f"at_capacity={str(collector.get('query_log_at_capacity') is True).lower()} "
            f"status={collector.get('query_log_continuity_status', 'unproven')}"
        )
        next_step = collector.get("next_step")
        if isinstance(next_step, str) and next_step:
            lines.append(f"- collector next step: {next_step}")
    worker = operations.get("profile_worker")
    if isinstance(worker, Mapping) and worker.get("accepted") is True:
        next_step = worker.get("next_step")
        backlog_health = worker.get("profile_backlog_health")
        lines.append(
            "- profile worker: "
            f"claimed={worker.get('jobs_claimed', 0)} "
            f"completed={worker.get('jobs_completed', 0)} "
            f"retried={worker.get('jobs_retried', 0)} "
            f"failed={worker.get('jobs_failed', 0)} "
            f"lease_lost={worker.get('jobs_lease_lost', 0)} "
            f"cache={worker.get('analysis_cache_records', 0)} "
            f"artifacts={worker.get('profile_artifact_records', 0)} "
            f"issues={worker.get('issue_count', 0)}"
        )
        observed_at = worker.get("observed_at_iso")
        if isinstance(observed_at, str) and observed_at:
            lines.append(f"- profile worker observed: {observed_at}")
        if isinstance(next_step, str) and next_step:
            lines.append(f"- profile worker next step: {next_step}")
        if isinstance(backlog_health, Mapping):
            lines.append(
                "- profile backlog: "
                f"pending={backlog_health.get('pending_jobs', 0)} "
                f"retry={backlog_health.get('retry_pending_jobs', 0)} "
                f"leased={backlog_health.get('leased_jobs', 0)} "
                f"stale_leased={backlog_health.get('stale_leased_jobs', 0)} "
                f"failed={backlog_health.get('failed_jobs', 0)}"
            )
        backlog_next_step = worker.get("profile_backlog_next_step")
        if isinstance(backlog_next_step, str) and backlog_next_step:
            lines.append(f"- profile backlog next step: {backlog_next_step}")
    retention = operations.get("retention")
    if isinstance(retention, Mapping) and retention.get("accepted") is True:
        lines.append(
            "- retention: "
            f"deleted={retention.get('total_deleted', 0)} "
            f"summaries={retention.get('summaries_deleted', 0)} "
            f"jobs={retention.get('profile_jobs_deleted', 0)} "
            f"cache={retention.get('analysis_cache_deleted', 0)} "
            f"artifacts={retention.get('profile_artifacts_deleted', 0)} "
            f"issues={retention.get('issue_count', 0)}"
        )
    remediation = operations.get("profile_remediation")
    if isinstance(remediation, Mapping) and remediation.get("accepted") is True:
        next_step = remediation.get("next_step")
        lines.append(
            "- profile remediation: "
            f"mode={remediation.get('mode', 'unknown')} "
            f"matched={remediation.get('matched_failed_jobs', 0)} "
            f"selected={remediation.get('selected_failed_jobs', 0)} "
            f"requeued={remediation.get('requeued_jobs', 0)} "
            f"skipped={remediation.get('skipped_due_to_limit', 0)} "
            f"issues={remediation.get('issue_count', 0)}"
        )
        if isinstance(next_step, str) and next_step:
            lines.append(f"- profile remediation next step: {next_step}")
    return lines
