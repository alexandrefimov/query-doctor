"""Default processor for one Recent profile worker job."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from query_doctor.recent.batch_models import BatchConfig, CaseResult
from query_doctor.recent.batch_scoring import score_case
from query_doctor.recent.batch_summary import case_to_summary
from query_doctor.recent.case_processing import (
    collect_case_profile,
    metadata_subprocess_env,
    run_analysis_pass,
)
from query_doctor.recent.history_store import recent_history_source_key
from query_doctor.recent.profile_budget import (
    ANALYSIS_CACHE_SUMMARY_FIELDS,
    PROFILE_JOB_NOT_FOUND_ERROR_CODE,
    RecentProfileJobRecord,
)
from query_doctor.recent.profile_worker import (
    RECENT_PROFILE_WORKER_ANALYZER_CONTRACT,
    RecentProfileWorkerJobOutcome,
)


_RETRYABLE_COLLECTION_FAILURES = {
    "profile_collection_failed",
    "profile_collection_timeout",
}
_RETRYABLE_ANALYSIS_FAILURES = {
    "analysis_or_metadata_timeout",
}


def process_recent_profile_job(
    job: RecentProfileJobRecord,
    config: BatchConfig,
    env: dict[str, str],
    repo_root: Path,
) -> RecentProfileWorkerJobOutcome:
    expected_source_key = recent_history_source_key(config)
    if job.engine != "impala":
        return RecentProfileWorkerJobOutcome(
            status="failed",
            error_code="recent_profile_worker_unsupported_engine",
        )
    if job.source_kind != config.query_profile_source or job.source_key != expected_source_key:
        return RecentProfileWorkerJobOutcome(
            status="retry",
            retry=True,
            error_code="recent_profile_worker_source_mismatch",
        )
    worker_root = config.out / "profile-worker-cases"
    wrapper_dir = worker_root / f"job-{uuid4().hex}"
    case = CaseResult(
        index=1,
        query_id=job.query_id,
        duration_sec=None,
        user=None,
        pool=None,
        query_type=None,
        sql_verb=None,
        wrapper_dir=wrapper_dir,
    )
    try:
        collect_case_profile(
            config, case, env=env, repo_root=repo_root, collect_cm_timeseries=False
        )
        if case.failure_category == PROFILE_JOB_NOT_FOUND_ERROR_CODE:
            # Every endpoint answered that it no longer holds the query: the
            # profile is gone for good, as it is for a query past the window.
            return RecentProfileWorkerJobOutcome(
                status="aged_out", error_code=PROFILE_JOB_NOT_FOUND_ERROR_CODE
            )
        if case.collection_status != "ok":
            retry = case.failure_category in _RETRYABLE_COLLECTION_FAILURES
            return RecentProfileWorkerJobOutcome(
                status="retry" if retry else "failed",
                retry=retry,
                error_code=_profile_collection_error_code(case),
            )
        # The mode is the deployment's to choose. Forcing it off here meant the
        # worker never collected metadata whatever the config said, and it is the
        # only component that analyzes a case: the collector runs discover-only,
        # so the separate refresh_top_metadata pass is never reached either.
        # metadata_subprocess_env carries the table names extracted from the
        # statement during collection; without it the pipeline sees only the
        # redacted facts, where every identifier is a placeholder.
        run_analysis_pass(
            config,
            case,
            env=metadata_subprocess_env(env, case),
            repo_root=repo_root,
        )
        if case.analysis_status != "ok":
            retry = case.failure_category in _RETRYABLE_ANALYSIS_FAILURES
            return RecentProfileWorkerJobOutcome(
                status="retry" if retry else "failed",
                retry=retry,
                error_code=case.failure_category or "recent_profile_worker_analysis_failed",
            )
        score_case(case)
        profile_digest_path = case_profile_digest_path(case)
        if profile_digest_path is None:
            return RecentProfileWorkerJobOutcome(
                status="failed",
                error_code="recent_profile_worker_profile_digest_missing",
            )
        profile_fingerprint = digest_file_fingerprint(profile_digest_path)
        payload = analysis_cache_payload(case_to_summary(case))
        payload["case_artifact_contract"] = RECENT_PROFILE_WORKER_ANALYZER_CONTRACT
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint=profile_fingerprint,
            analysis_payload=payload,
            artifact_storage_key=profile_fingerprint,
            artifact_size_bytes=safe_file_size(profile_digest_path),
        )
    finally:
        cleanup_worker_case_dir(wrapper_dir, worker_root)


def _profile_collection_error_code(case: CaseResult) -> str:
    """Retain only canonical raw-free HTTP and endpoint-timeout reasons."""
    fallback = case.failure_category or "recent_profile_worker_collection_failed"
    reason = case.failure_reason
    if fallback != "profile_collection_failed" or not isinstance(reason, str) or len(reason) > 96:
        return fallback
    if reason == "Impala profile endpoint profile collection timed out.":
        return "profile_fetch_timeout"
    for source in ("Cloudera Manager", "Impala profile endpoint"):
        prefix = f"{source} profile collection returned HTTP "
        if reason.startswith(prefix) and reason.endswith("."):
            status = reason[len(prefix) : -1]
            if (
                len(status) == 3
                and status.isascii()
                and status.isdecimal()
                and 100 <= int(status) <= 599
            ):
                return f"profile_fetch_http_{status}"
    return fallback


def analysis_cache_payload(case_summary: Mapping[str, object]) -> dict[str, object]:
    return {key: case_summary[key] for key in ANALYSIS_CACHE_SUMMARY_FIELDS if key in case_summary}


def case_profile_digest_path(case: CaseResult) -> Path | None:
    if case.actual_case_dir is None:
        return None
    path = case.actual_case_dir / "profile_digest.md"
    return path if path.is_file() else None


def digest_file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256_{digest}"


def safe_file_size(path: Path) -> int | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    return size if size >= 0 else None


def cleanup_worker_case_dir(wrapper_dir: Path, worker_root: Path) -> None:
    try:
        resolved_root = worker_root.resolve(strict=False)
        resolved_wrapper = wrapper_dir.resolve(strict=False)
    except OSError:
        return
    if resolved_wrapper.parent != resolved_root:
        return
    if not resolved_wrapper.name.startswith("job-"):
        return
    if wrapper_dir.is_symlink() or not wrapper_dir.is_dir():
        return
    shutil.rmtree(wrapper_dir, ignore_errors=True)
