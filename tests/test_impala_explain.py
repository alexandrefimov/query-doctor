import json
import os
import re
import shutil
from pathlib import Path

from query_doctor.analyzer.impala_explain import (
    ExplainParseLimits,
    parse_impala_explain,
)
from query_doctor.analyzer.impala_explain_loader import load_impala_explain_facts
from query_doctor.cli import analyze_profile


REPO_DIR = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_DIR / "tests" / "fixtures" / "impala_explain"


def fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def node(facts: dict, operator_kind: str) -> dict:
    return next(item for item in facts["nodes"] if item["operator_kind"] == operator_kind)


def test_reads_cardinality_through_impala_annotations():
    """Impala decorates the cardinality line, and the node must survive it.

    A tuple id gains an "N" when the tuple is nullable, a runtime filter prints the
    filtered estimate ahead of the unfiltered one, and a cardinality taken from
    historical statistics is followed by "(from HBO)" - with a caveat inside the
    parentheses when the match ignored partition constants. Each of these used to make
    the line unrecognisable, which dropped the node's cardinality and row size.
    """
    facts = parse_impala_explain(fixture_text("annotated_cardinality.txt"))

    assert facts["status"] == "supported"
    assert facts["detail_hint"] == "extended_like"

    aggregate = node(facts, "aggregate")
    assert aggregate["estimated_cardinality"] == 1.0
    assert aggregate["estimated_row_size_bytes"] == 8.0

    join = node(facts, "hash_join")
    assert join["estimated_cardinality"] == 31.0
    assert join["estimated_row_size_bytes"] == 29.0

    # The filtered estimate is the one the node actually produces, so it is the one kept.
    scan = node(facts, "hdfs_scan")
    assert scan["estimated_cardinality"] == 37_880.0
    assert scan["estimated_row_size_bytes"] == 21.0


def test_parses_standard_explain_into_raw_free_typed_facts():
    facts = parse_impala_explain(fixture_text("standard.txt"))

    assert facts["status"] == "supported"
    assert facts["parser_status"] == "supported"
    assert facts["detail_hint"] == "extended_like"
    assert facts["observed_fragment_count"] == 0
    assert facts["observed_node_count"] == 4
    assert facts["observed_operator_family_counts"] == {
        "aggregate": 2,
        "exchange": 1,
        "scan": 1,
    }
    assert facts["resource_estimates"] == {
        "estimated_per_host_memory_state": "supported",
        "estimated_per_host_memory_bytes": 42 * 1024**2,
        "estimated_vcores_state": "supported",
        "estimated_vcores": 1,
    }
    scan = node(facts, "hdfs_scan")
    assert scan["scan_partition_state"] == "supported"
    assert scan["scan_partitions_selected"] == 2
    assert scan["scan_partitions_total"] == 8
    assert scan["scan_partition_selection"] == "pruned"
    assert scan["estimated_scan_bytes"] == 5.25 * 1024**2
    assert scan["estimated_cardinality"] == 250.0
    assert scan["table_stats_state"] == "reported_available"
    assert scan["column_stats_state"] == "reported_available"
    assert scan["predicate_section_state"] == "unknown"
    assert scan["runtime_filter_section_state"] == "unknown"

    payload = serialized(facts)
    assert "synthetic_db" not in payload
    assert "synthetic_orders" not in payload
    assert "sum(count" not in payload
    assert "node_id" not in payload
    assert "fragment_id" not in payload


def test_parses_verbose_fragments_join_distributions_and_structural_overlap():
    profile_operators = [
        {"operator_id": "04", "operator_name": "HASH JOIN"},
        {"operator_id": "05", "operator_name": "EXCHANGE"},
        {"operator_id": "03", "operator_name": "HASH JOIN"},
        {"operator_id": "02", "operator_name": "HDFS SCAN"},
        {"operator_id": "01", "operator_name": "KUDU SCAN"},
    ]

    facts = parse_impala_explain(fixture_text("verbose.txt"), profile_operators=profile_operators)

    assert facts["parser_status"] == "supported"
    assert facts["detail_hint"] == "verbose_like"
    assert facts["observed_fragment_count"] == 2
    assert facts["observed_node_count"] == 5
    assert facts["observed_join_distribution_counts"] == {
        "broadcast": 1,
        "partitioned": 1,
    }
    assert facts["missing_stats_warning_state"] == "supported"
    assert facts["missing_stats_warning_observed"] is True
    assert facts["correlation"] == {
        "structural_match_status": "matched",
        "method": "engine_local_operator_identity_and_kind",
        "checked_plan_node_count": 5,
        "checked_profile_operator_count": 5,
        "matched_plan_node_count": 5,
        "ambiguous_plan_node_count": 0,
        "mismatched_plan_node_count": 0,
        "unmatched_plan_node_count": 0,
        "extra_profile_operator_count": 0,
        "unmapped_profile_operator_count": 0,
        "identity_link_basis": "unbound_external_artifact",
        "execution_identity_status": "unknown",
        "statement_identity_status": "unknown",
    }
    hdfs_scan = node(facts, "hdfs_scan")
    assert hdfs_scan["scan_partition_selection"] == "full_selection"
    assert hdfs_scan["predicate_section_state"] == "supported"
    assert hdfs_scan["runtime_filter_section_state"] == "supported"
    assert hdfs_scan["table_stats_state"] == "reported_unavailable"

    payload = serialized(facts)
    for raw_marker in (
        "synthetic_db",
        "synthetic_fact",
        "account_id",
        "RF001",
        "42",
    ):
        # Whole tokens only: "42" is a predicate literal, while a derived byte
        # count such as 134217728 may contain the same digits.
        assert not re.search(rf"(?<![\w.]){re.escape(raw_marker)}(?!\w)", payload)


