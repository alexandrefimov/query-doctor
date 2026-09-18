"""Markdown rendering for table metadata and Impala context facts."""

from __future__ import annotations

from typing import Any

from query_doctor.analyzer.profile_evidence import (
    DATA_MOVEMENT_FINDING_ID,
    STORAGE_FINDING_ID,
    profile_data_movement_supported,
    profile_storage_supported,
)
from query_doctor.analyzer.scan_skew import scan_skew_facts_from_analysis
from query_doctor.impala.table_metadata_facts import is_unknown_marker


NON_STATS_BOTTLENECK_FINDINGS = {
    "large_intermediate_or_exchange_traffic": "exchange_or_data_movement",
    "spill_or_scratch_io": "spill_or_scratch",
    "hdfs_or_storage_bottleneck": "storage_or_hdfs",
    "host_execution_tail_suspected": "backend_execution_tail",
    "backend_write_path_anomaly": "backend_write_path",
    "codegen_bottleneck": "codegen",
    "join_bottleneck": "query_shape",
    "sort_bottleneck": "query_shape",
    "analytic_bottleneck": "query_shape",
}


def availability_label(item: dict[str, Any]) -> str:
    return "available" if item.get("available") else "missing"


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


def render_table_metadata_context(analysis: dict[str, Any]) -> list[str]:
    context = analysis.get("table_metadata_context") or {}
    lines = ["## Table Metadata Context", ""]
    context_file = context.get("context_file", "not_observed")
    lines.append(f"- context file: {context_file}")
    if context.get("context_path"):
        lines.append(f"- context path: `{context['context_path']}`")
    lines.append(f"- table metadata facts: {context.get('table_metadata_facts', 'unknown')}")
    if context.get("metadata_source"):
        lines.append(f"- metadata source: {context['metadata_source']}")
    lines.append(f"- tables requested: {context.get('tables_requested', 0)}")
    read_only = context.get("read_only_statements_only")
    if read_only is not None:
        lines.append(f"- read-only statements only: {'yes' if read_only else 'no'}")
    if context.get("statement_result_count") is not None:
        lines.append(f"- statement result count: {context['statement_result_count']}")
    status_counts = metadata_counts_text(context.get("statement_status_counts"))
    if status_counts:
        lines.append(f"- statement status counts: {status_counts}")
    issue_counts = metadata_counts_text(context.get("statement_issue_counts"))
    if issue_counts:
        lines.append(f"- statement issue counts: {issue_counts}")
    if context.get("metadata_output_limit_bytes") is not None:
        lines.append(f"- metadata output limit bytes: {context['metadata_output_limit_bytes']}")
    if context.get("error"):
        lines.append(f"- error: {context['error']}")
    lines.append("")

    for table in context.get("tables") or []:
        lines.extend([f"### Table: {table['table']}", ""])
        lines.append(f"- object type: {table.get('object_type', 'unknown')}")
        for statement in ("SHOW CREATE TABLE", "SHOW TABLE STATS", "SHOW COLUMN STATS"):
            lines.append(
                f"- {statement} status: {table.get('statements', {}).get(statement, 'unknown')}"
            )
        lines.append(f"- table stats rows: {metadata_value(table.get('table_rows'))}")
        lines.append(
            "- table stats row-count completeness: "
            f"{metadata_value(table.get('table_stats_row_count_completeness'))}"
        )
        lines.append(f"- table stats size: {metadata_value(table.get('table_size'))}")
        if table.get("table_stats_last_computed"):
            lines.append(f"- table stats last computed: {table['table_stats_last_computed']}")
        if has_partition_row_count_facts(table):
            lines.append(f"- partition count: {metadata_count(table.get('partition_count'))}")
            lines.append(
                "- partitions with known row count: "
                f"{metadata_count(table.get('partitions_with_known_row_count'))}"
            )
            lines.append(
                "- partitions with unknown row count: "
                f"{metadata_count(table.get('partitions_with_unknown_row_count'))}"
            )
            lines.append(
                "- partitions with zero row count: "
                f"{metadata_count(table.get('partitions_with_zero_row_count'))}"
            )
        lines.append(
            "- column stats columns observed: "
            f"{metadata_count(table.get('column_stats_columns_observed'))}"
        )
        lines.append(
            "- column stats missing/unknown markers: "
            f"{metadata_count(table.get('column_stats_missing_markers'))}"
        )
        lines.append(
            f"- column stats completeness: {metadata_value(table.get('column_stats_completeness'))}"
        )
        if has_column_stats_status_counts(table):
            lines.append(
                "- column stats complete columns: "
                f"{metadata_count(table.get('column_stats_complete_columns'))}"
            )
            lines.append(
                "- column stats NDV-missing columns: "
                f"{metadata_count(table.get('column_stats_ndv_missing_columns'))}"
            )
            lines.append(
                "- column stats size-missing columns: "
                f"{metadata_count(table.get('column_stats_size_missing_columns'))}"
            )
            lines.append(
                "- column stats all-missing columns: "
                f"{metadata_count(table.get('column_stats_all_missing_columns'))}"
            )
        columns = table.get("column_stats_columns") or []
        if columns:
            lines.append(
                "- column stats columns: " + ", ".join(f"`{column}`" for column in columns)
            )
        lines.append(f"- file format: {table.get('file_format', 'unknown')}")
        lines.append(f"- storage family: {table.get('storage_family', 'unknown')}")
        lines.append(f"- storage scheme: {table.get('storage_scheme', 'unknown')}")
        partitions = table.get("partition_columns") or []
        if partitions:
            lines.append(
                "- partition columns: " + ", ".join(f"`{column}`" for column in partitions)
            )
        else:
            lines.append("- partition columns: unknown")
        lines.append("")
    return lines


