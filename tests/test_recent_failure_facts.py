import json

import pytest

from query_doctor.recent.failure_facts import (
    IMPALA_FAILURE_FACTS_CONTRACT,
    MAX_ERROR_MESSAGE_CHARS,
    build_impala_failure_facts,
)


def profile(
    status: str, *, query_type: str = "QUERY", state: str = "EXCEPTION", body: str = ""
) -> str:
    """A synthetic profile shaped like an Impala 4 plain-text profile."""

    return (
        "Query (id=4f2a00000000beef:77aa000000000000):\n"
        "  DEBUG MODE WARNING: Query profile created while running a DEBUG build of Impala.\n"
        "  Summary:\n"
        "    Session ID: 1111222233334444:5555666677778888\n"
        "    Session Type: HIVESERVER2\n"
        "    HiveServer2 Protocol Version: V6\n"
        "    Start Time: 2026-09-25 08:00:00.000000000\n"
        "    End Time: 2026-09-25 08:00:05.000000000\n"
        "    Duration: 5s000ms\n"
        f"    Query Type: {query_type}\n"
        f"    Query State: {state}\n"
        "    Impala Query State: ERROR\n"
        f"    Query Status: {status}\n"
        "    Impala Version: impalad version 4.5.0-RELEASE RELEASE (build 0000000)\n"
        "    User: <user>\n"
        "    Connected User: <user>\n"
        "    Delegated User: \n"
        "    Network Address: host_01:50000\n"
        "    Default Db: sales\n"
        "    Sql Statement: select *\n"
        "  from sales.orders o\n"
        "  join sales.items i on o.id = i.order_id\n"
        "    Coordinator: host_02:27000\n"
        f"{body}"
    )


ADMITTED_BODY = (
    "    Query Options (set by configuration and planner): MT_DOP=0\n"
    "    Plan: \n"
    "----------------\n"
    "Max Per-Host Resource Reservation: Memory=8.00MB Threads=3\n"
    "  |  output: count(*)\n"
    "01:EXCHANGE [UNPARTITIONED]\n"
    "----------------\n"
    "    Estimated Per-Host Mem: 1073741824\n"
    "    Request Pool: <pool>\n"
    "    Per Host Min Memory Reservation: host_02:27000(8.00 MB) host_03:32700(8.00 MB)\n"
    "    Admission result: Admitted immediately\n"
    "    Cluster Memory Admitted: 256.00 GB\n"
    "    Executor Group: root.default-group-000\n"
    "    ExecSummary: \n"
    "Operator       #Hosts  #Inst   Avg Time\n"
    "    Errors: \n"
    "    Query Compilation: 250.000ms\n"
    "       - Metadata load started: 1.000ms (1.000ms)\n"
    "    Query Timeline: 5s000ms\n"
    "       - Query submitted: 0.000ns (0.000ns)\n"
    "       - Planning finished: 250.000ms (250.000ms)\n"
    "       - Submit for admission: 251.000ms (1.000ms)\n"
    "       - Completed admission: 401.000ms (150.000ms)\n"
    "       - Ready to start on 8 backends: 420.000ms (19.000ms)\n"
    "       - All 8 execution backends (20 fragment instances) started: 500.000ms (80.000ms)\n"
    "       - Execution error: 2s500ms (1s900ms)\n"
    "       - Released admission control resources: 4s900ms (2s027ms)\n"
    "       - Unregister query: 5s000ms (100.000ms)\n"
    "    Frontend:\n"
    "  ImpalaServer:\n"
    "     - ClientFetchWaitTimer: 0.000ns\n"
    "  Execution Profile 4f2a00000000beef:77aa000000000000:(Total: 4s900ms, non-child: 0.000ns)\n"
    "    Number of filters: 0\n"
    "    Per Node Peak Memory Usage: host_02:27000(0) host_03:32700(12.75 GB) host_04:32700(1.50 GB)\n"
)


