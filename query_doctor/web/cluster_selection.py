"""Cluster selection helpers for the local web UI."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from query_doctor.cm.metrics_catalog import DEFAULT_CM_METRICS_PROFILE, normalize_cm_metrics_profile
from query_doctor.impala.hms_metadata import DEFAULT_HMS_POSTGRES_DSN_ENV, METADATA_SOURCE_IMPALA
from query_doctor.source_visibility import (
    SOURCE_VISIBILITY_SAFE,
    collectable_owner_users,
    normalize_source_owner_user,
    normalize_source_visibility,
    source_owner_user_from_env,
)
from query_doctor.trino.support_mode import (
    TRINO_SUPPORT_MODE_BETA,
    normalize_trino_support_mode,
    trino_support_mode_enabled,
)
from query_doctor.web.models import (
    DEFAULT_IMPALA_PROFILE_PORT,
    DEFAULT_IMPALA_PROFILE_SCHEME,
    DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES,
    DEFAULT_METADATA_AUTH,
    DEFAULT_METADATA_PROTOCOL,
    DEFAULT_METADATA_TIMEOUT_SEC,
    DEFAULT_PROMETHEUS_METRICS_PROFILE,
    DEFAULT_PROMETHEUS_STEP_SEC,
    DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
    DEFAULT_QUERY_PROFILE_SOURCE,
    DEFAULT_RECENT_SCAN_TIMEZONE,
    WebClusterConfig,
    WebError,
    WebSettings,
)
from query_doctor.web.viewer_identity import (
    VIEWER_IDENTITY_LOCAL_FIRST,
    local_first_viewer_identity,
)


def build_web_cluster_configs(config_values: Mapping[str, object]) -> tuple[WebClusterConfig, ...]:
    clusters_value = config_values.get("clusters")
    if isinstance(clusters_value, list):
        clusters: list[WebClusterConfig] = []
        for cluster in clusters_value:
            if not isinstance(cluster, Mapping):
                raise WebError(
                    "Invalid clusters local config.",
                    title="Cluster config is invalid",
                    reason_code="web.cluster_config_invalid",
                    stage="Loading local web config",
                    next_step="Fix clusters[] so every entry is an object, then restart the web UI.",
                )
            clusters.append(build_web_cluster_config(cluster, defaults=config_values))
        return tuple(clusters)
    if has_top_level_cluster_config(config_values):
        return (
            build_web_cluster_config(
                {
                    "id": "default",
                    "label": "Configured cluster",
                },
                defaults=config_values,
            ),
        )
    return ()


def has_top_level_cluster_config(config_values: Mapping[str, object]) -> bool:
    return any(
        config_values.get(key)
        for key in (
            "cm_url",
            "cluster",
            "service",
            "manual_profile_dir",
            "query_profile_source",
            "impala_profile_hosts",
            "trino_support_mode",
            "trino_beta_enabled",
            "trino_coordinator_url",
            "trino_query_info_source_contract",
            "trino_query_list_source_contract",
            "trino_auth_header_file",
            "trino_kerberos_ca_cert",
            "trino_kerberos_insecure_tls",
            "trino_kerberos_principal",
            "trino_kerberos_service_name",
            "trino_krb5_ccname",
            "trino_krb5_config",
        )
    )


def build_web_cluster_config(
    values: Mapping[str, object],
    *,
    defaults: Mapping[str, object],
) -> WebClusterConfig:
    key = string_value(values, "id") or "default"
    label = string_value(values, "label") or key
    cm_metrics_profile = normalize_cluster_cm_metrics_profile(
        first_string(
            string_value(values, "cm_metrics_profile"),
            string_value(defaults, "cm_metrics_profile"),
            DEFAULT_CM_METRICS_PROFILE,
        )
    )
    privacy_mode = first_bool(values, defaults, "privacy_mode", default=True)
    source_visibility = normalize_source_visibility(
        first_string(
            string_value(values, "source_visibility"),
            string_value(defaults, "source_visibility"),
            SOURCE_VISIBILITY_SAFE,
        )
    )
    source_owner_user = normalize_source_owner_user(
        first_string(
            string_value(values, "source_owner_user"),
            string_value(defaults, "source_owner_user"),
            source_owner_user_from_env(dict(os.environ)),
        )
    )
    legacy_trino_beta_enabled = first_bool(values, defaults, "trino_beta_enabled", default=False)
    trino_support_mode = normalize_trino_support_mode(
        first_string(
            string_value(values, "trino_support_mode"),
            string_value(defaults, "trino_support_mode"),
        ),
        legacy_beta_enabled=legacy_trino_beta_enabled,
    )
    return WebClusterConfig(
        key=key,
        label=label,
        cm_url=first_string(string_value(values, "cm_url"), string_value(defaults, "cm_url")),
        cm_cluster=first_string(string_value(values, "cluster"), string_value(defaults, "cluster")),
        cm_service=first_string(string_value(values, "service"), string_value(defaults, "service")),
        cm_username=first_string(
            string_value(values, "username"), string_value(defaults, "username")
        ),
        ca_bundle=first_string(
            string_value(values, "ca_bundle"), string_value(defaults, "ca_bundle")
        ),
        insecure_skip_verify=first_bool(values, defaults, "insecure_skip_verify", default=False),
        manual_profile_dir=first_path(values, defaults, "manual_profile_dir"),
        cm_metrics_profile=cm_metrics_profile,
        query_profile_source=first_string(
            string_value(values, "query_profile_source"),
            string_value(defaults, "query_profile_source"),
            DEFAULT_QUERY_PROFILE_SOURCE,
        )
        or DEFAULT_QUERY_PROFILE_SOURCE,
        impala_profile_hosts=first_string_tuple(values, defaults, "impala_profile_hosts"),
        impala_profile_port=first_int(
            values,
            defaults,
            "impala_profile_port",
            default=DEFAULT_IMPALA_PROFILE_PORT,
        ),
        impala_profile_scheme=first_string(
            string_value(values, "impala_profile_scheme"),
            string_value(defaults, "impala_profile_scheme"),
            DEFAULT_IMPALA_PROFILE_SCHEME,
        )
        or DEFAULT_IMPALA_PROFILE_SCHEME,
        impala_profile_timeout_sec=first_int(
            values,
            defaults,
            "impala_profile_timeout_sec",
            default=DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
        ),
        impala_query_list_max_bytes=first_int(
            values,
            defaults,
            "impala_query_list_max_bytes",
            default=DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES,
        ),
        impala_profile_prefer_json=first_bool(
            values, defaults, "impala_profile_prefer_json", default=False
        ),
        impala_profile_collect_docs=first_bool(
            values, defaults, "impala_profile_collect_docs", default=False
        ),
        impala_collect_admission_context=first_bool(
            values, defaults, "impala_collect_admission_context", default=False
        ),
        collect_prometheus_timeseries=first_bool(
            values, defaults, "collect_prometheus_timeseries", default=False
        ),
        prometheus_url=first_string(
            string_value(values, "prometheus_url"),
            string_value(defaults, "prometheus_url"),
        ),
        prometheus_metrics_profile=first_string(
            string_value(values, "prometheus_metrics_profile"),
            string_value(defaults, "prometheus_metrics_profile"),
            DEFAULT_PROMETHEUS_METRICS_PROFILE,
        )
        or DEFAULT_PROMETHEUS_METRICS_PROFILE,
        prometheus_step_sec=first_int(
            values,
            defaults,
            "prometheus_step_sec",
            default=DEFAULT_PROMETHEUS_STEP_SEC,
        ),
        prometheus_timeseries_padding_sec=first_int(
            values,
            defaults,
            "prometheus_timeseries_padding_sec",
            default=DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
        ),
        metadata_source=first_string(
            string_value(values, "metadata_source"),
            string_value(defaults, "metadata_source"),
            METADATA_SOURCE_IMPALA,
        )
        or METADATA_SOURCE_IMPALA,
        metadata_hms_postgres_dsn_env=first_string(
            string_value(values, "metadata_hms_postgres_dsn_env"),
            string_value(defaults, "metadata_hms_postgres_dsn_env"),
            DEFAULT_HMS_POSTGRES_DSN_ENV,
        )
        or DEFAULT_HMS_POSTGRES_DSN_ENV,
        metadata_coordinator=first_string(
            string_value(values, "metadata_coordinator"),
            string_value(defaults, "metadata_coordinator"),
        ),
        metadata_auth=first_string(
            string_value(values, "metadata_auth"),
            string_value(defaults, "metadata_auth"),
            DEFAULT_METADATA_AUTH,
        )
        or DEFAULT_METADATA_AUTH,
        metadata_protocol=first_string(
            string_value(values, "metadata_protocol"),
            string_value(defaults, "metadata_protocol"),
            DEFAULT_METADATA_PROTOCOL,
        )
        or DEFAULT_METADATA_PROTOCOL,
        metadata_kerberos_service_name=first_string(
            string_value(values, "metadata_kerberos_service_name"),
            string_value(values, "impala_kerberos_service_name"),
            string_value(defaults, "metadata_kerberos_service_name"),
            string_value(defaults, "impala_kerberos_service_name"),
        ),
        metadata_kerberos_host_fqdn=first_string(
            string_value(values, "metadata_kerberos_host_fqdn"),
            string_value(defaults, "metadata_kerberos_host_fqdn"),
        ),
        metadata_ssl=first_bool(values, defaults, "metadata_ssl", default=False),
        metadata_ca_cert=first_string(
            string_value(values, "metadata_ca_cert"),
            string_value(defaults, "metadata_ca_cert"),
        ),
        metadata_timeout_sec=first_int(
            values,
            defaults,
            "metadata_timeout_sec",
            default=DEFAULT_METADATA_TIMEOUT_SEC,
        ),
        metadata_max_tables=optional_int(values, defaults, "metadata_max_tables"),
        metadata_max_output_bytes=optional_int(values, defaults, "metadata_max_output_bytes"),
        metadata_redact=first_bool(values, defaults, "metadata_redact", default=privacy_mode),
        privacy_mode=privacy_mode,
        redact_identifiers=first_bool(values, defaults, "redact_identifiers", default=privacy_mode),
        redact_hosts=first_bool(values, defaults, "redact_hosts", default=privacy_mode),
        source_visibility=source_visibility,
        source_owner_user=source_owner_user,
        krb5ccname=first_string(
            string_value(values, "krb5ccname"),
            string_value(defaults, "krb5ccname"),
        ),
        recent_scan_timezone=first_string(
            string_value(values, "recent_scan_timezone"),
            string_value(defaults, "recent_scan_timezone"),
            DEFAULT_RECENT_SCAN_TIMEZONE,
        )
        or DEFAULT_RECENT_SCAN_TIMEZONE,
        trino_support_mode=trino_support_mode,
        trino_beta_enabled=(
            legacy_trino_beta_enabled or trino_support_mode == TRINO_SUPPORT_MODE_BETA
        ),
        trino_coordinator_url=first_string(
            string_value(values, "trino_coordinator_url"),
            string_value(defaults, "trino_coordinator_url"),
        ),
        trino_query_info_source_contract=first_path(
            values, defaults, "trino_query_info_source_contract"
        ),
        trino_query_list_source_contract=first_path(
            values, defaults, "trino_query_list_source_contract"
        ),
        trino_auth_header_file=first_path(values, defaults, "trino_auth_header_file"),
        trino_kerberos_principal=first_string(
            string_value(values, "trino_kerberos_principal"),
            string_value(defaults, "trino_kerberos_principal"),
        ),
        trino_kerberos_service_name=first_string(
            string_value(values, "trino_kerberos_service_name"),
            string_value(defaults, "trino_kerberos_service_name"),
            "HTTP",
        )
        or "HTTP",
        trino_krb5_ccname=first_string(
            string_value(values, "trino_krb5_ccname"),
            string_value(defaults, "trino_krb5_ccname"),
        ),
        trino_krb5_config=first_path(values, defaults, "trino_krb5_config"),
        trino_kerberos_ca_cert=first_path(values, defaults, "trino_kerberos_ca_cert"),
        trino_kerberos_insecure_tls=first_bool(
            values,
            defaults,
            "trino_kerberos_insecure_tls",
            default=False,
        ),
    )


def normalize_cluster_cm_metrics_profile(value: str | None) -> str:
    try:
        return normalize_cm_metrics_profile(value or DEFAULT_CM_METRICS_PROFILE)
    except ValueError as exc:
        raise WebError(
            "Invalid cm_metrics_profile in local cluster config.",
            title="CM metrics profile is invalid",
            reason_code="web.cm_metrics_profile_invalid",
            stage="Loading local web config",
            next_step="Use a supported cm_metrics_profile value in local config, then restart the web UI.",
        ) from exc


def string_value(values: Mapping[str, object], key: str) -> str | None:
    value = values.get(key)
    return value if isinstance(value, str) and value else None


def int_value(values: Mapping[str, object], key: str) -> int | None:
    value = values.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def first_string(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def first_bool(
    values: Mapping[str, object],
    defaults: Mapping[str, object],
    key: str,
    *,
    default: bool,
) -> bool:
    for source in (values, defaults):
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return default


def first_int(
    values: Mapping[str, object],
    defaults: Mapping[str, object],
    key: str,
    *,
    default: int,
) -> int:
    for source in (values, defaults):
        value = int_value(source, key)
        if value is not None:
            return value
    return default


def optional_int(
    values: Mapping[str, object],
    defaults: Mapping[str, object],
    key: str,
) -> int | None:
    for source in (values, defaults):
        value = int_value(source, key)
        if value is not None:
            return value
    return None


def first_string_tuple(
    values: Mapping[str, object],
    defaults: Mapping[str, object],
    key: str,
) -> tuple[str, ...]:
    for source in (values, defaults):
        value = source.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return tuple(item for item in value if item)
    return ()


def first_path(
    values: Mapping[str, object],
    defaults: Mapping[str, object],
    key: str,
) -> Path | None:
    value = first_string(string_value(values, key), string_value(defaults, key))
    return Path(value).expanduser() if value else None


def default_cluster_key(settings: WebSettings) -> str:
    if settings.active_cluster_key and any(
        cluster.key == settings.active_cluster_key for cluster in settings.clusters
    ):
        return settings.active_cluster_key
    if settings.clusters:
        return settings.clusters[0].key
    return ""


def cluster_select_options(settings: WebSettings) -> tuple[tuple[str, str], ...]:
    return tuple((cluster.key, cluster_select_label(cluster)) for cluster in settings.clusters)


def cluster_select_label(cluster: WebClusterConfig) -> str:
    label = cluster.label
    if cluster.trino_support_mode == "production":
        return label
    trino_label = trino_support_mode_label(cluster)
    if cluster_trino_beta_query_ready(cluster) and cluster_trino_beta_recent_ready(cluster):
        return f"{label} - {trino_label} Recent + One Query ID"
    if cluster_trino_beta_recent_ready(cluster):
        return f"{label} - {trino_label} Recent"
    if cluster_trino_beta_query_ready(cluster):
        return f"{label} - {trino_label} One Query ID"
    return label


def trino_support_mode_label(cluster: WebClusterConfig | WebSettings) -> str:
    if cluster.trino_support_mode == "production":
        return "Trino"
    if cluster.trino_support_mode == TRINO_SUPPORT_MODE_BETA or cluster.trino_beta_enabled:
        return "Trino Beta"
    return "Trino"


def cluster_trino_beta_query_ready(cluster: WebClusterConfig) -> bool:
    return bool(
        (trino_support_mode_enabled(cluster.trino_support_mode) or cluster.trino_beta_enabled)
        and cluster.trino_coordinator_url
        and cluster.trino_query_info_source_contract
    )


def cluster_trino_beta_recent_ready(cluster: WebClusterConfig) -> bool:
    return bool(
        cluster_trino_beta_query_ready(cluster) and cluster.trino_query_list_source_contract
    )


def selected_cluster_key_from_mapping(
    values: Mapping[str, object] | None, settings: WebSettings
) -> str:
    if values is None:
        return default_cluster_key(settings)
    value = values.get("cluster_key")
    if isinstance(value, list):
        raw = str(value[0]) if value else ""
    elif value is None:
        raw = ""
    else:
        raw = str(value)
    return raw.strip() or default_cluster_key(settings)


def settings_for_cluster_key(settings: WebSettings, cluster_key: str | None) -> WebSettings:
    key = (cluster_key or "").strip() or default_cluster_key(settings)
    if not key:
        return settings
    for cluster in settings.clusters:
        if cluster.key == key:
            source_owner_user = cluster.source_owner_user or settings.source_owner_user
            viewer_identity = settings.viewer_identity
            if viewer_identity.mode == VIEWER_IDENTITY_LOCAL_FIRST:
                viewer_identity = local_first_viewer_identity(
                    collectable_owner_users(
                        source_owner_user,
                        settings.source_owner_user_options,
                    )
                )
            return replace(
                settings,
                active_cluster_key=cluster.key,
                cm_url=cluster.cm_url,
                cm_cluster=cluster.cm_cluster,
                cm_service=cluster.cm_service,
                cm_username=cluster.cm_username,
                ca_bundle=cluster.ca_bundle,
                insecure_skip_verify=cluster.insecure_skip_verify,
                manual_profile_dir=cluster.manual_profile_dir,
                cm_metrics_profile=cluster.cm_metrics_profile,
                query_profile_source=cluster.query_profile_source,
                impala_profile_hosts=cluster.impala_profile_hosts,
                impala_profile_port=cluster.impala_profile_port,
                impala_profile_scheme=cluster.impala_profile_scheme,
                impala_profile_timeout_sec=cluster.impala_profile_timeout_sec,
                impala_query_list_max_bytes=cluster.impala_query_list_max_bytes,
                impala_profile_prefer_json=cluster.impala_profile_prefer_json,
                impala_profile_collect_docs=cluster.impala_profile_collect_docs,
                impala_collect_admission_context=cluster.impala_collect_admission_context,
                collect_prometheus_timeseries=cluster.collect_prometheus_timeseries,
                prometheus_url=cluster.prometheus_url,
                prometheus_metrics_profile=cluster.prometheus_metrics_profile,
                prometheus_step_sec=cluster.prometheus_step_sec,
                prometheus_timeseries_padding_sec=cluster.prometheus_timeseries_padding_sec,
                metadata_source=cluster.metadata_source,
                metadata_hms_postgres_dsn_env=cluster.metadata_hms_postgres_dsn_env,
                metadata_coordinator=cluster.metadata_coordinator,
                metadata_auth=cluster.metadata_auth,
                metadata_protocol=cluster.metadata_protocol,
                metadata_kerberos_service_name=cluster.metadata_kerberos_service_name,
                metadata_kerberos_host_fqdn=cluster.metadata_kerberos_host_fqdn,
                metadata_ssl=cluster.metadata_ssl,
                metadata_ca_cert=cluster.metadata_ca_cert,
                metadata_timeout_sec=cluster.metadata_timeout_sec,
                metadata_max_tables=cluster.metadata_max_tables,
                metadata_max_output_bytes=cluster.metadata_max_output_bytes,
                metadata_redact=cluster.metadata_redact,
                privacy_mode=cluster.privacy_mode,
                redact_identifiers=cluster.redact_identifiers,
                redact_hosts=cluster.redact_hosts,
                source_visibility=cluster.source_visibility,
                source_owner_user=source_owner_user,
                viewer_identity=viewer_identity,
                krb5ccname=cluster.krb5ccname,
                recent_scan_timezone=cluster.recent_scan_timezone,
                trino_support_mode=cluster.trino_support_mode,
                trino_beta_enabled=cluster.trino_beta_enabled,
                trino_coordinator_url=cluster.trino_coordinator_url,
                trino_query_info_source_contract=cluster.trino_query_info_source_contract,
                trino_query_list_source_contract=cluster.trino_query_list_source_contract,
                trino_auth_header_file=cluster.trino_auth_header_file,
                trino_kerberos_principal=cluster.trino_kerberos_principal,
                trino_kerberos_service_name=cluster.trino_kerberos_service_name,
                trino_krb5_ccname=cluster.trino_krb5_ccname,
                trino_krb5_config=cluster.trino_krb5_config,
                trino_kerberos_ca_cert=cluster.trino_kerberos_ca_cert,
                trino_kerberos_insecure_tls=cluster.trino_kerberos_insecure_tls,
            )
    raise WebError(
        "Selected cluster is not configured in local config.",
        title="Selected source is not configured",
        reason_code="web.cluster_not_found",
        stage="Checking selected source",
        next_step="Choose an available source from local config or restart after fixing clusters[].",
    )


def require_cm_cluster_settings(settings: WebSettings) -> None:
    if settings.query_profile_source != "cm":
        raise WebError(
            "Recent and Running scans require a Cloudera Manager configured cluster.",
            title="Cloudera Manager source is required",
            reason_code="impala.cm_source_required",
            stage="Checking selected source",
            next_step="Select a Cloudera Manager source for Recent/Running, or use a direct-Impala supported action.",
        )
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
            stage="Checking selected source",
            next_step="Add the missing CM settings to the selected source or choose another source.",
        )