def render_stats_metadata_quality(analysis: dict[str, Any]) -> list[str]:
    context = analysis.get("table_metadata_context") or {}
    quality = analysis.get("stats_metadata_quality")
    quality = quality if isinstance(quality, dict) else stats_metadata_quality(context, analysis)
    lines = ["## Stats Metadata Quality", ""]
    for key in (
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
    ):
        lines.append(f"- {key}: {quality[key]}")
    lines.append("")
    return lines


def stats_metadata_quality(
    context: dict[str, Any], analysis: dict[str, Any] | None = None
) -> dict[str, Any]:
    tables = [table for table in context.get("tables") or [] if isinstance(table, dict)]
    analysis_facts = analysis if isinstance(analysis, dict) else {}
    row_estimate_issue_count = len(analysis_facts.get("cardinality_anomalies") or []) + len(
        analysis_facts.get("zero_row_estimate_gaps") or []
    )
    row_estimate_evidence = "observed" if row_estimate_issue_count > 0 else "not_observed"
    if not tables:
        return {
            "status": "unavailable",
            "table_stats": "not_checked",
            "column_stats": "not_checked",
            "tables_with_missing_table_stats": 0,
            "tables_with_incomplete_column_stats": 0,
            "column_stats_complete_columns": 0,
            "column_stats_ndv_missing_columns": 0,
            "column_stats_size_missing_columns": 0,
            "column_stats_all_missing_columns": 0,
            "row_estimate_evidence": row_estimate_evidence,
            "row_estimate_issue_count": row_estimate_issue_count,
            "partition_coverage": "unknown",
            "partitioned_tables": 0,
            "partitioned_tables_with_missing_table_stats": 0,
            "partition_count": 0,
            "partitions_with_known_row_count": 0,
            "partitions_with_unknown_row_count": 0,
            "partitions_with_zero_row_count": 0,
            "join_filter_column_relevance": "unknown",
            "join_filter_columns_observed": 0,
            "join_filter_columns_with_stats": 0,
            "join_filter_columns_without_stats": 0,
            "join_filter_columns_with_complete_stats": 0,
            "join_filter_columns_with_ndv_missing_stats": 0,
            "join_filter_columns_with_size_missing_stats": 0,
            "join_filter_columns_with_all_missing_stats": 0,
            "join_filter_columns_with_unknown_stats": 0,
            "join_filter_partition_columns": 0,
            "non_stats_bottleneck_signals": 0,
            "non_stats_bottleneck_categories": "none",
            "stats_primary_bottleneck": "unknown",
            "stats_context": "metadata_unavailable",
            "interpretation": "Metadata was not collected; stats quality is unknown.",
            "guardrail": "Stats quality is follow-up evidence, not a standalone root cause.",
        }

    table_values = [
        stats_value(table.get("table_stats_row_count_completeness")) for table in tables
    ]
    column_values = [stats_value(table.get("column_stats_completeness")) for table in tables]
    missing_table_stats = sum(
        1 for value in table_values if value in {"missing/unknown", "missing", "unknown"}
    )
    incomplete_column_stats = sum(
        1 for value in column_values if value in {"incomplete/unknown", "incomplete", "unknown"}
    )
    table_stats = aggregate_stats_status(table_values, good_value="available")
    column_stats = aggregate_stats_status(column_values, good_value="complete")
    column_stats_complete_columns = sum(
        int_value(table.get("column_stats_complete_columns")) for table in tables
    )
    column_stats_ndv_missing_columns = sum(
        int_value(table.get("column_stats_ndv_missing_columns")) for table in tables
    )
    column_stats_size_missing_columns = sum(
        int_value(table.get("column_stats_size_missing_columns")) for table in tables
    )
    column_stats_all_missing_columns = sum(
        int_value(table.get("column_stats_all_missing_columns")) for table in tables
    )
    partitioned_tables = sum(1 for table in tables if has_partition_columns(table))
    partitioned_tables_with_missing_table_stats = sum(
        1
        for table, value in zip(tables, table_values)
        if has_partition_columns(table) and value in {"missing/unknown", "missing", "unknown"}
    )
    partition_count = sum(
        int_value(table.get("partition_count")) for table in tables if has_partition_columns(table)
    )
    partitions_with_known_row_count = sum(
        int_value(table.get("partitions_with_known_row_count"))
        for table in tables
        if has_partition_columns(table)
    )
    partitions_with_unknown_row_count = sum(
        int_value(table.get("partitions_with_unknown_row_count"))
        for table in tables
        if has_partition_columns(table)
    )
    partitions_with_zero_row_count = sum(
        int_value(table.get("partitions_with_zero_row_count"))
        for table in tables
        if has_partition_columns(table)
    )
    partition_coverage = partition_stats_coverage(
        partitioned_tables,
        partitioned_tables_with_missing_table_stats,
        table_stats,
        partition_count=partition_count,
        partitions_with_known_row_count=partitions_with_known_row_count,
        partitions_with_unknown_row_count=partitions_with_unknown_row_count,
    )
    sql_column_context = (
        analysis_facts.get("sql_column_context") if isinstance(analysis_facts, dict) else {}
    )
    sql_column_context = sql_column_context if isinstance(sql_column_context, dict) else {}
    join_filter_relevance = str(sql_column_context.get("join_filter_column_relevance") or "unknown")
    join_filter_columns_observed = int_value(sql_column_context.get("join_filter_columns_observed"))
    join_filter_columns_with_stats = int_value(
        sql_column_context.get("join_filter_columns_with_stats")
    )
    join_filter_columns_without_stats = int_value(
        sql_column_context.get("join_filter_columns_without_stats")
    )
    join_filter_columns_with_complete_stats = int_value(
        sql_column_context.get("join_filter_columns_with_complete_stats")
    )
    join_filter_columns_with_ndv_missing_stats = int_value(
        sql_column_context.get("join_filter_columns_with_ndv_missing_stats")
    )
    join_filter_columns_with_size_missing_stats = int_value(
        sql_column_context.get("join_filter_columns_with_size_missing_stats")
    )
    join_filter_columns_with_all_missing_stats = int_value(
        sql_column_context.get("join_filter_columns_with_all_missing_stats")
    )
    join_filter_columns_with_unknown_stats = int_value(
        sql_column_context.get("join_filter_columns_with_unknown_stats")
    )
    join_filter_partition_columns = int_value(
        sql_column_context.get("join_filter_partition_columns")
    )
    non_stats_categories = non_stats_bottleneck_categories(analysis_facts)
    non_stats_signal_count = len(non_stats_categories)
    non_stats_category_label = ", ".join(non_stats_categories) if non_stats_categories else "none"

    if table_stats == "not_applicable" and column_stats == "not_applicable":
        status = "not_applicable"
        interpretation = "Referenced metadata is not physical-table stats evidence."
        stats_context = "not_physical_table_stats"
        stats_primary_bottleneck = "not_applicable"
    elif missing_table_stats or incomplete_column_stats:
        status = "limited"
        if row_estimate_issue_count > 0:
            stats_context = "stats_gap_with_row_estimate_evidence"
            if non_stats_signal_count:
                stats_primary_bottleneck = "mixed_candidate"
                interpretation = (
                    "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence, "
                    "but competing non-stats bottleneck signals are also present."
                )
            else:
                stats_primary_bottleneck = "candidate_supported"
                interpretation = "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence."
        else:
            stats_context = "stats_gap_without_row_estimate_evidence"
            interpretation = "Metadata shows missing or incomplete stats coverage."
            stats_primary_bottleneck = "not_supported"
    elif table_stats == "available" and column_stats == "complete":
        status = "available"
        if row_estimate_issue_count > 0:
            stats_context = "stats_present_with_row_estimate_evidence"
            if non_stats_signal_count:
                stats_primary_bottleneck = "not_primary_supported"
                interpretation = (
                    "Stats are present, and competing non-stats bottleneck signals are supported; "
                    "prioritize those findings before treating stats as primary."
                )
            else:
                stats_primary_bottleneck = "not_supported_by_metadata"
                interpretation = (
                    "Stats are present, but row-estimate mismatch evidence remains; "
                    "stats may not be the primary explanation."
                )
        else:
            stats_context = "stats_available_no_row_estimate_evidence"
            interpretation = (
                "Collected metadata shows available table stats and complete column stats."
            )
            stats_primary_bottleneck = "not_supported"
    else:
        status = "unknown"
        stats_context = "stats_quality_unknown"
        interpretation = "Collected metadata does not give a complete stats-quality classification."
        stats_primary_bottleneck = "unknown"

    return {
        "status": status,
        "table_stats": table_stats,
        "column_stats": column_stats,
        "tables_with_missing_table_stats": missing_table_stats,
        "tables_with_incomplete_column_stats": incomplete_column_stats,
        "column_stats_complete_columns": column_stats_complete_columns,
        "column_stats_ndv_missing_columns": column_stats_ndv_missing_columns,
        "column_stats_size_missing_columns": column_stats_size_missing_columns,
        "column_stats_all_missing_columns": column_stats_all_missing_columns,
        "row_estimate_evidence": row_estimate_evidence,
        "row_estimate_issue_count": row_estimate_issue_count,
        "partition_coverage": partition_coverage,
        "partitioned_tables": partitioned_tables,
        "partitioned_tables_with_missing_table_stats": partitioned_tables_with_missing_table_stats,
        "partition_count": partition_count,
        "partitions_with_known_row_count": partitions_with_known_row_count,
        "partitions_with_unknown_row_count": partitions_with_unknown_row_count,
        "partitions_with_zero_row_count": partitions_with_zero_row_count,
        "join_filter_column_relevance": join_filter_relevance,
        "join_filter_columns_observed": join_filter_columns_observed,
        "join_filter_columns_with_stats": join_filter_columns_with_stats,
        "join_filter_columns_without_stats": join_filter_columns_without_stats,
        "join_filter_columns_with_complete_stats": join_filter_columns_with_complete_stats,
        "join_filter_columns_with_ndv_missing_stats": join_filter_columns_with_ndv_missing_stats,
        "join_filter_columns_with_size_missing_stats": join_filter_columns_with_size_missing_stats,
        "join_filter_columns_with_all_missing_stats": join_filter_columns_with_all_missing_stats,
        "join_filter_columns_with_unknown_stats": join_filter_columns_with_unknown_stats,
        "join_filter_partition_columns": join_filter_partition_columns,
        "non_stats_bottleneck_signals": non_stats_signal_count,
        "non_stats_bottleneck_categories": non_stats_category_label,
        "stats_primary_bottleneck": stats_primary_bottleneck,
        "stats_context": stats_context,
        "interpretation": interpretation,
        "guardrail": "Stats quality is follow-up evidence, not a standalone root cause.",
    }


