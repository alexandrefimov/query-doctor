"""Data models for the Recent batch workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_doctor.cli import collect_cm_profiles as cm_profiles
    from query_doctor.recent.optimizer_rewrite_support import OptimizerRewriteSupport
    from query_doctor.recent.query_optimization_score import QueryOptimizationCandidateScore
    from query_doctor.recent.stats_optimization_score import StatsOptimizationCandidateScore


@dataclass(frozen=True)
class BatchConfig:
    out: Path
    cm_url: str | None
    cluster: str | None
    service: str | None
    cm_username: str | None
    ca_bundle: str | None
    verify_tls: bool
    recent_window_minutes: int
    cm_inspect_limit: int
    triage_profile_limit: int
    metadata_top_limit: int
    min_duration_sec: float | None
    max_duration_sec: float | None
    order: str
    include_failed: bool
    include_running: bool
    user: str | None
    pool: str | None
    query_type: str | None
    max_profile_bytes: int
    collect_cm_events: bool
    cm_events_max_events: int
    collect_cm_timeseries: bool
    cm_metrics_profile: str
    cm_timeseries_top_limit: int
    cm_timeseries_padding_sec: int
    max_timeseries_bytes: int
    max_timeseries_points: int
    metadata_mode: str
    metadata_coordinator: str | None
    metadata_auth: str
    metadata_protocol: str
    metadata_kerberos_service_name: str | None
    metadata_ssl: bool
    metadata_ca_cert: str | None
    metadata_timeout_sec: int
    metadata_max_tables: int | None
    metadata_max_output_bytes: int | None
    metadata_redact: bool
    top_reports: int
    cm_jobs: int
    jobs: int
    metadata_jobs: int
    allow_high_jobs: bool
    discover_only: bool
    overwrite: bool
    config_path: str | None
    progress_jsonl: Path | None
    krb5ccname: str | None
    from_time: str | None = None
    to_time: str | None = None
    only_running: bool = False
    query_profile_source: str = "cm"
    impala_profile_hosts: tuple[str, ...] = ()
    impala_profile_port: int = 25000
    impala_profile_scheme: str = "http"
    impala_profile_timeout_sec: int = 15
    impala_query_list_max_bytes: int = 5 * 1024 * 1024
    impala_profile_prefer_json: bool = False
    impala_profile_collect_docs: bool = False
    impala_collect_admission_context: bool = False
    collect_prometheus_timeseries: bool = False
    prometheus_url: str | None = None
    prometheus_metrics_profile: str = "ambari-hadoop"
    prometheus_step_sec: int = 30
    prometheus_timeseries_padding_sec: int = 120
    prometheus_timeout_sec: int = 30
    collect_workload_history: bool = False
    workload_history_path: Path | None = None
    workload_history_max_bytes: int = 5 * 1024 * 1024
    recent_history_backend: str = "disabled"
    recent_history_db: Path | None = None
    recent_history_postgres_dsn_env: str = "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN"
    recent_history_collector_summary_json: Path | None = None
    recent_history_summary_retention_days: int | None = None
    recent_history_profile_job_retention_days: int | None = None
    recent_history_analysis_cache_retention_days: int | None = None
    recent_history_profile_artifact_retention_days: int | None = None
    privacy_mode: bool = True
    redact_identifiers: bool = True
    redact_hosts: bool = True
    source_visibility: str = "safe"
    source_owner_user: str | None = None
    collectable_owner_users: tuple[str, ...] = ()
    metadata_kerberos_host_fqdn: str | None = None
    analyzed_profile_reuse_roots: tuple[Path, ...] = ()
    metadata_source: str = "impala"
    metadata_hms_postgres_dsn_env: str = "QUERY_DOCTOR_HMS_POSTGRES_DSN"


@dataclass
class DiscoveryResult:
    candidates: list[cm_profiles.RecentQueryCandidate]
    warnings: list[str]
    duration_filter_mode: str
    server_filter_expression: str | None
    summaries_inspected: int | None = None
    scan_too_broad: bool = False
    raw_summary_scan_cap_hit: bool = False
    time_sharded: bool = False
    time_shard_count: int = 0
    time_shard_minutes: int | None = None
    time_shard_min_minutes: int | None = None
    time_shard_scan_limit_warning_count: int = 0
    query_log_at_capacity: bool = False
    query_log_oldest_completed_at_iso: str | None = None


@dataclass
class CaseResult:
    index: int
    query_id: str
    duration_sec: float | None
    user: str | None
    pool: str | None
    query_type: str | None
    sql_verb: str | None
    wrapper_dir: Path
    metadata_source_tables: tuple[str, ...] = ()
    actual_case_dir: Path | None = None
    collection_status: str = "not_started"
    analysis_status: str = "not_started"
    metadata_status: str = "not_observed"
    table_stats_status: str = "not_checked"
    referenced_table_count: int = 0
    collectable_metadata_table_count: int = 0
    collected_metadata_table_count: int = 0
    skipped_due_to_max_table_limit: int = 0
    too_large_count: int = 0
    score: int = 0
    score_reasons: list[str] = field(default_factory=list)
    scoring_evidence_source: str = "not_scored"
    scoring_fallback_reason: str | None = None
    query_optimization_candidate: QueryOptimizationCandidateScore | None = None
    optimizer_rewrite_support: OptimizerRewriteSupport | None = None
    query_optimization_rank: int | None = None
    stats_optimization_candidate: StatsOptimizationCandidateScore | None = None
    stats_optimization_rank: int | None = None
    case_primary_bottleneck: dict[str, object] | None = None
    cardinality_anomaly_count: int | None = None
    memory_anomaly_count: int | None = None
    zero_row_estimate_gap_count: int | None = None
    zero_memory_estimate_gap_count: int | None = None
    backend_data_skew: bool | str = "unknown"
    host_tail_candidate_count: int | None = None
    execution_tail_candidate_count: int | None = None
    report_generated: bool = False
    report_validation_status: str = "not_run"
    failure_category: str | None = None
    failure_reason: str | None = None
    candidate_rank: int | None = None
    triage_rank: int | None = None
    metadata_refreshed: bool = False
    cm_collect_seconds: float | None = None
    analysis_seconds: float | None = None
    report_seconds: float | None = None
    profile_reuse_status: str = "not_requested"
