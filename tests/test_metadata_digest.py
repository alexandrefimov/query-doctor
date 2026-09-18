from query_doctor.analyzer.context_collection import collect_sql_column_context
from query_doctor.analyzer.metadata_renderer import (
    render_stats_metadata_quality,
    render_table_metadata_context,
)
from query_doctor.analyzer.storage_context import build_storage_context
from query_doctor.impala import metadata_digest, table_metadata_facts


def test_metadata_digest_exposes_table_metadata_contract():
    assert metadata_digest.TABLE_METADATA_CONTEXT_HEADING == "## Table Metadata Context"
    assert table_metadata_facts.STATEMENTS == (
        "SHOW CREATE TABLE",
        "SHOW TABLE STATS",
        "SHOW COLUMN STATS",
    )
    assert table_metadata_facts.is_unknown_marker("-2")


def test_parse_table_stats_counts_partition_row_coverage_without_values():
    facts = table_metadata_facts.parse_table_stats(
        "\n".join(
            [
                "| ds | #Rows | Size |",
                "| 2026-05-01 | 10 | 1 MiB |",
                "| 2026-05-02 | -1 | 1 MiB |",
                "| 2026-05-03 | 0 | 0B |",
                "| Total | -1 | 2 MiB |",
            ]
        )
    )

    assert facts["table_rows"] == "unknown"
    assert facts["table_stats_row_count_completeness"] == "missing/unknown"
    assert facts["table_size"] == "2 MiB"
    assert facts["partition_count"] == 3
    assert facts["partitions_with_known_row_count"] == 2
    assert facts["partitions_with_unknown_row_count"] == 1
    assert facts["partitions_with_zero_row_count"] == 1
    assert "-1" not in repr(facts)
    assert "2026-05-01" not in repr(facts)
    assert "2026-05-02" not in repr(facts)


def test_parse_table_stats_totals_only_has_unknown_partition_coverage():
    facts = table_metadata_facts.parse_table_stats(
        "\n".join(
            [
                "| ds | #Rows | Size |",
                "| Total | 10 | 1 MiB |",
            ]
        )
    )

    assert facts["table_rows"] == 10
    assert facts["table_stats_row_count_completeness"] == "available"
    assert facts["table_size"] == "1 MiB"
    assert facts["partition_count"] == 0
    assert facts["partitions_with_known_row_count"] == 0
    assert facts["partitions_with_unknown_row_count"] == 0
    assert facts["partitions_with_zero_row_count"] == 0


def test_parse_table_stats_non_partitioned_keeps_table_level_rows():
    facts = table_metadata_facts.parse_table_stats(
        "\n".join(
            [
                "| #Rows | Size |",
                "| --- | --- |",
                "| 123 | 5 MiB |",
            ]
        )
    )

    assert facts["table_rows"] == 123
    assert facts["table_stats_row_count_completeness"] == "available"
    assert facts["table_size"] == "5 MiB"
    assert "partition_count" not in facts


def test_parse_column_stats_classifies_per_column_statuses_without_values():
    facts = table_metadata_facts.parse_column_stats(
        "\n".join(
            [
                "| Column | Type | NDV | #Nulls | Max Size | Avg Size |",
                "| id | BIGINT | 100 | 0 | 8 | 8 |",
                "| user_id | BIGINT | -1 | 0 | 8 | 8 |",
                "| payload | STRING | 10 | 0 | -1 | unknown |",
                "| comment | STRING | -1 | -1 | -1 | -1 |",
            ]
        )
    )

    assert facts["column_stats_columns_observed"] == 4
    assert facts["column_stats_completeness"] == "incomplete/unknown"
    assert facts["column_stats_per_column"] == {
        "id": "complete",
        "user_id": "ndv_missing",
        "payload": "size_missing",
        "comment": "all_missing",
    }
    assert facts["column_stats_complete_columns"] == 1
    assert facts["column_stats_ndv_missing_columns"] == 1
    assert facts["column_stats_size_missing_columns"] == 1
    assert facts["column_stats_all_missing_columns"] == 1
    assert "-1" not in repr(facts)
    assert "NULL" not in repr(facts)
    assert "unknown" not in repr(facts["column_stats_per_column"])
    assert "100" not in repr(facts["column_stats_per_column"])


