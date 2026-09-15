import http.client
import json
import urllib.error

import pytest

from query_doctor.cli import collect_impala_profile
from query_doctor.cm.models import CMAdapterError
from query_doctor.impala.admission_context import (
    build_admission_context,
    fetch_impala_admission_context,
    impala_admission_context_urls,
)
from query_doctor.impala.daemon_identity import fetch_impala_daemon_identity
from query_doctor.impala.profile_docs import (
    fetch_impala_profile_docs_context,
    impala_profile_docs_urls,
    profile_docs_counter_labels,
)
from query_doctor.impala.profile_source import (
    PROFILE_MARKER_SCAN_CHARS,
    extract_profile_text_from_response,
    fetch_impala_profile_text,
    impala_profile_urls,
    profile_text_looks_like_runtime_profile,
)
from query_doctor.impala.query_discovery import (
    fetch_impala_query_summaries,
    impala_query_list_urls,
    parse_duration_text,
)


class FakeResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size: int) -> bytes:
        return self.body


class BrokenReadResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size: int) -> bytes:
        raise http.client.IncompleteRead(b"partial")


def test_impala_profile_urls_target_explicit_query_on_each_impalad_host():
    urls = impala_profile_urls(
        ["impalad-1.example.com", "impalad-3.example.com:25001"],
        query_id="abc:def",
        port=25000,
        scheme="http",
    )

    assert urls == (
        "http://impalad-1.example.com:25000/query_profile?query_id=abc%3Adef&format=text",
        "http://impalad-1.example.com:25000/query_profile?query_id=abc%3Adef",
        "http://impalad-3.example.com:25001/query_profile?query_id=abc%3Adef&format=text",
        "http://impalad-3.example.com:25001/query_profile?query_id=abc%3Adef",
    )


def test_impala_profile_urls_can_prefer_json_without_removing_text_fallback():
    urls = impala_profile_urls(
        ["impalad-1.example.com"],
        query_id="abc:def",
        prefer_json=True,
    )

    assert urls == (
        "http://impalad-1.example.com:25000/query_profile?query_id=abc%3Adef&format=json",
        "http://impalad-1.example.com:25000/query_profile?query_id=abc%3Adef&format=text",
        "http://impalad-1.example.com:25000/query_profile?query_id=abc%3Adef",
    )


def test_impala_query_list_urls_target_each_impalad_host():
    urls = impala_query_list_urls(
        ["impalad-1.example.com", "impalad-3.example.com:25001"],
        port=25000,
        scheme="http",
    )

    assert urls == (
        "http://impalad-1.example.com:25000/queries?json",
        "http://impalad-1.example.com:25000/queries?json=true",
        "http://impalad-3.example.com:25001/queries?json",
        "http://impalad-3.example.com:25001/queries?json=true",
    )


def test_impala_profile_docs_urls_target_each_impalad_host():
    urls = impala_profile_docs_urls(
        ["impalad-1.example.com", "impalad-3.example.com:25001"],
        port=25000,
        scheme="http",
    )

    assert urls == (
        "http://impalad-1.example.com:25000/profile_docs/?json",
        "http://impalad-1.example.com:25000/profile_docs",
        "http://impalad-3.example.com:25001/profile_docs/?json",
        "http://impalad-3.example.com:25001/profile_docs",
    )


def test_impala_admission_context_urls_target_each_impalad_host():
    urls = impala_admission_context_urls(
        ["impalad-1.example.com", "impalad-3.example.com:25001"],
        port=25000,
        scheme="http",
    )

    assert urls == (
        "http://impalad-1.example.com:25000/admission?json",
        "http://impalad-3.example.com:25001/admission?json",
    )


def test_build_admission_context_keeps_only_safe_aggregate_pool_facts():
    context = build_admission_context(
        {
            "admission_pools": {
                "root.analytics": {
                    "queued_queries": [{"query_id": "raw-query-id"}],
                    "running_queries": [{"stmt": "SELECT secret_col FROM sensitive_table"}],
                    "avg_queue_time_ms": 7_000,
                }
            },
            "statestore": {"is_stale": False},
        },
        target_pool="root.analytics",
    )

    context_text = json.dumps(context)
    assert context["status"] == "available"
    assert context["scope"] == "selected_pool"
    assert context["queue_present"] == "yes"
    assert context["running_present"] == "yes"
    assert context["queued_pool_count"] == 1
    assert context["avg_queue_time_bucket"] == "5s_30s"
    assert context["pool_pressure"] == "medium"
    assert context["freshness"] == "fresh"
    assert "root.analytics" not in context_text
    assert "raw-query-id" not in context_text
    assert "secret_col" not in context_text
    assert "sensitive_table" not in context_text


def test_fetch_impala_admission_context_is_unavailable_for_old_endpoint():
    def fake_opener(_request, timeout):
        raise urllib.error.URLError("not here")

    result = fetch_impala_admission_context(
        hosts=["impalad-1.example.com"],
        opener=fake_opener,
    )

    assert result.context["status"] == "unavailable"
    assert result.context["reason"] == "request_failed"
    assert result.attempted_endpoints == 1


