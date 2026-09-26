import http.client
import json
import subprocess
from pathlib import Path

import pytest


REPO_DIR = Path(__file__).resolve().parents[1]


def load_collector_module():
    from query_doctor.cli import collect_cm_profiles

    return collect_cm_profiles


def test_package_entrypoint_keeps_repo_root_anchor():
    from query_doctor.cli import collect_cm_profiles

    assert collect_cm_profiles.REPO_DIR == REPO_DIR


def base_args(tmp_path: Path) -> list[str]:
    return [
        "--cm-url",
        "https://cm.example.com:7183",
        "--cluster",
        "CLUSTER_NAME",
        "--service",
        "IMPALA_SERVICE_NAME",
        "--out",
        str(tmp_path / "cm-corpus"),
    ]


def write_config(tmp_path: Path, values: dict[str, object], name: str = "cm-config.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def synthetic_cm_credential_url(path: str = "") -> str:
    return "https://cm_user:" + "embedded-pass" + "@cm.example.com:7183" + path


def synthetic_cm_basic_url(path: str = "") -> str:
    return "https://cm_user:" + "cm_pass" + "@cm.example.com:7183" + path


@pytest.fixture(autouse=True)
def isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class FakeResponse:
    def __init__(self, payload: str):
        self.payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            return self.payload
        return self.payload[:size]


class BrokenReadResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        raise http.client.IncompleteRead(b"partial")


def test_help_works_without_credentials(capsys):
    module = load_collector_module()

    with pytest.raises(SystemExit) as exc:
        module.main(["--help"], env={})

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--cm-url" in output
    assert "--config" in output
    assert "--ca-bundle" in output
    assert "--max-profile-bytes" in output
    assert "--list-recent-queries" in output
    assert "--recent-limit" in output


def test_dry_run_works_without_credentials_and_creates_no_output(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cm-corpus"

    result = module.main(base_args(tmp_path) + ["--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 0
    assert "No CM API calls are performed in dry-run mode." in captured.out
    assert "not configured; allowed for dry-run" in captured.out
    assert "Max profile bytes: 52428800" in captured.out
    assert not output_dir.exists()


def test_dry_run_does_not_create_http_client(tmp_path):
    module = load_collector_module()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("dry-run must not create a CM HTTP client")

    result = module.main(
        base_args(tmp_path) + ["--dry-run"],
        env={},
        client_factory=fail_if_called,
    )

    assert result == 0


def test_preflight_uses_mocked_client_and_writes_no_output(tmp_path, monkeypatch, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cm-corpus"
    calls = []

    def fail_if_called(*args, **kwargs):
        raise AssertionError("preflight must not write collected cases")

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {
                "items": [{"queryId": "query-123"}],
                "nextPageToken": "page-2",
            }

    def fake_client_factory(http_config):
        assert http_config.token == "secret-token"
        assert http_config.verify_tls is True
        assert http_config.ca_bundle is None
        return FakeClient()

    monkeypatch.setattr(module, "write_collected_case", fail_if_called)
    monkeypatch.setattr(module, "collect_and_write_cm_profiles", fail_if_called)

    result = module.main(
        base_args(tmp_path) + ["--preflight"],
        env={"CM_TOKEN": "secret-token"},
        client_factory=fake_client_factory,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert (
        calls[0][0] == "/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries"
    )
    assert calls[0][1]["limit"] == 1
    assert "from" in calls[0][1]
    assert "to" in calls[0][1]
    assert "cluster" not in calls[0][1]
    assert "service" not in calls[0][1]
    assert "[CM profile collector] Preflight result: OK" in captured.out
    assert "Query summaries parsed: 1" in captured.out
    assert "Next page token present: yes" in captured.out
    assert "First query id present: yes" in captured.out
    assert "Max profile bytes: 52428800" in captured.out
    assert "query-123" not in captured.out
    assert "items" not in captured.out
    assert "secret-token" not in captured.out
    assert captured.err == ""
    assert not output_dir.exists()


def test_preflight_passes_ca_bundle_to_http_config(tmp_path, capsys):
    module = load_collector_module()
    seen_configs = []

    class FakeClient:
        def get_json(self, path, params=None):
            return {"items": []}

    def fake_client_factory(http_config):
        seen_configs.append(http_config)
        return FakeClient()

    result = module.main(
        base_args(tmp_path) + ["--preflight", "--ca-bundle", "/tmp/cli-ca.pem"],
        env={"CM_CA_BUNDLE": "/tmp/env-ca.pem"},
        client_factory=fake_client_factory,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert seen_configs[0].ca_bundle == "/tmp/cli-ca.pem"
    assert seen_configs[0].verify_tls is True
    assert "CA bundle: /tmp/cli-ca.pem" in output
    assert "/tmp/env-ca.pem" not in output
    assert not (tmp_path / "cm-corpus").exists()


def test_preflight_with_query_id_reports_profile_presence_without_content(tmp_path, capsys):
    module = load_collector_module()
    profile_text = "SELECT * FROM sensitive_table\n01:SCAN HDFS\n"
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            if path.endswith("/impalaQueries"):
                return {"items": [{"queryId": query_id}]}
            raise AssertionError(f"unexpected path: {path}")

        def get_text(self, path, params=None, *, max_response_bytes=None):
            calls.append((path, params))
            assert max_response_bytes == module.DEFAULT_MAX_PROFILE_BYTES
            if path.endswith(f"/impalaQueries/{query_id}"):
                return profile_text
            raise AssertionError(f"unexpected path: {path}")

    result = module.main(
        base_args(tmp_path) + ["--preflight", "--query-id", query_id],
        env={"CM_USERNAME": "cm_user", "CM_PASSWORD": "secret-password"},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    assert result == 0
    assert (
        calls[0][0] == "/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries"
    )
    assert calls[0][1]["limit"] == 1
    assert calls[1] == (
        f"/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries/{query_id}",
        {"format": "text"},
    )
    assert "Profile text present: yes" in captured.out
    assert f"Profile text length: {len(profile_text)}" in captured.out
    assert "SELECT" not in captured.out
    assert "sensitive_table" not in captured.out
    assert "01:SCAN" not in captured.out
    assert "secret-password" not in captured.out
    assert captured.err == ""
    assert not (tmp_path / "cm-corpus").exists()


def test_preflight_with_invalid_query_id_fails_safely(tmp_path, capsys):
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {"items": [{"queryId": "query-123"}]}

        def get_text(self, path, params=None, *, max_response_bytes=None):
            raise AssertionError("invalid query id must not fetch profile text")

    result = module.main(
        base_args(tmp_path) + ["--preflight", "--query-id", "query-123"],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    assert result == 4
    assert len(calls) == 1
    assert "Profile text check failed" in captured.err
    assert "Impala Query ID path usage" in captured.err
    assert "CM profile text request" not in captured.err
    assert "[A-Za-z0-9]+:[A-Za-z0-9]+" in captured.err
    assert "Profile text endpoint:" not in captured.out
    assert not (tmp_path / "cm-corpus").exists()


def test_list_recent_queries_does_not_require_query_id_or_write_cases(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cm-corpus"
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {
                "items": [
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "durationMillis": 90000,
                        "user": "alice",
                        "poolName": "root.analytics",
                        "statement": "SELECT secret_col FROM example_guarded_db.table_a",
                    }
                ]
            }

        def get_text(self, path, params=None, *, max_response_bytes=None):
            raise AssertionError("recent listing must not fetch profile text")

    result = module.main(
        [
            "--cm-url",
            "https://cm.example.com:7183",
            "--cluster",
            "CLUSTER_NAME",
            "--service",
            "IMPALA_SERVICE_NAME",
            "--list-recent-queries",
        ],
        env={"CM_TOKEN": "secret-token"},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert result == 0
    assert (
        calls[0][0] == "/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries"
    )
    assert calls[0][1]["limit"] == module.DEFAULT_RECENT_LIMIT
    assert "Recent query listing" in captured.out
    assert "Candidates selected: 1" in captured.out
    assert "selected=yes" in captured.out
    assert "query_id=aaaaaaaaaaaaaaaa:0000000000000001" in captured.out
    assert "verb=SELECT" in captured.out
    assert "user=<user>" in captured.out
    assert "SELECT secret_col" not in output
    assert "sensitive_db" not in output
    assert "alice" not in output
    assert "secret-token" not in output
    assert not output_dir.exists()


def test_list_recent_queries_excludes_admin_metadata_statements(tmp_path, capsys):
    module = load_collector_module()

    statements = [
        "SHOW CREATE TABLE db.table_a",
        "SHOW TABLE STATS db.table_a",
        "SHOW COLUMN STATS db.table_a",
        "COMPUTE STATS db.table_a",
        "REFRESH db.table_a",
        "INVALIDATE METADATA db.table_a",
        "MSCK REPAIR TABLE db.table_a",
        "DESCRIBE db.table_a",
        "SET MEM_LIMIT=1g",
        "USE db",
        "EXPLAIN SELECT * FROM db.table_a",
        "SELECT * FROM db.table_b",
    ]

    class FakeClient:
        def get_json(self, path, params=None):
            return {
                "items": [
                    {
                        "queryId": f"aaaaaaaaaaaaaaaa:{index:016x}",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "statement": statement,
                    }
                    for index, statement in enumerate(statements, start=1)
                ]
            }

    result = module.main(
        base_args(tmp_path)
        + [
            "--list-recent-queries",
            "--recent-limit",
            str(len(statements)),
            "--recent-select",
            "5",
        ],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Candidates selected: 1" in output
    assert output.count("selected=yes") == 1
    assert output.count("excluded: admin or metadata statement") == 11
    assert "SELECT * FROM" not in output


def test_list_recent_queries_can_write_sanitized_json(tmp_path):
    module = load_collector_module()
    json_path = tmp_path / "recent" / "candidates.json"

    class FakeClient:
        def get_json(self, path, params=None):
            return {
                "items": [
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "durationMillis": 12345,
                        "user": "alice",
                        "poolName": "root.analytics",
                        "statement": "SELECT secret_col FROM example_guarded_db.table_a",
                    }
                ]
            }

    result = module.main(
        base_args(tmp_path)
        + [
            "--list-recent-queries",
            "--recent-output-json",
            str(json_path),
        ],
        env={"CM_PASSWORD": "secret-password"},
        client_factory=lambda http_config: FakeClient(),
    )

    assert result == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "recent-query-listing"
    assert payload["selected_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["query_id"] == "aaaaaaaaaaaaaaaa:0000000000000001"
    assert candidate["selected"] is True
    assert candidate["sql_verb"] == "SELECT"
    assert candidate["user"] == "<user>"
    assert "statement" not in candidate
    payload_text = json_path.read_text(encoding="utf-8")
    assert "SELECT secret_col" not in payload_text
    assert "sensitive_db" not in payload_text
    assert "alice" not in payload_text
    assert "secret-password" not in payload_text


def test_recent_limit_caps_inspected_summaries(tmp_path, capsys):
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {
                "items": [
                    {
                        "queryId": f"aaaaaaaaaaaaaaaa:{index:016x}",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "statement": "SELECT * FROM db.table_a",
                    }
                    for index in range(1, 5)
                ]
            }

    result = module.main(
        base_args(tmp_path)
        + [
            "--list-recent-queries",
            "--recent-limit",
            "2",
            "--recent-select",
            "2",
        ],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert calls[0][1]["limit"] == 2
    assert "Summaries inspected: 2" in output
    assert output.count("selected=yes") == 2


@pytest.mark.parametrize(
    "args, expected",
    [
        (["--recent-limit", "101"], "--recent-limit must be <="),
        (["--recent-select", "21"], "--recent-select must be <="),
        (
            ["--recent-limit", "2", "--recent-select", "3"],
            "--recent-select must be <= --recent-limit",
        ),
    ],
)
def test_list_recent_queries_rejects_unbounded_recent_values(tmp_path, capsys, args, expected):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path) + ["--list-recent-queries"] + args,
        env={},
        client_factory=lambda http_config: pytest.fail("invalid config must not create client"),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert expected in captured.err


def test_list_recent_queries_respects_failed_and_running_switches():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="failed-query",
            status="failed",
            query_type="QUERY",
            statement="SELECT * FROM db.table_a",
        ),
        module.CMQuerySummary(
            query_id="running-query",
            status="running",
            query_type="QUERY",
            statement="SELECT * FROM db.table_b",
        ),
    ]

    default_candidates = module.select_recent_query_candidates(summaries, select_limit=2)
    included_candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        include_failed=True,
        include_running=True,
    )

    assert [candidate.selected for candidate in default_candidates] == [False, False]
    assert [candidate.selected for candidate in included_candidates] == [True, True]


def test_list_recent_queries_selects_data_processing_dml_and_ctas():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="insert-query",
            status="succeeded",
            query_type="QUERY",
            statement="INSERT OVERWRITE TABLE example_mart.target SELECT * FROM example_raw.source",
        ),
        module.CMQuerySummary(
            query_id="delete-query",
            status="succeeded",
            query_type="QUERY",
            statement="DELETE FROM example_mart.target WHERE id IN (SELECT id FROM example_raw.source)",
        ),
        module.CMQuerySummary(
            query_id="upsert-query",
            status="succeeded",
            query_type="QUERY",
            statement="UPSERT INTO example_mart.target SELECT * FROM example_raw.source",
        ),
        module.CMQuerySummary(
            query_id="ctas-query",
            status="succeeded",
            query_type="QUERY",
            statement="CREATE TABLE example_mart.target AS SELECT * FROM example_raw.source",
        ),
        module.CMQuerySummary(
            query_id="alter-query",
            status="succeeded",
            query_type="QUERY",
            statement="ALTER TABLE example_mart.target ADD COLUMNS (new_col STRING)",
        ),
        module.CMQuerySummary(
            query_id="compute-query",
            status="succeeded",
            query_type="QUERY",
            statement="COMPUTE INCREMENTAL STATS example_mart.target",
        ),
        module.CMQuerySummary(
            query_id="drop-query",
            status="succeeded",
            query_type="QUERY",
            statement="DROP TABLE example_mart.target",
        ),
        module.CMQuerySummary(
            query_id="get-query",
            status="succeeded",
            query_type="QUERY",
            statement="/* frontend metadata */\nGET TABLES",
        ),
        module.CMQuerySummary(
            query_id="show-query",
            status="succeeded",
            query_type="QUERY",
            statement="SHOW TABLES IN mart",
        ),
        module.CMQuerySummary(
            query_id="plain-create",
            status="succeeded",
            query_type="QUERY",
            statement="CREATE TABLE example_mart.empty_target (id BIGINT)",
        ),
    ]

    candidates = module.select_recent_query_candidates(summaries, select_limit=6)

    assert [
        (candidate.summary.query_id, candidate.selected, candidate.reason)
        for candidate in candidates
    ] == [
        ("insert-query", True, "selected: INSERT query"),
        ("delete-query", True, "selected: DELETE query"),
        ("upsert-query", True, "selected: UPSERT query"),
        ("ctas-query", True, "selected: CREATE TABLE AS SELECT query"),
        ("alter-query", False, "excluded: admin or metadata statement"),
        ("compute-query", False, "excluded: admin or metadata statement"),
        ("drop-query", False, "excluded: admin or metadata statement"),
        ("get-query", False, "excluded: admin or metadata statement"),
        ("show-query", False, "excluded: admin or metadata statement"),
        ("plain-create", False, "excluded: DDL statement"),
    ]
    assert [candidate.sql_verb for candidate in candidates] == [
        "INSERT",
        "DELETE",
        "UPSERT",
        "CREATE",
        "ALTER",
        "COMPUTE",
        "DROP",
        "GET",
        "SHOW",
        "CREATE",
    ]


def test_list_recent_queries_selects_running_dml_without_statement_text():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="running-insert",
            status="IN PROGRESS",
            query_type="DML",
            statement=None,
        ),
        module.CMQuerySummary(
            query_id="finished-insert",
            status="succeeded",
            query_type="DML",
            statement=None,
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        include_running=True,
        only_running=True,
    )

    assert [
        (candidate.summary.query_id, candidate.selected, candidate.reason)
        for candidate in candidates
    ] == [
        ("running-insert", True, "selected: query type indicates user query; SQL verb unknown"),
        ("finished-insert", False, "excluded: not running query"),
    ]


def test_list_recent_queries_treats_query_state_running_as_running():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="state-running-insert",
            status=None,
            query_state="RUNNING",
            query_type="DML",
            statement=None,
        ),
        module.CMQuerySummary(
            query_id="state-finished-insert",
            status=None,
            query_state="FINISHED",
            query_type="DML",
            statement=None,
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        include_running=True,
        only_running=True,
    )

    assert [
        (candidate.summary.query_id, candidate.selected, candidate.reason)
        for candidate in candidates
    ] == [
        (
            "state-running-insert",
            True,
            "selected: query type indicates user query; SQL verb unknown",
        ),
        ("state-finished-insert", False, "excluded: not running query"),
    ]


def test_list_recent_queries_applies_recent_user_and_pool_filters(tmp_path, capsys):
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {
                "items": [
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "user": "service_user",
                        "poolName": "root.etl",
                        "statement": "SELECT * FROM db.table_a",
                    },
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000002",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "user": "analyst",
                        "poolName": "root.analytics",
                        "statement": "SELECT * FROM db.table_b",
                    },
                ]
            }

    result = module.main(
        base_args(tmp_path)
        + [
            "--list-recent-queries",
            "--recent-user",
            "analyst",
            "--recent-pool",
            "root.analytics",
        ],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert (
        calls[0][1]["filter"]
        == 'user = "analyst" AND pool = "root.analytics" AND executing = false'
    )
    assert "Candidates selected: 1" in output
    assert output.count("selected=yes") == 1
    assert "excluded: user filter mismatch" in output
    assert "analyst" not in output
    assert "service_user" not in output


def test_recent_listing_duration_filters_exclude_short_long_and_unknown():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="too-short",
            status="succeeded",
            query_type="QUERY",
            duration_ms=500,
            statement="SELECT * FROM db.table_a",
        ),
        module.CMQuerySummary(
            query_id="selected",
            status="succeeded",
            query_type="QUERY",
            duration_ms=1500,
            statement="SELECT * FROM db.table_b",
        ),
        module.CMQuerySummary(
            query_id="too-long",
            status="succeeded",
            query_type="QUERY",
            duration_ms=3000,
            statement="SELECT * FROM db.table_c",
        ),
        module.CMQuerySummary(
            query_id="unknown-duration",
            status="succeeded",
            query_type="QUERY",
            statement="SELECT * FROM db.table_d",
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=5,
        min_duration_sec=1.0,
        max_duration_sec=2.0,
    )

    assert [(candidate.summary.query_id, candidate.selected) for candidate in candidates] == [
        ("too-short", False),
        ("selected", True),
        ("too-long", False),
        ("unknown-duration", False),
    ]
    assert candidates[0].reason == "excluded: duration below recent-min-duration-sec"
    assert candidates[2].reason == "excluded: duration above recent-max-duration-sec"
    assert candidates[3].reason == "excluded: duration unknown"

    no_filter_candidates = module.select_recent_query_candidates(
        [summaries[3]],
        select_limit=1,
    )
    assert no_filter_candidates[0].selected is True


def test_recent_listing_duration_order_desc_prefers_longer_queries():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="short",
            status="succeeded",
            duration_ms=100,
            statement="SELECT * FROM db.short_table",
        ),
        module.CMQuerySummary(
            query_id="long",
            status="succeeded",
            duration_ms=3000,
            statement="SELECT * FROM db.long_table",
        ),
        module.CMQuerySummary(
            query_id="middle",
            status="succeeded",
            duration_ms=1000,
            statement="SELECT * FROM db.middle_table",
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        order="duration-desc",
    )

    selected_ids = [candidate.summary.query_id for candidate in candidates if candidate.selected]
    assert selected_ids == ["long", "middle"]
    assert (
        candidates[0].reason == "eligible but not selected because recent-select limit was reached"
    )


def test_recent_listing_duration_order_asc_prefers_shorter_queries():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="middle",
            status="succeeded",
            duration_ms=1000,
            statement="SELECT * FROM db.middle_table",
        ),
        module.CMQuerySummary(
            query_id="long",
            status="succeeded",
            duration_ms=3000,
            statement="SELECT * FROM db.long_table",
        ),
        module.CMQuerySummary(
            query_id="short",
            status="succeeded",
            duration_ms=100,
            statement="SELECT * FROM db.short_table",
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        order="duration-asc",
    )

    selected_ids = [candidate.summary.query_id for candidate in candidates if candidate.selected]
    assert selected_ids == ["middle", "short"]
    assert (
        candidates[1].reason == "eligible but not selected because recent-select limit was reached"
    )