def test_metadata_digest_normalizes_legacy_stats_placeholders():
    digest = metadata_digest.build_metadata_facts_digest(
        "\n".join(
            [
                "## Table Metadata Context",
                "",
                "- context file: present",
                "- table metadata facts: supported",
                "- tables requested: 1",
                "",
                "### Table: db.fact",
                "",
                "- SHOW TABLE STATS status: ok",
                "- SHOW COLUMN STATS status: ok",
                "- table stats rows: -1",
                "- table stats row-count completeness: -1",
                "- table stats size: NULL",
                "- column stats columns observed: -1",
                "- column stats missing/unknown markers: -1",
                "- column stats completeness: N/A",
            ]
        ),
        language="en",
    )

    assert "- table stats rows: unknown" in digest
    assert "- table stats row-count completeness: unknown" in digest
    assert "- table stats size: unknown" in digest
    assert "- column stats columns observed: unknown" in digest
    assert "- column stats missing/unknown markers: unknown" in digest
    assert "- column stats completeness: unknown" in digest
    assert ": -1" not in digest
    assert ": NULL" not in digest
    assert ": N/A" not in digest


def test_table_metadata_context_renderer_normalizes_stats_placeholders():
    text = "\n".join(
        render_table_metadata_context(
            {
                "table_metadata_context": {
                    "context_file": "present",
                    "table_metadata_facts": "supported",
                    "tables_requested": 1,
                    "tables": [
                        {
                            "table": "db.fact",
                            "statements": {
                                "SHOW TABLE STATS": "ok",
                                "SHOW COLUMN STATS": "ok",
                            },
                            "table_rows": -1,
                            "table_stats_row_count_completeness": "-1",
                            "table_size": "NULL",
                            "partition_count": 3,
                            "partitions_with_known_row_count": 2,
                            "partitions_with_unknown_row_count": -1,
                            "partitions_with_zero_row_count": 0,
                            "column_stats_columns_observed": "-1",
                            "column_stats_missing_markers": "N/A",
                            "column_stats_completeness": "NULL",
                        }
                    ],
                }
            }
        )
    )

    assert "- table stats rows: unknown" in text
    assert "- table stats row-count completeness: unknown" in text
    assert "- table stats size: unknown" in text
    assert "- partitions with unknown row count: unknown" in text
    assert "- column stats columns observed: unknown" in text
    assert "- column stats missing/unknown markers: unknown" in text
    assert "- column stats completeness: unknown" in text
    assert ": -1" not in text
    assert ": NULL" not in text
    assert ": N/A" not in text


def test_table_metadata_context_exposes_safe_statement_issue_counts(tmp_path):
    context_path = tmp_path / "impala_context.json"
    context = table_metadata_facts.context_from_payload(
        {
            "tables": ["db.fact"],
            "max_output_bytes": 262144,
            "read_only_statements_only": True,
            "results": [
                {
                    "table": "db.fact",
                    "statement": "SHOW CREATE TABLE",
                    "status": "ok",
                    "stdout": "CREATE TABLE db.fact (id BIGINT)",
                    "stderr": "Starting Impala Shell with Kerberos authentication",
                },
                {
                    "table": "db.fact",
                    "statement": "SHOW TABLE STATS",
                    "status": "too_large",
                    "stderr": "captured output exceeded max-output-bytes",
                },
                {
                    "table": "db.fact",
                    "statement": "SHOW COLUMN STATS",
                    "status": "error",
                    "stderr": "GSSAPI Failure: no serverFQDN password=secret",
                },
            ],
        },
        context_path,
        tmp_path,
    )

    assert context["statement_result_count"] == 3
    assert context["statement_status_counts"] == {"error": 1, "ok": 1, "too_large": 1}
    assert context["statement_issue_counts"] == {
        "kerberos_host_fqdn": 1,
        "too_large": 1,
    }
    assert context["metadata_output_limit_bytes"] == 262144
    assert "secret" not in repr(context)
    assert "no serverFQDN" not in repr(context)
    assert "auth_transport" not in context["statement_issue_counts"]


