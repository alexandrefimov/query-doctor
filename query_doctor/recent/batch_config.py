"""Configuration and safety checks for the Recent batch workflow."""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from query_doctor.cli import collect_cm_profiles as cm_profiles
from query_doctor.config.contract import merge_kerberos_cache_env
from query_doctor.impala.profile_source import (
    DEFAULT_IMPALA_PROFILE_PORT,
    DEFAULT_IMPALA_PROFILE_SCHEME,
    DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    normalize_impala_profile_hosts,
    normalize_impala_profile_scheme,
)
from query_doctor.impala import hms_metadata, hs2_runner
from query_doctor.impala.hms_metadata import (
    DEFAULT_HMS_POSTGRES_DSN_ENV,
    METADATA_SOURCE_HMS_POSTGRES,
    METADATA_SOURCE_IMPALA,
    METADATA_SOURCES,
)
from query_doctor.impala.kerberos_preflight import check_kerberos_ticket_cache
from query_doctor.impala.metadata_workflow import METADATA_DRIVER_MISSING_REASON
from query_doctor.impala.query_discovery import DEFAULT_MAX_QUERY_LIST_BYTES
from query_doctor.prometheus.timeseries import (
    DEFAULT_PROMETHEUS_METRICS_PROFILE,
    DEFAULT_PROMETHEUS_STEP_SEC,
    DEFAULT_PROMETHEUS_TIMEOUT_SEC,
    DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
    normalize_prometheus_metrics_profile,
)
from query_doctor.recent.batch_models import BatchConfig
from query_doctor.recent.profile_budget import DEFAULT_PROFILE_JOB_MAX_AGE_HOURS
from query_doctor.recent.workload_history import DEFAULT_WORKLOAD_HISTORY_MAX_BYTES
from query_doctor.source_visibility import (
    SOURCE_VISIBILITY_OWNER_RAW,
    SOURCE_VISIBILITY_SAFE,
    collectable_owner_user as normalize_collectable_owner_user,
    collectable_owner_users as normalize_collectable_owner_users,
    normalize_source_visibility,
    source_owner_user_from_env,
)


MAX_CM_INSPECT_LIMIT = 5000
MAX_RAW_CM_SUMMARY_SCAN_LIMIT = 20000
MAX_TRIAGE_PROFILE_LIMIT = 5000
MAX_IMPALA_QUERY_LIST_BYTES = 256 * 1024 * 1024
MAX_METADATA_TOP_LIMIT = 200
DEFAULT_CM_TIMESERIES_TOP_LIMIT = 10
MAX_CM_TIMESERIES_TOP_LIMIT = 200
DEFAULT_CM_EVENTS_MAX_EVENTS = 50
MAX_CM_EVENTS_MAX_EVENTS = 200
BAD_METADATA_REFRESH_LIMIT = 50
SUSPICIOUS_METADATA_REFRESH_LIMIT = 20
SUSPICIOUS_METADATA_PROMOTION_SCORE_FLOOR = 23
MAX_JOBS = 4
MAX_HIGH_JOBS = 100
MAX_CM_JOBS = 100
MAX_METADATA_JOBS = 5
ORDER_CHOICES = (
    "recent",
    "duration-desc",
    "duration-asc",
    "recent-duration-desc",
    "status-priority",
)
METADATA_MODE_CHOICES = ("auto", "on", "off", "dry-run")
RECENT_HISTORY_BACKEND_CHOICES = ("disabled", "sqlite", "postgres")
DEFAULT_RECENT_HISTORY_POSTGRES_DSN_ENV = "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN"
ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
SAFE_OUTPUT_PREFIX = "query-doctor-"
SYSTEM_OUTPUT_ROOTS = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/var",
    "/opt",
    "/System",
    "/Library",
    "/Applications",
    "/private/etc",
    "/private/var",
)


def elapsed_seconds(started: float) -> float:
    return round(max(0.0, time.monotonic() - started), 3)


def format_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}s"


def duration_filter_label(config: BatchConfig) -> str:
    lower = config.min_duration_sec
    upper = config.max_duration_sec
    if lower is None and upper is None:
        return "none"
    parts: list[str] = []
    if lower is not None:
        parts.append(f">= {display_float(lower)} sec")
    if upper is not None:
        parts.append(f"<= {display_float(upper)} sec")
    return " and ".join(parts)