def test_parses_legacy_and_boxed_layouts_without_inventing_missing_estimates():
    legacy = parse_impala_explain(fixture_text("legacy.txt"))
    boxed = parse_impala_explain(fixture_text("boxed.txt"))

    assert legacy["parser_status"] == "supported"
    assert legacy["detail_hint"] == "verbose_like"
    assert legacy["observed_fragment_count"] == 2
    assert legacy["resource_estimates"]["estimated_per_host_memory_bytes"] == 1_127_658_681.0
    assert legacy["resource_estimates"]["estimated_vcores"] == 2
    assert legacy["observed_join_distribution_counts"] == {"broadcast": 1}
    scans = [item for item in legacy["nodes"] if item["operator_family"] == "scan"]
    assert len(scans) == 2
    assert scans[0]["estimated_scan_bytes"] == 33.0
    assert scans[1]["estimated_per_host_memory_state"] == "unknown"
    assert scans[1]["estimated_per_host_memory_bytes"] is None

    assert boxed["parser_status"] == "supported"
    assert boxed["observed_node_count"] == 2
    assert boxed["resource_estimates"]["estimated_per_host_memory_bytes"] == 8 * 1024**2
    assert node(boxed, "hdfs_scan")["scan_partition_selection"] == "full_selection"


def test_parses_modern_fragment_host_suffix_and_bare_partition_layout():
    facts = parse_impala_explain(fixture_text("modern_fragment_hosts.txt"))

    assert facts["parser_status"] == "supported"
    assert facts["detail_hint"] == "verbose_like"
    assert facts["observed_fragment_count"] == 2
    assert facts["observed_fragment_partitioning_counts"] == {
        "random": 1,
        "unpartitioned": 1,
    }
    exchange = node(facts, "exchange")
    scan = node(facts, "hdfs_scan")
    assert exchange["fragment_partitioning"] == "unpartitioned"
    assert exchange["partitioning"] == "unpartitioned"
    assert scan["fragment_partitioning"] == "random"
    assert scan["partitioning"] == "random"
    assert "synthetic_table" not in serialized(facts)


def test_supported_explain_levels_keep_bounded_raw_free_coverage():
    representative_levels = (
        ("level_minimal.txt", "basic", 0),
        ("boxed.txt", "basic", 0),
        ("standard.txt", "extended_like", 0),
        ("modern_verbose_resources.txt", "verbose_like", 2),
    )

    for fixture_name, detail_hint, fragment_count in representative_levels:
        facts = parse_impala_explain(fixture_text(fixture_name))

        assert facts["status"] == "supported"
        assert facts["parser_status"] == "supported"
        assert facts["detail_hint"] == detail_hint
        assert facts["observed_fragment_count"] == fragment_count
        assert facts["causal_claim_supported"] is False
        assert facts["engine_recommendation_supported"] is False
        payload = serialized(facts)
        assert "synthetic_db" not in payload
        assert "synthetic_table" not in payload


def test_parses_modern_scan_and_stored_statistics_layout_without_raw_names():
    facts = parse_impala_explain(fixture_text("modern_verbose_resources.txt"))

    scan = node(facts, "hdfs_scan")
    assert facts["parser_status"] == "supported"
    assert scan["scan_partition_state"] == "supported"
    assert scan["scan_partitions_selected"] == 2
    assert scan["scan_partitions_total"] == 6
    assert scan["scan_file_count"] == 4
    assert scan["estimated_scan_bytes"] == 64 * 1024**2
    assert scan["table_stats_state"] == "reported_available"
    assert scan["column_stats_state"] == "reported_partial"
    assert scan["estimated_row_size_bytes"] == 16.0
    assert scan["estimated_cardinality"] == 100.0

    payload = serialized(facts)
    assert "synthetic_key" not in payload
    assert "max-scan-range-rows" not in payload
    assert "mem-reservation" not in payload


