from query_doctor.analyzer.profile_impala_signals import build_profile_impala_signal_facts

PROFILE = """
Query (id=aaaaaaaaaaaaaaaa:0000000000000001)
  Summary:
    Tables Missing Stats: synthetic_db.fact_a, synthetic_db.dim_b
    Tables With Corrupt Table Stats: synthetic_db.fact_c
  Execution Profile aaaaaaaaaaaaaaaa:0000000000000001:(Total: 10s000ms)
    skew(s) found at: HDFS_SCAN_NODE (id=0), GroupingAggregator 0, HASH_JOIN_NODE (id=3)
    Averaged Fragment F00 [3 instances]:
      HDFS_SCAN_NODE (id=0) [3 instances]:
        Skew details: RowsRead (1000, 2000, 90000, CoV=1.52, mean=31000)
      HASH_JOIN_NODE (id=3) [3 instances]:
        Skew details: ProbeRows (10, 20, 900, CoV=2.10, mean=310)
"""


def test_impala_reported_skew_and_stats_are_counted():
    facts = build_profile_impala_signal_facts(PROFILE)

    assert facts["status"] == "observed"
    assert facts["skew_node_count"] == 3
    assert facts["skew_node_kinds"] == {
        "GroupingAggregator": 1,
        "HASH_JOIN_NODE": 1,
        "HDFS_SCAN_NODE": 1,
    }
    assert facts["max_skew_cov"] == 2.1
    assert facts["tables_missing_stats_count"] == 2
    assert facts["tables_with_corrupt_stats_count"] == 1


def test_impala_signal_facts_carry_no_table_names():
    text = repr(build_profile_impala_signal_facts(PROFILE))

    for raw in ("synthetic_db", "fact_a", "dim_b", "fact_c"):
        assert raw not in text


def test_profile_without_signals_reports_zero_counts():
    facts = build_profile_impala_signal_facts("Query (id=a:b)\n  Summary:\n    Query Type: QUERY\n")

    assert facts["status"] == "not_observed"
    assert facts["skew_node_count"] == 0
    assert facts["tables_missing_stats_count"] == 0
    assert facts["max_skew_cov"] is None


def test_unsupported_profile_dialect_is_unknown():
    assert build_profile_impala_signal_facts("")["status"] == "unknown"