def test_memory_limit_names_this_query_trackers_and_the_failing_node():
    status = (
        "Memory limit exceeded: Error occurred on backend host_03:32700 by fragment "
        "4f2a00000000beef:77aa000000000003\n"
        "Memory left in process limit: 100.00 GB\n"
        # A process-limit dump lists other queries on the node first.
        "Query(9999000000000000:1111000000000000): Limit=64.00 GB Reservation=30.00 GB "
        "ReservationLimit=48.00 GB OtherMemory=10.00 MB Total=30.01 GB Peak=30.01 GB\n"
        "Query(4f2a00000000beef:77aa000000000000): Limit=16.00 GB Reservation=12.00 GB "
        "ReservationLimit=12.80 GB OtherMemory=20.00 MB Total=12.00 GB Peak=12.50 GB\n"
        "  Fragment 4f2a00000000beef:77aa000000000003: Reservation=11.00 GB OtherMemory=1.00 MB "
        "Total=11.00 GB Peak=11.50 GB\n"
        "    HASH_JOIN_NODE (id=3): Reservation=11.00 GB OtherMemory=0 Total=11.00 GB Peak=11.50 GB"
    )
    facts = build_impala_failure_facts(profile(status, body=ADMITTED_BODY), include_error_text=True)

    assert facts["facts_contract"] == IMPALA_FAILURE_FACTS_CONTRACT
    assert facts["error_category"] == "memory_limit"
    assert facts["error_class"] == "memory_limit_exceeded"
    assert facts["outcome"] == "failed"
    assert "cancellation_source" not in facts
    gib = 1024**3
    assert facts["memory"] == {
        "query_limit_bytes": 16 * gib,
        "query_total_bytes_at_failure": 12 * gib,
        "query_peak_bytes_at_failure": round(12.5 * gib),
        "process_memory_left_bytes": 100 * gib,
        "per_node_peak_max_bytes": round(12.75 * gib),
        "per_node_peak_max_node": "host_03:32700",
        "node_count": 3,
        "cluster_memory_admitted_bytes": 256 * gib,
        "estimated_per_host_bytes": 1073741824,
    }
    assert facts["failing_node"] == "host_03:32700"
    assert facts["failing_fragment"] == "4f2a00000000beef:77aa000000000003"
    assert facts["admission"] == {"result": "Admitted immediately"}
    assert facts["backend_count"] == 8
    assert facts["timings"] == {
        "duration_ms": 5000,
        "planning_ms": 250,
        "admission_wait_ms": 150,
        "failure_at_ms": 2500,
    }
    # The compilation timeline is not the query timeline.
    assert [event["event"] for event in facts["timeline"]] == [
        "query_submitted",
        "planning_finished",
        "submit_for_admission",
        "completed_admission",
        "ready_to_start",
        "all_backends_started",
        "execution_error",
        "released_admission_resources",
        "unregister_query",
    ]
    assert facts["default_db"] == "sales"
    assert facts["error_message"].startswith("Memory limit exceeded: Error occurred on backend")
    assert "HASH_JOIN_NODE (id=3)" in facts["error_message"]
    assert facts["error_message_truncated"] is False


@pytest.mark.parametrize(
    ("status", "category", "error_class"),
    [
        (
            "ParseException: Syntax error in line 1:\nselect * frm orders\n         ^\n"
            "Encountered: IDENTIFIER\nExpected: FROM\n\nCAUSED BY: Exception: Syntax error",
            "syntax",
            "ParseException",
        ),
        (
            "AnalysisException: Could not resolve table reference: 'sales.missing'",
            "analysis",
            "AnalysisException",
        ),
        (
            "TableNotFoundException: Table not found: sales.missing",
            "analysis",
            "TableNotFoundException",
        ),
        (
            "AuthorizationException: User '<user>' does not have privileges to access: sales.orders",
            "authorization",
            "AuthorizationException",
        ),
        (
            "PatternSyntaxException: Illegal repetition near index 2",
            "runtime",
            "PatternSyntaxException",
        ),
        ("UDF ERROR: Cannot divide decimal by zero", "other", "other"),
    ],
)
def test_analysis_stage_failures_have_no_timeline_beyond_planning(status, category, error_class):
    body = (
        "    Query Timeline: 12.000ms\n"
        "       - Query submitted: 0.000ns (0.000ns)\n"
        "       - Cancelled: 11.000ms (11.000ms)\n"
        "       - Unregister query: 12.000ms (1.000ms)\n"
        "  ImpalaServer:\n"
    )
    facts = build_impala_failure_facts(profile(status, query_type="N/A", body=body))

    assert facts["error_category"] == category
    assert facts["error_class"] == error_class
    assert facts["outcome"] == "failed"
    # N/A is not a statement type; the summary row carries the verb.
    assert "query_type" not in facts
    assert facts["timings"] == {"duration_ms": 5000, "failure_at_ms": 11}
    # Without the opt-in no text leaves the profile: status, database, reasons.
    serialized = json.dumps(facts)
    for raw in ("sales", "orders", "frm", "privileges", "decimal", "index 2"):
        assert raw not in serialized
    assert "error_message" not in facts and "default_db" not in facts


@pytest.mark.parametrize(
    ("status", "category", "source", "outcome"),
    [
        ("Cancelled", "cancelled", "client", "cancelled"),
        ("Cancelled (queued)", "cancelled", "admission_queue", "cancelled"),
        (
            "Cancelled from Impala's debug web interface by user: '<user>' at host_05:25000",
            "cancelled",
            "web_ui",
            "cancelled",
        ),
        (
            "Query 4f2a00000000beef:77aa000000000000 expired due to client inactivity (timeout is 10m)",
            "idle_query_timeout",
            "idle_query_timeout",
            "cancelled",
        ),
        (
            "Session expired due to inactivity",
            "idle_session_timeout",
            "idle_session_timeout",
            "cancelled",
        ),
        (
            "Session closed because it has no active connections",
            "session_closed",
            "session_closed",
            "cancelled",
        ),
        (
            "Query 4f2a00000000beef:77aa000000000000 expired due to execution time limit of 1h",
            "execution_time_limit",
            "execution_time_limit",
            "failed",
        ),
        (
            "Query 4f2a00000000beef:77aa000000000000 terminated due to scan bytes limit of 1.00 TB",
            "resource_limit",
            "resource_limit",
            "failed",
        ),
    ],
)
def test_cancellations_and_timeouts_name_their_source(status, category, source, outcome):
    facts = build_impala_failure_facts(profile(status, body=ADMITTED_BODY))

    assert (facts["error_category"], facts["cancellation_source"], facts["outcome"]) == (
        category,
        source,
        outcome,
    )