def test_table_metadata_context_renderer_and_parser_include_safe_issue_counts():
    from query_doctor.web.details_facts import parse_table_metadata_context_facts

    text = "\n".join(
        render_table_metadata_context(
            {
                "table_metadata_context": {
                    "context_file": "present",
                    "table_metadata_facts": "supported",
                    "tables_requested": 1,
                    "read_only_statements_only": True,
                    "statement_result_count": 3,
                    "statement_status_counts": {"ok": 2, "too_large": 1},
                    "statement_issue_counts": {"too_large": 1},
                    "metadata_output_limit_bytes": 262144,
                    "tables": [],
                }
            }
        )
    )
    parsed = parse_table_metadata_context_facts(text)

    assert "- statement result count: 3" in text
    assert "- statement status counts: ok=2, too_large=1" in text
    assert "- statement issue counts: too_large=1" in text
    assert "- metadata output limit bytes: 262144" in text
    assert parsed is not None
    assert parsed["summary"]["statement result count"] == "3"
    assert parsed["summary"]["statement status counts"] == "ok=2, too_large=1"
    assert parsed["summary"]["statement issue counts"] == "too_large=1"
    assert parsed["summary"]["metadata output limit bytes"] == "262144"


def test_parse_show_create_extracts_only_safe_storage_location_facts():
    facts = table_metadata_facts.parse_show_create(
        "\n".join(
            [
                "CREATE TABLE db.fact (id BIGINT)",
                "STORED AS PARQUET",
                "LOCATION 's3a://raw-lake-prod/warehouse/db.fact'",
            ]
        )
    )

    assert facts["object_type"] == "table"
    assert facts["file_format"] == "PARQUET"
    assert facts["storage_scheme"] == "s3a"
    assert facts["storage_family"] == "s3"
    assert "raw-lake-prod" not in repr(facts)
    assert "warehouse/db.fact" not in repr(facts)


def test_parse_show_create_maps_hdfs_and_unknown_storage_without_raw_location():
    hdfs_facts = table_metadata_facts.parse_show_create(
        "CREATE TABLE db.fact (id BIGINT)\n"
        "LOCATION 'hdfs://warehouse01.example.invalid:8020/warehouse/db.fact'\n"
    )
    relative_facts = table_metadata_facts.parse_show_create(
        "CREATE TABLE db.fact (id BIGINT)\nLOCATION '/warehouse/db.fact'\n"
    )

    assert hdfs_facts["storage_scheme"] == "hdfs"
    assert hdfs_facts["storage_family"] == "hdfs"
    assert "warehouse01" not in repr(hdfs_facts)
    assert "storage_scheme" not in relative_facts
    assert "storage_family" not in relative_facts


def test_parse_show_create_accepts_impala_shell_pipe_table_output():
    view_facts = table_metadata_facts.parse_show_create(
        "\n".join(
            [
                "| result |",
                "| --- |",
                "| CREATE VIEW db.view_a AS SELECT id FROM db.table_a |",
            ]
        )
    )
    table_facts = table_metadata_facts.parse_show_create(
        "\n".join(
            [
                "| result |",
                "| --- |",
                "| CREATE TABLE db.fact (id BIGINT) |",
                "| STORED AS PARQUET |",
                "| LOCATION 'hdfs://warehouse01.example.invalid/warehouse/db.fact' |",
            ]
        )
    )

    assert view_facts["object_type"] == "view"
    assert "storage_scheme" not in view_facts
    assert "storage_family" not in view_facts
    assert table_facts["object_type"] == "table"
    assert table_facts["file_format"] == "PARQUET"
    assert table_facts["storage_scheme"] == "hdfs"
    assert table_facts["storage_family"] == "hdfs"
    assert "warehouse01" not in repr(table_facts)


