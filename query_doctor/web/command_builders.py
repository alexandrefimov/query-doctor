"""Command builders for the local web UI workflows."""

from __future__ import annotations

from pathlib import Path

from query_doctor.cli.commands import command_prefix
from query_doctor.impala.hms_metadata import METADATA_SOURCE_HMS_POSTGRES
from query_doctor.web.config import metadata_configured
from query_doctor.web.models import WebError, WebSettings


BATCH_REPORT_NAME = "diagnosis.md"
BATCH_REPORT_PARTIAL_NAME = "diagnosis.partial.md"
BATCH_REPORT_VALIDATION_MARKER = "diagnosis.validated.json"
PYTHON_REPORT_NAME = "diagnosis_python.md"
PYTHON_REPORT_PARTIAL_NAME = "diagnosis_python.partial.md"
PYTHON_REPORT_VALIDATION_MARKER = "diagnosis_python.validated.json"
LLM_REPORT_NAME = "diagnosis_llm.md"
LLM_REPORT_PARTIAL_NAME = "diagnosis_llm.partial.md"
LLM_REPORT_VALIDATION_MARKER = "diagnosis_llm.validated.json"
REPORT_VARIANT_PYTHON = "python"
REPORT_VARIANT_LLM = "llm"
OPTIMIZED_QUERY_NAME = "optimized_query.sql"
OPTIMIZED_QUERY_RECOMMENDATIONS_NAME = "optimized_query_recommendations.md"
OPTIMIZED_QUERY_PARTIAL_NAME = "optimized_query.partial.txt"
OPTIMIZED_QUERY_VALIDATION_MARKER = "optimized_query.validated.json"
WEB_REPORT_VALIDATION_MODE = "strict"
WEB_REPORT_MARKER_SCHEMA_VERSION = 1
OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION = 2
OPTIMIZED_QUERY_VALIDATION_MODE = "strict_v2"


def report_artifacts_for_variant(report_variant: str) -> tuple[str, str, str]:
    if report_variant == REPORT_VARIANT_PYTHON:
        return (
            PYTHON_REPORT_NAME,
            PYTHON_REPORT_PARTIAL_NAME,
            PYTHON_REPORT_VALIDATION_MARKER,
        )
    if report_variant == REPORT_VARIANT_LLM:
        return (LLM_REPORT_NAME, LLM_REPORT_PARTIAL_NAME, LLM_REPORT_VALIDATION_MARKER)
    raise WebError(
        "Unknown report variant.",
        title="Report variant was rejected",
        reason_code="web.report_variant_invalid",
        stage="Preparing report generation",
        next_step="Choose a supported report variant and retry.",
    )


