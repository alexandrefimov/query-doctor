"""Backend-neutral raw-free Recent summary history store contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol

from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
from query_doctor.cm.query_discovery import extract_sql_verb
from query_doctor.recent.batch_models import BatchConfig
from query_doctor.recent.statement_identity import (
    error_class_from_status,
    statement_fingerprint,
)
from query_doctor.recent.summary_suspicion import (
    SummarySuspicionScore,
    score_recent_summary_suspicion,
)
from query_doctor.safety.redaction import sanitize_identifier_for_log, sanitize_text_for_log


SCHEMA_VERSION = 2


class RecentHistoryStoreError(RuntimeError):
    """Raised by Recent history store backends after sanitizing local details."""


class RecentHistoryStoreBackend(Protocol):
    def initialize(self) -> None:
        """Prepare backend storage."""

    def upsert_summaries(self, records: Iterable["RecentSummaryHistoryRecord"]) -> int:
        """Insert or update summary records and return the attempted record count."""


class RecentHistoryRetentionStoreBackend(RecentHistoryStoreBackend, Protocol):
    def count_summaries(self) -> int:
        """Return the number of retained raw-free summary rows."""

    def load_payloads(self) -> list[dict[str, object]]:
        """Load retained raw-free summary payloads for read-only inbox views."""

    def prune_history(
        self,
        *,
        policy: "RecentHistoryRetentionPolicy",
    ) -> "RecentHistoryRetentionResult":
        """Prune old raw-free history rows and return aggregate delete counts."""


@dataclass(frozen=True)
class RecentSummaryHistoryRecord:
    schema_version: int
    engine: str
    source_kind: str
    source_key: str
    query_id: str
    recorded_at_iso: str
    start_time: str | None
    end_time: str | None
    duration_ms: int | None
    status: str | None
    query_state: str | None
    user: str | None
    pool: str | None
    query_type: str | None
    sql_verb: str | None
    statement_present: bool
    admission_result: str | None
    admission_wait_ms: int | None
    rows_produced: int | None
    bytes_read: int | None
    bytes_sent: int | None
    memory_aggregate_peak: int | None
    memory_per_node_peak: int | None
    suspicion_score: int
    suspicion_level: str
    suspicion_reasons: tuple[str, ...]
    selected: bool
    selected_reason: str | None
    profile_status: str = "not_collected"
    error_class: str | None = None
    statement_fingerprint: str | None = None

    def safe_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RecentHistoryRetentionPolicy:
    summary_cutoff_iso: str | None = None
    profile_job_cutoff_iso: str | None = None
    analysis_cache_cutoff_iso: str | None = None
    profile_artifact_cutoff_iso: str | None = None


@dataclass(frozen=True)
class RecentHistoryRetentionResult:
    summaries_deleted: int = 0
    profile_jobs_deleted: int = 0
    analysis_cache_deleted: int = 0
    profile_artifacts_deleted: int = 0

    @property
    def total_deleted(self) -> int:
        return (
            self.summaries_deleted
            + self.profile_jobs_deleted
            + self.analysis_cache_deleted
            + self.profile_artifacts_deleted
        )

    def safe_payload(self) -> dict[str, object]:
        return asdict(self) | {"total_deleted": self.total_deleted}


def history_records_from_candidates(
    candidates: Iterable[RecentQueryCandidate],
    *,
    config: BatchConfig,
    recorded_at_iso: str | None = None,
) -> list[RecentSummaryHistoryRecord]:
    observed_at = recorded_at_iso or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_key = recent_history_source_key(config)
    return [
        history_record_from_candidate(
            candidate,
            engine="impala",
            source_kind=config.query_profile_source,
            source_key=source_key,
            recorded_at_iso=observed_at,
        )
        for candidate in candidates
    ]


def history_record_from_candidate(
    candidate: RecentQueryCandidate,
    *,
    engine: str,
    source_kind: str,
    source_key: str,
    recorded_at_iso: str,
) -> RecentSummaryHistoryRecord:
    summary = candidate.summary
    suspicion = score_recent_summary_suspicion(summary)
    sql_verb = candidate.sql_verb or extract_sql_verb(summary.statement)
    return history_record_from_summary(
        summary,
        suspicion=suspicion,
        engine=engine,
        source_kind=source_kind,
        source_key=source_key,
        recorded_at_iso=recorded_at_iso,
        sql_verb=sql_verb,
        selected=candidate.selected,
        selected_reason=candidate.reason,
    )


def history_record_from_summary(
    summary: CMQuerySummary,
    *,
    suspicion: SummarySuspicionScore,
    engine: str,
    source_kind: str,
    source_key: str,
    recorded_at_iso: str,
    sql_verb: str | None,
    selected: bool,
    selected_reason: str | None,
) -> RecentSummaryHistoryRecord:
    return RecentSummaryHistoryRecord(
        schema_version=SCHEMA_VERSION,
        engine=safe_label(engine, default="unknown"),
        source_kind=safe_label(source_kind, default="unknown"),
        source_key=safe_source_key(source_key),
        query_id=safe_query_id(summary.query_id),
        recorded_at_iso=safe_text(recorded_at_iso),
        start_time=safe_optional_text(summary.start_time),
        end_time=safe_optional_text(summary.end_time),
        duration_ms=safe_nonnegative_int(summary.duration_ms),
        status=safe_optional_label(summary.status),
        query_state=safe_optional_label(summary.query_state),
        user=safe_optional_text(summary.user),
        pool=safe_optional_text(summary.pool),
        query_type=safe_optional_label(summary.query_type),
        sql_verb=safe_optional_label(sql_verb),
        statement_present=bool(summary.statement),
        admission_result=safe_optional_label(summary.admission_result),
        admission_wait_ms=safe_nonnegative_int(summary.admission_wait_ms),
        rows_produced=safe_nonnegative_int(summary.rows_produced),
        bytes_read=safe_nonnegative_int(summary.bytes_read),
        bytes_sent=safe_nonnegative_int(summary.bytes_sent),
        memory_aggregate_peak=safe_nonnegative_int(summary.memory_aggregate_peak),
        memory_per_node_peak=safe_nonnegative_int(summary.memory_per_node_peak),
        suspicion_score=max(0, int(suspicion.score)),
        suspicion_level=safe_label(suspicion.level, default="none"),
        suspicion_reasons=tuple(
            safe_label(reason, default="unknown") for reason in suspicion.reasons
        ),
        selected=selected,
        selected_reason=safe_optional_text(selected_reason),
        error_class=error_class_from_status(summary.status),
        statement_fingerprint=statement_fingerprint(summary.statement),
    )


def recent_history_source_key(config: BatchConfig) -> str:
    if config.query_profile_source == "impala":
        host_count = len(config.impala_profile_hosts)
        return f"impala-daemon:{host_count}-hosts"
    cluster = safe_optional_text(config.cluster) or "unknown-cluster"
    service = safe_optional_text(config.service) or "unknown-service"
    return f"cm:{cluster}:{service}"


def persist_recent_history(
    candidates: Iterable[RecentQueryCandidate],
    *,
    config: BatchConfig,
    env: dict[str, str],
) -> tuple[int, str | None]:
    try:
        store = recent_history_store_from_config(config, env=env)
    except (OSError, RecentHistoryStoreError):
        return 0, recent_history_store_warning()
    if store is None:
        return 0, None
    return persist_recent_history_with_store(store, candidates, config=config)


def prune_recent_history(
    *,
    config: BatchConfig,
    env: dict[str, str],
    now: datetime | None = None,
) -> tuple[RecentHistoryRetentionResult, str | None]:
    if not recent_history_retention_enabled(config):
        return RecentHistoryRetentionResult(), None
    try:
        store = recent_history_store_from_config(config, env=env)
        if store is None:
            return RecentHistoryRetentionResult(), recent_history_prune_warning()
        policy = recent_history_retention_policy(config, now=now)
        return store.prune_history(policy=policy), None
    except (OSError, RecentHistoryStoreError):
        return RecentHistoryRetentionResult(), recent_history_prune_warning()


def recent_history_store_from_config(
    config: BatchConfig,
    *,
    env: dict[str, str],
) -> RecentHistoryRetentionStoreBackend | None:
    if config.recent_history_backend == "disabled":
        return None
    if config.recent_history_backend == "sqlite":
        from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

        if config.recent_history_db is None:
            raise RecentHistoryStoreError("recent_history_sqlite_db_missing")
        return SqliteRecentHistoryStore(config.recent_history_db)
    if config.recent_history_backend == "postgres":
        from query_doctor.recent.postgres_history_store import PostgresRecentHistoryStore

        return PostgresRecentHistoryStore.from_env(
            config.recent_history_postgres_dsn_env,
            env=env,
        )
    raise RecentHistoryStoreError("recent_history_backend_invalid")


def persist_recent_history_with_store(
    store: RecentHistoryStoreBackend,
    candidates: Iterable[RecentQueryCandidate],
    *,
    config: BatchConfig,
) -> tuple[int, str | None]:
    try:
        records = history_records_from_candidates(candidates, config=config)
        return store.upsert_summaries(records), None
    except (OSError, RecentHistoryStoreError):
        return 0, recent_history_store_warning()


def load_recent_history_payloads(
    *,
    config: BatchConfig,
    env: dict[str, str],
) -> tuple[list[dict[str, object]], int | None, str | None]:
    try:
        store = recent_history_store_from_config(config, env=env)
        if store is None:
            return [], None, None
        payloads = store.load_payloads()
        return payloads, store.count_summaries(), None
    except (OSError, RecentHistoryStoreError):
        return [], None, recent_history_load_warning()


def enqueue_recent_profile_jobs(
    candidates: Iterable[RecentQueryCandidate],
    *,
    config: BatchConfig,
    env: dict[str, str],
) -> tuple[int, str | None]:
    try:
        store = recent_history_store_from_config(config, env=env)
    except (OSError, RecentHistoryStoreError):
        return 0, recent_profile_job_enqueue_warning()
    if store is None:
        return 0, None
    return enqueue_recent_profile_jobs_with_store(store, candidates, config=config)


def enqueue_recent_profile_jobs_with_store(
    store: object,
    candidates: Iterable[RecentQueryCandidate],
    *,
    config: BatchConfig,
) -> tuple[int, str | None]:
    enqueue = getattr(store, "enqueue_profile_jobs", None)
    if not callable(enqueue):
        return 0, recent_profile_job_enqueue_warning()
    try:
        from query_doctor.recent.profile_budget import ProfileBudgetPolicy
        from query_doctor.recent.profile_budget import plan_recent_profile_jobs

        records = history_records_from_candidates(candidates, config=config)
        jobs = plan_recent_profile_jobs(
            records,
            policy=ProfileBudgetPolicy(max_jobs=config.triage_profile_limit),
        )
        return int(enqueue(jobs)), None
    except (OSError, RecentHistoryStoreError):
        return 0, recent_profile_job_enqueue_warning()


def recent_history_store_warning() -> str:
    return (
        "Recent history store was not updated; check local history store "
        "configuration and filesystem or database permissions."
    )


def recent_profile_job_enqueue_warning() -> str:
    return (
        "Recent profile jobs were not planned; check local history store "
        "configuration and database permissions."
    )


def recent_history_prune_warning() -> str:
    return (
        "Recent history retention was not pruned; check local history store "
        "configuration and database permissions."
    )


def recent_history_load_warning() -> str:
    return (
        "Recent history store could not be read; check local history store "
        "configuration and filesystem or database permissions."
    )


def recent_history_retention_enabled(config: BatchConfig) -> bool:
    return bool(
        config.recent_history_summary_retention_days
        or config.recent_history_profile_job_retention_days
        or config.recent_history_analysis_cache_retention_days
        or config.recent_history_profile_artifact_retention_days
    )


def recent_history_retention_policy(
    config: BatchConfig,
    *,
    now: datetime | None = None,
) -> RecentHistoryRetentionPolicy:
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc).replace(microsecond=0)
    return RecentHistoryRetentionPolicy(
        summary_cutoff_iso=retention_cutoff_iso(
            observed_at, config.recent_history_summary_retention_days
        ),
        profile_job_cutoff_iso=retention_cutoff_iso(
            observed_at, config.recent_history_profile_job_retention_days
        ),
        analysis_cache_cutoff_iso=retention_cutoff_iso(
            observed_at, config.recent_history_analysis_cache_retention_days
        ),
        profile_artifact_cutoff_iso=retention_cutoff_iso(
            observed_at, config.recent_history_profile_artifact_retention_days
        ),
    )


def retention_cutoff_iso(now: datetime, days: int | None) -> str | None:
    if days is None:
        return None
    return (now - timedelta(days=days)).isoformat()


def safe_label(value: object, *, default: str) -> str:
    text = safe_text(value).strip().lower().replace("-", "_")
    return text if text.replace("_", "").isalnum() else default


def safe_source_key(value: object) -> str:
    text = safe_text(value).strip()
    return text[:256] if text else "default"


def safe_optional_label(value: object) -> str | None:
    text = safe_label(value, default="")
    return text or None


def safe_optional_text(value: object) -> str | None:
    text = safe_text(value).strip()
    return text[:256] if text else None


def safe_text(value: object) -> str:
    return sanitize_text_for_log(str(value or ""))


def safe_query_id(value: object) -> str:
    return sanitize_identifier_for_log(str(value or ""))


def safe_retention_cutoff(value: object) -> str | None:
    text = safe_text(value).strip()
    return text[:128] if text else None


def safe_retention_policy(policy: RecentHistoryRetentionPolicy) -> RecentHistoryRetentionPolicy:
    return RecentHistoryRetentionPolicy(
        summary_cutoff_iso=safe_retention_cutoff(policy.summary_cutoff_iso),
        profile_job_cutoff_iso=safe_retention_cutoff(policy.profile_job_cutoff_iso),
        analysis_cache_cutoff_iso=safe_retention_cutoff(policy.analysis_cache_cutoff_iso),
        profile_artifact_cutoff_iso=safe_retention_cutoff(policy.profile_artifact_cutoff_iso),
    )


def safe_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
