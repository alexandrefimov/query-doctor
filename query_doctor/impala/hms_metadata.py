"""Impala table metadata read from the Hive Metastore's PostgreSQL database.

`SHOW TABLE STATS` and its siblings make catalogd load a table it has not loaded
yet, and loading an HDFS table lists every partition directory. The facts the
analyzer uses -- row counts, partition row-count coverage, partition columns,
column-stats presence and storage scheme -- are metastore rows, so this source
reads them straight from the metastore database and never reaches Impala or the
file system.

Everything here is read-only: the session is opened with
`default_transaction_read_only=on`, and every query is a parameterized SELECT
against the metastore schema tables named below.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from query_doctor.impala.metadata_policy import normalize_table_identifier
from query_doctor.impala.table_metadata_facts import storage_family_for_scheme


METADATA_SOURCE_IMPALA = "impala"
METADATA_SOURCE_HMS_POSTGRES = "hms-postgres"
METADATA_SOURCES = (METADATA_SOURCE_IMPALA, METADATA_SOURCE_HMS_POSTGRES)
DEFAULT_HMS_POSTGRES_DSN_ENV = "QUERY_DOCTOR_HMS_POSTGRES_DSN"
DSN_ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
POSTGRES_DRIVER_MISSING_REASON = (
    "metastore database driver is not available; install query-doctor[postgres]"
)
APPLICATION_NAME = "query-doctor-metadata"
# Bounds the column query; a wider table is reported with incomplete column stats.
MAX_COLUMNS = 5000
QUERY_CANCELED_SQLSTATE = "57014"
TABLE_PARAM_KEYS = (
    "numRows",
    "totalSize",
    "impala.lastComputeStatsTime",
    "storage_handler",
    "table_type",
)
VIEW_TABLE_TYPES = {"VIRTUAL_VIEW", "MATERIALIZED_VIEW"}
# Impala knows the width of these types without statistics, so SHOW COLUMN STATS
# reports their Max/Avg Size even for a column that was never analyzed.
FIXED_LENGTH_TYPE_RE = re.compile(
    r"^(?:boolean|tinyint|smallint|int|integer|bigint|float|double|date|timestamp|"
    r"char|decimal)\b",
    re.IGNORECASE,
)
COMPLEX_TYPE_RE = re.compile(r"^(?:array|map|struct|uniontype)\s*<", re.IGNORECASE)
INPUT_FORMAT_NAMES = (
    ("parquet", "PARQUET"),
    ("orc", "ORC"),
    ("avro", "AVRO"),
    ("sequencefile", "SEQUENCEFILE"),
    ("rcfile", "RCFILE"),
    ("textinputformat", "TEXTFILE"),
)
NON_NEGATIVE_INTEGER_RE = re.compile(r"^[0-9]+$")

TABLE_SQL = """
SELECT t."TBL_ID", t."TBL_TYPE", s."LOCATION", s."INPUT_FORMAT", s."CD_ID"
FROM "TBLS" t
JOIN "DBS" d ON d."DB_ID" = t."DB_ID"
LEFT JOIN "SDS" s ON s."SD_ID" = t."SD_ID"
WHERE d."NAME" = %s AND t."TBL_NAME" = %s
"""
TABLE_PARAMS_SQL = """
SELECT "PARAM_KEY", "PARAM_VALUE"
FROM "TABLE_PARAMS"
WHERE "TBL_ID" = %s AND "PARAM_KEY" = ANY(%s)
"""
PARTITION_KEYS_SQL = """
SELECT "PKEY_NAME"
FROM "PARTITION_KEYS"
WHERE "TBL_ID" = %s
ORDER BY "INTEGER_IDX"
"""
PARTITION_SUMMARY_SQL = """
SELECT
  count(*),
  count(*) FILTER (WHERE row_count."PARAM_VALUE" ~ '^[0-9]+$'),
  count(*) FILTER (WHERE row_count."PARAM_VALUE" ~ '^0+$'),
  count(*) FILTER (WHERE total_size."PARAM_VALUE" ~ '^[0-9]+$'),
  coalesce(sum(CASE WHEN total_size."PARAM_VALUE" ~ '^[0-9]+$'
                    THEN total_size."PARAM_VALUE"::numeric END), 0)
FROM "PARTITIONS" p
LEFT JOIN "PARTITION_PARAMS" row_count
  ON row_count."PART_ID" = p."PART_ID" AND row_count."PARAM_KEY" = 'numRows'
LEFT JOIN "PARTITION_PARAMS" total_size
  ON total_size."PART_ID" = p."PART_ID" AND total_size."PARAM_KEY" = 'totalSize'
