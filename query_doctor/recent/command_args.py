"""Recent batch subprocess argument builders."""

from __future__ import annotations

from query_doctor.impala.hms_metadata import METADATA_SOURCE_HMS_POSTGRES
from query_doctor.recent.batch_models import BatchConfig


def append_cm_connection_args(cmd: list[str], config: BatchConfig) -> None:
    if config.config_path:
        cmd.extend(["--config", config.config_path])
    if config.cm_url and config.cluster and config.service:
        cmd.extend(
            ["--cm-url", config.cm_url, "--cluster", config.cluster, "--service", config.service]
        )
    if config.ca_bundle:
        cmd.extend(["--ca-bundle", config.ca_bundle])


def append_cm_config_args(cmd: list[str], config: BatchConfig) -> None:
    append_cm_connection_args(cmd, config)
    if config.redact_identifiers:
        cmd.append("--redact-identifiers")
    if not config.redact_hosts:
        cmd.append("--no-redact-hosts")


def metadata_source_configured(config: BatchConfig) -> bool:
    if config.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
        return True
    return bool(config.metadata_coordinator)


def append_metadata_args(cmd: list[str], config: BatchConfig) -> None:
    cmd.extend(["--metadata-mode", config.metadata_mode])
    if config.metadata_mode == "off" or not metadata_source_configured(config):
        return
    if config.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
        cmd.extend(["--metadata-source", config.metadata_source])
        cmd.extend(["--metadata-hms-postgres-dsn-env", config.metadata_hms_postgres_dsn_env])
        cmd.extend(["--metadata-timeout-sec", str(config.metadata_timeout_sec)])
        append_metadata_bounds_and_redaction_args(cmd, config)
        return
    cmd.extend(["--metadata-coordinator", config.metadata_coordinator])
    cmd.extend(["--metadata-auth", config.metadata_auth])
    cmd.extend(["--metadata-protocol", config.metadata_protocol])
    cmd.extend(["--metadata-timeout-sec", str(config.metadata_timeout_sec)])
    if config.metadata_kerberos_service_name:
        cmd.extend(["--metadata-kerberos-service-name", config.metadata_kerberos_service_name])
    if config.metadata_kerberos_host_fqdn:
        cmd.extend(["--metadata-kerberos-host-fqdn", config.metadata_kerberos_host_fqdn])
    if config.metadata_ssl:
        cmd.append("--metadata-ssl")
    if config.metadata_ca_cert:
        cmd.extend(["--metadata-ca-cert", config.metadata_ca_cert])
    append_metadata_bounds_and_redaction_args(cmd, config)


def append_metadata_bounds_and_redaction_args(cmd: list[str], config: BatchConfig) -> None:
    if config.metadata_max_tables is not None:
        cmd.extend(["--metadata-max-tables", str(config.metadata_max_tables)])
    if config.metadata_max_output_bytes is not None:
        cmd.extend(["--metadata-max-output-bytes", str(config.metadata_max_output_bytes)])
    if config.metadata_redact:
        cmd.append("--metadata-redact")
    if config.redact_identifiers:
        cmd.append("--metadata-redact-identifiers")
    else:
        cmd.append("--metadata-no-redact-identifiers")
    if config.redact_hosts:
        cmd.append("--metadata-redact-hosts")
    else:
        cmd.append("--metadata-no-redact-hosts")