def test_stored_statistics_block_fails_closed_before_unexpected_detail_text():
    facts = parse_impala_explain(
        """00:SCAN HDFS [synthetic_db.synthetic_table]
| HDFS partitions=1/1 files=1 size=16B
| stored statistics:
|   table: rows=1 size=16B
|   columns missing stats: private_column
| cardinality=9777777777777777
| row-size=8888888888888888B
"""
    )

    scan = node(facts, "hdfs_scan")
    assert scan["table_stats_state"] == "reported_available"
    assert scan["column_stats_state"] == "reported_partial"
    assert scan["estimated_cardinality"] is None
    assert scan["estimated_row_size_bytes"] is None
    payload = serialized(facts)
    assert "private_column" not in payload
    assert "9777777777777777" not in payload
    assert "8888888888888888" not in payload


def test_malformed_stored_table_detail_fails_closed_before_tuple_estimates():
    facts = parse_impala_explain(
        """00:SCAN HDFS [synthetic_db.synthetic_table]
| stored statistics:
|   table: private rows=1 size=16B
| tuple-ids=0 row-size=16B cardinality=9444444444444444
"""
    )

    scan = node(facts, "hdfs_scan")
    assert scan["table_stats_state"] == "unknown"
    assert scan["estimated_row_size_bytes"] is None
    assert scan["estimated_cardinality"] is None
    payload = serialized(facts)
    assert "private" not in payload
    assert "9444444444444444" not in payload


def test_malformed_tuple_detail_fails_closed_before_following_estimates():
    facts = parse_impala_explain(
        """00:SCAN HDFS [synthetic_db.synthetic_table]
| tuple-ids=private row-size=16B cardinality=9666666666666666
| cardinality=9555555555555555
"""
    )

    scan = node(facts, "hdfs_scan")
    assert scan["estimated_row_size_bytes"] is None
    assert scan["estimated_cardinality"] is None
    payload = serialized(facts)
    assert "private" not in payload
    assert "9666666666666666" not in payload
    assert "9555555555555555" not in payload


def test_bare_scan_partitioning_requires_a_separate_allowlisted_attribute():
    relation_only = parse_impala_explain("00:SCAN HDFS [RANDOM]\n")
    allowlisted_attribute = parse_impala_explain(
        "00:SCAN HDFS [synthetic_db.synthetic_table, RANDOM]\n"
    )

    assert node(relation_only, "hdfs_scan")["partitioning"] == "unknown"
    assert node(allowlisted_attribute, "hdfs_scan")["partitioning"] == "random"


def test_utf8_bom_does_not_hide_the_first_supported_node():
    facts = parse_impala_explain("\ufeff00:SCAN HDFS [synthetic_db.synthetic_table]\n")

    assert facts["parser_status"] == "supported"
    assert facts["observed_node_count"] == 1
    assert node(facts, "hdfs_scan")["operator_family"] == "scan"


def test_unknown_node_is_partial_and_never_serializes_the_unknown_label():
    text = (
        """09:PRIVATE_SECRET_OPERATOR [customer_token]\n00:SCAN HDFS [private_db.private_table]\n"""
    )

    facts = parse_impala_explain(text)

    assert facts["status"] == "supported"
    assert facts["parser_status"] == "partial"
    assert facts["observed_node_count"] == 1
    assert facts["unmapped_node_header_count"] == 1
    assert "unmapped_node_headers" in facts["limitation_codes"]
    payload = serialized(facts)
    assert "PRIVATE_SECRET_OPERATOR" not in payload
    assert "customer_token" not in payload
    assert "private_db" not in payload
    assert "private_table" not in payload


def test_duplicate_fragment_identity_is_partial_and_linkage_stays_unknown():
    facts = parse_impala_explain(
        """F01:PLAN FRAGMENT [PARTITION=HASH]
00:SCAN HDFS [synthetic_db.left]
F01:PLAN FRAGMENT [PARTITION=BROADCAST]
01:SCAN HDFS [synthetic_db.right]
"""
    )

    assert facts["parser_status"] == "partial"
    assert "duplicate_plan_fragment_identity" in facts["limitation_codes"]
    assert {plan_node["fragment_partitioning"] for plan_node in facts["nodes"]} == {"unknown"}


def test_malformed_fragment_header_cannot_reuse_previous_fragment_linkage():
    facts = parse_impala_explain(
        """F01:PLAN FRAGMENT [UNPARTITIONED] hosts=1 instances=1
00:SCAN HDFS [synthetic_db.first]
F02:PLAN FRAGMENT [RANDOM] hosts=2 instances=2private
01:SCAN HDFS [synthetic_db.second]
F12345:PLAN FRAGMENT [RANDOM] hosts=2 instances=2
02:SCAN HDFS [synthetic_db.third]
FX:PLAN FRAGMENT [RANDOM] hosts=2 instances=2
03:SCAN HDFS [synthetic_db.fourth]
"""
    )

    assert facts["parser_status"] == "partial"
    assert "unsupported_fragment_header" in facts["limitation_codes"]
    assert facts["nodes"][0]["fragment_partitioning"] == "unpartitioned"
    for plan_node in facts["nodes"][1:]:
        assert plan_node["fragment_observed"] is False
        assert plan_node["fragment_partitioning"] == "unknown"
    assert "2private" not in serialized(facts)


