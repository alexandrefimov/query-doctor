"""Shared Recent profile job worker orchestration."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from query_doctor.recent.batch_models import BatchConfig
from query_doctor.recent.history_store import RecentHistoryStoreError, recent_history_source_key
from query_doctor.recent.profile_budget import (
    ANALYSIS_CACHE_DEFAULT_CONTRACT,
    ANALYSIS_CACHE_SCHEMA_VERSION,
    ANALYSIS_CACHE_STATUS_READY,
    PROFILE_ARTIFACT_DEFAULT_CONTRACT,
    PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND,
    PROFILE_ARTIFACT_SCHEMA_VERSION,
    PROFILE_ARTIFACT_STATUS_AVAILABLE,
    RecentAnalysisCacheRecord,
    RecentProfileBacklogHealth,
    RecentProfileArtifactRecord,
    RecentProfileBudgetStoreBackend,
    RecentProfileJobRecord,
    normalize_profile_error_code,
    normalize_profile_lease_owner,
)
from query_doctor.recent.progress import ProgressWriter


RECENT_PROFILE_WORKER_SUMMARY_KIND = "query_doctor_recent_profile_worker_v1"
RECENT_PROFILE_WORKER_ANALYZER_CONTRACT = ANALYSIS_CACHE_DEFAULT_CONTRACT
DEFAULT_PROFILE_WORKER_MAX_JOBS = 1
DEFAULT_PROFILE_WORKER_LEASE_SECONDS = 900
DEFAULT_PROFILE_WORKER_MAX_ATTEMPTS = 3
PROFILE_BACKLOG_COUNT_KEYS = (
    "pending_jobs",
    "retry_pending_jobs",
    "leased_jobs",
    "stale_leased_jobs",
    "failed_jobs",
)


@dataclass(frozen=True)
class RecentProfileWorkerOptions:
    max_jobs: int = DEFAULT_PROFILE_WORKER_MAX_JOBS
    lease_owner: str = "recent-profile-worker"
    lease_seconds: int = DEFAULT_PROFILE_WORKER_LEASE_SECONDS
    max_attempts: int = DEFAULT_PROFILE_WORKER_MAX_ATTEMPTS


@dataclass(frozen=True)
class RecentProfileWorkerJobOutcome:
    status: str
    error_code: str | None = None
    retry: bool = False
    profile_fingerprint: str | None = None
    analyzer_contract: str = RECENT_PROFILE_WORKER_ANALYZER_CONTRACT
    analysis_payload: Mapping[str, object] | None = None
    artifact_contract: str = PROFILE_ARTIFACT_DEFAULT_CONTRACT
    artifact_storage_kind: str = PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND
    artifact_storage_key: str | None = None
    artifact_size_bytes: int | None = None


@dataclass
class RecentProfileWorkerResult:
    status: str = "done"
    observed_at_iso: str = ""
    jobs_claimed: int = 0
    jobs_completed: int = 0
    jobs_retried: int = 0
    jobs_failed: int = 0
    jobs_lease_lost: int = 0
    jobs_aged_out: int = 0
    analysis_cache_records: int = 0
    profile_artifact_records: int = 0
    profile_backlog_health: RecentProfileBacklogHealth = field(
        default_factory=RecentProfileBacklogHealth
    )
    issue_codes: list[str] = field(default_factory=list)

    def add_issue(self, code: object) -> None:
        safe_code = normalize_profile_error_code(code)
        if safe_code not in self.issue_codes:
            self.issue_codes.append(safe_code)
        if self.status == "done":
            self.status = "warning"

    def safe_payload(self) -> dict[str, object]:
        profile_backlog_health = self.profile_backlog_health.safe_payload()
        return {
            "summary_kind": RECENT_PROFILE_WORKER_SUMMARY_KIND,
            **asdict(self),
            "profile_backlog_health": profile_backlog_health,
            "next_step": recent_profile_worker_next_step(
                jobs_claimed=self.jobs_claimed,
                jobs_completed=self.jobs_completed,
                jobs_retried=self.jobs_retried,
                jobs_failed=self.jobs_failed,
                jobs_lease_lost=self.jobs_lease_lost,
                analysis_cache_records=self.analysis_cache_records,
                profile_artifact_records=self.profile_artifact_records,
            ),
            "profile_backlog_next_step": recent_profile_backlog_next_step(
                **{key: profile_backlog_health[key] for key in PROFILE_BACKLOG_COUNT_KEYS}
            ),
        }


ProfileJobProcessor = Callable[
    [RecentProfileJobRecord, BatchConfig, dict[str, str], Path],
    RecentProfileWorkerJobOutcome,
]


def run_recent_profile_worker(
    *,
    store: RecentProfileBudgetStoreBackend,
    config: BatchConfig,
    env: dict[str, str],
    repo_root: Path,
    options: RecentProfileWorkerOptions | None = None,
    progress: ProgressWriter | None = None,
    now: datetime | None = None,
    processor: ProfileJobProcessor | None = None,
) -> RecentProfileWorkerResult:
    worker_options = normalize_worker_options(options or RecentProfileWorkerOptions())
    observed_at = utc_now(now)
    lease_owner = normalize_profile_lease_owner(worker_options.lease_owner)
    lease_until = observed_at + timedelta(seconds=worker_options.lease_seconds)
    result = RecentProfileWorkerResult(observed_at_iso=observed_at.isoformat())
    source_key = recent_history_source_key(config)
    age_out_stale_profile_jobs(result=result, store=store, config=config, observed_at=observed_at)
    progress_emit(
        progress,
        stage="recent_profile_worker",
        status="claiming",
        max_jobs=worker_options.max_jobs,
    )
    try:
        jobs = store.claim_profile_jobs(
            max_jobs=worker_options.max_jobs,
            lease_owner=lease_owner,
            lease_until_iso=lease_until.isoformat(),
            now_iso=observed_at.isoformat(),
            engine="impala",
            source_kind=config.query_profile_source,
            source_key=source_key,
        )
    except (OSError, RecentHistoryStoreError):
        result.add_issue("recent_profile_worker_claim_failed")
        attach_profile_backlog_health(
            result=result,
            store=store,
            config=config,
            observed_at=observed_at,
        )
        progress_emit(progress, stage="recent_profile_worker", status="warning")
        return result

    result.jobs_claimed = len(jobs)
    progress_emit(
        progress,
        stage="recent_profile_worker",
        status="claimed",
        jobs_claimed=result.jobs_claimed,
    )
    if processor is None:
        from query_doctor.recent.profile_worker_processor import process_recent_profile_job

        job_processor = process_recent_profile_job
    else:
        job_processor = processor
    for index, job in enumerate(jobs, start=1):
        process_claimed_job(
            store=store,
            job=job,
            config=config,
            env=env,
            repo_root=repo_root,
            lease_owner=lease_owner,
            options=worker_options,
            result=result,
            processor=job_processor,
            progress=progress,
            job_index=index,
        )
    completed_at = utc_now(now)
    result.observed_at_iso = completed_at.isoformat()
    attach_profile_backlog_health(
        result=result,
        store=store,
        config=config,
        observed_at=completed_at,
    )
    progress_emit(
        progress,
        stage="recent_profile_worker",
        status=result.status,
        **result_counts(result),
    )
    return result


def process_claimed_job(
    *,
    store: RecentProfileBudgetStoreBackend,
    job: RecentProfileJobRecord,
    config: BatchConfig,
    env: dict[str, str],
    repo_root: Path,
    lease_owner: str,
    options: RecentProfileWorkerOptions,
    result: RecentProfileWorkerResult,
    processor: ProfileJobProcessor,
    progress: ProgressWriter | None,
    job_index: int,
) -> None:
    progress_emit(
        progress,
        stage="recent_profile_worker_job",
        status="started",
        job_index=job_index,
    )
    if not renew_worker_lease(store, job, lease_owner=lease_owner, options=options):
        result.jobs_lease_lost += 1
        result.add_issue("recent_profile_worker_lease_lost")
        progress_emit(
            progress,
            stage="recent_profile_worker_job",
            status="lease_lost",
            job_index=job_index,
        )
        return
    try:
        outcome = processor(job, config, env, repo_root)
    except Exception:  # noqa: BLE001 - worker summaries must not expose exception text.
        outcome = RecentProfileWorkerJobOutcome(
            status="retry",
            error_code="recent_profile_worker_processor_failed",
            retry=True,
        )
    if outcome.status == "completed":
        complete_worker_job(
            store=store,
            job=job,
            lease_owner=lease_owner,
            outcome=outcome,
            result=result,
            progress=progress,
            job_index=job_index,
        )
        return
    fail_worker_job(
        store=store,
        job=job,
        lease_owner=lease_owner,
        outcome=outcome,
        options=options,
        result=result,
        progress=progress,
        job_index=job_index,
    )


def complete_worker_job(
    *,
    store: RecentProfileBudgetStoreBackend,
    job: RecentProfileJobRecord,
    lease_owner: str,
    outcome: RecentProfileWorkerJobOutcome,
    result: RecentProfileWorkerResult,
    progress: ProgressWriter | None,
    job_index: int,
) -> None:
    completed_at = utc_now().isoformat()
    if not outcome.profile_fingerprint:
        fail_completed_worker_job(
            store=store,
            job=job,
            lease_owner=lease_owner,
            error_code="recent_profile_worker_missing_fingerprint",
            options=RecentProfileWorkerOptions(),
            result=result,
            progress=progress,
            job_index=job_index,
        )
        return
    if outcome.analysis_payload is None:
        fail_completed_worker_job(
            store=store,
            job=job,
            lease_owner=lease_owner,
            error_code="recent_profile_worker_missing_analysis_cache",
            options=RecentProfileWorkerOptions(),
            result=result,
            progress=progress,
            job_index=job_index,
        )
        return
    if outcome.artifact_storage_key is None:
        fail_completed_worker_job(
            store=store,
            job=job,
            lease_owner=lease_owner,
            error_code="recent_profile_worker_missing_artifact_metadata",
            options=RecentProfileWorkerOptions(),
            result=result,
            progress=progress,
            job_index=job_index,
        )
        return
    try:
        cache_count = store.store_analysis_cache_records(
            [
                RecentAnalysisCacheRecord(
                    schema_version=ANALYSIS_CACHE_SCHEMA_VERSION,
                    engine=job.engine,
                    source_kind=job.source_kind,
                    source_key=job.source_key,
                    query_id=job.query_id,
                    profile_fingerprint=outcome.profile_fingerprint,
                    analyzer_contract=outcome.analyzer_contract,
                    recorded_at_iso=completed_at,
                    status=ANALYSIS_CACHE_STATUS_READY,
                    payload=outcome.analysis_payload,
                )
            ]
        )
        if cache_count <= 0:
            fail_completed_worker_job(
                store=store,
                job=job,
                lease_owner=lease_owner,
                error_code="recent_profile_worker_analysis_cache_rejected",
                options=RecentProfileWorkerOptions(),
                result=result,
                progress=progress,
                job_index=job_index,
            )
            return
        result.analysis_cache_records += cache_count
        artifact_count = store.store_profile_artifact_records(
            [
                RecentProfileArtifactRecord(
                    schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
                    engine=job.engine,
                    source_kind=job.source_kind,
                    source_key=job.source_key,
                    query_id=job.query_id,
                    profile_fingerprint=outcome.profile_fingerprint,
                    artifact_contract=outcome.artifact_contract,
                    recorded_at_iso=completed_at,
                    status=PROFILE_ARTIFACT_STATUS_AVAILABLE,
                    storage_kind=outcome.artifact_storage_kind,
                    storage_key=outcome.artifact_storage_key,
                    size_bytes=outcome.artifact_size_bytes,
                )
            ]
        )
        if artifact_count <= 0:
            fail_completed_worker_job(
                store=store,
                job=job,
                lease_owner=lease_owner,
                error_code="recent_profile_worker_artifact_metadata_rejected",
                options=RecentProfileWorkerOptions(),
                result=result,
                progress=progress,
                job_index=job_index,
            )
            return
        result.profile_artifact_records += artifact_count
        completed = store.complete_profile_job(
            engine=job.engine,
            source_kind=job.source_kind,
            source_key=job.source_key,
            query_id=job.query_id,
            lease_owner=lease_owner,
            completed_at_iso=completed_at,
        )
    except (OSError, RecentHistoryStoreError):
        result.add_issue("recent_profile_worker_store_failed")
        return
    if completed:
        result.jobs_completed += 1
        progress_emit(
            progress,
            stage="recent_profile_worker_job",
            status="completed",
            job_index=job_index,
        )
        return
    result.jobs_lease_lost += 1
    result.add_issue("recent_profile_worker_complete_lease_lost")
    progress_emit(
        progress,
        stage="recent_profile_worker_job",
        status="lease_lost",
        job_index=job_index,
    )


def fail_completed_worker_job(
    *,
    store: RecentProfileBudgetStoreBackend,
    job: RecentProfileJobRecord,
    lease_owner: str,
    error_code: str,
    options: RecentProfileWorkerOptions,
    result: RecentProfileWorkerResult,
    progress: ProgressWriter | None,
    job_index: int,
) -> None:
    fail_worker_job(
        store=store,
        job=job,
        lease_owner=lease_owner,
        outcome=RecentProfileWorkerJobOutcome(status="failed", error_code=error_code),
        options=options,
        result=result,
        progress=progress,
        job_index=job_index,
    )


def fail_worker_job(
    *,
    store: RecentProfileBudgetStoreBackend,
    job: RecentProfileJobRecord,
    lease_owner: str,
    outcome: RecentProfileWorkerJobOutcome,
    options: RecentProfileWorkerOptions,
    result: RecentProfileWorkerResult,
    progress: ProgressWriter | None,
    job_index: int,
) -> None:
    failed_at = utc_now().isoformat()
    error_code = normalize_profile_error_code(outcome.error_code or outcome.status)
    retry = bool(outcome.retry or outcome.status == "retry")
    if retry and job.attempts >= options.max_attempts:
        retry = False
        retry_suffix = "_retry_exhausted"
        error_code = normalize_profile_error_code(
            f"{error_code[: 96 - len(retry_suffix)].rstrip('_')}{retry_suffix}"
        )
    try:
        failed = store.fail_profile_job(
            engine=job.engine,
            source_kind=job.source_kind,
            source_key=job.source_key,
            query_id=job.query_id,
            lease_owner=lease_owner,
            failed_at_iso=failed_at,
            error_code=error_code,
            retry=retry,
        )
    except (OSError, RecentHistoryStoreError):
        result.add_issue("recent_profile_worker_fail_transition_failed")
        return
    if not failed:
        result.jobs_lease_lost += 1
        result.add_issue("recent_profile_worker_fail_lease_lost")
        progress_emit(
            progress,
            stage="recent_profile_worker_job",
            status="lease_lost",
            job_index=job_index,
        )
        return
    if retry:
        result.jobs_retried += 1
        progress_emit(
            progress,
            stage="recent_profile_worker_job",
            status="retry",
            job_index=job_index,
            error_code=error_code,
        )
    else:
        result.jobs_failed += 1
        progress_emit(
            progress,
            stage="recent_profile_worker_job",
            status="failed",
            job_index=job_index,
            error_code=error_code,
        )


def renew_worker_lease(
    store: RecentProfileBudgetStoreBackend,
    job: RecentProfileJobRecord,
    *,
    lease_owner: str,
    options: RecentProfileWorkerOptions,
) -> bool:
    now = utc_now()
    lease_until = now + timedelta(seconds=options.lease_seconds)
    try:
        return store.renew_profile_job_lease(
            engine=job.engine,
            source_kind=job.source_kind,
            source_key=job.source_key,
            query_id=job.query_id,
            lease_owner=lease_owner,
            lease_until_iso=lease_until.isoformat(),
            now_iso=now.isoformat(),
        )
    except (OSError, RecentHistoryStoreError):
        return False


def normalize_worker_options(options: RecentProfileWorkerOptions) -> RecentProfileWorkerOptions:
    return RecentProfileWorkerOptions(
        max_jobs=max(0, int(options.max_jobs)),
        lease_owner=normalize_profile_lease_owner(options.lease_owner),
        lease_seconds=max(1, int(options.lease_seconds)),
        max_attempts=max(1, int(options.max_attempts)),
    )


def utc_now(value: datetime | None = None) -> datetime:
    observed_at = value or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.astimezone(timezone.utc).replace(microsecond=0)


def result_counts(result: RecentProfileWorkerResult) -> dict[str, object]:
    payload = result.safe_payload()
    return {
        key: value
        for key, value in payload.items()
        if key not in {"summary_kind", "status", "next_step", "profile_backlog_next_step"}
    }


def profile_window_start(config: BatchConfig, observed_at: datetime) -> datetime | None:
    """Start of the window in which a finished query's profile can still be fetched."""
    max_age_hours = config.profile_job_max_age_hours
    if max_age_hours <= 0:
        return None
    return observed_at - timedelta(hours=max_age_hours)