def display_float(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def validate_cm_time_bound(value: str, *, name: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"{name} must be formatted as YYYY-MM-DDTHH:MM:SSZ") from exc
    return value


def build_batch_config(
    args: argparse.Namespace,
    *,
    env: dict[str, str],
    cwd: Path,
    repo_root: Path,
    validate_scan_selection_limits: bool = True,
) -> BatchConfig:
    use_repo_default = not any(
        (
            args.cm_url,
            args.cluster,
            args.service,
            args.ca_bundle,
            getattr(args, "query_profile_source", None),
            getattr(args, "impala_profile_hosts", None),
        )
    )
    default_config_path = None
    if not args.config:
        default_config_path = cm_profiles.discover_default_local_config(
            cwd=cwd,
            repo_root=repo_root,
            use_repo_default=use_repo_default,
        )
    effective_config_path = resolve_config_path(args.config, cwd) or (
        str(default_config_path) if default_config_path else None
    )
    try:
        config_values = cm_profiles.load_effective_local_config(
            args.config,
            cwd=cwd,
            repo_root=repo_root,
            use_repo_default=use_repo_default,
        )
    except cm_profiles.ConfigError:
        if args.config or use_repo_default:
            raise
        # Explicit connection flags should not be blocked by an unrelated
        # implicit local config in the current working directory.
        config_values = {}
        effective_config_path = None
    config_values = apply_config_cluster(
        config_values,
        getattr(args, "config_cluster", None),
        auto_select=use_repo_default,
    )
    query_profile_source = (
        first_string(
            getattr(args, "query_profile_source", None),
            config_values.get("query_profile_source"),
            "cm",
        )
        or "cm"
    )
    if query_profile_source not in {"cm", "impala"}:
        raise ValueError("--query-profile-source must be one of: cm, impala")
    source_visibility = normalize_source_visibility(
        first_string(
            getattr(args, "source_visibility", None),
            config_values.get("source_visibility"),
            SOURCE_VISIBILITY_SAFE,
        )
    )
    source_owner_user_values = source_owner_user_arg_values(
        getattr(args, "source_owner_user", None)
    )
    cli_source_owner_user = source_owner_user_values[0] if source_owner_user_values else None
    source_owner_user = normalize_collectable_owner_user(
        first_string(
            cli_source_owner_user,
            config_values.get("source_owner_user"),
            source_owner_user_from_env(env),
        )
    )
    collectable_owner_user_values = normalize_collectable_owner_users(
        source_owner_user,
        source_owner_user_values,
    )
    if source_owner_user is None and collectable_owner_user_values:
        source_owner_user = collectable_owner_user_values[0]
    cm_url = first_string(args.cm_url, env.get("CM_URL"), config_values.get("cm_url"))
    cluster = first_string(args.cluster, config_values.get("cluster"))
    service = first_string(args.service, config_values.get("service"))
    impala_profile_hosts = normalize_impala_profile_hosts(
        first_string_tuple(
            getattr(args, "impala_profile_hosts", None), config_values.get("impala_profile_hosts")
        )
    )
    impala_profile_port = first_int(
        getattr(args, "impala_profile_port", None),
        config_values.get("impala_profile_port"),
        default=DEFAULT_IMPALA_PROFILE_PORT,
    )
    impala_profile_scheme = normalize_impala_profile_scheme(
        first_string(
            getattr(args, "impala_profile_scheme", None),
            config_values.get("impala_profile_scheme"),
            DEFAULT_IMPALA_PROFILE_SCHEME,
        )
    )
    impala_profile_timeout_sec = first_int(
        getattr(args, "impala_profile_timeout_sec", None),
        config_values.get("impala_profile_timeout_sec"),
        default=DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    )
    impala_query_list_max_bytes = first_int(
        getattr(args, "impala_query_list_max_bytes", None),
        config_values.get("impala_query_list_max_bytes"),
        default=DEFAULT_MAX_QUERY_LIST_BYTES,
    )
    impala_profile_prefer_json = first_bool(
        getattr(args, "impala_profile_prefer_json", None),
        config_values.get("impala_profile_prefer_json"),
        default=False,
    )
    impala_profile_collect_docs = first_bool(
        getattr(args, "impala_profile_collect_docs", None),
        config_values.get("impala_profile_collect_docs"),
        default=False,
    )
    impala_collect_admission_context = first_bool(
        getattr(args, "impala_collect_admission_context", None),
        config_values.get("impala_collect_admission_context"),
        default=False,
    )
    prometheus_url = first_string(
        getattr(args, "prometheus_url", None),
        config_values.get("prometheus_url"),
    )
    collect_prometheus_timeseries = first_bool(
        getattr(args, "collect_prometheus_timeseries", None),
        config_values.get("recent_collect_prometheus_timeseries"),
        config_values.get("collect_prometheus_timeseries"),
        default=bool(prometheus_url),
    )
    if collect_prometheus_timeseries and not prometheus_url:
        raise ValueError(
            "--collect-prometheus-timeseries requires --prometheus-url or local config prometheus_url."
        )
    prometheus_metrics_profile = normalize_prometheus_metrics_profile(
        first_string(
            getattr(args, "prometheus_metrics_profile", None),
            config_values.get("prometheus_metrics_profile"),
            DEFAULT_PROMETHEUS_METRICS_PROFILE,
        )
    )
    prometheus_step_sec = first_int(
        getattr(args, "prometheus_step_sec", None),
        config_values.get("prometheus_step_sec"),
        default=DEFAULT_PROMETHEUS_STEP_SEC,
    )
    prometheus_timeseries_padding_sec = first_int(
        getattr(args, "prometheus_timeseries_padding_sec", None),
        config_values.get("prometheus_timeseries_padding_sec"),
        default=DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
    )
    prometheus_timeout_sec = first_int(
        getattr(args, "prometheus_timeout_sec", None),
        config_values.get("prometheus_timeout_sec"),
        default=DEFAULT_PROMETHEUS_TIMEOUT_SEC,
    )
    if query_profile_source == "cm":
        if not cm_url:
            raise ValueError("Missing --cm-url, CM_URL, or local config cm_url.")
        if not cluster:
            raise ValueError("Missing --cluster or local config cluster.")
        if not service:
            raise ValueError("Missing --service or local config service.")
    else:
        if not impala_profile_hosts:
            raise ValueError(
                "Impala query discovery requires --impala-profile-host or local config impala_profile_hosts."
            )

    cm_inspect_limit = first_int(
        args.cm_inspect_limit,
        config_values.get("recent_cm_summary_limit"),
        default=100,
    )
    triage_profile_limit = first_int(
        args.select_limit_alias,
        args.triage_profile_limit,
        config_values.get("recent_profile_analysis_limit"),
        default=20,
    )
    metadata_top_limit = first_int(
        args.metadata_top_limit,
        config_values.get("recent_metadata_top_limit"),
        default=0,
    )
    cm_timeseries_top_limit = first_int(
        args.cm_timeseries_top_limit,
        config_values.get("recent_cm_timeseries_top_limit"),
        default=DEFAULT_CM_TIMESERIES_TOP_LIMIT,
    )
    cm_events_max_events = first_int(
        args.cm_events_max_events,
        config_values.get("recent_cm_events_max_events"),
        default=DEFAULT_CM_EVENTS_MAX_EVENTS,
    )
    collect_cm_events = first_bool(
        args.collect_cm_events,
        config_values.get("recent_collect_cm_events"),
        default=False,
    )
    collect_cm_timeseries = first_bool(
        args.collect_cm_timeseries,
        config_values.get("recent_collect_cm_timeseries"),
        config_values.get("collect_cm_timeseries"),
        default=False,
    )
    if query_profile_source == "impala":
        collect_cm_events = False
        collect_cm_timeseries = False
    else:
        collect_prometheus_timeseries = False
    recent_window_minutes = first_int(
        args.recent_window_minutes,
        config_values.get("recent_window_minutes"),
        default=60,
    )
    from_time = first_string(args.from_time, config_values.get("recent_from_time"))
    to_time = first_string(args.to_time, config_values.get("recent_to_time"))
    if bool(from_time) != bool(to_time):
        raise ValueError("--from-time and --to-time must be provided together")
    if from_time and to_time:
        from_time = validate_cm_time_bound(from_time, name="--from-time")
        to_time = validate_cm_time_bound(to_time, name="--to-time")
        if from_time >= to_time:
            raise ValueError("--to-time must be later than --from-time")
    if cm_inspect_limit > MAX_CM_INSPECT_LIMIT:
        raise ValueError(f"--cm-inspect-limit must be <= {MAX_CM_INSPECT_LIMIT}")
    if triage_profile_limit > MAX_TRIAGE_PROFILE_LIMIT:
        raise ValueError(f"--triage-profile-limit must be <= {MAX_TRIAGE_PROFILE_LIMIT}")
    if impala_query_list_max_bytes > MAX_IMPALA_QUERY_LIST_BYTES:
        raise ValueError(f"--impala-query-list-max-bytes must be <= {MAX_IMPALA_QUERY_LIST_BYTES}")
    if validate_scan_selection_limits and triage_profile_limit > cm_inspect_limit:
        raise ValueError("--triage-profile-limit must be <= --cm-inspect-limit")
    if metadata_top_limit > MAX_METADATA_TOP_LIMIT:
        raise ValueError(f"--metadata-top-limit must be <= {MAX_METADATA_TOP_LIMIT}")
    if cm_timeseries_top_limit > MAX_CM_TIMESERIES_TOP_LIMIT:
        raise ValueError(f"--cm-timeseries-top-limit must be <= {MAX_CM_TIMESERIES_TOP_LIMIT}")
    if cm_events_max_events > MAX_CM_EVENTS_MAX_EVENTS:
        raise ValueError(f"--cm-events-max-events must be <= {MAX_CM_EVENTS_MAX_EVENTS}")
    cm_jobs = first_int(args.cm_jobs, config_values.get("recent_cm_jobs"), default=args.jobs)
    metadata_jobs = first_int(
        args.metadata_jobs, config_values.get("recent_metadata_jobs"), default=5
    )
    validate_jobs_config(
        args.jobs,
        allow_high_jobs=args.allow_high_jobs,
        metadata_mode=args.metadata_mode,
        top_reports=args.top_reports,
    )
    validate_cm_jobs_config(cm_jobs)
    validate_metadata_jobs_config(metadata_jobs)
    min_duration_sec = (
        None
        if args.no_min_duration_filter
        else first_float(
            args.min_duration_sec,
            config_values.get("recent_min_duration_sec"),
            default=60.0,
        )
    )
    max_duration_sec = first_float(
        args.max_duration_sec,
        config_values.get("recent_max_duration_sec"),
        default=None,
    )
    if max_duration_sec is not None and min_duration_sec is not None:
        if max_duration_sec < min_duration_sec:
            raise ValueError("--max-duration-sec must be >= --min-duration-sec")
    collect_workload_history = first_bool(
        getattr(args, "collect_workload_history", None),
        config_values.get("recent_collect_workload_history"),
        config_values.get("collect_workload_history"),
        default=False,
    )
    workload_history_path = expand_optional_path(
        first_string(
            getattr(args, "workload_history_path", None),
            config_values.get("recent_workload_history_path"),
            config_values.get("workload_history_path"),
        ),
        cwd=cwd,
    )
    workload_history_max_bytes = first_int(
        getattr(args, "workload_history_max_bytes", None),
        config_values.get("recent_workload_history_max_bytes"),
        config_values.get("workload_history_max_bytes"),
        default=DEFAULT_WORKLOAD_HISTORY_MAX_BYTES,
    )
    recent_history_db = expand_optional_path(
        first_string(
            getattr(args, "recent_history_db", None),
            config_values.get("recent_history_db"),
        ),
        cwd=cwd,
    )
    recent_history_backend = normalize_recent_history_backend(
        first_string(
            getattr(args, "recent_history_backend", None),
            config_values.get("recent_history_backend"),
        ),
        recent_history_db=recent_history_db,
    )
    recent_history_postgres_dsn_env = validate_env_var_name(
        first_string(
            getattr(args, "recent_history_postgres_dsn_env", None),
            config_values.get("recent_history_postgres_dsn_env"),
            DEFAULT_RECENT_HISTORY_POSTGRES_DSN_ENV,
        )
        or DEFAULT_RECENT_HISTORY_POSTGRES_DSN_ENV,
        name="recent_history_postgres_dsn_env",
    )
    metadata_source = (
        first_string(
            getattr(args, "metadata_source", None),
            config_values.get("metadata_source"),
            METADATA_SOURCE_IMPALA,
        )
        or METADATA_SOURCE_IMPALA
    )
    if metadata_source not in METADATA_SOURCES:
        raise ValueError(f"metadata_source must be one of: {', '.join(METADATA_SOURCES)}.")
    metadata_hms_postgres_dsn_env = validate_env_var_name(
        first_string(
            getattr(args, "metadata_hms_postgres_dsn_env", None),
            config_values.get("metadata_hms_postgres_dsn_env"),
            DEFAULT_HMS_POSTGRES_DSN_ENV,
        )
        or DEFAULT_HMS_POSTGRES_DSN_ENV,
        name="metadata_hms_postgres_dsn_env",
    )
    recent_history_collector_summary_json = expand_optional_path(
        first_string(
            getattr(args, "recent_history_collector_summary_json", None),
            config_values.get("recent_history_collector_summary_json"),
        ),
        cwd=cwd,
    )
    if recent_history_backend == "sqlite" and recent_history_db is None:
        raise ValueError("recent_history_backend=sqlite requires --recent-history-db.")
    recent_history_summary_retention_days = validate_optional_positive_int(
        first_int(
            getattr(args, "recent_history_summary_retention_days", None),
            config_values.get("recent_history_summary_retention_days"),
            default=None,
        ),
        name="recent_history_summary_retention_days",
    )
    profile_job_max_age_hours = first_int(
        getattr(args, "profile_job_max_age_hours", None),
        config_values.get("recent_profile_job_max_age_hours"),
        default=DEFAULT_PROFILE_JOB_MAX_AGE_HOURS,
    )
    if profile_job_max_age_hours is None or profile_job_max_age_hours < 0:
        raise ValueError("recent_profile_job_max_age_hours must be a non-negative integer.")
    recent_history_profile_job_retention_days = validate_optional_positive_int(
        first_int(
            getattr(args, "recent_history_profile_job_retention_days", None),
            config_values.get("recent_history_profile_job_retention_days"),
            default=None,
        ),
        name="recent_history_profile_job_retention_days",
    )
    recent_history_analysis_cache_retention_days = validate_optional_positive_int(
        first_int(
            getattr(args, "recent_history_analysis_cache_retention_days", None),
            config_values.get("recent_history_analysis_cache_retention_days"),
            default=None,
        ),
        name="recent_history_analysis_cache_retention_days",
    )
    recent_history_profile_artifact_retention_days = validate_optional_positive_int(
        first_int(
            getattr(args, "recent_history_profile_artifact_retention_days", None),
            config_values.get("recent_history_profile_artifact_retention_days"),
            default=None,
        ),
        name="recent_history_profile_artifact_retention_days",
    )
    if recent_history_backend == "disabled" and (
        recent_history_summary_retention_days
        or recent_history_profile_job_retention_days
        or recent_history_analysis_cache_retention_days
        or recent_history_profile_artifact_retention_days
    ):
        raise ValueError("recent history retention requires recent_history_backend.")

    out_value = first_string(args.out, config_values.get("out"))
    if not out_value:
        raise ValueError("missing required output directory: provide --out or config field out")
    out = Path(out_value).expanduser()
    if not out.is_absolute():
        out = (cwd / out).resolve()
    validate_batch_output_path(out, repo_root)
    progress_jsonl = None
    if args.progress_jsonl:
        progress_jsonl = Path(args.progress_jsonl).expanduser()
        if not progress_jsonl.is_absolute():
            progress_jsonl = (cwd / progress_jsonl).resolve()
    analyzed_profile_reuse_roots: list[Path] = []
    for value in getattr(args, "analyzed_profile_reuse_roots", None) or ():
        path = expand_optional_path(value, cwd=cwd)
        if path is not None:
            analyzed_profile_reuse_roots.append(path)

    ca_bundle = expand_optional_path_string(
        first_string(args.ca_bundle, env.get("CM_CA_BUNDLE"), config_values.get("ca_bundle"))
    )
    insecure_skip_verify = first_bool(
        args.insecure_skip_verify,
        config_values.get("insecure_skip_verify"),
        default=False,
    )
    privacy_mode = first_bool(config_values.get("privacy_mode"), default=True)
    redact_identifiers = first_bool(
        getattr(args, "metadata_redact_identifiers", None),
        config_values.get("redact_identifiers"),
        default=privacy_mode,
    )
    redact_hosts = first_bool(
        getattr(args, "metadata_redact_hosts", None),
        config_values.get("redact_hosts"),
        default=privacy_mode,
    )
    metadata_redact = first_bool(
        args.metadata_redact, config_values.get("metadata_redact"), default=privacy_mode
    )

    recent_user = first_string(args.user, config_values.get("recent_user"))
    if source_visibility == SOURCE_VISIBILITY_OWNER_RAW:
        if not collectable_owner_user_values:
            raise ValueError(
                "source_visibility=owner_raw requires at least one collectable source_owner_user or simple Kerberos principal."
            )
        if recent_user and recent_user not in collectable_owner_user_values:
            raise ValueError(
                "source_visibility=owner_raw requires recent_user to match a collectable source_owner_user."
            )
        if not recent_user and len(collectable_owner_user_values) == 1:
            recent_user = collectable_owner_user_values[0]

    return BatchConfig(
        out=out,
        cm_url=str(cm_url) if cm_url else None,
        cluster=str(cluster) if cluster else None,
        service=str(service) if service else None,
        cm_username=first_string(env.get("CM_USERNAME"), config_values.get("username")),
        ca_bundle=ca_bundle,
        verify_tls=not insecure_skip_verify,
        recent_window_minutes=recent_window_minutes,
        from_time=from_time,
        to_time=to_time,
        cm_inspect_limit=cm_inspect_limit,
        triage_profile_limit=triage_profile_limit,
        metadata_top_limit=metadata_top_limit,
        min_duration_sec=min_duration_sec,
        max_duration_sec=max_duration_sec,
        order=first_string(args.order, config_values.get("recent_order"), "duration-desc")
        or "duration-desc",
        include_failed=first_bool(
            args.include_failed, config_values.get("recent_include_failed"), default=False
        ),
        include_running=first_bool(
            args.include_running, config_values.get("recent_include_running"), default=False
        ),
        only_running=first_bool(
            args.only_running, config_values.get("recent_only_running"), default=False
        ),
        user=recent_user,
        pool=first_string(args.pool, config_values.get("recent_pool")),
        query_type=first_string(args.query_type, config_values.get("query_type")),
        max_profile_bytes=first_int(
            args.max_profile_bytes,
            config_values.get("max_profile_bytes"),
            default=cm_profiles.DEFAULT_MAX_PROFILE_BYTES,
        ),
        collect_cm_events=collect_cm_events,
        cm_events_max_events=cm_events_max_events,
        collect_cm_timeseries=collect_cm_timeseries,
        cm_metrics_profile=cm_profiles.validate_cm_metrics_profile(
            first_string(
                args.cm_metrics_profile,
                config_values.get("cm_metrics_profile"),
                env.get("CM_METRICS_PROFILE"),
                cm_profiles.DEFAULT_CM_METRICS_PROFILE,
            )
        ),
        cm_timeseries_top_limit=cm_timeseries_top_limit,
        cm_timeseries_padding_sec=first_int(
            args.cm_timeseries_padding_sec,
            config_values.get("cm_timeseries_padding_sec"),
            default=cm_profiles.DEFAULT_CM_TIMESERIES_PADDING_SEC,
        ),
        max_timeseries_bytes=first_int(
            args.max_timeseries_bytes,
            config_values.get("max_timeseries_bytes"),
            default=cm_profiles.DEFAULT_MAX_TIMESERIES_BYTES,
        ),
        max_timeseries_points=first_int(
            args.max_timeseries_points,
            config_values.get("max_timeseries_points"),
            default=cm_profiles.DEFAULT_MAX_TIMESERIES_POINTS,
        ),
        metadata_mode=args.metadata_mode,
        metadata_coordinator=first_string(
            args.metadata_coordinator, config_values.get("metadata_coordinator")
        ),
        metadata_auth=first_string(
            args.metadata_auth, config_values.get("metadata_auth"), "kerberos"
        )
        or "kerberos",
        metadata_protocol=first_string(
            args.metadata_protocol, config_values.get("metadata_protocol"), "hs2"
        )
        or "beeswax",
        metadata_kerberos_service_name=first_string(
            getattr(args, "metadata_kerberos_service_name", None),
            config_values.get("metadata_kerberos_service_name"),
            config_values.get("impala_kerberos_service_name"),
        ),
        metadata_kerberos_host_fqdn=first_string(
            getattr(args, "metadata_kerberos_host_fqdn", None),
            config_values.get("metadata_kerberos_host_fqdn"),
        ),
        metadata_ssl=first_bool(
            args.metadata_ssl, config_values.get("metadata_ssl"), default=False
        ),
        metadata_ca_cert=first_string(args.metadata_ca_cert, config_values.get("metadata_ca_cert")),
        metadata_timeout_sec=first_int(
            args.metadata_timeout_sec,
            config_values.get("metadata_timeout_sec"),
            default=30,
        ),
        metadata_max_tables=first_int(
            args.metadata_max_tables, config_values.get("metadata_max_tables"), default=None
        ),
        metadata_max_output_bytes=first_int(
            args.metadata_max_output_bytes,
            config_values.get("metadata_max_output_bytes"),
            default=None,
        ),
        metadata_redact=metadata_redact,
        top_reports=args.top_reports,
        cm_jobs=cm_jobs,
        jobs=args.jobs,
        metadata_jobs=metadata_jobs,
        allow_high_jobs=args.allow_high_jobs,
        discover_only=args.discover_only,
        overwrite=args.overwrite,
        config_path=effective_config_path,
        progress_jsonl=progress_jsonl,
        krb5ccname=first_string(config_values.get("krb5ccname")),
        query_profile_source=query_profile_source,
        impala_profile_hosts=impala_profile_hosts,
        impala_profile_port=int(impala_profile_port or DEFAULT_IMPALA_PROFILE_PORT),
        impala_profile_scheme=impala_profile_scheme,
        impala_profile_timeout_sec=int(
            impala_profile_timeout_sec or DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC
        ),
        impala_query_list_max_bytes=int(
            impala_query_list_max_bytes or DEFAULT_MAX_QUERY_LIST_BYTES
        ),
        impala_profile_prefer_json=impala_profile_prefer_json,
        impala_profile_collect_docs=impala_profile_collect_docs,
        impala_collect_admission_context=impala_collect_admission_context,
        collect_prometheus_timeseries=collect_prometheus_timeseries,
        prometheus_url=prometheus_url,
        prometheus_metrics_profile=prometheus_metrics_profile,
        prometheus_step_sec=int(prometheus_step_sec or DEFAULT_PROMETHEUS_STEP_SEC),
        prometheus_timeseries_padding_sec=int(
            prometheus_timeseries_padding_sec or DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC
        ),
        prometheus_timeout_sec=int(prometheus_timeout_sec or DEFAULT_PROMETHEUS_TIMEOUT_SEC),
        collect_workload_history=collect_workload_history,
        workload_history_path=workload_history_path,
        workload_history_max_bytes=int(
            workload_history_max_bytes or DEFAULT_WORKLOAD_HISTORY_MAX_BYTES
        ),
        recent_history_backend=recent_history_backend,
        recent_history_db=recent_history_db,
        recent_history_postgres_dsn_env=recent_history_postgres_dsn_env,
        recent_history_collector_summary_json=recent_history_collector_summary_json,
        recent_history_summary_retention_days=recent_history_summary_retention_days,
        recent_history_profile_job_retention_days=recent_history_profile_job_retention_days,
        recent_history_analysis_cache_retention_days=recent_history_analysis_cache_retention_days,
        recent_history_profile_artifact_retention_days=recent_history_profile_artifact_retention_days,
        privacy_mode=privacy_mode,
        redact_identifiers=redact_identifiers,
        redact_hosts=redact_hosts,
        source_visibility=source_visibility,
        source_owner_user=source_owner_user,
        collectable_owner_users=collectable_owner_user_values,
        analyzed_profile_reuse_roots=tuple(analyzed_profile_reuse_roots),
        metadata_source=metadata_source,
        metadata_hms_postgres_dsn_env=metadata_hms_postgres_dsn_env,
        profile_job_max_age_hours=profile_job_max_age_hours,
    )


def apply_config_cluster(
    config_values: dict[str, object],
    cluster_id: str | None,
    *,
    auto_select: bool = True,
) -> dict[str, object]:
    requested = first_string(cluster_id)
    clusters = config_values.get("clusters")
    if not requested:
        if not auto_select:
            return config_values
        selected = default_config_cluster(config_values)
        if selected is None:
            return config_values
        merged = {key: value for key, value in config_values.items() if key != "clusters"}
        merged.update(cluster_config_overrides(selected))
        return merged
    if not isinstance(clusters, list):
        raise ValueError("--config-cluster requires local config clusters[].")
    cluster = find_config_cluster(clusters, requested)
    if cluster is not None:
        merged = {key: value for key, value in config_values.items() if key != "clusters"}
        merged.update(cluster_config_overrides(cluster))
        return merged
    raise ValueError(f"--config-cluster {requested!r} was not found in local config clusters[].")


def default_config_cluster(config_values: dict[str, object]) -> dict[str, object] | None:
    clusters = config_values.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return None
    active_cluster_key = first_string(config_values.get("active_cluster_key"))
    if active_cluster_key:
        selected = find_config_cluster(clusters, active_cluster_key)
        if selected is None:
            raise ValueError("active_cluster_key was not found in local config clusters[].")
        return selected
    if len(clusters) == 1:
        cluster = clusters[0]
        if not isinstance(cluster, dict):
            raise ValueError("local config clusters[] entries must be objects.")
        return cluster
    raise ValueError(
        "local config clusters[] has multiple entries; pass --config-cluster "
        "or set active_cluster_key."
    )


def find_config_cluster(
    clusters: list[object],
    cluster_id: str,
) -> dict[str, object] | None:
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        if str(cluster.get("id") or "") == cluster_id:
            return cluster
    return None


def cluster_config_overrides(cluster: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in cluster.items() if key not in {"id", "label"}}


def validate_jobs_config(
    jobs: int, *, allow_high_jobs: bool, metadata_mode: str, top_reports: int
) -> None:
    if jobs > MAX_HIGH_JOBS:
        raise ValueError(f"--jobs must be <= {MAX_HIGH_JOBS}")
    if allow_high_jobs:
        if top_reports != 0:
            raise ValueError("--allow-high-jobs requires --top-reports 0")
        return
    if jobs > MAX_JOBS:
        raise ValueError(
            f"--jobs must be <= {MAX_JOBS} unless --allow-high-jobs is used with --top-reports 0"
        )


def validate_cm_jobs_config(cm_jobs: int) -> None:
    if cm_jobs > MAX_CM_JOBS:
        raise ValueError(f"--cm-jobs must be <= {MAX_CM_JOBS}")


def validate_metadata_jobs_config(metadata_jobs: int) -> None:
    if metadata_jobs > MAX_METADATA_JOBS:
        raise ValueError(f"--metadata-jobs must be <= {MAX_METADATA_JOBS}")


def normalize_recent_history_backend(value: str | None, *, recent_history_db: Path | None) -> str:
    if value is None:
        return "sqlite" if recent_history_db is not None else "disabled"
    normalized = value.strip().lower()
    if normalized not in RECENT_HISTORY_BACKEND_CHOICES:
        raise ValueError(
            "--recent-history-backend must be one of: " + ", ".join(RECENT_HISTORY_BACKEND_CHOICES)
        )
    return normalized


def validate_env_var_name(value: str, *, name: str) -> str:
    normalized = value.strip()
    if not ENV_NAME_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be an uppercase environment variable name.")
    return normalized


def first_string(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def source_owner_user_arg_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def first_string_tuple(*values: object) -> tuple[str, ...]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            items = tuple(item.strip() for item in value.split(",") if item.strip())
        elif isinstance(value, (list, tuple)):
            items = tuple(str(item).strip() for item in value if str(item).strip())
        else:
            items = (str(value).strip(),)
        if items:
            return items
    return ()


def first_int(*values: object, default: int | None) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        return int(value)
    return default


def validate_optional_positive_int(value: int | None, *, name: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def first_float(*values: object, default: float | None) -> float | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        return float(value)
    return default


def first_bool(*values: object, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        return bool(value)
    return default


def resolve_config_path(config_path: str | None, cwd: Path) -> str | None:
    if not config_path:
        return None
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return str(path.resolve())


def expand_optional_path_string(value: str | None) -> str | None:
    return str(Path(value).expanduser()) if value else None


def expand_optional_path(value: str | None, *, cwd: Path) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (cwd / path).resolve()


def effective_subprocess_env(env: dict[str, str], krb5ccname: str | None) -> dict[str, str]:
    return merge_kerberos_cache_env(env, {"krb5ccname": krb5ccname})


def validate_batch_output_path(out: Path, repo_root: Path) -> None:
    repo_root = repo_root.resolve()
    out = out.resolve()
    if path_is_relative_to(out, repo_root):
        raise ValueError(
            "--out must be outside the repository. Use /tmp or another directory outside the repository."
        )
    validate_not_dangerous_output_path(out)


def validate_not_dangerous_output_path(out: Path) -> None:
    resolved = out.resolve()
    root = Path(resolved.anchor or "/").resolve()
    home = Path.home().resolve()
    safe_temp_roots = safe_output_temp_roots()
    if resolved == root:
        raise ValueError("--out must point to a dedicated batch directory, not filesystem root")
    if resolved in safe_temp_roots:
        raise ValueError(
            "--out must point to a dedicated query-doctor-* batch directory, not the temp root itself"
        )
    if resolved == home:
        raise ValueError("--out must point to a dedicated batch directory, not the home directory")
    if resolved.parent == root:
        raise ValueError("--out path is too shallow; use a dedicated /tmp batch directory")
    if resolved.parent == home:
        raise ValueError(
            "--out must not be a direct child of the home directory; use /tmp or another dedicated directory"
        )
    under_safe_temp = any(path_is_relative_to(resolved, temp_root) for temp_root in safe_temp_roots)
    if not under_safe_temp:
        for system_root in system_output_roots():
            if path_is_relative_to(resolved, system_root):
                raise ValueError(
                    "--out must not point inside a system directory; use /tmp/query-doctor-*"
                )
        raise ValueError(
            "--out must be a dedicated query-doctor-* directory under /tmp or the system temp directory"
        )
    if not resolved.name.startswith(SAFE_OUTPUT_PREFIX):
        raise ValueError("--out directory name must start with query-doctor-")


def prepare_batch_output_dir(out: Path, *, repo_root: Path, overwrite: bool) -> None:
    validate_batch_output_path(out, repo_root)
    if out.exists() and out.is_symlink():
        raise ValueError("--out must not be a symlink")
    if out.exists() and not out.is_dir():
        raise ValueError("--out exists and is not a directory")
    if out.exists() and any(out.iterdir()):
        if not overwrite:
            raise ValueError(
                "output directory exists and is not empty; use --overwrite or choose a new /tmp path"
            )
        validate_safe_overwrite_target(out, repo_root=repo_root)
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)


def validate_safe_overwrite_target(out: Path, *, repo_root: Path) -> None:
    validate_batch_output_path(out, repo_root)
    if not out.exists():
        return
    if out.is_symlink():
        raise ValueError("--out must not be a symlink")
    if not out.is_dir():
        raise ValueError("--out exists and is not a directory")


def safe_output_temp_roots() -> set[Path]:
    return {
        Path("/tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }


def system_output_roots() -> tuple[Path, ...]:
    return tuple(Path(value).resolve() for value in SYSTEM_OUTPUT_ROOTS)


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def preflight(config: BatchConfig, *, env: dict[str, str]) -> None:
    if config.query_profile_source == "cm" and not (env.get("CM_PASSWORD") or env.get("CM_TOKEN")):
        raise ValueError("CM auth env is not set in this execution environment.")
    if metadata_kerberos_preflight_required(config):
        ticket_status = check_kerberos_ticket_cache(env)
        if not ticket_status.ok:
            raise ValueError(ticket_status.reason or "Kerberos ticket preflight failed.")
    if metadata_configuration_preflight_required(config):
        if config.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
            if not hms_metadata.driver_available():
                raise ValueError(hms_metadata.POSTGRES_DRIVER_MISSING_REASON)
        elif not hs2_runner.driver_available():
            raise ValueError(METADATA_DRIVER_MISSING_REASON)


def metadata_configuration_preflight_required(config: BatchConfig) -> bool:
    if config.discover_only:
        return False
    if config.metadata_source != METADATA_SOURCE_HMS_POSTGRES and not config.metadata_coordinator:
        return False
    if config.metadata_mode not in {"auto", "on"}:
        return False
    return config.metadata_top_limit > 0 or config.top_reports > 0


def metadata_kerberos_preflight_required(config: BatchConfig) -> bool:
    if not metadata_configuration_preflight_required(config):
        return False
    if config.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
        return False
    return config.metadata_auth == "kerberos"


def secret_values(env: dict[str, str]) -> list[str]:
    return [value for value in (env.get("CM_PASSWORD"), env.get("CM_TOKEN")) if value]
