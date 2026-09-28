from query_doctor.analyzer.profile_scan_io import build_profile_scan_io_facts

# Two instances of one scan plus an averaged copy that must not be counted.
PROFILE = """
Query (id=aaaaaaaaaaaaaaaa:0000000000000001)
  Execution Profile aaaaaaaaaaaaaaaa:0000000000000001:(Total: 10s000ms)
    Per Node Profiles:
      host_01:22000:
        Fragment F00:
          CodeGen:
             - CompileTime: 10.000ms
    Averaged Fragment F00 [2 instances]:(Total: 9s000ms)
      HDFS_SCAN_NODE (id=0) [2 instances]:(Total: 1s000ms)
         - DataCacheHitBytes: 999.00 GB (1072668082176)
         - NumRowGroups: 999 (999)
    Fragment F00:
      Instance aaaaaaaaaaaaaaaa:0000000000000002 (host=host_01:22000):(Total: 9s000ms)
        HDFS_SCAN_NODE (id=0):
          Table Name: synthetic_db.synthetic_table
          Node Lifecycle Event Timeline: 1s000ms
             - Open Started: 10.000ms (10.000ms)
           - DataCacheHitBytes: 3.00 MB (3145728)
           - DataCacheMissBytes: 1.00 MB (1048576)
           - DataCachePartialHitCount: 1 (1)
           - NumRowGroups: 10 (10)
           - NumStatsFilteredRowGroups: 4 (4)
           - NumRuntimeFilteredRowGroups: 2 (2)
           - NumBloomFilteredRowGroups: 1 (1)
           - NumRowGroupsWithPageIndex: 10 (10)
           - NumPages: 100 (100)
           - NumStatsFilteredPages: 20 (20)
           - NumRuntimeFilteredPages: 5 (5)
           - NumPagesSkippedByLateMaterialization: 30 (30)
           - NumFileMetadataRead: 0 (0)
          Filter 0 (1.00 MB):
             - Files rejected: 1 (1)
             - RowGroups rejected: 2 (2)
             - Rows processed: 1.00K (1000)
             - Rows rejected: 400 (400)
             - Rows total: 5.00K (5000)
      Instance aaaaaaaaaaaaaaaa:0000000000000003 (host=host_02:22000):(Total: 9s000ms)
        HDFS_SCAN_NODE (id=0):
           - DataCacheHitBytes: 1.00 MB (1048576)
           - DataCacheMissBytes: 3.00 MB (3145728)
           - NumRowGroups: 10 (10)
           - NumRowGroupsWithPageIndex: 0 (0)
          Filter 0 (1.00 MB):
             - Rows processed: 2.00K (2000)
             - Rows rejected: 100 (100)
             - Rows total: 2.00K (2000)
        TUPLE_CACHE_NODE (id=3):
           - NumTupleCacheHits: 1 (1)
           - NumTupleCacheSkipped: 0 (0)
           - TupleCacheBytesRead: 2.00 MB (2097152)
    Coordinator Fragment F01:
      Instance aaaaaaaaaaaaaaaa:0000000000000000 (host=host_01:22000):(Total: 10s000ms)
        TUPLE_CACHE_NODE (id=4):
           - NumTupleCacheHits: 0 (0)
           - NumTupleCacheHalted: 1 (1)
           - TupleCacheBytesWritten: 5.00 MB (5242880)
"""


def test_scan_counters_are_summed_over_instances_only():
    facts = build_profile_scan_io_facts(PROFILE)

    scan = facts["scan_io"]
    assert scan["status"] == "observed"
    assert scan["scan_instance_count"] == 2
    assert scan["data_cache_hit_bytes"] == 4 * 1024**2
    assert scan["data_cache_miss_bytes"] == 4 * 1024**2
    assert scan["data_cache_hit_ratio"] == 0.5
    assert scan["row_groups"] == 20
    assert scan["stats_filtered_row_groups"] == 4
    assert scan["runtime_filtered_row_groups"] == 2
    assert scan["bloom_filtered_row_groups"] == 1
    assert scan["row_groups_with_page_index"] == 10
    assert scan["pages"] == 100
    assert scan["stats_filtered_pages"] == 20
    assert scan["runtime_filtered_pages"] == 5
    assert scan["pages_skipped_by_late_materialization"] == 30


def test_runtime_filter_sections_count_rejections_and_partial_checks():
    scan = build_profile_scan_io_facts(PROFILE)["scan_io"]

    assert scan["filter_rows_rejected"] == 500
    assert scan["filter_files_rejected"] == 1
    # The first filter stopped checking after 1000 of 5000 rows.
    assert scan["filter_sections_checked_partially"] == 1
    assert scan["filter_sections"] == 2


def test_tuple_cache_nodes_are_summed_over_instances():
    cache = build_profile_scan_io_facts(PROFILE)["tuple_cache"]

    assert cache["status"] == "observed"
    assert cache["node_instance_count"] == 2
    assert cache["hits"] == 1
    assert cache["halted"] == 1
    assert cache["bytes_read"] == 2 * 1024**2
    assert cache["bytes_written"] == 5 * 1024**2


def test_facts_carry_no_names_from_the_profile():
    text = repr(build_profile_scan_io_facts(PROFILE))

    for raw in ("synthetic_db", "synthetic_table", "host_01", "aaaaaaaaaaaaaaaa"):
        assert raw not in text


def test_profile_without_instances_is_not_observed():
    facts = build_profile_scan_io_facts("Query (id=a:b)\n  Summary:\n    Query Type: QUERY\n")

    assert facts["scan_io"]["status"] == "not_observed"
    assert facts["tuple_cache"]["status"] == "not_observed"


def test_unsupported_profile_dialect_is_unknown():
    facts = build_profile_scan_io_facts("")

    assert facts["scan_io"]["status"] == "unknown"
    assert facts["tuple_cache"]["status"] == "unknown"