def test_recent_listing_recent_duration_order_prefers_newer_long_queries():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="old-long",
            end_time="2026-05-03T09:00:00Z",
            status="succeeded",
            duration_ms=9000,
            statement="SELECT * FROM db.old_long",
        ),
        module.CMQuerySummary(
            query_id="new-short",
            end_time="2026-05-03T10:00:00Z",
            status="succeeded",
            duration_ms=1000,
            statement="SELECT * FROM db.new_short",
        ),
        module.CMQuerySummary(
            query_id="new-long",
            end_time="2026-05-03T10:00:00Z",
            status="succeeded",
            duration_ms=8000,
            statement="SELECT * FROM db.new_long",
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        order="recent-duration-desc",
    )

    selected_ids = {candidate.summary.query_id for candidate in candidates if candidate.selected}
    assert selected_ids == {"new-short", "new-long"}
    assert (
        candidates[0].reason == "eligible but not selected because recent-select limit was reached"
    )


def test_recent_listing_status_priority_order_prefers_running_and_failed_queries():
    module = load_collector_module()
    summaries = [
        module.CMQuerySummary(
            query_id="succeeded-long",
            status="succeeded",
            duration_ms=9000,
            statement="SELECT * FROM db.succeeded_long",
        ),
        module.CMQuerySummary(
            query_id="failed-mid",
            status="failed",
            duration_ms=5000,
            statement="SELECT * FROM db.failed_mid",
        ),
        module.CMQuerySummary(
            query_id="running-short",
            status="running",
            duration_ms=1000,
            statement="SELECT * FROM db.running_short",
        ),
    ]

    candidates = module.select_recent_query_candidates(
        summaries,
        select_limit=2,
        include_failed=True,
        include_running=True,
        order="status-priority",
    )

    selected_ids = {candidate.summary.query_id for candidate in candidates if candidate.selected}
    assert selected_ids == {"failed-mid", "running-short"}
    assert (
        candidates[0].reason == "eligible but not selected because recent-select limit was reached"
    )