def test_storage_context_marks_view_only_metadata_without_storage_family():
    facts = build_storage_context(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "object_type": "view",
                        "storage_family": "unknown",
                    }
                ]
            },
            "operators": [{"operator_name": "HDFS SCAN", "time_ms": 1000}],
        }
    )

    assert facts["status"] == "unknown"
    assert facts["storage_family"] == "unknown"
    assert facts["source"] == "table_metadata_view_only"
    assert facts["view_table_count"] == 1
    assert facts["location_scheme_count"] == 0
    assert any("views without safe physical storage" in item for item in facts["limitations"])


def test_sql_column_context_counts_join_filter_column_stats_statuses(tmp_path):
    query_dir = tmp_path / "case"
    context_dir = query_dir / "impala_context"
    context_dir.mkdir(parents=True)
    (context_dir / "original_query.sql").write_text(
        "select * from db.fact f join db.dim d on f.user_id = d.id where f.payload = 'x'",
        encoding="utf-8",
    )
    context = {
        "tables": [
            {
                "table": "db.fact",
                "column_stats_columns": ["user_id", "payload"],
                "column_stats_per_column": {
                    "user_id": "ndv_missing",
                    "payload": "size_missing",
                },
            },
            {
                "table": "db.dim",
                "column_stats_columns": ["id"],
                "column_stats_per_column": {"id": "complete"},
            },
        ]
    }

    facts = collect_sql_column_context(query_dir, "", context)

    assert facts["join_filter_columns_observed"] == 3
    assert facts["join_filter_columns_with_stats"] == 1
    assert facts["join_filter_columns_without_stats"] == 2
    assert facts["join_filter_columns_with_complete_stats"] == 1
    assert facts["join_filter_columns_with_ndv_missing_stats"] == 1
    assert facts["join_filter_columns_with_size_missing_stats"] == 1
    assert facts["join_filter_columns_with_all_missing_stats"] == 0
    assert facts["join_filter_column_relevance"] == "partial"