@pytest.mark.parametrize(
    ("status", "category", "reason_field"),
    [
        (
            "Rejected query from pool root.adhoc: request memory needed 120.00 GB is greater "
            "than pool max mem resources 100.00 GB.",
            "admission_rejected",
            "Latest admission queue reason",
        ),
        (
            "Admission for query exceeded timeout 60000ms in pool root.adhoc. Queued reason: "
            "Not enough aggregate memory available in pool root.adhoc with max mem resources 1.00 TB.",
            "admission_timeout",
            "Initial admission queue reason",
        ),
    ],
)
def test_admission_failures_keep_the_result_and_the_queue_reason(status, category, reason_field):
    body = (
        "    Request Pool: <pool>\n"
        "    Admission result: Rejected\n"
        f"    {reason_field}: Not enough aggregate memory available in pool root.adhoc\n"
        "    Query Timeline: 60s002ms\n"
        "       - Query submitted: 0.000ns (0.000ns)\n"
        "       - Planning finished: 40.000ms (40.000ms)\n"
        "       - Submit for admission: 41.000ms (1.000ms)\n"
        "       - Completed admission: 60s001ms (59s960ms)\n"
        "       - Unregister query: 60s002ms (1.000ms)\n"
        "  ImpalaServer:\n"
    )
    facts = build_impala_failure_facts(profile(status, body=body), include_error_text=True)

    assert facts["error_category"] == category
    assert facts["admission"] == {
        "result": "Rejected",
        "queue_reason": "Not enough aggregate memory available in pool root.adhoc",
    }
    assert facts["timings"]["admission_wait_ms"] == 59960
    assert "failure_at_ms" not in facts["timings"]
    assert "queue_reason" not in build_impala_failure_facts(profile(status, body=body))["admission"]


def test_disk_io_error_keeps_the_node_and_drops_java_frames():
    status = (
        "Disk I/O error on host_07:32700: Failed to open HDFS file hdfs://nn/warehouse/t/part-0\n"
        "Error(2): No such file or directory\n"
        "Root cause: RemoteException: File does not exist: /warehouse/t/part-0\n"
        "\tat org.apache.hadoop.hdfs.server.namenode.INodeFile.valueOf(INodeFile.java:85)\n"
        "\tat org.apache.hadoop.ipc.Server$Handler.run(Server.java:2680)\n"
    )
    facts = build_impala_failure_facts(profile(status, body=ADMITTED_BODY), include_error_text=True)

    assert facts["error_category"] == "io"
    assert facts["failing_node"] == "host_07:32700"
    assert "Root cause: RemoteException" in facts["error_message"]
    assert "INodeFile" not in facts["error_message"]
    assert facts["error_message_truncated"] is True


def test_long_status_is_cut_to_the_bound():
    status = "AnalysisException: " + "x" * 5000
    facts = build_impala_failure_facts(profile(status), include_error_text=True)

    assert len(facts["error_message"]) == MAX_ERROR_MESSAGE_CHARS
    assert facts["error_message_truncated"] is True
    assert len(json.dumps(facts)) < 10 * 1024


def test_excerpt_with_the_middle_removed_still_yields_the_summary():
    text = profile("Cancelled", body=ADMITTED_BODY)
    head, tail = text[: text.index("  ImpalaServer:")], text[text.index("  Execution Profile") :]
    facts = build_impala_failure_facts(head + "\n[... profile excerpt omitted ...]\n" + tail)

    assert facts["cancellation_source"] == "client"
    assert facts["admission"] == {"result": "Admitted immediately"}
    assert facts["memory"]["per_node_peak_max_node"] == "host_03:32700"

    only_head = build_impala_failure_facts(text[: text.index("    Plan:")])
    assert only_head["error_category"] == "cancelled"
    assert "admission" not in only_head and "timeline" not in only_head


def test_successful_query_and_unrecognized_text_have_no_facts():
    finished = profile("OK", state="FINISHED")
    assert build_impala_failure_facts(finished) is None
    assert build_impala_failure_facts("not a profile") is None
    assert build_impala_failure_facts("") is None


def test_json_wrapped_profile_text_is_unwrapped():
    wrapped = json.dumps({"profile": profile("Cancelled", body=ADMITTED_BODY)})

    assert build_impala_failure_facts(wrapped)["cancellation_source"] == "client"