def test_preflight_profile_text_above_limit_fails_without_content_leak(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    profile_text = "SELECT secret_value FROM sensitive_table"

    class FakeClient:
        def get_json(self, path, params=None):
            return {"items": [{"queryId": query_id}]}

        def get_text(self, path, params=None, *, max_response_bytes=None):
            assert max_response_bytes == 12
            return profile_text

    result = module.main(
        base_args(tmp_path)
        + [
            "--preflight",
            "--query-id",
            query_id,
            "--max-profile-bytes",
            "12",
        ],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert result == 4
    assert "Max profile bytes: 12" in captured.out
    assert "Profile text check failed" in captured.err
    assert "exceeded maximum allowed bytes" in captured.err
    assert "SELECT" not in combined
    assert "secret_value" not in combined
    assert "sensitive_table" not in combined
    assert not (tmp_path / "cm-corpus").exists()


def test_preflight_summary_failure_is_sanitized(tmp_path, capsys):
    module = load_collector_module()

    class FakeClient:
        def get_json(self, path, params=None):
            raise module.CMHttpError("Authorization: Bearer secret-token password=secret-password")

    result = module.main(
        base_args(tmp_path) + ["--preflight"],
        env={"CM_TOKEN": "secret-token"},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    assert result == 4
    combined = captured.out + captured.err
    assert "[CM profile collector] Preflight result: FAILED" in captured.out
    assert "Query summary check failed" in captured.err
    assert "Endpoint path or response shape may need verification" in captured.err
    assert "secret-token" not in combined
    assert "secret-password" not in combined
    assert "Authorization: Bearer <redacted>" in combined
    assert not (tmp_path / "cm-corpus").exists()


def test_cm_url_env_can_provide_url(tmp_path, capsys):
    module = load_collector_module()
    args = [
        "--cluster",
        "CLUSTER_NAME",
        "--service",
        "IMPALA_SERVICE_NAME",
        "--out",
        str(tmp_path / "cm-corpus"),
        "--dry-run",
    ]

    result = module.main(args, env={"CM_URL": "https://env-cm.example.com:7183"})

    assert result == 0
    assert "https://env-cm.example.com:7183" in capsys.readouterr().out


def test_cli_cm_url_overrides_env(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path) + ["--dry-run"],
        env={"CM_URL": "https://env-cm.example.com:7183"},
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "https://cm.example.com:7183" in output
    assert "env-cm" not in output


def test_cm_ca_bundle_env_fallback_is_used_in_dry_run(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path) + ["--dry-run"],
        env={"CM_CA_BUNDLE": "/tmp/env-ca.pem"},
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "CA bundle: /tmp/env-ca.pem" in output
    assert not (tmp_path / "cm-corpus").exists()


def test_cli_ca_bundle_overrides_env_in_dry_run(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path) + ["--dry-run", "--ca-bundle", "/tmp/cli-ca.pem"],
        env={"CM_CA_BUNDLE": "/tmp/env-ca.pem"},
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "CA bundle: /tmp/cli-ca.pem" in output
    assert "/tmp/env-ca.pem" not in output
    assert not (tmp_path / "cm-corpus").exists()


def test_config_loads_non_secret_collector_settings(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "configured-corpus"
    config_path = write_config(
        tmp_path,
        {
            "ca_bundle": "/tmp/config-ca.pem",
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "limit": 3,
            "max_profile_bytes": 12345,
            "min_duration_sec": 45,
            "out": str(output_dir),
            "pool": "etl",
            "query_type": "QUERY",
            "redact": True,
            "redact_identifiers": True,
            "service": "CONFIG_IMPALA",
            "since_hours": 2,
            "status": "failed",
            "user": "analyst",
        },
    )

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    output = capsys.readouterr().out
    assert result == 0
    assert "https://config-cm.example.com:7183" in output
    assert "Cluster: CONFIG_CLUSTER" in output
    assert "Service: CONFIG_IMPALA" in output
    assert f"Output path: {output_dir}" in output
    assert "Since hours: 2" in output
    assert "Limit: 3" in output
    assert "Max profile bytes: 12345" in output
    assert "Minimum duration seconds: 45" in output
    assert "pool: etl" in output
    assert "user: analyst" in output
    assert "status: failed" in output
    assert "query_type: QUERY" in output
    assert "Redaction: enabled" in output
    assert "Identifier redaction: enabled" in output
    assert "CA bundle: /tmp/config-ca.pem" in output
    assert not output_dir.exists()


def test_config_ca_bundle_expands_home_directory(tmp_path, capsys, monkeypatch):
    module = load_collector_module()
    monkeypatch.setenv("HOME", str(tmp_path))
    output_dir = tmp_path / "configured-corpus"
    ca_path = tmp_path / "cm-ca.pem"
    ca_path.write_text("CA\n", encoding="utf-8")
    config_path = write_config(
        tmp_path,
        {
            "ca_bundle": "~/cm-ca.pem",
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(output_dir),
            "service": "CONFIG_IMPALA",
            "username": "cm_config_user",
        },
    )

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    output = capsys.readouterr().out
    assert result == 0
    assert f"CA bundle: {ca_path}" in output
    assert "~/cm-ca.pem" not in output
    assert not output_dir.exists()


def test_default_local_config_is_loaded_when_present(tmp_path, capsys):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "ca_bundle": "/tmp/default-ca.pem",
            "cluster": "DEFAULT_CLUSTER",
            "cm_url": "https://default-cm.example.com:7183",
            "out": str(tmp_path / "default-corpus"),
            "service": "DEFAULT_IMPALA",
            "username": "cm_config_user",
            "recent_limit": 9,
            "recent_select": 3,
            "recent_window_minutes": 30,
        },
        name=module.DEFAULT_LOCAL_CONFIG_NAME,
    )

    result = module.main(["--dry-run"], env={})

    output = capsys.readouterr().out
    assert result == 0
    assert config_path.exists()
    assert "https://default-cm.example.com:7183" in output
    assert "Cluster: DEFAULT_CLUSTER" in output
    assert "Service: DEFAULT_IMPALA" in output
    assert "CA bundle: /tmp/default-ca.pem" in output
    assert "CM_USERNAME configured without CM_PASSWORD or CM_TOKEN" in output


def test_legacy_default_local_config_is_loaded_with_warning(tmp_path, capsys):
    module = load_collector_module()
    write_config(
        tmp_path,
        {
            "cluster": "LEGACY_CLUSTER",
            "cm_url": "https://legacy-cm.example.com:7183",
            "out": str(tmp_path / "legacy-corpus"),
            "service": "LEGACY_IMPALA",
        },
        name=module.LEGACY_LOCAL_CONFIG_NAME,
    )

    result = module.main(["--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 0
    assert "https://legacy-cm.example.com:7183" in captured.out
    assert "Cluster: LEGACY_CLUSTER" in captured.out
    assert module.LEGACY_LOCAL_CONFIG_WARNING in captured.err


def test_new_default_local_config_wins_over_legacy(tmp_path, capsys):
    module = load_collector_module()
    write_config(
        tmp_path,
        {
            "cluster": "LEGACY_CLUSTER",
            "cm_url": "https://legacy-cm.example.com:7183",
            "out": str(tmp_path / "legacy-corpus"),
            "service": "LEGACY_IMPALA",
        },
        name=module.LEGACY_LOCAL_CONFIG_NAME,
    )
    write_config(
        tmp_path,
        {
            "cluster": "NEW_CLUSTER",
            "cm_url": "https://new-cm.example.com:7183",
            "out": str(tmp_path / "new-corpus"),
            "service": "NEW_IMPALA",
        },
        name=module.DEFAULT_LOCAL_CONFIG_NAME,
    )

    result = module.main(["--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 0
    assert "https://new-cm.example.com:7183" in captured.out
    assert "Cluster: NEW_CLUSTER" in captured.out
    assert "legacy-cm" not in captured.out
    assert module.LEGACY_LOCAL_CONFIG_WARNING not in captured.err


def test_missing_default_local_config_does_not_change_cli_env_behavior(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(base_args(tmp_path) + ["--dry-run"], env={})

    output = capsys.readouterr().out
    assert result == 0
    assert "https://cm.example.com:7183" in output
    assert "Cluster: CLUSTER_NAME" in output
    assert "CA bundle: system default trust store" in output


def test_default_local_config_can_drive_recent_listing(tmp_path, capsys):
    module = load_collector_module()
    write_config(
        tmp_path,
        {
            "cluster": "DEFAULT_CLUSTER",
            "cm_url": "https://default-cm.example.com:7183",
            "out": str(tmp_path / "default-corpus"),
            "service": "DEFAULT_IMPALA",
            "recent_limit": 2,
            "recent_select": 1,
            "recent_window_minutes": 15,
        },
        name=module.DEFAULT_LOCAL_CONFIG_NAME,
    )
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {
                "items": [
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "queryType": "QUERY",
                        "status": "succeeded",
                        "statement": "SELECT * FROM db.table_a",
                    }
                ]
            }

        def get_text(self, path, params=None, *, max_response_bytes=None):
            raise AssertionError("listing must not fetch profile text")

    result = module.main(
        ["--list-recent-queries"],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert calls[0][0] == "/api/v32/clusters/DEFAULT_CLUSTER/services/DEFAULT_IMPALA/impalaQueries"
    assert calls[0][1]["limit"] == 2
    assert "Recent window minutes: 15" in output
    assert "Candidates selected: 1" in output


def test_config_cm_user_alias_supplies_http_username(tmp_path):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "cm_user": "config_cm_user",
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
        },
    )
    args = module.parse_args(["--config", str(config_path), "--dry-run"])
    config = module.build_config(args, env={"CM_PASSWORD": "secret-password"})
    http_config = module.build_http_config(config, env={"CM_PASSWORD": "secret-password"})

    assert config.cm_username == "config_cm_user"
    assert http_config.username == "config_cm_user"
    assert http_config.password == "secret-password"


def test_env_username_overrides_config_username(tmp_path):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "username": "config_cm_user",
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
        },
    )
    args = module.parse_args(["--config", str(config_path), "--dry-run"])
    config = module.build_config(args, env={"CM_USERNAME": "env_cm_user"})
    http_config = module.build_http_config(config, env={"CM_USERNAME": "env_cm_user"})

    assert config.cm_username == "env_cm_user"
    assert http_config.username == "env_cm_user"


def test_config_rejects_duplicate_username_aliases(tmp_path, capsys):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "cm_user": "config_cm_user",
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
            "username": "other_user",
        },
    )

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "duplicates normalized field username" in captured.err


def test_config_loads_recent_listing_defaults(tmp_path, capsys):
    module = load_collector_module()
    json_path = tmp_path / "recent.json"
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(tmp_path / "cm-corpus"),
            "recent_include_failed": True,
            "recent_include_running": True,
            "recent_limit": 3,
            "recent_min_duration_sec": 1.0,
            "recent_max_duration_sec": 10.0,
            "recent_order": "duration-desc",
            "recent_output_json": str(json_path),
            "recent_pool": "root.analytics",
            "recent_select": 2,
            "recent_user": "analyst",
            "recent_window_minutes": 45,
            "service": "CONFIG_IMPALA",
        },
    )

    class FakeClient:
        def get_json(self, path, params=None):
            return {
                "items": [
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "durationMillis": 2000,
                        "queryType": "QUERY",
                        "status": "failed",
                        "user": "analyst",
                        "poolName": "root.analytics",
                        "statement": "SELECT * FROM db.table_a",
                    },
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000002",
                        "durationMillis": 3000,
                        "queryType": "QUERY",
                        "status": "running",
                        "user": "analyst",
                        "poolName": "root.analytics",
                        "statement": "SELECT * FROM db.table_b",
                    },
                ]
            }

    result = module.main(
        ["--config", str(config_path), "--list-recent-queries"],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Recent window minutes: 45" in output
    assert "Recent inspect limit: 3" in output
    assert "Recent minimum duration seconds: 1.0" in output
    assert "Recent maximum duration seconds: 10.0" in output
    assert "Recent selection order: duration-desc" in output
    assert "Candidates selected: 2" in output
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["recent_min_duration_sec"] == 1.0
    assert payload["recent_max_duration_sec"] == 10.0
    assert payload["recent_order"] == "duration-desc"


def test_cli_recent_duration_options_override_config(tmp_path, capsys):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(tmp_path / "cm-corpus"),
            "recent_min_duration_sec": 10.0,
            "recent_order": "duration-asc",
            "service": "CONFIG_IMPALA",
        },
    )

    class FakeClient:
        def get_json(self, path, params=None):
            return {
                "items": [
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "durationMillis": 500,
                        "status": "succeeded",
                        "statement": "SELECT * FROM db.short_table",
                    },
                    {
                        "queryId": "aaaaaaaaaaaaaaaa:0000000000000002",
                        "durationMillis": 2000,
                        "status": "succeeded",
                        "statement": "SELECT * FROM db.long_table",
                    },
                ]
            }

    result = module.main(
        [
            "--config",
            str(config_path),
            "--list-recent-queries",
            "--recent-min-duration-sec",
            "0",
            "--recent-order",
            "duration-desc",
            "--recent-select",
            "1",
        ],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Recent minimum duration seconds: 0.0" in output
    assert "Recent selection order: duration-desc" in output
    assert output.count("selected=yes") == 1
    assert "query_id=aaaaaaaaaaaaaaaa:0000000000000002" in output
    assert "query_id=aaaaaaaaaaaaaaaa:0000000000000001" in output
    first_selected_line = next(line for line in output.splitlines() if "selected=yes" in line)
    assert "0000000000000002" in first_selected_line


def test_cli_values_override_config_values(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cli-corpus"
    config_path = write_config(
        tmp_path,
        {
            "ca_bundle": "/tmp/config-ca.pem",
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "limit": 3,
            "max_profile_bytes": 12345,
            "min_duration_sec": 45,
            "out": str(tmp_path / "config-corpus"),
            "redact": True,
            "service": "CONFIG_IMPALA",
            "since_hours": 2,
            "status": "failed",
        },
    )

    result = module.main(
        [
            "--config",
            str(config_path),
            "--cm-url",
            "https://cli-cm.example.com:7183",
            "--cluster",
            "CLI_CLUSTER",
            "--service",
            "CLI_IMPALA",
            "--out",
            str(output_dir),
            "--since-hours",
            "5",
            "--limit",
            "7",
            "--max-profile-bytes",
            "67890",
            "--min-duration-sec",
            "90",
            "--status",
            "succeeded",
            "--ca-bundle",
            "/tmp/cli-ca.pem",
            "--no-redact",
            "--dry-run",
        ],
        env={},
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "https://cli-cm.example.com:7183" in output
    assert "Cluster: CLI_CLUSTER" in output
    assert "Service: CLI_IMPALA" in output
    assert f"Output path: {output_dir}" in output
    assert "Since hours: 5" in output
    assert "Limit: 7" in output
    assert "Max profile bytes: 67890" in output
    assert "Minimum duration seconds: 90" in output
    assert "status: succeeded" in output
    assert "CA bundle: /tmp/cli-ca.pem" in output
    assert "Redaction: disabled" in output
    assert "config-cm" not in output
    assert not output_dir.exists()


def test_env_overrides_config_cm_url_and_ca_bundle(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cm-corpus"
    config_path = write_config(
        tmp_path,
        {
            "ca_bundle": "/tmp/config-ca.pem",
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(output_dir),
            "service": "CONFIG_IMPALA",
        },
    )

    result = module.main(
        ["--config", str(config_path), "--dry-run"],
        env={
            "CM_CA_BUNDLE": "/tmp/env-ca.pem",
            "CM_URL": "https://env-cm.example.com:7183",
        },
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "https://env-cm.example.com:7183" in output
    assert "CA bundle: /tmp/env-ca.pem" in output
    assert "config-cm" not in output
    assert "/tmp/config-ca.pem" not in output
    assert not output_dir.exists()


def test_max_profile_bytes_default_is_50_mib(tmp_path):
    module = load_collector_module()

    args = module.parse_args(base_args(tmp_path))
    config = module.build_config(args, env={})

    assert module.DEFAULT_MAX_PROFILE_BYTES == 52_428_800
    assert config.max_profile_bytes == 52_428_800
    assert config.collect_cm_timeseries is True
    assert config.cm_metrics_profile == "cm6"


def test_collect_cm_timeseries_can_be_disabled_for_explicit_collection(tmp_path):
    module = load_collector_module()

    args = module.parse_args(base_args(tmp_path) + ["--no-collect-cm-timeseries"])
    config = module.build_config(args, env={})

    assert config.collect_cm_timeseries is False


def test_max_profile_bytes_env_fallback_and_env_precedence(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cm-corpus"
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "max_profile_bytes": 2222,
            "out": str(output_dir),
            "service": "CONFIG_IMPALA",
        },
    )

    env_result = module.main(
        base_args(tmp_path) + ["--dry-run"],
        env={"CM_MAX_PROFILE_BYTES": "1111"},
    )
    env_output = capsys.readouterr().out
    assert env_result == 0
    assert "Max profile bytes: 1111" in env_output

    config_result = module.main(
        ["--config", str(config_path), "--dry-run"],
        env={"CM_MAX_PROFILE_BYTES": "1111"},
    )
    config_output = capsys.readouterr().out
    assert config_result == 0
    assert "Max profile bytes: 1111" in config_output
    assert not output_dir.exists()


@pytest.mark.parametrize("value", [0, -1, "large", True, None])
def test_config_rejects_invalid_max_profile_bytes(tmp_path, capsys, value):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "max_profile_bytes": value,
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
        },
    )

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "max_profile_bytes must be a positive integer" in captured.err


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("recent_min_duration_sec", -1, "recent_min_duration_sec must be a non-negative number"),
        (
            "recent_max_duration_sec",
            "slow",
            "recent_max_duration_sec must be a non-negative number",
        ),
        ("recent_order", "longest", "recent_order must be one of"),
    ],
)
def test_config_rejects_invalid_recent_duration_settings(
    tmp_path,
    capsys,
    field,
    value,
    expected,
):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            field: value,
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
        },
    )

    result = module.main(["--config", str(config_path), "--list-recent-queries"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert expected in captured.err


def test_recent_duration_max_must_not_be_below_min(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path)
        + [
            "--list-recent-queries",
            "--recent-min-duration-sec",
            "5",
            "--recent-max-duration-sec",
            "1",
        ],
        env={},
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "--recent-max-duration-sec must be >= --recent-min-duration-sec" in captured.err


@pytest.mark.parametrize("value", ["0", "-1", "large", ""])
def test_env_rejects_invalid_max_profile_bytes(tmp_path, capsys, value):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path) + ["--dry-run"],
        env={"CM_MAX_PROFILE_BYTES": value},
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Environment value for max_profile_bytes" in captured.err


def test_credentials_still_come_only_from_env_with_config(tmp_path, capsys):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
        },
    )

    result = module.main(
        ["--config", str(config_path), "--dry-run"],
        env={
            "CM_PASSWORD": "secret-password",
            "CM_TOKEN": "secret-token",
            "CM_USERNAME": "cm_user",
        },
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "CM_TOKEN configured (secret not shown)" in output
    assert "secret-password" not in output
    assert "secret-token" not in output


@pytest.mark.parametrize(
    "secret_key", ["password", "token", "CM_PASSWORD", "CM_TOKEN", "auth_header", "authorization"]
)
def test_config_rejects_secret_looking_keys(tmp_path, capsys, secret_key):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
            secret_key: "secret-value",
        },
    )

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "looks secret-bearing" in captured.err
    assert "secret-value" not in captured.err


def git_check_ignore(path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "check-ignore", "-v", path],
        cwd=REPO_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def git_ls_files(path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        cwd=REPO_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_local_config_gitignore_rules_are_safe():
    ignore_text = (REPO_DIR / ".gitignore").read_text(encoding="utf-8")

    assert "query-doctor-config.json" in ignore_text
    assert "!query-doctor-config.example.json" in ignore_text
    assert ".query-doctor-cm.local.json" in ignore_text
    assert "!.query-doctor-cm.local.example.json" not in ignore_text
    assert git_check_ignore("query-doctor-config.json").returncode == 0
    assert git_ls_files("query-doctor-config.example.json").returncode == 0
    assert git_ls_files(".query-doctor-cm.local.example.json").returncode != 0
    assert git_check_ignore(".query-doctor-cm.local.json").returncode == 0


def test_committed_local_config_template_is_safe():
    module = load_collector_module()
    template_path = REPO_DIR / "query-doctor-config.example.json"
    template_text = template_path.read_text(encoding="utf-8")
    template = json.loads(template_text)

    loaded = module.load_local_config(str(template_path), cwd=REPO_DIR)
    canonical_allowed_keys = set(module.LOCAL_CONFIG_ALLOWED_KEYS)
    normalized_template_keys = {module.normalize_local_config_key(key) for key in template}

    assert normalized_template_keys <= canonical_allowed_keys
    assert "cm_user" not in template
    assert "query_profile_source" not in template
    assert "optimizer_model" not in template
    assert template["optimizer_llm_model"] == "deepseek-coder-v2:16b"
    assert loaded["optimizer_model"] == "deepseek-coder-v2:16b"
    assert template["report_llm_provider"] == "ollama"
    assert template["report_llm_model"] == "qwen3-coder:30b-a3b-q8_0"
    assert "report_llm_base_url" not in template
    assert "optimizer_llm_base_url" not in template
    assert "metadata_krb5ccname" not in template
    assert template["language"] == "en"
    assert loaded["language"] == "en"
    assert template["out"] == "/tmp/query-doctor-local-output"
    assert template["recent_scan_timezone"] == "UTC"
    assert loaded["recent_scan_timezone"] == "UTC"
    assert template["recent_window_minutes"] == 60
    assert set(template) == {
        "clusters",
        "language",
        "optimizer_llm_model",
        "optimizer_llm_provider",
        "out",
        "recent_scan_timezone",
        "recent_window_minutes",
        "report_llm_model",
        "report_llm_provider",
    }
    assert len(template["clusters"]) == 2
    cm_cluster, direct_cluster = loaded["clusters"]
    assert cm_cluster["query_profile_source"] == "cm"
    assert cm_cluster["cm_url"] == "https://cm-prod.example.com:7183/"
    assert cm_cluster["cluster"] == "prod_cluster"
    assert cm_cluster["service"] == "impala"
    assert cm_cluster["cm_metrics_profile"] == "cm7"
    assert cm_cluster["metadata_coordinator"] == "impala-prod-coordinator.example.com:21050"
    assert direct_cluster["query_profile_source"] == "impala"
    assert "cm_url" not in direct_cluster
    assert "cluster" not in direct_cluster
    assert "service" not in direct_cluster
    assert direct_cluster["impala_kerberos_service_name"] == "hive"
    assert direct_cluster["metadata_kerberos_service_name"] == "hive"
    assert direct_cluster["collect_prometheus_timeseries"] is True
    for default_key in (
        "host",
        "port",
        "krb5ccname",
        "metadata_auth",
        "metadata_protocol",
        "metadata_redact",
        "metadata_timeout_sec",
        "metadata_max_tables",
        "metadata_max_output_bytes",
        "recent_parallelism",
        "recent_cm_jobs",
        "recent_metadata_jobs",
        "recent_user",
        "recent_pool",
    ):
        assert default_key not in template
    assert not any(
        key.lower() in {"password", "passwd", "token", "cookie", "authorization"}
        for key in template
    )
    for unsupported_key in (
        "profile_analysis_limit",
        "triage_profile_limit",
        "metadata_top_limit",
        "cm_inspect_limit",
    ):
        assert unsupported_key not in template
    assert "internal-db-host-01" not in template_text
    assert "internal.example.invalid" not in template_text
    assert "/Users/example" not in template_text


def test_config_rejects_unknown_keys(tmp_path, capsys):
    module = load_collector_module()
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(tmp_path / "cm-corpus"),
            "service": "CONFIG_IMPALA",
            "unexpected": "value",
        },
    )

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "Unknown config field unexpected" in captured.err