def stats_value(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    aliases = {
        "not/available": "not_available",
        "not_available": "not_available",
        "not/applicable": "not_applicable",
        "not_applicable": "not_applicable",
        "missing_unknown": "missing/unknown",
        "incomplete_unknown": "incomplete/unknown",
    }
    return aliases.get(text, text)


def aggregate_stats_status(values: list[str], *, good_value: str) -> str:
    if not values:
        return "unknown"
    unique = set(values)
    if unique <= {"not_available", "not_applicable"}:
        return "not_applicable"
    if unique == {good_value}:
        return good_value
    if unique <= {good_value, "not_available", "not_applicable"}:
        return "mixed"
    if any(
        value in {"missing/unknown", "missing", "unknown", "incomplete/unknown", "incomplete"}
        for value in values
    ):
        return "incomplete_or_unknown"
    return "mixed"


def has_partition_columns(table: dict[str, Any]) -> bool:
    partitions = table.get("partition_columns")
    if not isinstance(partitions, list):
        return False
    return any(
        str(column or "").strip().lower() not in {"", "unknown", "none"} for column in partitions
    )


def has_partition_row_count_facts(table: dict[str, Any]) -> bool:
    return any(
        int_value(table.get(key)) > 0
        for key in (
            "partition_count",
            "partitions_with_known_row_count",
            "partitions_with_unknown_row_count",
            "partitions_with_zero_row_count",
        )
    )


def has_column_stats_status_counts(table: dict[str, Any]) -> bool:
    return any(
        int_value(table.get(key)) > 0
        for key in (
            "column_stats_complete_columns",
            "column_stats_ndv_missing_columns",
            "column_stats_size_missing_columns",
            "column_stats_all_missing_columns",
        )
    )


def partition_stats_coverage(
    partitioned_tables: int,
    missing_partitioned_table_stats: int,
    table_stats: str,
    *,
    partition_count: int = 0,
    partitions_with_known_row_count: int = 0,
    partitions_with_unknown_row_count: int = 0,
) -> str:
    if partitioned_tables == 0:
        return "not_observed"
    if partition_count <= 0:
        return "unknown"
    if partitions_with_unknown_row_count >= partition_count:
        return "missing"
    if partitions_with_unknown_row_count > 0:
        return "partial"
    if partitions_with_known_row_count == partition_count:
        return "available"
    if missing_partitioned_table_stats > 0 or table_stats != "available":
        return "partial"
    return "unknown"


def non_stats_bottleneck_categories(analysis: dict[str, Any]) -> tuple[str, ...]:
    categories: list[str] = []
    for finding in analysis.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("id") or "")
        if finding_id == DATA_MOVEMENT_FINDING_ID and not profile_data_movement_supported(analysis):
            continue
        if finding_id == STORAGE_FINDING_ID and not profile_storage_supported(analysis):
            continue
        category = NON_STATS_BOTTLENECK_FINDINGS.get(finding_id)
        if category:
            categories.append(category)

    backend_tail = analysis.get("backend_tail")
    if isinstance(backend_tail, dict):
        scan_skew = scan_skew_facts_from_analysis(analysis)
        if scan_skew.primary_supported and scan_skew.evidence_tier == "strong":
            categories.append("backend_data_skew")
        if int_value(backend_tail.get("execution_tail_candidate_count")) > 0:
            categories.append("backend_execution_tail")
        if int_value(backend_tail.get("write_path_tail_candidate_count")) > 0:
            categories.append("backend_write_path")

    return tuple(dedupe_preserve_order(categories))


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def metadata_value(value: Any) -> str:
    if is_unknown_marker(value):
        return "unknown"
    text = str(value).strip()
    return text if text else "unknown"


