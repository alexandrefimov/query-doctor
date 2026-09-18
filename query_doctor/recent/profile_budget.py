"""Raw-free profile-budget planning over Recent summary history."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol, Sequence

from query_doctor.recent.history_store import (
    RecentSummaryHistoryRecord,
    safe_label,
    safe_query_id,
    safe_text,
)
from query_doctor.recent.profile_artifact_storage import (
    PROFILE_ARTIFACT_STORAGE_KIND_FINGERPRINT_ONLY,
    canonical_profile_artifact_storage_kind,
    profile_artifact_storage_metadata_allowed,
)


PROFILE_BUDGET_SCHEMA_VERSION = 1
PROFILE_JOB_STATUS_PENDING = "pending"
PROFILE_JOB_STATUS_LEASED = "leased"
PROFILE_JOB_STATUS_COMPLETED = "completed"
PROFILE_JOB_STATUS_FAILED = "failed"
# The profile can no longer be fetched: either the query ended before the
# profile window (RecentProfileBudgetStoreBackend.age_out_profile_jobs), or every
# profile endpoint answered that it no longer holds the query.
PROFILE_JOB_STATUS_AGED_OUT = "aged_out"
PROFILE_JOB_AGED_OUT_ERROR_CODE = "profile_aged_out"
PROFILE_JOB_NOT_FOUND_ERROR_CODE = "profile_not_found"
DEFAULT_PROFILE_JOB_MAX_AGE_HOURS = 12
DEFAULT_PROFILE_BUDGET_MIN_SUSPICION_SCORE = 20
DEFAULT_PROFILE_LEASE_OWNER = "worker"
DEFAULT_PROFILE_LEASE_LIMIT = 1
DEFAULT_PROFILE_REQUEUE_LIMIT = 50
DEFAULT_PROFILE_ERROR_CODE = "unknown_error"
PROFILE_STATUS_NOT_COLLECTED = "not_collected"
PROFILE_STATUS_PENDING = "pending"
PROFILE_STATUS_PROCESSING = "processing"
PROFILE_STATUS_RETRY_PENDING = "retry_pending"
PROFILE_STATUS_ANALYZED = "analyzed"
PROFILE_STATUS_FAILED = "failed"

COLLECTED_PROFILE_STATUSES = {
    PROFILE_STATUS_ANALYZED,
    "collected",
    "reused",
}

PROFILE_JOB_STORAGE_COLUMNS = (
    "schema_version",
    "engine",
    "source_kind",
    "source_key",
    "query_id",
    "created_at_iso",
    "updated_at_iso",
    "summary_recorded_at_iso",
    "summary_end_time",
    "priority_score",
    "priority_level",
    "priority_reasons_json",
    "status",
    "attempts",
    "lease_owner",
    "lease_until_iso",
    "last_error_code",
    "last_error_at_iso",
)

ANALYSIS_CACHE_SCHEMA_VERSION = 1
ANALYSIS_CACHE_STATUS_READY = "ready"
ANALYSIS_CACHE_DEFAULT_CONTRACT = "profile_digest_analysis_json_v1"
ANALYSIS_CACHE_SUMMARY_FIELDS = (
    "analysis_status",
    "backend_data_skew",
    "cardinality_anomaly_count",
    "case_primary_bottleneck",
    "collection_status",
    "failure_category",
    "host_tail_candidate_count",
    "memory_anomaly_count",
    "metadata_status",
    "optimizer_rewrite_support",
    "profile_reuse_status",
    "query_optimization_candidate",
    "score",
    "score_reasons",
    "score_severity",
    "scoring_evidence_source",
    "scoring_fallback_reason",
    "stats_optimization_candidate",
    "table_stats_status",
    "workload_fingerprint",
    "workload_fingerprint_incomplete",
    "workload_fingerprint_incomplete_fields",
    "zero_memory_estimate_gap_count",
    "zero_row_estimate_gap_count",
    "referenced_table_count",
    "collectable_metadata_table_count",
    "collected_metadata_table_count",
    "skipped_due_to_max_table_limit",
    "too_large_count",
    "cm_collect_seconds",
    "analysis_seconds",
    "total_seconds",
)

ANALYSIS_CACHE_STORAGE_COLUMNS = (
    "schema_version",
    "engine",
    "source_kind",
    "source_key",
    "query_id",
    "profile_fingerprint",
    "analyzer_contract",
    "recorded_at_iso",
    "status",
    "payload_json",
)

PROFILE_ARTIFACT_SCHEMA_VERSION = 1
PROFILE_ARTIFACT_STATUS_AVAILABLE = "available"
PROFILE_ARTIFACT_DEFAULT_CONTRACT = "profile_artifact_v1"
PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND = PROFILE_ARTIFACT_STORAGE_KIND_FINGERPRINT_ONLY

PROFILE_ARTIFACT_STORAGE_COLUMNS = (
    "schema_version",
    "engine",
    "source_kind",
    "source_key",
    "query_id",
    "profile_fingerprint",
    "artifact_contract",
    "recorded_at_iso",
    "status",
    "storage_kind",
    "storage_key",
    "size_bytes",
)

RAW_ANALYSIS_CACHE_PAYLOAD_KEYS = {
    "artifact",
    "artifact_filename",
    "artifact_path",
    "case_dir",
    "case_path",
    "config_path",
    "dsn",
    "endpoint",
    "filename",
    "host",
    "hostname",
    "keytab",
    "local_path",
    "metadata",
    "model",
    "model_name",
    "password",
    "path",
    "profile",
    "profile_file",
    "profile_path",
    "profile_text",
    "query",
    "query_text",
    "raw_metadata",
    "raw_profile",
    "raw_sql",
    "secret",
    "sql",
    "statement",
    "subprocess_output",
    "token",
    "uri",
    "url",
}


@dataclass(frozen=True)
class ProfileBudgetPolicy:
    max_jobs: int
    min_suspicion_score: int = DEFAULT_PROFILE_BUDGET_MIN_SUSPICION_SCORE
    include_selected: bool = True


@dataclass(frozen=True)
class RecentProfileJobRecord:
    schema_version: int
    engine: str
    source_kind: str
    source_key: str
    query_id: str
    created_at_iso: str
    updated_at_iso: str
    summary_recorded_at_iso: str
    summary_end_time: str | None
    priority_score: int
    priority_level: str
    priority_reasons: tuple[str, ...]
    status: str = PROFILE_JOB_STATUS_PENDING
    attempts: int = 0
    lease_owner: str | None = None
    lease_until_iso: str | None = None
    last_error_code: str | None = None
    last_error_at_iso: str | None = None

    def safe_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RecentProfileBacklogHealth:
    pending_jobs: int = 0
    retry_pending_jobs: int = 0
    leased_jobs: int = 0
    stale_leased_jobs: int = 0
    failed_jobs: int = 0
    # Jobs that finished in the profile window, counted only when a window is set.
    window_hours: int | None = None
    window_completed_jobs: int | None = None
    window_failed_jobs: int | None = None
    window_not_found_jobs: int | None = None

    def safe_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "pending_jobs": safe_profile_backlog_count(self.pending_jobs),
            "retry_pending_jobs": safe_profile_backlog_count(self.retry_pending_jobs),
            "leased_jobs": safe_profile_backlog_count(self.leased_jobs),
            "stale_leased_jobs": safe_profile_backlog_count(self.stale_leased_jobs),
            "failed_jobs": safe_profile_backlog_count(self.failed_jobs),
        }
        if self.window_hours is not None:
            payload["window_hours"] = safe_profile_backlog_count(self.window_hours)
            payload["window_completed_jobs"] = safe_profile_backlog_count(
                self.window_completed_jobs
            )
            payload["window_failed_jobs"] = safe_profile_backlog_count(self.window_failed_jobs)
            payload["window_not_found_jobs"] = safe_profile_backlog_count(
                self.window_not_found_jobs
            )
        return payload


@dataclass(frozen=True)
class RecentAnalysisCacheRecord:
    schema_version: int
    engine: str
    source_kind: str
    source_key: str
    query_id: str
    profile_fingerprint: str
    analyzer_contract: str
    recorded_at_iso: str
    status: str
    payload: Mapping[str, object]

    def safe_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["payload"] = safe_analysis_cache_payload(self.payload)
        return payload


@dataclass(frozen=True)
class RecentProfileArtifactRecord:
    schema_version: int
    engine: str
    source_kind: str
    source_key: str
    query_id: str
    profile_fingerprint: str
    artifact_contract: str
    recorded_at_iso: str
    status: str
    storage_kind: str
    storage_key: str
    size_bytes: int | None = None

    def safe_payload(self) -> dict[str, object]:
        row = profile_artifact_record_to_storage_row(self)
        return row if row is not None else {}


@dataclass(frozen=True)
class RecentProfileJobRequeueResult:
    matched_failed_jobs: int = 0
    selected_failed_jobs: int = 0
    requeued_jobs: int = 0
    dry_run: bool = True

    @property
    def skipped_due_to_limit(self) -> int:
        return max(0, self.matched_failed_jobs - self.selected_failed_jobs)

    def safe_payload(self) -> dict[str, object]:
        return {
            "matched_failed_jobs": max(0, int(self.matched_failed_jobs)),
            "selected_failed_jobs": max(0, int(self.selected_failed_jobs)),
            "requeued_jobs": max(0, int(self.requeued_jobs)),
            "skipped_due_to_limit": self.skipped_due_to_limit,
            "dry_run": bool(self.dry_run),
        }


class RecentProfileBudgetStoreBackend(Protocol):
    def enqueue_profile_jobs(self, records: Iterable[RecentProfileJobRecord]) -> int:
        """Insert pending profile jobs without resetting existing job state."""

    def claim_profile_jobs(
        self,
        *,
        max_jobs: int,
        lease_owner: str,
        lease_until_iso: str,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
    ) -> list[RecentProfileJobRecord]:
        """Atomically lease pending or expired profile jobs."""

    def renew_profile_job_lease(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        lease_owner: str,
        lease_until_iso: str,
        now_iso: str,
    ) -> bool:
        """Extend the lease for one currently leased profile job."""

    def complete_profile_job(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        lease_owner: str,
        completed_at_iso: str,
    ) -> bool:
        """Mark one currently leased profile job as completed."""

    def fail_profile_job(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        lease_owner: str,
        failed_at_iso: str,
        error_code: str,
        retry: bool,
        aged_out: bool = False,
    ) -> bool:
        """Mark one currently leased profile job as failed, retryable or aged out.

        `aged_out` applies when the job is not retried. Its summary row moves to
        failed either way, since no profile will be collected.
        """

    def requeue_failed_profile_jobs(
        self,
        *,
        max_jobs: int,
        requeued_at_iso: str,
        dry_run: bool,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
    ) -> RecentProfileJobRequeueResult:
        """Count and optionally requeue terminal failed jobs without returning identities."""

    def age_out_profile_jobs(
        self,
        *,
        cutoff_iso: str,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
    ) -> int:
        """Mark pending and stale-leased jobs whose query ended before cutoff as aged out.

        Their summary rows move to failed, since no profile will be collected.
        Terminal failed jobs and jobs without a query end time are left alone.
        """

    def summarize_profile_backlog_health(
        self,
        *,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
        window_start_iso: str | None = None,
        window_hours: int | None = None,
    ) -> RecentProfileBacklogHealth:
        """Return aggregate raw-free profile backlog health counts.

        With a window start, also count jobs completed or failed since then, and
        jobs aged out since then because no endpoint held their profile.
        """

    def store_analysis_cache_records(
        self,
        records: Iterable[RecentAnalysisCacheRecord],
    ) -> int:
        """Insert or update raw-free analysis-cache records."""

    def load_analysis_cache_record(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        profile_fingerprint: str,
        analyzer_contract: str,
    ) -> RecentAnalysisCacheRecord | None:
        """Load one raw-free analysis-cache record by compatibility key."""

    def store_profile_artifact_records(
        self,
        records: Iterable[RecentProfileArtifactRecord],
    ) -> int:
        """Insert or update raw-free profile-artifact metadata records."""

    def load_profile_artifact_record(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        profile_fingerprint: str,
        artifact_contract: str,
    ) -> RecentProfileArtifactRecord | None:
        """Load one profile-artifact metadata record by compatibility key."""


def plan_recent_profile_jobs(
    summaries: Iterable[RecentSummaryHistoryRecord],
    *,
    policy: ProfileBudgetPolicy,
    planned_at_iso: str | None = None,
) -> list[RecentProfileJobRecord]:
    limit = max(0, int(policy.max_jobs))
    if limit <= 0:
        return []
    planned_at = safe_text(
        planned_at_iso or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    min_score = max(0, int(policy.min_suspicion_score))
    candidates = [
        summary
        for summary in summaries
        if profile_job_is_eligible(
            summary,
            min_suspicion_score=min_score,
            include_selected=policy.include_selected,
        )
    ]
    ranked = sorted(candidates, key=profile_job_rank_key)
    return [
        profile_job_from_summary(summary, planned_at_iso=planned_at, min_suspicion_score=min_score)
        for summary in ranked[:limit]
    ]


def profile_job_is_eligible(
    summary: RecentSummaryHistoryRecord,
    *,
    min_suspicion_score: int,
    include_selected: bool,
) -> bool:
    if not safe_query_id(summary.query_id):
        return False
    profile_status = safe_label(summary.profile_status, default="not_collected")
    if profile_status in COLLECTED_PROFILE_STATUSES:
        return False
    if summary.suspicion_score >= min_suspicion_score:
        return True
    return bool(include_selected and summary.selected)


def profile_job_rank_key(summary: RecentSummaryHistoryRecord) -> tuple[object, ...]:
    return (
        -max(0, int(summary.suspicion_score)),
        not bool(summary.selected),
        -(summary.duration_ms or 0),
        safe_text(summary.end_time or ""),
        safe_query_id(summary.query_id),
    )


def profile_job_from_summary(
    summary: RecentSummaryHistoryRecord,
    *,
    planned_at_iso: str,
    min_suspicion_score: int,
) -> RecentProfileJobRecord:
    return RecentProfileJobRecord(
        schema_version=PROFILE_BUDGET_SCHEMA_VERSION,
        engine=safe_label(summary.engine, default="unknown"),
        source_kind=safe_label(summary.source_kind, default="unknown"),
        source_key=safe_text(summary.source_key)[:256] or "default",
        query_id=safe_query_id(summary.query_id),
        created_at_iso=planned_at_iso,
        updated_at_iso=planned_at_iso,
        summary_recorded_at_iso=safe_text(summary.recorded_at_iso),
        summary_end_time=safe_text(summary.end_time)[:256] or None,
        priority_score=max(0, int(summary.suspicion_score)),
        priority_level=safe_label(summary.suspicion_level, default="none"),
        priority_reasons=profile_job_reasons(summary, min_suspicion_score=min_suspicion_score),
    )


def profile_job_reasons(
    summary: RecentSummaryHistoryRecord,
    *,
    min_suspicion_score: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if summary.selected:
        reasons.append("selected_recent_candidate")
    reasons.extend(safe_label(reason, default="unknown") for reason in summary.suspicion_reasons)
    if not reasons and summary.suspicion_score >= min_suspicion_score:
        reasons.append("suspicion_score_ge_threshold")
    return tuple(dict.fromkeys(reasons))


def normalize_profile_claim_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PROFILE_LEASE_LIMIT
    return max(0, parsed)


def safe_profile_backlog_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def profile_backlog_health_from_counts(
    *,
    pending_jobs: object,
    retry_pending_jobs: object,
    leased_jobs: object,
    stale_leased_jobs: object,
    failed_jobs: object,
    window_hours: int | None = None,
    window_counts: Sequence[object] | None = None,
) -> RecentProfileBacklogHealth:
    windowed = window_hours is not None and window_counts is not None
    return RecentProfileBacklogHealth(
        pending_jobs=safe_profile_backlog_count(pending_jobs),
        retry_pending_jobs=safe_profile_backlog_count(retry_pending_jobs),
        leased_jobs=safe_profile_backlog_count(leased_jobs),
        stale_leased_jobs=safe_profile_backlog_count(stale_leased_jobs),
        failed_jobs=safe_profile_backlog_count(failed_jobs),
        window_hours=safe_profile_backlog_count(window_hours) if windowed else None,
        window_completed_jobs=safe_profile_backlog_count(window_counts[0]) if windowed else None,
        window_failed_jobs=safe_profile_backlog_count(window_counts[1]) if windowed else None,
        window_not_found_jobs=safe_profile_backlog_count(window_counts[2]) if windowed else None,
    )


def normalize_profile_lease_owner(value: object) -> str:
    normalized = safe_label(value, default=DEFAULT_PROFILE_LEASE_OWNER)
    return normalized[:64] or DEFAULT_PROFILE_LEASE_OWNER


def next_profile_job_status(*, retry: bool, aged_out: bool) -> str:
    """Status a leased job moves to when its attempt does not complete."""
    if retry:
        return PROFILE_JOB_STATUS_PENDING
    return PROFILE_JOB_STATUS_AGED_OUT if aged_out else PROFILE_JOB_STATUS_FAILED


def normalize_profile_lease_timestamp(value: object) -> str:
    normalized = safe_text(value).strip()
    return normalized[:128] or datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_profile_error_code(value: object) -> str:
    normalized = safe_label(value, default=DEFAULT_PROFILE_ERROR_CODE)
    return normalized[:96] or DEFAULT_PROFILE_ERROR_CODE


def normalize_profile_job_key(
    *,
    engine: object,
    source_kind: object,
    source_key: object,
    query_id: object,
) -> tuple[str, str, str, str]:
    return (
        safe_label(engine, default="unknown"),
        safe_label(source_kind, default="unknown"),
        safe_text(source_key)[:256] or "default",
        safe_query_id(query_id),
    )


def normalize_optional_profile_job_filters(
    *,
    engine: object | None = None,
    source_kind: object | None = None,
    source_key: object | None = None,
) -> tuple[str | None, str | None, str | None]:
    return (
        safe_label(engine, default="") if engine is not None else None,
        safe_label(source_kind, default="") if source_kind is not None else None,
        (safe_text(source_key)[:256] or None) if source_key is not None else None,
    )


def normalize_analysis_cache_fingerprint(value: object) -> str:
    return safe_label(value, default="")[:128]


def normalize_analysis_cache_contract(value: object) -> str:
    return safe_label(value, default="")[:128]


def normalize_analysis_cache_status(value: object) -> str:
    normalized = safe_label(value, default=ANALYSIS_CACHE_STATUS_READY)
    return normalized[:64] or ANALYSIS_CACHE_STATUS_READY


def normalize_profile_artifact_contract(value: object) -> str:
    return safe_label(value, default=PROFILE_ARTIFACT_DEFAULT_CONTRACT)[:128]


def normalize_profile_artifact_status(value: object) -> str:
    normalized = safe_label(value, default=PROFILE_ARTIFACT_STATUS_AVAILABLE)
    return normalized[:64] or PROFILE_ARTIFACT_STATUS_AVAILABLE


def normalize_profile_artifact_storage_kind(value: object) -> str:
    normalized = safe_label(
        canonical_profile_artifact_storage_kind(value),
        default=PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND,
    )
    return normalized[:64] or PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND


def normalize_profile_artifact_storage_key(value: object) -> str:
    return safe_label(value, default="")[:128]


def analysis_cache_record_to_storage_row(
    record: RecentAnalysisCacheRecord,
) -> dict[str, object] | None:
    engine, source_kind, source_key, query_id = normalize_profile_job_key(
        engine=record.engine,
        source_kind=record.source_kind,
        source_key=record.source_key,
        query_id=record.query_id,
    )
    profile_fingerprint = normalize_analysis_cache_fingerprint(record.profile_fingerprint)
    analyzer_contract = normalize_analysis_cache_contract(record.analyzer_contract)
    if not query_id or not profile_fingerprint or not analyzer_contract:
        return None
    return {
        "schema_version": record.schema_version,
        "engine": engine,
        "source_kind": source_kind,
        "source_key": source_key,
        "query_id": query_id,
        "profile_fingerprint": profile_fingerprint,
        "analyzer_contract": analyzer_contract,
        "recorded_at_iso": normalize_profile_lease_timestamp(record.recorded_at_iso),
        "status": normalize_analysis_cache_status(record.status),
        "payload_json": json.dumps(
            safe_analysis_cache_payload(record.payload),
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def profile_artifact_record_to_storage_row(
    record: RecentProfileArtifactRecord,
) -> dict[str, object] | None:
    engine, source_kind, source_key, query_id = normalize_profile_job_key(
        engine=record.engine,
        source_kind=record.source_kind,
        source_key=record.source_key,
        query_id=record.query_id,
    )
    profile_fingerprint = normalize_analysis_cache_fingerprint(record.profile_fingerprint)
    artifact_contract = normalize_profile_artifact_contract(record.artifact_contract)
    storage_kind = normalize_profile_artifact_storage_kind(record.storage_kind)
    storage_key = normalize_profile_artifact_storage_key(record.storage_key)
    if not query_id or not profile_fingerprint or not artifact_contract or not storage_key:
        return None
    if not profile_artifact_storage_metadata_allowed(
        storage_kind=storage_kind,
        storage_key=storage_key,
    ):
        return None
    return {
        "schema_version": record.schema_version,
        "engine": engine,
        "source_kind": source_kind,
        "source_key": source_key,
        "query_id": query_id,
        "profile_fingerprint": profile_fingerprint,
        "artifact_contract": artifact_contract,
        "recorded_at_iso": normalize_profile_lease_timestamp(record.recorded_at_iso),
        "status": normalize_profile_artifact_status(record.status),
        "storage_kind": storage_kind,
        "storage_key": storage_key,
        "size_bytes": safe_optional_int(record.size_bytes),
    }


def profile_job_record_from_storage_values(
    values: Sequence[object],
) -> RecentProfileJobRecord:
    row = dict(zip(PROFILE_JOB_STORAGE_COLUMNS, values))
    return RecentProfileJobRecord(
        schema_version=safe_int(row.get("schema_version"), default=PROFILE_BUDGET_SCHEMA_VERSION),
        engine=safe_label(row.get("engine"), default="unknown"),
        source_kind=safe_label(row.get("source_kind"), default="unknown"),
        source_key=safe_text(row.get("source_key"))[:256] or "default",
        query_id=safe_query_id(row.get("query_id")),
        created_at_iso=safe_text(row.get("created_at_iso")),
        updated_at_iso=safe_text(row.get("updated_at_iso")),
        summary_recorded_at_iso=safe_text(row.get("summary_recorded_at_iso")),
        summary_end_time=safe_optional_text(row.get("summary_end_time")),
        priority_score=safe_int(row.get("priority_score"), default=0),
        priority_level=safe_label(row.get("priority_level"), default="none"),
        priority_reasons=profile_job_reason_tuple(row.get("priority_reasons_json")),
        status=safe_label(row.get("status"), default=PROFILE_JOB_STATUS_PENDING),
        attempts=safe_int(row.get("attempts"), default=0),
        lease_owner=safe_optional_text(row.get("lease_owner")),
        lease_until_iso=safe_optional_text(row.get("lease_until_iso")),
        last_error_code=safe_optional_profile_error_code(row.get("last_error_code")),
        last_error_at_iso=safe_optional_text(row.get("last_error_at_iso")),
    )


def analysis_cache_record_from_storage_values(
    values: Sequence[object],
) -> RecentAnalysisCacheRecord:
    row = dict(zip(ANALYSIS_CACHE_STORAGE_COLUMNS, values))
    return RecentAnalysisCacheRecord(
        schema_version=safe_int(row.get("schema_version"), default=ANALYSIS_CACHE_SCHEMA_VERSION),
        engine=safe_label(row.get("engine"), default="unknown"),
        source_kind=safe_label(row.get("source_kind"), default="unknown"),
        source_key=safe_text(row.get("source_key"))[:256] or "default",
        query_id=safe_query_id(row.get("query_id")),
        profile_fingerprint=normalize_analysis_cache_fingerprint(row.get("profile_fingerprint")),
        analyzer_contract=normalize_analysis_cache_contract(row.get("analyzer_contract")),
        recorded_at_iso=safe_text(row.get("recorded_at_iso")),
        status=normalize_analysis_cache_status(row.get("status")),
        payload=safe_analysis_cache_payload(row.get("payload_json")),
    )


def profile_artifact_record_from_storage_values(
    values: Sequence[object],
) -> RecentProfileArtifactRecord:
    row = dict(zip(PROFILE_ARTIFACT_STORAGE_COLUMNS, values))
    return RecentProfileArtifactRecord(
        schema_version=safe_int(row.get("schema_version"), default=PROFILE_ARTIFACT_SCHEMA_VERSION),
        engine=safe_label(row.get("engine"), default="unknown"),
        source_kind=safe_label(row.get("source_kind"), default="unknown"),
        source_key=safe_text(row.get("source_key"))[:256] or "default",
        query_id=safe_query_id(row.get("query_id")),
        profile_fingerprint=normalize_analysis_cache_fingerprint(row.get("profile_fingerprint")),
        artifact_contract=normalize_profile_artifact_contract(row.get("artifact_contract")),
        recorded_at_iso=safe_text(row.get("recorded_at_iso")),
        status=normalize_profile_artifact_status(row.get("status")),
        storage_kind=normalize_profile_artifact_storage_kind(row.get("storage_kind")),
        storage_key=normalize_profile_artifact_storage_key(row.get("storage_key")),
        size_bytes=safe_optional_int(row.get("size_bytes")),
    )


def profile_job_reason_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
    else:
        parsed = value
    if not isinstance(parsed, (list, tuple)):
        return ()
    return tuple(safe_label(reason, default="unknown") for reason in parsed)


def safe_analysis_cache_payload(value: object) -> dict[str, object]:
    safe_value = safe_analysis_cache_json_value(value, depth=0)
    return safe_value if isinstance(safe_value, dict) else {}


def safe_analysis_cache_json_value(value: object, *, depth: int) -> object:
    if depth > 8:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return safe_text(value)[:4096]
        return safe_analysis_cache_json_value(parsed, depth=depth + 1)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_child in list(value.items())[:256]:
            key = safe_label(raw_key, default="")
            if not key or key in RAW_ANALYSIS_CACHE_PAYLOAD_KEYS:
                continue
            child = safe_analysis_cache_json_value(raw_child, depth=depth + 1)
            if child is not None:
                result[key[:128]] = child
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for raw_child in value[:256]:
            child = safe_analysis_cache_json_value(raw_child, depth=depth + 1)
            if child is not None:
                result.append(child)
        return result
    return safe_text(value)[:4096]


def safe_optional_text(value: object) -> str | None:
    text = safe_text(value).strip()
    return text[:256] if text else None


def safe_optional_profile_error_code(value: object) -> str | None:
    text = safe_text(value).strip()
    return normalize_profile_error_code(text) if text else None


def safe_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def safe_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default