@pytest.mark.parametrize(
    "key",
    ["profile_analysis_limit", "triage_profile_limit", "metadata_top_limit", "cm_inspect_limit"],
)
def test_config_rejects_unsupported_generic_recent_scope_keys(tmp_path, key):
    module = load_collector_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(json.dumps({key: 1}), encoding="utf-8")

    with pytest.raises(module.ConfigError):
        module.load_local_config(str(config_path), cwd=tmp_path)


def test_config_rejects_invalid_json(tmp_path, capsys):
    module = load_collector_module()
    config_path = tmp_path / "invalid.json"
    config_path.write_text("{not-json", encoding="utf-8")

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "Invalid JSON" in captured.err


def test_config_rejects_missing_path(tmp_path, capsys):
    module = load_collector_module()
    config_path = tmp_path / "missing.json"

    result = module.main(["--config", str(config_path), "--dry-run"], env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "Could not read config file" in captured.err


def test_preflight_with_config_uses_mocked_client_and_creates_no_output(tmp_path, capsys):
    module = load_collector_module()
    output_dir = tmp_path / "cm-corpus"
    config_path = write_config(
        tmp_path,
        {
            "cluster": "CONFIG_CLUSTER",
            "cm_url": "https://config-cm.example.com:7183",
            "out": str(output_dir),
            "service": "CONFIG_IMPALA",
        },
    )
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {"items": []}

    result = module.main(
        ["--config", str(config_path), "--preflight"],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    assert result == 0
    assert calls[0][0] == "/api/v32/clusters/CONFIG_CLUSTER/services/CONFIG_IMPALA/impalaQueries"
    assert "Preflight result: OK" in captured.out
    assert not output_dir.exists()


def test_secrets_are_not_printed_in_dry_run(tmp_path, capsys):
    module = load_collector_module()
    secret_password = "super-secret-password"
    secret_token = "super-secret-token"

    result = module.main(
        [
            "--cm-url",
            f"https://cm_user:{secret_password}@cm.example.com:7183/path?token={secret_token}",
            "--cluster",
            "CLUSTER_NAME",
            "--service",
            "IMPALA_SERVICE_NAME",
            "--out",
            str(tmp_path / "cm-corpus"),
            "--dry-run",
        ],
        env={
            "CM_USERNAME": "cm_user",
            "CM_PASSWORD": secret_password,
            "CM_TOKEN": secret_token,
        },
    )

    output = capsys.readouterr().out
    assert result == 0
    assert secret_password not in output
    assert secret_token not in output
    assert "CM_TOKEN configured (secret not shown)" in output
    assert "credentials/query/fragment redacted" in output


def test_invalid_status_is_rejected():
    module = load_collector_module()

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--cm-url",
                "https://cm.example.com:7183",
                "--cluster",
                "CLUSTER_NAME",
                "--service",
                "IMPALA_SERVICE_NAME",
                "--out",
                "cases/cm-corpus",
                "--status",
                "running",
            ]
        )


def test_non_preflight_without_query_id_fails_broad_collection_closed(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(base_args(tmp_path), env={})

    captured = capsys.readouterr()
    assert result == 3
    assert "Broad CM profile collection is not enabled" in captured.err
    assert "Provide --query-id" in captured.err
    assert not (tmp_path / "cm-corpus").exists()


def test_non_preflight_query_id_without_redact_fails_closed(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("collection must not create a CM HTTP client without --redact")

    result = module.main(
        base_args(tmp_path) + ["--query-id", query_id, "--limit", "1"],
        env={},
        client_factory=fail_if_called,
    )

    captured = capsys.readouterr()
    assert result == 3
    assert "Real CM collection requires --redact" in captured.err
    assert not (tmp_path / "cm-corpus").exists()


def test_non_preflight_query_id_rejects_limit_other_than_one(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("collection must not create a CM HTTP client with limit != 1")

    result = module.main(
        base_args(tmp_path) + ["--query-id", query_id, "--redact", "--limit", "2"],
        env={},
        client_factory=fail_if_called,
    )

    captured = capsys.readouterr()
    assert result == 3
    assert "Single-query CM collection requires --limit 1" in captured.err
    assert not (tmp_path / "cm-corpus").exists()


def test_non_preflight_query_id_redact_writes_one_case_with_fake_client(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    output_dir = tmp_path / "cm-corpus"
    profile_text = (
        "User: alice\n"
        "SQL: SELECT * FROM sensitive_table\n"
        "Coordinator: impala-worker-1.example.invalid.example.com\n"
        "01:SCAN HDFS\n"
        "RowsProduced: 123456\n"
    )
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            assert params is None
            assert path.endswith(f"/impalaQueries/{query_id}")
            return {
                "queryId": query_id,
                "statement": "SELECT user_id FROM sensitive_table WHERE dt = '2026-01-01'",
                "durationMillis": 1234,
                "status": "succeeded",
                "queryState": "FINISHED",
                "admissionResult": "admitted",
                "admissionWaitMillis": 250,
                "rowsProduced": 123456,
                "bytesRead": 1048576,
                "totalBytesSent": 2097152,
                "memoryAggregatePeak": 3221225472,
            }

        def get_text(self, path, params=None, *, max_response_bytes=None):
            calls.append((path, params, max_response_bytes))
            return profile_text

    result = module.main(
        base_args(tmp_path)
        + [
            "--query-id",
            query_id,
            "--redact",
            "--limit",
            "1",
            "--max-profile-bytes",
            "52428800",
            "--metadata-source-tables-out",
            str(output_dir / ".metadata-source-tables.json"),
        ],
        env={"CM_TOKEN": "secret-token"},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    output = captured.out + captured.err
    case_dir = output_dir / "aaaaaaaaaaaaaaaa_0000000000000001"
    assert result == 0
    assert calls == [
        (
            f"/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries/{query_id}",
            {"format": "text"},
            52428800,
        )
    ]
    assert "[CM profile collector] Collection result: OK" in captured.out
    assert "Collected count: 1" in captured.out
    assert f"Output case directory: {case_dir}" in captured.out
    assert f"Profile text length: {len(profile_text)}" in captured.out
    assert "Redaction: enabled" in captured.out
    assert "Max profile bytes: 52428800" in captured.out
    assert "CM time-series context: enabled" in captured.out
    assert captured.err == ""
    assert "SELECT * FROM sensitive_table" not in output
    assert "alice" not in output
    assert "secret-token" not in output
    assert "Authorization" not in output

    assert case_dir.exists()
    assert {path.name for path in case_dir.iterdir()} == {
        "profile_digest.md",
        "query_metadata.json",
        "cm_metadata.json",
        "runtime_metrics_context.json",
        "cm_timeseries_context.json",
        "collection_warnings.txt",
    }
    assert not (case_dir / "analysis_facts.md").exists()
    assert not list(case_dir.glob("report*.md"))
    source_tables = json.loads(
        (output_dir / ".metadata-source-tables.json").read_text(encoding="utf-8")
    )
    assert source_tables == ["sensitive_table"]

    written_profile = (case_dir / "profile_digest.md").read_text(encoding="utf-8")
    metadata = json.loads((case_dir / "query_metadata.json").read_text(encoding="utf-8"))
    legacy_metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))
    timeseries_context = json.loads(
        (case_dir / "cm_timeseries_context.json").read_text(encoding="utf-8")
    )
    runtime_metrics_context = json.loads(
        (case_dir / "runtime_metrics_context.json").read_text(encoding="utf-8")
    )
    warnings_text = (case_dir / "collection_warnings.txt").read_text(encoding="utf-8")
    assert "alice" not in written_profile
    assert "impala-worker-1.example.invalid.example.com" not in written_profile
    # The statement's only table takes the first label, as metadata names it.
    assert "SQL: SELECT * FROM <db>.<table_1>" in written_profile
    assert "RowsProduced: 123456" in written_profile
    assert metadata["query_id"] == query_id
    assert legacy_metadata == metadata
    assert metadata["duration_ms"] == 1234
    assert metadata["query_state"] == "FINISHED"
    assert metadata["admission_result"] == "admitted"
    assert metadata["admission_wait_ms"] == 250
    assert metadata["rows_produced"] == 123456
    assert metadata["bytes_read"] == 1048576
    assert metadata["bytes_sent"] == 2097152
    assert metadata["memory_aggregate_peak"] == 3221225472
    assert metadata["statement"] == "SELECT user_id FROM <table> WHERE dt = '2026-01-01'"
    assert timeseries_context["available"] is False
    assert timeseries_context["reason"] == "query start/end time unavailable"
    assert timeseries_context["queries"] == []
    assert runtime_metrics_context == timeseries_context
    assert "collected by Query Doctor CM collector" in warnings_text
    assert "CM query details metadata collected" in warnings_text
    assert "CM time-series context unavailable" in warnings_text
    assert "redaction enabled" in warnings_text
    assert "analyzer/report were not run automatically" in warnings_text


def test_non_preflight_query_id_extracts_statement_from_profile_details(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    output_dir = tmp_path / "cm-corpus"
    profile_text = json.dumps(
        {
            "details": (
                "Query Runtime Profile\n"
                "Start Time: 2026-05-03 17:05:04.451431000\n"
                "End Time: 2026-05-03 17:05:11.400421000\n"
                "Query Type: DML\n"
                "Query State: FINISHED\n"
                "Query Status: OK\n"
                "User: alice\n"
                "Request Pool: root.analytics\n"
                "Sql Statement: INSERT OVERWRITE TABLE example_mart.target\n\n"
                "SELECT user_id FROM sensitive_table WHERE dt = '2026-01-01'\n\n"
                "Query Options (set by configuration):\n"
            )
        }
    )

    class FakeClient:
        def get_json(self, path, params=None):
            assert params is None
            assert path.endswith(f"/impalaQueries/{query_id}")
            return {
                "queryId": query_id,
                "durationMillis": 1234,
                "status": "succeeded",
            }

        def get_text(self, path, params=None, *, max_response_bytes=None):
            return profile_text

    result = module.main(
        base_args(tmp_path)
        + [
            "--query-id",
            query_id,
            "--redact",
            "--limit",
            "1",
        ],
        env={"CM_TOKEN": "secret-token"},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    output = captured.out + captured.err
    case_dir = output_dir / "aaaaaaaaaaaaaaaa_0000000000000001"
    metadata = json.loads((case_dir / "query_metadata.json").read_text(encoding="utf-8"))
    legacy_metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))
    warnings_text = (case_dir / "collection_warnings.txt").read_text(encoding="utf-8")
    assert result == 0
    assert metadata["query_id"] == query_id
    assert metadata["start_time"] == "2026-05-03T17:05:04Z"
    assert metadata["end_time"] == "2026-05-03T17:05:11Z"
    assert metadata["duration_ms"] == 1234
    assert metadata["query_type"] == "DML"
    assert metadata["query_state"] == "FINISHED"
    assert metadata["status"] == "succeeded"
    assert metadata["user"] == "<user>"
    assert metadata["pool"] == "<pool>"
    assert metadata["statement"] == (
        "INSERT OVERWRITE TABLE <db>.<table>\n\nSELECT user_id FROM <table> WHERE dt = '2026-01-01'"
    )
    assert "CM profile text timing metadata collected" in warnings_text
    assert "CM profile text status metadata collected" in warnings_text
    assert "CM profile text user metadata collected" in warnings_text
    assert "CM profile text pool metadata collected" in warnings_text
    assert "CM profile text statement metadata collected" in warnings_text
    assert "SELECT user_id FROM sensitive_table" not in output
    assert "secret-token" not in output


def test_extract_summary_metadata_from_profile_text_normalizes_profile_summary_fields():
    module = load_collector_module()
    profile_text = json.dumps(
        {
            "details": (
                "Query Runtime Profile\n"
                "Start Time: 2026-05-03 17:05:04.451431000\n"
                "End Time: 2026-05-03 17:05:11.400421000\n"
                "Query Type: DML\n"
                "Query State: FINISHED\n"
                "Query Status: OK\n"
                "User: alice\n"
                "Request Pool: root.analytics\n"
            )
        }
    )

    metadata = module.extract_summary_metadata_from_profile_text(profile_text)

    assert metadata == {
        "start_time": "2026-05-03T17:05:04Z",
        "end_time": "2026-05-03T17:05:11Z",
        "duration_ms": 7000,
        "query_type": "DML",
        "query_state": "FINISHED",
        "status": "OK",
        "user": "alice",
        "pool": "root.analytics",
    }


def test_non_preflight_query_id_oversized_profile_fails_without_writing_case(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"

    class FakeClient:
        def get_text(self, path, params=None, *, max_response_bytes=None):
            assert max_response_bytes == 12
            return "SELECT secret_value FROM sensitive_table"

    result = module.main(
        base_args(tmp_path)
        + [
            "--query-id",
            query_id,
            "--redact",
            "--limit",
            "1",
            "--max-profile-bytes",
            "12",
        ],
        env={"CM_TOKEN": "secret-token"},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert result == 4
    assert "Collection result: FAILED" in captured.err
    assert "exceeded maximum allowed bytes" in captured.err
    assert "SELECT" not in combined
    assert "secret_value" not in combined
    assert "sensitive_table" not in combined
    assert "secret-token" not in combined
    assert not (tmp_path / "cm-corpus").exists()


def test_non_preflight_query_id_collision_fails_without_overwrite(tmp_path, capsys):
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    case_dir = tmp_path / "cm-corpus" / "aaaaaaaaaaaaaaaa_0000000000000001"
    case_dir.mkdir(parents=True)
    existing_profile = case_dir / "profile_digest.md"
    existing_profile.write_text("existing profile\n", encoding="utf-8")

    class FakeClient:
        def get_text(self, path, params=None, *, max_response_bytes=None):
            return "# Synthetic profile\n"

    result = module.main(
        base_args(tmp_path) + ["--query-id", query_id, "--redact", "--limit", "1"],
        env={},
        client_factory=lambda http_config: FakeClient(),
    )

    captured = capsys.readouterr()
    assert result == 4
    assert "Collection result: FAILED" in captured.err
    assert "Refusing to overwrite existing case directory" in captured.err
    assert existing_profile.read_text(encoding="utf-8") == "existing profile\n"
    assert not (case_dir / "analysis_facts.md").exists()


@pytest.mark.parametrize("out_path", ["/", str(REPO_DIR)])
def test_dangerous_output_paths_are_rejected(out_path, tmp_path, capsys):
    module = load_collector_module()
    args = [
        "--cm-url",
        "https://cm.example.com:7183",
        "--cluster",
        "CLUSTER_NAME",
        "--service",
        "IMPALA_SERVICE_NAME",
        "--out",
        out_path,
        "--dry-run",
    ]

    result = module.main(args, env={})

    captured = capsys.readouterr()
    assert result == 2
    assert "Refusing to use" in captured.err


def test_insecure_skip_verify_is_false_by_default(tmp_path, capsys):
    module = load_collector_module()

    args = module.parse_args(base_args(tmp_path))
    config = module.build_config(args, env={})
    assert config.insecure_skip_verify is False

    result = module.main(base_args(tmp_path) + ["--dry-run"], env={})
    output = capsys.readouterr().out
    assert result == 0
    assert "TLS verification: enabled" in output
    assert "CA bundle: system default trust store" in output
    assert "UNSAFE" not in output


def test_insecure_skip_verify_is_explicitly_marked_unsafe(tmp_path, capsys):
    module = load_collector_module()

    result = module.main(
        base_args(tmp_path) + ["--dry-run", "--insecure-skip-verify"],
        env={},
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "TLS verification: disabled by --insecure-skip-verify (UNSAFE)" in output
    assert "CA bundle: ignored because TLS verification is disabled" in output


def test_build_query_filters_contains_non_secret_cli_filters(tmp_path):
    module = load_collector_module()
    args = module.parse_args(
        base_args(tmp_path)
        + [
            "--since-hours",
            "12",
            "--limit",
            "7",
            "--min-duration-sec",
            "30",
            "--pool",
            "etl",
            "--user",
            "analyst",
            "--status",
            "failed",
            "--query-id",
            "query-123",
            "--query-type",
            "QUERY",
        ]
    )
    config = module.build_config(
        args,
        env={
            "CM_USERNAME": "cm-user",
            "CM_PASSWORD": "secret-password",
            "CM_TOKEN": "secret-token",
        },
    )

    filters = module.build_query_filters(config)

    assert filters.as_log_dict() == {
        "cluster": "CLUSTER_NAME",
        "service": "IMPALA_SERVICE_NAME",
        "since_hours": 12,
        "limit": 7,
        "min_duration_sec": 30,
        "max_duration_sec": None,
        "server_duration_filter": False,
        "pool": "etl",
        "user": "analyst",
        "status": "failed",
        "query_id": "query-123",
        "query_type": "QUERY",
        "executing": None,
    }
    assert "secret" not in repr(filters)
    assert "secret" not in repr(filters.as_log_dict())


def test_collect_query_summaries_uses_fake_paginated_fetcher():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=24,
        limit=4,
        min_duration_sec=60,
    )
    calls = []

    def fake_fetch_page(received_filters, page_token):
        calls.append((received_filters, page_token))
        if page_token is None:
            return module.CMQueryPage(
                items=[
                    module.CMQuerySummary(query_id="q1", duration_ms=1000),
                    module.CMQuerySummary(query_id="q2", duration_ms=2000),
                ],
                next_page_token="next",
            )
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="q3", duration_ms=3000),
            ]
        )

    items, warnings = module.collect_query_summaries(filters, fake_fetch_page)

    assert [item.query_id for item in items] == ["q1", "q2", "q3"]
    assert warnings == []
    assert [token for _received_filters, token in calls] == [None, "next"]
    assert [received_filters.limit for received_filters, _token in calls] == [4, 4]
    assert [received_filters.page_size for received_filters, _token in calls] == [None, 2]


def test_collect_query_summaries_stops_after_limit():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=24,
        limit=2,
        min_duration_sec=60,
    )
    calls = []

    def fake_fetch_page(received_filters, page_token):
        calls.append(page_token)
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="q1"),
                module.CMQuerySummary(query_id="q2"),
                module.CMQuerySummary(query_id="q3"),
            ],
            next_page_token="should-not-fetch",
        )

    items, warnings = module.collect_query_summaries(filters, fake_fetch_page)

    assert [item.query_id for item in items] == ["q1", "q2"]
    assert warnings == []
    assert calls == [None]