WHERE p."TBL_ID" = %s
"""
COLUMNS_SQL = """
SELECT
  c."COLUMN_NAME", c."TYPE_NAME", s."CS_ID" IS NOT NULL,
  s."NUM_DISTINCTS", s."NUM_NULLS", s."AVG_COL_LEN", s."MAX_COL_LEN",
  s."NUM_TRUES", s."NUM_FALSES"
FROM "COLUMNS_V2" c
LEFT JOIN "TAB_COL_STATS" s
  ON s."TBL_ID" = %s AND s."COLUMN_NAME" = c."COLUMN_NAME"
WHERE c."CD_ID" = %s
ORDER BY c."INTEGER_IDX"
LIMIT %s
"""

ConnectFactory = Callable[..., Any]


class HmsMetadataError(Exception):
    """A metastore read failure whose message is safe to print and persist."""


class HmsMetadataUnavailableError(HmsMetadataError):
    """The metastore source cannot run at all: no driver or no DSN."""


class HmsMetadataTimeoutError(HmsMetadataError):
    """A metastore query ran past the statement timeout."""


class HmsMetadataConnectionError(HmsMetadataError):
    """The metastore database refused or never answered the connection."""


@dataclass(frozen=True)
class HmsColumn:
    name: str
    type_name: str
    has_stats: bool = False
    num_distincts: int | None = None
    num_nulls: int | None = None
    avg_col_len: float | None = None
    max_col_len: int | None = None
    num_trues: int | None = None
    num_falses: int | None = None


@dataclass(frozen=True)
class HmsTableSnapshot:
    table_type: str
    location: str = ""
    input_format: str = ""
    table_params: Mapping[str, str] | None = None
    partition_keys: tuple[str, ...] = ()
    partition_count: int = 0
    partitions_with_known_row_count: int = 0
    partitions_with_zero_row_count: int = 0
    partitions_with_known_size: int = 0
    partition_total_size: int = 0
    columns: tuple[HmsColumn, ...] = ()
    columns_truncated: bool = False

    @property
    def is_view(self) -> bool:
        return self.table_type.upper() in VIEW_TABLE_TYPES

    def param(self, key: str) -> str | None:
        value = (self.table_params or {}).get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None


def driver_available() -> bool:
    return importlib.util.find_spec("psycopg") is not None


class HmsPostgresMetadataReader:
    """Reads one table at a time over a single read-only connection."""

    def __init__(
        self,
        dsn: str,
        *,
        timeout_sec: int,
        connect: ConnectFactory | None = None,
    ) -> None:
        normalized = dsn.strip()
        if not normalized:
            raise HmsMetadataUnavailableError("metastore database DSN is empty")
        self._dsn = normalized
        self._timeout_sec = timeout_sec
        self._connect_factory = connect
        self._connection: Any = None

    @classmethod
    def from_env(
        cls,
        dsn_env: str,
        *,
        env: Mapping[str, str],
        timeout_sec: int,
        connect: ConnectFactory | None = None,
    ) -> "HmsPostgresMetadataReader":
        dsn = env.get(dsn_env)
        if not dsn or not dsn.strip():
            raise HmsMetadataUnavailableError(
                f"metastore database DSN environment variable {dsn_env} is not set"
            )
        return cls(dsn, timeout_sec=timeout_sec, connect=connect)

    def read_table(self, table: str) -> HmsTableSnapshot | None:
        """Return the table's metastore rows, or None when the metastore has no such table."""
        database, name = normalize_table_identifier(table).lower().split(".", 1)
        table_row = self._fetchone(TABLE_SQL, (database, name))
        if table_row is None:
            return None
        tbl_id, table_type, location, input_format, cd_id = table_row
        params = {
            str(key): str(value)
            for key, value in self._fetchall(TABLE_PARAMS_SQL, (tbl_id, list(TABLE_PARAM_KEYS)))
            if key is not None and value is not None
        }
        partition_keys = tuple(
            str(row[0]) for row in self._fetchall(PARTITION_KEYS_SQL, (tbl_id,)) if row[0]
        )
        summary = (0, 0, 0, 0, 0)
        if partition_keys:
            summary = self._fetchone(PARTITION_SUMMARY_SQL, (tbl_id,)) or summary
        columns: tuple[HmsColumn, ...] = ()
        truncated = False
        if cd_id is not None:
            rows = self._fetchall(COLUMNS_SQL, (tbl_id, cd_id, MAX_COLUMNS + 1))
            truncated = len(rows) > MAX_COLUMNS
            columns = dedupe_columns(column_from_row(row) for row in rows[:MAX_COLUMNS])
        return HmsTableSnapshot(
            table_type=str(table_type or ""),
            location=str(location or ""),
            input_format=str(input_format or ""),
            table_params=params,
            partition_keys=partition_keys,
            partition_count=int(summary[0] or 0),
            partitions_with_known_row_count=int(summary[1] or 0),
            partitions_with_zero_row_count=int(summary[2] or 0),
            partitions_with_known_size=int(summary[3] or 0),
            partition_total_size=int(summary[4] or 0),
            columns=columns,
            columns_truncated=truncated,
        )

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - closing is best effort.
            pass

    def _fetchone(self, sql: str, params: tuple[Any, ...]) -> Any:
        rows = self._fetchall(sql, params)
        return rows[0] if rows else None

    def _fetchall(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        connection = self._open()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        except Exception as exc:  # noqa: BLE001 - driver errors carry the DSN and host.
            if getattr(exc, "sqlstate", None) == QUERY_CANCELED_SQLSTATE:
                raise HmsMetadataTimeoutError(
                    f"metastore query timed out after {self._timeout_sec}s"
                ) from None
            raise HmsMetadataError("metastore database query failed") from None

    def _open(self) -> Any:
        if self._connection is not None:
            return self._connection
        options = {
            "connect_timeout": max(1, self._timeout_sec),
            "application_name": APPLICATION_NAME,
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={max(1, self._timeout_sec) * 1000}"
            ),
            "autocommit": True,
        }
        try:
            if self._connect_factory is not None:
                self._connection = self._connect_factory(self._dsn, **options)
            else:
                try:
                    import psycopg
                except ImportError:
                    raise HmsMetadataUnavailableError(POSTGRES_DRIVER_MISSING_REASON) from None
                self._connection = psycopg.connect(self._dsn, **options)
        except HmsMetadataUnavailableError:
            raise
        except Exception:  # noqa: BLE001 - driver errors carry the DSN and host.
            raise HmsMetadataConnectionError(
                "could not connect to the metastore database"
            ) from None
        return self._connection