def metadata_count(value: Any) -> int | str:
    if is_unknown_marker(value):
        return "unknown"
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return "unknown"
    return parsed if parsed >= 0 else "unknown"


def int_value(value: Any) -> int:
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 0


def render_impala_context(analysis: dict[str, Any]) -> list[str]:
    context = analysis.get("impala_context")
    if not context:
        return []

    lines = ["## Impala Context", ""]
    original_sql = context["original_sql"]
    lines.extend(
        [
            "### Original SQL",
            "",
            f"- present: {'yes' if original_sql['available'] else 'no'}",
            f"- path: `{original_sql['path']}`",
            "",
            "### Referenced Tables",
            "",
        ]
    )

    tables = context.get("referenced_tables") or []
    if tables:
        lines.extend(f"- `{table}`" for table in tables)
    else:
        lines.append("- none parsed")
    lines.append("")

    lines.extend(["### Collected Metadata", ""])
    explain = context["explain"]
    lines.append(f"- EXPLAIN: {availability_label(explain)} (`{explain['path']}`)")
    for table in tables:
        lines.append(f"- `{table}`:")
        for command, status in context["table_metadata"].get(table, {}).items():
            lines.append(f"  - {command}: {availability_label(status)} (`{status['path']}`)")
    lines.append("")

    lines.extend(["### Collector Warnings / Failures", ""])
    warnings = context.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none found in collector summary")
    lines.append("")
    return lines