def test_collect_query_summaries_pages_with_numeric_offsets_without_response_token():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=2,
        since_minutes=120,
        limit=2500,
        min_duration_sec=None,
        query_type="QUERY",
    )
    calls = []

    def fake_fetch_page(received_filters, page_token):
        calls.append((received_filters.page_size, page_token))
        offset = int(page_token or 0)
        page_size = received_filters.page_size or received_filters.limit
        if offset >= 2500:
            return module.CMQueryPage(items=[])
        count = min(page_size, 2500 - offset)
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(
                    query_id=f"q{offset + index}:id",
                    duration_ms=1000,
                    query_type="QUERY",
                    statement="SELECT 1",
                )
                for index in range(count)
            ]
        )

    items, warnings = module.collect_query_summaries(filters, fake_fetch_page)

    assert len(items) == 2500
    assert warnings == []
    assert calls == [(1000, None), (1000, "1000"), (500, "2000")]


def test_collect_query_summaries_handles_empty_page():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=24,
        limit=5,
        min_duration_sec=60,
    )

    def fake_fetch_page(received_filters, page_token):
        assert page_token is None
        return module.CMQueryPage(items=[])

    items, warnings = module.collect_query_summaries(filters, fake_fetch_page)

    assert items == []
    assert warnings == []


def test_collect_query_summaries_returns_safe_error_without_secret_leak():
    module = load_collector_module()
    secret_token = "super-secret-token"
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=24,
        limit=5,
        min_duration_sec=60,
    )

    def fake_fetch_page(received_filters, page_token):
        raise module.CMClientError(f"CM rejected token {secret_token}")

    items, warnings = module.collect_query_summaries(
        filters,
        fake_fetch_page,
        secrets=[secret_token],
    )

    assert items == []
    assert warnings == ["CM rejected token <secret>"]
    assert secret_token not in repr(warnings)


def test_collect_query_summaries_applies_query_id_filter_in_abstract_layer():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=24,
        limit=10,
        min_duration_sec=60,
        query_id="target-query",
    )

    def fake_fetch_page(received_filters, page_token):
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="other-query"),
                module.CMQuerySummary(query_id="target-query"),
            ]
        )

    items, warnings = module.collect_query_summaries(filters, fake_fetch_page)

    assert [item.query_id for item in items] == ["target-query"]
    assert warnings == []


def test_query_summary_log_sanitizer_has_no_secret_fields():
    module = load_collector_module()
    summary = module.CMQuerySummary(
        query_id="query-123",
        start_time="2026-04-29T10:00:00Z",
        end_time="2026-04-29T10:05:00Z",
        duration_ms=300000,
        status="failed",
        user="analyst",
        pool="etl",
        query_type="QUERY",
        statement="SELECT * FROM example_mart.orders",
    )

    log_summary = module.sanitize_query_summary_for_log(summary)

    assert log_summary["query_id"] == "query-123"
    assert log_summary["duration_ms"] == 300000
    assert "password" not in repr(log_summary).lower()
    assert "token" not in repr(log_summary).lower()


def test_parse_cm_query_summary_from_mocked_response():
    module = load_collector_module()
    raw = {
        "queryId": "query-123",
        "startTime": "2026-04-29T10:00:00Z",
        "endTime": "2026-04-29T10:02:00Z",
        "durationMillis": "120000",
        "status": "failed",
        "user": "synthetic_user",
        "poolName": "synthetic_pool",
        "statementType": "QUERY",
    }

    summary = module.parse_cm_query_summary(raw)

    assert summary.query_id == "query-123"
    assert summary.start_time == "2026-04-29T10:00:00Z"
    assert summary.end_time == "2026-04-29T10:02:00Z"
    assert summary.duration_ms == 120000
    assert summary.duration_sec == 120.0
    assert summary.status == "failed"
    assert summary.user == "synthetic_user"
    assert summary.pool == "synthetic_pool"
    assert summary.query_type == "QUERY"


def test_parse_cm_query_summary_reads_attributes_map():
    module = load_collector_module()
    raw = {
        "queryId": "query-123",
        "attributes": {
            "admission_result": "admitted immediately",
            "admission_wait_ms": "15",
            "pool": "root.analytics",
            "query_duration": "120000",
            "query_state": "FINISHED",
            "query_status": "OK",
            "query_type": "QUERY",
            "user": "analyst",
        },
        "statement": "SELECT 1",
    }

    summary = module.parse_cm_query_summary(raw)

    assert summary.duration_ms == 120000
    assert summary.status == "OK"
    assert summary.user == "analyst"
    assert summary.pool == "root.analytics"
    assert summary.query_type == "QUERY"
    assert summary.query_state == "FINISHED"
    assert summary.admission_result == "admitted immediately"
    assert summary.admission_wait_ms == 15


def test_parse_cm_query_summary_reads_camelcase_admission_wait():
    module = load_collector_module()
    raw = {
        "queryId": "query-123",
        "attributes": {
            "admissionWait": "45000",
        },
    }

    summary = module.parse_cm_query_summary(raw)

    assert summary.admission_wait_ms == 45000


def test_parse_cm_query_summary_tolerates_missing_optional_fields():
    module = load_collector_module()

    summary = module.parse_cm_query_summary({"id": "query-123"})

    assert summary == module.CMQuerySummary(query_id="query-123")


def test_parse_cm_query_summary_missing_required_query_id_fails_safely():
    module = load_collector_module()

    with pytest.raises(module.CMAdapterError) as exc:
        module.parse_cm_query_summary(
            {
                "user": "synthetic_user",
                "token": "secret-token-value",
                "authorization": "Bearer secret-token-value",
            }
        )

    message = str(exc.value)
    assert "missing required query id" in message
    assert "secret-token-value" not in message


def test_parse_cm_query_summary_page_with_multiple_shapes_and_next_token():
    module = load_collector_module()
    raw = {
        "querySummaries": [
            {
                "query_id": "query-1",
                "duration_sec": 1.5,
                "queryType": "QUERY",
            },
            {
                "queryId": "query-2",
                "durationMs": 2500,
                "pool": "synthetic_pool",
            },
        ],
        "paging": {"nextPageToken": "page-2"},
        "warnings": ["password=secret-value"],
    }

    page = module.parse_cm_query_summary_page(raw)

    assert [item.query_id for item in page.items] == ["query-1", "query-2"]
    assert [item.duration_ms for item in page.items] == [1500, 2500]
    assert page.items[0].query_type == "QUERY"
    assert page.items[1].pool == "synthetic_pool"
    assert page.next_page_token == "page-2"
    assert page.warnings == ["password=<redacted>"]

    alternate = module.parse_cm_query_summary_page(
        {
            "impalaQueries": [
                {
                    "queryId": "query-3",
                    "queryType": "QUERY",
                    "durationMillis": 1000,
                }
            ]
        }
    )
    assert [item.query_id for item in alternate.items] == ["query-3"]


@pytest.mark.parametrize("field", ["profile", "profileText", "text"])
def test_extract_profile_text_from_supported_mocked_fields(field):
    module = load_collector_module()
    profile_text = "# Synthetic profile\n01:SCAN HDFS\n"

    assert module.extract_profile_text({field: profile_text}) == profile_text


@pytest.mark.parametrize("raw", [{}, {"profile": {"nested": "not text"}}])
def test_extract_profile_text_missing_or_non_string_fails_safely(raw):
    module = load_collector_module()

    with pytest.raises(module.CMAdapterError) as exc:
        module.extract_profile_text(raw)

    message = str(exc.value)
    assert "profile" in message.lower()
    assert "nested" not in message


def test_adapter_error_sanitizer_removes_secret_like_values():
    module = load_collector_module()
    secret_token = "secret-token-value"

    message = module.sanitize_adapter_error_message(
        "Authorization: Bearer secret-token-value "
        "Cookie: sessionid=secret-cookie-value\n"
        f"{synthetic_cm_credential_url('/api?token=url-secret')} password=secret-password",
        secrets=[secret_token],
    )

    assert secret_token not in message
    assert "secret-cookie-value" not in message
    assert "embedded-pass" not in message
    assert "url-secret" not in message
    assert "secret-password" not in message
    assert "Authorization: Bearer <redacted>" in message
    assert "Cookie: <redacted>" in message
    assert "password=<redacted>" in message


def test_log_sanitizer_redacts_host_like_values():
    module = load_collector_module()

    message = module.sanitize_text_for_log(
        "failed on impala-worker-1.example.invalid.example.com and 10.20.30.40"
    )

    assert "impala-worker-1.example.invalid.example.com" not in message
    assert "10.20.30.40" not in message
    assert "host_01" in message
    assert "host_02" in message


def test_build_cm_query_summary_page_request_uses_non_secret_filters():
    module = load_collector_module()
    now = module.datetime(2026, 4, 29, 12, 0, 0, tzinfo=module.timezone.utc)
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
        server_duration_filter=True,
        pool="synthetic_pool",
        user="synthetic_user",
        status="failed",
        query_id="query-123",
        query_type="QUERY",
    )

    path, params = module.build_cm_query_summary_page_request(filters, "page-2", now=now)

    assert path == "/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries"
    assert params == {
        "from": "2026-04-29T11:00:00Z",
        "to": "2026-04-29T12:00:00Z",
        "limit": 2,
        "offset": "page-2",
        "filter": (
            'queryDuration > 60s AND user = "synthetic_user" AND pool = "synthetic_pool" '
            'AND query_type = "QUERY"'
        ),
    }
    assert "password" not in repr(params).lower()
    assert "token" not in repr(params).lower()


def test_build_cm_query_summary_page_request_encodes_path_segments():
    module = load_collector_module()
    now = module.datetime(2026, 4, 29, 12, 0, 0, tzinfo=module.timezone.utc)
    filters = module.CMQueryFilters(
        cluster="../CLUSTER NAME?x=1",
        service="http://service/name",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    path, params = module.build_cm_query_summary_page_request(filters, now=now)

    assert path == (
        "/api/v32/clusters/..%2FCLUSTER%20NAME%3Fx%3D1/services/"
        "http%3A%2F%2Fservice%2Fname/impalaQueries"
    )
    assert "../CLUSTER" not in path
    assert "http://service" not in path
    assert params["limit"] == 1
    assert "filter" not in params


def test_build_cm_query_summary_page_request_uses_explicit_time_window():
    module = load_collector_module()
    now = module.datetime(2026, 4, 29, 12, 0, 0, tzinfo=module.timezone.utc)
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=24,
        since_minutes=1440,
        from_time="2026-05-02T21:00:00Z",
        to_time="2026-05-02T22:00:00Z",
        limit=10,
        min_duration_sec=None,
    )

    _path, params = module.build_cm_query_summary_page_request(filters, now=now)

    assert params["from"] == "2026-05-02T21:00:00Z"
    assert params["to"] == "2026-05-02T22:00:00Z"


def test_recent_listing_min_duration_builds_server_side_filter():
    module = load_collector_module()
    config = module.CollectorConfig(
        cm_url="https://cm.example.net",
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        out=Path("/tmp/out"),
        since_hours=24,
        limit=1,
        max_profile_bytes=1000,
        min_duration_sec=60,
        pool=None,
        user=None,
        status="all",
        query_id=None,
        query_type=None,
        cm_username=None,
        dry_run=False,
        preflight=False,
        list_recent_queries=True,
        recent_limit=100,
        recent_select=5,
        recent_window_minutes=60,
        recent_min_duration_sec=1.25,
        recent_max_duration_sec=None,
        recent_order="duration-desc",
        recent_output_json=None,
        recent_include_failed=False,
        recent_include_running=False,
        recent_user=None,
        recent_pool=None,
        redact=True,
        redact_identifiers=False,
        redact_hosts=True,
        metadata_source_tables_out=None,
        collect_cm_timeseries=False,
        cm_metrics_profile=module.DEFAULT_CM_METRICS_PROFILE,
        cm_timeseries_padding_sec=120,
        max_timeseries_bytes=2097152,
        max_timeseries_points=2000,
        insecure_skip_verify=False,
        ca_bundle=None,
        credentials=module.CredentialSummary(False, False, False),
    )
    filters = module.build_recent_query_filters(config)

    path, params = module.build_cm_query_summary_page_request(filters)

    assert path.endswith("/impalaQueries")
    assert params["limit"] == 100
    assert params["filter"] == "queryDuration > 2s AND executing = false"