def test_fetch_impala_admission_context_is_unavailable_for_incomplete_read():
    def fake_opener(_request, timeout):
        return BrokenReadResponse()

    result = fetch_impala_admission_context(
        hosts=["impalad-1.example.com"],
        opener=fake_opener,
    )

    assert result.context["status"] == "unavailable"
    assert result.context["reason"] == "request_failed"
    assert result.attempted_endpoints == 1


def test_profile_docs_counter_labels_parse_impala_profile_docs_shape():
    labels = profile_docs_counter_labels(
        {
            "profile_docs": [
                {
                    "name": "ClientFetchWaitTimer",
                    "significance": "STABLE_LOW",
                    "description": "not collected",
                    "unit": "TIME_NS",
                },
                {"name": "SpilledBytes", "significance": "STABLE_HIGH"},
                {"name": "DebugOnly", "significance": "DEBUG"},
            ],
            "significance_docs": [{"name": "STABLE_HIGH", "description": "not collected"}],
        }
    )

    assert labels == {
        "ClientFetchWaitTimer": "STABLE_LOW",
        "SpilledBytes": "STABLE_HIGH",
        "DebugOnly": "DEBUG",
    }


def test_profile_docs_counter_labels_parse_impala_profile_docs_html_shape():
    labels = profile_docs_counter_labels(
        """
        <html><body>
          <table>
            <tr><th>Name</th><th>Significance</th><th>Unit</th><th>Description</th></tr>
            <tr><td>ClientFetchWaitTimer</td><td>STABLE &amp; LOW</td><td>TIME_NS</td><td>not collected</td></tr>
            <tr><td>SpilledBytes</td><td>STABLE &amp; HIGH</td><td>BYTES</td><td>not collected</td></tr>
            <tr><td>DebugOnly</td><td>DEBUG</td><td>UNIT</td><td>not collected</td></tr>
          </table>
          <table>
            <tr><th>Significance</th><th>Description</th></tr>
            <tr><td>UNSTABLE</td><td>not a counter row</td></tr>
          </table>
        </body></html>
        """
    )

    assert labels == {
        "ClientFetchWaitTimer": "STABLE_LOW",
        "SpilledBytes": "STABLE_HIGH",
        "DebugOnly": "DEBUG",
    }


def test_fetch_impala_profile_docs_context_writes_only_known_registry_entries():
    def fake_opener(request, timeout):
        assert request.full_url == "http://impalad-1.example.com:25000/profile_docs/?json"
        return FakeResponse(
            json.dumps(
                {
                    "profile_docs": [
                        {"name": "ClientFetchWaitTimer", "significance": "STABLE_LOW"},
                        {"name": "SpilledBytes", "significance": "STABLE_HIGH"},
                        {"name": "UnrelatedCounter", "significance": "STABLE_HIGH"},
                    ]
                }
            )
        )

    result = fetch_impala_profile_docs_context(
        hosts=["impalad-1.example.com"],
        impala_version="4.5.0",
        opener=fake_opener,
    )

    context = result.context
    entries = context["entries"]
    assert result.attempted_endpoints == 1
    assert context["status"] == "available"
    assert context["source_counter_count"] == 3
    assert all("description" not in entry for entry in entries)
    assert not any(entry["canonical_name"] == "UnrelatedCounter" for entry in entries)


def test_fetch_impala_profile_docs_context_accepts_html_table_shape():
    def fake_opener(request, timeout):
        if request.full_url.endswith("/profile_docs/?json"):
            raise urllib.error.URLError("json docs unavailable")
        assert request.full_url == "http://impalad-1.example.com:25000/profile_docs"
        return FakeResponse(
            """
            <html><body>
              <table>
                <tr><th>Name</th><th>Significance</th><th>Unit</th><th>Description</th></tr>
                <tr><td>ClientFetchWaitTimer</td><td>STABLE &amp; LOW</td><td>TIME_NS</td><td>not collected</td></tr>
                <tr><td>SpilledBytes</td><td>STABLE &amp; HIGH</td><td>BYTES</td><td>not collected</td></tr>
                <tr><td>UnrelatedCounter</td><td>DEBUG</td><td>UNIT</td><td>not collected</td></tr>
              </table>
            </body></html>
            """
        )

    result = fetch_impala_profile_docs_context(
        hosts=["impalad-1.example.com"],
        impala_version="5.0.0-SNAPSHOT",
        opener=fake_opener,
    )

    context = result.context
    entries = context["entries"]
    assert result.attempted_endpoints == 2
    assert context["status"] == "available"
    assert context["source"] == "profile_docs"
    assert context["source_counter_count"] == 3
    assert any(
        entry["canonical_name"] == "ClientFetchWaitTimer"
        and entry["stability_label"] == "STABLE_LOW"
        for entry in entries
    )
    assert all("description" not in entry for entry in entries)
    assert not any(entry["canonical_name"] == "UnrelatedCounter" for entry in entries)


