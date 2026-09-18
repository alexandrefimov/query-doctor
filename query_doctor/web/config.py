"""Web startup config loading and settings assembly."""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import replace
from pathlib import Path

from query_doctor.cli import collect_cm_profiles as cm_collector
from query_doctor.config.contract import load_and_validate_config
from query_doctor.report.llm_client import (
    DEFAULT_LLM_API_BASE_URL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_URL,
    LLM_PROVIDER_OLLAMA,
    normalize_llm_provider,
)
from query_doctor.report.language_contract import normalize_report_language
from query_doctor.trino.support_mode import (
    TRINO_SUPPORT_MODE_BETA,
    TRINO_SUPPORT_MODE_OFF,
    normalize_trino_support_mode,
    trino_support_mode_enabled,
)
from query_doctor.recent.batch_config import validate_not_dangerous_output_path
from query_doctor.web.cluster_selection import build_web_cluster_configs, settings_for_cluster_key
from query_doctor.impala.hms_metadata import DEFAULT_HMS_POSTGRES_DSN_ENV, METADATA_SOURCE_IMPALA
from query_doctor.web.models import (
    DEFAULT_CORPUS_DIR,
    DEFAULT_HOST,
    DEFAULT_IMPALA_PROFILE_PORT,
    DEFAULT_IMPALA_PROFILE_SCHEME,
    DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES,
    DEFAULT_LANGUAGE,
    DEFAULT_METADATA_AUTH,
    DEFAULT_METADATA_PROTOCOL,
    DEFAULT_METADATA_TIMEOUT_SEC,
    DEFAULT_MODEL,
    DEFAULT_OPTIMIZER_MODEL,
    DEFAULT_PORT,
    DEFAULT_QUERY_PROFILE_SOURCE,
    DEFAULT_RECENT_SCAN_TIMEZONE,
    WebError,
    WebClusterConfig,
    WebSettings,
    metadata_collection_configured,
)
from query_doctor.web.owner_raw_policy import (
    authenticated_viewer_identity_configured_for_policy,
    owner_raw_nonlocal_bind_requires_authenticated_viewer,
)
from query_doctor.web.public_demo import default_public_demo_summary_path
from query_doctor.web.viewer_identity import (
    local_first_viewer_identity,
    normalize_viewer_identity_header,
    unauthenticated_viewer_identity,
)
from query_doctor.prometheus.timeseries import (
    DEFAULT_PROMETHEUS_METRICS_PROFILE,
    DEFAULT_PROMETHEUS_STEP_SEC,
    DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
)
from query_doctor.source_visibility import (
    SOURCE_VISIBILITY_OWNER_RAW,
    SOURCE_VISIBILITY_SAFE,
    collectable_owner_users,
    normalize_source_owner_user,
    normalize_source_visibility,
    owner_user_from_kerberos_principal,
    source_owner_user_from_env,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}
KEYTAB_OWNER_USERS_TIMEOUT_SEC = 2
MAX_KEYTAB_OWNER_USER_OPTIONS = 50


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def validate_bind_host(host: str, *, allow_nonlocal_web_bind: bool) -> None:
    if host in LOCAL_BIND_HOSTS:
        return
    if allow_nonlocal_web_bind:
        return
    raise WebError(
        "Refusing non-local bind. Use --host 127.0.0.1 or pass "
        "--allow-nonlocal-web-bind explicitly for a local web risk review.",
        title="Non-local web bind was rejected",
        reason_code="web.nonlocal_bind_rejected",
        stage="Checking web startup security",
        next_step="Bind to 127.0.0.1, or explicitly allow non-local bind after a local risk review.",
    )


def settings_include_owner_raw_source_visibility(settings: WebSettings) -> bool:
    if settings.source_visibility == SOURCE_VISIBILITY_OWNER_RAW:
        return True
    return any(
        cluster.source_visibility == SOURCE_VISIBILITY_OWNER_RAW for cluster in settings.clusters
    )


def viewer_identity_has_authenticated_raw_subjects(settings: WebSettings) -> bool:
    return authenticated_viewer_identity_configured_for_policy(
        settings.viewer_identity,
        viewer_identity_header=None,
    )


def authenticated_viewer_identity_configured(settings: WebSettings) -> bool:
    return authenticated_viewer_identity_configured_for_policy(
        settings.viewer_identity,
        viewer_identity_header=settings.viewer_identity_header,
    )


def validate_owner_raw_nonlocal_bind(settings: WebSettings) -> None:
    if not owner_raw_nonlocal_bind_requires_authenticated_viewer(
        host=settings.host,
        allow_nonlocal_web_bind=settings.allow_nonlocal_web_bind,
        source_visibility_owner_raw=settings_include_owner_raw_source_visibility(settings),
        authenticated_viewer_configured=authenticated_viewer_identity_configured(settings),
    ):
        return
    raise WebError(
        "Refusing non-local web bind with source_visibility=owner_raw. "
        "Shared owner_raw requires a D3 trusted auth front door that authenticates "
        "the request, strips inbound viewer headers, and sets exactly one "
        "normalized viewer_identity_header for Query Doctor's owner check. "
        "Configure viewer_identity_header behind that front door, bind to "
        "127.0.0.1, or use source_visibility=safe.",
        title="Owner raw source visibility was rejected",
        reason_code="owner_raw.nonlocal_auth_required",
        stage="Checking owner source visibility",
        next_step=(
            "Configure a trusted viewer_identity_header front door, bind locally, "
            "or switch source_visibility to safe."
        ),
    )


def metadata_configured(settings: WebSettings) -> bool:
    return metadata_collection_configured(settings)