def test_recent_listing_max_duration_builds_server_side_filter():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=10,
        min_duration_sec=None,
        max_duration_sec=10.5,
        server_duration_filter=True,
    )

    path, params = module.build_cm_query_summary_page_request(filters)

    assert path.endswith("/impalaQueries")
    assert params["filter"] == "queryDuration < 10s"


def test_duration_filter_fractional_min_is_not_rounded_down():
    module = load_collector_module()

    assert module.duration_lower_bound_literal(10.001) == "11s"
    assert (
        module.build_cm_query_filter_expression(
            module.CMQueryFilters(
                cluster="CLUSTER_NAME",
                service="IMPALA_SERVICE_NAME",
                since_hours=1,
                limit=10,
                min_duration_sec=10.0001,
                server_duration_filter=True,
            )
        )
        == "queryDuration > 11s"
    )


def test_string_filters_are_quoted_for_cm_filter_expression():
    module = load_collector_module()

    assert module.build_cm_query_filter_expression(
        module.CMQueryFilters(
            cluster="CLUSTER_NAME",
            service="IMPALA_SERVICE_NAME",
            since_hours=1,
            limit=10,
            min_duration_sec=None,
            pool='root.analytics"daily',
            user="domain\\analyst",
            query_type="QUERY",
            executing=True,
        )
    ) == (
        'user = "domain\\\\analyst" AND pool = "root.analytics\\"daily" '
        'AND query_type = "QUERY" AND executing = true'
    )


def test_collect_query_summaries_duration_zero_fallback_is_bounded():
    module = load_collector_module()
    calls = []
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=10,
        server_duration_filter=True,
    )

    def fake_fetch_page(received_filters, page_token):
        calls.append((received_filters, page_token))
        if received_filters.server_duration_filter:
            return module.CMQueryPage(items=[])
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="q1", duration_ms=1000),
                module.CMQuerySummary(query_id="q2", duration_ms=11000),
                module.CMQuerySummary(query_id="q3", duration_ms=12000),
            ]
        )

    items, warnings, used_fallback = module.collect_query_summaries_with_duration_fallback(
        filters,
        fake_fetch_page,
    )

    assert used_fallback is True
    assert [item.query_id for item in items] == ["q1", "q2"]
    assert calls[0][0].server_duration_filter is True
    assert calls[1][0].server_duration_filter is False
    assert calls[1][0].limit == 2
    assert warnings == []


def test_fetch_cm_query_summary_page_calls_client_and_parses_page():
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, params))
            return {
                "items": [
                    {"queryId": "query-1", "durationMillis": 1000},
                    {"queryId": "query-2", "durationSec": 2.5},
                ],
                "nextPageToken": "page-2",
            }

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=10,
        min_duration_sec=60,
        status="all",
    )

    page = module.fetch_cm_query_summary_page(FakeClient(), filters)

    assert [item.query_id for item in page.items] == ["query-1", "query-2"]
    assert [item.duration_ms for item in page.items] == [1000, 2500]
    assert page.next_page_token == "page-2"
    assert (
        calls[0][0] == "/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries"
    )
    assert calls[0][1]["limit"] == 10
    assert "from" in calls[0][1]
    assert "to" in calls[0][1]
    assert "cluster" not in calls[0][1]
    assert "service" not in calls[0][1]


def test_fetch_cm_query_summary_page_passes_page_token_param():
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append(params)
            return {"items": []}

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=10,
        min_duration_sec=60,
    )

    page = module.fetch_cm_query_summary_page(FakeClient(), filters, "page-2")

    assert page.items == []
    assert calls[0]["offset"] == "page-2"


def test_fetch_cm_query_summary_page_sanitizes_client_errors():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        raise OSError(
            "Authorization: Bearer secret-token "
            f"{synthetic_cm_credential_url('/api')} password=secret-password"
        )

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url=synthetic_cm_credential_url(),
            password="secret-password",
            token="secret-token",
        ),
        opener=fake_opener,
    )
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    with pytest.raises(module.CMHttpError) as exc:
        module.fetch_cm_query_summary_page(client, filters)

    message = str(exc.value)
    assert "secret-token" not in message
    assert "secret-password" not in message
    assert "embedded-pass" not in message
    assert "Authorization: Bearer <redacted>" in message


def test_fetch_cm_query_summary_page_sanitizes_adapter_errors():
    module = load_collector_module()

    class FakeClient:
        def get_json(self, path, params=None):
            return {"items": {"password": "secret-password"}}

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    with pytest.raises(module.CMAdapterError) as exc:
        module.fetch_cm_query_summary_page(FakeClient(), filters)

    message = str(exc.value)
    assert "items must be a list" in message
    assert "secret-password" not in message


def test_collect_query_summaries_can_use_cm_fetch_helper_with_fake_client():
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_json(self, path, params=None):
            calls.append((path, dict(params or {})))
            if not params or "offset" not in params:
                return {
                    "items": [{"queryId": "query-1"}],
                    "nextPageToken": "page-2",
                }
            return {"items": [{"queryId": "query-2"}]}

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
    )
    client = FakeClient()

    items, warnings = module.collect_query_summaries(
        filters,
        lambda received_filters, page_token: module.fetch_cm_query_summary_page(
            client,
            received_filters,
            page_token,
        ),
    )

    assert [item.query_id for item in items] == ["query-1", "query-2"]
    assert warnings == []
    assert (
        calls[0][0] == "/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries"
    )
    assert "cluster" not in calls[0][1]
    assert "offset" not in calls[0][1]
    assert calls[1][1]["offset"] == "page-2"


def test_build_cm_profile_text_request_uses_non_secret_params():
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
        user="synthetic_user",
        pool="synthetic_pool",
    )

    path, params = module.build_cm_profile_text_request(filters, query_id)

    assert path == (
        f"/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries/{query_id}"
    )
    assert params == {"format": "text"}
    assert "password" not in repr(params).lower()
    assert "token" not in repr(params).lower()
    assert "synthetic_user" not in repr(params)
    assert "synthetic_pool" not in repr(params)


@pytest.mark.parametrize("query_id", ["", "   "])
def test_build_cm_profile_text_request_rejects_empty_query_id(query_id):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    with pytest.raises(module.CMAdapterError):
        module.build_cm_profile_text_request(filters, query_id)


@pytest.mark.parametrize(
    "query_id",
    [
        "query-123",
        "aaaaaaaaaaaaaaaa0000000000000001",
        "aaaaaaaaaaaaaaaa/0000000000000001",
        "../aaaaaaaaaaaaaaaa:0000000000000001",
        "aaaaaaaaaaaaaaaa%3A0000000000000001",
        "aaaaaaaaaaaaaaaa%2F0000000000000001",
        "https://cm.example.com/aaaaaaaaaaaaaaaa:0000000000000001",
        "aaaaaaaaaaaaaaaa:0000000000000001?format=text",
        "aaaaaaaaaaaaaaaa:0000000000000001#fragment",
        "aaaaaaaaaaaaaaaa:0000000000000001 extra",
    ],
)
def test_build_cm_profile_text_request_rejects_unsafe_query_id_shapes(query_id):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    with pytest.raises(module.CMAdapterError):
        module.build_cm_profile_text_request(filters, query_id)


def test_build_cm_profile_text_request_encodes_cluster_and_service_but_preserves_query_id_separator():
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="../CLUSTER NAME?x=1",
        service="http://service/name",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"

    path, params = module.build_cm_profile_text_request(filters, query_id)
    client = module.CMHttpClient(module.CMHttpConfig(cm_url="https://cm.example.com:7183"))
    url = client.build_url(path, params)

    assert path == (
        "/api/v32/clusters/..%2FCLUSTER%20NAME%3Fx%3D1/services/"
        "http%3A%2F%2Fservice%2Fname/impalaQueries/"
        f"{query_id}"
    )
    assert params == {"format": "text"}
    assert path.rsplit("/", 1)[-1] == query_id
    assert "http://service" not in path
    assert "../CLUSTER" not in url
    assert "format=text" in url


def test_build_cm_timeseries_request_uses_allowlisted_query_without_secrets():
    module = load_collector_module()
    query = module.CM_TIMESERIES_QUERY_ALLOWLIST[0]

    path, params = module.build_cm_timeseries_request(
        query,
        from_time="2026-05-04T10:00:00Z",
        to_time="2026-05-04T10:05:00Z",
    )

    assert path == "/api/v32/timeseries"
    assert params["query"] == query.tsquery
    assert params["from"] == "2026-05-04T10:00:00Z"
    assert params["to"] == "2026-05-04T10:05:00Z"
    assert "password" not in repr(params).lower()
    assert "token" not in repr(params).lower()


def test_collect_cm_timeseries_context_summarizes_without_raw_points():
    module = load_collector_module()
    calls = []

    class FakeClient:
        def get_text(self, path, params=None, *, max_response_bytes=None):
            calls.append((path, params, max_response_bytes))
            return json.dumps(
                {
                    "items": [
                        {
                            "timeSeries": [
                                {
                                    "data": [
                                        {"timestamp": "2026-05-04T10:00:00Z", "value": 10},
                                        {"timestamp": "2026-05-04T10:01:00Z", "value": 30},
                                    ]
                                },
                                {
                                    "data": [
                                        {"timestamp": "2026-05-04T10:00:00Z", "value": 5},
                                        {"timestamp": "2026-05-04T10:01:00Z", "value": 15},
                                    ]
                                },
                            ]
                        }
                    ]
                }
            )

    summary = module.CMQuerySummary(
        query_id="aaaaaaaaaaaaaaaa:0000000000000001",
        start_time="2026-05-04T10:00:00Z",
        end_time="2026-05-04T10:05:00Z",
    )

    context = module.collect_cm_timeseries_context(
        FakeClient(),
        summary,
        padding_sec=60,
        max_response_bytes=12345,
        max_points=10,
    )

    assert context["available"] is True
    assert context["metrics_profile"] == "cm6"
    assert context["window"] == {
        "from": "2026-05-04T09:59:00Z",
        "to": "2026-05-04T10:06:00Z",
        "padding_sec": 60,
    }
    assert context["limits"] == {
        "max_response_bytes": 12345,
        "max_points_per_query": 10,
    }
    assert len(context["queries"]) == len(module.CM_TIMESERIES_QUERY_ALLOWLIST)
    assert context["queries"][0]["signal_id"] == module.CM_TIMESERIES_QUERY_ALLOWLIST[0].signal_id
    assert context["queries"][0]["point_count"] == 4
    assert context["queries"][0]["min"] == 5
    assert context["queries"][0]["max"] == 30
    assert context["queries"][0]["avg"] == 15
    assert context["queries"][0]["series_count"] == 2
    assert context["queries"][0]["top_series"][0]["series"] == "series_01"
    assert context["queries"][0]["top_series"][0]["max"] == 30
    assert context["queries"][0]["top_series"][1]["series"] == "series_02"
    assert context["queries"][0]["top_series"][1]["max"] == 15
    assert "timestamp" not in json.dumps(context)
    assert all(call[0] == "/api/v32/timeseries" for call in calls)
    assert all(call[2] == 12345 for call in calls)


def test_fetch_cm_profile_text_calls_client_and_returns_text_profile():
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    calls = []

    class FakeClient:
        def get_text(self, path, params=None, *, max_response_bytes=None):
            calls.append((path, params, max_response_bytes))
            return "# Synthetic profile\n01:SCAN HDFS\n"

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    profile_text = module.fetch_cm_profile_text(FakeClient(), filters, query_id)

    assert profile_text == "# Synthetic profile\n01:SCAN HDFS\n"
    assert calls == [
        (
            f"/api/v32/clusters/CLUSTER_NAME/services/IMPALA_SERVICE_NAME/impalaQueries/{query_id}",
            {"format": "text"},
            module.DEFAULT_MAX_PROFILE_BYTES,
        )
    ]


def test_fetch_cm_profile_text_sanitizes_client_errors():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        raise OSError(
            "Authorization: Bearer secret-token "
            f"{synthetic_cm_credential_url('/api')} password=secret-password"
        )

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url=synthetic_cm_credential_url(),
            password="secret-password",
            token="secret-token",
        ),
        opener=fake_opener,
    )
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    with pytest.raises(module.CMHttpError) as exc:
        module.fetch_cm_profile_text(
            client,
            filters,
            "aaaaaaaaaaaaaaaa:0000000000000001",
        )

    message = str(exc.value)
    assert "secret-token" not in message
    assert "secret-password" not in message
    assert "embedded-pass" not in message
    assert "Authorization: Bearer <redacted>" in message


def test_fetch_cm_profile_text_accepts_profile_below_limit():
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"
    calls = []

    class FakeClient:
        def get_text(self, path, params=None, *, max_response_bytes=None):
            calls.append(max_response_bytes)
            return "0123456789"

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    profile_text = module.fetch_cm_profile_text(
        FakeClient(),
        filters,
        query_id,
        max_profile_bytes=10,
    )

    assert profile_text == "0123456789"
    assert calls == [10]


def test_fetch_cm_profile_text_rejects_profile_above_limit_without_content_leak():
    module = load_collector_module()
    query_id = "aaaaaaaaaaaaaaaa:0000000000000001"

    class FakeClient:
        def get_text(self, path, params=None, *, max_response_bytes=None):
            assert max_response_bytes == 12
            return "SELECT secret_value FROM sensitive_table"

    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    with pytest.raises(module.CMAdapterError) as exc:
        module.fetch_cm_profile_text(
            FakeClient(),
            filters,
            query_id,
            max_profile_bytes=12,
        )

    message = str(exc.value)
    assert "exceeded maximum allowed bytes" in message
    assert "limit 12" in message
    assert "SELECT" not in message
    assert "secret_value" not in message
    assert "sensitive_table" not in message


def test_collect_and_write_cm_profiles_writes_two_synthetic_cases(tmp_path):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
    )

    def fake_fetch_summary_page(received_filters, page_token):
        assert received_filters == filters
        assert page_token is None
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(
                    query_id="query-1",
                    duration_ms=1000,
                    status="failed",
                    user="analyst_one",
                ),
                module.CMQuerySummary(
                    query_id="query-2",
                    duration_ms=2000,
                    status="succeeded",
                    user="analyst_two",
                ),
            ]
        )

    def fake_fetch_profile_text(summary):
        return f"# Synthetic profile for {summary.query_id}\n01:SCAN HDFS\n"

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
    )

    assert result.collected_count == 2
    assert result.failed_count == 0
    assert result.skipped_count == 0
    assert [path.name for path in result.case_dirs] == ["query-1", "query-2"]
    assert result.warnings == []
    assert result.failures == []

    for query_id in ("query-1", "query-2"):
        case_dir = tmp_path / query_id
        assert (case_dir / "profile_digest.md").exists()
        assert (case_dir / "cm_metadata.json").exists()
        assert (case_dir / "collection_warnings.txt").exists()
        assert not (case_dir / "analysis_facts.md").exists()
        assert not (case_dir / "report.md").exists()
        assert not (case_dir / "report_user.md").exists()

    profile_text = (tmp_path / "query-1" / "profile_digest.md").read_text(encoding="utf-8")
    metadata = json.loads((tmp_path / "query-1" / "cm_metadata.json").read_text(encoding="utf-8"))
    assert "Synthetic profile for query-1" in profile_text
    assert metadata["query_id"] == "query-1"
    assert metadata["duration_ms"] == 1000
    assert metadata["user"] == "analyst_one"