def test_fetch_impala_profile_docs_context_is_unavailable_for_old_endpoint():
    def fake_opener(_request, timeout):
        raise urllib.error.URLError("not here")

    result = fetch_impala_profile_docs_context(
        hosts=["impalad-1.example.com"],
        opener=fake_opener,
    )

    assert result.context["status"] == "unavailable"
    assert result.context["reason"] == "request_failed"
    assert result.attempted_endpoints == 2


def test_fetch_impala_profile_docs_context_is_unavailable_for_incomplete_read():
    def fake_opener(_request, timeout):
        return BrokenReadResponse()

    result = fetch_impala_profile_docs_context(
        hosts=["impalad-1.example.com"],
        opener=fake_opener,
    )

    assert result.context["status"] == "unavailable"
    assert result.context["reason"] == "request_failed"
    assert result.attempted_endpoints == 2


def test_fetch_impala_query_summaries_parses_completed_and_running_queries():
    def fake_opener(request, timeout):
        assert request.full_url in {
            "http://impalad-1.example.com:25000/queries?json",
            "http://impalad-1.example.com:25000/queries?json=true",
        }
        return FakeResponse(
            json.dumps(
                {
                    "completed_queries": [
                        {
                            "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                            "stmt": "SELECT 1",
                            "user": "analyst",
                            "pool": "root.analytics",
                            "duration_ms": 120000,
                            "end_time": "2026-05-12 10:15:00",
                        }
                    ],
                    "in_flight_queries": [
                        {
                            "query_id": "cccccccccccccccc:dddddddddddddddd",
                            "stmt": "INSERT INTO t SELECT 1",
                            "effective_user": "loader",
                            "duration": "30s",
                            "start_time": "2026-05-12 10:20:00",
                            "end_time": "2026-05-12 10:20:30",
                            "executing": True,
                            "state": "FINISHED",
                            "stmt_type": "QUERY",
                        }
                    ],
                    "query_locations": [
                        {
                            "query_id": "eeeeeeeeeeeeeeee:ffffffffffffffff",
                            "stmt_type": "QUERY",
                        }
                    ],
                    "queryLocations": [
                        {
                            "query_id": "1111111111111111:2222222222222222",
                            "stmt_type": "QUERY",
                        }
                    ],
                }
            )
        )

    result = fetch_impala_query_summaries(
        hosts=["impalad-1.example.com"],
        max_query_list_bytes=4096,
        opener=fake_opener,
    )

    assert [summary.query_id for summary in result.summaries] == [
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
        "cccccccccccccccc:dddddddddddddddd",
    ]
    assert any("multiple query location hints" in warning for warning in result.warnings)
    assert result.summaries[0].status == "finished"
    assert result.summaries[0].duration_ms == 120000
    assert result.summaries[0].end_time == "2026-05-12T10:15:00Z"
    assert result.summaries[1].status == "running"
    assert result.summaries[1].query_state == "running"
    assert result.summaries[1].duration_ms == 30000


def test_fetch_impala_query_summaries_warns_when_completed_log_is_full():
    def fake_opener(request, timeout):
        assert request.full_url in {
            "http://impalad-1.example.com:25000/queries?json",
            "http://impalad-1.example.com:25000/queries?json=true",
        }
        return FakeResponse(
            json.dumps(
                {
                    "completed_log_size": 2,
                    "completed_queries": [
                        {
                            "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                            "stmt": "SELECT 1",
                            "end_time": "2026-05-12T10:15:00Z",
                        },
                        {
                            "query_id": "cccccccccccccccc:dddddddddddddddd",
                            "stmt": "SELECT 2",
                            "end_time": "2026-05-12T10:20:00Z",
                        },
                    ],
                }
            )
        )

    result = fetch_impala_query_summaries(
        hosts=["impalad-1.example.com"],
        max_query_list_bytes=4096,
        opener=fake_opener,
    )

    assert [summary.query_id for summary in result.summaries] == [
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
        "cccccccccccccccc:dddddddddddddddd",
    ]
    assert result.query_log_at_capacity is True
    assert result.query_log_oldest_completed_at_iso == "2026-05-12T10:15:00Z"
    assert any("retained log size" in warning for warning in result.warnings)


def test_large_profile_is_recognised_when_sections_sit_past_the_scan_window():
    # The recogniser reads only the first PROFILE_MARKER_SCAN_CHARS. On a large
    # plan the section headings it looked for land beyond that, so the profile
    # was rejected as "non-profile content" -- and the profiles large enough to
    # do that are the ones worth reading. The daemon's own header is at offset
    # zero, whatever follows it.
    header = "Query (id=2441d737c62be331:bee9b5a300000000):\n  Summary:\n"
    padding = "    Filler: value\n" * 40_000
    profile = header + padding + "  Query Timeline:\n    Query submitted: 1ms\n"

    assert profile.lower().find("query timeline") > PROFILE_MARKER_SCAN_CHARS
    assert profile_text_looks_like_runtime_profile(profile)