def test_stats_metadata_quality_renders_counts_without_table_names():
    lines = render_stats_metadata_quality(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "table": "db.fact_orders",
                        "table_stats_row_count_completeness": "missing/unknown",
                        "column_stats_completeness": "incomplete/unknown",
                        "column_stats_complete_columns": 1,
                        "column_stats_ndv_missing_columns": 2,
                        "column_stats_size_missing_columns": 1,
                        "column_stats_all_missing_columns": 0,
                        "partition_columns": ["event_day"],
                        "partition_count": 5,
                        "partitions_with_known_row_count": 3,
                        "partitions_with_unknown_row_count": 2,
                        "partitions_with_zero_row_count": 1,
                    },
                    {
                        "table": "db.dim_customer",
                        "table_stats_row_count_completeness": "available",
                        "column_stats_completeness": "complete",
                        "column_stats_complete_columns": 3,
                    },
                ]
            },
            "sql_column_context": {
                "join_filter_column_relevance": "partial",
                "join_filter_columns_observed": 3,
                "join_filter_columns_with_stats": 2,
                "join_filter_columns_without_stats": 1,
                "join_filter_columns_with_complete_stats": 2,
                "join_filter_columns_with_ndv_missing_stats": 1,
                "join_filter_columns_with_size_missing_stats": 0,
                "join_filter_columns_with_all_missing_stats": 0,
                "join_filter_columns_with_unknown_stats": 0,
                "join_filter_partition_columns": 1,
            },
            "cardinality_anomalies": [{"label": "01:HASH JOIN"}],
            "zero_row_estimate_gaps": [{"label": "02:SCAN"}],
        }
    )
    text = "\n".join(lines)

    assert "## Stats Metadata Quality" in text
    assert "- status: limited" in text
    assert "- table_stats: incomplete_or_unknown" in text
    assert "- column_stats: incomplete_or_unknown" in text
    assert "- tables_with_missing_table_stats: 1" in text
    assert "- tables_with_incomplete_column_stats: 1" in text
    assert "- column_stats_complete_columns: 4" in text
    assert "- column_stats_ndv_missing_columns: 2" in text
    assert "- column_stats_size_missing_columns: 1" in text
    assert "- column_stats_all_missing_columns: 0" in text
    assert "- row_estimate_evidence: observed" in text
    assert "- row_estimate_issue_count: 2" in text
    assert "- partition_coverage: partial" in text
    assert "- partitioned_tables: 1" in text
    assert "- partitioned_tables_with_missing_table_stats: 1" in text
    assert "- partition_count: 5" in text
    assert "- partitions_with_known_row_count: 3" in text
    assert "- partitions_with_unknown_row_count: 2" in text
    assert "- partitions_with_zero_row_count: 1" in text
    assert "- join_filter_column_relevance: partial" in text
    assert "- join_filter_columns_observed: 3" in text
    assert "- join_filter_columns_with_stats: 2" in text
    assert "- join_filter_columns_without_stats: 1" in text
    assert "- join_filter_columns_with_complete_stats: 2" in text
    assert "- join_filter_columns_with_ndv_missing_stats: 1" in text
    assert "- join_filter_columns_with_size_missing_stats: 0" in text
    assert "- join_filter_columns_with_all_missing_stats: 0" in text
    assert "- join_filter_columns_with_unknown_stats: 0" in text
    assert "- join_filter_partition_columns: 1" in text
    assert "- non_stats_bottleneck_signals: 0" in text
    assert "- non_stats_bottleneck_categories: none" in text
    assert "- stats_primary_bottleneck: candidate_supported" in text
    assert "- stats_context: stats_gap_with_row_estimate_evidence" in text
    assert (
        "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence." in text
    )
    assert "db.fact_orders" not in text
    assert "db.dim_customer" not in text


def test_stats_metadata_quality_treats_not_available_as_not_applicable():
    lines = render_stats_metadata_quality(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "table": "db.view_orders",
                        "table_stats_row_count_completeness": "not_available",
                        "column_stats_completeness": "not_available",
                    },
                ]
            }
        }
    )
    text = "\n".join(lines)

    assert "- status: not_applicable" in text
    assert "- table_stats: not_applicable" in text
    assert "- column_stats: not_applicable" in text
    assert "Referenced metadata is not physical-table stats evidence." in text
    assert "db.view_orders" not in text


def test_stats_metadata_quality_flags_stats_present_with_estimate_mismatch():
    lines = render_stats_metadata_quality(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "table": "db.fact_orders",
                        "table_stats_row_count_completeness": "available",
                        "column_stats_completeness": "complete",
                        "partition_columns": ["event_day"],
                        "partition_count": 2,
                        "partitions_with_known_row_count": 2,
                        "partitions_with_unknown_row_count": 0,
                    },
                ]
            },
            "cardinality_anomalies": [{"label": "01:AGGREGATE"}],
            "zero_row_estimate_gaps": [],
        }
    )
    text = "\n".join(lines)

    assert "- status: available" in text
    assert "- row_estimate_evidence: observed" in text
    assert "- partition_coverage: available" in text
    assert "- non_stats_bottleneck_signals: 0" in text
    assert "- non_stats_bottleneck_categories: none" in text
    assert "- stats_primary_bottleneck: not_supported_by_metadata" in text
    assert "- stats_context: stats_present_with_row_estimate_evidence" in text
    assert "stats may not be the primary explanation" in text
    assert "db.fact_orders" not in text


