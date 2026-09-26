#!/usr/bin/env python3
"""Explicit read-only Impala table metadata collector for Query Doctor.

Metadata comes from Impala itself (the allowlisted SHOW statements over
HiveServer2) or, with `--source hms-postgres`, from the Hive Metastore's
PostgreSQL database, which answers the same questions without making catalogd
load a table it has not loaded yet.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from query_doctor.impala.connection_policy import (
    ImpalaConnectionConfigError,
    split_coordinator,
    validate_auth,
    validate_coordinator,
    validate_kerberos_host_fqdn,
    validate_kerberos_service_name,
    validate_protocol,
)
from query_doctor.impala.hs2_runner import (
    DEFAULT_HTTP_PATH,
    DEFAULT_KERBEROS_SERVICE_NAME,
    Hs2ConnectionSettings,
    Hs2MetadataSession,
    ImpalaDriverUnavailableError,
    ImpalaStatementError,
    ImpalaStatementTimeoutError,
    render_statement_output,
)
from query_doctor.impala.hms_collection import collect_hms_results
from query_doctor.impala.hms_metadata import (
    DEFAULT_HMS_POSTGRES_DSN_ENV,
    DSN_ENV_NAME_RE,
    METADATA_SOURCE_HMS_POSTGRES,
    METADATA_SOURCE_IMPALA,
    METADATA_SOURCES,
    HmsMetadataUnavailableError,
    HmsPostgresMetadataReader,
)
from query_doctor.impala.metadata_output import normalize_output_text
from query_doctor.impala.metadata_policy import (
    ALLOWED_STATEMENTS,
    CollectorError,
    StatementPlan,
    build_statement_plan,
    dedupe_preserve_order,
    normalize_database_identifier,
    normalize_table_identifier,
    validate_read_only_statement,
)
from query_doctor.impala.metadata_results import (
    StatementResult,
    not_applicable_result,
    planned_result,
    write_outputs,
)
from query_doctor.impala.metadata_redaction import redact_impala_context_text
from query_doctor.cli.collect_cm_profiles import (
    ConfigError,
    load_effective_local_config,
)
from query_doctor.config.contract import (
    DEFAULT_CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    QDCREDS_CONFIG_PATH,
    merge_kerberos_cache_env,
)


DEFAULT_TIMEOUT_SEC = 30
DEFAULT_MAX_OUTPUT_BYTES = 262_144
DEFAULT_PROTOCOL = "hs2"
CREATE_VIEW_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", re.IGNORECASE | re.MULTILINE
)
VIEW_NOT_APPLICABLE_RE = re.compile(r"not\s+applicable\s+to\s+a\s+view", re.IGNORECASE)
REPO_DIR = Path(__file__).resolve().parents[2]


def redact_metadata_value(args: argparse.Namespace, value: object) -> str:
    if not args.redact:
        return str(value)
    return redact_impala_context_text(
        value,
        redact_identifiers=getattr(args, "redact_identifiers", True),
        redact_hosts=getattr(args, "redact_hosts", True),
    )


def build_connection_settings(args: argparse.Namespace) -> Hs2ConnectionSettings:
    host, port = split_coordinator(args.coordinator)
    validate_auth(args.auth)
    protocol = validate_protocol(args.protocol) or DEFAULT_PROTOCOL
    if args.ca_cert and not args.ssl:
        raise ImpalaConnectionConfigError("--ca-cert requires --ssl.")
    return Hs2ConnectionSettings(
        host=host,
        port=port,
        kerberos_service_name=validate_kerberos_service_name(args.kerberos_service_name),
        kerberos_host_fqdn=validate_kerberos_host_fqdn(args.kerberos_host_fqdn),
        use_ssl=bool(args.ssl),
        ca_cert=args.ca_cert,
        use_http_transport=protocol == "hs2-http",
        http_path=DEFAULT_HTTP_PATH,
        timeout_sec=args.timeout_sec,
    )


def run_statement(
    args: argparse.Namespace,
    plan: StatementPlan,
    *,
    session: Hs2MetadataSession,
) -> StatementResult:
    validate_read_only_statement(plan.sql, plan.table)
    try:
        rows = session.run(plan.sql, timeout_sec=args.timeout_sec)
    except ImpalaStatementTimeoutError:
        return StatementResult(
            table=plan.table,
            label=plan.label,
            sql=plan.sql,
            status="timeout",
            error=f"statement timed out after {args.timeout_sec}s",
        )
    except ImpalaStatementError as exc:
        message = redact_metadata_value(args, exc)
        if is_view_not_applicable_error("", message):
            return StatementResult(
                table=plan.table,
                label=plan.label,
                sql=plan.sql,
                status="not_applicable",
                error="object is a view",
            )
        stderr_output = normalize_output_text(message)
        return StatementResult(
            table=plan.table,
            label=plan.label,
            sql=plan.sql,
            status="error",
            stderr=stderr_output.text,
            error="the coordinator rejected the statement",
            stderr_raw_bytes=stderr_output.raw_bytes,
            stderr_bytes=stderr_output.bytes,
            stderr_normalized=stderr_output.normalized,
        )
    except OSError as exc:
        return StatementResult(
            table=plan.table,
            label=plan.label,
            sql=plan.sql,
            status="error",
            error=redact_metadata_value(args, exc),
        )

    stdout_output = normalize_output_text(render_statement_output(plan.label, rows))
    size_metadata = {
        "stdout_raw_bytes": stdout_output.raw_bytes,
        "stdout_bytes": stdout_output.bytes,
        "stdout_normalized": stdout_output.normalized,
    }
    if stdout_output.bytes > args.max_output_bytes:
        return StatementResult(
            table=plan.table,
            label=plan.label,
            sql=plan.sql,
            status="too_large",
            error=f"captured output exceeded max-output-bytes ({args.max_output_bytes})",
            **size_metadata,
        )

    return StatementResult(
        table=plan.table,
        label=plan.label,
        sql=plan.sql,
        status="ok",
        stdout=redact_metadata_value(args, stdout_output.text),
        **size_metadata,
    )


def is_create_view_output(text: str) -> bool:
    return bool(CREATE_VIEW_RE.search(text))


def is_view_not_applicable_error(stdout: str, stderr: str) -> bool:
    return bool(VIEW_NOT_APPLICABLE_RE.search(stdout) or VIEW_NOT_APPLICABLE_RE.search(stderr))


def open_metadata_session(args: argparse.Namespace) -> Hs2MetadataSession:
    apply_kerberos_cache_env(args)
    return Hs2MetadataSession(build_connection_settings(args))


def open_hms_reader(args: argparse.Namespace) -> HmsPostgresMetadataReader:
    return HmsPostgresMetadataReader.from_env(
        args.hms_postgres_dsn_env,
        env=os.environ,
        timeout_sec=args.timeout_sec,
    )


def collect_impala_context(
    args: argparse.Namespace,
    *,
    session: Hs2MetadataSession | None = None,
    hms_reader: HmsPostgresMetadataReader | None = None,
) -> int:
    tables = dedupe_preserve_order(normalize_table_identifier(table) for table in args.table)
    plans = build_statement_plan(tables)
    source = getattr(args, "source", METADATA_SOURCE_IMPALA)

    if args.dry_run:
        if source == METADATA_SOURCE_HMS_POSTGRES:
            print("Planned read-only metastore database reads:")
        else:
            print("Planned read-only Impala statements:")
        print_connection_plan(args)
        for plan in plans:
            print(f"- {redact_metadata_value(args, plan.sql)}")
        results = [planned_result(plan) for plan in plans]
    elif source == METADATA_SOURCE_HMS_POSTGRES:
        print(f"Reading table metadata for {len(tables)} table(s) from the metastore database.")
        owned_reader = hms_reader is None
        hms_reader = hms_reader or open_hms_reader(args)
        try:
            results = collect_hms_results(plans, reader=hms_reader)
        finally:
            if owned_reader:
                hms_reader.close()
    else:
        print(f"Collecting read-only Impala metadata for {len(tables)} table(s).")
        owned_session = session is None
        session = session or open_metadata_session(args)
        try:
            results = collect_statements(args, tables, plans, session=session)
        finally:
            if owned_session:
                session.close()

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_dir = Path(args.out)
    write_outputs(out_dir, timestamp=timestamp, tables=tables, results=results, args=args)
    print(f"Wrote Impala context to {out_dir / 'impala_context.md'}")

    failure_statuses = {"error", "timeout", "too_large"}
    return 1 if any(result.status in failure_statuses for result in results) else 0


def collect_statements(
    args: argparse.Namespace,
    tables: list[str],
    plans: list[StatementPlan],
    *,
    session: Hs2MetadataSession,
) -> list[StatementResult]:
    results: list[StatementResult] = []
    for table in tables:
        table_plans = [plan for plan in plans if plan.table == table]
        create_plan = table_plans[0]
        print(f"- {create_plan.label} {create_plan.table}")
        create_result = run_statement(args, create_plan, session=session)
        print(f"  status: {create_result.status}")
        results.append(create_result)
        if create_result.status == "ok" and is_create_view_output(create_result.stdout):
            for plan in table_plans[1:]:
                result = not_applicable_result(plan, "object is a view")
                print(f"- {plan.label} {plan.table}")
                print(f"  status: {result.status}")
                results.append(result)
            continue
        for plan in table_plans[1:]:
            print(f"- {plan.label} {plan.table}")
            result = run_statement(args, plan, session=session)
            print(f"  status: {result.status}")
            results.append(result)
    return results


def print_connection_plan(args: argparse.Namespace) -> None:
    if getattr(args, "source", METADATA_SOURCE_IMPALA) == METADATA_SOURCE_HMS_POSTGRES:
        print(f"- source: {METADATA_SOURCE_HMS_POSTGRES}")
        print(f"- metastore DSN environment variable: {args.hms_postgres_dsn_env}")
        return
    coordinator = (
        redact_metadata_value(args, args.coordinator)
        if args.coordinator
        else "<required for execution>"
    )
    print(f"- coordinator: {coordinator}")
    print(f"- auth: {args.auth}")
    print(f"- protocol: {args.protocol or DEFAULT_PROTOCOL}")
    service_name = args.kerberos_service_name or DEFAULT_KERBEROS_SERVICE_NAME
    unset = "" if args.kerberos_service_name else " (driver default)"
    print(f"- kerberos service name: {service_name}{unset}")
    if args.kerberos_host_fqdn:
        print(f"- kerberos host fqdn: {redact_metadata_value(args, args.kerberos_host_fqdn)}")
    if args.ssl:
        print("- ssl: yes")
    if args.ca_cert:
        print(f"- ca-cert: {redact_metadata_value(args, args.ca_cert)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect explicit read-only Impala table metadata for Query Doctor. "
            "Only SHOW CREATE TABLE, SHOW TABLE STATS, and SHOW COLUMN STATS are planned, "
            "or, with --source hms-postgres, the equivalent read-only metastore database reads."
        )
    )
    parser.add_argument(
        "--config",
        help=(
            "Optional local config with non-secret metadata settings. If omitted, "
            f"{DEFAULT_CONFIG_PATH} is loaded when present, then {QDCREDS_CONFIG_PATH}, "
            f"then legacy {LEGACY_CONFIG_PATH}."
        ),
    )
    parser.add_argument(
        "--table",
        action="append",
        required=True,
        help="Fully qualified table name to inspect, e.g. db.table. May be repeated.",
    )
    parser.add_argument(
        "--table-number",
        action="append",
        type=int,
        help=(
            "With identifier redaction, the 1-based number to name each --table by, "
            "in the same order. Pass it for every --table or not at all."
        ),
    )
    parser.add_argument("--out", required=True, help="Output directory for impala_context.md/json.")
    parser.add_argument(
        "--source",
        choices=METADATA_SOURCES,
        help=(
            "Where table metadata comes from: impala runs the SHOW statements on the "
            "coordinator; hms-postgres reads the Hive Metastore's PostgreSQL database. "
            f"Default: {METADATA_SOURCE_IMPALA}."
        ),
    )
    parser.add_argument(
        "--hms-postgres-dsn-env",
        help=(
            "Name of the environment variable holding the metastore database DSN for "
            f"--source hms-postgres. Default: {DEFAULT_HMS_POSTGRES_DSN_ENV}."
        ),
    )
    parser.add_argument(
        "--coordinator",
        help=(
            "Impala coordinator HOST:PORT for real execution, on its HiveServer2 port. "
            "Not required for --dry-run."
        ),
    )
    parser.add_argument(
        "--auth",
        help="Authentication mode for the coordinator connection. Only kerberos is supported.",
    )
    parser.add_argument(
        "--protocol",
        choices=["hs2", "hs2-http"],
        help=f"HiveServer2 transport. Default: {DEFAULT_PROTOCOL}.",
    )
    parser.add_argument(
        "--kerberos-service-name",
        help=(
            "Kerberos service principal short name, e.g. hive or impala. "
            f"Default: {DEFAULT_KERBEROS_SERVICE_NAME}."
        ),
    )
    parser.add_argument(
        "--kerberos-host-fqdn",
        help=(
            "Expected Kerberos host FQDN for the service principal. Use this when the "
            "network coordinator is a load balancer address but the service principal "
            "uses a DNS hostname."
        ),
    )
    parser.add_argument(
        "--ssl", action="store_true", default=None, help="Use TLS for the coordinator connection."
    )
    parser.add_argument("--ca-cert", help="CA certificate path for --ssl connections.")
    parser.add_argument(
        "--timeout-sec",
        type=int,
        help=f"Timeout per statement in seconds (default: {DEFAULT_TIMEOUT_SEC}).",
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        help=f"Maximum captured output bytes per statement (default: {DEFAULT_MAX_OUTPUT_BYTES}).",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        default=None,
        help="Redact metadata output before writing. Enabled by default.",
    )
    parser.add_argument(
        "--no-redact",
        dest="redact",
        action="store_false",
        help="Write metadata output without redaction.",
    )
    parser.add_argument(
        "--redact-identifiers",
        action="store_true",
        default=None,
        help="Redact database and table identifiers in metadata output.",
    )
    parser.add_argument(
        "--no-redact-identifiers",
        dest="redact_identifiers",
        action="store_false",
        help="Preserve database and table identifiers in local metadata output.",
    )
    parser.add_argument(
        "--redact-hosts",
        action="store_true",
        default=None,
        help="Redact hostnames in metadata output.",
    )
    parser.add_argument(
        "--no-redact-hosts",
        dest="redact_hosts",
        action="store_false",
        help="Preserve hostnames in local metadata output.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without connecting.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        apply_local_config(args, cwd=Path.cwd())
    except ConfigError as exc:
        parser.error(str(exc))
    if args.timeout_sec <= 0:
        parser.error("--timeout-sec must be positive")
    if args.max_output_bytes <= 0:
        parser.error("--max-output-bytes must be positive")
    if not DSN_ENV_NAME_RE.fullmatch(args.hms_postgres_dsn_env):
        parser.error("--hms-postgres-dsn-env must be an uppercase environment variable name")
    try:
        validate_auth(args.auth)
        validate_protocol(args.protocol)
        args.kerberos_service_name = validate_kerberos_service_name(args.kerberos_service_name)
        args.kerberos_host_fqdn = validate_kerberos_host_fqdn(args.kerberos_host_fqdn)
        if args.coordinator:
            args.coordinator = validate_coordinator(args.coordinator)
        elif not args.dry_run and args.source == METADATA_SOURCE_IMPALA:
            parser.error("--coordinator is required unless --dry-run is used")
        if args.ca_cert and not args.ssl:
            parser.error("--ca-cert requires --ssl")
    except ImpalaConnectionConfigError as exc:
        parser.error(str(exc))
    return args


def apply_local_config(args: argparse.Namespace, *, cwd: Path) -> None:
    config_values = load_effective_local_config(
        args.config,
        cwd=cwd,
        repo_root=REPO_DIR,
        use_repo_default=False,
    )

    if config_values.get("metadata_impala_shell"):
        print(
            "note: metadata_impala_shell is no longer used; metadata is collected "
            "over HiveServer2. The setting can be removed from the config.",
            file=sys.stderr,
        )
    args.source = first_string(
        args.source, config_values.get("metadata_source"), METADATA_SOURCE_IMPALA
    )
    if args.source not in METADATA_SOURCES:
        raise ConfigError(
            f"Config field metadata_source must be one of: {', '.join(METADATA_SOURCES)}."
        )
    args.hms_postgres_dsn_env = first_string(
        args.hms_postgres_dsn_env,
        config_values.get("metadata_hms_postgres_dsn_env"),
        DEFAULT_HMS_POSTGRES_DSN_ENV,
    )
    args.coordinator = first_string(args.coordinator, config_values.get("metadata_coordinator"))
    args.auth = first_string(args.auth, config_values.get("metadata_auth"), "kerberos")
    args.protocol = first_string(
        args.protocol, config_values.get("metadata_protocol"), DEFAULT_PROTOCOL
    )
    args.kerberos_service_name = first_string(
        args.kerberos_service_name,
        config_values.get("metadata_kerberos_service_name"),
        config_values.get("impala_kerberos_service_name"),
    )
    args.kerberos_host_fqdn = first_string(
        args.kerberos_host_fqdn,
        config_values.get("metadata_kerberos_host_fqdn"),
    )
    args.ssl = first_bool(args.ssl, config_values.get("metadata_ssl"), default=False)
    args.ca_cert = first_string(args.ca_cert, config_values.get("metadata_ca_cert"))
    args.timeout_sec = first_int(
        args.timeout_sec, config_values.get("metadata_timeout_sec"), default=DEFAULT_TIMEOUT_SEC
    )
    args.max_output_bytes = first_int(
        args.max_output_bytes,
        config_values.get("metadata_max_output_bytes"),
        default=DEFAULT_MAX_OUTPUT_BYTES,
    )
    privacy_mode = first_bool(config_values.get("privacy_mode"), default=True)
    args.redact = first_bool(
        args.redact, config_values.get("metadata_redact"), default=privacy_mode
    )
    args.redact_identifiers = first_bool(
        args.redact_identifiers,
        config_values.get("redact_identifiers"),
        default=privacy_mode,
    )
    args.redact_hosts = first_bool(
        args.redact_hosts,
        config_values.get("redact_hosts"),
        default=privacy_mode,
    )
    args.krb5ccname = first_string(config_values.get("krb5ccname"))
    max_tables = first_int(config_values.get("metadata_max_tables"), default=None)
    if max_tables is not None and len(args.table or []) > max_tables:
        raise ConfigError(
            f"Config field metadata_max_tables allows at most {max_tables} tables for this metadata run."
        )


def first_string(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def first_int(*values: object, default: int | None) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        return int(value)
    return default


def first_bool(*values: object, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        return bool(value)
    return default


def apply_kerberos_cache_env(args: argparse.Namespace) -> None:
    """Point this process at the configured ticket cache.

    The GSSAPI handshake now runs in-process, and libkrb5 reads the cache
    location from KRB5CCNAME only.
    """
    krb5ccname = getattr(args, "krb5ccname", None)
    if not krb5ccname or os.environ.get("KRB5CCNAME"):
        return
    effective = merge_kerberos_cache_env(os.environ, {"krb5ccname": krb5ccname})
    cache = effective.get("KRB5CCNAME")
    if cache:
        os.environ["KRB5CCNAME"] = cache


def main(
    argv: list[str] | None = None,
    *,
    session: Hs2MetadataSession | None = None,
    hms_reader: HmsPostgresMetadataReader | None = None,
) -> int:
    args = parse_args(argv)
    try:
        return collect_impala_context(args, session=session, hms_reader=hms_reader)
    except (
        CollectorError,
        ImpalaConnectionConfigError,
        ImpalaDriverUnavailableError,
        HmsMetadataUnavailableError,
    ) as exc:
        print(f"error: {redact_impala_context_text(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