def test_non_profile_content_is_still_rejected():
    assert not profile_text_looks_like_runtime_profile("<html><body>Not here</body></html>")
    assert not profile_text_looks_like_runtime_profile("")
    assert not profile_text_looks_like_runtime_profile("Could not find query")


@pytest.mark.parametrize(
    ("text", "expected_ms"),
    [
        # What the daemon actually prints. Anything under a second is one term;
        # anything longer is a run, which is why the runs matter most here.
        ("214.305ms", 214),
        ("5s830ms", 5830),
        ("30s470ms", 30470),
        ("1m23s", 83_000),
        ("1h2m", 3_720_000),
        ("1h2m3s456ms", 3_723_456),
        # Single terms and spacing, unchanged from before.
        ("5s", 5_000),
        ("2m", 120_000),
        ("3 seconds", 3_000),
        ("  7s 250ms ", 7_250),
        # A bare number still means milliseconds.
        ("1234", 1_234),
        # Not durations.
        ("", None),
        ("abc", None),
        ("12x", None),
        ("5s abc", None),
        ("1m 2 3s", None),
    ],
)
def test_parse_duration_text_reads_impala_duration_runs(text, expected_ms):
    assert parse_duration_text(text) == expected_ms


def test_impala_query_list_keeps_the_duration_of_slow_queries():
    # Regression: reading only single-term durations dropped every query slower
    # than a second, because those are exactly the ones printed as runs. The
    # duration filter then discarded them, so a scan selected no candidates at
    # all while looking healthy.
    def fake_opener(request, timeout):
        return FakeResponse(
            json.dumps(
                {
                    "completed_queries": [
                        {
                            "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                            "stmt": "SELECT 1",
                            "effective_user": "analyst",
                            "duration": "1m23s",
                            "end_time": "2026-05-12 10:15:00",
                        },
                        {
                            "query_id": "cccccccccccccccc:dddddddddddddddd",
                            "stmt": "SELECT 2",
                            "effective_user": "analyst",
                            "duration": "214.305ms",
                            "end_time": "2026-05-12 10:16:00",
                        },
                    ],
                    "in_flight_queries": [],
                }
            )
        )

    result = fetch_impala_query_summaries(hosts=["impalad-1.example.com"], opener=fake_opener)

    assert [summary.duration_ms for summary in result.summaries] == [83_000, 214]


def test_fetch_impala_query_summaries_honours_a_raised_byte_limit():
    # A coordinator with a large query_log_size answers /queries?json with far
    # more than the default cap allows. The default must still refuse it, and a
    # raised limit must let the same payload through.
    payload = json.dumps(
        {
            "completed_queries": [
                {
                    "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                    "stmt": "SELECT 1",
                    "user": "analyst",
                    "pool": "root.analytics",
                    "duration_ms": 120000,
                    "end_time": "2026-05-12 10:15:00",
                    "padding": "x" * (6 * 1024 * 1024),
                }
            ],
            "in_flight_queries": [],
        }
    )

    def fake_opener(request, timeout):
        return FakeResponse(payload)

    with pytest.raises(CMAdapterError):
        fetch_impala_query_summaries(hosts=["impalad-1.example.com"], opener=fake_opener)

    result = fetch_impala_query_summaries(
        hosts=["impalad-1.example.com"],
        max_query_list_bytes=16 * 1024 * 1024,
        opener=fake_opener,
    )

    assert [summary.query_id for summary in result.summaries] == [
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"
    ]


def test_fetch_impala_query_summaries_skips_incomplete_read_endpoint():
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if request.full_url.endswith("/queries?json"):
            return BrokenReadResponse()
        return FakeResponse(
            json.dumps(
                {
                    "completed_queries": [
                        {
                            "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                            "stmt": "SELECT 1",
                        }
                    ],
                }
            )
        )

    result = fetch_impala_query_summaries(
        hosts=["impalad-1.example.com"],
        max_query_list_bytes=4096,
        opener=fake_opener,
    )

    assert [summary.query_id for summary in result.summaries] == [
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
    ]
    assert result.attempted_endpoints == 2
    assert seen_urls[0].endswith("/queries?json")
    assert seen_urls[1].endswith("/queries?json=true")


def test_fetch_impala_profile_text_tries_hosts_until_profile_is_found():
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if "impalad-1" in request.full_url:
            raise urllib.error.URLError("not here")
        return FakeResponse(
            "<html><body><pre>Query Runtime Profile\nUser: alice</pre></body></html>"
        )

    result = fetch_impala_profile_text(
        query_id="abc:def",
        hosts=["impalad-1.example.com", "impalad-2.example.com"],
        max_profile_bytes=1024,
        opener=fake_opener,
    )

    assert result.query_id == "abc:def"
    assert result.profile_text == "Query Runtime Profile\nUser: alice\n"
    assert result.attempted_endpoints == 3
    assert result.profile_endpoint_format == "text"
    assert seen_urls[0].startswith("http://impalad-1.example.com:25000/")
    assert seen_urls[2].startswith("http://impalad-2.example.com:25000/")