def impala_profile_source_configured(settings: WebSettings) -> bool:
    return settings.query_profile_source == "impala" and bool(settings.impala_profile_hosts)


def resolve_web_config_path(config_path: str | Path | None, *, cwd: Path) -> Path:
    if config_path:
        return Path(config_path).expanduser()
    default_path = cm_collector.discover_default_local_config(
        cwd=cwd,
        repo_root=_REPO_ROOT,
    )
    return default_path or (cwd / cm_collector.DEFAULT_LOCAL_CONFIG_NAME)


def load_web_local_config(config_path: str | Path | None, *, cwd: Path) -> dict[str, object]:
    if config_path:
        path = Path(config_path).expanduser()
        if not path.is_absolute():
            path = cwd / path
        if not path.is_file():
            return {}
        return cm_collector.load_local_config(str(path), cwd=cwd)
    result = load_and_validate_config(
        None,
        cwd=cwd,
        repo_root=_REPO_ROOT,
    )
    return result.values


def load_krb5ccname_from_local_config(config_path: Path, *, cwd: Path) -> str | None:
    values = load_web_local_config(config_path, cwd=cwd)
    value = values.get("krb5ccname")
    return value if isinstance(value, str) else None


def keytab_path_from_env(env: dict[str, str] | os._Environ[str]) -> str | None:
    value = (env.get("QD_KEYTAB") or "").strip()
    if not value:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    path_text = value[5:] if value.startswith("FILE:") else value
    path = Path(path_text).expanduser()
    if not path.is_file():
        return None
    return str(path)


def source_owner_user_options_from_keytab(
    env: dict[str, str] | os._Environ[str] | None = None,
    *,
    runner=subprocess.run,
) -> tuple[str, ...]:
    env = os.environ if env is None else env
    keytab = keytab_path_from_env(env)
    if keytab is None:
        return ()
    for command in (["klist", "-k", keytab], ["ktutil", "-k", keytab, "list"]):
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                timeout=KEYTAB_OWNER_USERS_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if getattr(result, "returncode", 1) != 0:
            continue
        options = source_owner_user_options_from_klist(getattr(result, "stdout", ""))
        if options:
            return options
    return ()


def source_owner_user_options_from_klist(text: str) -> tuple[str, ...]:
    options: list[str] = []
    for line in text.splitlines():
        tokens = line.strip().split()
        if not tokens or not tokens[0].isdigit():
            continue
        principal = tokens[-1]
        if principal.startswith("("):
            continue
        owner = owner_user_from_kerberos_principal(principal)
        if owner is None:
            continue
        options.append(owner)
    return sorted_source_owner_user_options(tuple(options))


