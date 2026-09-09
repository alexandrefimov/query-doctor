"""Safe facts loaders and parsers for web details pages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from query_doctor.impala import table_metadata_facts

from query_doctor.web.models import WebSettings
from query_doctor.web.trusted_artifacts import (
    batch_case_artifact_dirs,
    load_case_analyzer_facts_text,
    load_case_impala_context_artifact,
    resolve_batch_case_dir,
)


MAX_METADATA_FACTS_BYTES = 512 * 1024
MAX_IMPALA_CONTEXT_BYTES = 4 * 1024 * 1024
TABLE_METADATA_SUMMARY_KEYS = {
    "context file": "context file",
    "context path": "context path",
    "table metadata facts": "table metadata facts",
    "tables requested": "tables requested",
    "read-only statements only": "read-only statements only",
    "statement result count": "statement result count",
    "statement status counts": "statement status counts",
    "statement issue counts": "statement issue counts",
    "metadata output limit bytes": "metadata output limit bytes",
    "error": "error",
}
TABLE_METADATA_TABLE_KEYS = {
    "object type": "object type",
    "table stats rows": "table stats rows",
    "table stats row-count completeness": "table stats row-count completeness",
    "table stats size": "table stats size",
    "partition count": "partition count",
    "partitions with known row count": "partitions with known row count",
    "partitions with unknown row count": "partitions with unknown row count",
    "partitions with zero row count": "partitions with zero row count",
    "column stats columns observed": "column stats columns observed",
    "column stats missing/unknown markers": "column stats missing/unknown markers",
    "column stats completeness": "column stats completeness",
    "column stats complete columns": "column stats complete columns",
    "column stats ndv-missing columns": "column stats NDV-missing columns",
    "column stats size-missing columns": "column stats size-missing columns",
    "column stats all-missing columns": "column stats all-missing columns",
    "column stats columns": "column stats columns",
    "file format": "file format",
    "storage family": "storage family",
    "storage scheme": "storage scheme",
    "partition columns": "partition columns",
}
TABLE_METADATA_TABLE_KEYS_START = {"table", "table name", "referenced table"}
CM_METRIC_SIGNAL_LABELS = {
    "admission_pool_pressure": "Admission/pool pressure",
    "host_cpu_pressure": "Host CPU pressure",
    "daemon_memory_growth": "Daemon memory growth",
    "daemon_memory_pressure": "Daemon memory pressure",
    "host_disk_io_pressure": "Host disk I/O pressure",
    "hdfs_datanode_io_pressure": "HDFS DataNode I/O pressure",
    "network_io_spike": "Network I/O spike",
}
QUERY_CONTEXT_HEADINGS = {"## CM Query Context", "## Query Profile Context"}
QUERY_CONTEXT_SUMMARY_KEYS = {
    "available": "available",
    "source": "source",
    "source_label": "source",
    "status": "status",
    "query status": "status",
    "query_state": "query_state",
    "query_type": "query_type",
    "pool": "pool",
    "start_time": "start_time",
    "end_time": "end_time",
    "duration": "duration",
    "admission_result": "admission_result",
    "admission_wait": "admission_wait",
    "rows_produced": "rows_produced",
    "bytes_read": "bytes_read",
    "bytes_sent": "bytes_sent",
    "memory_aggregate_peak": "memory_aggregate_peak",
    "memory_per_node_peak": "memory_per_node_peak",
}


def load_specific_query_metadata_facts(case_dir: Path) -> dict[str, Any] | None:
    fallback_facts: dict[str, Any] | None = None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        context_facts = load_batch_case_impala_context_facts(artifact_dir)
        if context_facts and context_facts.get("tables"):
            return context_facts
        if context_facts and fallback_facts is None:
            fallback_facts = context_facts
        facts = load_batch_case_analysis_metadata_facts(artifact_dir)
        if facts and facts.get("tables"):
            return facts
        if facts and fallback_facts is None:
            fallback_facts = facts
    return fallback_facts


def load_specific_query_evidence_quality_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_evidence_quality_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_stats_quality_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_stats_quality_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_runtime_metrics_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_runtime_metrics_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_cm_metrics_facts(case_dir: Path) -> dict[str, Any] | None:
    return load_specific_query_runtime_metrics_facts(case_dir)


def load_specific_query_query_context_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_query_context_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_source_provenance_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_source_provenance_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_runtime_diagnosis_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_runtime_diagnosis_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_data_movement_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_data_movement_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_specific_query_cluster_runtime_context_facts(case_dir: Path) -> dict[str, Any] | None:
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_cluster_runtime_context_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_metadata_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    fallback_facts: dict[str, Any] | None = None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        context_facts = load_batch_case_impala_context_facts(artifact_dir)
        if context_facts and context_facts.get("tables"):
            return context_facts
        if context_facts and fallback_facts is None:
            fallback_facts = context_facts
        facts = load_batch_case_analysis_metadata_facts(artifact_dir)
        if facts and facts.get("tables"):
            return facts
        if facts and fallback_facts is None:
            fallback_facts = facts
    return fallback_facts


def load_batch_case_evidence_quality_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_evidence_quality_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_stats_quality_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_stats_quality_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_runtime_metrics_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_runtime_metrics_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_cm_metrics_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    return load_batch_case_runtime_metrics_facts(settings, case)


def load_batch_case_query_context_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        # Online History keeps no case directory, so a retained summary carries
        # its own runtime facts.
        return case_query_context_facts(case)
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_query_context_facts(artifact_dir)
        if facts:
            return facts
    return case_query_context_facts(case)


def case_query_context_facts(case: dict[str, object]) -> dict[str, Any] | None:
    facts = case.get("query_context")
    if not isinstance(facts, dict):
        return None
    summary = facts.get("summary")
    return facts if isinstance(summary, dict) and summary else None


def load_batch_case_source_provenance_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_source_provenance_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_runtime_diagnosis_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_runtime_diagnosis_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_data_movement_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_data_movement_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_cluster_runtime_context_facts(
    settings: WebSettings, case: dict[str, object]
) -> dict[str, Any] | None:
    case_dir = resolve_batch_case_dir(settings, case)
    if case_dir is None:
        return None
    for artifact_dir in batch_case_artifact_dirs(case_dir):
        facts = load_case_analysis_cluster_runtime_context_facts(artifact_dir)
        if facts:
            return facts
    return None


def load_batch_case_analysis_metadata_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_table_metadata_context_facts(text)


def load_case_analysis_evidence_quality_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_evidence_quality_facts(text)


def load_case_analysis_stats_quality_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_stats_quality_facts(text)


def load_case_analysis_runtime_metrics_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_runtime_metrics_facts(text)


def load_case_analysis_cm_metrics_facts(case_dir: Path) -> dict[str, Any] | None:
    return load_case_analysis_runtime_metrics_facts(case_dir)


def load_case_analysis_query_context_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_query_context_facts(text)


def load_case_analysis_source_provenance_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_source_provenance_facts(text)


def load_case_analysis_runtime_diagnosis_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_runtime_diagnosis_facts(text)


def load_case_analysis_data_movement_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_data_movement_facts(text)


def load_case_analysis_cluster_runtime_context_facts(case_dir: Path) -> dict[str, Any] | None:
    text = load_case_analyzer_facts_text(case_dir, max_bytes=MAX_METADATA_FACTS_BYTES)
    if text is None:
        return None
    return parse_cluster_runtime_context_facts(text)


def load_batch_case_impala_context_facts(case_dir: Path) -> dict[str, Any] | None:
    artifact = load_case_impala_context_artifact(case_dir, max_bytes=MAX_IMPALA_CONTEXT_BYTES)
    if artifact is None:
        return None
    context = table_metadata_facts.context_from_payload(
        artifact.payload,
        artifact.context_path,
        artifact.case_dir,
    )
    return convert_table_metadata_context_for_web(context)


def convert_table_metadata_context_for_web(context: dict[str, Any]) -> dict[str, Any] | None:
    tables = context.get("tables")
    if not isinstance(tables, list):
        return None
    converted = [
        convert_table_metadata_table_for_web(table) for table in tables if isinstance(table, dict)
    ]
    if not converted and not context:
        return None
    statement_counts = context.get("statement_status_counts")
    if not isinstance(statement_counts, dict):
        statement_counts = metadata_statement_counts(converted)
    return {
        "summary": {
            "context file": context.get("context_file", "unknown"),
            "table metadata facts": context.get("table_metadata_facts", "unknown"),
            "tables requested": str(context.get("tables_requested", "unknown")),
            "read-only statements only": context.get("read_only_statements_only", "unknown"),
            "statement result count": context.get("statement_result_count", "unknown"),
            "statement status counts": metadata_counts_text(context.get("statement_status_counts"))
            or "unknown",
            "statement issue counts": metadata_counts_text(context.get("statement_issue_counts"))
            or "none",
            "metadata output limit bytes": context.get("metadata_output_limit_bytes", "unknown"),
        },
        "tables": converted,
        "statement_counts": statement_counts,
    }


def convert_table_metadata_table_for_web(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "table": table.get("table", "unknown"),
        "object type": table.get("object_type", "unknown"),
        "statements": table.get("statements") if isinstance(table.get("statements"), dict) else {},
        "table stats row-count completeness": table.get(
            "table_stats_row_count_completeness", "unknown"
        ),
        "partition count": table.get("partition_count", 0),
        "partitions with known row count": table.get("partitions_with_known_row_count", 0),
        "partitions with unknown row count": table.get("partitions_with_unknown_row_count", 0),
        "partitions with zero row count": table.get("partitions_with_zero_row_count", 0),
        "column stats columns observed": table.get("column_stats_columns_observed", "unknown"),
        "column stats missing/unknown markers": table.get(
            "column_stats_missing_markers", "unknown"
        ),
        "column stats completeness": table.get("column_stats_completeness", "unknown"),
        "column stats complete columns": table.get("column_stats_complete_columns", 0),
        "column stats NDV-missing columns": table.get("column_stats_ndv_missing_columns", 0),
        "column stats size-missing columns": table.get("column_stats_size_missing_columns", 0),
        "column stats all-missing columns": table.get("column_stats_all_missing_columns", 0),
        "file format": table.get("file_format", "unknown"),
        "storage family": table.get("storage_family", "unknown"),
        "storage scheme": table.get("storage_scheme", "unknown"),
        "partition columns": ", ".join(str(item) for item in table.get("partition_columns") or [])
        or "unknown",
    }


def parse_table_metadata_context_facts(text: str) -> dict[str, Any] | None:
    in_section = False
    summary: dict[str, str] = {}
    tables: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line == "## Table Metadata Context"
            current = None
            continue
        if not in_section:
            continue
        if line.startswith("### "):
            table_name = parse_table_metadata_heading(line)
            if table_name:
                current = {"table": table_name, "statements": {}}
                tables.append(current)
            else:
                current = None
            continue
        if not line.startswith("- ") or ": " not in line:
            continue
        key, value = line[2:].split(": ", 1)
        key = key.strip()
        value = clean_metadata_fact_value(value)
        key_lower = key.lower()
        if key_lower in TABLE_METADATA_TABLE_KEYS_START:
            if value:
                current = {"table": value, "statements": {}}
                tables.append(current)
            continue
        if current is None:
            if key_lower in TABLE_METADATA_SUMMARY_KEYS:
                summary[TABLE_METADATA_SUMMARY_KEYS[key_lower]] = value
            continue
        statement = parse_table_metadata_statement_status_key(key)
        if statement:
            current.setdefault("statements", {})[statement] = normalize_metadata_placeholder(value)
        elif key_lower in TABLE_METADATA_TABLE_KEYS:
            current[TABLE_METADATA_TABLE_KEYS[key_lower]] = normalize_metadata_placeholder(value)
    if not summary and not tables:
        return None
    return {
        "summary": summary,
        "tables": tables,
        "statement_counts": metadata_statement_counts(tables),
    }


def parse_evidence_quality_facts(text: str) -> dict[str, Any] | None:
    section = ""
    summary: dict[str, str] = {}
    strengths: list[str] = []
    limitations: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = "evidence_quality" if line == "## Evidence Quality" else ""
            continue
        if not section:
            continue
        if line.startswith("### "):
            heading = line.removeprefix("###").strip().lower()
            if heading == "strengths":
                section = "evidence_quality_strengths"
            elif heading == "limitations":
                section = "evidence_quality_limitations"
            else:
                section = "evidence_quality"
            continue
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if section == "evidence_quality_strengths":
            if bullet:
                strengths.append(clean_metadata_fact_value(bullet))
            continue
        if section == "evidence_quality_limitations":
            if bullet:
                limitations.append(clean_metadata_fact_value(bullet))
            continue
        if ": " not in bullet:
            continue
        key, value = bullet.split(": ", 1)
        key = key.strip()
        if key in {"score", "level"}:
            summary[key] = clean_metadata_fact_value(value)
    if not summary and not strengths and not limitations:
        return None
    return {
        "score": summary.get("score", ""),
        "level": summary.get("level", ""),
        "strengths": strengths[:8],
        "limitations": limitations[:8],
    }


def parse_stats_quality_facts(text: str) -> dict[str, Any] | None:
    in_section = False
    summary: dict[str, str] = {}
    allowed = {
        "status",
        "table_stats",
        "column_stats",
        "tables_with_missing_table_stats",
        "tables_with_incomplete_column_stats",
        "column_stats_complete_columns",
        "column_stats_ndv_missing_columns",
        "column_stats_size_missing_columns",
        "column_stats_all_missing_columns",
        "row_estimate_evidence",
        "row_estimate_issue_count",
        "partition_coverage",
        "partitioned_tables",
        "partitioned_tables_with_missing_table_stats",
        "partition_count",
        "partitions_with_known_row_count",
        "partitions_with_unknown_row_count",
        "partitions_with_zero_row_count",
        "join_filter_column_relevance",
        "join_filter_columns_observed",
        "join_filter_columns_with_stats",
        "join_filter_columns_without_stats",
        "join_filter_columns_with_complete_stats",
        "join_filter_columns_with_ndv_missing_stats",
        "join_filter_columns_with_size_missing_stats",
        "join_filter_columns_with_all_missing_stats",
        "join_filter_columns_with_unknown_stats",
        "join_filter_partition_columns",
        "non_stats_bottleneck_signals",
        "non_stats_bottleneck_categories",
        "stats_primary_bottleneck",
        "stats_context",
        "interpretation",
        "guardrail",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line == "## Stats Metadata Quality"
            continue
        if not in_section or not line.startswith("- ") or ": " not in line:
            continue
        key, value = line[2:].split(": ", 1)
        key = key.strip()
        if key in allowed:
            cleaned_value = clean_metadata_fact_value(value)
            if key not in {
                "non_stats_bottleneck_categories",
                "interpretation",
                "guardrail",
            }:
                cleaned_value = normalize_metadata_placeholder(cleaned_value)
            summary[key] = cleaned_value
    if not summary:
        return None
    return summary


def parse_runtime_metrics_facts(text: str) -> dict[str, Any] | None:
    section = ""
    in_limitations = False
    summary: dict[str, str] = {}
    correlation_summary: dict[str, str] = {}
    signal_values: dict[str, dict[str, str]] = {
        key: {"label": label} for key, label in CM_METRIC_SIGNAL_LABELS.items()
    }
    correlation_values: dict[str, dict[str, str]] = {
        key: {"label": label} for key, label in CM_METRIC_SIGNAL_LABELS.items()
    }
    current_correlation_key = ""
    limitations: list[str] = []
    facts_headings = {"## Runtime Metrics Facts", "## CM Metrics Facts"}
    correlation_headings = {"## Runtime Metrics Correlation", "## CM Metrics Correlation"}
    limitation_headings = {"Runtime metrics limitations", "CM metrics limitations"}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            if line in facts_headings:
                section = "facts"
            elif line in correlation_headings:
                section = "correlation"
            else:
                section = ""
            in_limitations = False
            current_correlation_key = ""
            continue
        if not section:
            continue
        if line.startswith("### "):
            heading = line.removeprefix("###").strip()
            in_limitations = section == "facts" and heading in limitation_headings
            continue
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if section == "facts" and in_limitations:
            if bullet:
                limitations.append(clean_metadata_fact_value(bullet))
            continue
        if ": " not in bullet:
            continue
        key, value = bullet.split(": ", 1)
        key = key.strip()
        value = clean_metadata_fact_value(value)
        if section == "facts" and key in {
            "status",
            "source",
            "source_label",
            "metrics_profile",
            "coverage",
            "availability",
            "unavailable_metrics",
            "no_data_metrics",
        }:
            summary[key] = value
            continue
        if section == "facts" and key.endswith("_basis"):
            signal_key = key.removesuffix("_basis")
            if signal_key in signal_values:
                signal_values[signal_key]["basis"] = value
            continue
        if section == "facts" and key in signal_values:
            signal_values[key]["status"] = value
            continue
        if section == "correlation" and key in {
            "status",
            "coverage",
            "correlated_signals",
            "context_only_signals",
            "guardrail",
        }:
            correlation_summary[key] = value
            current_correlation_key = ""
            continue
        if section == "correlation" and key in correlation_values:
            status, _, rest = value.partition(" ")
            correlation_values[key]["status"] = clean_metadata_fact_value(status)
            match = re.search(r"metric=([^,\s)]+)", rest)
            if match:
                correlation_values[key]["metric_status"] = clean_metadata_fact_value(match.group(1))
            match = re.search(r"strength=([^,\s)]+)", rest)
            if match:
                correlation_values[key]["strength"] = clean_metadata_fact_value(match.group(1))
            current_correlation_key = key
            continue
        if (
            section == "correlation"
            and current_correlation_key
            and key in {"basis", "interpretation"}
        ):
            correlation_values[current_correlation_key][key] = value
    signals = [
        signal for signal in signal_values.values() if signal.get("status") or signal.get("basis")
    ]
    correlations = [
        correlation
        for correlation in correlation_values.values()
        if correlation.get("status") or correlation.get("interpretation")
    ]
    if (
        not summary
        and not signals
        and not limitations
        and not correlation_summary
        and not correlations
    ):
        return None
    return {
        "summary": summary,
        "signals": signals,
        "correlation_summary": correlation_summary,
        "correlations": correlations,
        "limitations": limitations[:5],
    }


def parse_cm_metrics_facts(text: str) -> dict[str, Any] | None:
    return parse_runtime_metrics_facts(text)


def parse_query_context_facts(text: str) -> dict[str, Any] | None:
    in_section = False
    summary: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line in QUERY_CONTEXT_HEADINGS
            continue
        if not in_section or not line.startswith("- ") or ": " not in line:
            continue
        key, value = line[2:].split(": ", 1)
        key = key.strip()
        mapped_key = QUERY_CONTEXT_SUMMARY_KEYS.get(key.lower())
        if mapped_key:
            summary[mapped_key] = clean_metadata_fact_value(value)
    if not summary:
        return None
    return {"summary": summary}


def parse_source_provenance_facts(text: str) -> dict[str, Any] | None:
    in_section = False
    guardrail = ""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line == "## Source Provenance"
            current = None
            continue
        if not in_section or not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if bullet.startswith("limitation:"):
            if current is not None:
                limitation = clean_metadata_fact_value(bullet.removeprefix("limitation:"))
                if limitation:
                    current.setdefault("limitations", []).append(limitation)
            continue
        if ": " not in bullet:
            continue
        key, value = bullet.split(": ", 1)
        key = clean_metadata_fact_value(key).lower()
        value = clean_metadata_fact_value(value)
        if key == "guardrail":
            guardrail = value
            current = None
            continue
        item = parse_source_provenance_item(key, value)
        if item:
            items.append(item)
            current = item
    if not guardrail and not items:
        return None
    return {"guardrail": guardrail, "items": items}


def parse_source_provenance_item(kind: str, value: str) -> dict[str, Any] | None:
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        return None
    item: dict[str, Any] = {
        "kind": clean_metadata_fact_value(kind),
        "status": clean_metadata_fact_value(parts[0]),
        "source": "",
        "coverage": "",
        "limitations": [],
    }
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip().lower()
        if key == "source":
            item["source"] = clean_metadata_fact_value(raw_value)
        elif key == "coverage":
            item["coverage"] = clean_metadata_fact_value(raw_value)
    return item


def parse_runtime_diagnosis_facts(text: str) -> dict[str, Any] | None:
    section = ""
    summary: dict[str, str] = {}
    signals: list[dict[str, Any]] = []
    current_signal: dict[str, Any] | None = None
    in_evidence = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = "runtime_diagnosis" if line == "## Runtime Diagnosis" else ""
            current_signal = None
            in_evidence = False
            continue
        if not section:
            continue
        if line.startswith("### "):
            title = clean_metadata_fact_value(line.removeprefix("###").strip())
            current_signal = {"title": title, "evidence": []}
            signals.append(current_signal)
            in_evidence = False
            continue
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if current_signal is not None and in_evidence and bullet:
            current_signal.setdefault("evidence", []).append(clean_metadata_fact_value(bullet))
            continue
        if ": " not in bullet:
            continue
        key, value = bullet.split(": ", 1)
        key = key.strip()
        value = clean_metadata_fact_value(value)
        if current_signal is None:
            if key in {"status", "summary", "guardrail"}:
                summary[key] = value
            continue
        if key == "evidence":
            in_evidence = True
            if value and value != "none":
                current_signal.setdefault("evidence", []).append(value)
            continue
        if key in {"status", "interpretation"}:
            current_signal[key] = value
            in_evidence = False
    if not summary and not signals:
        return None
    return {
        "status": summary.get("status", "unknown"),
        "summary": summary.get("summary", "unknown"),
        "guardrail": summary.get("guardrail", ""),
        "signals": signals,
    }


def parse_data_movement_facts(text: str) -> dict[str, Any] | None:
    section = ""
    summary: dict[str, str] = {}
    limitations: list[str] = []
    allowed_keys = {
        "status",
        "evidence_tier",
        "finding_supported",
        "primary_supported",
        "total_bytes_sent",
        "exchange_operator_count",
        "exchange_elapsed",
        "exchange_elapsed_share",
        "guardrail",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = "data_movement" if line == "## Data Movement Evidence" else ""
            continue
        if not section or not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if section == "data_movement_limitations":
            if bullet:
                limitations.append(clean_metadata_fact_value(bullet))
            continue
        if bullet == "limitations:":
            section = "data_movement_limitations"
            continue
        if ": " not in bullet:
            continue
        key, value = bullet.split(": ", 1)
        key = key.strip()
        if key in allowed_keys:
            summary[key] = clean_metadata_fact_value(value)
    if not summary and not limitations:
        return None
    return {
        "summary": summary,
        "limitations": limitations[:8],
    }


def parse_cluster_runtime_context_facts(text: str) -> dict[str, Any] | None:
    section = ""
    summary: dict[str, str] = {}
    signal_rollup: dict[str, str] = {}
    limitations: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = "cluster_runtime_context" if line == "## Cluster Runtime Context" else ""
            continue
        if not section:
            continue
        if line.startswith("### "):
            heading = line.removeprefix("###").strip()
            if heading == "Signal rollup":
                section = "cluster_runtime_rollup"
            elif heading == "Cluster runtime limitations":
                section = "cluster_runtime_limitations"
            else:
                section = "cluster_runtime_context"
            continue
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if section == "cluster_runtime_limitations":
            if bullet:
                limitations.append(clean_metadata_fact_value(bullet))
            continue
        if ": " not in bullet:
            continue
        key, value = bullet.split(": ", 1)
        key = key.strip()
        value = clean_metadata_fact_value(value)
        if section == "cluster_runtime_rollup":
            if key in {
                "observed_signals",
                "correlated_signals",
                "context_only_signals",
                "unknown_signals",
                "not_observed_signals",
            }:
                signal_rollup[key] = value
            continue
        if key in {
            "status",
            "source",
            "source_label",
            "collection_status",
            "coverage",
            "metrics_profile",
            "window_scope",
            "limit_summary",
            "scoring_contribution",
            "guardrail",
        }:
            summary[key] = value
    if not summary and not signal_rollup and not limitations:
        return None
    return {
        "summary": summary,
        "signal_rollup": signal_rollup,
        "limitations": limitations[:8],
    }


def parse_table_metadata_heading(line: str) -> str:
    heading = line.removeprefix("###").strip()
    if heading.lower().startswith("table:"):
        return heading.split(":", 1)[1].strip()
    return ""


def parse_table_metadata_statement_status_key(key: str) -> str:
    key_upper = key.upper()
    if not key_upper.endswith(" STATUS"):
        return ""
    statement = key_upper.removesuffix(" STATUS").strip()
    return statement if statement in table_metadata_facts.STATEMENTS else ""


def clean_metadata_fact_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return text[1:-1]
    return text


def normalize_metadata_placeholder(value: str) -> str:
    return "unknown" if table_metadata_facts.is_unknown_marker(value) else value


def metadata_counts_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key in sorted(value):
        count = value.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            continue
        parts.append(f"{key}={count}")
    return ", ".join(parts)


def metadata_statement_counts(tables: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        statements = table.get("statements")
        if not isinstance(statements, dict):
            continue
        for status in statements.values():
            key = str(status or "unknown")
            counts[key] = counts.get(key, 0) + 1
    return counts