def age_out_stale_profile_jobs(
    *,
    result: RecentProfileWorkerResult,
    store: RecentProfileBudgetStoreBackend,
    config: BatchConfig,
    observed_at: datetime,
) -> None:
    window_start = profile_window_start(config, observed_at)
    if window_start is None:
        return
    try:
        result.jobs_aged_out = store.age_out_profile_jobs(
            # Both sources store end times as UTC ISO-8601 text ending in Z, compared as
            # strings; CM's fractional seconds shift the cutoff by under a second.
            cutoff_iso=window_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            now_iso=observed_at.isoformat(),
            engine="impala",
            source_kind=config.query_profile_source,
            source_key=recent_history_source_key(config),
        )
    except (OSError, RecentHistoryStoreError):
        result.add_issue("recent_profile_worker_age_out_failed")


def attach_profile_backlog_health(
    *,
    result: RecentProfileWorkerResult,
    store: RecentProfileBudgetStoreBackend,
    config: BatchConfig,
    observed_at: datetime,
) -> None:
    window_start = profile_window_start(config, observed_at)
    window_kwargs: dict[str, object] = {}
    if window_start is not None:
        window_kwargs = {
            "window_start_iso": window_start.isoformat(),
            "window_hours": config.profile_job_max_age_hours,
        }
    try:
        result.profile_backlog_health = store.summarize_profile_backlog_health(
            now_iso=observed_at.isoformat(),
            engine="impala",
            source_kind=config.query_profile_source,
            source_key=recent_history_source_key(config),
            **window_kwargs,
        )
    except (OSError, RecentHistoryStoreError):
        result.add_issue("recent_profile_worker_backlog_health_failed")