def column_from_row(row: Any) -> HmsColumn:
    name, type_name, has_stats, ndv, nulls, avg_len, max_len, trues, falses = row
    return HmsColumn(
        name=str(name or ""),
        type_name=str(type_name or ""),
        has_stats=bool(has_stats),
        num_distincts=optional_int(ndv),
        num_nulls=optional_int(nulls),
        avg_col_len=optional_float(avg_len),
        max_col_len=optional_int(max_len),
        num_trues=optional_int(trues),
        num_falses=optional_int(falses),
    )


def dedupe_columns(columns: Any) -> tuple[HmsColumn, ...]:
    # The metastore keeps a statistics row per engine; one row per column is enough
    # to tell whether statistics exist.
    seen: dict[str, HmsColumn] = {}
    for column in columns:
        current = seen.get(column.name)
        if current is None or (column.has_stats and not current.has_stats):
            seen[column.name] = column
    return tuple(seen.values())


def optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def show_create_facts(snapshot: HmsTableSnapshot) -> dict[str, Any]:
    facts: dict[str, Any] = {"object_type": "view" if snapshot.is_view else "table"}
    if snapshot.is_view:
        return facts
    file_format = file_format_for(snapshot)
    if file_format:
        facts["file_format"] = file_format
    scheme = location_scheme(snapshot.location)
    if scheme:
        facts["storage_scheme"] = scheme
        facts["storage_family"] = storage_family_for_scheme(scheme)
    if snapshot.partition_keys:
        facts["partition_columns"] = list(snapshot.partition_keys[:20])
    return facts


def table_stats_facts(snapshot: HmsTableSnapshot) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    num_rows = snapshot.param("numRows")
    if num_rows is not None and NON_NEGATIVE_INTEGER_RE.fullmatch(num_rows):
        facts["table_rows"] = int(num_rows)
        facts["table_stats_row_count_completeness"] = "available"
    else:
        facts["table_rows"] = "unknown"
        facts["table_stats_row_count_completeness"] = "missing/unknown"

    if snapshot.partition_keys:
        facts["partition_count"] = snapshot.partition_count
        facts["partitions_with_known_row_count"] = snapshot.partitions_with_known_row_count
        facts["partitions_with_unknown_row_count"] = (
            snapshot.partition_count - snapshot.partitions_with_known_row_count
        )
        facts["partitions_with_zero_row_count"] = snapshot.partitions_with_zero_row_count
        if snapshot.partition_count and (
            snapshot.partitions_with_known_size == snapshot.partition_count
        ):
            facts["table_size"] = format_bytes(snapshot.partition_total_size)
    else:
        total_size = snapshot.param("totalSize")
        if total_size is not None and NON_NEGATIVE_INTEGER_RE.fullmatch(total_size):
            facts["table_size"] = format_bytes(int(total_size))

    computed_at = epoch_seconds_to_iso(snapshot.param("impala.lastComputeStatsTime"))
    if computed_at:
        facts["table_stats_last_computed"] = computed_at
    return facts


