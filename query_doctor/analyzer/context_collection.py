"""Case context collection helpers for deterministic analyzer runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from query_doctor.case_metadata import existing_query_metadata_path
from query_doctor.analyzer.context_files import (
    context_file_status,
    context_table_file_status,
    extract_context_warnings,
    read_referenced_context_tables,
    rel_path,
)
from query_doctor.analyzer.scalars import fmt_duration, numeric_context_value
from query_doctor.analyzer.sql_sources import (
    extract_join_filter_column_references_from_sql,
    extract_referenced_tables_from_sql,
    sql_inputs_for_case,
)
from query_doctor.cluster.context import MAX_CONTEXT_SOURCES
from query_doctor.cluster.event_context import (
    ALLOWED_CONTEXT_STATUSES,
    ALLOWED_PRODUCT_STATUSES,
    safe_count_map,
    safe_limitations,
    safe_signals,
    safe_token,
    safe_window,
)
from query_doctor.safety.redaction import sanitize_text_for_log

COLUMN_STATS_JOIN_FILTER_STATUSES = (
    "complete",
    "ndv_missing",
    "size_missing",
    "all_missing",
    "unknown",
)
IMPALA_DAEMON_PROFILE_SOURCE = "impala_daemon"
IMPALA_DAEMON_PROFILE_SOURCE_LABEL = "Impala daemon profile endpoint"
MANUAL_PROFILE_TEXT_SOURCE = "manual_profile_text"
MANUAL_PROFILE_TEXT_SOURCE_LABEL = "Local exported Impala text profile"
SAFE_PROFILE_SOURCE_LABELS = {
    IMPALA_DAEMON_PROFILE_SOURCE: IMPALA_DAEMON_PROFILE_SOURCE_LABEL,
    MANUAL_PROFILE_TEXT_SOURCE: MANUAL_PROFILE_TEXT_SOURCE_LABEL,
}


def build_query_wall_clock(
    totals: dict[str, dict[str, Any] | None],
    cm_query_context: dict[str, Any] | None = None,
    query_timeline_ms: float | None = None,
) -> dict[str, Any]:
    cm_duration_ms = numeric_context_value(cm_query_context or {}, "duration_ms")
    if cm_duration_ms is not None and cm_duration_ms > 0:
        source = "CM Query Context"
        profile_source = (cm_query_context or {}).get("profile_source")
        if profile_source in SAFE_PROFILE_SOURCE_LABELS:
            source = SAFE_PROFILE_SOURCE_LABELS[profile_source]
        return {
            "duration_ms": cm_duration_ms,
            "duration_human": fmt_duration(cm_duration_ms),
            "source": source,
            "confidence": "high",
        }

    if isinstance(query_timeline_ms, (int, float)) and query_timeline_ms > 0:
        return {
            "duration_ms": float(query_timeline_ms),
            "duration_human": fmt_duration(float(query_timeline_ms)),
            "source": "profile Query Timeline",
            "confidence": "medium",
        }

    profile_total = totals.get("TotalTime") or {}
    profile_total_ms = profile_total.get("ms")
    if isinstance(profile_total_ms, (int, float)) and profile_total_ms > 0:
        return {
            "duration_ms": float(profile_total_ms),
            "duration_human": fmt_duration(float(profile_total_ms)),
            "source": "profile TotalTime",
            "confidence": "medium",
        }

    return {
        "duration_ms": None,
        "duration_human": "unknown",
        "source": "unknown",
        "confidence": "unknown",
    }


def collect_referenced_tables(case_dir: Path, profile_text: str) -> list[str]:
    tables: set[str] = set()
    tables.update(
        read_referenced_context_tables(case_dir / "impala_context" / "referenced_tables.txt")
    )
    for sql in sql_inputs_for_case(case_dir, profile_text):
        tables.update(extract_referenced_tables_from_sql(sql))
    return sorted(tables, key=lambda value: value.lower())


def collect_sql_column_context(
    case_dir: Path,
    profile_text: str,
    table_metadata_context: dict[str, Any] | None,
) -> dict[str, Any]:
    references: set[tuple[str, str]] = set()
    for sql in sql_inputs_for_case(case_dir, profile_text):
        references.update(extract_join_filter_column_references_from_sql(sql))
    metadata = metadata_columns_by_table(table_metadata_context or {})
    observed = len(references)
    if not references:
        return {
            "status": "unknown",
            "join_filter_columns_observed": 0,
            "join_filter_columns_with_stats": 0,
            "join_filter_columns_without_stats": 0,
            "join_filter_columns_with_complete_stats": 0,
            "join_filter_columns_with_ndv_missing_stats": 0,
            "join_filter_columns_with_size_missing_stats": 0,
            "join_filter_columns_with_all_missing_stats": 0,
            "join_filter_columns_with_unknown_stats": 0,
            "join_filter_partition_columns": 0,
            "join_filter_column_relevance": "unknown",
        }

    with_stats = 0
    partition_columns = 0
    status_counts = {status: 0 for status in COLUMN_STATS_JOIN_FILTER_STATUSES}
    for table, column in references:
        table_key = metadata_table_key(table)
        column_key = column.lower()
        table_metadata = metadata.get(table_key, {})
        column_status = join_filter_column_stats_status(table_metadata, column_key)
        status_counts[column_status] += 1
        if column_status == "complete":
            with_stats += 1
        if column_key in table_metadata.get("partition_columns", set()):
            partition_columns += 1
    without_stats = max(0, observed - with_stats)
    if with_stats == observed:
        relevance = "covered"
    elif with_stats > 0:
        relevance = "partial"
    else:
        relevance = "missing"
    return {
        "status": "available",
        "join_filter_columns_observed": observed,
        "join_filter_columns_with_stats": with_stats,
        "join_filter_columns_without_stats": without_stats,
        "join_filter_columns_with_complete_stats": status_counts["complete"],
        "join_filter_columns_with_ndv_missing_stats": status_counts["ndv_missing"],
        "join_filter_columns_with_size_missing_stats": status_counts["size_missing"],
        "join_filter_columns_with_all_missing_stats": status_counts["all_missing"],
        "join_filter_columns_with_unknown_stats": status_counts["unknown"],
        "join_filter_partition_columns": partition_columns,
        "join_filter_column_relevance": relevance,
    }


def metadata_columns_by_table(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tables = context.get("tables")
    if not isinstance(tables, list):
        return {}
    result: dict[str, dict[str, set[str]]] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = metadata_table_key(str(table.get("table") or ""))
        if not table_name:
            continue
        result[table_name] = {
            "column_stats": normalized_name_set(table.get("column_stats_columns")),
            "column_stats_per_column": normalized_status_map(table.get("column_stats_per_column")),
            "partition_columns": normalized_name_set(table.get("partition_columns")),
        }
    return result


def metadata_table_key(table: str) -> str:
    """Key a table the same way in SQL references and metadata.

    SQL parsing drops the angle brackets of a redacted label, so
    `<db>.<table_2>` in metadata meets `db.table_2` in the SQL.
    """
    return table.strip().lower().replace("<", "").replace(">", "")


def normalized_name_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def normalized_status_map(values: Any) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    allowed = {"complete", "ndv_missing", "size_missing", "all_missing"}
    result: dict[str, str] = {}
    for key, value in values.items():
        column = str(key or "").strip().lower()
        status = str(value or "").strip().lower()
        if column and status in allowed:
            result[column] = status
    return result


def join_filter_column_stats_status(table_metadata: dict[str, Any], column_key: str) -> str:
    per_column = table_metadata.get("column_stats_per_column")
    if isinstance(per_column, dict) and column_key in per_column:
        return str(per_column[column_key])
    if column_key in table_metadata.get("column_stats", set()):
        return "complete"
    return "unknown"


def collect_impala_context(case_dir: Path) -> dict[str, Any] | None:
    context_dir = case_dir / "impala_context"
    summary_path = context_dir / "impala_context.md"
    if not summary_path.exists():
        return None

    original_query_path = context_dir / "original_query.sql"
    referenced_tables_path = context_dir / "referenced_tables.txt"
    explain_path = context_dir / "explain.txt"
    tables = read_referenced_context_tables(referenced_tables_path)

    return {
        "context_dir": rel_path(context_dir, case_dir),
        "summary": context_file_status(summary_path, case_dir),
        "original_sql": context_file_status(original_query_path, case_dir),
        "referenced_tables_file": context_file_status(referenced_tables_path, case_dir),
        "referenced_tables": tables,
        "explain": context_file_status(explain_path, case_dir),
        "table_metadata": {
            table: context_table_file_status(context_dir, case_dir, table) for table in tables
        },
        "warnings": extract_context_warnings(summary_path),
    }


CM_QUERY_CONTEXT_FIELDS = (
    "query_id",
    "status",
    "query_state",
    "query_type",
    "pool",
    "start_time",
    "end_time",
    "duration_ms",
    "admission_result",
    "admission_wait_ms",
    "admission_wait",
    "resources_reserved_wait_time",
    "rows_produced",
    "bytes_read",
    "bytes_sent",
    "memory_aggregate_peak",
    "memory_per_node_peak",
    "impala_daemon_product",
    "impala_daemon_version",
    "impala_daemon_version_label",
    "impala_daemon_build_type",
    "impala_daemon_server_mode",
    "impala_daemon_local_catalog_mode",
    "profile_response_format",
    "profile_fetch_attempt_count",
    "profile_json_probe_enabled",
    "profile_docs_probe_enabled",
    "profile_docs_fetch_attempt_count",
    "admission_context_probe_enabled",
    "admission_context_fetch_attempt_count",
)
UNSAFE_CLUSTER_TEXT_RE = re.compile(
    r"(/[^ \n\t]+|[A-Za-z]:\\|https?://|RAW_[A-Z0-9_]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)"
)
ADMISSION_CONTEXT_FILENAME = "admission_context.json"
ADMISSION_CONTEXT_GUARDRAIL = (
    "Admission context is bounded aggregate evidence only. It cannot promote "
    "runtime_admission without selected-query admission wait or result evidence."
)
ALLOWED_ADMISSION_STATUS = {"available", "unavailable"}
ALLOWED_ADMISSION_SOURCE = {"impala_admission_debug"}
ALLOWED_ADMISSION_SCOPE = {
    "selected_pool",
    "all_pools_selected_pool_not_found",
    "all_pools",
    "unknown",
}
ALLOWED_ADMISSION_YES_NO_UNKNOWN = {"yes", "no", "unknown"}
ALLOWED_ADMISSION_COUNT_BUCKET = {"none", "1", "2_4", "5_9", "10_plus", "unknown"}
ALLOWED_ADMISSION_DURATION_BUCKET = {
    "none",
    "lt_1s",
    "1s_5s",
    "5s_30s",
    "30s_plus",
    "unknown",
}
ALLOWED_ADMISSION_PRESSURE = {"low", "medium", "high", "unknown"}
ALLOWED_ADMISSION_FRESHNESS = {"fresh", "stale", "unknown"}
ALLOWED_ADMISSION_REASON = {
    "request_failed",
    "response_too_large",
    "invalid_json",
    "no_pool_entries",
}
ALLOWED_ADMISSION_LIMITATIONS = {
    "Admission debug context is aggregate pool context. It must not promote runtime_admission without selected-query admission wait or result evidence.",
    "Selected-query pool was not found in the admission debug context; only all-pool aggregate context is available.",
    "Admission debug context may be stale according to the safe statestore freshness signal.",
    "Admission debug context did not expose a safe queue depth or queue-time aggregate.",
    "Impala admission debug context was unavailable or unmapped; keep pool/admission context unknown.",
}


def collect_cm_query_context(case_dir: Path) -> dict[str, Any] | None:
    metadata_path = existing_query_metadata_path(case_dir)
    if metadata_path is None:
        return None
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {"available": False, "error": "failed to parse query metadata"}
    if not isinstance(raw, dict):
        return {"available": False, "error": "query metadata is not an object"}

    context = {
        field: raw.get(field) for field in CM_QUERY_CONTEXT_FIELDS if raw.get(field) is not None
    }
    profile_source = raw.get("profile_source")
    if profile_source in SAFE_PROFILE_SOURCE_LABELS:
        context["profile_source"] = profile_source
        context["source_label"] = SAFE_PROFILE_SOURCE_LABELS[profile_source]
    context["available"] = bool(context)
    return context


def collect_cm_timeseries_context(case_dir: Path) -> dict[str, Any] | None:
    context_path = case_dir / "runtime_metrics_context.json"
    if not context_path.exists():
        context_path = case_dir / "cm_timeseries_context.json"
    if not context_path.exists():
        return None
    try:
        raw = json.loads(context_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {"available": False, "error": "failed to parse runtime metrics context"}
    if not isinstance(raw, dict):
        return {"available": False, "error": "runtime metrics context is not an object"}
    queries = raw.get("queries")
    if not isinstance(queries, list):
        return {"available": False, "error": "runtime metrics context query list is missing"}
    return {
        "available": bool(raw.get("available")),
        "source": safe_token(raw.get("source"), default="cm_timeseries"),
        "source_label": safe_runtime_metrics_source_label(
            raw.get("source_label"), raw.get("source")
        ),
        "metrics_profile": raw.get("metrics_profile")
        if isinstance(raw.get("metrics_profile"), str)
        else None,
        "window": raw.get("window") if isinstance(raw.get("window"), dict) else {},
        "limits": raw.get("limits") if isinstance(raw.get("limits"), dict) else {},
        "queries": [query for query in queries if isinstance(query, dict)],
        "warnings": [
            sanitize_text_for_log(warning)
            for warning in raw.get("warnings", [])
            if isinstance(warning, str)
        ][:5],
    }


def collect_admission_context(case_dir: Path) -> dict[str, Any] | None:
    context_path = case_dir / ADMISSION_CONTEXT_FILENAME
    if not context_path.exists():
        return None
    try:
        raw = json.loads(context_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return unavailable_safe_admission_context("invalid_json")
    if not isinstance(raw, dict):
        return unavailable_safe_admission_context("invalid_json")
    return sanitize_admission_context(raw)


def sanitize_admission_context(raw: dict[str, Any]) -> dict[str, Any]:
    """Whitelist direct Impala admission aggregate context before rendering."""

    status = safe_token(raw.get("status"), default="unavailable")
    if status not in ALLOWED_ADMISSION_STATUS:
        status = "unavailable"
    source = safe_token(raw.get("source"), default="impala_admission_debug")
    if source not in ALLOWED_ADMISSION_SOURCE:
        source = "impala_admission_debug"
    available = bool(raw.get("available")) and status == "available"
    context: dict[str, Any] = {
        "schema_version": 1,
        "available": available,
        "status": status,
        "source": source,
        "source_label": "Impala admission debug endpoint",
        "scope": safe_admission_token(raw.get("scope"), ALLOWED_ADMISSION_SCOPE),
        "pool_count": nonnegative_count(raw.get("pool_count")),
        "matched_pool_count": nonnegative_count(raw.get("matched_pool_count")),
        "queue_present": safe_admission_token(
            raw.get("queue_present"), ALLOWED_ADMISSION_YES_NO_UNKNOWN
        ),
        "running_present": safe_admission_token(
            raw.get("running_present"), ALLOWED_ADMISSION_YES_NO_UNKNOWN
        ),
        "queued_pool_count": nonnegative_count(raw.get("queued_pool_count")),
        "running_pool_count": nonnegative_count(raw.get("running_pool_count")),
        "max_queue_depth_bucket": safe_admission_token(
            raw.get("max_queue_depth_bucket"), ALLOWED_ADMISSION_COUNT_BUCKET
        ),
        "max_running_bucket": safe_admission_token(
            raw.get("max_running_bucket"), ALLOWED_ADMISSION_COUNT_BUCKET
        ),
        "avg_queue_time_bucket": safe_admission_token(
            raw.get("avg_queue_time_bucket"), ALLOWED_ADMISSION_DURATION_BUCKET
        ),
        "pool_pressure": safe_admission_token(raw.get("pool_pressure"), ALLOWED_ADMISSION_PRESSURE),
        "freshness": safe_admission_token(raw.get("freshness"), ALLOWED_ADMISSION_FRESHNESS),
        "guardrail": ADMISSION_CONTEXT_GUARDRAIL,
        "limitations": safe_admission_limitations(raw.get("limitations")),
    }
    if not available:
        context["reason"] = safe_admission_token(
            raw.get("reason"), ALLOWED_ADMISSION_REASON, default="request_failed"
        )
    return context


def unavailable_safe_admission_context(reason: object) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "available": False,
        "status": "unavailable",
        "source": "impala_admission_debug",
        "source_label": "Impala admission debug endpoint",
        "scope": "unknown",
        "pool_count": 0,
        "matched_pool_count": 0,
        "queue_present": "unknown",
        "running_present": "unknown",
        "queued_pool_count": 0,
        "running_pool_count": 0,
        "max_queue_depth_bucket": "unknown",
        "max_running_bucket": "unknown",
        "avg_queue_time_bucket": "unknown",
        "pool_pressure": "unknown",
        "freshness": "unknown",
        "reason": safe_admission_token(reason, ALLOWED_ADMISSION_REASON, default="request_failed"),
        "guardrail": ADMISSION_CONTEXT_GUARDRAIL,
        "limitations": [
            "Impala admission debug context was unavailable or unmapped; keep pool/admission context unknown."
        ],
    }


def safe_admission_token(
    value: object,
    allowed: set[str],
    *,
    default: str = "unknown",
) -> str:
    token = safe_token(value, default=default)
    return token if token in allowed else default


def nonnegative_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def safe_admission_limitations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    limitations: list[str] = []
    for item in value[:8]:
        text = str(item).strip()
        if text in ALLOWED_ADMISSION_LIMITATIONS:
            limitations.append(text)
    return list(dict.fromkeys(limitations))


def safe_runtime_metrics_source_label(value: object, source: object = None) -> str:
    text = str(value or "").strip()
    if text in {"Cloudera Manager time-series metrics", "Prometheus runtime metrics"}:
        return text
    source_token = safe_token(source, default="cm_timeseries")
    if source_token == "prometheus":
        return "Prometheus runtime metrics"
    return "Cloudera Manager time-series metrics"


def find_cluster_context_path(case_dir: Path) -> Path | None:
    """Find a safe aggregate cluster context next to a case or its batch root."""

    parent_candidates = [case_dir]
    parent_candidates.extend(parent for index, parent in enumerate(case_dir.parents) if index < 6)
    for parent in parent_candidates:
        candidate = parent / "cluster_context.json"
        if candidate.exists():
            return candidate
    return None


def collect_cluster_context(case_dir: Path) -> dict[str, Any] | None:
    context_path = find_cluster_context_path(case_dir)
    if context_path is None:
        return None
    try:
        raw = json.loads(context_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {
            "available": False,
            "status": "inconclusive",
            "error": "failed to parse cluster context",
        }
    if not isinstance(raw, dict):
        return {
            "available": False,
            "status": "inconclusive",
            "error": "cluster context is not an object",
        }
    return sanitize_cluster_context(raw)


def sanitize_cluster_context(raw: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the aggregate cluster context before it enters analyzer facts."""

    status = safe_token(raw.get("status"), default="inconclusive")
    if status not in ALLOWED_PRODUCT_STATUSES:
        status = "inconclusive"
    signals = safe_signals(raw.get("signals"))
    return {
        "available": bool(raw.get("available")),
        "status": status,
        "product": safe_token(raw.get("product"), default="cluster_doctor"),
        "sources": sanitize_cluster_sources(raw.get("sources")),
        "window": safe_window(raw.get("window")),
        "signal_counts": safe_count_map(raw.get("signal_counts")),
        "signals": signals,
        "limitations": safe_limitations(raw.get("limitations")),
        "next_checks": safe_cluster_next_checks(raw.get("next_checks")),
        "guardrail": (
            "Cluster context is a deterministic raw-free summary. "
            "It can guide operational checks, not prove root cause."
        ),
    }


def sanitize_cluster_sources(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sources: list[dict[str, Any]] = []
    for raw_source in value[:MAX_CONTEXT_SOURCES]:
        if not isinstance(raw_source, dict):
            continue
        source = safe_token(raw_source.get("source"), default="")
        if not source:
            continue
        status = safe_token(raw_source.get("status"), default="unknown")
        if status not in ALLOWED_CONTEXT_STATUSES:
            status = "unknown"
        product_status = safe_token(raw_source.get("product_status"), default="inconclusive")
        if product_status not in ALLOWED_PRODUCT_STATUSES:
            product_status = "inconclusive"
        sources.append(
            {
                "source": source,
                "available": bool(raw_source.get("available")),
                "status": status,
                "product_status": product_status,
            }
        )
    return sources


def safe_cluster_next_checks(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    checks: list[str] = []
    for raw_item in value[:8]:
        item = str(raw_item).strip()
        if 0 < len(item) <= 180 and not UNSAFE_CLUSTER_TEXT_RE.search(item):
            checks.append(item)
    return list(dict.fromkeys(checks))