def recent_profile_worker_next_step(
    *,
    jobs_claimed: object,
    jobs_completed: object,
    jobs_retried: object,
    jobs_failed: object,
    jobs_lease_lost: object,
    analysis_cache_records: object,
    profile_artifact_records: object,
) -> str:
    claimed = safe_worker_count(jobs_claimed)
    completed = safe_worker_count(jobs_completed)
    retried = safe_worker_count(jobs_retried)
    failed = safe_worker_count(jobs_failed)
    lease_lost = safe_worker_count(jobs_lease_lost)
    cache_records = safe_worker_count(analysis_cache_records)
    artifact_records = safe_worker_count(profile_artifact_records)
    if lease_lost:
        return "Check worker concurrency and lease duration before the next run."
    if failed:
        return (
            "Review normalized worker error codes, fix collection or materialization "
            "settings, then requeue according to operator policy."
        )
    if retried:
        return (
            "Let the next worker run retry pending jobs; investigate repeated "
            "normalized error codes."
        )
    if claimed <= 0:
        return (
            "No matching jobs were claimed; run a discover-only refresh or check backlog filters."
        )
    if completed and (cache_records < completed or artifact_records < completed):
        return "Check materialization counters before expecting Online History Details."
    if completed:
        return "Refresh Online History to see newly materialized Details."
    return "No worker action is needed from this run."