def sorted_source_owner_user_options(options: tuple[str, ...]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for option in options:
        owner = normalize_source_owner_user(option)
        if owner and owner not in unique:
            unique[owner] = owner
    return tuple(sorted(unique, key=lambda value: (value.casefold(), value)))[
        :MAX_KEYTAB_OWNER_USER_OPTIONS
    ]


def source_owner_user_from_keytab_options(options: tuple[str, ...]) -> str | None:
    sorted_options = sorted_source_owner_user_options(options)
    return sorted_options[0] if sorted_options else None


def llm_base_url_for_provider(provider: str, configured: str | None) -> str | None:
    if configured:
        return configured
    if provider == LLM_PROVIDER_OLLAMA:
        return DEFAULT_OLLAMA_URL
    return DEFAULT_LLM_API_BASE_URL or None


def validate_web_startup_config(
    config_path: Path,
    *,
    cwd: Path,
    env: dict[str, str] | os._Environ[str] | None = None,
    require_cm: bool = True,
) -> list[str]:
    env = os.environ if env is None else env
    config_values = load_web_local_config(config_path, cwd=cwd)
    clusters = build_web_cluster_configs(config_values)
    config_base_dir = resolve_config_path_value(Path(config_path), base_dir=cwd).parent
    manual_only_profile_dir = configured_manual_profile_dir(config_values, base_dir=config_base_dir)
    if manual_only_profile_dir is not None:
        validate_manual_profile_dir(manual_only_profile_dir)
    clusters_to_validate = clusters or (
        WebClusterConfig(
            key="default",
            label="Configured cluster",
            cm_url=optional_config_string(config_values, "cm_url"),
            cm_cluster=optional_config_string(config_values, "cluster"),
            cm_service=optional_config_string(config_values, "service"),
            cm_username=optional_config_string(config_values, "username"),
            ca_bundle=optional_config_string(config_values, "ca_bundle"),
            insecure_skip_verify=optional_config_bool(config_values, "insecure_skip_verify")
            is True,
            manual_profile_dir=manual_only_profile_dir,
            query_profile_source=first_string_value(
                optional_config_string(config_values, "query_profile_source"),
                DEFAULT_QUERY_PROFILE_SOURCE,
            )
            or DEFAULT_QUERY_PROFILE_SOURCE,
            impala_profile_hosts=optional_config_string_list(config_values, "impala_profile_hosts"),
        ),
    )
    missing: list[str] = []
    cm_clusters = [
        cluster
        for cluster in clusters_to_validate
        if cluster.query_profile_source != "impala"
        and not cluster_is_manual_only(cluster)
        and not cluster_is_trino_beta_only(cluster)
    ]
    for cluster in clusters_to_validate:
        if cluster.manual_profile_dir is not None:
            validate_manual_profile_dir(
                resolve_config_path_value(cluster.manual_profile_dir, base_dir=config_base_dir)
            )
        if cluster_has_trino_beta_config(cluster):
            validate_trino_beta_startup_cluster(cluster, config_base_dir=config_base_dir)
        if cluster_is_manual_only(cluster) or cluster_is_trino_beta_only(cluster):
            continue
        if not require_cm:
            continue
        if cluster.query_profile_source == "impala":
            if not cluster.impala_profile_hosts:
                raise WebError(
                    "Missing required Impala startup setting(s): impala_profile_hosts. "
                    "Provide one or more impalad web hosts in local config.",
                    title="Direct Impala profile host is not configured",
                    reason_code="impala.direct_profile_host_missing",
                    stage="Checking web startup source config",
                    next_step="Add impala_profile_hosts to the selected source or choose a CM source.",
                )
            if cluster.collect_prometheus_timeseries and not cluster.prometheus_url:
                raise WebError(
                    "collect_prometheus_timeseries=true requires prometheus_url in local config.",
                    title="Prometheus metrics source is not configured",
                    reason_code="impala.prometheus_url_missing",
                    stage="Checking web startup source config",
                    next_step=(
                        "Configure prometheus_url for this source or disable Prometheus timeseries collection."
                    ),
                )
            continue
        if not first_string_value(cluster.cm_url, env.get("CM_URL")):
            missing.append("cm_url")
        if not cluster.cm_cluster:
            missing.append("cluster")
        if not cluster.cm_service:
            missing.append("service")
    if (
        require_cm
        and cm_clusters
        and not first_string_value(
            optional_config_string(config_values, "username"), env.get("CM_USERNAME")
        )
        and not any(cluster.cm_username for cluster in cm_clusters)
    ):
        missing.append("username/cm_user")
    if (
        require_cm
        and cm_clusters
        and not ((env.get("CM_PASSWORD") or "").strip() or (env.get("CM_TOKEN") or "").strip())
    ):
        missing.append("CM_PASSWORD/CM_TOKEN environment variable")
    missing = list(dict.fromkeys(missing))
    if missing:
        raise WebError(
            "Missing required CM startup setting(s): "
            + ", ".join(missing)
            + ". Provide non-secret CM settings in local config and CM_PASSWORD or CM_TOKEN "
            "via environment variables. If you only have one exported Impala text profile, "
            "configure manual_profile_dir as a local profile inbox instead of CM settings.",
            title="Cloudera Manager startup settings are missing",
            reason_code="impala.cm_startup_settings_missing",
            stage="Checking web startup source config",
            next_step=(
                "Add the missing non-secret CM settings and CM_PASSWORD or CM_TOKEN, "
                "or configure manual_profile_dir for local exported profiles."
            ),
        )

    warnings: list[str] = []
    for cluster in clusters_to_validate:
        if cluster.query_profile_source == "impala":
            continue
        ca_bundle = cluster.ca_bundle
        insecure_skip_verify = cluster.insecure_skip_verify
        if ca_bundle:
            ca_path = Path(ca_bundle).expanduser()
            if not ca_path.is_absolute():
                ca_path = cwd / ca_path
            if not ca_path.is_file() or not os.access(ca_path, os.R_OK):
                raise WebError(
                    "Configured ca_bundle is not readable.",
                    title="Cloudera Manager CA bundle is not readable",
                    reason_code="impala.cm_ca_bundle_unreadable",
                    stage="Checking web startup TLS config",
                    next_step=(
                        "Fix the configured CM ca_bundle file, remove the setting, "
                        "or explicitly use insecure_skip_verify for a local risk review."
                    ),
                )
            if insecure_skip_verify:
                warnings.append(
                    "insecure_skip_verify=true is set; CM TLS verification will be disabled even though ca_bundle is configured."
                )
        elif insecure_skip_verify:
            warnings.append(
                "insecure_skip_verify=true is set; CM TLS verification will be disabled."
            )
    return warnings


def configured_manual_profile_dir(
    config_values: dict[str, object],
    *,
    base_dir: Path,
) -> Path | None:
    path = optional_config_path(config_values, "manual_profile_dir")
    if path is None:
        return None
    return resolve_config_path_value(path, base_dir=base_dir)


def configured_corpus_dir(
    args: argparse.Namespace,
    config_values: dict[str, object],
    *,
    config_path: Path,
    cwd: Path,
) -> Path:
    cli_value = getattr(args, "corpus_dir", None)
    if cli_value:
        return resolve_config_path_value(Path(cli_value), base_dir=cwd)
    config_value = optional_config_path(config_values, "corpus_dir")
    if config_value is not None:
        config_base_dir = resolve_config_path_value(config_path, base_dir=cwd).parent
        return resolve_config_path_value(config_value, base_dir=config_base_dir)
    return resolve_config_path_value(DEFAULT_CORPUS_DIR, base_dir=cwd)


def configured_recent_batch_root(
    args: argparse.Namespace,
    config_values: dict[str, object],
    *,
    config_path: Path,
    cwd: Path,
) -> Path | None:
    cli_value = getattr(args, "recent_batch_root", None)
    if cli_value:
        return resolve_config_path_value(Path(cli_value), base_dir=cwd)
    config_value = optional_config_path(config_values, "recent_batch_root")
    if config_value is None:
        return None
    config_base_dir = resolve_config_path_value(config_path, base_dir=cwd).parent
    return resolve_config_path_value(config_value, base_dir=config_base_dir)


def configured_optional_config_path(
    config_values: dict[str, object],
    key: str,
    *,
    config_path: Path,
    cwd: Path,
) -> Path | None:
    value = optional_config_path(config_values, key)
    if value is None:
        return None
    config_base_dir = resolve_config_path_value(config_path, base_dir=cwd).parent
    return resolve_config_path_value(value, base_dir=config_base_dir)


def resolve_config_path_value(path: Path, *, base_dir: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else base_dir / expanded


def validate_manual_profile_dir(path: Path) -> None:
    try:
        if not path.is_dir() or not os.access(path, os.R_OK):
            raise WebError(
                "Configured manual_profile_dir is not a readable local directory.",
                title="Manual profile directory is not readable",
                reason_code="web.manual_profile_dir_unreadable",
                stage="Checking web startup source config",
                next_step="Fix manual_profile_dir or choose a CM/direct-Impala source.",
            )
    except OSError as exc:
        raise WebError(
            "Configured manual_profile_dir is not available.",
            title="Manual profile directory is not available",
            reason_code="web.manual_profile_dir_unavailable",
            stage="Checking web startup source config",
            next_step="Fix manual_profile_dir or choose a CM/direct-Impala source.",
        ) from exc


def validate_recent_batch_root(path: Path | None) -> None:
    if path is None:
        return
    try:
        validate_not_dangerous_output_path(path / "query-doctor-web-batch-validation")
    except ValueError as exc:
        raise WebError(
            "Configured recent_batch_root must allow dedicated Query Doctor batch output directories under the system temp directory.",
            title="Recent batch cache root is not safe",
            reason_code="web.recent_batch_root_invalid",
            stage="Checking Recent scan cache config",
            next_step="Use a dedicated Query Doctor temp-backed cache directory.",
        ) from exc


def cluster_is_manual_only(cluster: WebClusterConfig) -> bool:
    return bool(cluster.manual_profile_dir) and not any(
        (
            cluster.cm_url,
            cluster.cm_cluster,
            cluster.cm_service,
            cluster.impala_profile_hosts,
        )
    )


def cluster_is_trino_beta_only(cluster: WebClusterConfig) -> bool:
    return bool(
        (trino_support_mode_enabled(cluster.trino_support_mode) or cluster.trino_beta_enabled)
        and cluster.trino_coordinator_url
        and cluster.trino_query_info_source_contract
        and not any(
            (
                cluster.cm_url,
                cluster.cm_cluster,
                cluster.cm_service,
                cluster.impala_profile_hosts,
                cluster.manual_profile_dir,
            )
        )
    )


def cluster_has_trino_beta_config(cluster: WebClusterConfig) -> bool:
    return bool(
        cluster.trino_support_mode != TRINO_SUPPORT_MODE_OFF
        or cluster.trino_beta_enabled
        or cluster.trino_coordinator_url
        or cluster.trino_query_info_source_contract
        or cluster.trino_query_list_source_contract
        or cluster.trino_auth_header_file
        or cluster.trino_kerberos_principal
        or cluster.trino_krb5_ccname
        or cluster.trino_krb5_config
        or cluster.trino_kerberos_ca_cert
        or cluster.trino_kerberos_insecure_tls
    )


def validate_trino_beta_startup_cluster(
    cluster: WebClusterConfig,
    *,
    config_base_dir: Path,
) -> None:
    from query_doctor.web.trino_beta_query import validate_trino_beta_startup_config

    if not cluster_has_trino_beta_config(cluster):
        return
    if cluster.trino_support_mode != TRINO_SUPPORT_MODE_BETA and cluster.trino_beta_enabled:
        raise WebError(
            "trino_beta_enabled is a legacy beta-only setting and cannot be combined with "
            "trino_support_mode=production.",
            title="Trino support mode is ambiguous",
            reason_code="trino.support_mode_conflict",
            stage="Checking Trino local config",
            next_step=(
                "Remove trino_beta_enabled when using trino_support_mode=production, "
                "or set trino_support_mode=beta for the legacy beta lane."
            ),
        )
    if not trino_support_mode_enabled(cluster.trino_support_mode):
        raise WebError(
            "Trino local config requires trino_support_mode=beta or production.",
            title="Trino source is not enabled",
            reason_code="trino.not_enabled",
            stage="Checking Trino local config",
            next_step=(
                "Set trino_support_mode=beta or production for this local source, "
                "or remove Trino settings."
            ),
        )
    if not cluster.trino_coordinator_url:
        raise WebError(
            "Trino local config requires trino_coordinator_url.",
            title="Trino coordinator is not configured",
            reason_code="trino.coordinator_missing",
            stage="Checking Trino local config",
            next_step="Configure trino_coordinator_url for this local source or remove Trino settings.",
        )
    if cluster.trino_query_info_source_contract is None:
        raise WebError(
            "Trino local config requires trino_query_info_source_contract.",
            title="Trino source contract is not configured",
            reason_code="trino.query_info_contract_missing",
            stage="Checking Trino local config",
            next_step=(
                "Configure trino_query_info_source_contract for this local source "
                "or remove Trino settings."
            ),
        )
    validate_trino_beta_startup_config(
        source_contract=resolve_config_path_value(
            cluster.trino_query_info_source_contract,
            base_dir=config_base_dir,
        ),
        coordinator_url=cluster.trino_coordinator_url,
        query_list_source_contract=resolve_config_path_value(
            cluster.trino_query_list_source_contract,
            base_dir=config_base_dir,
        )
        if cluster.trino_query_list_source_contract is not None
        else None,
        auth_header_file=resolve_config_path_value(
            cluster.trino_auth_header_file,
            base_dir=config_base_dir,
        )
        if cluster.trino_auth_header_file is not None
        else None,
        kerberos_principal=cluster.trino_kerberos_principal,
        kerberos_service_name=cluster.trino_kerberos_service_name,
        krb5_ccname=cluster.trino_krb5_ccname,
        krb5_config=resolve_config_path_value(
            cluster.trino_krb5_config,
            base_dir=config_base_dir,
        )
        if cluster.trino_krb5_config is not None
        else None,
        kerberos_ca_cert=resolve_config_path_value(
            cluster.trino_kerberos_ca_cert,
            base_dir=config_base_dir,
        )
        if cluster.trino_kerberos_ca_cert is not None
        else None,
        kerberos_insecure_tls=cluster.trino_kerberos_insecure_tls,
    )


def validate_public_demo_settings(settings: WebSettings) -> None:
    if not settings.public_demo:
        return
    if settings.batch_summary is None:
        raise WebError(
            "Public demo mode requires --batch-summary generated by query-doctor-demo.",
            title="Public demo pack is not configured",
            reason_code="web.public_demo_summary_missing",
            stage="Checking public demo startup",
            next_step="Start public demo mode with the generated synthetic demo batch summary.",
        )
    if not settings.no_llm:
        raise WebError(
            "Public demo mode requires --no-llm.",
            title="Public demo LLM actions are not allowed",
            reason_code="web.public_demo_llm_not_allowed",
            stage="Checking public demo startup",
            next_step="Restart public demo mode with --no-llm.",
        )
    if settings.clusters or any(
        (
            settings.cm_url,
            settings.cm_cluster,
            settings.cm_service,
            settings.cm_username,
            settings.manual_profile_dir,
            settings.impala_profile_hosts,
            settings.prometheus_url,
            settings.metadata_coordinator,
            settings.metadata_source != METADATA_SOURCE_IMPALA,
            settings.metadata_kerberos_host_fqdn,
            settings.source_owner_user,
            settings.source_owner_user_options,
            settings.viewer_identity_header,
            settings.viewer_identity.viewer_raw_subjects,
            settings.krb5ccname,
            settings.trino_support_mode != TRINO_SUPPORT_MODE_OFF,
            settings.trino_beta_enabled,
            settings.trino_coordinator_url,
            settings.trino_query_info_source_contract,
            settings.trino_query_list_source_contract,
            settings.trino_auth_header_file,
            settings.trino_kerberos_principal,
            settings.trino_krb5_ccname,
            settings.trino_krb5_config,
            settings.trino_kerberos_ca_cert,
            settings.trino_kerberos_insecure_tls,
        )
    ):
        raise WebError(
            "Public demo mode must not load CM, Impala, Prometheus, metadata, owner source, raw viewer, or Trino settings.",
            title="Public demo source config was rejected",
            reason_code="web.public_demo_external_source_rejected",
            stage="Checking public demo startup",
            next_step="Remove live source settings from public demo mode and use the synthetic demo pack.",
        )


def optional_config_string(config_values: dict[str, object], key: str) -> str | None:
    value = config_values.get(key)
    return value if isinstance(value, str) and value else None


def optional_config_path(config_values: dict[str, object], key: str) -> Path | None:
    value = optional_config_string(config_values, key)
    return Path(value).expanduser() if value else None


def optional_config_int(config_values: dict[str, object], key: str) -> int | None:
    value = config_values.get(key)
    return value if isinstance(value, int) else None


def optional_config_bool(config_values: dict[str, object], key: str) -> bool | None:
    value = config_values.get(key)
    return value if isinstance(value, bool) else None


def optional_config_string_list(config_values: dict[str, object], key: str) -> tuple[str, ...]:
    value = config_values.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item for item in value if item)
    return ()


def first_string_value(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def first_int_value(*values: int | None, default: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return default


def normalize_engine_config(value: object) -> str:
    engine = str(value or "").strip().lower()
    return engine if engine in {"impala", "trino"} else "impala"


def configured_viewer_identity_header(
    args: argparse.Namespace,
    config_values: dict[str, object],
) -> str | None:
    value = first_string_value(
        getattr(args, "viewer_identity_header", None),
        optional_config_string(config_values, "viewer_identity_header"),
    )
    try:
        return normalize_viewer_identity_header(value)
    except ValueError as exc:
        raise WebError(
            str(exc),
            title="Viewer identity header is invalid",
            reason_code="owner_raw.viewer_identity_header_invalid",
            stage="Checking owner source visibility",
            next_step="Use a simple HTTP header token for viewer_identity_header or disable owner raw source.",
        ) from exc


def owner_raw_source_enabled_setting(
    args: argparse.Namespace,
    config_values: dict[str, object],
) -> bool:
    if getattr(args, "disable_owner_raw_source", False):
        return False
    value = optional_config_bool(config_values, "owner_raw_source_enabled")
    return True if value is None else value


def merged_bool_setting(
    cli_value: bool, config_value: bool | None, *, default: bool = False
) -> bool:
    return bool(cli_value) or (config_value if config_value is not None else default)


def build_web_settings(
    args: argparse.Namespace,
    *,
    cwd: Path,
    ignore_default_config: bool = False,
) -> WebSettings:
    public_demo = getattr(args, "public_demo", False)
    if public_demo and not args.config:
        config_path = cwd / cm_collector.DEFAULT_LOCAL_CONFIG_NAME
        config_values: dict[str, object] = {}
    elif ignore_default_config and not args.config:
        config_path = cwd / ".query-doctor-quickstart-no-config.json"
        config_values = {}
    else:
        config_path = resolve_web_config_path(args.config, cwd=cwd)
        config_values = load_web_local_config(args.config, cwd=cwd)
    config_base_dir = resolve_config_path_value(config_path, base_dir=cwd).parent
    clusters = resolve_web_cluster_config_paths(
        build_web_cluster_configs(config_values),
        base_dir=config_base_dir,
    )
    source_owner_user_options = (
        ()
        if public_demo
        else sorted_source_owner_user_options(
            source_owner_user_options_from_keytab(dict(os.environ))
        )
    )
    privacy_mode = optional_config_bool(config_values, "privacy_mode")
    if privacy_mode is None:
        privacy_mode = True
    redact_identifiers = optional_config_bool(config_values, "redact_identifiers")
    if redact_identifiers is None:
        redact_identifiers = privacy_mode
    redact_hosts = optional_config_bool(config_values, "redact_hosts")
    if redact_hosts is None:
        redact_hosts = privacy_mode
    source_visibility = normalize_source_visibility(
        first_string_value(
            optional_config_string(config_values, "source_visibility"),
            SOURCE_VISIBILITY_SAFE,
        )
    )
    source_owner_user = (
        None
        if public_demo
        else normalize_source_owner_user(
            first_string_value(
                optional_config_string(config_values, "source_owner_user"),
                source_owner_user_from_env(dict(os.environ)),
                source_owner_user_from_keytab_options(source_owner_user_options),
            )
        )
    )
    viewer_identity = (
        unauthenticated_viewer_identity()
        if public_demo
        else local_first_viewer_identity(
            collectable_owner_users(source_owner_user, source_owner_user_options)
        )
    )
    legacy_trino_beta_enabled = optional_config_bool(config_values, "trino_beta_enabled") is True
    trino_support_mode = normalize_trino_support_mode(
        optional_config_string(config_values, "trino_support_mode"),
        legacy_beta_enabled=legacy_trino_beta_enabled,
    )
    report_llm_provider = normalize_llm_provider(
        first_string_value(
            getattr(args, "report_llm_provider", None),
            optional_config_string(config_values, "report_llm_provider"),
            os.environ.get("QD_REPORT_LLM_PROVIDER"),
            os.environ.get("QD_LLM_PROVIDER"),
            DEFAULT_LLM_PROVIDER,
        )
    )
    optimizer_llm_provider = normalize_llm_provider(
        first_string_value(
            getattr(args, "optimizer_llm_provider", None),
            optional_config_string(config_values, "optimizer_llm_provider"),
            os.environ.get("QD_OPTIMIZER_LLM_PROVIDER"),
            os.environ.get("QD_LLM_PROVIDER"),
            DEFAULT_LLM_PROVIDER,
        )
    )
    report_llm_base_url = llm_base_url_for_provider(
        report_llm_provider,
        first_string_value(
            getattr(args, "report_llm_base_url", None),
            optional_config_string(config_values, "report_llm_base_url"),
            os.environ.get("QD_REPORT_LLM_API_BASE_URL"),
            os.environ.get("QD_REPORT_LLM_BASE_URL"),
            os.environ.get("QD_LLM_API_BASE_URL"),
            os.environ.get("QD_LLM_BASE_URL"),
        ),
    )
    optimizer_llm_base_url = llm_base_url_for_provider(
        optimizer_llm_provider,
        first_string_value(
            getattr(args, "optimizer_llm_base_url", None),
            optional_config_string(config_values, "optimizer_llm_base_url"),
            os.environ.get("QD_OPTIMIZER_LLM_API_BASE_URL"),
            os.environ.get("QD_OPTIMIZER_LLM_BASE_URL"),
            os.environ.get("QD_LLM_API_BASE_URL"),
            os.environ.get("QD_LLM_BASE_URL"),
        ),
    )
    try:
        language = normalize_report_language(
            first_string_value(
                optional_config_string(config_values, "language"),
                DEFAULT_LANGUAGE,
            )
            or DEFAULT_LANGUAGE
        )
    except ValueError as exc:
        raise WebError(
            str(exc),
            title="Report language is invalid",
            reason_code="web.language_invalid",
            stage="Loading local web config",
            next_step="Use a supported report language value in local config.",
        ) from exc

    settings = WebSettings(
        config=config_path,
        host=first_string_value(
            args.host,
            optional_config_string(config_values, "host"),
            DEFAULT_HOST,
        )
        or DEFAULT_HOST,
        cm_url=first_string_value(
            optional_config_string(config_values, "cm_url"),
        ),
        cm_cluster=first_string_value(
            optional_config_string(config_values, "cluster"),
        ),
        cm_service=first_string_value(
            optional_config_string(config_values, "service"),
        ),
        cm_username=first_string_value(
            optional_config_string(config_values, "username"),
        ),
        ca_bundle=first_string_value(
            optional_config_string(config_values, "ca_bundle"),
        ),
        insecure_skip_verify=optional_config_bool(config_values, "insecure_skip_verify") is True,
        corpus_dir=configured_corpus_dir(
            args,
            config_values,
            config_path=config_path,
            cwd=cwd,
        ),
        recent_batch_root=configured_recent_batch_root(
            args,
            config_values,
            config_path=config_path,
            cwd=cwd,
        ),
        manual_profile_dir=optional_config_path(config_values, "manual_profile_dir"),
        clusters=clusters,
        active_cluster_key=first_string_value(
            optional_config_string(config_values, "active_cluster_key"),
            clusters[0].key if clusters else None,
        ),
        port=first_int_value(
            args.port,
            optional_config_int(config_values, "port"),
            default=DEFAULT_PORT,
        ),
        allow_nonlocal_web_bind=args.allow_nonlocal_web_bind,
        max_profile_bytes=first_int_value(
            args.max_profile_bytes,
            optional_config_int(config_values, "max_profile_bytes"),
            default=None,
        ),
        model=first_string_value(
            args.model,
            optional_config_string(config_values, "report_llm_model"),
            os.environ.get("QD_REPORT_LLM_MODEL"),
            os.environ.get("OLLAMA_MODEL"),
            DEFAULT_MODEL,
        )
        or DEFAULT_MODEL,
        optimizer_model=first_string_value(
            args.optimizer_model,
            optional_config_string(config_values, "optimizer_llm_model"),
            optional_config_string(config_values, "optimizer_model"),
            os.environ.get("QD_OPTIMIZER_LLM_MODEL"),
            os.environ.get("QD_OPTIMIZER_MODEL"),
            DEFAULT_OPTIMIZER_MODEL,
        ),
        report_llm_provider=report_llm_provider,
        report_llm_base_url=report_llm_base_url,
        report_llm_chat_path=first_string_value(
            getattr(args, "report_llm_chat_path", None),
            optional_config_string(config_values, "report_llm_chat_path"),
            os.environ.get("QD_REPORT_LLM_CHAT_PATH"),
            os.environ.get("QD_LLM_CHAT_PATH"),
        ),
        optimizer_llm_provider=optimizer_llm_provider,
        optimizer_llm_base_url=optimizer_llm_base_url,
        optimizer_llm_chat_path=first_string_value(
            getattr(args, "optimizer_llm_chat_path", None),
            optional_config_string(config_values, "optimizer_llm_chat_path"),
            os.environ.get("QD_OPTIMIZER_LLM_CHAT_PATH"),
            os.environ.get("QD_LLM_CHAT_PATH"),
        ),
        no_llm=public_demo
        or getattr(args, "no_llm", False)
        or optional_config_bool(config_values, "no_llm") is True,
        privacy_mode=privacy_mode,
        redact_identifiers=redact_identifiers,
        redact_hosts=redact_hosts,
        source_visibility=source_visibility,
        source_owner_user=source_owner_user,
        timeout_sec=args.timeout_sec,
        batch_summary=(
            Path(args.batch_summary).expanduser()
            if args.batch_summary
            else default_public_demo_summary_path()
            if public_demo
            else None
        ),
        public_demo=public_demo,
        query_profile_source=first_string_value(
            optional_config_string(config_values, "query_profile_source"),
            DEFAULT_QUERY_PROFILE_SOURCE,
        )
        or DEFAULT_QUERY_PROFILE_SOURCE,
        impala_profile_hosts=optional_config_string_list(config_values, "impala_profile_hosts"),
        impala_profile_port=first_int_value(
            optional_config_int(config_values, "impala_profile_port"),
            default=DEFAULT_IMPALA_PROFILE_PORT,
        )
        or DEFAULT_IMPALA_PROFILE_PORT,
        impala_profile_scheme=first_string_value(
            optional_config_string(config_values, "impala_profile_scheme"),
            DEFAULT_IMPALA_PROFILE_SCHEME,
        )
        or DEFAULT_IMPALA_PROFILE_SCHEME,
        impala_profile_timeout_sec=first_int_value(
            optional_config_int(config_values, "impala_profile_timeout_sec"),
            default=DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
        )
        or DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
        impala_query_list_max_bytes=first_int_value(
            optional_config_int(config_values, "impala_query_list_max_bytes"),
            default=DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES,
        )
        or DEFAULT_IMPALA_QUERY_LIST_MAX_BYTES,
        impala_profile_prefer_json=optional_config_bool(config_values, "impala_profile_prefer_json")
        is True,
        impala_profile_collect_docs=optional_config_bool(
            config_values, "impala_profile_collect_docs"
        )
        is True,
        impala_collect_admission_context=optional_config_bool(
            config_values, "impala_collect_admission_context"
        )
        is True,
        collect_prometheus_timeseries=optional_config_bool(
            config_values, "collect_prometheus_timeseries"
        )
        is True,
        prometheus_url=optional_config_string(config_values, "prometheus_url"),
        prometheus_metrics_profile=first_string_value(
            optional_config_string(config_values, "prometheus_metrics_profile"),
            DEFAULT_PROMETHEUS_METRICS_PROFILE,
        )
        or DEFAULT_PROMETHEUS_METRICS_PROFILE,
        prometheus_step_sec=first_int_value(
            optional_config_int(config_values, "prometheus_step_sec"),
            default=DEFAULT_PROMETHEUS_STEP_SEC,
        )
        or DEFAULT_PROMETHEUS_STEP_SEC,
        prometheus_timeseries_padding_sec=first_int_value(
            optional_config_int(config_values, "prometheus_timeseries_padding_sec"),
            default=DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
        )
        or DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
        metadata_source=first_string_value(
            getattr(args, "metadata_source", None),
            optional_config_string(config_values, "metadata_source"),
            METADATA_SOURCE_IMPALA,
        )
        or METADATA_SOURCE_IMPALA,
        metadata_hms_postgres_dsn_env=first_string_value(
            getattr(args, "metadata_hms_postgres_dsn_env", None),
            optional_config_string(config_values, "metadata_hms_postgres_dsn_env"),
            DEFAULT_HMS_POSTGRES_DSN_ENV,
        )
        or DEFAULT_HMS_POSTGRES_DSN_ENV,
        metadata_coordinator=first_string_value(
            args.metadata_coordinator,
            optional_config_string(config_values, "metadata_coordinator"),
        ),
        metadata_auth=first_string_value(
            args.metadata_auth,
            optional_config_string(config_values, "metadata_auth"),
            DEFAULT_METADATA_AUTH,
        )
        or DEFAULT_METADATA_AUTH,
        metadata_protocol=first_string_value(
            args.metadata_protocol,
            optional_config_string(config_values, "metadata_protocol"),
            DEFAULT_METADATA_PROTOCOL,
        )
        or DEFAULT_METADATA_PROTOCOL,
        metadata_kerberos_service_name=first_string_value(
            args.metadata_kerberos_service_name,
            optional_config_string(config_values, "metadata_kerberos_service_name"),
            optional_config_string(config_values, "impala_kerberos_service_name"),
        ),
        metadata_kerberos_host_fqdn=first_string_value(
            args.metadata_kerberos_host_fqdn,
            optional_config_string(config_values, "metadata_kerberos_host_fqdn"),
        ),
        metadata_ssl=merged_bool_setting(
            args.metadata_ssl,
            optional_config_bool(config_values, "metadata_ssl"),
        ),
        metadata_ca_cert=first_string_value(
            args.metadata_ca_cert,
            optional_config_string(config_values, "metadata_ca_cert"),
        ),
        metadata_timeout_sec=first_int_value(
            args.metadata_timeout_sec,
            optional_config_int(config_values, "metadata_timeout_sec"),
            default=DEFAULT_METADATA_TIMEOUT_SEC,
        ),
        metadata_max_tables=first_int_value(
            args.metadata_max_tables,
            optional_config_int(config_values, "metadata_max_tables"),
            default=None,
        ),
        metadata_max_output_bytes=first_int_value(
            args.metadata_max_output_bytes,
            optional_config_int(config_values, "metadata_max_output_bytes"),
            default=None,
        ),
        metadata_redact=merged_bool_setting(
            args.metadata_redact,
            optional_config_bool(config_values, "metadata_redact"),
            default=privacy_mode,
        ),
        krb5ccname=optional_config_string(config_values, "krb5ccname"),
        recent_scan_timezone=first_string_value(
            optional_config_string(config_values, "recent_scan_timezone"),
            DEFAULT_RECENT_SCAN_TIMEZONE,
        )
        or DEFAULT_RECENT_SCAN_TIMEZONE,
        language=language,
        source_owner_user_options=source_owner_user_options,
        viewer_identity_header=configured_viewer_identity_header(args, config_values),
        owner_raw_source_enabled=owner_raw_source_enabled_setting(args, config_values),
        viewer_identity=viewer_identity,
        selected_engine=normalize_engine_config(optional_config_string(config_values, "engine")),
        trino_support_mode=trino_support_mode,
        trino_beta_enabled=(
            legacy_trino_beta_enabled or trino_support_mode == TRINO_SUPPORT_MODE_BETA
        ),
        trino_coordinator_url=optional_config_string(config_values, "trino_coordinator_url"),
        trino_query_info_source_contract=configured_optional_config_path(
            config_values,
            "trino_query_info_source_contract",
            config_path=config_path,
            cwd=cwd,
        ),
        trino_query_list_source_contract=configured_optional_config_path(
            config_values,
            "trino_query_list_source_contract",
            config_path=config_path,
            cwd=cwd,
        ),
        trino_auth_header_file=configured_optional_config_path(
            config_values,
            "trino_auth_header_file",
            config_path=config_path,
            cwd=cwd,
        ),
        trino_kerberos_principal=optional_config_string(
            config_values,
            "trino_kerberos_principal",
        ),
        trino_kerberos_service_name=optional_config_string(
            config_values,
            "trino_kerberos_service_name",
        )
        or "HTTP",
        trino_krb5_ccname=optional_config_string(config_values, "trino_krb5_ccname"),
        trino_krb5_config=configured_optional_config_path(
            config_values,
            "trino_krb5_config",
            config_path=config_path,
            cwd=cwd,
        ),
        trino_kerberos_ca_cert=configured_optional_config_path(
            config_values,
            "trino_kerberos_ca_cert",
            config_path=config_path,
            cwd=cwd,
        ),
        trino_kerberos_insecure_tls=optional_config_bool(
            config_values,
            "trino_kerberos_insecure_tls",
        )
        is True,
    )
    validate_recent_batch_root(settings.recent_batch_root)
    if clusters:
        return settings_for_cluster_key(settings, settings.active_cluster_key)
    return settings


def resolve_web_cluster_config_paths(
    clusters: tuple[WebClusterConfig, ...],
    *,
    base_dir: Path,
) -> tuple[WebClusterConfig, ...]:
    resolved: list[WebClusterConfig] = []
    for cluster in clusters:
        resolved.append(
            replace(
                cluster,
                manual_profile_dir=resolve_optional_path(
                    cluster.manual_profile_dir, base_dir=base_dir
                ),
                trino_query_info_source_contract=resolve_optional_path(
                    cluster.trino_query_info_source_contract,
                    base_dir=base_dir,
                ),
                trino_query_list_source_contract=resolve_optional_path(
                    cluster.trino_query_list_source_contract,
                    base_dir=base_dir,
                ),
                trino_auth_header_file=resolve_optional_path(
                    cluster.trino_auth_header_file,
                    base_dir=base_dir,
                ),
                trino_krb5_config=resolve_optional_path(
                    cluster.trino_krb5_config,
                    base_dir=base_dir,
                ),
                trino_kerberos_ca_cert=resolve_optional_path(
                    cluster.trino_kerberos_ca_cert,
                    base_dir=base_dir,
                ),
            )
        )
    return tuple(resolved)


def resolve_optional_path(path: Path | None, *, base_dir: Path) -> Path | None:
    if path is None:
        return None
    return resolve_config_path_value(path, base_dir=base_dir)