def column_stats_facts(snapshot: HmsTableSnapshot) -> dict[str, Any]:
    statuses: list[tuple[str, str]] = []
    missing_markers = 0
    for column in snapshot.columns:
        if COMPLEX_TYPE_RE.match(column.type_name):
            # COMPUTE STATS never produces statistics for nested types.
            continue
        status, missing = regular_column_status(column)
        statuses.append((column.name, status))
        missing_markers += missing
    # Impala lists partition columns last and derives their NDV and null count
    # from the partition list, so they never lack statistics.
    statuses.extend((name, "complete") for name in snapshot.partition_keys)

    counts = {status: 0 for status in ("complete", "ndv_missing", "size_missing", "all_missing")}
    for _, status in statuses:
        counts[status] += 1
    complete = (
        bool(statuses) and not snapshot.columns_truncated and (counts["complete"] == len(statuses))
    )
    return {
        "column_stats_columns_observed": len(statuses),
        "column_stats_missing_markers": missing_markers,
        "column_stats_completeness": "complete" if complete else "incomplete/unknown",
        "column_stats_columns": [name for name, _ in statuses[:20]],
        "column_stats_per_column": dict(statuses[:20]),
        "column_stats_complete_columns": counts["complete"],
        "column_stats_ndv_missing_columns": counts["ndv_missing"],
        "column_stats_size_missing_columns": counts["size_missing"],
        "column_stats_all_missing_columns": counts["all_missing"],
    }


def regular_column_status(column: HmsColumn) -> tuple[str, int]:
    """Classify one non-partition column the way SHOW COLUMN STATS would show it."""
    fixed_length = bool(FIXED_LENGTH_TYPE_RE.match(column.type_name))
    if not column.has_stats:
        return "all_missing", 2 if fixed_length else 3
    if column.type_name.lower().startswith("boolean"):
        # Impala derives a boolean column's NDV from its true and false counts.
        ndv_known = column.num_trues is not None and column.num_falses is not None
    else:
        ndv_known = column.num_distincts is not None and column.num_distincts >= 0
    nulls_known = column.num_nulls is not None and column.num_nulls >= 0
    size_known = fixed_length or (
        column.avg_col_len is not None
        and column.avg_col_len >= 0
        and column.max_col_len is not None
        and column.max_col_len >= 0
    )
    missing = sum(1 for known in (ndv_known, nulls_known, size_known) if not known)
    if not ndv_known:
        return "ndv_missing", missing
    if not size_known:
        return "size_missing", missing
    return "complete", missing


def file_format_for(snapshot: HmsTableSnapshot) -> str | None:
    table_type = (snapshot.param("table_type") or "").upper()
    if table_type == "ICEBERG":
        return "ICEBERG"
    handler = (snapshot.param("storage_handler") or "").lower()
    if "kudu" in handler:
        return "KUDU"
    if "hbase" in handler:
        return "HBASE"
    input_format = snapshot.input_format.lower()
    for marker, name in INPUT_FORMAT_NAMES:
        if marker in input_format:
            return name
    return None


def location_scheme(location: str) -> str | None:
    match = re.match(r"\s*([A-Za-z][A-Za-z0-9+.-]*)://", location or "")
    return match.group(1).lower() if match else None


def format_bytes(value: int) -> str:
    """Render bytes the way Impala's SHOW TABLE STATS does: binary units, decimal names."""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.2f}{unit}"
        size /= 1024
    return f"{value}B"


def epoch_seconds_to_iso(value: str | None) -> str | None:
    if value is None or not NON_NEGATIVE_INTEGER_RE.fullmatch(value):
        return None
    seconds = int(value)
    if seconds <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def summary_text(label: str, facts: Mapping[str, Any]) -> str:
    """Short human-readable record of what was read, for impala_context.md."""
    lines = [f"source: hive metastore database ({label} equivalent)"]
    for key in sorted(facts):
        value = facts[key]
        if isinstance(value, dict):
            value = ", ".join(f"{name}={status}" for name, status in value.items())
        elif isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