def test_overlong_line_clears_fragment_context_before_following_nodes():
    limits = ExplainParseLimits(max_line_chars=48)
    facts = parse_impala_explain(
        """F01:PLAN FRAGMENT [UNPARTITIONED]
00:SCAN HDFS [synthetic_db.first]
F02:PLAN FRAGMENT [RANDOM] hosts=2 instances=2 private-suffix
01:SCAN HDFS [synthetic_db.second]
""",
        limits=limits,
    )

    assert facts["parser_status"] == "partial"
    assert "overlong_line_ignored" in facts["limitation_codes"]
    assert facts["nodes"][0]["fragment_partitioning"] == "unpartitioned"
    assert facts["nodes"][1]["fragment_observed"] is False
    assert facts["nodes"][1]["fragment_partitioning"] == "unknown"


def test_malformed_node_and_legacy_partition_suffixes_cannot_promote_typed_facts():
    malformed_nodes = parse_impala_explain(
        """00:SCAN HDFS [synthetic_db.synthetic_table, RANDOM] private
01:EXCHANGE [BROADCAST] private
"""
    )
    malformed_fragment = parse_impala_explain(
        """PLAN FRAGMENT 1
PARTITION: UNPARTITIONEDprivate
00:SCAN HDFS [synthetic_db.synthetic_table]
"""
    )

    assert malformed_nodes["parser_status"] == "partial"
    assert "unsupported_node_header_attributes" in malformed_nodes["limitation_codes"]
    assert node(malformed_nodes, "hdfs_scan")["partitioning"] == "unknown"
    assert node(malformed_nodes, "exchange")["partitioning"] == "unknown"
    assert malformed_fragment["parser_status"] == "partial"
    assert "unsupported_fragment_partitioning" in malformed_fragment["limitation_codes"]
    assert malformed_fragment["nodes"][0]["fragment_partitioning"] == "unknown"


def test_node_partition_like_details_cannot_mutate_fragment_or_inject_following_facts():
    facts = parse_impala_explain(
        """PLAN FRAGMENT 1
PARTITION: UNPARTITIONED
00:SCAN HDFS [synthetic_db.first]
| PARTITION: BROADCAST
| cardinality=7777777777777771
01:SCAN HDFS [synthetic_db.second]
| PARTITION: private-expression
| cardinality=7777777777777772
"""
    )

    assert facts["parser_status"] == "supported"
    for plan_node in facts["nodes"]:
        assert plan_node["fragment_partitioning"] == "unpartitioned"
        assert plan_node["estimated_cardinality"] is None
    payload = serialized(facts)
    assert "7777777777777771" not in payload
    assert "7777777777777772" not in payload


def test_legacy_fragment_partition_is_only_read_immediately_after_header():
    facts = parse_impala_explain(
        """PLAN FRAGMENT 1
PARTITION: RANDOM
STREAM DATA SINK
PARTITION: UNPARTITIONED
00:SCAN HDFS [synthetic_db.synthetic_table]
"""
    )

    assert facts["parser_status"] == "supported"
    assert facts["nodes"][0]["fragment_partitioning"] == "random"


def test_parser_bounds_lines_nodes_and_overlong_input_without_negative_claims():
    limits = ExplainParseLimits(
        max_bytes=10_000,
        max_lines=2,
        max_line_chars=64,
        max_nodes=1,
        max_fragments=1,
    )
    text = "\n".join(
        [
            "F01:PLAN FRAGMENT [PARTITION=RANDOM]",
            "01:HASH JOIN [INNER JOIN, BROADCAST]",
            "00:SCAN HDFS [private_db.private_table]",
            "X" * 100,
        ]
    )

    facts = parse_impala_explain(text, limits=limits)

    assert facts["status"] == "supported"
    assert facts["parser_status"] == "partial"
    assert facts["observed_node_count"] == 1
    assert "line_limit_reached" in facts["limitation_codes"]
    assert facts["missing_stats_warning_state"] == "unknown"
    assert facts["causal_claim_supported"] is False
    assert facts["engine_recommendation_supported"] is False


def test_default_node_bound_keeps_serialized_projection_under_budget():
    limits = ExplainParseLimits()
    text = "\n".join(
        f"{node_id:04d}:SCAN HDFS [synthetic_db.synthetic_{node_id}]"
        for node_id in range(limits.max_nodes)
    )

    facts = parse_impala_explain(text, limits=limits)

    assert facts["observed_node_count"] == limits.max_nodes
    persisted = json.dumps(facts, ensure_ascii=False, indent=2)
    assert len(persisted.encode("utf-8")) < 5 * 1024 * 1024