def test_collect_and_write_cm_profiles_redacts_written_case(tmp_path):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=1,
        min_duration_sec=60,
    )

    def fake_fetch_summary_page(received_filters, page_token):
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(
                    query_id="query-1",
                    user="alice",
                    pool="root.secret_pool",
                )
            ]
        )

    def fake_fetch_profile_text(summary):
        return (
            "# Synthetic profile\n"
            "User: alice\n"
            "Coordinator: impala-worker-1.example.invalid.example.com\n"
            "RowsProduced: 123456\n"
        )

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
        redact=True,
    )

    assert result.collected_count == 1
    assert result.failed_count == 0

    case_dir = tmp_path / "query-1"
    profile_text = (case_dir / "profile_digest.md").read_text(encoding="utf-8")
    metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))
    assert "alice" not in profile_text
    assert "impala-worker-1.example.invalid.example.com" not in profile_text
    assert "RowsProduced: 123456" in profile_text
    assert metadata["user"] == "<user>"
    assert metadata["pool"] == "<pool>"


def test_collect_and_write_cm_profiles_respects_filter_limit(tmp_path):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
    )
    fetched_profiles = []

    def fake_fetch_summary_page(received_filters, page_token):
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="query-1"),
                module.CMQuerySummary(query_id="query-2"),
                module.CMQuerySummary(query_id="query-3"),
            ],
            next_page_token="should-not-fetch",
        )

    def fake_fetch_profile_text(summary):
        fetched_profiles.append(summary.query_id)
        return f"# Synthetic profile for {summary.query_id}\n"

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
    )

    assert result.collected_count == 2
    assert result.failed_count == 0
    assert fetched_profiles == ["query-1", "query-2"]
    assert (tmp_path / "query-1").exists()
    assert (tmp_path / "query-2").exists()
    assert not (tmp_path / "query-3").exists()


def test_collect_and_write_cm_profiles_records_profile_failure_and_continues(tmp_path):
    module = load_collector_module()
    secret = "secret-token-value"
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=3,
        min_duration_sec=60,
    )

    def fake_fetch_summary_page(received_filters, page_token):
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="query-1"),
                module.CMQuerySummary(query_id="query-2"),
            ]
        )

    def fake_fetch_profile_text(summary):
        if summary.query_id == "query-1":
            raise module.CMClientError(f"profile fetch failed token={secret}")
        return "# Synthetic profile for query-2\n"

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
        secrets=[secret],
    )

    assert result.collected_count == 1
    assert result.failed_count == 1
    assert not (tmp_path / "query-1").exists()
    assert (tmp_path / "query-2" / "profile_digest.md").exists()
    assert "query-1" in result.failures[0]
    assert secret not in result.failures[0]
    assert "<secret>" in result.failures[0]


def test_collect_and_write_cm_profiles_records_oversized_profile_failure_and_continues(tmp_path):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
    )

    def fake_fetch_summary_page(received_filters, page_token):
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="query-1"),
                module.CMQuerySummary(query_id="query-2"),
            ]
        )

    def fake_fetch_profile_text(summary):
        if summary.query_id == "query-1":
            profile_text = "SELECT secret_value FROM sensitive_table"
            module.enforce_profile_text_size(profile_text, max_profile_bytes=12)
            return profile_text
        return "# Synthetic profile for query-2\n"

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
    )

    assert result.collected_count == 1
    assert result.failed_count == 1
    assert not (tmp_path / "query-1").exists()
    assert (tmp_path / "query-2" / "profile_digest.md").exists()
    assert "query-1" in result.failures[0]
    assert "exceeded maximum allowed bytes" in result.failures[0]
    assert "SELECT" not in result.failures[0]
    assert "secret_value" not in result.failures[0]
    assert "sensitive_table" not in result.failures[0]


def test_collect_and_write_cm_profiles_records_write_collision_and_continues(tmp_path):
    module = load_collector_module()
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
    )
    existing_case = tmp_path / "query-1"
    existing_case.mkdir()
    existing_profile = existing_case / "profile_digest.md"
    existing_profile.write_text("existing profile\n", encoding="utf-8")

    def fake_fetch_summary_page(received_filters, page_token):
        return module.CMQueryPage(
            items=[
                module.CMQuerySummary(query_id="query-1"),
                module.CMQuerySummary(query_id="query-2"),
            ]
        )

    def fake_fetch_profile_text(summary):
        return f"# Synthetic profile for {summary.query_id}\n"

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
    )

    assert result.collected_count == 1
    assert result.failed_count == 1
    assert existing_profile.read_text(encoding="utf-8") == "existing profile\n"
    assert (tmp_path / "query-2" / "profile_digest.md").exists()
    assert "query-1" in result.failures[0]
    assert "Refusing to overwrite existing case directory" in result.failures[0]


def test_collect_and_write_cm_profiles_sanitizes_summary_fetch_failure(tmp_path):
    module = load_collector_module()
    secret = "secret-token-value"
    filters = module.CMQueryFilters(
        cluster="CLUSTER_NAME",
        service="IMPALA_SERVICE_NAME",
        since_hours=1,
        limit=2,
        min_duration_sec=60,
    )

    def fake_fetch_summary_page(received_filters, page_token):
        raise module.CMClientError(f"summary fetch failed token={secret}")

    def fake_fetch_profile_text(summary):
        raise AssertionError("profile fetch should not be called")

    result = module.collect_and_write_cm_profiles(
        filters=filters,
        out_dir=tmp_path,
        fetch_summary_page=fake_fetch_summary_page,
        fetch_profile_text=fake_fetch_profile_text,
        secrets=[secret],
    )

    assert result.collected_count == 0
    assert result.failed_count == 1
    assert result.case_dirs == []
    assert "query summary collection" in result.failures[0]
    assert secret not in repr(result.warnings)
    assert secret not in repr(result.failures)
    assert "<secret>" in result.failures[0]
    assert list(tmp_path.iterdir()) == []


def test_broad_non_dry_run_cli_still_does_not_attempt_collection(tmp_path, monkeypatch, capsys):
    module = load_collector_module()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("collection helper should not be called by CLI yet")

    monkeypatch.setattr(module, "collect_and_write_cm_profiles", fail_if_called)
    monkeypatch.setattr(module, "collect_query_summaries", fail_if_called)
    monkeypatch.setattr(module, "fetch_cm_query_summary_page", fail_if_called)
    monkeypatch.setattr(module, "fetch_cm_profile_text", fail_if_called)
    monkeypatch.setattr(module, "CMHttpClient", fail_if_called)

    result = module.main(base_args(tmp_path), env={})

    captured = capsys.readouterr()
    assert result == 3
    assert "Broad CM profile collection is not enabled" in captured.err


def test_write_collected_case_writes_expected_files_under_output_root(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(
        query_id="query-123",
        start_time="2026-04-29T10:00:00Z",
        end_time="2026-04-29T10:01:30Z",
        duration_ms=90000,
        status="failed",
        user="analyst",
        pool="etl",
        query_type="QUERY",
        statement="SELECT * FROM example_mart.orders",
    )

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text="# Synthetic profile digest\n",
        warnings=["synthetic warning"],
    )

    assert case_dir == tmp_path / "query-123"
    assert (case_dir / "profile_digest.md").read_text(encoding="utf-8") == (
        "# Synthetic profile digest\n"
    )
    assert (case_dir / "collection_warnings.txt").read_text(encoding="utf-8") == (
        "synthetic warning\n"
    )

    metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "duration_ms": 90000,
        "duration_sec": 90.0,
        "end_time": "2026-04-29T10:01:30Z",
        "pool": "etl",
        "query_id": "query-123",
        "query_type": "QUERY",
        "start_time": "2026-04-29T10:00:00Z",
        "statement": "SELECT * FROM example_mart.orders",
        "status": "failed",
        "user": "analyst",
    }
    assert "password" not in repr(metadata).lower()
    assert "token" not in repr(metadata).lower()


def test_write_collected_case_sanitizes_warning_secrets(tmp_path):
    module = load_collector_module()
    secret = "secret-token-value"
    summary = module.CMQuerySummary(query_id="query-123")

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text="# Synthetic profile digest\n",
        warnings=[
            f"collector warning with {secret}",
            "Cookie: sessionid=warning-cookie-secret",
            "Authorization: Bearer warning-auth-secret",
            "failed on impala-worker-1.example.invalid.example.com",
        ],
        secrets=[secret],
    )

    warnings_text = (case_dir / "collection_warnings.txt").read_text(encoding="utf-8")
    assert "collector warning with <secret>" in warnings_text
    assert "Cookie: <redacted>" in warnings_text
    assert "Authorization: Bearer <redacted>" in warnings_text
    assert "host_01" in warnings_text
    assert secret not in warnings_text
    assert "warning-cookie-secret" not in warnings_text
    assert "warning-auth-secret" not in warnings_text
    assert "impala-worker-1.example.invalid.example.com" not in warnings_text


def test_write_collected_case_query_id_cannot_escape_output_directory(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(query_id="../../outside")

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text="# Synthetic profile digest\n",
    )

    assert case_dir == tmp_path / "outside"
    assert (case_dir / "profile_digest.md").exists()
    assert not (tmp_path.parent / "outside").exists()


@pytest.mark.parametrize("query_id", ["", "///", "..."])
def test_write_collected_case_rejects_empty_or_invalid_query_id(tmp_path, query_id):
    module = load_collector_module()
    summary = module.CMQuerySummary(query_id=query_id)

    with pytest.raises(module.OutputError):
        module.write_collected_case(
            tmp_path,
            summary,
            profile_digest_text="# Synthetic profile digest\n",
        )


def test_write_collected_case_fails_closed_when_case_directory_exists(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(query_id="query-123")
    (tmp_path / "query-123").mkdir()

    with pytest.raises(module.OutputError):
        module.write_collected_case(
            tmp_path,
            summary,
            profile_digest_text="# Synthetic profile digest\n",
        )


def test_case_dir_for_query_rejects_dangerous_output_roots():
    module = load_collector_module()
    summary = module.CMQuerySummary(query_id="query-123")

    with pytest.raises(module.OutputError):
        module.case_dir_for_query(Path("/"), summary)

    with pytest.raises(module.OutputError):
        module.case_dir_for_query(REPO_DIR, summary)


def test_redact_profile_text_removes_sensitive_values_but_preserves_counters():
    module = load_collector_module()
    text = f"""
Query ID: 4f2c:abcd
User: alice
Email: alice@example.com
Coordinator: impala-worker-1.example.invalid.example.com
Host: 10.20.30.40
CM URL: {synthetic_cm_basic_url("/api?token=url-token-secret")}
Authorization: Bearer secret-token
Cookie: sessionid=super-secret-cookie; csrftoken=secret-csrf
Set-Cookie: cm-session=another-secret-cookie; HttpOnly
password=hunter2
cookie=inline-cookie-secret
access_token: ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890
03:HASH JOIN
  actual rows: 1234567
  estimated rows: 10.55K
  PeakMemUsage: 1.50 GiB
  TotalBytesSent: 42.0 MB
  Runtime: 3m12s
"""

    redacted = module.redact_profile_text(text)

    assert "alice" not in redacted
    assert "alice@example.com" not in redacted
    assert "impala-worker-1.example.invalid.example.com" not in redacted
    assert "10.20.30.40" not in redacted
    assert "cm_user" not in redacted
    assert "cm_pass" not in redacted
    assert "url-token-secret" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "super-secret-cookie" not in redacted
    assert "secret-csrf" not in redacted
    assert "another-secret-cookie" not in redacted
    assert "inline-cookie-secret" not in redacted
    assert "hunter2" not in redacted
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in redacted
    assert "Cookie: <redacted>" in redacted
    assert "Set-Cookie: <redacted>" in redacted

    assert "Query ID: 4f2c:abcd" in redacted
    assert "03:HASH JOIN" in redacted
    assert "actual rows: 1234567" in redacted
    assert "estimated rows: 10.55K" in redacted
    assert "PeakMemUsage: 1.50 GiB" in redacted
    assert "TotalBytesSent: 42.0 MB" in redacted
    assert "Runtime: 3m12s" in redacted


def test_redact_profile_text_preserves_stable_safe_host_aliases():
    module = load_collector_module()
    text = """
Coordinator: impala-worker-1.example.invalid.example.com:22000
Instance query:0001 (host=impala-worker-1.example.invalid.example.com:22000)
Instance query:0002 (host=impala-worker-2.example.invalid.example.com:22000)
Instance query:0003 (host=impala-worker-1.example.invalid.example.com:22000)
DataNode: 10.20.30.40:9866
DataNode: 10.20.30.41:9866
DataNode: 10.20.30.40:9866
RowsProduced: 1234567
BytesWritten: 66.0 GiB
PeakMemUsage: 256.00 MiB
TotalTime: 4.90h
"""

    redacted = module.redact_profile_text(text)

    assert "impala-worker-1.example.invalid.example.com" not in redacted
    assert "impala-worker-2.example.invalid.example.com" not in redacted
    assert "prod.example.com" not in redacted
    assert "10.20.30.40" not in redacted
    assert "10.20.30.41" not in redacted
    assert "Coordinator: host_01:22000" in redacted
    assert redacted.count("host_01:22000") == 3
    assert redacted.count("host_02:22000") == 1
    assert redacted.count("host_03:9866") == 2
    assert redacted.count("host_04:9866") == 1
    assert "RowsProduced: 1234567" in redacted
    assert "BytesWritten: 66.0 GiB" in redacted
    assert "PeakMemUsage: 256.00 MiB" in redacted
    assert "TotalTime: 4.90h" in redacted


def test_redact_profile_text_can_preserve_hosts_for_private_diagnostics():
    module = load_collector_module()
    text = """
User: alice
Coordinator: impala-worker-1.example.invalid.example.com:22000
Instance query:0001 (host=impala-worker-1.example.invalid.example.com:22000)
Host: 10.20.30.40
Authorization: Bearer secret-token
"""

    redacted = module.redact_profile_text(text, redact_hosts=False)

    assert "alice" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "impala-worker-1.example.invalid.example.com:22000" in redacted
    assert "host=impala-worker-1.example.invalid.example.com:22000" in redacted
    assert "Host: 10.20.30.40" in redacted


def test_redact_profile_text_redacts_pool_fields_and_local_paths():
    module = load_collector_module()
    text = """
Request Pool: root.analytics
Admission Pool: root.batch
Scratch path: /Users/example/query-doctor/scratch
Temporary file: /private/tmp/query-doctor-profile.tmp
"""

    redacted = module.redact_profile_text(text)

    assert "root.analytics" not in redacted
    assert "root.batch" not in redacted
    assert "/Users/example" not in redacted
    assert "/private/tmp/query-doctor-profile.tmp" not in redacted
    assert "Request Pool: <pool>" in redacted
    assert "Admission Pool: <pool>" in redacted
    assert "Scratch path: <local_path>" in redacted
    assert "Temporary file: <local_path>" in redacted


def test_redact_profile_text_redacts_ipv6_without_touching_timestamps():
    module = load_collector_module()
    text = """
Host: 2001:db8::1
Coordinator: [2001:db8::1]:22000
Network Address: 2001:db8::2
RPC peer 2001:db8::2
CM URL: https://[2001:db8::3]:7183/api
Start Time: 2026-04-30 11:21:41.656793000
"""

    redacted = module.redact_profile_text(text)

    assert "2001:db8" not in redacted
    assert "Host: host_01" in redacted
    assert "Coordinator: host_01:22000" in redacted
    assert "https://host_02:7183/api" in redacted
    assert redacted.count("host_03") == 2
    assert "11:21:41.656793000" in redacted


def test_redact_profile_text_identifier_redaction_is_opt_in():
    module = load_collector_module()
    text = "SELECT * FROM db.table d JOIN example_dim.users u ON d.user_id = u.id"

    default_redacted = module.redact_profile_text(text)
    identifier_redacted = module.redact_profile_text(text, redact_identifiers=True)

    assert "db.table" in default_redacted
    assert "example_dim.users" in default_redacted
    assert "FROM <db>.<table>" in identifier_redacted
    assert "JOIN <db>.<table>" in identifier_redacted
    assert "db.table" not in identifier_redacted
    assert "example_dim.users" not in identifier_redacted


def test_redact_metadata_redacts_sensitive_fields_but_preserves_query_facts():
    module = load_collector_module()
    metadata = {
        "query_id": "query-123",
        "start_time": "2026-04-29T10:00:00Z",
        "end_time": "2026-04-29T10:01:30Z",
        "duration_ms": 90000,
        "duration_sec": 90.0,
        "status": "failed",
        "query_type": "QUERY",
        "user": "alice",
        "pool": "root.secret_pool",
        "coordinator_host": "impala-worker-1.example.invalid.example.com",
        "cm_url": synthetic_cm_basic_url(),
        "auth_token": "secret-token-value",
        "email": "alice@example.com",
    }

    redacted = module.redact_metadata(metadata)

    assert redacted["query_id"] == "query-123"
    assert redacted["start_time"] == "2026-04-29T10:00:00Z"
    assert redacted["end_time"] == "2026-04-29T10:01:30Z"
    assert redacted["duration_ms"] == 90000
    assert redacted["duration_sec"] == 90.0
    assert redacted["status"] == "failed"
    assert redacted["query_type"] == "QUERY"
    assert redacted["user"] == "<user>"
    assert redacted["pool"] == "<pool>"
    assert redacted["coordinator_host"] == "host_01"
    assert redacted["cm_url"] == "<url>"
    assert redacted["auth_token"] == "<redacted>"
    assert redacted["email"] == "<email>"

    redacted_repr = repr(redacted)
    assert "alice" not in redacted_repr
    assert "secret_pool" not in redacted_repr
    assert "impala-worker-1.example.invalid.example.com" not in redacted_repr
    assert "cm_pass" not in redacted_repr
    assert "secret-token-value" not in redacted_repr


def test_redact_metadata_can_preserve_hosts_for_private_diagnostics():
    module = load_collector_module()
    metadata = {
        "query_id": "query-123",
        "user": "alice",
        "coordinator_host": "impala-worker-1.example.invalid.example.com",
        "auth_token": "secret-token-value",
    }

    redacted = module.redact_metadata(metadata, redact_hosts=False)

    assert redacted["query_id"] == "query-123"
    assert redacted["user"] == "<user>"
    assert redacted["coordinator_host"] == "impala-worker-1.example.invalid.example.com"
    assert redacted["auth_token"] == "<redacted>"


def test_write_collected_case_with_redaction_writes_redacted_digest_and_metadata(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(
        query_id="query-123",
        start_time="2026-04-29T10:00:00Z",
        end_time="2026-04-29T10:01:30Z",
        duration_ms=90000,
        status="failed",
        user="alice",
        pool="root.secret_pool",
        query_type="QUERY",
    )
    profile_digest = """
# Synthetic profile
User: alice
Coordinator: impala-worker-1.example.invalid.example.com
Instance query:0001 (host=impala-worker-1.example.invalid.example.com:22000)
Instance query:0002 (host=impala-worker-2.example.invalid.example.com:22000)
Host: 10.20.30.40
Authorization: Basic abcdefghijklmnopqrstuvwxyz123456
02:SCAN HDFS
  RowsProduced: 1234567
  PeakMemUsage: 256.00 MiB
"""

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text=profile_digest,
        warnings=["warning contains secret-token-value"],
        secrets=["secret-token-value"],
        redact=True,
    )

    written_digest = (case_dir / "profile_digest.md").read_text(encoding="utf-8")
    metadata = json.loads((case_dir / "query_metadata.json").read_text(encoding="utf-8"))
    legacy_metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))
    warnings_text = (case_dir / "collection_warnings.txt").read_text(encoding="utf-8")

    assert "alice" not in written_digest
    assert "impala-worker-1.example.invalid.example.com" not in written_digest
    assert "impala-worker-2.example.invalid.example.com" not in written_digest
    assert "prod.example.com" not in written_digest
    assert "10.20.30.40" not in written_digest
    assert "abcdefghijklmnopqrstuvwxyz123456" not in written_digest
    assert "Coordinator: host_01" in written_digest
    assert "host=host_01:22000" in written_digest
    assert "host=host_03:22000" in written_digest
    assert "Host: host_02" in written_digest
    assert "02:SCAN HDFS" in written_digest
    assert "RowsProduced: 1234567" in written_digest
    assert "PeakMemUsage: 256.00 MiB" in written_digest

    assert metadata["query_id"] == "query-123"
    assert legacy_metadata == metadata
    assert metadata["duration_ms"] == 90000
    assert metadata["user"] == "<user>"
    assert metadata["pool"] == "<pool>"
    assert "alice" not in repr(metadata)
    assert "secret_pool" not in repr(metadata)

    assert warnings_text == "warning contains <secret>\n"
    assert "secret-token-value" not in warnings_text


