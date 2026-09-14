#!/usr/bin/env python3
"""Bounded recent-query batch workflow for Query Doctor."""

from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from query_doctor.cli import collect_cm_profiles as cm_profiles
from query_doctor.cli.commands import command_prefix
from query_doctor.config.contract import QDCREDS_CONFIG_PATH
from query_doctor.engines import get_default_engine_adapter
from query_doctor.prometheus.timeseries import (
    DEFAULT_MAX_PROMETHEUS_POINTS,
    DEFAULT_PROMETHEUS_METRICS_PROFILE,
    DEFAULT_PROMETHEUS_STEP_SEC,
    DEFAULT_PROMETHEUS_TIMEOUT_SEC,
    DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
    PROMETHEUS_METRICS_PROFILE_CHOICES,
)
from query_doctor.recent.batch_config import (
    MAX_CM_INSPECT_LIMIT,
    MAX_CM_EVENTS_MAX_EVENTS,
    MAX_CM_JOBS,
    MAX_HIGH_JOBS,
    MAX_JOBS,
    MAX_METADATA_JOBS,
    MAX_METADATA_TOP_LIMIT,
    MAX_CM_TIMESERIES_TOP_LIMIT,
    MAX_RAW_CM_SUMMARY_SCAN_LIMIT,
    MAX_TRIAGE_PROFILE_LIMIT,
    METADATA_MODE_CHOICES,
    ORDER_CHOICES,
    RECENT_HISTORY_BACKEND_CHOICES,
    SAFE_OUTPUT_PREFIX,
    build_batch_config,
    display_float,
    duration_filter_label,
    effective_subprocess_env,
    elapsed_seconds,
    expand_optional_path_string,
    first_bool,
    first_float,
    first_int,
    first_string,
    format_seconds,
    preflight,
    prepare_batch_output_dir,
    resolve_config_path,
    safe_output_temp_roots,
    secret_values,
    system_output_roots,
    validate_batch_output_path,
    validate_cm_jobs_config,
    validate_cm_time_bound,
    validate_jobs_config,
    validate_metadata_jobs_config,
    validate_not_dangerous_output_path,
    validate_safe_overwrite_target,
)
from query_doctor.recent.batch_models import BatchConfig, CaseResult, DiscoveryResult
from query_doctor.recent.backfill import (
    backfill_pool_warning,
    discovery_select_limit,
    retain_backfilled_case_results,
)
from query_doctor.recent.batch_scoring import (
    backend_data_skew_value,
    count_max_table_skips,
    count_referenced_tables,
    duration_seconds_value,
    extract_scoring_components,
    fact_int,
    fact_values,
    has_metadata_completeness_value,
    has_metadata_error_status,
    has_supported_spill_scratch_evidence,
    inspect_case_outputs,
    normalized_tail_candidate_count,
    score_analysis_facts,
    score_case,
    scoring_section_text,
    section_text,
    severe_backend_data_skew_ratio,
    table_stats_status_from_facts,
)
from query_doctor.recent.batch_summary import (
    batch_ranking_key,
    build_summary,
    candidate_reason_counts,
    candidate_reason_sql_verb_counts,
    case_primary_bottleneck_distribution,
    case_primary_unknown_breakdown,
    case_score_severity,
    case_to_summary,
    optimizer_funnel,
    optimizer_rewriteability_distribution,
    rank_cases_for_query_optimization,
    rank_cases_for_stats_optimization,
    write_batch_outputs,
)
from query_doctor.recent.workload_history import update_summary_with_workload_history
from query_doctor.recent import case_processing as batch_case_processing
from query_doctor.recent.command_args import append_cm_config_args, append_metadata_args
from query_doctor.recent.case_processing import (
    analyze_case_for_batch,
    collect_scan_cm_events,
    analyze_cases,
    collect_case_for_batch,
    collect_case_profile,
    collect_cases,
    print_case_progress,
    process_cases,
    refresh_case_metadata,
    refresh_top_metadata,
    run_analysis_pass,
    run_subprocess,
    run_top_reports,
)
from query_doctor.recent.collector_summary import (
    collector_issue_codes,
    collector_observed_at,
    collector_status,
    collector_summary_payload,
    query_log_continuity_status,
    read_previous_collector_summary,
    write_collector_summary,
)
from query_doctor.recent.discovery import (
    build_recent_filters,
    classify_duration_filter_mode,
    discover_candidates as discover_candidates_impl,
    discovery_window_bounds,
    make_cm_http_client,
    matching_candidate_limit_hit,
    raw_cm_summary_scan_limit,
)
from query_doctor.cm.profile_parsing import parse_cm_timestamp
from query_doctor.cm.query_discovery import is_running_query_summary
from query_doctor.analyzer.sql_sources import extract_referenced_tables_from_sql
from query_doctor.impala.query_discovery import fetch_impala_query_summaries
from query_doctor.recent.progress import ProgressWriter
from query_doctor.recent.query_optimization_score import (
    QueryOptimizationCandidateScore,
    score_query_optimization_candidate,
)
from query_doctor.recent.stats_optimization_score import (
    StatsOptimizationCandidateScore,
    score_stats_optimization_candidate,
)
from query_doctor.source_visibility import SOURCE_VISIBILITY_CHOICES, SOURCE_VISIBILITY_OWNER_RAW
from query_doctor.recent.metadata_refresh import (
    mark_metadata_not_requested,
    metadata_refresh_candidates,
    metadata_refresh_skip_reason,
    rank_cases_for_metadata,
    select_metadata_refresh_candidates,
    suspicious_can_be_promoted_by_metadata,
)
from query_doctor.recent.case_reuse import reuse_analyzed_cases
from query_doctor.recent.history_store import (
    RecentHistoryRetentionResult,
    enqueue_recent_profile_jobs,
    persist_recent_history,
    prune_recent_history,
    recent_history_retention_enabled,
)

REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CREDS_DIR_NAME = ".qdcreds"
DEFAULT_CM_ENV_NAME = "cm-ro.env"
CM_ENV_FILE_KEYS = {
    "CM_USERNAME",
    "CM_USER",
    "CM_PASSWORD",
    "CM_TOKEN",
}


def default_cm_env_path(env: dict[str, str]) -> tuple[Path, bool]:
    configured = env.get("QD_CM_ENV")
    if configured:
        return Path(configured).expanduser(), True
    creds_dir = Path(env.get("QD_CREDS_DIR") or (Path.home() / DEFAULT_CREDS_DIR_NAME)).expanduser()
    return creds_dir / DEFAULT_CM_ENV_NAME, False


def parse_cm_env_line(line: str, *, line_number: int) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    try:
        tokens = shlex.split(stripped, comments=True, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid CM env file syntax at line {line_number}") from exc
    if not tokens:
        return None
    if tokens[0] == "export":
        tokens = tokens[1:]
    if len(tokens) != 1 or "=" not in tokens[0]:
        return None
    key, value = tokens[0].split("=", 1)
    if key not in CM_ENV_FILE_KEYS:
        return None
    return key, value


def load_local_cm_env(env: dict[str, str], *, allow_default: bool) -> dict[str, str]:
    env_path, explicit = default_cm_env_path(env)
    if not explicit and not allow_default:
        return env
    if not env_path.exists():
        if explicit:
            raise ValueError("QD_CM_ENV points to a missing CM credentials env file.")
        return env
    if not env_path.is_file():
        raise ValueError("CM credentials env path is not a file.")
    loaded = dict(env)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("could not read CM credentials env file.") from exc
    for line_number, line in enumerate(lines, 1):
        parsed = parse_cm_env_line(line, line_number=line_number)
        if parsed is None:
            continue
        key, value = parsed
        if not loaded.get(key):
            loaded[key] = value
    if not loaded.get("CM_USERNAME") and loaded.get("CM_USER"):
        loaded["CM_USERNAME"] = loaded["CM_USER"]
    return loaded


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    engine = get_default_engine_adapter()
    parser = argparse.ArgumentParser(
        description=(
            f"Bounded recent CM query batch for {engine.display_name}: discover candidates, collect explicit "
            "profiles, run analyzer/metadata without LLM, then optionally report "
            "only the top ranked cases."
        )
    )
    parser.add_argument(
        "--config",
        help=(
            "Optional local Query Doctor config. Defaults to query-doctor-config.json, "
            f"then {QDCREDS_CONFIG_PATH}, then legacy "
            f"{cm_profiles.LEGACY_LOCAL_CONFIG_NAME}."
        ),
    )
    parser.add_argument(
        "--config-cluster",
        help=(
            "Cluster id from local config clusters[] to use for source, metadata, and "
            "runtime-metric settings. CLI flags still override selected cluster fields."
        ),
    )
    parser.add_argument("--cm-url", help="Cloudera Manager base URL. May also use CM_URL.")
    parser.add_argument("--cluster", help="Cloudera Manager cluster name.")
    parser.add_argument("--service", help="Impala service name.")
    parser.add_argument("--ca-bundle", help="PEM CA bundle for verified CM TLS connections.")
    parser.add_argument("--insecure-skip-verify", action="store_true", default=None)
    parser.add_argument(
        "--query-profile-source",
        choices=("cm", "impala"),
        help="Discovery/profile source. cm uses Cloudera Manager; impala uses direct impalad debug web endpoints.",
    )
    parser.add_argument(
        "--impala-profile-host",
        dest="impala_profile_hosts",
        action="append",
        default=[],
        help="impalad debug web host or host:port for direct Impala discovery/profile collection.",
    )
    parser.add_argument("--impala-profile-port", type=positive_int)
    parser.add_argument("--impala-profile-scheme", choices=("http", "https"))
    parser.add_argument("--impala-profile-timeout-sec", type=positive_int)
    parser.add_argument("--impala-query-list-max-bytes", type=positive_int)
    parser.add_argument(
        "--impala-profile-prefer-json",
        action="store_true",
        default=None,
        help=(
            "Try direct impalad JSON profile endpoints before text. Text endpoints "
            "remain the fallback for older Impala versions."
        ),
    )
    parser.add_argument(
        "--no-impala-profile-prefer-json",
        action="store_false",
        dest="impala_profile_prefer_json",
        help="Use direct impalad text profile endpoints first.",
    )
    parser.add_argument(
        "--impala-profile-collect-docs",
        action="store_true",
        default=None,
        help=(
            "Collect safe profile counter stability labels from direct impalad "
            "/profile_docs/?json, falling back to /profile_docs. Missing endpoints are "
            "non-fatal."
        ),
    )
    parser.add_argument(
        "--no-impala-profile-collect-docs",
        action="store_false",
        dest="impala_profile_collect_docs",
        help="Do not collect direct impalad profile counter docs.",
    )
    parser.add_argument(
        "--impala-collect-admission-context",
        action="store_true",
        default=None,
        help=(
            "Collect bounded aggregate pool context from direct impalad /admission?json. "
            "Missing endpoints are non-fatal."
        ),
    )
    parser.add_argument(
        "--no-impala-collect-admission-context",
        action="store_false",
        dest="impala_collect_admission_context",
        help="Do not collect direct impalad admission aggregate context.",
    )
    parser.add_argument(
        "--source-visibility",
        choices=SOURCE_VISIBILITY_CHOICES,
        help=(
            "Source display mode. safe keeps browser/report output raw-free; owner_raw "
            "only enables owner-gated source workflows and does not expose raw fields by itself."
        ),
    )
    parser.add_argument(
        "--source-owner-user",
        action="append",
        help=(
            "Query owner user allowed for owner_raw source visibility. May be passed multiple times. "
            "If omitted, a simple Kerberos principal may be used."
        ),
    )
    parser.add_argument(
        "--prometheus-url",
        help="Prometheus base URL for direct Impala runtime metric summaries. No credentials in the URL.",
    )
    parser.add_argument(
        "--collect-prometheus-timeseries",
        action="store_true",
        default=None,
        help=(
            "Collect bounded allowlisted Prometheus runtime metric summaries for direct Impala scans. "
            "Providing --prometheus-url also enables this unless --no-collect-prometheus-timeseries is set."
        ),
    )
    parser.add_argument(
        "--no-collect-prometheus-timeseries",
        action="store_false",
        dest="collect_prometheus_timeseries",
        help="Disable Prometheus runtime metric summaries for direct Impala scans.",
    )
    parser.add_argument(
        "--prometheus-metrics-profile",
        choices=PROMETHEUS_METRICS_PROFILE_CHOICES,
        help=f"Prometheus metric-name compatibility profile. Default: {DEFAULT_PROMETHEUS_METRICS_PROFILE}.",
    )
    parser.add_argument(
        "--prometheus-step-sec",
        type=positive_int,
        help=f"Prometheus query_range step in seconds. Default: {DEFAULT_PROMETHEUS_STEP_SEC}.",
    )
    parser.add_argument(
        "--prometheus-timeseries-padding-sec",
        type=non_negative_int,
        help=(
            "Seconds to pad before query start and after query end for Prometheus metrics. "
            f"Default: {DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC}."
        ),
    )
    parser.add_argument(
        "--prometheus-timeout-sec",
        type=positive_int,
        help=f"Timeout per Prometheus request. Default: {DEFAULT_PROMETHEUS_TIMEOUT_SEC}.",
    )
    parser.add_argument(
        "--out",
        help="Dedicated query-doctor-* output directory under /tmp or the system temp directory.",
    )
    parser.add_argument("--recent-window-minutes", type=positive_int)
    parser.add_argument(
        "--from-time",
        help="Explicit CM query summary window start, formatted as YYYY-MM-DDTHH:MM:SSZ.",
    )
    parser.add_argument(
        "--to-time",
        help="Explicit CM query summary window end, formatted as YYYY-MM-DDTHH:MM:SSZ.",
    )
    parser.add_argument(
        "--cm-inspect-limit",
        type=positive_int,
        help=f"Maximum CM query summaries to request/inspect. Hard cap: {MAX_CM_INSPECT_LIMIT}.",
    )
    parser.add_argument(
        "--triage-profile-limit",
        type=positive_int,
        help=(
            "Profile analysis limit: maximum candidate profiles to collect/analyze. "
            f"Hard cap: {MAX_TRIAGE_PROFILE_LIMIT}."
        ),
    )
    parser.add_argument(
        "--select-limit",
        type=positive_int,
        dest="select_limit_alias",
        help="Deprecated alias for --triage-profile-limit.",
    )
    parser.add_argument(
        "--metadata-top-limit",
        type=non_negative_int,
        help=(
            "Metadata budget: refresh priority cases first, then fill with remaining "
            "collectable top cases. "
            f"Hard cap: {MAX_METADATA_TOP_LIMIT}. Default: 0."
        ),
    )
    parser.add_argument("--min-duration-sec", type=non_negative_float)
    parser.add_argument(
        "--no-min-duration-filter",
        action="store_true",
        help="Disable the minimum duration filter. Intended for web runs with an empty Min duration field.",
    )
    parser.add_argument("--max-duration-sec", type=non_negative_float)
    parser.add_argument("--order", choices=ORDER_CHOICES)
    parser.add_argument("--include-failed", action="store_true", default=None)
    parser.add_argument("--include-running", action="store_true", default=None)
    parser.add_argument("--only-running", action="store_true", default=None)
    parser.add_argument("--user", help="Optional recent-query user filter.")
    parser.add_argument("--pool", help="Optional recent-query pool filter.")
    parser.add_argument("--query-type", help="Optional CM query type filter.")
    parser.add_argument(
        "--max-profile-bytes",
        type=positive_int,
    )
    parser.add_argument(
        "--collect-cm-events",
        action="store_true",
        default=None,
        help="Collect one bounded Cloudera Manager Events context for the scan window.",
    )
    parser.add_argument(
        "--cm-events-max-events",
        type=positive_int,
        help=(
            "Maximum CM Events records to summarize for the scan window. "
            f"Hard cap: {MAX_CM_EVENTS_MAX_EVENTS}. Default: 50."
        ),
    )
    parser.add_argument(
        "--collect-cm-timeseries",
        action="store_true",
        default=None,
        help="Collect bounded allowlisted CM time-series summaries for top ranked collected cases.",
    )
    parser.add_argument(
        "--cm-timeseries-top-limit",
        type=non_negative_int,
        help=(
            "Maximum top ranked analyzed cases that may receive CM time-series summaries. "
            f"Hard cap: {MAX_CM_TIMESERIES_TOP_LIMIT}. Default: 10."
        ),
    )
    parser.add_argument(
        "--cm-metrics-profile",
        choices=cm_profiles.CM_METRICS_PROFILE_CHOICES,
        help=(
            "CM metric-name compatibility profile for allowlisted time-series queries. "
            f"Default: {cm_profiles.DEFAULT_CM_METRICS_PROFILE}."
        ),
    )
    parser.add_argument(
        "--cm-timeseries-padding-sec",
        type=non_negative_int,
        help=f"Seconds to pad before query start and after query end for CM metrics. Default: {cm_profiles.DEFAULT_CM_TIMESERIES_PADDING_SEC}.",
    )
    parser.add_argument(
        "--max-timeseries-bytes",
        type=positive_int,
        help=f"Maximum bytes per CM time-series response. Default: {cm_profiles.DEFAULT_MAX_TIMESERIES_BYTES}.",
    )
    parser.add_argument(
        "--max-timeseries-points",
        type=positive_int,
        help=(
            "Maximum numeric data points to summarize per runtime metric query. "
            f"Default: CM {cm_profiles.DEFAULT_MAX_TIMESERIES_POINTS}, Prometheus {DEFAULT_MAX_PROMETHEUS_POINTS}."
        ),
    )
    parser.add_argument(
        "--metadata-mode",
        choices=METADATA_MODE_CHOICES,
        default="auto",
        help="Metadata mode passed to query-doctor-pipeline. Default: auto.",
    )
    parser.add_argument("--metadata-coordinator", help="Impala coordinator HOST:PORT.")
    parser.add_argument("--metadata-impala-shell", help="impala-shell executable.")
    parser.add_argument("--metadata-auth")
    parser.add_argument("--metadata-protocol", choices=("beeswax", "hs2", "hs2-http"))
    parser.add_argument("--metadata-kerberos-service-name")
    parser.add_argument("--metadata-kerberos-host-fqdn")
    parser.add_argument("--metadata-ssl", action="store_true", default=None)
    parser.add_argument("--metadata-ca-cert")
    parser.add_argument("--metadata-timeout-sec", type=positive_int)
    parser.add_argument("--metadata-max-tables", type=positive_int)
    parser.add_argument("--metadata-max-output-bytes", type=positive_int)
    parser.add_argument("--metadata-redact", action="store_true", default=None)
    parser.add_argument("--metadata-redact-identifiers", action="store_true", default=None)
    parser.add_argument(
        "--metadata-no-redact-identifiers",
        dest="metadata_redact_identifiers",
        action="store_false",
    )
    parser.add_argument("--metadata-redact-hosts", action="store_true", default=None)
    parser.add_argument(
        "--metadata-no-redact-hosts",
        dest="metadata_redact_hosts",
        action="store_false",
    )
    parser.add_argument(
        "--top-reports",
        type=non_negative_int,
        default=0,
        help="Run full LLM reports only for this many top scored cases. Default: 0.",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=1,
        help=(
            f"Parallel analyzer workers after CM profile collection. Hard cap: {MAX_JOBS}, "
            f"or {MAX_HIGH_JOBS} with --allow-high-jobs in no-report mode."
        ),
    )
    parser.add_argument(
        "--cm-jobs",
        type=positive_int,
        help=f"Parallel CM profile collection workers. Hard cap: {MAX_CM_JOBS}. Default: --jobs.",
    )
    parser.add_argument(
        "--metadata-jobs",
        type=positive_int,
        help=f"Parallel metadata refresh workers for top cases. Hard cap: {MAX_METADATA_JOBS}. Default: 5.",
    )
    parser.add_argument(
        "--allow-high-jobs",
        action="store_true",
        help="Allow up to 100 jobs only with --top-reports 0.",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only discover and summarize candidates; do not collect profiles.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Remove and recreate --out before running. Refuses repository or unsafe shallow paths.",
    )
    parser.add_argument(
        "--progress-jsonl",
        help="Optional append-only JSONL progress file. Contains sanitized structured stage events only.",
    )
    parser.add_argument(
        "--collect-workload-history",
        action="store_true",
        default=None,
        help="Opt in to local workload fingerprint baseline history and regression labels.",
    )
    parser.add_argument(
        "--workload-history-path",
        help="Optional local JSONL path for workload baseline history. Default: ~/.query-doctor/workload_history.jsonl.",
    )
    parser.add_argument(
        "--workload-history-max-bytes",
        type=positive_int,
        help="Rotate workload history before appending when this byte limit is exceeded.",
    )
    parser.add_argument(
        "--recent-history-db",
        help=(
            "Optional local SQLite database for raw-free Recent summary history. "
            "Stores bounded summary signals and suspicion reasons, not raw SQL or profile text."
        ),
    )
    parser.add_argument(
        "--recent-history-backend",
        choices=RECENT_HISTORY_BACKEND_CHOICES,
        help=(
            "Recent summary history backend. Defaults to sqlite when --recent-history-db is set, "
            "otherwise disabled. postgres reads its DSN from an environment variable."
        ),
    )
    parser.add_argument(
        "--recent-history-postgres-dsn-env",
        help=(
            "Environment variable name containing the Postgres DSN for recent_history_backend=postgres. "
            "The DSN value itself must not be stored in Query Doctor JSON config."
        ),
    )
    parser.add_argument(
        "--recent-history-collector-summary-json",
        type=Path,
        help=(
            "Optional raw-free summary JSON for this Recent summary collector run. "
            "Contains aggregate counts and status only, not query IDs, raw SQL, paths, or provider output."
        ),
    )
    parser.add_argument(
        "--recent-history-summary-retention-days",
        type=positive_int,
        help="Prune raw-free Recent summary rows older than this many days after recording history.",
    )
    parser.add_argument(
        "--recent-history-profile-job-retention-days",
        type=positive_int,
        help=(
            "Prune terminal raw-free Recent profile job rows older than this many days. "
            "Pending and leased jobs are retained."
        ),
    )
    parser.add_argument(
        "--recent-history-analysis-cache-retention-days",
        type=positive_int,
        help="Prune raw-free Recent analysis-cache rows older than this many days.",
    )
    parser.add_argument(
        "--recent-history-profile-artifact-retention-days",
        type=positive_int,
        help="Prune raw-free Recent profile-artifact metadata rows older than this many days.",
    )
    parser.add_argument(
        "--reuse-analyzed-profiles-from",
        action="append",
        dest="analyzed_profile_reuse_roots",
        default=[],
        help=(
            "Search direct child query-doctor-* batch outputs under this local root and reuse "
            "already analyzed cases with matching Query IDs. Intended for repeated safe web "
            "Recent scans on a trusted local cache root."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    total_started = time.monotonic()
    allow_default_cm_env = env is None
    env = dict(os.environ if env is None else env)
    args = parse_args(argv)
    repo_root = REPO_DIR
    try:
        env = load_local_cm_env(env, allow_default=allow_default_cm_env)
        config = build_batch_config(args, env=env, cwd=Path.cwd(), repo_root=repo_root)
        env = effective_subprocess_env(env, config.krb5ccname)
        preflight(config, env=env)
        prepare_batch_output_dir(config.out, repo_root=repo_root, overwrite=config.overwrite)
    except ValueError as exc:
        print(f"[batch] ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        progress = ProgressWriter(config.progress_jsonl)
    except OSError as exc:
        print(f"[batch] ERROR: cannot write --progress-jsonl: {exc}", file=sys.stderr)
        return 2

    cases_root = config.out / "cases"
    cases_root.mkdir(parents=True, exist_ok=True)

    case_results: list[CaseResult] = []
    warnings: list[str] = []
    discovery = DiscoveryResult([], [], "none", None)
    discovery_started: float | None = None
    discovery_seconds: float | None = None
    discovery_failed = False
    recent_history_recorded_count = 0
    recent_profile_jobs_planned_count = 0
    recent_history_status = "not_configured"
    recent_history_retention_result = RecentHistoryRetentionResult()
    recent_history_retention_status = "not_configured"
    cluster_context: dict[str, object] | None = None
    try:
        discovery_started = time.monotonic()
        progress.emit(stage="discovery", status="started")
        discovery = discover_candidates(config, env=env)
        discovery_seconds = elapsed_seconds(discovery_started)
        print(f"[batch] discovery: {format_seconds(discovery_seconds)}")
        warnings.extend(discovery.warnings)
        history_count, history_warning = persist_recent_history(
            discovery.candidates,
            config=config,
            env=env,
        )
        recent_history_recorded_count = history_count
        recent_history_status = (
            "disabled" if config.recent_history_backend == "disabled" else "recorded"
        )
        if history_warning:
            recent_history_status = "warning"
            warnings.append(history_warning)
        if config.discover_only:
            profile_job_count, profile_job_warning = enqueue_recent_profile_jobs(
                discovery.candidates,
                config=config,
                env=env,
            )
            recent_profile_jobs_planned_count = profile_job_count
            if profile_job_warning:
                recent_history_status = "warning"
                warnings.append(profile_job_warning)
        prune_result, prune_warning = prune_recent_history(config=config, env=env)
        recent_history_retention_result = prune_result
        if recent_history_retention_enabled(config):
            recent_history_retention_status = "pruned"
            if prune_warning:
                recent_history_retention_status = "warning"
                warnings.append(prune_warning)
        if config.recent_history_backend != "disabled":
            progress.emit(
                stage="recent_history",
                status="done" if recent_history_status != "warning" else "warning",
                summaries_recorded=history_count,
                profile_jobs_planned=recent_profile_jobs_planned_count,
            )
        if recent_history_retention_enabled(config):
            progress.emit(
                stage="recent_history_retention",
                status="done" if prune_warning is None else "warning",
                **recent_history_retention_result.safe_payload(),
            )
        selected = [candidate for candidate in discovery.candidates if candidate.selected]
        progress.emit(
            stage="discovery",
            status="done",
            summaries_inspected=discovery.summaries_inspected
            if discovery.summaries_inspected is not None
            else len(discovery.candidates),
            candidates_selected=len(selected),
            duration_filter=duration_filter_label(config),
            seconds=discovery_seconds,
        )
        for index, candidate in enumerate(selected, start=1):
            summary = candidate.summary
            case_results.append(
                CaseResult(
                    index=index,
                    query_id=summary.query_id,
                    duration_sec=summary.duration_sec,
                    user=summary.user,
                    pool=summary.pool,
                    query_type=summary.query_type,
                    sql_verb=candidate.sql_verb,
                    wrapper_dir=cases_root / f"case-{index:03d}",
                    metadata_source_tables=tuple(
                        extract_referenced_tables_from_sql(summary.statement or "")
                    ),
                    candidate_rank=index,
                )
            )
    except Exception as exc:  # noqa: BLE001 - user-facing sanitized batch failure
        discovery_failed = True
        if discovery_seconds is None:
            discovery_seconds = (
                elapsed_seconds(discovery_started) if discovery_started is not None else None
            )
            if discovery_seconds is not None:
                print(f"[batch] discovery: {format_seconds(discovery_seconds)}")
        warnings.append(cm_profiles.sanitize_text_for_log(exc, secrets=secret_values(env)))
        progress.emit(
            stage="discovery", status="failed", phase="discovery", seconds=discovery_seconds
        )

    try:
        if not config.discover_only:
            batch_case_processing.run_subprocess = run_subprocess
            cluster_context, cm_events_warning = collect_scan_cm_events(
                config,
                env=env,
                repo_root=repo_root,
                progress=progress,
            )
            if cm_events_warning:
                warnings.append(cm_events_warning)
            print(f"[batch] CM jobs: {config.cm_jobs}")
            print(f"[batch] analyzer jobs: {config.jobs}")
            print(f"[batch] metadata jobs: {config.metadata_jobs}")
            reused_case_count = reuse_analyzed_cases(config, case_results, progress=progress)
            if reused_case_count:
                print(f"[batch] reused analyzed profiles: {reused_case_count}")
            progress.emit(
                stage="case_processing",
                status="started",
                total=len(case_results),
                jobs=config.jobs,
                cm_jobs=config.cm_jobs,
                metadata_jobs=config.metadata_jobs,
            )
            case_processing_warnings = process_cases(
                config, case_results, env=env, repo_root=repo_root, progress=progress
            )
            warnings.extend(case_processing_warnings)
            processed_case_count = len(case_results)
            case_results, backfill_retention_warning = retain_backfilled_case_results(
                config, case_results
            )
            if backfill_retention_warning:
                warnings.append(backfill_retention_warning)
            rank_cases_for_metadata(case_results)
            refresh_top_metadata(
                config, case_results, env=env, repo_root=repo_root, progress=progress
            )
            completed_cases = sum(1 for case in case_results if case.analysis_status == "ok")
            failed_cases = sum(1 for case in case_results if case.failure_category)
            progress.emit(
                stage="case_processing",
                status="done",
                total=processed_case_count,
                retained=len(case_results),
                completed=completed_cases,
                failed=failed_cases,
            )
            run_top_reports(config, case_results, env=env, repo_root=repo_root)

        summary_started = time.monotonic()
        progress.emit(stage="summary", status="started")
        total_seconds = elapsed_seconds(total_started)
        summary = build_summary(
            config,
            discovery,
            case_results,
            warnings,
            discovery_seconds=discovery_seconds,
            discovery_failed=discovery_failed,
            cluster_context=cluster_context,
            total_seconds=total_seconds,
        )
        if config.collect_workload_history:
            update_summary_with_workload_history(
                summary,
                path=config.workload_history_path,
                max_bytes=config.workload_history_max_bytes,
            )
        if config.recent_history_backend != "disabled":
            summary["recent_history"] = {
                "schema_version": 1,
                "enabled": True,
                "backend": config.recent_history_backend,
                "status": recent_history_status,
                "summaries_recorded": recent_history_recorded_count,
                "profile_jobs_planned": recent_profile_jobs_planned_count,
            }
            if recent_history_retention_enabled(config):
                summary["recent_history"]["retention"] = {
                    "enabled": True,
                    "status": recent_history_retention_status,
                    **recent_history_retention_result.safe_payload(),
                }
        write_batch_outputs(config.out, summary)
        if config.recent_history_collector_summary_json is not None:
            selected = [candidate for candidate in discovery.candidates if candidate.selected]
            summaries_inspected = (
                discovery.summaries_inspected
                if discovery.summaries_inspected is not None
                else len(discovery.candidates)
            )
            previous_collector_summary = read_previous_collector_summary(
                config.recent_history_collector_summary_json
            )
            query_log_continuity = query_log_continuity_status(
                direct_impala=config.query_profile_source == "impala",
                query_log_at_capacity=discovery.query_log_at_capacity,
                oldest_completed_at_iso=discovery.query_log_oldest_completed_at_iso,
                previous_summary=previous_collector_summary,
            )
            collector_run_status = collector_status(
                discovery_failed=bool(summary.get("discovery_failed")),
                recent_history_status=recent_history_status,
                recent_history_backend=config.recent_history_backend,
                candidates_discovered=len(discovery.candidates),
                summaries_recorded=recent_history_recorded_count,
                profile_jobs_planned=recent_profile_jobs_planned_count,
                query_log_continuity_status=query_log_continuity,
            )
            collector_payload = collector_summary_payload(
                status=collector_run_status,
                observed_at_iso=collector_observed_at(),
                discover_only=config.discover_only,
                recent_history_backend=config.recent_history_backend,
                summaries_inspected=summaries_inspected,
                candidates_discovered=len(discovery.candidates),
                selected_count=len(selected),
                summaries_recorded=recent_history_recorded_count,
                profile_jobs_planned=recent_profile_jobs_planned_count,
                query_log_at_capacity=discovery.query_log_at_capacity,
                query_log_continuity_status=query_log_continuity,
                issue_codes=collector_issue_codes(
                    status=collector_run_status,
                    recent_history_status=recent_history_status,
                    query_log_continuity_status=query_log_continuity,
                ),
            )
            try:
                write_collector_summary(
                    config.recent_history_collector_summary_json,
                    collector_payload,
                )
            except OSError:
                progress.emit(stage="recent_history_collector_summary", status="failed")
                print(
                    "[batch] ERROR: could not write Recent history collector summary JSON",
                    file=sys.stderr,
                )
                return 2
            progress.emit(
                stage="recent_history_collector_summary",
                summary_kind=collector_payload["summary_kind"],
                status=collector_payload["status"],
                observed_at_iso=collector_payload["observed_at_iso"],
                discover_only=collector_payload["discover_only"],
                history_backend=collector_payload["history_backend"],
                summaries_inspected=collector_payload["summaries_inspected"],
                candidates_discovered=collector_payload["candidates_discovered"],
                selected_count=collector_payload["selected_count"],
                summaries_recorded=collector_payload["summaries_recorded"],
                profile_jobs_planned=collector_payload["profile_jobs_planned"],
                query_log_at_capacity=collector_payload["query_log_at_capacity"],
                query_log_continuity_status=collector_payload["query_log_continuity_status"],
                issue_codes=collector_payload["issue_codes"],
                raw_output=collector_payload["raw_output"],
                sensitive_value_echo=collector_payload["sensitive_value_echo"],
            )
        progress.emit(stage="summary", status="done", seconds=elapsed_seconds(summary_started))
        if summary.get("discovery_failed"):
            progress.emit(
                stage="batch", status="failed", phase="discovery", total_seconds=total_seconds
            )
        else:
            progress.emit(stage="batch", status="done", total_seconds=total_seconds)
        print(f"[batch] summaries inspected: {summary['summaries_inspected']}")
        print(f"[batch] candidates selected: {summary['selected_count']}")
        print(f"[batch] duration filter: {summary['duration_filter']}")
        print(f"[batch] jobs: {summary['jobs']}")
        print(f"[batch] total: {format_seconds(total_seconds)}")
        print(f"[batch] summary JSON: {config.out / 'batch_summary.json'}")
        print(f"[batch] summary Markdown: {config.out / 'batch_summary.md'}")
        return 0 if not summary.get("discovery_failed") else 1
    except Exception:
        progress.emit(
            stage="batch",
            status="failed",
            phase="runtime",
            total_seconds=elapsed_seconds(total_started),
        )
        raise
    finally:
        progress.close()


def discover_candidates(config: BatchConfig, *, env: dict[str, str]) -> DiscoveryResult:
    if config.query_profile_source == "impala":
        result = fetch_impala_query_summaries(
            hosts=config.impala_profile_hosts,
            port=config.impala_profile_port,
            scheme=config.impala_profile_scheme,
            timeout_sec=config.impala_profile_timeout_sec,
            max_query_list_bytes=config.impala_query_list_max_bytes,
        )
        summaries = filter_impala_summaries_for_window(config, result.summaries)
        summaries = filter_impala_summaries_for_owner(config, summaries)
        select_limit = discovery_select_limit(config)
        candidates = cm_profiles.select_recent_query_candidates(
            summaries,
            select_limit=select_limit,
            include_failed=config.include_failed,
            include_running=config.include_running or config.only_running,
            only_running=config.only_running,
            user=config.user,
            pool=config.pool,
            query_type=config.query_type,
            min_duration_sec=config.min_duration_sec,
            max_duration_sec=config.max_duration_sec,
            order=config.order,
        )
        warnings = list(result.warnings)
        pool_warning = backfill_pool_warning(config, select_limit=select_limit)
        if pool_warning:
            warnings.append(pool_warning)
        if matching_candidate_limit_hit(candidates):
            warnings.append(
                f"More than {select_limit} query summaries matched "
                f"the current filters; selected the top {select_limit} by scan order."
            )
        return DiscoveryResult(
            candidates=candidates,
            warnings=warnings,
            duration_filter_mode="client-side",
            server_filter_expression="impala-daemon-query-list",
            summaries_inspected=len(summaries),
            query_log_at_capacity=bool(getattr(result, "query_log_at_capacity", False)),
            query_log_oldest_completed_at_iso=getattr(
                result, "query_log_oldest_completed_at_iso", None
            ),
        )
    return discover_candidates_impl(config, env=env, make_client=make_cm_http_client)


def filter_impala_summaries_for_owner(
    config: BatchConfig,
    summaries: list[cm_profiles.CMQuerySummary],
) -> list[cm_profiles.CMQuerySummary]:
    if config.source_visibility != SOURCE_VISIBILITY_OWNER_RAW:
        return summaries
    owner_users = set(config.collectable_owner_users)
    if not owner_users:
        return []
    return [summary for summary in summaries if summary.user in owner_users]


def filter_impala_summaries_for_window(
    config: BatchConfig,
    summaries: list[cm_profiles.CMQuerySummary],
) -> list[cm_profiles.CMQuerySummary]:
    if config.only_running:
        return [summary for summary in summaries if is_running_query_summary(summary)]
    start, end = discovery_window_bounds(config)
    filtered: list[cm_profiles.CMQuerySummary] = []
    for summary in summaries:
        if is_running_query_summary(summary):
            if config.include_running:
                filtered.append(summary)
            continue
        parsed = impala_summary_window_timestamp(summary)
        if parsed is None:
            continue
        if start <= parsed < end:
            filtered.append(summary)
    return filtered


def impala_summary_window_timestamp(summary: cm_profiles.CMQuerySummary) -> datetime | None:
    start_time = parse_optional_impala_summary_timestamp(summary.start_time)
    end_time = parse_optional_impala_summary_timestamp(summary.end_time)
    if start_time is not None and end_time is not None and end_time < start_time:
        return start_time
    return end_time or start_time


def parse_optional_impala_summary_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_cm_timestamp(value)
    except Exception:  # noqa: BLE001 - malformed daemon timestamps are ignored for bounded discovery.
        return None


if __name__ == "__main__":
    raise SystemExit(main())