def recent_profile_backlog_next_step(
    *,
    pending_jobs: object,
    retry_pending_jobs: object,
    leased_jobs: object,
    stale_leased_jobs: object,
    failed_jobs: object,
) -> str:
    pending = safe_worker_count(pending_jobs)
    retry_pending = safe_worker_count(retry_pending_jobs)
    leased = safe_worker_count(leased_jobs)
    stale_leased = safe_worker_count(stale_leased_jobs)
    failed = safe_worker_count(failed_jobs)
    active_leased = max(0, leased - stale_leased)
    if stale_leased:
        return (
            "Run the Recent profile worker to reclaim expired leases; check worker "
            "lease duration if stale leases persist."
        )
    if failed:
        return "Run profile remediation dry-run before requeueing terminal failed profile jobs."
    if retry_pending:
        return (
            "Let the profile worker retry pending rows; investigate repeated normalized "
            "error codes if retry backlog persists."
        )
    if pending:
        return "Run or schedule the Recent profile worker to materialize Details."
    if active_leased:
        return "Wait for active worker leases before starting another worker."
    return "No profile backlog is waiting."


def safe_worker_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def progress_emit(progress: ProgressWriter | None, **event: object) -> None:
    if progress is not None:
        progress.emit(**event)


def worker_result_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def format_recent_profile_worker_result(payload: Mapping[str, object]) -> str:
    backlog_health = payload.get("profile_backlog_health")
    backlog_values = backlog_health if isinstance(backlog_health, Mapping) else {}
    lines = [
        f"Recent profile worker: {payload.get('status', 'unknown')}",
        f"- jobs claimed: {payload.get('jobs_claimed', 0)}",
        f"- jobs completed: {payload.get('jobs_completed', 0)}",
        f"- jobs retried: {payload.get('jobs_retried', 0)}",
        f"- jobs failed: {payload.get('jobs_failed', 0)}",
        f"- lease-lost jobs: {payload.get('jobs_lease_lost', 0)}",
        f"- analysis-cache records: {payload.get('analysis_cache_records', 0)}",
        f"- profile-artifact records: {payload.get('profile_artifact_records', 0)}",
    ]
    if backlog_values:
        lines.append(
            "- profile backlog: "
            f"pending={safe_worker_count(backlog_values.get('pending_jobs'))} "
            f"retry={safe_worker_count(backlog_values.get('retry_pending_jobs'))} "
            f"leased={safe_worker_count(backlog_values.get('leased_jobs'))} "
            f"stale_leased={safe_worker_count(backlog_values.get('stale_leased_jobs'))} "
            f"failed={safe_worker_count(backlog_values.get('failed_jobs'))}"
        )
    issue_codes = payload.get("issue_codes")
    if isinstance(issue_codes, list) and issue_codes:
        lines.append(f"- issue codes: {', '.join(str(code) for code in issue_codes)}")
    next_step = payload.get("next_step")
    if isinstance(next_step, str) and next_step:
        lines.append(f"- next step: {next_step}")
    backlog_next_step = payload.get("profile_backlog_next_step")
    if isinstance(backlog_next_step, str) and backlog_next_step:
        lines.append(f"- profile backlog next step: {backlog_next_step}")
    return "\n".join(lines) + "\n"
