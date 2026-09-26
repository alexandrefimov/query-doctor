"""Explicit pipeline workflow for referenced-table Impala metadata collection."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from query_doctor.impala.connection_policy import (
    ImpalaConnectionConfigError,
    validate_auth,
    validate_coordinator,
    validate_kerberos_host_fqdn,
    validate_kerberos_service_name,
    validate_protocol,
)
from query_doctor.impala import hms_metadata, hs2_runner
from query_doctor.impala.hms_metadata import (
    DEFAULT_HMS_POSTGRES_DSN_ENV,
    DSN_ENV_NAME_RE,
    METADATA_SOURCE_HMS_POSTGRES,
    METADATA_SOURCE_IMPALA,
    METADATA_SOURCES,
)
from query_doctor.impala.metadata_policy import (
    CollectorError,
    normalize_database_identifier,
    normalize_table_identifier,
)


DEFAULT_METADATA_MAX_TABLES = 5
DEFAULT_METADATA_TIMEOUT_SEC = 30
DEFAULT_METADATA_MAX_OUTPUT_BYTES = 262_144
DEFAULT_METADATA_AUTH = "kerberos"
DEFAULT_METADATA_PROTOCOL = "hs2"
METADATA_MODES = ("auto", "on", "off", "dry-run")
METADATA_DRIVER_MISSING_REASON = (
    "impala metadata driver is not available; install query-doctor[impala]"
)
REPO_DIR = Path(__file__).resolve().parents[2]
METADATA_SOURCE_TABLES_ENV = "QD_METADATA_SOURCE_TABLES_JSON"
REDACTED_TABLE_PART_RE = re.compile(r"<?table_[0-9]{1,6}>?")
GENERIC_METADATA_IDENTIFIER_PARTS = {
    "<db>",
    "<database>",
    "<schema>",
    "<table>",
}


@dataclass(frozen=True)
class MetadataPlan:
    selected_tables: list[str]
    skipped_tables: list[str]
    invalid_tables: list[str]
    max_tables: int
    default_database: str | None = None
    # 1-based position in the raw list of the first entry that produced each
    # selected table.
    raw_positions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataConfigStatus:
    configured: bool
    reason: str | None = None
    fatal: bool = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def add_metadata_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--metadata-mode",
        choices=METADATA_MODES,
        default="auto",
        help=(
            "Impala metadata collection mode: auto collects only when configured, "
            "on requires collection, off disables it, dry-run prints the plan and exits. "
            "Default: %(default)s."
        ),
    )
    parser.add_argument(
        "--collect-impala-metadata",
        action="store_true",
        help="Legacy alias for --metadata-mode on.",
    )
    parser.add_argument(
        "--metadata-source",
        choices=METADATA_SOURCES,
        default=os.environ.get("QD_METADATA_SOURCE") or METADATA_SOURCE_IMPALA,
        help=(
            "Where table metadata comes from: impala runs SHOW statements on the "
            "coordinator; hms-postgres reads the Hive Metastore's PostgreSQL database. "
            "Default: %(default)s."
        ),
    )
    parser.add_argument(
        "--metadata-hms-postgres-dsn-env",
        default=os.environ.get("QD_METADATA_HMS_POSTGRES_DSN_ENV") or DEFAULT_HMS_POSTGRES_DSN_ENV,
        help=(
            "Environment variable holding the metastore database DSN for "
            "--metadata-source hms-postgres. Default: %(default)s."
        ),
    )
    parser.add_argument(
        "--metadata-coordinator",
        default=os.environ.get("QD_METADATA_COORDINATOR"),
        help=("Impala coordinator HOST:PORT for metadata collection, on its HiveServer2 port."),
    )
    parser.add_argument(
        "--metadata-auth",
        default=os.environ.get("QD_METADATA_AUTH", DEFAULT_METADATA_AUTH),
        help="Metadata collector auth mode. Only kerberos is supported.",
    )
    parser.add_argument(
        "--metadata-protocol",
        choices=["hs2", "hs2-http"],
        default=os.environ.get("QD_METADATA_PROTOCOL", DEFAULT_METADATA_PROTOCOL),
        help="HiveServer2 transport for metadata collection. Default: %(default)s.",
    )
    parser.add_argument(
        "--metadata-kerberos-service-name",
        default=os.environ.get("QD_METADATA_KERBEROS_SERVICE_NAME")
        or os.environ.get("QD_IMPALA_KERBEROS_SERVICE_NAME"),
        help="Kerberos service principal short name, e.g. hive or impala.",
    )
    parser.add_argument(
        "--metadata-kerberos-host-fqdn",
        default=os.environ.get("QD_METADATA_KERBEROS_HOST_FQDN"),
        help="Expected Kerberos host FQDN for load-balanced metadata coordinators.",
    )
    parser.add_argument(
        "--metadata-ssl",
        action="store_true",
        help="Use TLS for the metadata coordinator connection.",
    )
    parser.add_argument(
        "--metadata-ca-cert",
        help="CA certificate path for --metadata-ssl connections.",
    )
    parser.add_argument(
        "--metadata-timeout-sec",
        type=int,
        default=DEFAULT_METADATA_TIMEOUT_SEC,
        help=f"Timeout per metadata statement. Default: {DEFAULT_METADATA_TIMEOUT_SEC}.",
    )
    parser.add_argument(
        "--metadata-max-output-bytes",
        type=int,
        default=None,
        help=f"Maximum captured metadata output bytes. Default: {DEFAULT_METADATA_MAX_OUTPUT_BYTES}.",
    )
    parser.add_argument(
        "--metadata-max-tables",
        type=int,
        default=None,
        help=f"Maximum referenced tables to collect. Default: {DEFAULT_METADATA_MAX_TABLES}.",
    )
    parser.add_argument(
        "--metadata-default-db",
        default=os.environ.get("QD_METADATA_DEFAULT_DB"),
        help=(
            "Default database used to qualify unqualified referenced table names "
            "before metadata collection. When omitted, analyzer facts may provide it."
        ),
    )
    parser.add_argument(
        "--metadata-redact",
        action="store_true",
        default=_env_bool("QD_METADATA_REDACT", True),
        help="Redact metadata output before writing. Enabled by default.",
    )
    parser.add_argument(
        "--metadata-redact-identifiers",
        action="store_true",
        default=_env_bool("QD_METADATA_REDACT_IDENTIFIERS", True),
        help="Redact database and table identifiers in metadata output. Enabled by default.",
    )
    parser.add_argument(
        "--metadata-no-redact-identifiers",
        dest="metadata_redact_identifiers",
        action="store_false",
        help="Preserve database and table identifiers in local metadata output.",
    )
    parser.add_argument(
        "--metadata-redact-hosts",
        action="store_true",
        default=_env_bool("QD_METADATA_REDACT_HOSTS", True),
        help="Redact hostnames in metadata output. Enabled by default.",
    )
    parser.add_argument(
        "--metadata-no-redact-hosts",
        dest="metadata_redact_hosts",
        action="store_false",
        help="Preserve hostnames in local metadata output.",
    )
    parser.add_argument(
        "--metadata-dry-run",
        action="store_true",
        help="Show the bounded metadata collection plan without connecting.",
    )


def validate_metadata_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    effective_mode = resolve_metadata_mode(args)
    _resolve_metadata_int_option(
        parser,
        args,
        attr="metadata_max_output_bytes",
        env_name="QD_METADATA_MAX_OUTPUT_BYTES",
        default=DEFAULT_METADATA_MAX_OUTPUT_BYTES,
        use_env=effective_mode != "off",
    )
    _resolve_metadata_int_option(
        parser,
        args,
        attr="metadata_max_tables",
        env_name="QD_METADATA_MAX_TABLES",
        default=DEFAULT_METADATA_MAX_TABLES,
        use_env=effective_mode != "off",
    )
    if args.metadata_timeout_sec <= 0:
        parser.error("--metadata-timeout-sec must be positive")
    if args.metadata_max_output_bytes <= 0:
        parser.error("--metadata-max-output-bytes must be positive")
    if args.metadata_max_tables <= 0:
        parser.error("--metadata-max-tables must be positive")
    if args.metadata_ca_cert and not args.metadata_ssl:
        parser.error("--metadata-ca-cert requires --metadata-ssl")
    try:
        args.metadata_kerberos_service_name = validate_kerberos_service_name(
            args.metadata_kerberos_service_name
        )
        args.metadata_kerberos_host_fqdn = validate_kerberos_host_fqdn(
            args.metadata_kerberos_host_fqdn
        )
    except ImpalaConnectionConfigError as exc:
        parser.error(str(exc))
    if effective_mode != "off" and args.metadata_default_db:
        try:
            args.metadata_default_db = normalize_database_identifier(args.metadata_default_db)
        except CollectorError as exc:
            parser.error(str(exc))
    if args.metadata_source not in METADATA_SOURCES:
        parser.error(f"--metadata-source must be one of: {', '.join(METADATA_SOURCES)}")
    if not DSN_ENV_NAME_RE.fullmatch(args.metadata_hms_postgres_dsn_env or ""):
        parser.error(
            "--metadata-hms-postgres-dsn-env must be an uppercase environment variable name"
        )
    if args.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
        return
    if effective_mode == "on" and not args.metadata_coordinator:
        parser.error("--metadata-coordinator is required with --metadata-mode on")
    if effective_mode in {"on", "dry-run"} and args.metadata_coordinator:
        try:
            validate_auth(args.metadata_auth)
            validate_protocol(args.metadata_protocol)
            args.metadata_coordinator = validate_coordinator(args.metadata_coordinator)
        except ImpalaConnectionConfigError as exc:
            parser.error(str(exc))
    elif effective_mode == "on":
        try:
            validate_auth(args.metadata_auth)
            validate_protocol(args.metadata_protocol)
        except ImpalaConnectionConfigError as exc:
            parser.error(str(exc))


def _resolve_metadata_int_option(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    attr: str,
    env_name: str,
    default: int,
    use_env: bool,
) -> None:
    explicit_value = getattr(args, attr)
    if explicit_value is not None:
        return
    if not use_env:
        setattr(args, attr, default)
        return
    raw_env_value = os.environ.get(env_name)
    if raw_env_value is None or not raw_env_value.strip():
        setattr(args, attr, default)
        return
    try:
        value = int(raw_env_value)
    except ValueError:
        parser.error(f"{env_name} must be a positive integer; got {raw_env_value!r}")
    if value <= 0:
        parser.error(f"{env_name} must be a positive integer; got {raw_env_value!r}")
    setattr(args, attr, value)


def resolve_metadata_mode(args: argparse.Namespace) -> str:
    if args.metadata_dry_run:
        return "dry-run"
    if args.collect_impala_metadata:
        return "on"
    return args.metadata_mode


def metadata_config_status(args: argparse.Namespace) -> MetadataConfigStatus:
    if getattr(args, "metadata_source", METADATA_SOURCE_IMPALA) == METADATA_SOURCE_HMS_POSTGRES:
        return hms_postgres_config_status(args)
    if not args.metadata_coordinator:
        return MetadataConfigStatus(False, "metadata coordinator is not configured")
    try:
        validate_auth(args.metadata_auth)
        validate_protocol(args.metadata_protocol)
    except ImpalaConnectionConfigError as exc:
        return MetadataConfigStatus(False, str(exc), fatal=True)
    try:
        args.metadata_coordinator = validate_coordinator(args.metadata_coordinator)
    except ImpalaConnectionConfigError as exc:
        return MetadataConfigStatus(False, str(exc), fatal=True)
    if not hs2_runner.driver_available():
        return MetadataConfigStatus(False, METADATA_DRIVER_MISSING_REASON)
    return MetadataConfigStatus(True)


def hms_postgres_config_status(args: argparse.Namespace) -> MetadataConfigStatus:
    dsn_env = args.metadata_hms_postgres_dsn_env
    if not os.environ.get(dsn_env, "").strip():
        return MetadataConfigStatus(
            False, f"metastore database DSN environment variable {dsn_env} is not set"
        )
    if not hms_metadata.driver_available():
        return MetadataConfigStatus(False, hms_metadata.POSTGRES_DRIVER_MISSING_REASON)
    return MetadataConfigStatus(True)


def read_referenced_tables_from_facts(facts_path: Path) -> list[str]:
    if not facts_path.exists():
        return []
    tables: list[str] = []
    in_section = False
    for line in facts_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped == "## Referenced Tables":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section or not stripped.startswith("- "):
            continue
        value = stripped[2:].strip()
        if value.startswith("not_observed"):
            continue
        if value.startswith("`") and value.endswith("`"):
            value = value[1:-1]
        if value and value not in tables:
            tables.append(value)
    return tables


def read_default_database_from_facts(facts_path: Path) -> str | None:
    if not facts_path.exists():
        return None
    in_section = False
    for line in facts_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped == "## SQL Context":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section or not stripped.startswith("- default_database:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if value.startswith("not_observed"):
            return None
        if value.startswith("`") and value.endswith("`"):
            value = value[1:-1]
        return value or None
    return None


def build_metadata_plan(
    raw_tables: list[str],
    max_tables: int,
    *,
    default_database: str | None = None,
) -> MetadataPlan:
    # Normalize, dedupe and cap table names before any collector command is built.
    normalized_default_database: str | None = None
    if default_database:
        try:
            normalized_default_database = normalize_database_identifier(default_database)
        except CollectorError:
            normalized_default_database = None

    normalized: list[str] = []
    invalid: list[str] = []
    raw_positions: dict[str, int] = {}
    for position, table in enumerate(raw_tables, start=1):
        if is_generic_metadata_identifier(table):
            invalid.append(table)
            continue
        try:
            normalized_table = normalize_table_identifier(table)
        except CollectorError:
            if not normalized_default_database:
                invalid.append(table)
                continue
            if is_generic_metadata_identifier(table):
                invalid.append(table)
                continue
            try:
                normalized_table = normalize_table_identifier(
                    f"{normalized_default_database}.{table}"
                )
            except CollectorError:
                invalid.append(table)
                continue
        if normalized_table not in normalized:
            normalized.append(normalized_table)
            raw_positions[normalized_table] = position
    selected = normalized[:max_tables]
    return MetadataPlan(
        selected_tables=selected,
        skipped_tables=normalized[max_tables:],
        invalid_tables=invalid,
        max_tables=max_tables,
        default_database=normalized_default_database,
        raw_positions={table: raw_positions[table] for table in selected},
    )


def is_generic_metadata_identifier(raw_table: str) -> bool:
    """Return true for placeholders that should not become live metadata queries."""
    parts = [
        part.strip().strip("`").lower() for part in raw_table.strip().split(".") if part.strip()
    ]
    if not parts:
        return True
    if any(part in GENERIC_METADATA_IDENTIFIER_PARTS for part in parts):
        return True
    if parts == ["db", "table"]:
        return True
    # A redacted table label, `<db>.<table_N>`, reads as db.table_N once a SQL
    # parser drops the angle brackets.
    if parts[0] == "db" and REDACTED_TABLE_PART_RE.fullmatch(parts[-1]):
        return True
    # `table` is also an Impala keyword. The collector emits unquoted
    # allowlisted SHOW statements, so this shape is not collectable even with a
    # default database and is usually produced by redacted SQL text.
    return parts[-1] == "table"


def build_metadata_collector_cmd(
    args: argparse.Namespace,
    *,
    collector: Path | None = None,
    collector_prefix: list[str] | None = None,
    case_dir: Path,
    tables: list[str],
    table_numbers: list[int] | None = None,
) -> list[str]:
    if collector_prefix is not None:
        cmd = list(collector_prefix)
    elif collector is not None:
        cmd = [
            sys.executable,
            str(collector),
        ]
    else:
        raise ValueError("collector or collector_prefix is required")
    for table in tables:
        cmd.extend(["--table", table])
    if table_numbers is not None and len(table_numbers) == len(tables):
        for number in table_numbers:
            cmd.extend(["--table-number", str(number)])
    if getattr(args, "metadata_source", METADATA_SOURCE_IMPALA) == METADATA_SOURCE_HMS_POSTGRES:
        cmd.extend(
            [
                "--out",
                str(case_dir),
                "--source",
                METADATA_SOURCE_HMS_POSTGRES,
                "--hms-postgres-dsn-env",
                args.metadata_hms_postgres_dsn_env,
                "--timeout-sec",
                str(args.metadata_timeout_sec),
                "--max-output-bytes",
                str(args.metadata_max_output_bytes),
            ]
        )
        append_redaction_args(cmd, args)
        if args.metadata_dry_run:
            cmd.append("--dry-run")
        return cmd
    cmd.extend(
        [
            "--out",
            str(case_dir),
            "--coordinator",
            args.metadata_coordinator,
            "--auth",
            args.metadata_auth,
            "--protocol",
            args.metadata_protocol,
            "--timeout-sec",
            str(args.metadata_timeout_sec),
            "--max-output-bytes",
            str(args.metadata_max_output_bytes),
        ]
    )
    append_redaction_args(cmd, args)
    kerberos_service_name = getattr(args, "metadata_kerberos_service_name", None)
    if kerberos_service_name:
        cmd.extend(["--kerberos-service-name", kerberos_service_name])
    kerberos_host_fqdn = getattr(args, "metadata_kerberos_host_fqdn", None)
    if kerberos_host_fqdn:
        cmd.extend(["--kerberos-host-fqdn", kerberos_host_fqdn])
    if args.metadata_ssl:
        cmd.append("--ssl")
    if args.metadata_ca_cert:
        cmd.extend(["--ca-cert", args.metadata_ca_cert])
    if args.metadata_dry_run:
        cmd.append("--dry-run")
    return cmd


def append_redaction_args(cmd: list[str], args: argparse.Namespace) -> None:
    if args.metadata_redact:
        cmd.append("--redact")
    else:
        cmd.append("--no-redact")
    if getattr(args, "metadata_redact_identifiers", True):
        cmd.append("--redact-identifiers")
    else:
        cmd.append("--no-redact-identifiers")
    if getattr(args, "metadata_redact_hosts", True):
        cmd.append("--redact-hosts")
    else:
        cmd.append("--no-redact-hosts")


def print_metadata_plan(
    plan: MetadataPlan, *, dry_run: bool, redact_identifiers: bool = False
) -> None:
    def display_table(table: str) -> str:
        if not redact_identifiers:
            return table
        if "." in table:
            return "<db>.<table>"
        return "<table>"

    print()
    print("[pipeline] Impala metadata collection plan:")
    if plan.default_database:
        default_database = "<db>" if redact_identifiers else plan.default_database
        print(f"[pipeline] default database for unqualified tables: {default_database}")
    print(f"[pipeline] selected referenced tables: {len(plan.selected_tables)}")
    for table in plan.selected_tables:
        print(f"[pipeline]   collect: {display_table(table)}")
    if plan.skipped_tables:
        print(
            f"[pipeline] skipped due to metadata max tables ({plan.max_tables}): {len(plan.skipped_tables)}"
        )
        for table in plan.skipped_tables:
            print(f"[pipeline]   skip: {display_table(table)}")
    if plan.invalid_tables:
        print(f"[pipeline] skipped malformed referenced tables: {len(plan.invalid_tables)}")
        for table in plan.invalid_tables:
            print(f"[pipeline]   invalid: {display_table(table)}")
    if dry_run:
        print("[pipeline] metadata dry-run requested; no coordinator connection will open")
