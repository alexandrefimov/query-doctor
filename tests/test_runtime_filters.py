from query_doctor.analyzer.runtime_filters import build_runtime_filter_facts


CLASSIC_TEXT_FORMAT = {"dialect": "classic_text_profile"}
COMPLETE_NODES = {"runtime_filter_effectiveness": "supported"}


def test_runtime_filter_plan_arrows_are_context_only():
    facts = build_runtime_filter_facts(
        """
F01:PLAN FRAGMENT
|  02:HASH JOIN
|  |  runtime filters: RF001[bloom] <- join_key
|  03:HDFS SCAN
|     runtime filters: RF001[bloom] -> scan_key
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "context_only"
    assert facts["evidence_tier"] == "context_only"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert facts["runtime_filter_lines"] == 2
    assert facts["plan_filter_lines"] == 2
    assert facts["runtime_filter_id_count"] == 1
    assert facts["plan_producer_lines"] == 1
    assert facts["plan_consumer_lines"] == 1
    assert facts["plan_filter_id_count"] == 1
    assert facts["producer_filter_id_count"] == 1
    assert facts["consumer_filter_id_count"] == 1
    assert facts["paired_filter_id_count"] == 1
    assert facts["producer_only_filter_id_count"] == 0
    assert facts["consumer_only_filter_id_count"] == 0
    assert facts["producer_consumer_mapping_status"] == "mapped"
    assert facts["target_scan_consumer_lines"] == 1
    assert facts["non_scan_consumer_lines"] == 0
    assert facts["unknown_target_consumer_lines"] == 0
    assert facts["target_scan_filter_id_count"] == 1
    assert facts["paired_target_scan_filter_id_count"] == 1
    assert facts["target_scan_mapping_status"] == "mapped"
    assert facts["target_scan_family_counts"] == {"hdfs": 1}
    assert facts["filter_kind_counts"] == {"bloom": 2}
    assert facts["arrival_status"] == "not_reported"
    assert any("does not independently support" in item for item in facts["limitations"])
    assert any("aggregate context only" in item for item in facts["limitations"])


def test_runtime_filter_unpaired_plan_context_stays_context_only():
    facts = build_runtime_filter_facts(
        """
F01:PLAN FRAGMENT
|  02:HASH JOIN
|  |  runtime filters: RF001[bloom] <- join_key
|  |  runtime filters: RF003[min_max] <- another_key
|  03:HDFS SCAN
|     runtime filters: RF001[bloom] -> scan_key
|     runtime filters: RF002[bloom] -> other_scan_key
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "context_only"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert facts["plan_filter_id_count"] == 3
    assert facts["producer_filter_id_count"] == 2
    assert facts["consumer_filter_id_count"] == 2
    assert facts["paired_filter_id_count"] == 1
    assert facts["producer_only_filter_id_count"] == 1
    assert facts["consumer_only_filter_id_count"] == 1
    assert facts["producer_consumer_mapping_status"] == "partial"
    assert facts["target_scan_consumer_lines"] == 2
    assert facts["target_scan_filter_id_count"] == 2
    assert facts["paired_target_scan_filter_id_count"] == 1
    assert facts["target_scan_mapping_status"] == "partial"
    assert facts["target_scan_family_counts"] == {"hdfs": 2}
    assert facts["filter_kind_counts"] == {"bloom": 3, "min_max": 1}
    assert any("not paired" in item for item in facts["limitations"])


def test_runtime_filter_non_scan_consumer_target_stays_context_only():
    facts = build_runtime_filter_facts(
        """
F01:PLAN FRAGMENT
|  02:HASH JOIN
|  |  runtime filters: RF001[bloom] <- join_key
|  04:EXCHANGE
|     runtime filters: RF001[bloom] -> exchange_key
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "context_only"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert facts["target_scan_consumer_lines"] == 0
    assert facts["non_scan_consumer_lines"] == 1
    assert facts["unknown_target_consumer_lines"] == 0
    assert facts["target_scan_filter_id_count"] == 0
    assert facts["paired_target_scan_filter_id_count"] == 0
    assert facts["target_scan_mapping_status"] == "missing_target_scan"
    assert facts["target_scan_family_counts"] == {}
    assert any(
        "target-scan mapping is aggregate context only" in item for item in facts["limitations"]
    )


def test_runtime_filter_union_branch_scan_consumer_maps_target_scan():
    facts = build_runtime_filter_facts(
        """
F01:PLAN FRAGMENT
03:HASH JOIN
|  runtime filters: RF001[min_max] <- build_key, RF002[bloom] <- other_key
|
00:UNION
|--01:SCAN KUDU
|     runtime filters: RF001[min_max] -> probe_key
|
02:SCAN HDFS
   runtime filters: RF002[bloom] -> other_probe_key
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "context_only"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert facts["producer_consumer_mapping_status"] == "mapped"
    assert facts["target_scan_consumer_lines"] == 2
    assert facts["non_scan_consumer_lines"] == 0
    assert facts["unknown_target_consumer_lines"] == 0
    assert facts["target_scan_filter_id_count"] == 2
    assert facts["paired_target_scan_filter_id_count"] == 2
    assert facts["target_scan_mapping_status"] == "mapped"
    assert facts["target_scan_family_counts"] == {"hdfs": 1, "kudu": 1}


def test_runtime_filter_arrival_gaps_stay_context_only():
    facts = build_runtime_filter_facts(
        """
HDFS_SCAN_NODE (id=3)
  Runtime filters: Not all filters arrived (arrived: [], missing [0]), waited for 803ms
  - BloomFilterBytes: 2.0 MiB (2097152)
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "context_only"
    assert facts["runtime_filter_lines"] == 1
    assert facts["plan_filter_lines"] == 0
    assert facts["plan_filter_id_count"] == 0
    assert facts["producer_consumer_mapping_status"] == "not_observed"
    assert facts["target_scan_mapping_status"] == "not_observed"
    assert facts["arrival_status"] == "missing_observed"
    assert facts["missing_arrival_lines"] == 1
    assert facts["max_arrival_wait_ms"] == 803
    assert facts["max_arrival_wait_human"] == "803ms"
    assert facts["bloom_filter_counter_lines"] == 1
    assert facts["bloom_filter_counter_nonzero_lines"] == 1
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert any("keeps them as context" in item for item in facts["limitations"])


def test_runtime_filter_routing_tables_stay_aggregate_context():
    facts = build_runtime_filter_facts(
        """
Execution Profile
  Filter routing table:
 ID  Src. Node  Tgt. Node(s)  Target type  Partition filter  Pending (Expected)  First arrived  Completed   Enabled
-------------------------------------------------------------------------------------------------------------------
  1          3             4        LOCAL             false              0 (4)           12ms       14ms      true
  0          3             5       GLOBAL              true              2 (4)            N/A        N/A     false
  Backend startup latencies: Count: 4
  Final filter table:
 ID  Src. Node  Tgt. Node(s)  Target type  Partition filter  Pending (Expected)  First arrived  Completed   Enabled
-------------------------------------------------------------------------------------------------------------------
  1          3             4        LOCAL             false              0 (4)           12ms       14ms      true
  0          3             5       GLOBAL              true              1 (4)            N/A       20ms      true
  Per Node Peak Memory Usage: host_01(1 B)
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "context_only"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert facts["routing_table_status"] == "observed"
    assert facts["routing_filter_count"] == 2
    assert facts["final_filter_count"] == 2
    assert facts["enabled_filter_count"] == 2
    assert facts["partition_filter_count"] == 1
    assert facts["pending_nonzero_count"] == 1
    assert facts["arrival_observed_count"] == 1
    assert facts["completed_observed_count"] == 2
    assert facts["target_type_counts"] == {"global": 1, "local": 1}
    assert any("routing table context is aggregate only" in item for item in facts["limitations"])


def test_modern_runtime_filter_tables_are_read_by_header():
    # Impala 4.x adds Eff. Tgt. Node(s) and the Bloom Size .. In-list size tail.
    facts = build_runtime_filter_facts(
        """
Execution Profile
  Filter routing table:
 ID  Src. Node  Tgt. Node(s)  Eff. Tgt. Node(s)  Target type  Partition filter  Pending (Expected)  First arrived  Completed  Enabled  Bloom Size   Est fpp  Min value            Max value            In-list size
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  1          3             4                  4        LOCAL             false              0 (4)           12ms       14ms     true    1.00 MB  1.2e-03
  0          3             5                  5       GLOBAL              true              2 (4)            N/A        N/A    false                     2026-01-01 00:00:00  2026-01-31 23:59:59
  Backend startup latencies: Count: 4
  Final filter table:
 ID  Src. Node  Tgt. Node(s)  Eff. Tgt. Node(s)  Target type  Partition filter  Pending (Expected)  First arrived  Completed  Enabled  Bloom Size   Est fpp  Min value            Max value            In-list size
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  1          3             4                  4        LOCAL             false              0 (4)           12ms       14ms     true    1.00 MB  1.2e-03
  0          3             5                  5       GLOBAL              true              1 (4)            N/A       20ms     true                     2026-01-01 00:00:00  2026-01-31 23:59:59
  Per Node Peak Memory Usage: host_01(1 B)
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["routing_table_status"] == "observed"
    assert facts["routing_filter_count"] == 2
    assert facts["final_filter_count"] == 2
    assert facts["enabled_filter_count"] == 2
    assert facts["partition_filter_count"] == 1
    assert facts["pending_nonzero_count"] == 1
    assert facts["arrival_observed_count"] == 1
    assert facts["completed_observed_count"] == 2
    assert facts["target_type_counts"] == {"global": 1, "local": 1}


def test_local_mode_runtime_filter_table_has_no_arrival_columns():
    facts = build_runtime_filter_facts(
        """
Execution Profile
  Final filter table:
 ID  Src. Node  Tgt. Node(s)  Eff. Tgt. Node(s)  Target type  Partition filter  Enabled  Bloom Size  Est fpp  Min value  Max value  In-list size
----------------------------------------------------------------------------------------------------------------------------------------------
  1          3             4                  4        LOCAL             false     true     1.00 MB  1.2e-03
  Per Node Peak Memory Usage: host_01(1 B)
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["final_filter_count"] == 1
    assert facts["enabled_filter_count"] == 1
    assert facts["pending_nonzero_count"] == 0
    assert facts["arrival_observed_count"] == 0
    assert facts["target_type_counts"] == {"local": 1}


def test_empty_runtime_filter_tables_are_not_observed():
    facts = build_runtime_filter_facts(
        """
Execution Profile
  Number of filters: 0
  Filter routing table:
 ID  Src. Node  Tgt. Node(s)  Target type  Partition filter  Pending (Expected)  First arrived  Completed   Enabled
-------------------------------------------------------------------------------------------------------------------
  Backend startup latencies: Count: 4
  Final filter table:
 ID  Src. Node  Tgt. Node(s)  Target type  Partition filter  Pending (Expected)  First arrived  Completed   Enabled
-------------------------------------------------------------------------------------------------------------------
  Per Node Peak Memory Usage: Count: 1
""",
        CLASSIC_TEXT_FORMAT,
        COMPLETE_NODES,
    )

    assert facts["status"] == "not_observed"
    assert facts["routing_table_status"] == "not_observed"
    assert facts["routing_filter_count"] == 0
    assert facts["final_filter_count"] == 0
    assert facts["target_type_counts"] == {}
    assert not any(
        "routing table context is aggregate only" in item for item in facts["limitations"]
    )


def test_runtime_filter_unknown_dialect_fails_closed():
    facts = build_runtime_filter_facts(
        "runtime filters: RF001[bloom] <- join_key\n",
        {"dialect": "classic_json_profile"},
        COMPLETE_NODES,
    )

    assert facts["status"] == "unknown"
    assert facts["evidence_tier"] == "unsupported"
    assert facts["runtime_filter_lines"] == 0
    assert facts["plan_filter_lines"] == 0
    assert facts["runtime_filter_id_count"] == 0
    assert facts["plan_filter_id_count"] == 0
    assert facts["producer_consumer_mapping_status"] == "unknown"
    assert facts["target_scan_mapping_status"] == "unknown"
    assert facts["routing_table_status"] == "unknown"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
    assert any("not interpreted for this profile dialect" in item for item in facts["limitations"])


def test_runtime_filter_incomplete_nodes_limit_effectiveness():
    facts = build_runtime_filter_facts(
        "runtime filters: RF001[bloom] -> scan_key\n",
        CLASSIC_TEXT_FORMAT,
        {"runtime_filter_effectiveness": "limited"},
    )

    assert facts["status"] == "context_only"
    assert facts["exec_node_runtime_filter_effectiveness"] == "limited"
    assert any("Exec-node completeness limits" in item for item in facts["limitations"])


def test_runtime_filter_absence_is_not_observed():
    facts = build_runtime_filter_facts("", CLASSIC_TEXT_FORMAT, COMPLETE_NODES)

    assert facts["status"] == "not_observed"
    assert facts["evidence_tier"] == "unsupported"
    assert facts["arrival_status"] == "not_observed"
    assert facts["producer_consumer_mapping_status"] == "not_observed"
    assert facts["target_scan_mapping_status"] == "not_observed"
    assert facts["routing_table_status"] == "not_observed"
    assert facts["finding_supported"] is False
    assert facts["primary_supported"] is False