def test_parser_rejects_oversized_nul_and_empty_text_with_stable_reasons():
    tiny = ExplainParseLimits(max_bytes=8)

    oversized = parse_impala_explain("00:SCAN HDFS", limits=tiny)
    nul = parse_impala_explain("00:SCAN HDFS\x00[private]", limits=ExplainParseLimits())
    invalid_unicode = parse_impala_explain("\udcff")
    empty = parse_impala_explain("  \n")

    assert oversized["artifact_status"] == "too_large"
    assert oversized["limitation_codes"] == ["artifact_too_large"]
    assert nul["artifact_status"] == "invalid"
    assert nul["limitation_codes"] == ["invalid_text"]
    assert invalid_unicode["artifact_status"] == "invalid"
    assert invalid_unicode["limitation_codes"] == ["invalid_text"]
    assert empty["artifact_status"] == "available"
    assert empty["limitation_codes"] == ["empty_input"]
    assert all(item["status"] == "unknown" for item in (oversized, nul, invalid_unicode, empty))


def test_wrapped_missing_stats_warning_is_observed_without_retaining_names():
    facts = parse_impala_explain(
        """WARNING: The following tables are missing relevant table and/or column
statistics: private_db.private_table
00:SCAN HDFS [private_db.private_table]
"""
    )

    assert facts["missing_stats_warning_state"] == "supported"
    assert facts["missing_stats_warning_observed"] is True
    assert "private_db" not in serialized(facts)


def test_non_plan_text_cannot_publish_negative_missing_stats_evidence():
    facts = parse_impala_explain("This is not an Impala plan.\n")

    assert facts["status"] == "unknown"
    assert facts["parser_status"] == "unknown"
    assert facts["missing_stats_warning_state"] == "unknown"


def test_unsupported_global_memory_units_remain_unknown():
    for value in ("1.5PB", "1.5XB", "10Bytes"):
        facts = parse_impala_explain(
            f"Estimated Per-Host Requirements: Memory={value} VCores=1\n"
            "00:SCAN HDFS [synthetic_db.t]\n"
        )

        assert facts["resource_estimates"]["estimated_per_host_memory_state"] == "unknown"
        assert facts["resource_estimates"]["estimated_per_host_memory_bytes"] is None


def test_unsupported_detail_units_and_suffixes_remain_unknown():
    facts = parse_impala_explain(
        """00:SCAN HDFS [synthetic_db.t]
| partitions=1/9private files=8 size=7GBprivate
| cardinality=10Bytes
| row-size=6PB
| per-host memory=5XB
"""
    )
    scan = node(facts, "hdfs_scan")

    assert scan["scan_partition_state"] == "unknown"
    assert scan["estimated_scan_bytes"] is None
    assert scan["estimated_cardinality"] is None
    assert scan["estimated_row_size_bytes"] is None
    assert scan["estimated_per_host_memory_bytes"] is None


def test_fractional_integer_fields_and_rate_suffixes_remain_unknown():
    facts = parse_impala_explain(
        """Estimated Per-Host Requirements: Memory=4GB/s VCores=1.5
00:SCAN HDFS [synthetic_db.t]
| partitions=1/2.5 files=3.5 size=4GB/s
| hosts=1.5 per-host-mem=2GB/s
| row-size=3B/s
| cardinality=10
"""
    )
    scan = node(facts, "hdfs_scan")

    assert facts["resource_estimates"]["estimated_per_host_memory_bytes"] is None
    assert facts["resource_estimates"]["estimated_vcores"] is None
    assert scan["scan_partition_state"] == "unknown"
    assert scan["scan_file_count"] is None
    assert scan["estimated_scan_bytes"] is None
    assert scan["estimated_host_count"] is None
    assert scan["estimated_per_host_memory_bytes"] is None
    assert scan["estimated_row_size_bytes"] is None
    assert scan["estimated_cardinality"] == 10.0


def test_loader_accepts_one_fixed_slot_and_fails_closed_for_duplicates(tmp_path):
    case_dir = tmp_path / "case"
    nested = case_dir / "impala_context" / "explain.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text(fixture_text("standard.txt"), encoding="utf-8")

    nested_facts = load_impala_explain_facts(case_dir)
    assert nested_facts["source_slot"] == "impala_context"
    assert nested_facts["artifact_status"] == "available"

    root = case_dir / "explain.txt"
    root.write_text(fixture_text("boxed.txt"), encoding="utf-8")
    ambiguous = load_impala_explain_facts(case_dir)
    assert ambiguous["artifact_status"] == "ambiguous"
    assert ambiguous["candidate_count"] == 2
    assert ambiguous["observed_node_count"] is None
    assert ambiguous["limitation_codes"] == ["artifact_ambiguous"]

    nested.unlink()
    root_facts = load_impala_explain_facts(case_dir)
    assert root_facts["source_slot"] == "case_root"
    assert root_facts["artifact_status"] == "available"


