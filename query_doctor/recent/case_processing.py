"""Recent batch case collection and analyzer execution."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from query_doctor.cli.commands import command_prefix, command_spec
from query_doctor.impala.metadata_workflow import (
    METADATA_SOURCE_TABLES_ENV,
)
from query_doctor.impala.profile_source import PROFILE_NOT_FOUND_EVERYWHERE_ERROR
from query_doctor.metadata_source_tables import read_metadata_source_tables
from query_doctor.recent.batch_config import elapsed_seconds, format_seconds
from query_doctor.recent.batch_models import BatchConfig, CaseResult
from query_doctor.recent.batch_scoring import inspect_case_outputs, score_case
from query_doctor.recent.batch_summary import batch_ranking_key
from query_doctor.recent.metadata_collectable import update_collectable_metadata_table_count
from query_doctor.recent.command_args import (
    append_cm_config_args,
    append_cm_connection_args,
    append_metadata_args,
)
from query_doctor.recent.metadata_refresh import (
    mark_metadata_not_requested,
    metadata_refresh_skip_reason,
    rank_cases_for_metadata,
    select_metadata_refresh_candidates_for_config,
)
from query_doctor.recent.progress import ProgressWriter


PROFILE_COLLECTION_TIMEOUT_SEC = 900
ANALYSIS_TIMEOUT_SEC = 900
METADATA_ANALYSIS_TIMEOUT_SEC = 1800
REPORT_TIMEOUT_SEC = 2400
RUNTIME_METRICS_REFRESH_TIMEOUT_SEC = 300
DEFAULT_SUBPROCESS_TIMEOUT_SEC = 900
SUBPROCESS_TIMEOUT_RETURN_CODE = 124
MAX_CM_TIMESERIES_REFRESH_JOBS = 5
PROFILE_COLLECTION_MAX_ATTEMPTS = 2
CM_PROFILE_FAILURE_BREAKER_MIN_FAILURES = 5
CM_PROFILE_FAILURE_BREAKER_CONSECUTIVE_FAILURES = 5
CM_PROFILE_FAILURE_BREAKER_MIN_COMPLETED = 10
CM_PROFILE_FAILURE_BREAKER_FAILURE_RATIO = 0.5
CM_PROFILE_FAILURE_BREAKER_REASON = (
    "Cloudera Manager profile collection was stopped after repeated HTTP 5xx "
    "responses; reduce --cm-jobs or wait for Service Monitor to recover before "
    "rerunning the batch."
)
HTTP_STATUS_RE = re.compile(r"\bHTTP(?:\s+Error)?\s+([1-5][0-9][0-9])\b", re.IGNORECASE)
IMPALA_PROFILE_FAILURE_PREFIX = "Single-query Impala profile collection failed: "
IMPALA_PROFILE_NOT_FOUND_SUFFIX = f"Last safe error: {PROFILE_NOT_FOUND_EVERYWHERE_ERROR}."


@dataclass
class CmProfileCollectionCircuitBreaker:
    total_completed: int = 0
    http_5xx_failures: int = 0
    consecutive_http_5xx_failures: int = 0
    tripped: bool = False

    def record(self, case: CaseResult) -> bool:
        self.total_completed += 1
        if case_is_cm_http_5xx_collection_failure(case):
            self.http_5xx_failures += 1
            self.consecutive_http_5xx_failures += 1
        else:
            self.consecutive_http_5xx_failures = 0
        self.tripped = self.tripped or self.should_trip()
        return self.tripped

    def should_trip(self) -> bool:
        if self.http_5xx_failures < CM_PROFILE_FAILURE_BREAKER_MIN_FAILURES:
            return False
        if self.consecutive_http_5xx_failures >= CM_PROFILE_FAILURE_BREAKER_CONSECUTIVE_FAILURES:
            return True
        if self.total_completed < CM_PROFILE_FAILURE_BREAKER_MIN_COMPLETED:
            return False
        return (
            self.http_5xx_failures / self.total_completed
            >= CM_PROFILE_FAILURE_BREAKER_FAILURE_RATIO
        )

    def warning(self) -> str:
        return (
            f"{CM_PROFILE_FAILURE_BREAKER_REASON} "
            f"HTTP 5xx failures: {self.http_5xx_failures}/{self.total_completed} "
            "completed profile collection tasks."
        )


def cm_profile_collection_circuit_breaker(
    config: BatchConfig,
) -> CmProfileCollectionCircuitBreaker | None:
    if config.query_profile_source != "cm":
        return None
    return CmProfileCollectionCircuitBreaker()


def case_is_cm_http_5xx_collection_failure(case: CaseResult) -> bool:
    if case.failure_category != "profile_collection_failed":
        return False
    if not case.failure_reason:
        return False
    match = HTTP_STATUS_RE.search(case.failure_reason)
    return bool(match and match.group(1).startswith("5"))


def mark_unsubmitted_profile_collection_cases(cases: Iterable[CaseResult]) -> None:
    for case in cases:
        if case.collection_status != "not_started":
            continue
        case.collection_status = "skipped"
        case.failure_category = "profile_collection_skipped"
        case.failure_reason = CM_PROFILE_FAILURE_BREAKER_REASON


def collect_scan_cm_events(
    config: BatchConfig,
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> tuple[dict[str, object] | None, str | None]:
    if config.query_profile_source != "cm":
        progress.emit(stage="cm_events", status="skipped", reason="query_profile_source=impala")
        return None, None
    if not config.collect_cm_events:
        progress.emit(stage="cm_events", status="skipped", reason="collect_cm_events=false")
        return None, None
    started = time.monotonic()
    progress.emit(stage="cm_events", status="started", max_events=config.cm_events_max_events)
    cluster_context_path = config.out / "cluster_context.json"
    cluster_event_context_path = config.out / "cluster_event_context.json"
    cmd = command_prefix(repo_root, "cm_events") + [
        "--max-events",
        str(config.cm_events_max_events),
        "--cluster-event-context-json",
        str(cluster_event_context_path),
        "--cluster-context-json",
        str(cluster_context_path),
    ]
    if config.only_running or not (config.from_time and config.to_time):
        cmd.extend(["--window-minutes", str(config.recent_window_minutes)])
    else:
        cmd.extend(["--from-time", config.from_time, "--to-time", config.to_time])
    append_cm_connection_args(cmd, config)
    result = run_subprocess(cmd, cwd=repo_root, env=env)
    context = read_cluster_context_json(cluster_context_path)
    if context is None:
        progress.emit(stage="cm_events", status="failed", seconds=elapsed_seconds(started))
        return None, "Cluster event context was requested but no safe cluster context was produced."
    status = "done" if result.returncode == 0 else "partial"
    progress.emit(
        stage="cm_events",
        status=status,
        product_status=context.get("status"),
        seconds=elapsed_seconds(started),
    )
    warning = None
    if result.returncode != 0:
        warning = "Cluster event context was partial or unavailable; query analysis continued without treating events as proof."
    return context, warning


def read_cluster_context_json(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def collect_case_profile(
    config: BatchConfig,
    case: CaseResult,
    *,
    env: dict[str, str],
    repo_root: Path,
    collect_cm_timeseries: bool | None = None,
    out_dir: Path | None = None,
) -> None:
    started = time.monotonic()
    try:
        target_out_dir = out_dir or case.wrapper_dir
        target_out_dir.mkdir(parents=True, exist_ok=True)
        metadata_source_tables_out = (
            target_out_dir / ".metadata-source-tables.json"
            if direct_impala_metadata_source_tables_enabled(config)
            else None
        )
        if config.query_profile_source == "impala":
            cmd = command_prefix(repo_root, "collect_impala_profile") + [
                "--query-id",
                case.query_id,
                "--out",
                str(target_out_dir),
                "--redact",
                "--max-profile-bytes",
                str(config.max_profile_bytes),
                "--port",
                str(config.impala_profile_port),
                "--scheme",
                config.impala_profile_scheme,
                "--timeout-sec",
                str(config.impala_profile_timeout_sec),
            ]
            if metadata_source_tables_out is not None:
                cmd.extend(["--metadata-source-tables-out", str(metadata_source_tables_out)])
            for host in config.impala_profile_hosts:
                cmd.extend(["--host", host])
            if config.impala_profile_prefer_json:
                cmd.append("--prefer-json-profile")
            if config.impala_profile_collect_docs:
                cmd.append("--collect-profile-docs")
            if config.impala_collect_admission_context:
                cmd.append("--collect-admission-context")
            if config.redact_identifiers:
                cmd.append("--redact-identifiers")
            if not config.redact_hosts:
                cmd.append("--no-redact-hosts")
            include_prometheus = (
                config.collect_prometheus_timeseries
                if collect_cm_timeseries is None
                else collect_cm_timeseries
            )
            if include_prometheus and config.prometheus_url:
                cmd.extend(
                    [
                        "--prometheus-url",
                        config.prometheus_url,
                        "--collect-prometheus-timeseries",
                        "--prometheus-metrics-profile",
                        config.prometheus_metrics_profile,
                        "--prometheus-step-sec",
                        str(config.prometheus_step_sec),
                        "--prometheus-timeseries-padding-sec",
                        str(config.prometheus_timeseries_padding_sec),
                        "--prometheus-timeout-sec",
                        str(config.prometheus_timeout_sec),
                        "--max-timeseries-bytes",
                        str(config.max_timeseries_bytes),
                        "--max-timeseries-points",
                        str(config.max_timeseries_points),
                    ]
                )
        else:
            cmd = command_prefix(repo_root, "collect_cm") + [
                "--query-id",
                case.query_id,
                "--out",
                str(target_out_dir),
                "--redact",
                "--limit",
                "1",
                "--max-profile-bytes",
                str(config.max_profile_bytes),
            ]
            include_cm_timeseries = (
                config.collect_cm_timeseries
                if collect_cm_timeseries is None
                else collect_cm_timeseries
            )
            if include_cm_timeseries:
                cmd.extend(
                    [
                        "--collect-cm-timeseries",
                        "--cm-metrics-profile",
                        config.cm_metrics_profile,
                        "--cm-timeseries-padding-sec",
                        str(config.cm_timeseries_padding_sec),
                        "--max-timeseries-bytes",
                        str(config.max_timeseries_bytes),
                        "--max-timeseries-points",
                        str(config.max_timeseries_points),
                    ]
                )
            else:
                cmd.append("--no-collect-cm-timeseries")
            append_cm_config_args(cmd, config)
        result = run_profile_collection_subprocess(cmd, cwd=repo_root, env=env)
        if result.returncode != 0:
            if result.returncode == SUBPROCESS_TIMEOUT_RETURN_CODE:
                case.collection_status = "timeout"
                case.failure_category = "profile_collection_timeout"
            elif config.query_profile_source == "impala" and impala_profile_not_found(result):
                case.collection_status = "failed"
                case.failure_category = "profile_not_found"
            else:
                case.collection_status = "failed"
                case.failure_category = "profile_collection_failed"
            case.failure_reason = profile_collection_failure_reason(config, result)
            return
        profile_paths = sorted(target_out_dir.rglob("profile_digest.md"))
        if not profile_paths:
            case.collection_status = "failed"
            case.failure_category = "profile_digest_missing"
            case.failure_reason = "Profile collection finished but no profile digest was produced."
            return
        case.collection_status = "ok"
        if metadata_source_tables_out is not None:
            merge_case_metadata_source_tables(
                case,
                read_metadata_source_tables(metadata_source_tables_out),
            )
        if out_dir is None:
            case.actual_case_dir = profile_paths[0].parent
    finally:
        case.cm_collect_seconds = elapsed_seconds(started)


def direct_impala_metadata_source_tables_enabled(config: BatchConfig) -> bool:
    return (
        config.query_profile_source == "impala"
        and config.metadata_mode != "off"
        and config.metadata_top_limit > 0
    )


def merge_case_metadata_source_tables(case: CaseResult, tables: tuple[str, ...]) -> None:
    if not tables:
        return
    merged: list[str] = []
    for table in (*case.metadata_source_tables, *tables):
        if table not in merged:
            merged.append(table)
    case.metadata_source_tables = tuple(merged)


def process_cases(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> list[str]:
    collection_started = time.monotonic()
    progress.emit(
        stage="profile_collection", status="started", total=len(cases), cm_jobs=config.cm_jobs
    )
    if streaming_case_processing_enabled(config, cases):
        warnings = process_cases_streaming(
            config,
            cases,
            env=env,
            repo_root=repo_root,
            progress=progress,
            collection_started=collection_started,
        )
        refresh_top_cm_timeseries(config, cases, env=env, repo_root=repo_root, progress=progress)
        return warnings

    warnings = collect_cases(config, cases, env=env, repo_root=repo_root, progress=progress)
    progress.emit(
        stage="profile_collection",
        status="done",
        total=len(cases),
        seconds=elapsed_seconds(collection_started),
    )
    analysis_started = time.monotonic()
    progress.emit(stage="analyzer_scoring", status="started", total=len(cases), jobs=config.jobs)
    analyze_cases(config, cases, env=env, repo_root=repo_root, progress=progress)
    progress.emit(
        stage="analyzer_scoring",
        status="done",
        total=len(cases),
        seconds=elapsed_seconds(analysis_started),
    )
    refresh_top_cm_timeseries(config, cases, env=env, repo_root=repo_root, progress=progress)
    return warnings


def streaming_case_processing_enabled(config: BatchConfig, cases: list[CaseResult]) -> bool:
    return len(cases) > 1 and config.cm_jobs > 1 and config.jobs > 1


def process_cases_streaming(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
    collection_started: float,
) -> list[str]:
    analysis_started = time.monotonic()
    progress.emit(stage="analyzer_scoring", status="started", total=len(cases), jobs=config.jobs)
    analysis_futures = []
    breaker = cm_profile_collection_circuit_breaker(config)
    warning: str | None = None
    with ThreadPoolExecutor(max_workers=config.cm_jobs) as collection_executor:
        with ThreadPoolExecutor(max_workers=config.jobs) as analysis_executor:
            pending_cases = iter(cases)
            collection_futures: dict[Future[CaseResult], CaseResult] = {}

            def submit_next() -> bool:
                if breaker is not None and breaker.tripped:
                    return False
                try:
                    case = next(pending_cases)
                except StopIteration:
                    return False
                future = collection_executor.submit(
                    collect_case_for_batch,
                    config,
                    case,
                    env=env,
                    repo_root=repo_root,
                    progress=progress,
                )
                collection_futures[future] = case
                return True

            for _ in range(config.cm_jobs):
                if not submit_next():
                    break

            while collection_futures:
                done, _pending = wait(collection_futures, return_when=FIRST_COMPLETED)
                for future in done:
                    collection_futures.pop(future)
                    case = future.result()
                    if breaker is not None and breaker.record(case) and warning is None:
                        warning = breaker.warning()
                        progress.emit(
                            stage="profile_collection",
                            status="stopped",
                            reason="cm_http_5xx_circuit_breaker",
                            completed=breaker.total_completed,
                            http_5xx_failures=breaker.http_5xx_failures,
                        )
                        mark_unsubmitted_profile_collection_cases(pending_cases)
                    else:
                        submit_next()
                    if case.collection_status == "ok":
                        analysis_futures.append(
                            analysis_executor.submit(
                                analyze_case_for_batch,
                                config,
                                case,
                                env=env,
                                repo_root=repo_root,
                                progress=progress,
                            )
                        )
                    else:
                        print_case_progress(case)
            progress.emit(
                stage="profile_collection",
                status="done",
                total=len(cases),
                seconds=elapsed_seconds(collection_started),
            )
            for future in as_completed(analysis_futures):
                print_case_progress(future.result())
    progress.emit(
        stage="analyzer_scoring",
        status="done",
        total=len(cases),
        seconds=elapsed_seconds(analysis_started),
    )
    return [warning] if warning else []


def collect_cases(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> list[str]:
    if config.cm_jobs == 1:
        for case in cases:
            collect_case_for_batch(config, case, env=env, repo_root=repo_root, progress=progress)
        return []

    breaker = cm_profile_collection_circuit_breaker(config)
    warning: str | None = None
    with ThreadPoolExecutor(max_workers=config.cm_jobs) as executor:
        pending_cases = iter(cases)
        futures: dict[Future[CaseResult], CaseResult] = {}

        def submit_next() -> bool:
            if breaker is not None and breaker.tripped:
                return False
            try:
                case = next(pending_cases)
            except StopIteration:
                return False
            future = executor.submit(
                collect_case_for_batch,
                config,
                case,
                env=env,
                repo_root=repo_root,
                progress=progress,
            )
            futures[future] = case
            return True

        for _ in range(config.cm_jobs):
            if not submit_next():
                break

        while futures:
            done, _pending = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                case = future.result()
                if breaker is not None and breaker.record(case) and warning is None:
                    warning = breaker.warning()
                    progress.emit(
                        stage="profile_collection",
                        status="stopped",
                        reason="cm_http_5xx_circuit_breaker",
                        completed=breaker.total_completed,
                        http_5xx_failures=breaker.http_5xx_failures,
                    )
                    mark_unsubmitted_profile_collection_cases(pending_cases)
                else:
                    submit_next()
    return [warning] if warning else []


def analyze_cases(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> None:
    if config.jobs == 1:
        for case in cases:
            analyze_case_for_batch(config, case, env=env, repo_root=repo_root, progress=progress)
            print_case_progress(case)
        return

    with ThreadPoolExecutor(max_workers=config.jobs) as executor:
        futures = [
            executor.submit(
                analyze_case_for_batch,
                config,
                case,
                env=env,
                repo_root=repo_root,
                progress=progress,
            )
            for case in cases
        ]
        for future in as_completed(futures):
            print_case_progress(future.result())


def collect_case_for_batch(
    config: BatchConfig,
    case: CaseResult,
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> CaseResult:
    case_id = f"case-{case.index:03d}"
    if case.profile_reuse_status == "reused" and case.collection_status == "ok":
        progress.emit(
            stage="case",
            case_id=case_id,
            status="collection_reused",
            seconds=case.cm_collect_seconds,
        )
        return case
    progress.emit(stage="case", case_id=case_id, status="collection_started")
    collect_case_profile(config, case, env=env, repo_root=repo_root, collect_cm_timeseries=False)
    if case.collection_status != "ok":
        progress.emit(
            stage="case",
            case_id=case_id,
            status="failed",
            phase="collection",
            seconds=case.cm_collect_seconds,
        )
        return case
    progress.emit(
        stage="case", case_id=case_id, status="collection_done", seconds=case.cm_collect_seconds
    )
    return case


def refresh_top_cm_timeseries(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> None:
    if config.query_profile_source == "cm" and not config.collect_cm_timeseries:
        progress.emit(
            stage="cm_timeseries_refresh", status="skipped", reason="collect_cm_timeseries=false"
        )
        return
    if config.query_profile_source == "impala":
        if not config.collect_prometheus_timeseries:
            progress.emit(
                stage="cm_timeseries_refresh",
                status="skipped",
                reason="collect_prometheus_timeseries=false",
            )
            return
        if not config.prometheus_url:
            progress.emit(
                stage="cm_timeseries_refresh",
                status="skipped",
                reason="prometheus_url not configured",
            )
            return
    if config.cm_timeseries_top_limit <= 0:
        progress.emit(
            stage="cm_timeseries_refresh", status="skipped", reason="cm_timeseries_top_limit=0"
        )
        return
    ranked = [
        case
        for case in sorted(cases, key=batch_ranking_key)
        if case.collection_status == "ok"
        and case.analysis_status == "ok"
        and case.actual_case_dir is not None
    ]
    candidates = ranked[: config.cm_timeseries_top_limit]
    candidates = [
        case
        for case in candidates
        if not (case.profile_reuse_status == "reused" and case_has_runtime_metrics_context(case))
    ]
    if not candidates:
        progress.emit(
            stage="cm_timeseries_refresh",
            status="skipped",
            reason="no refreshable analyzed cases",
        )
        return
    jobs = cm_timeseries_refresh_jobs(config, len(candidates))
    started = time.monotonic()
    progress.emit(stage="cm_timeseries_refresh", status="started", total=len(candidates), jobs=jobs)
    if jobs == 1:
        for case in candidates:
            refresh_case_cm_timeseries(
                config,
                case,
                env=env,
                repo_root=repo_root,
                progress=progress,
            )
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [
                executor.submit(
                    refresh_case_cm_timeseries,
                    config,
                    case,
                    env=env,
                    repo_root=repo_root,
                    progress=progress,
                )
                for case in candidates
            ]
            for future in as_completed(futures):
                future.result()
    progress.emit(
        stage="cm_timeseries_refresh",
        status="done",
        total=len(candidates),
        jobs=jobs,
        seconds=elapsed_seconds(started),
    )


def cm_timeseries_refresh_jobs(config: BatchConfig, candidate_count: int) -> int:
    if candidate_count <= 1:
        return 1
    return max(1, min(candidate_count, config.cm_jobs, MAX_CM_TIMESERIES_REFRESH_JOBS))


def refresh_case_cm_timeseries(
    config: BatchConfig,
    case: CaseResult,
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> CaseResult:
    case_id = f"case-{case.index:03d}"
    if case.actual_case_dir is None:
        progress.emit(
            stage="cm_timeseries_refresh",
            case_id=case_id,
            status="failed",
            reason="case_dir_missing",
        )
        return case
    refresh_dir = case.wrapper_dir / f".cm-timeseries-refresh-{uuid4().hex}"
    progress.emit(stage="cm_timeseries_refresh", case_id=case_id, status="started")
    started = time.monotonic()
    try:
        refresh_case = replace(case, wrapper_dir=refresh_dir, actual_case_dir=None)
        collect_case_profile(
            config,
            refresh_case,
            env=env,
            repo_root=repo_root,
            collect_cm_timeseries=True,
            out_dir=refresh_dir,
        )
        canonical_context_paths = sorted(refresh_dir.rglob("runtime_metrics_context.json"))
        legacy_context_paths = sorted(refresh_dir.rglob("cm_timeseries_context.json"))
        context_paths = canonical_context_paths or legacy_context_paths
        context_paths_to_copy = canonical_context_paths + list(legacy_context_paths)
        if case.cm_collect_seconds is None:
            case.cm_collect_seconds = refresh_case.cm_collect_seconds
        elif refresh_case.cm_collect_seconds is not None:
            case.cm_collect_seconds = round(
                case.cm_collect_seconds + refresh_case.cm_collect_seconds, 3
            )
        if refresh_case.collection_status == "ok" and context_paths:
            for context_path in context_paths_to_copy:
                target = case.actual_case_dir / context_path.name
                shutil.copyfile(context_path, target)
            run_analysis_pass(config, case, env=env, repo_root=repo_root, metadata_mode="off")
            if case.analysis_status == "ok":
                score_case(case)
            progress.emit(
                stage="cm_timeseries_refresh",
                case_id=case_id,
                status="done",
                seconds=elapsed_seconds(started),
                score=case.score,
            )
        else:
            reason = (
                "runtime_metrics_refresh_timeout"
                if refresh_case.collection_status == "timeout"
                else "runtime_metrics_context_missing"
            )
            progress.emit(
                stage="cm_timeseries_refresh",
                case_id=case_id,
                status="failed",
                reason=reason,
                seconds=elapsed_seconds(started),
            )
    finally:
        shutil.rmtree(refresh_dir, ignore_errors=True)
    return case


def analyze_case_for_batch(
    config: BatchConfig,
    case: CaseResult,
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> CaseResult:
    case_id = f"case-{case.index:03d}"
    if case.collection_status != "ok":
        return case
    if (
        case.profile_reuse_status == "reused"
        and case.analysis_status == "ok"
        and case.actual_case_dir is not None
    ):
        progress.emit(
            stage="case",
            case_id=case_id,
            status="analysis_reused",
            seconds=case.analysis_seconds,
            score=case.score,
        )
        return case
    progress.emit(stage="case", case_id=case_id, status="analysis_started")
    run_analysis_pass(config, case, env=env, repo_root=repo_root, metadata_mode="off")
    if case.analysis_status != "ok":
        progress.emit(
            stage="case",
            case_id=case_id,
            status="failed",
            phase="analysis",
            seconds=case.analysis_seconds,
        )
        return case
    if case.analysis_status == "ok":
        score_case(case)
        progress.emit(
            stage="case",
            case_id=case_id,
            status="analysis_done",
            seconds=case.analysis_seconds,
            score=case.score,
        )
    return case


def print_case_progress(case: CaseResult) -> None:
    print(
        f"[batch] case-{case.index:03d} collection: "
        f"{format_seconds(case.cm_collect_seconds)} ({case.collection_status})"
    )
    if case.analysis_seconds is not None:
        print(
            f"[batch] case-{case.index:03d} analyzer triage: "
            f"{format_seconds(case.analysis_seconds)} ({case.analysis_status})"
        )


def run_analysis_pass(
    config: BatchConfig,
    case: CaseResult,
    *,
    env: dict[str, str],
    repo_root: Path,
    metadata_mode: str | None = None,
) -> None:
    started = time.monotonic()
    if case.actual_case_dir is None:
        case.analysis_status = "failed"
        case.failure_category = "case_dir_missing"
        case.failure_reason = (
            "Case artifacts are missing; rerun collection before relying on Details."
        )
        case.analysis_seconds = elapsed_seconds(started)
        return
    try:
        cmd = command_prefix(repo_root, "pipeline") + [
            str(case.actual_case_dir),
            "--stop-after-analysis",
            "--metadata-failure-policy",
            "continue",
        ]
        effective_metadata_mode = config.metadata_mode if metadata_mode is None else metadata_mode
        append_metadata_args(cmd, replace(config, metadata_mode=effective_metadata_mode))
        result = run_subprocess(cmd, cwd=repo_root, env=env)
        case.analysis_status = "ok" if result.returncode == 0 else "failed"
        if result.returncode == SUBPROCESS_TIMEOUT_RETURN_CODE:
            case.analysis_status = "timeout"
            case.failure_category = "analysis_or_metadata_timeout"
            case.failure_reason = (
                "Deterministic analysis or metadata collection timed out before it completed."
            )
        if result.returncode != 0:
            case.failure_category = case.failure_category or "analysis_or_metadata_failed"
            case.failure_reason = case.failure_reason or (
                "Deterministic analysis or metadata collection failed before it completed."
            )
        inspect_case_outputs(case)
        if case.analysis_status == "ok" and case.metadata_status == "failed":
            case.failure_category = "metadata_collection_failed"
            case.failure_reason = (
                "Metadata collection failed for this case; deterministic profile facts may still "
                "be available."
            )
    finally:
        case.analysis_seconds = elapsed_seconds(started)


def refresh_top_metadata(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> None:
    ranked = rank_cases_for_metadata(cases)
    skip_reason = metadata_refresh_skip_reason(config, ranked)
    if skip_reason is not None:
        mark_metadata_not_requested(ranked)
        progress.emit(stage="metadata_refresh", status="skipped", reason=skip_reason)
        return
    collectable_ranked = [
        case for case in ranked if metadata_case_has_collectable_references(config, case)
    ]
    if not collectable_ranked:
        mark_metadata_not_requested(ranked)
        progress.emit(
            stage="metadata_refresh",
            status="skipped",
            reason="no collectable referenced tables",
        )
        return
    candidates = select_metadata_refresh_candidates_for_config(config, collectable_ranked)
    if not candidates:
        mark_metadata_not_requested(ranked)
        progress.emit(
            stage="metadata_refresh", status="skipped", reason="no bad or suspicious cases"
        )
        return
    started = time.monotonic()
    progress.emit(
        stage="metadata_refresh",
        status="started",
        total=len(candidates),
        metadata_jobs=config.metadata_jobs,
    )
    if config.metadata_jobs == 1:
        for case in candidates:
            refresh_case_metadata(config, case, env=env, repo_root=repo_root, progress=progress)
    else:
        with ThreadPoolExecutor(max_workers=config.metadata_jobs) as executor:
            futures = [
                executor.submit(
                    refresh_case_metadata,
                    config,
                    case,
                    env=env,
                    repo_root=repo_root,
                    progress=progress,
                )
                for case in candidates
            ]
            for future in as_completed(futures):
                future.result()
    refreshed_ids = {id(case) for case in candidates}
    mark_metadata_not_requested(
        [case for case in cases if case.analysis_status == "ok" and id(case) not in refreshed_ids]
    )
    progress.emit(
        stage="metadata_refresh",
        status="done",
        total=len(candidates),
        seconds=elapsed_seconds(started),
    )


def metadata_case_has_collectable_references(config: BatchConfig, case: CaseResult) -> bool:
    if case.profile_reuse_status == "reused" and case.metadata_status in {"collected", "partial"}:
        return False
    return update_collectable_metadata_table_count(config, case) > 0


def case_has_runtime_metrics_context(case: CaseResult) -> bool:
    if case.actual_case_dir is None:
        return False
    return any(
        (case.actual_case_dir / name).is_file()
        for name in ("runtime_metrics_context.json", "cm_timeseries_context.json")
    )


def metadata_subprocess_env(env: dict[str, str], case: CaseResult) -> dict[str, str]:
    if not case.metadata_source_tables:
        return env
    case_env = dict(env)
    case_env[METADATA_SOURCE_TABLES_ENV] = json.dumps(list(case.metadata_source_tables))
    return case_env


def refresh_case_metadata(
    config: BatchConfig,
    case: CaseResult,
    *,
    env: dict[str, str],
    repo_root: Path,
    progress: ProgressWriter,
) -> CaseResult:
    case_id = f"case-{case.index:03d}"
    progress.emit(
        stage="metadata_refresh", case_id=case_id, status="started", triage_rank=case.triage_rank
    )
    started = time.monotonic()
    run_analysis_pass(
        config,
        case,
        env=metadata_subprocess_env(env, case),
        repo_root=repo_root,
        metadata_mode=config.metadata_mode,
    )
    case.metadata_refreshed = True
    seconds = elapsed_seconds(started)
    if case.analysis_status == "ok":
        score_case(case)
        progress.emit(
            stage="metadata_refresh",
            case_id=case_id,
            status="done",
            metadata_status=case.metadata_status,
            score=case.score,
            seconds=seconds,
        )
    else:
        progress.emit(
            stage="metadata_refresh",
            case_id=case_id,
            status="failed",
            metadata_status=case.metadata_status,
            seconds=seconds,
        )
    return case


def run_top_reports(
    config: BatchConfig,
    cases: list[CaseResult],
    *,
    env: dict[str, str],
    repo_root: Path,
) -> None:
    if config.top_reports <= 0:
        return
    ranked = sorted(
        [
            case
            for case in cases
            if case.analysis_status == "ok" and case.score > 0 and case.actual_case_dir
        ],
        key=batch_ranking_key,
    )
    for case in ranked[: config.top_reports]:
        assert case.actual_case_dir is not None
        started = time.monotonic()
        try:
            cmd = command_prefix(repo_root, "pipeline") + [
                str(case.actual_case_dir),
            ]
            append_metadata_args(cmd, config)
            result = run_subprocess(cmd, cwd=repo_root, env=env)
            diagnosis = case.actual_case_dir / "diagnosis.md"
            partial = case.actual_case_dir / "diagnosis.partial.md"
            case.report_generated = diagnosis.exists()
            if result.returncode == SUBPROCESS_TIMEOUT_RETURN_CODE:
                case.report_validation_status = "timeout"
                case.failure_category = case.failure_category or "report_generation_timeout"
                case.failure_reason = case.failure_reason or "Report generation timed out."
            elif result.returncode == 0 and diagnosis.exists():
                case.report_validation_status = "passed"
            elif partial.exists():
                case.report_validation_status = "failed_partial_untrusted"
                case.failure_category = case.failure_category or "report_validation_failed"
                case.failure_reason = case.failure_reason or (
                    "Report generation finished, but deterministic validation rejected the output."
                )
            else:
                case.report_validation_status = "failed"
                case.failure_category = case.failure_category or "report_generation_failed"
                case.failure_reason = case.failure_reason or "Report generation failed."
        finally:
            case.report_seconds = elapsed_seconds(started)
            print(
                f"[batch] case-{case.index:03d} report: "
                f"{format_seconds(case.report_seconds)} ({case.report_validation_status})"
            )


def command_uses_role(cmd: list[str], role: str) -> bool:
    spec = command_spec(role)
    return spec.module in cmd or spec.console_script in cmd


def command_value(cmd: list[str], flag: str) -> str | None:
    try:
        index = cmd.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(cmd):
        return None
    return cmd[index + 1]


def command_has_flag(cmd: list[str], flag: str) -> bool:
    return flag in cmd


def subprocess_timeout_sec(cmd: list[str]) -> int:
    if command_uses_role(cmd, "collect_cm"):
        if command_has_flag(cmd, "--collect-cm-timeseries"):
            return RUNTIME_METRICS_REFRESH_TIMEOUT_SEC
        return PROFILE_COLLECTION_TIMEOUT_SEC
    if command_uses_role(cmd, "collect_impala_profile") and command_has_flag(
        cmd, "--collect-prometheus-timeseries"
    ):
        return RUNTIME_METRICS_REFRESH_TIMEOUT_SEC
    if command_uses_role(cmd, "pipeline"):
        if "--stop-after-analysis" not in cmd:
            return REPORT_TIMEOUT_SEC
        metadata_mode = command_value(cmd, "--metadata-mode") or "auto"
        if metadata_mode == "off":
            return ANALYSIS_TIMEOUT_SEC
        return METADATA_ANALYSIS_TIMEOUT_SEC
    return DEFAULT_SUBPROCESS_TIMEOUT_SEC


def run_subprocess(
    cmd: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    timeout_sec = subprocess_timeout_sec(cmd)
    capture_output = command_uses_role(cmd, "collect_cm") or command_uses_role(
        cmd, "collect_impala_profile"
    )
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            shell=False,
            text=True,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, SUBPROCESS_TIMEOUT_RETURN_CODE)


def run_profile_collection_subprocess(
    cmd: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    result = run_subprocess(cmd, cwd=cwd, env=env)
    attempts = 1
    while (
        result.returncode != 0
        and attempts < PROFILE_COLLECTION_MAX_ATTEMPTS
        and profile_collection_failure_is_retryable(result)
    ):
        attempts += 1
        result = run_subprocess(cmd, cwd=cwd, env=env)
    return result


def profile_collection_failure_is_retryable(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == SUBPROCESS_TIMEOUT_RETURN_CODE:
        return True
    if impala_profile_not_found(result):
        return False
    http_status = subprocess_http_status(result)
    if http_status is None:
        return True
    return http_status.startswith("5")


def profile_collection_failure_reason(
    config: BatchConfig,
    result: subprocess.CompletedProcess,
) -> str:
    if result.returncode == SUBPROCESS_TIMEOUT_RETURN_CODE:
        return "Profile collection timed out before a profile digest was produced."
    http_status = subprocess_http_status(result)
    if http_status is not None:
        source = (
            "Cloudera Manager" if config.query_profile_source == "cm" else "Impala profile endpoint"
        )
        return f"{source} profile collection returned HTTP {http_status}."
    if config.query_profile_source == "impala" and isinstance(result.stderr, str):
        for line in result.stderr.splitlines():
            if line.startswith("Single-query Impala profile collection failed: ") and line.endswith(
                "Last safe error: Impala profile endpoint request timed out safely."
            ):
                return "Impala profile endpoint profile collection timed out."
        if impala_profile_not_found(result):
            return "No Impala profile endpoint holds the profile any more."
    return "Profile collection command failed before a profile digest was produced."


def impala_profile_not_found(result: subprocess.CompletedProcess) -> bool:
    """Whether every daemon answered that the query has left its query log."""
    stderr = getattr(result, "stderr", None)
    if not isinstance(stderr, str):
        return False
    return any(
        line.startswith(IMPALA_PROFILE_FAILURE_PREFIX)
        and line.endswith(IMPALA_PROFILE_NOT_FOUND_SUFFIX)
        for line in stderr.splitlines()
    )


def subprocess_http_status(result: subprocess.CompletedProcess) -> str | None:
    for value in (getattr(result, "stderr", None), getattr(result, "stdout", None)):
        if not isinstance(value, str):
            continue
        match = HTTP_STATUS_RE.search(value)
        if match:
            return match.group(1)
    return None