def test_stats_metadata_quality_marks_present_stats_as_not_primary_when_runtime_signals_compete():
    lines = render_stats_metadata_quality(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "table": "db.fact_orders",
                        "table_stats_row_count_completeness": "available",
                        "column_stats_completeness": "complete",
                    },
                ]
            },
            "cardinality_anomalies": [{"label": "01:HASH JOIN"}],
            "totals": {"TotalBytesSent": {"bytes": 42 * 1024**3}},
            "top_operators_by_time": [{"operator_name": "EXCHANGE", "time_ms": 8_000}],
            "findings": [
                {"id": "large_intermediate_or_exchange_traffic"},
                {"id": "spill_or_scratch_io"},
                {"id": "cardinality_estimate_errors"},
            ],
            "scan_skew": {"primary_supported": True, "evidence_tier": "strong"},
            "backend_tail": {
                "data_skew": "yes",
                "execution_tail_candidate_count": 1,
            },
        }
    )
    text = "\n".join(lines)

    assert "- status: available" in text
    assert "- stats_primary_bottleneck: not_primary_supported" in text
    assert "- non_stats_bottleneck_signals: 4" in text
    assert (
        "- non_stats_bottleneck_categories: "
        "exchange_or_data_movement, spill_or_scratch, backend_data_skew, backend_execution_tail"
    ) in text
    assert "competing non-stats bottleneck signals are supported" in text
    assert "cardinality_estimate_errors" not in text
    assert "db.fact_orders" not in text


def test_stats_metadata_quality_keeps_stats_mixed_when_stats_gap_and_runtime_signals_compete():
    lines = render_stats_metadata_quality(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "table": "db.fact_orders",
                        "table_stats_row_count_completeness": "missing/unknown",
                        "column_stats_completeness": "complete",
                    },
                ]
            },
            "cardinality_anomalies": [{"label": "01:HASH JOIN"}],
            "totals": {"TotalBytesRead": {"bytes": 42 * 1024**3}},
            "top_operators_by_time": [{"operator_name": "HDFS SCAN", "time_ms": 12_000}],
            "findings": [{"id": "hdfs_or_storage_bottleneck"}],
        }
    )
    text = "\n".join(lines)

    assert "- status: limited" in text
    assert "- stats_primary_bottleneck: mixed_candidate" in text
    assert "- non_stats_bottleneck_categories: storage_or_hdfs" in text
    assert "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence" in text
    assert "competing non-stats bottleneck signals are also present" in text
    assert "db.fact_orders" not in text


def test_stats_metadata_quality_ignores_context_only_exchange_storage_categories():
    lines = render_stats_metadata_quality(
        {
            "table_metadata_context": {
                "tables": [
                    {
                        "table": "db.fact_orders",
                        "table_stats_row_count_completeness": "available",
                        "column_stats_completeness": "complete",
                    },
                ]
            },
            "cardinality_anomalies": [{"label": "01:HASH JOIN"}],
            "findings": [
                {"id": "large_intermediate_or_exchange_traffic"},
                {"id": "hdfs_or_storage_bottleneck"},
            ],
        }
    )
    text = "\n".join(lines)

    assert "- stats_primary_bottleneck: not_supported_by_metadata" in text
    assert "- non_stats_bottleneck_categories: none" in text
    assert "competing non-stats bottleneck signals are supported" not in text
    assert "db.fact_orders" not in text


def test_column_stats_ignore_boolean_counts_of_non_boolean_columns():
    from query_doctor.impala.table_metadata_facts import parse_column_stats

    complete = """| Column | Type | #Distinct Values | #Nulls | Max Size | Avg Size | #Trues | #Falses |
| id | BIGINT | 1000 | 0 | 8 | 8.0 | -1 | -1 |
| name | STRING | 900 | 3 | 40 | 12.5 | -1 | -1 |
| is_test | BOOLEAN | 2 | 0 | 1 | 1.0 | 10 | 990 |"""
    boolean_missing = complete.replace("| 10 | 990 |", "| -1 | -1 |")

    facts = parse_column_stats(complete)
    missing = parse_column_stats(boolean_missing)

    assert facts["column_stats_completeness"] == "complete"
    assert facts["column_stats_missing_markers"] == 0
    assert missing["column_stats_completeness"] == "incomplete/unknown"
    assert missing["column_stats_missing_markers"] == 2
