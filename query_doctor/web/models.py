"""Shared web server models and defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from query_doctor.cli.collect_cm_profiles import DEFAULT_CM_METRICS_PROFILE
from query_doctor.impala.hms_metadata import (
    DEFAULT_HMS_POSTGRES_DSN_ENV,
    METADATA_SOURCE_HMS_POSTGRES,
    METADATA_SOURCE_IMPALA,
)
from query_doctor.optimizer.defaults import DEFAULT_OPTIMIZER_MODEL
from query_doctor.report.llm_client import (
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_URL,
)
from query_doctor.prometheus.timeseries import (
    DEFAULT_PROMETHEUS_METRICS_PROFILE,
    DEFAULT_PROMETHEUS_STEP_SEC,
    DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
)
from query_doctor.trino.support_mode import TRINO_SUPPORT_MODE_OFF, TrinoSupportMode
from query_doctor.web.viewer_identity import (
    ViewerIdentity,
    unauthenticated_viewer_identity,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT_SEC = 1800
DEFAULT_MODEL = "qwen3-coder:30b-a3b-q8_0"
DEFAULT_CORPUS_DIR = Path("cases/cm-corpus")
DEFAULT_RECENT_SCAN_TIMEZONE = "UTC"
DEFAULT_QUERY_PROFILE_SOURCE = "cm"
DEFAULT_IMPALA_PROFILE_PORT = 25000
DEFAULT_IMPALA_PROFILE_SCHEME = "http"
DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC = 15
DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_METADATA_AUTH = "kerberos"
DEFAULT_METADATA_PROTOCOL = "hs2"
DEFAULT_METADATA_TIMEOUT_SEC = 30
DEFAULT_LANGUAGE = "en"
BATCH_CM_INSPECT_LIMIT_MAX = 5000
WEB_BATCH_METADATA_TOP_LIMIT_DEFAULT = 70
WEB_CM_TIMESERIES_TOP_LIMIT_DEFAULT = 10
WEB_CM_EVENTS_MAX_EVENTS_DEFAULT = 50

_REPO_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_WEB_BATCH_ROOT = Path("/tmp")


def batch_output_dir(job_id: str, root: Path | None = None) -> Path:
    return (root or DEFAULT_WEB_BATCH_ROOT) / f"query-doctor-web-batch-{job_id}"


def batch_reuse_root(settings: object | None = None) -> Path:
    configured = getattr(settings, "recent_batch_root", None)
    return configured if isinstance(configured, Path) else DEFAULT_WEB_BATCH_ROOT


def batch_progress_path(job_id: str, root: Path | None = None) -> Path:
    return batch_output_dir(job_id, root=root) / "progress.jsonl"


class WebError(RuntimeError):
    """User-facing web error that must not contain secrets or raw profiles."""

    def __init__(
        self,
        message: object,
        *,
        title: str | None = None,
        reason_code: str | None = None,
        stage: str | None = None,
        next_step: str | None = None,
        details: tuple[object, ...] | list[object] | None = None,
    ) -> None:
        super().__init__(message)
        self.title = title
        self.reason_code = reason_code
        self.stage = stage
        self.next_step = next_step
        self.details = tuple(details or ())


@dataclass(frozen=True)
class WebClusterConfig:
    key: str
    label: str
    cm_url: str | None = None
    cm_cluster: str | None = None
    cm_service: str | None = None
    cm_username: str | None = None
    ca_bundle: str | None = None
    insecure_skip_verify: bool = False
    manual_profile_dir: Path | None = None
    cm_metrics_profile: str = DEFAULT_CM_METRICS_PROFILE
    query_profile_source: str = DEFAULT_QUERY_PROFILE_SOURCE
    impala_profile_hosts: tuple[str, ...] = ()
    impala_profile_port: int = DEFAULT_IMPALA_PROFILE_PORT
    impala_profile_scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME
    impala_profile_timeout_sec: int = DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC
    impala_query_list_max_bytes: int = DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES
    impala_profile_prefer_json: bool = False
    impala_profile_collect_docs: bool = False
    impala_collect_admission_context: bool = False
    collect_prometheus_timeseries: bool = False
    prometheus_url: str | None = None
    prometheus_metrics_profile: str = DEFAULT_PROMETHEUS_METRICS_PROFILE
    prometheus_step_sec: int = DEFAULT_PROMETHEUS_STEP_SEC
    prometheus_timeseries_padding_sec: int = DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC
    metadata_source: str = METADATA_SOURCE_IMPALA
    metadata_hms_postgres_dsn_env: str = DEFAULT_HMS_POSTGRES_DSN_ENV
    metadata_coordinator: str | None = None
    metadata_auth: str = DEFAULT_METADATA_AUTH
    metadata_protocol: str = DEFAULT_METADATA_PROTOCOL
    metadata_kerberos_service_name: str | None = None
    metadata_kerberos_host_fqdn: str | None = None
    metadata_ssl: bool = False
    metadata_ca_cert: str | None = None
    metadata_timeout_sec: int = DEFAULT_METADATA_TIMEOUT_SEC
    metadata_max_tables: int | None = None
    metadata_max_output_bytes: int | None = None
    metadata_redact: bool = True
    privacy_mode: bool = True
    redact_identifiers: bool = True
    redact_hosts: bool = True
    source_visibility: str = "safe"
    source_owner_user: str | None = None
    krb5ccname: str | None = None
    recent_scan_timezone: str = DEFAULT_RECENT_SCAN_TIMEZONE
    trino_support_mode: TrinoSupportMode = TRINO_SUPPORT_MODE_OFF
    trino_beta_enabled: bool = False
    trino_coordinator_url: str | None = None
    trino_query_info_source_contract: Path | None = None
    trino_query_list_source_contract: Path | None = None
    trino_auth_header_file: Path | None = None
    trino_kerberos_principal: str | None = None
    trino_kerberos_service_name: str = "HTTP"
    trino_krb5_ccname: str | None = None
    trino_krb5_config: Path | None = None
    trino_kerberos_ca_cert: Path | None = None
    trino_kerberos_insecure_tls: bool = False


@dataclass(frozen=True)
class WebSettings:
    config: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allow_nonlocal_web_bind: bool = False
    cm_url: str | None = None
    cm_cluster: str | None = None
    cm_service: str | None = None
    cm_username: str | None = None
    ca_bundle: str | None = None
    insecure_skip_verify: bool = False
    manual_profile_dir: Path | None = None
    cm_metrics_profile: str = DEFAULT_CM_METRICS_PROFILE
    clusters: tuple[WebClusterConfig, ...] = ()
    active_cluster_key: str | None = None
    max_profile_bytes: int | None = None
    model: str = DEFAULT_MODEL
    optimizer_model: str | None = DEFAULT_OPTIMIZER_MODEL
    report_llm_provider: str = DEFAULT_LLM_PROVIDER
    report_llm_base_url: str | None = DEFAULT_OLLAMA_URL
    report_llm_chat_path: str | None = None
    optimizer_llm_provider: str = DEFAULT_LLM_PROVIDER
    optimizer_llm_base_url: str | None = DEFAULT_OLLAMA_URL
    optimizer_llm_chat_path: str | None = None
    no_llm: bool = False
    privacy_mode: bool = True
    redact_identifiers: bool = True
    redact_hosts: bool = True
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    repo_dir: Path = _REPO_ROOT
    corpus_dir: Path = DEFAULT_CORPUS_DIR
    recent_batch_root: Path | None = None
    batch_summary: Path | None = None
    corpus_summary: dict[str, object] | None = None
    corpus_summary_root: Path | None = None
    public_demo: bool = False
    query_profile_source: str = DEFAULT_QUERY_PROFILE_SOURCE
    impala_profile_hosts: tuple[str, ...] = ()
    impala_profile_port: int = DEFAULT_IMPALA_PROFILE_PORT
    impala_profile_scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME
    impala_profile_timeout_sec: int = DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC
    impala_query_list_max_bytes: int = DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES
    impala_profile_prefer_json: bool = False
    impala_profile_collect_docs: bool = False
    impala_collect_admission_context: bool = False
    collect_prometheus_timeseries: bool = False
    prometheus_url: str | None = None
    prometheus_metrics_profile: str = DEFAULT_PROMETHEUS_METRICS_PROFILE
    prometheus_step_sec: int = DEFAULT_PROMETHEUS_STEP_SEC
    prometheus_timeseries_padding_sec: int = DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC
    metadata_source: str = METADATA_SOURCE_IMPALA
    metadata_hms_postgres_dsn_env: str = DEFAULT_HMS_POSTGRES_DSN_ENV
    metadata_coordinator: str | None = None
    metadata_auth: str = DEFAULT_METADATA_AUTH
    metadata_protocol: str = DEFAULT_METADATA_PROTOCOL
    metadata_kerberos_service_name: str | None = None
    metadata_kerberos_host_fqdn: str | None = None
    metadata_ssl: bool = False
    metadata_ca_cert: str | None = None
    metadata_timeout_sec: int = DEFAULT_METADATA_TIMEOUT_SEC
    metadata_max_tables: int | None = None
    metadata_max_output_bytes: int | None = None
    metadata_redact: bool = True
    source_visibility: str = "safe"
    source_owner_user: str | None = None
    krb5ccname: str | None = None
    recent_scan_timezone: str = DEFAULT_RECENT_SCAN_TIMEZONE
    language: str = DEFAULT_LANGUAGE
    source_owner_user_options: tuple[str, ...] = ()
    viewer_identity_header: str | None = None
    owner_raw_source_enabled: bool = True
    viewer_identity: ViewerIdentity = field(default_factory=unauthenticated_viewer_identity)
    selected_engine: str = "impala"
    trino_support_mode: TrinoSupportMode = TRINO_SUPPORT_MODE_OFF
    trino_beta_enabled: bool = False
    trino_coordinator_url: str | None = None
    trino_query_info_source_contract: Path | None = None
    trino_query_list_source_contract: Path | None = None
    trino_auth_header_file: Path | None = None
    trino_kerberos_principal: str | None = None
    trino_kerberos_service_name: str = "HTTP"
    trino_krb5_ccname: str | None = None
    trino_krb5_config: Path | None = None
    trino_kerberos_ca_cert: Path | None = None
    trino_kerberos_insecure_tls: bool = False


@dataclass(frozen=True)
class WebResult:
    query_id: str
    case_dir: Path
    case_source: str
    report_mode: str
    parsed_operators: str
    cardinality_anomalies: str
    memory_anomalies: str
    report_text: str
    report_retry: bool = False


@dataclass(frozen=True)
class WebQueryAnalysisResult:
    query_id: str
    case: dict[str, object]


@dataclass(frozen=True)
class WebTrinoCaseArtifacts:
    case_id: str
    case_dir: Path
    boundary_path: Path
    compact_diagnosis_path: Path
    metadata_summary_path: Path
    analysis_path: Path
    analysis_facts_path: Path


@dataclass(frozen=True)
class WebTrinoQueryAnalysisResult:
    query_id: str
    diagnosis: dict[str, object]
    support_mode: str = "beta"
    case_artifacts: WebTrinoCaseArtifacts | None = None


@dataclass(frozen=True)
class WebTrinoRecentScanRow:
    query_id: str
    status: str
    lifecycle: str = "unknown"
    parser_coverage: str = "unknown"
    supported_attention_area_count: int = 0
    attention_areas: tuple[str, ...] = ()
    error: str = ""
    error_reason_code: str = ""
    error_next_step: str = ""
    case_artifacts: WebTrinoCaseArtifacts | None = None


@dataclass(frozen=True)
class WebTrinoRecentScanResult:
    rows: tuple[WebTrinoRecentScanRow, ...]
    records_seen: int
    records_selected: int
    records_diagnosed: int
    query_bound: int
    cluster_key: str = ""
    warnings: tuple[str, ...] = ()
    support_mode: str = "beta"


@dataclass(frozen=True)
class BatchRunConfig:
    engine: str = "impala"
    scan_preset: str = "standard"
    recent_window_minutes: int = 30
    scan_date: str = ""
    scan_hour: int = 0
    cluster_key: str = ""
    from_time: str | None = None
    to_time: str | None = None
    cm_inspect_limit: int = BATCH_CM_INSPECT_LIMIT_MAX
    triage_profile_limit: int = BATCH_CM_INSPECT_LIMIT_MAX
    metadata_top_limit: int = WEB_BATCH_METADATA_TOP_LIMIT_DEFAULT
    min_duration_sec: float | None = None
    max_duration_sec: float | None = None
    order: str = "duration-desc"
    parallelism: int = 50
    cm_jobs: int = 50
    jobs: int = 50
    metadata_jobs: int = 5
    user: str = ""
    pool: str = ""
    query_type: str = ""
    include_failed: bool = True
    include_running: bool = False
    only_running: bool = False
    discover_only: bool = False
    collect_cm_events: bool = True
    cm_events_max_events: int = WEB_CM_EVENTS_MAX_EVENTS_DEFAULT
    collect_cm_timeseries: bool = True
    cm_metrics_profile: str = DEFAULT_CM_METRICS_PROFILE
    cm_timeseries_top_limit: int = WEB_CM_TIMESERIES_TOP_LIMIT_DEFAULT
    publish_latest_summary: bool = True


@dataclass(frozen=True)
class WebJobSnapshot:
    job_id: str
    query_id: str
    report_mode: str
    status: str
    stage_label: str
    progress: int
    kind: str = "query"
    result_html: str = ""
    error: str = ""
    error_info: dict[str, object] | None = None
    batch_form_values: dict[str, object] | None = None
    batch_progress_path: Path | None = None
    batch_case_id: str | None = None
    batch_source: str = "batch"
    cancel_requested: bool = False


@dataclass
class WebJob:
    job_id: str
    query_id: str
    report_mode: str
    status: str
    stage_label: str
    progress: int
    kind: str = "query"
    result_html: str = ""
    error: str = ""
    error_info: dict[str, object] | None = None
    batch_form_values: dict[str, object] | None = None
    batch_progress_path: Path | None = None
    batch_case_id: str | None = None
    batch_source: str = "batch"
    cancel_requested: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    def snapshot(self) -> WebJobSnapshot:
        return WebJobSnapshot(
            job_id=self.job_id,
            query_id=self.query_id,
            report_mode=self.report_mode,
            status=self.status,
            stage_label=self.stage_label,
            progress=self.progress,
            kind=self.kind,
            result_html=self.result_html,
            error=self.error,
            error_info=dict(self.error_info) if self.error_info is not None else None,
            batch_form_values=dict(self.batch_form_values)
            if self.batch_form_values is not None
            else None,
            batch_progress_path=self.batch_progress_path,
            batch_case_id=self.batch_case_id,
            batch_source=self.batch_source,
            cancel_requested=self.cancel_requested,
        )


def metadata_collection_configured(settings: Any) -> bool:
    """Whether table metadata can be collected for these web or cluster settings."""
    if getattr(settings, "metadata_source", METADATA_SOURCE_IMPALA) == METADATA_SOURCE_HMS_POSTGRES:
        dsn_env = getattr(settings, "metadata_hms_postgres_dsn_env", DEFAULT_HMS_POSTGRES_DSN_ENV)
        return bool(os.environ.get(dsn_env, "").strip())
    return bool(getattr(settings, "metadata_coordinator", None))