def test_adjacent_sql_and_query_metadata_do_not_promote_external_artifact_identity(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_dir.joinpath("explain.txt").write_text(
        "00:SCAN HDFS [synthetic_db.synthetic_table]\n", encoding="utf-8"
    )
    case_dir.joinpath("original_query.sql").write_text(
        "SELECT private_literal FROM synthetic_db.synthetic_table\n", encoding="utf-8"
    )
    case_dir.joinpath("query_metadata.json").write_text(
        json.dumps(
            {
                "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                "statement": "SELECT private_literal FROM synthetic_db.synthetic_table",
            }
        ),
        encoding="utf-8",
    )

    facts = load_impala_explain_facts(
        case_dir,
        profile_operators=[{"operator_id": "00", "operator_name": "HDFS SCAN"}],
    )

    assert facts["correlation"]["structural_match_status"] == "matched"
    assert facts["correlation"]["identity_link_basis"] == "unbound_external_artifact"
    assert facts["correlation"]["statement_identity_status"] == "unknown"
    assert facts["correlation"]["execution_identity_status"] == "unknown"
    payload = serialized(facts)
    assert "private_literal" not in payload
    assert "aaaaaaaaaaaaaaaa" not in payload


def test_loader_degrades_for_missing_invalid_utf8_oversized_and_symlink(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    missing = load_impala_explain_facts(case_dir)
    assert missing["artifact_status"] == "missing"
    assert missing["limitation_codes"] == ["artifact_missing"]

    root = case_dir / "explain.txt"
    root.write_bytes(b"\xff\xfe\xfd")
    invalid = load_impala_explain_facts(case_dir)
    assert invalid["artifact_status"] == "invalid"
    assert invalid["limitation_codes"] == ["invalid_text"]

    root.write_bytes(b"x" * 32)
    oversized = load_impala_explain_facts(case_dir, limits=ExplainParseLimits(max_bytes=16))
    assert oversized["artifact_status"] == "too_large"
    assert oversized["limitation_codes"] == ["artifact_too_large"]

    root.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text(fixture_text("standard.txt"), encoding="utf-8")
    root.symlink_to(outside)
    invalid_link = load_impala_explain_facts(case_dir)
    assert invalid_link["artifact_status"] == "invalid"
    assert invalid_link["limitation_codes"] == ["artifact_invalid"]


def test_loader_rejects_fifo_without_blocking(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    fifo = case_dir / "explain.txt"
    os.mkfifo(fifo)

    facts = load_impala_explain_facts(case_dir)

    assert facts["artifact_status"] == "invalid"
    assert facts["limitation_codes"] == ["artifact_invalid"]


def test_loader_rejects_symlinked_case_directory(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_dir.joinpath("explain.txt").write_text(fixture_text("standard.txt"), encoding="utf-8")
    case_link = tmp_path / "case-link"
    case_link.symlink_to(case_dir, target_is_directory=True)

    facts = load_impala_explain_facts(case_link)

    assert facts["artifact_status"] == "invalid"
    assert facts["limitation_codes"] == ["artifact_invalid"]


def test_unknown_projection_never_uses_zero_as_negative_evidence(tmp_path):
    missing = load_impala_explain_facts(tmp_path)
    non_plan = parse_impala_explain("This is not an Impala plan.\n")

    for facts in (missing, non_plan):
        assert facts["status"] == "unknown"
        assert facts["observed_node_count"] is None
        assert facts["observed_operator_family_counts"] is None
        assert facts["missing_stats_warning_observed"] is None
        assert facts["nodes"] is None
        assert facts["correlation"]["checked_plan_node_count"] is None
        assert facts["correlation"]["identity_link_basis"] == "no_accepted_plan_source"


def test_predicate_and_unknown_detail_text_cannot_inject_typed_facts():
    text = """07:HASH JOIN [note='BROADCAST LEFT OUTER JOIN']
| other predicates: cardinality=900 hosts=8 per-host-mem=7GB row-size=6GB missing table stats
| join op: LEFT OUTER JOIN (BROADCAST) trailing-private-value
06:SCAN HDFS [synthetic.hash]
| predicates: cardinality=500 partitions=1/9 files=8 size=7GB
| runtime filters: hosts=6 per-host-mem=5GB
| arbitrary: cardinality=400 hosts=4 size=3GB
| table stats: secret_value
| column stats: arbitrary_value
"""

    facts = parse_impala_explain(text)
    join = node(facts, "hash_join")
    scan = node(facts, "hdfs_scan")

    assert join["join_kind"] == "unknown"
    assert join["join_distribution"] == "unknown"
    assert join["estimated_cardinality"] is None
    assert join["estimated_host_count"] is None
    assert join["estimated_per_host_memory_bytes"] is None
    assert join["estimated_row_size_bytes"] is None
    assert scan["partitioning"] == "unknown"
    assert scan["estimated_cardinality"] is None
    assert scan["estimated_host_count"] is None
    assert scan["estimated_scan_bytes"] is None
    assert scan["scan_partition_state"] == "unknown"
    assert scan["table_stats_state"] == "unknown"
    assert scan["column_stats_state"] == "unknown"
    assert facts["missing_stats_warning_state"] == "not_observed"
    payload = serialized(facts)
    for marker in (
        "900",
        "7GB",
        "500",
        "1/9",
        "5GB",
        "secret_value",
        "arbitrary_value",
    ):
        assert marker not in payload


def test_opaque_multiline_sections_cannot_inject_following_typed_facts():
    facts = parse_impala_explain(
        """01:HASH JOIN [INNER JOIN, BROADCAST]
| hash predicates: synthetic_left.key = synthetic_right.key
|   cardinality=4111111111111111
00:SCAN HDFS [synthetic_db.synthetic_table]
| partitions=2/8 size=64MB
| predicates: card_number = '4111111111111111'
|   cardinality=9222222222222222
|   partitions=1/9 files=8 size=7GB
| runtime filters: RF777 -> synthetic_db.synthetic_table.key
|   hosts=6 per-host-mem=5GB
02:AGGREGATE
| output: 'private-expression'
| cardinality=9333333333333333
"""
    )

    join = node(facts, "hash_join")
    scan = node(facts, "hdfs_scan")
    aggregate = node(facts, "aggregate")
    assert join["predicate_section_state"] == "supported"
    assert join["estimated_cardinality"] is None
    assert scan["scan_partitions_selected"] == 2
    assert scan["scan_partitions_total"] == 8
    assert scan["estimated_scan_bytes"] == 64 * 1024**2
    assert scan["scan_file_count"] is None
    assert scan["estimated_cardinality"] is None
    assert scan["estimated_host_count"] is None
    assert scan["estimated_per_host_memory_bytes"] is None
    assert scan["predicate_section_state"] == "supported"
    assert scan["runtime_filter_section_state"] == "supported"
    assert aggregate["estimated_cardinality"] is None
    payload = serialized(facts)
    for marker in (
        "4111111111111111",
        "9222222222222222",
        "9333333333333333",
        "private-expression",
        "RF777",
    ):
        assert marker not in payload


def test_partition_tokens_do_not_promote_join_facts_on_non_join_nodes():
    facts = parse_impala_explain(
        """02:EXCHANGE [BROADCAST]
01:SORT [PARTITIONED]
00:SCAN HDFS [BROADCAST]
"""
    )

    for plan_node in facts["nodes"]:
        assert plan_node["join_kind"] == "unknown"
        assert plan_node["join_distribution"] == "unknown"
    assert node(facts, "exchange")["partitioning"] == "broadcast"
    assert node(facts, "sort")["partitioning"] == "unknown"
    assert node(facts, "hdfs_scan")["partitioning"] == "unknown"


def test_persisted_projection_drops_raw_plan_canaries_and_engine_local_ids():
    text = """Estimated Per-Host Requirements: Memory=16.00MB VCores=1
07:HASH JOIN [INNER JOIN, BROADCAST]
| hash predicates: private_db.customer.email = 'secret-token'
06:SCAN HDFS [private_db.customer]
| predicates: card_number = '4111111111111111'
| runtime filters: RF777 -> private_db.customer.account_id
| location: hdfs://private-host.example.invalid/private/customer
| debug path: /private/tmp/raw-plan
| cardinality=1
"""

    first = parse_impala_explain(text)
    second = parse_impala_explain(text)

    assert first == second
    payload = serialized(first)
    for marker in (
        "private_db",
        "customer",
        "email",
        "secret-token",
        "4111111111111111",
        "RF777",
        "private-host",
        "hdfs://",
        "/private/",
        "node_id",
        "fragment_id",
    ):
        assert marker not in payload


def test_structural_linkage_keeps_execution_identity_unknown():
    plan = """01:HASH JOIN [INNER JOIN, BROADCAST]\n00:SCAN HDFS [synthetic_db.t]\n"""
    matched = parse_impala_explain(
        plan,
        profile_operators=[
            {"operator_id": "01", "operator_name": "HASH JOIN"},
            {"operator_id": "00", "operator_name": "HDFS SCAN"},
        ],
    )
    mismatch = parse_impala_explain(
        plan,
        profile_operators=[
            {"operator_id": "01", "operator_name": "SORT"},
            {"operator_id": "00", "operator_name": "HDFS SCAN"},
        ],
    )
    ambiguous = parse_impala_explain(
        "01:HASH JOIN\n01:SCAN HDFS [synthetic_db.t]\n",
        profile_operators=[{"operator_id": "01", "operator_name": "HASH JOIN"}],
    )
    partial_plan = parse_impala_explain(
        "09:UNMAPPED PRIVATE NODE\n00:SCAN HDFS [synthetic_db.t]\n",
        profile_operators=[{"operator_id": "00", "operator_name": "HDFS SCAN"}],
    )
    extra_profile = parse_impala_explain(
        "00:SCAN HDFS [synthetic_db.t]\n",
        profile_operators=[
            {"operator_id": "00", "operator_name": "HDFS SCAN"},
            {"operator_id": "01", "operator_name": "HASH JOIN"},
        ],
    )
    duplicate_profile_identity = parse_impala_explain(
        "00:SCAN HDFS [synthetic_db.t]\n",
        profile_operators=[
            {"operator_id": "00", "operator_name": "HDFS SCAN"},
            {"operator_id": "00", "operator_name": "HDFS SCAN"},
        ],
    )
    unmapped_profile = parse_impala_explain(
        "00:SCAN HDFS [synthetic_db.t]\n",
        profile_operators=[
            {"operator_id": "00", "operator_name": "HDFS SCAN"},
            {"operator_id": "99", "operator_name": "PRIVATE OPERATOR"},
        ],
    )
    same_family_different_kind = parse_impala_explain(
        "00:SCAN HDFS [synthetic_db.t]\n",
        profile_operators=[{"operator_id": "00", "operator_name": "KUDU SCAN"}],
    )

    assert matched["correlation"]["structural_match_status"] == "matched"
    assert mismatch["correlation"]["structural_match_status"] == "mismatch"
    assert ambiguous["correlation"]["structural_match_status"] == "ambiguous"
    assert partial_plan["correlation"]["structural_match_status"] == "partial"
    assert extra_profile["correlation"]["structural_match_status"] == "partial"
    assert duplicate_profile_identity["correlation"]["structural_match_status"] == "ambiguous"
    assert unmapped_profile["correlation"]["structural_match_status"] == "partial"
    assert same_family_different_kind["correlation"]["structural_match_status"] == "mismatch"
    assert extra_profile["correlation"]["extra_profile_operator_count"] == 1
    assert unmapped_profile["correlation"]["unmapped_profile_operator_count"] == 1
    for facts in (
        matched,
        mismatch,
        ambiguous,
        partial_plan,
        extra_profile,
        duplicate_profile_identity,
        unmapped_profile,
        same_family_different_kind,
    ):
        assert facts["correlation"]["identity_link_basis"] == "unbound_external_artifact"
        assert facts["correlation"]["execution_identity_status"] == "unknown"
        assert facts["correlation"]["statement_identity_status"] == "unknown"
        assert facts["causal_claim_supported"] is False


def test_analyzer_writes_raw_free_explain_facts_without_changing_diagnosis(tmp_path, capsys):
    source_case = REPO_DIR / "tests" / "fixtures" / "minimal_case"
    with_explain = tmp_path / "with-explain"
    without_explain = tmp_path / "without-explain"
    shutil.copytree(source_case, with_explain)
    shutil.copytree(source_case, without_explain)
    explain_dir = with_explain / "impala_context"
    explain_dir.mkdir()
    explain_dir.joinpath("explain.txt").write_text(
        """01:HASH JOIN [INNER JOIN, BROADCAST]
| hash predicates: private_db.customer.id = 'secret-token'
00:SCAN HDFS [private_db.customer]
| predicates: card_number = '4111111111111111'
| cardinality=10
""",
        encoding="utf-8",
    )
    with_json = with_explain / "analysis.json"
    without_json = without_explain / "analysis.json"

    assert analyze_profile.main([str(with_explain), "--json", str(with_json)]) == 0
    captured = capsys.readouterr()
    assert analyze_profile.main([str(without_explain), "--json", str(without_json)]) == 0
    captured_without = capsys.readouterr()

    with_payload = json.loads(with_json.read_text(encoding="utf-8"))
    without_payload = json.loads(without_json.read_text(encoding="utf-8"))
    facts = with_payload["impala_explain"]
    assert facts["status"] == "supported"
    assert facts["observed_operator_family_counts"] == {"join": 1, "scan": 1}
    assert without_payload["impala_explain"]["status"] == "unknown"
    assert with_payload["case_primary_bottleneck"] == without_payload["case_primary_bottleneck"]
    assert with_payload["action_cards"] == without_payload["action_cards"]

    public_text = with_json.read_text(encoding="utf-8")
    terminal_text = captured.out + captured.err + captured_without.out + captured_without.err
    for marker in (
        "private_db",
        "customer.id",
        "secret-token",
        "4111111111111111",
    ):
        assert marker not in public_text
        assert marker not in terminal_text