def test_fetch_impala_profile_text_retries_after_incomplete_read():
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if len(seen_urls) == 1:
            return BrokenReadResponse()
        return FakeResponse("Query Runtime Profile\nUser: alice\n")

    result = fetch_impala_profile_text(
        query_id="abc:def",
        hosts=["impalad-1.example.com"],
        max_profile_bytes=1024,
        opener=fake_opener,
    )

    assert result.profile_text == "Query Runtime Profile\nUser: alice\n"
    assert result.attempted_endpoints == 2
    assert result.profile_endpoint_format == "default"


def test_fetch_impala_profile_text_prefers_json_when_enabled_and_falls_back_to_text():
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if "format=json" in request.full_url:
            return FakeResponse('{"status": "not a profile"}')
        return FakeResponse("Query Runtime Profile\nUser: alice\n")

    result = fetch_impala_profile_text(
        query_id="abc:def",
        hosts=["impalad-1.example.com"],
        max_profile_bytes=1024,
        prefer_json=True,
        opener=fake_opener,
    )

    assert result.profile_text == "Query Runtime Profile\nUser: alice\n"
    assert result.attempted_endpoints == 2
    assert result.profile_endpoint_format == "text"
    assert seen_urls[0].endswith("query_id=abc%3Adef&format=json")
    assert seen_urls[1].endswith("query_id=abc%3Adef&format=text")


def test_fetch_impala_profile_text_accepts_json_profile_when_enabled():
    def fake_opener(request, timeout):
        assert "format=json" in request.full_url
        return FakeResponse(
            json.dumps(
                {
                    "profile_version": 1,
                    "runtime_profile": {
                        "counters": [{"name": "TotalTime", "value": "1s"}],
                    },
                }
            )
        )

    result = fetch_impala_profile_text(
        query_id="abc:def",
        hosts=["impalad-1.example.com"],
        max_profile_bytes=1024,
        prefer_json=True,
        opener=fake_opener,
    )

    assert '"runtime_profile"' in result.profile_text
    assert result.attempted_endpoints == 1
    assert result.profile_endpoint_format == "json"


def test_fetch_impala_profile_text_skips_non_profile_daemon_pages():
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if len(seen_urls) == 1:
            return FakeResponse("<html><body>Apache Impala Debug Web UI</body></html>")
        return FakeResponse("Query Runtime Profile\nUser: alice\n")

    result = fetch_impala_profile_text(
        query_id="abc:def",
        hosts=["impalad-1.example.com"],
        max_profile_bytes=1024,
        opener=fake_opener,
    )

    assert result.profile_text == "Query Runtime Profile\nUser: alice\n"
    assert result.attempted_endpoints == 2
    assert result.profile_endpoint_format == "default"


def test_fetch_impala_profile_text_accepts_profiles_after_large_html_prefix():
    html_prefix = "<html><body>" + ("navigation\n" * 1000)

    def fake_opener(_request, timeout):
        return FakeResponse(
            html_prefix
            + "Query State: FINISHED\n"
            + "Query Timeline: 261.507ms\n"
            + "Planner Timeline: 10ms\n"
            + "</body></html>"
        )

    result = fetch_impala_profile_text(
        query_id="abc:def",
        hosts=["impalad-1.example.com"],
        max_profile_bytes=20000,
        opener=fake_opener,
    )

    assert "Query Timeline" in result.profile_text
    assert result.attempted_endpoints == 1


def test_fetch_impala_profile_text_rejects_only_non_profile_daemon_pages():
    def fake_opener(_request, timeout):
        return FakeResponse("<html><body>Apache Impala Debug Web UI</body></html>")

    try:
        fetch_impala_profile_text(
            query_id="abc:def",
            hosts=["impalad-1.example.com"],
            max_profile_bytes=1024,
            opener=fake_opener,
        )
    except CMAdapterError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected non-profile daemon response to be rejected")

    assert "non-profile content" in message
    assert "profile was not found" not in message
    assert "impalad-1.example.com" not in message


@pytest.mark.parametrize("body", ["", "SENSITIVE_SENTINEL", "SENSITIVE_SENTINEL" * 100])
def test_fetch_impala_profile_text_does_not_call_invalid_response_not_found(body):
    with pytest.raises(CMAdapterError) as caught:
        fetch_impala_profile_text(
            query_id="abc:def",
            hosts=["coordinator.example.com"],
            max_profile_bytes=32,
            opener=lambda _request, timeout: FakeResponse(body),
        )

    message = str(caught.value)
    assert message.startswith("Impala profile collection failed")
    assert "Attempted endpoints: 2." in message
    for forbidden in (
        "profile was not found",
        "SENSITIVE_SENTINEL",
        "coordinator.example.com",
        "abc:def",
    ):
        assert forbidden not in message