def test_write_collected_case_without_redaction_remains_unchanged(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(
        query_id="query-123",
        duration_ms=1000,
        user="alice",
        pool="root.secret_pool",
    )
    profile_digest = "User: alice\nCoordinator: impala-worker-1.example.invalid.example.com\n"

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text=profile_digest,
        redact=False,
    )

    written_digest = (case_dir / "profile_digest.md").read_text(encoding="utf-8")
    metadata = json.loads((case_dir / "query_metadata.json").read_text(encoding="utf-8"))
    legacy_metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))

    assert written_digest == profile_digest
    assert metadata["user"] == "alice"
    assert metadata["pool"] == "root.secret_pool"
    assert legacy_metadata == metadata


def test_write_collected_case_preserves_statement_for_local_optimizer_source(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(
        query_id="query-123",
        duration_ms=1000,
        statement="SELECT secret_col FROM example_guarded_db.table_a WHERE ds = '2026-05-03'",
    )

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text="# Synthetic profile digest\n",
        redact=True,
    )

    metadata = json.loads((case_dir / "query_metadata.json").read_text(encoding="utf-8"))
    legacy_metadata = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))

    assert (
        metadata["statement"]
        == "SELECT secret_col FROM example_guarded_db.table_a WHERE ds = '2026-05-03'"
    )
    assert legacy_metadata == metadata


def test_write_collected_case_dual_writes_cm_runtime_context(tmp_path):
    module = load_collector_module()
    summary = module.CMQuerySummary(query_id="query-123", duration_ms=1000)
    context = {
        "available": True,
        "source": "cm_timeseries",
        "source_label": "Cloudera Manager time-series metrics",
        "queries": [],
    }

    case_dir = module.write_collected_case(
        tmp_path,
        summary,
        profile_digest_text="# Synthetic profile digest\n",
        cm_timeseries_context=context,
        redact=True,
    )

    runtime_metrics_context = json.loads(
        (case_dir / "runtime_metrics_context.json").read_text(encoding="utf-8")
    )
    legacy_context = json.loads(
        (case_dir / "cm_timeseries_context.json").read_text(encoding="utf-8")
    )
    assert runtime_metrics_context == context
    assert legacy_context == context


def test_cm_http_config_repr_and_display_do_not_expose_secrets():
    module = load_collector_module()
    config = module.CMHttpConfig(
        cm_url=synthetic_cm_credential_url(),
        username="cm_user",
        password="secret-password",
        token="secret-token",
    )

    config_repr = repr(config)
    display_repr = repr(config.safe_display())

    assert "secret-password" not in config_repr
    assert "secret-token" not in config_repr
    assert "embedded-pass" not in config_repr
    assert "secret-password" not in display_repr
    assert "secret-token" not in display_repr
    assert "embedded-pass" not in display_repr
    assert config.cm_url == "https://cm.example.com:7183"
    assert config.verify_tls is True
    assert config.timeout_sec == 30
    assert config.safe_display()["auth"] == "bearer token configured"


@pytest.mark.parametrize("cm_url", ["ftp://cm.example.com", "not-a-url"])
def test_cm_http_config_rejects_invalid_url(cm_url):
    module = load_collector_module()

    with pytest.raises(module.ConfigError):
        module.CMHttpConfig(cm_url=cm_url)


def test_cm_http_client_builds_url_from_base_path_and_params():
    module = load_collector_module()
    client = module.CMHttpClient(module.CMHttpConfig(cm_url="https://cm.example.com:7183/cm"))

    url = client.build_url("/api/v1/queries", {"limit": 20, "status": "failed", "empty": None})

    assert url == "https://cm.example.com:7183/cm/api/v1/queries?limit=20&status=failed"


def test_cm_http_client_rejects_absolute_api_path():
    module = load_collector_module()
    client = module.CMHttpClient(module.CMHttpConfig(cm_url="https://cm.example.com:7183"))

    with pytest.raises(module.CMHttpError):
        client.build_url("https://other.example.com/api")


def test_cm_http_client_rejects_parent_traversal_api_path():
    module = load_collector_module()
    client = module.CMHttpClient(module.CMHttpConfig(cm_url="https://cm.example.com:7183/cm"))

    with pytest.raises(module.CMHttpError):
        client.build_url("../api/v1/test")


def test_cm_http_client_get_json_uses_get_timeout_and_default_tls_context(monkeypatch):
    module = load_collector_module()
    calls = []
    default_context = object()

    def fake_default_context(*, cafile=None):
        assert cafile is None
        return default_context

    monkeypatch.setattr(module.ssl, "create_default_context", fake_default_context)

    def fake_opener(request, timeout=None, context=None):
        calls.append((request, timeout, context))
        return FakeResponse('{"ok": true}')

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            token="secret-token",
            timeout_sec=12,
        ),
        opener=fake_opener,
    )

    payload = client.get_json("/api/v1/test", {"limit": 1})

    assert payload == {"ok": True}
    request, timeout, context = calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == "https://cm.example.com:7183/api/v1/test?limit=1"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert timeout == 12
    assert context is default_context


def test_cm_http_client_uses_ca_bundle_for_verified_tls_context(monkeypatch):
    module = load_collector_module()
    calls = []
    verified_context = object()
    cafiles = []

    def fake_default_context(*, cafile=None):
        cafiles.append(cafile)
        return verified_context

    def fake_opener(request, timeout=None, context=None):
        calls.append(context)
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(module.ssl, "create_default_context", fake_default_context)

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            ca_bundle="/tmp/example-ca.pem",
        ),
        opener=fake_opener,
    )

    assert client.get_json("/api/v1/test") == {"ok": True}
    assert cafiles == ["/tmp/example-ca.pem"]
    assert calls == [verified_context]


def test_cm_http_client_insecure_tls_uses_unverified_context(monkeypatch):
    module = load_collector_module()
    calls = []
    unverified_context = object()

    def fail_default_context(*args, **kwargs):
        raise AssertionError("CA bundle must be ignored when TLS verification is disabled")

    def fake_unverified_context():
        return unverified_context

    def fake_opener(request, timeout=None, context=None):
        calls.append(context)
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(module.ssl, "create_default_context", fail_default_context)
    monkeypatch.setattr(module.ssl, "_create_unverified_context", fake_unverified_context)

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            ca_bundle="/tmp/example-ca.pem",
            verify_tls=False,
        ),
        opener=fake_opener,
    )

    assert client.get_json("/api/v1/test") == {"ok": True}
    assert calls == [unverified_context]


def test_cm_http_client_missing_ca_bundle_error_is_sanitized(monkeypatch):
    module = load_collector_module()
    opener_called = False

    def fake_default_context(*, cafile=None):
        raise OSError(f"cannot load {cafile} token=secret-token")

    def fake_opener(request, timeout=None, context=None):
        nonlocal opener_called
        opener_called = True
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(module.ssl, "create_default_context", fake_default_context)

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            token="secret-token",
            ca_bundle="/tmp/missing-ca.pem",
        ),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError) as exc:
        client.get_json("/api/v1/test")

    message = str(exc.value)
    assert "Could not load CA bundle /tmp/missing-ca.pem" in message
    assert "secret-token" not in message
    assert "token=<redacted>" in message
    assert opener_called is False


def test_cm_http_client_basic_auth_and_token_precedence():
    module = load_collector_module()
    basic_client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            username="cm_user",
            password="secret-password",
        )
    )
    token_client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            username="cm_user",
            password="secret-password",
            token="secret-token",
        )
    )

    basic_header = basic_client.authorization_header()
    token_header = token_client.authorization_header()

    assert basic_header is not None
    assert basic_header.startswith("Basic ")
    assert "secret-password" not in basic_header
    assert token_header == "Bearer secret-token"


def test_cm_http_client_error_sanitizes_secrets_and_url_credentials():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        raise OSError(
            "Authorization: Bearer secret-token "
            f"{synthetic_cm_credential_url('/api?token=url-secret')} password=secret-password"
        )

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url=synthetic_cm_credential_url(),
            password="secret-password",
            token="secret-token",
        ),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError) as exc:
        client.get_text("/api/v1/test")

    message = str(exc.value)
    assert "secret-token" not in message
    assert "secret-password" not in message
    assert "embedded-pass" not in message
    assert "url-secret" not in message
    assert "Authorization: Bearer <redacted>" in message
    assert "password=<redacted>" in message


def test_cm_http_client_get_text_rejects_response_above_limit_without_payload_leak():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        return FakeResponse("SELECT secret_value FROM sensitive_table")

    client = module.CMHttpClient(
        module.CMHttpConfig(cm_url="https://cm.example.com:7183"),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError) as exc:
        client.get_text("/api/v1/test", max_response_bytes=12)

    message = str(exc.value)
    assert "CM response exceeded maximum allowed bytes" in message
    assert "actual at least 13, limit 12" in message
    assert "SELECT" not in message
    assert "secret_value" not in message
    assert "sensitive_table" not in message


def test_cm_http_client_get_text_sanitizes_incomplete_read():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        return BrokenReadResponse()

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            token="secret-token",
        ),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError) as exc:
        client.get_text("/api/v1/test")

    message = str(exc.value)
    assert "CM request failed safely." in message
    assert "IncompleteRead" not in message
    assert "partial" not in message
    assert "secret-token" not in message


def test_cm_http_client_http_error_sanitizes_status_context():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        raise module.urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden token=secret-token",
            hdrs=None,
            fp=None,
        )

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            token="secret-token",
        ),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError) as exc:
        client.get_text("/api/v1/test")

    message = str(exc.value)
    assert "HTTP 403 from CM" in message
    assert "secret-token" not in message


def test_cm_http_client_invalid_json_error_is_sanitized():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        return FakeResponse("not-json")

    client = module.CMHttpClient(
        module.CMHttpConfig(
            cm_url="https://cm.example.com:7183",
            token="secret-token",
        ),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError) as exc:
        client.get_json("/api/v1/test")

    assert "CM returned invalid JSON" in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_cm_http_client_rejects_non_object_json():
    module = load_collector_module()

    def fake_opener(request, timeout=None, context=None):
        return FakeResponse("[1, 2, 3]")

    client = module.CMHttpClient(
        module.CMHttpConfig(cm_url="https://cm.example.com:7183"),
        opener=fake_opener,
    )

    with pytest.raises(module.CMHttpError):
        client.get_json("/api/v1/test")