def display_float(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def append_web_metadata_args(cmd: list[str], settings: WebSettings) -> None:
    if settings.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
        cmd.extend(["--metadata-source", settings.metadata_source])
        cmd.extend(["--metadata-hms-postgres-dsn-env", settings.metadata_hms_postgres_dsn_env])
        cmd.extend(["--metadata-timeout-sec", str(settings.metadata_timeout_sec)])
        append_web_metadata_bounds_and_redaction_args(cmd, settings)
        return
    if settings.metadata_coordinator:
        cmd.extend(["--metadata-coordinator", settings.metadata_coordinator])
    cmd.extend(["--metadata-auth", settings.metadata_auth])
    cmd.extend(["--metadata-protocol", settings.metadata_protocol])
    cmd.extend(["--metadata-timeout-sec", str(settings.metadata_timeout_sec)])
    if settings.metadata_kerberos_service_name:
        cmd.extend(["--metadata-kerberos-service-name", settings.metadata_kerberos_service_name])
    if settings.metadata_kerberos_host_fqdn:
        cmd.extend(["--metadata-kerberos-host-fqdn", settings.metadata_kerberos_host_fqdn])
    if settings.metadata_ssl:
        cmd.append("--metadata-ssl")
    if settings.metadata_ca_cert:
        cmd.extend(["--metadata-ca-cert", settings.metadata_ca_cert])
    append_web_metadata_bounds_and_redaction_args(cmd, settings)


def append_web_metadata_bounds_and_redaction_args(cmd: list[str], settings: WebSettings) -> None:
    if settings.metadata_max_tables is not None:
        cmd.extend(["--metadata-max-tables", str(settings.metadata_max_tables)])
    if settings.metadata_max_output_bytes is not None:
        cmd.extend(["--metadata-max-output-bytes", str(settings.metadata_max_output_bytes)])
    if settings.metadata_redact:
        cmd.append("--metadata-redact")
    if settings.redact_identifiers:
        cmd.append("--metadata-redact-identifiers")
    else:
        cmd.append("--metadata-no-redact-identifiers")
    if settings.redact_hosts:
        cmd.append("--metadata-redact-hosts")
    else:
        cmd.append("--metadata-no-redact-hosts")


def append_web_cm_args(cmd: list[str], settings: WebSettings) -> None:
    if not any((settings.cm_url, settings.cm_cluster, settings.cm_service, settings.ca_bundle)):
        return
    missing: list[str] = []
    if not settings.cm_url:
        missing.append("cm_url")
    if not settings.cm_cluster:
        missing.append("cluster")
    if not settings.cm_service:
        missing.append("service")
    if missing:
        raise WebError(
            "Selected cluster is missing CM setting(s): " + ", ".join(missing) + ".",
            title="Selected source is missing CM settings",
            reason_code="impala.cm_settings_missing",
            stage="Preparing CM request",
            next_step="Add the missing CM settings to the selected source or choose another source.",
        )
    cmd.extend(
        [
            "--cm-url",
            settings.cm_url,
            "--cluster",
            settings.cm_cluster,
            "--service",
            settings.cm_service,
        ]
    )
    if settings.ca_bundle:
        cmd.extend(["--ca-bundle", settings.ca_bundle])
    if settings.insecure_skip_verify:
        cmd.append("--insecure-skip-verify")


def append_web_impala_profile_args(cmd: list[str], settings: WebSettings) -> None:
    cmd.extend(
        [
            "--query-profile-source",
            "impala",
            "--impala-profile-port",
            str(settings.impala_profile_port),
            "--impala-profile-scheme",
            settings.impala_profile_scheme,
            "--impala-profile-timeout-sec",
            str(settings.impala_profile_timeout_sec),
            "--impala-query-list-max-bytes",
            str(settings.impala_query_list_max_bytes),
        ]
    )
    for host in settings.impala_profile_hosts:
        cmd.extend(["--impala-profile-host", host])
    if settings.impala_profile_prefer_json:
        cmd.append("--impala-profile-prefer-json")
    if settings.impala_profile_collect_docs:
        cmd.append("--impala-profile-collect-docs")
    if settings.impala_collect_admission_context:
        cmd.append("--impala-collect-admission-context")
    if settings.collect_prometheus_timeseries or settings.prometheus_url:
        if not settings.prometheus_url:
            raise WebError(
                "Prometheus runtime metrics are enabled but prometheus_url is not configured.",
                title="Prometheus metrics source is not configured",
                reason_code="impala.prometheus_url_missing",
                stage="Preparing direct Impala request",
                next_step="Configure prometheus_url for this source or disable Prometheus metrics.",
            )
        cmd.extend(
            [
                "--prometheus-url",
                settings.prometheus_url,
                "--collect-prometheus-timeseries",
                "--prometheus-metrics-profile",
                settings.prometheus_metrics_profile,
                "--prometheus-step-sec",
                str(settings.prometheus_step_sec),
                "--prometheus-timeseries-padding-sec",
                str(settings.prometheus_timeseries_padding_sec),
            ]
        )


def build_analyzer_command(case_dir: Path, settings: WebSettings) -> list[str]:
    return command_prefix(settings.repo_dir, "pipeline") + [
        str(case_dir),
        "--skip-report",
    ]


def build_query_id_analyzer_command(case_dir: Path, settings: WebSettings) -> list[str]:
    metadata_enabled = metadata_configured(settings)
    cmd = command_prefix(settings.repo_dir, "pipeline") + [
        str(case_dir),
        "--stop-after-analysis",
        "--metadata-failure-policy",
        "continue",
        "--metadata-mode",
        "on" if metadata_enabled else "off",
    ]
    if metadata_enabled:
        append_web_metadata_args(cmd, settings)
    return cmd


def build_report_command(
    case_dir: Path,
    report_mode: str,
    report_name: str,
    settings: WebSettings,
    *,
    no_llm: bool | None = None,
) -> list[str]:
    cmd = command_prefix(settings.repo_dir, "report") + [
        str(case_dir),
        "--model",
        settings.model,
        "--llm-provider",
        settings.report_llm_provider,
        "--mode",
        report_mode,
        "--language",
        settings.language,
        "--out",
        report_name,
        "--keep-alive",
        "0",
        "--validation-mode",
        WEB_REPORT_VALIDATION_MODE,
    ]
    if settings.report_llm_base_url:
        cmd.extend(["--llm-base-url", settings.report_llm_base_url])
    if settings.report_llm_chat_path:
        cmd.extend(["--llm-chat-path", settings.report_llm_chat_path])
    use_no_llm = settings.no_llm if no_llm is None else no_llm
    if use_no_llm:
        cmd.append("--no-llm")
    return cmd


def build_selected_case_report_command(
    case_dir: Path,
    settings: WebSettings,
    *,
    report_variant: str = REPORT_VARIANT_PYTHON,
) -> list[str]:
    report_name, _partial_name, _marker_name = report_artifacts_for_variant(report_variant)
    if report_variant == REPORT_VARIANT_LLM and settings.no_llm:
        raise WebError(
            "LLM report generation is disabled for this web session.",
            title="LLM report generation is disabled",
            reason_code="web.llm_report_disabled",
            stage="Preparing report generation",
            next_step="Use the deterministic Python report or restart the web session with LLM reports enabled.",
        )
    return build_report_command(
        case_dir,
        "admin",
        report_name,
        settings,
        no_llm=report_variant == REPORT_VARIANT_PYTHON,
    )


def build_batch_case_report_command(case_dir: Path, settings: WebSettings) -> list[str]:
    cmd = command_prefix(settings.repo_dir, "pipeline") + [
        str(case_dir),
        "--mode",
        "admin",
        "--language",
        settings.language,
        "--model",
        settings.model,
        "--llm-provider",
        settings.report_llm_provider,
        "--out",
        BATCH_REPORT_NAME,
        "--metadata-mode",
        "off",
        "--keep-alive",
        "0",
        "--report-validation-mode",
        WEB_REPORT_VALIDATION_MODE,
    ]
    if settings.report_llm_base_url:
        cmd.extend(["--llm-base-url", settings.report_llm_base_url])
    if settings.report_llm_chat_path:
        cmd.extend(["--llm-chat-path", settings.report_llm_chat_path])
    if settings.no_llm:
        cmd.append("--no-llm")
    return cmd


def build_optimized_query_command(case_dir: Path, settings: WebSettings) -> list[str]:
    cmd = command_prefix(settings.repo_dir, "optimize_query") + [
        str(case_dir),
        "--model",
        optimizer_model_for_settings(settings),
        "--llm-provider",
        settings.optimizer_llm_provider,
        "--out",
        OPTIMIZED_QUERY_NAME,
        "--source-visibility",
        settings.source_visibility,
        "--keep-alive",
        "0",
    ]
    if settings.optimizer_llm_base_url:
        cmd.extend(["--llm-base-url", settings.optimizer_llm_base_url])
    if settings.optimizer_llm_chat_path:
        cmd.extend(["--llm-chat-path", settings.optimizer_llm_chat_path])
    if settings.no_llm:
        cmd.append("--no-llm")
    return cmd


def optimizer_model_for_settings(settings: WebSettings) -> str:
    return settings.optimizer_model or settings.model