def test_fetch_impala_profile_text_treats_not_found_markers_case_insensitively():
    def fake_opener(_request, timeout):
        return FakeResponse("<html><body>could not find query id</body></html>")

    try:
        fetch_impala_profile_text(
            query_id="abc:def",
            hosts=["impalad-1.example.com"],
            max_profile_bytes=1024,
            opener=fake_opener,
        )
    except CMAdapterError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected profile-not-found marker to be rejected")

    assert "profile was not found" in message


def test_extract_profile_text_from_response_unwraps_preformatted_html():
    text = extract_profile_text_from_response("<html><pre>Query &amp; Profile</pre></html>")

    assert text == "Query & Profile\n"


def test_fetch_impala_daemon_identity_reads_safe_version_and_mode():
    def fake_opener(request, timeout):
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse(
                json.dumps(
                    {
                        "metric_group": {
                            "child_groups": [
                                {
                                    "metrics": [
                                        {
                                            "name": "impala-server.version",
                                            "value": "impalad version 5.0.0-SNAPSHOT RELEASE (build abcdef123456)",
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                )
            )
        return FakeResponse(
            "<html><body>Apache Impala<br>Impala Server Mode: Coordinator<br>"
            "(Local Catalog Mode)</body></html>"
        )

    identity = fetch_impala_daemon_identity(
        hosts=["impalad-2.example.com"],
        opener=fake_opener,
    )

    assert identity is not None
    assert identity.product == "apache_impala"
    assert identity.version == "5.0.0-SNAPSHOT"
    assert identity.version_label == "impalad version 5.0.0-SNAPSHOT RELEASE"
    assert identity.build_type == "RELEASE"
    assert identity.server_mode == "coordinator"
    assert identity.local_catalog_mode is True


def test_fetch_impala_daemon_identity_ignores_incomplete_read():
    def fake_opener(_request, timeout):
        return BrokenReadResponse()

    identity = fetch_impala_daemon_identity(
        hosts=["impalad-2.example.com"],
        opener=fake_opener,
    )

    assert identity is None


def test_collect_impala_profile_cli_writes_daemon_identity_metadata(tmp_path):
    def fake_opener(request, timeout):
        if "query_profile" in request.full_url:
            return FakeResponse("Query Runtime Profile\nUser: alice@example.com\n")
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse(
                json.dumps(
                    {
                        "metric_group": {
                            "metrics": [
                                {
                                    "name": "impala-server.version",
                                    "value": "impalad version 5.0.0-SNAPSHOT RELEASE (build abcdef123456)",
                                }
                            ]
                        }
                    }
                )
            )
        return FakeResponse(
            "<html>Apache Impala\nImpala Server Mode: Coordinator\n(Local Catalog Mode)</html>"
        )

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--redact-identifiers",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    metadata = json.loads(
        (tmp_path / "abc_def" / "query_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["impala_daemon_product"] == "apache_impala"
    assert metadata["impala_daemon_version"] == "5.0.0-SNAPSHOT"
    assert metadata["impala_daemon_version_label"] == "impalad version 5.0.0-SNAPSHOT RELEASE"
    assert metadata["impala_daemon_build_type"] == "RELEASE"
    assert metadata["impala_daemon_server_mode"] == "coordinator"
    assert metadata["impala_daemon_local_catalog_mode"] is True


def test_collect_impala_profile_cli_writes_redacted_case(tmp_path):
    def fake_opener(_request, timeout):
        return FakeResponse(
            "Query Runtime Profile\nUser: alice@example.com\nCoordinator: impalad-2.example.com\n"
        )

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--redact-identifiers",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    case_dir = tmp_path / "abc_def"
    profile_text = (case_dir / "profile_digest.md").read_text(encoding="utf-8")
    metadata = json.loads((case_dir / "query_metadata.json").read_text(encoding="utf-8"))
    legacy_metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))
    warnings = (case_dir / "collection_warnings.txt").read_text(encoding="utf-8")

    assert "<user>" in profile_text
    assert "impalad-2.example.com" not in profile_text
    assert metadata["query_id"] == "abc:def"
    assert metadata["profile_source"] == "impala_daemon"
    assert metadata["profile_source_label"] == "Impala daemon profile endpoint"
    assert legacy_metadata == metadata
    assert "direct Impala profile collector" in warnings
    assert "Impala profile text user metadata collected" in warnings
    assert "CM profile text" not in warnings
    assert "impalad-2.example.com" not in warnings


def test_collect_impala_profile_cli_can_try_json_profile_before_text(tmp_path):
    seen_profile_urls = []

    def fake_opener(request, timeout):
        if "query_profile" in request.full_url:
            seen_profile_urls.append(request.full_url)
            return FakeResponse(
                json.dumps(
                    {
                        "profile_version": 1,
                        "runtime_profile": {
                            "counters": [{"name": "TotalTime", "value": "1s"}],
                        },
                    }
                )
            )
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse("{}")
        return FakeResponse("<html>Apache Impala</html>")

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--prefer-json-profile",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    metadata = json.loads(
        (tmp_path / "abc_def" / "query_metadata.json").read_text(encoding="utf-8")
    )
    warnings = (tmp_path / "abc_def" / "collection_warnings.txt").read_text(encoding="utf-8")

    assert seen_profile_urls[0].endswith("query_id=abc%3Adef&format=json")
    assert metadata["profile_response_format"] == "json"
    assert metadata["profile_fetch_attempt_count"] == 1
    assert metadata["profile_json_probe_enabled"] is True
    assert metadata["profile_docs_probe_enabled"] is False
    assert metadata["profile_docs_fetch_attempt_count"] == 0
    assert metadata["admission_context_probe_enabled"] is False
    assert metadata["admission_context_fetch_attempt_count"] == 0
    assert "selected impalad profile endpoint format: json" in warnings
    assert "impalad-2.example.com" not in warnings


def test_collect_impala_profile_cli_can_write_profile_docs_context(tmp_path):
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if "query_profile" in request.full_url:
            return FakeResponse("Query Runtime Profile\nUser: alice@example.com\n")
        if request.full_url.endswith("/profile_docs/?json"):
            return FakeResponse(
                json.dumps(
                    {
                        "profile_docs": [
                            {"name": "ClientFetchWaitTimer", "significance": "STABLE_LOW"},
                            {"name": "SpilledBytes", "significance": "STABLE_HIGH"},
                            {"name": "UnrelatedCounter", "significance": "DEBUG"},
                        ],
                    }
                )
            )
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse(
                json.dumps(
                    {
                        "metric_group": {
                            "metrics": [
                                {
                                    "name": "impala-server.version",
                                    "value": "impalad version 4.5.0 RELEASE (build abcdef123456)",
                                }
                            ]
                        }
                    }
                )
            )
        return FakeResponse("<html>Apache Impala</html>")

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--collect-profile-docs",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    context = json.loads(
        (tmp_path / "abc_def" / "profile_counter_registry_context.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (tmp_path / "abc_def" / "query_metadata.json").read_text(encoding="utf-8")
    )
    warnings = (tmp_path / "abc_def" / "collection_warnings.txt").read_text(encoding="utf-8")

    assert any(url.endswith("/profile_docs/?json") for url in seen_urls)
    assert context["status"] == "available"
    assert context["source"] == "profile_docs"
    assert context["impala_version"] == "4.5.0"
    assert context["source_counter_count"] == 3
    assert all("description" not in entry for entry in context["entries"])
    assert metadata["profile_docs_probe_enabled"] is True
    assert metadata["profile_docs_fetch_attempt_count"] == 1
    assert "Impala profile counter stability docs collected" in warnings
    assert "impalad-2.example.com" not in warnings


def test_collect_impala_profile_cli_treats_missing_profile_docs_as_nonfatal(tmp_path):
    def fake_opener(request, timeout):
        if "query_profile" in request.full_url:
            return FakeResponse("Query Runtime Profile\nUser: alice@example.com\n")
        if "/profile_docs" in request.full_url:
            raise urllib.error.URLError("not here")
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse("{}")
        return FakeResponse("<html>Apache Impala</html>")

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--collect-profile-docs",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    context = json.loads(
        (tmp_path / "abc_def" / "profile_counter_registry_context.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (tmp_path / "abc_def" / "query_metadata.json").read_text(encoding="utf-8")
    )
    warnings = (tmp_path / "abc_def" / "collection_warnings.txt").read_text(encoding="utf-8")

    assert context["status"] == "unavailable"
    assert context["reason"] == "request_failed"
    assert metadata["profile_docs_probe_enabled"] is True
    assert metadata["profile_docs_fetch_attempt_count"] == 2
    assert "Impala profile counter stability docs unavailable" in warnings


def test_collect_impala_profile_cli_can_write_admission_context(tmp_path):
    seen_urls = []

    def fake_opener(request, timeout):
        seen_urls.append(request.full_url)
        if "query_profile" in request.full_url:
            return FakeResponse("Query Runtime Profile\nUser: alice@example.com\n")
        if request.full_url.endswith("/admission?json"):
            return FakeResponse(
                json.dumps(
                    {
                        "admission_pools": {
                            "root.analytics": {
                                "queued_queries": [{"query_id": "raw-query-id"}],
                                "running_queries": [
                                    {"stmt": "SELECT secret_col FROM sensitive_table"}
                                ],
                                "avg_queue_time_ms": 7_000,
                            }
                        },
                        "statestore": {"is_stale": False},
                    }
                )
            )
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse("{}")
        return FakeResponse("<html>Apache Impala</html>")

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--collect-admission-context",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    context_path = tmp_path / "abc_def" / "admission_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    metadata = json.loads(
        (tmp_path / "abc_def" / "query_metadata.json").read_text(encoding="utf-8")
    )
    warnings = (tmp_path / "abc_def" / "collection_warnings.txt").read_text(encoding="utf-8")
    context_text = context_path.read_text(encoding="utf-8")

    assert any(url.endswith("/admission?json") for url in seen_urls)
    assert context["status"] == "available"
    assert context["queue_present"] == "yes"
    assert context["pool_pressure"] == "medium"
    assert metadata["admission_context_probe_enabled"] is True
    assert metadata["admission_context_fetch_attempt_count"] == 1
    assert "Impala admission aggregate context collected" in warnings
    assert "impalad-2.example.com" not in warnings
    assert "root.analytics" not in context_text
    assert "raw-query-id" not in context_text
    assert "secret_col" not in context_text


def test_collect_impala_profile_cli_treats_missing_admission_context_as_nonfatal(tmp_path):
    def fake_opener(request, timeout):
        if "query_profile" in request.full_url:
            return FakeResponse("Query Runtime Profile\nUser: alice@example.com\n")
        if request.full_url.endswith("/admission?json"):
            raise urllib.error.URLError("not here")
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse("{}")
        return FakeResponse("<html>Apache Impala</html>")

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--collect-admission-context",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    context = json.loads(
        (tmp_path / "abc_def" / "admission_context.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (tmp_path / "abc_def" / "query_metadata.json").read_text(encoding="utf-8")
    )
    warnings = (tmp_path / "abc_def" / "collection_warnings.txt").read_text(encoding="utf-8")

    assert context["status"] == "unavailable"
    assert context["reason"] == "request_failed"
    assert metadata["admission_context_probe_enabled"] is True
    assert metadata["admission_context_fetch_attempt_count"] == 1
    assert "Impala admission aggregate context unavailable" in warnings


def test_collect_impala_profile_cli_writes_metadata_source_tables(tmp_path):
    def fake_opener(_request, timeout):
        return FakeResponse(
            "Query Runtime Profile\n"
            "User: alice@example.com\n"
            "Sql Statement: SELECT * FROM example_warehouse.real_table\n"
            "Coordinator: impalad-2.example.com\n"
        )

    source_tables_path = tmp_path / ".metadata-source-tables.json"

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--redact-identifiers",
            "--metadata-source-tables-out",
            str(source_tables_path),
        ],
        opener=fake_opener,
    )

    assert rc == 0
    assert json.loads(source_tables_path.read_text(encoding="utf-8")) == [
        "example_warehouse.real_table"
    ]
    profile_text = (tmp_path / "abc_def" / "profile_digest.md").read_text(encoding="utf-8")
    assert "example_warehouse.real_table" not in profile_text


def test_collect_impala_profile_cli_can_collect_prometheus_runtime_metrics(tmp_path):
    seen_prometheus_urls = []

    def fake_opener(request, timeout, context=None):
        if "query_profile" in request.full_url:
            return FakeResponse(
                "Query Runtime Profile\n"
                "Start Time: 2026-05-03 17:05:04.451431000\n"
                "End Time: 2026-05-03 17:05:11.400421000\n"
                "User: alice@example.com\n"
            )
        if request.full_url.endswith("/metrics?json"):
            return FakeResponse("{}")
        if request.full_url.startswith("http://prom.example.net"):
            seen_prometheus_urls.append(request.full_url)
            return FakeResponse(
                json.dumps(
                    {
                        "status": "success",
                        "data": {
                            "resultType": "matrix",
                            "result": [
                                {
                                    "metric": {"instance": "impalad-2.example.com"},
                                    "values": [[1, "1"], [2, "2"], [3, "3"]],
                                }
                            ],
                        },
                    }
                )
            )
        return FakeResponse("<html>Apache Impala</html>")

    rc = collect_impala_profile.main(
        [
            "--query-id",
            "abc:def",
            "--host",
            "impalad-2.example.com",
            "--out",
            str(tmp_path),
            "--redact",
            "--prometheus-url",
            "http://prom.example.net",
            "--collect-prometheus-timeseries",
            "--prometheus-step-sec",
            "30",
        ],
        opener=fake_opener,
    )

    assert rc == 0
    context = json.loads(
        (tmp_path / "abc_def" / "runtime_metrics_context.json").read_text(encoding="utf-8")
    )
    warnings = (tmp_path / "abc_def" / "collection_warnings.txt").read_text(encoding="utf-8")

    assert context["source"] == "prometheus"
    assert context["source_label"] == "Prometheus runtime metrics"
    assert context["available"] is True
    assert context["queries"][0]["status"] == "ok"
    assert context["queries"][0]["signal_id"] == "impala_daemon_memory_growth"
    assert "impalad-2.example.com" not in json.dumps(context)
    assert "Prometheus runtime metrics context collected" in warnings
    assert seen_prometheus_urls
