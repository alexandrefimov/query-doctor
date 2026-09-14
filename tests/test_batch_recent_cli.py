import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from command_test_support import command_args, command_uses_role


REPO_DIR = Path(__file__).resolve().parents[1]

from query_doctor.impala import hs2_runner


@pytest.fixture(autouse=True)
def metadata_driver_present(monkeypatch):
    """These tests exercise metadata wiring, not whether impyla is installed."""
    monkeypatch.setattr(hs2_runner, "driver_available", lambda: True)


def load_batch_module():
    from query_doctor.cli import batch_recent

    return batch_recent


def assert_line_locator(
    locators: list[dict[str, object]],
    coordinate: str,
    start_line: int,
    end_line: int,
) -> None:
    assert {
        "coordinate": coordinate,
        "line_span": {"start_line": start_line, "end_line": end_line},
        "line_span_source": "line_range_from_sql_parser",
    } in [
        {
            "coordinate": locator.get("coordinate"),
            "line_span": locator.get("line_span"),
            "line_span_source": locator.get("line_span_source"),
        }
        for locator in locators
    ]


def test_package_entrypoint_keeps_repo_root_anchor():
    from query_doctor.cli import batch_recent

    assert batch_recent.REPO_DIR == REPO_DIR


def test_batch_recent_direct_impala_config_does_not_require_cm_auth(tmp_path):
    module = load_batch_module()
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--prometheus-url",
        "http://prometheus.example.com:9090",
        "--impala-profile-prefer-json",
        "--impala-profile-collect-docs",
        "--collect-cm-events",
        "--collect-cm-timeseries",
    )
    module.preflight(config, env={})

    assert config.query_profile_source == "impala"
    assert config.impala_profile_hosts == ("impalad-1.example.com",)
    assert config.cm_url is None
    assert config.collect_cm_events is False
    assert config.collect_cm_timeseries is False
    assert config.collect_prometheus_timeseries is True
    assert config.prometheus_url == "http://prometheus.example.com:9090"
    assert config.prometheus_metrics_profile == "ambari-hadoop"
    assert config.impala_profile_prefer_json is True
    assert config.impala_profile_collect_docs is True


def test_batch_recent_direct_impala_discovery_filters_window_and_selects_candidates(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            end_time="2026-05-12T10:15:00Z",
            duration_ms=120000,
            status="finished",
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="cccccccccccccccc:dddddddddddddddd",
            end_time="2026-05-12T12:15:00Z",
            duration_ms=120000,
            status="finished",
            query_type="QUERY",
            statement="SELECT 2",
        ),
    ]

    def fake_fetch_impala_query_summaries(**kwargs):
        assert kwargs["hosts"] == ("impalad-1.example.com",)
        return type(
            "Result",
            (),
            {"summaries": summaries, "warnings": [], "query_log_at_capacity": True},
        )()

    monkeypatch.setattr(module, "fetch_impala_query_summaries", fake_fetch_impala_query_summaries)
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--from-time",
        "2026-05-12T10:00:00Z",
        "--to-time",
        "2026-05-12T11:00:00Z",
        "--no-min-duration-filter",
    )

    discovery = module.discover_candidates(config, env={})

    assert discovery.server_filter_expression == "impala-daemon-query-list"
    assert discovery.duration_filter_mode == "client-side"
    assert discovery.summaries_inspected == 1
    assert discovery.query_log_at_capacity is True
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == ["aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"]


def test_batch_recent_direct_impala_include_running_survives_window_filter(monkeypatch, tmp_path):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            end_time="2026-05-12T12:15:00Z",
            duration_ms=120000,
            status="finished",
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="cccccccccccccccc:dddddddddddddddd",
            start_time="2026-05-12T12:20:00Z",
            duration_ms=0,
            status="running",
            query_state="running",
            query_type="QUERY",
            statement="SELECT 2",
        ),
    ]

    def fake_fetch_impala_query_summaries(**kwargs):
        return type("Result", (), {"summaries": summaries, "warnings": []})()

    monkeypatch.setattr(module, "fetch_impala_query_summaries", fake_fetch_impala_query_summaries)
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--from-time",
        "2026-05-12T10:00:00Z",
        "--to-time",
        "2026-05-12T11:00:00Z",
        "--no-min-duration-filter",
        "--include-running",
    )

    discovery = module.discover_candidates(config, env={})

    assert discovery.summaries_inspected == 1
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == ["cccccccccccccccc:dddddddddddddddd"]


def test_batch_recent_direct_impala_window_uses_start_when_end_precedes_start(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            start_time="2026-05-12T10:15:00Z",
            end_time="1970-01-01T00:00:00Z",
            status="finished",
            query_type="QUERY",
            statement="SELECT 1",
        ),
    ]

    def fake_fetch_impala_query_summaries(**kwargs):
        return type("Result", (), {"summaries": summaries, "warnings": []})()

    monkeypatch.setattr(module, "fetch_impala_query_summaries", fake_fetch_impala_query_summaries)
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--from-time",
        "2026-05-12T10:00:00Z",
        "--to-time",
        "2026-05-12T11:00:00Z",
        "--no-min-duration-filter",
    )

    discovery = module.discover_candidates(config, env={})

    assert discovery.summaries_inspected == 1
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == ["aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"]


def test_batch_recent_direct_impala_only_running_filters_to_running_summaries(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            end_time="2026-05-12T12:15:00Z",
            duration_ms=120000,
            status="finished",
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="cccccccccccccccc:dddddddddddddddd",
            start_time="2026-05-12T12:20:00Z",
            duration_ms=0,
            status="running",
            query_state="running",
            query_type="QUERY",
            statement="SELECT 2",
        ),
    ]

    def fake_fetch_impala_query_summaries(**kwargs):
        return type("Result", (), {"summaries": summaries, "warnings": []})()

    monkeypatch.setattr(module, "fetch_impala_query_summaries", fake_fetch_impala_query_summaries)
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--from-time",
        "2026-05-12T10:00:00Z",
        "--to-time",
        "2026-05-12T11:00:00Z",
        "--no-min-duration-filter",
        "--only-running",
    )

    discovery = module.discover_candidates(config, env={})

    assert discovery.summaries_inspected == 1
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == ["cccccccccccccccc:dddddddddddddddd"]


def test_batch_recent_owner_raw_direct_impala_filters_to_owner_user(monkeypatch, tmp_path):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            end_time="2026-05-12T10:15:00Z",
            duration_ms=120000,
            status="finished",
            user="analyst_one",
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="cccccccccccccccc:dddddddddddddddd",
            end_time="2026-05-12T10:20:00Z",
            duration_ms=180000,
            status="finished",
            user="other_user",
            query_type="QUERY",
            statement="SELECT 2",
        ),
    ]

    def fake_fetch_impala_query_summaries(**_kwargs):
        return type("Result", (), {"summaries": summaries, "warnings": []})()

    monkeypatch.setattr(module, "fetch_impala_query_summaries", fake_fetch_impala_query_summaries)
    args = module.parse_args(
        direct_impala_owner_raw_args(
            tmp_path,
            "--from-time",
            "2026-05-12T10:00:00Z",
            "--to-time",
            "2026-05-12T11:00:00Z",
        )
    )
    config = module.build_batch_config(
        args,
        env={"KRB5_PRINCIPAL": "analyst_one@EXAMPLE.COM"},
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )

    discovery = module.discover_candidates(config, env={})

    assert config.user == "analyst_one"
    assert config.source_owner_user == "analyst_one"
    assert discovery.summaries_inspected == 1
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == ["aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"]


def test_batch_recent_owner_raw_direct_impala_filters_to_collectable_owner_users(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            end_time="2026-05-12T10:15:00Z",
            duration_ms=120000,
            status="finished",
            user="analyst_one",
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="cccccccccccccccc:dddddddddddddddd",
            end_time="2026-05-12T10:20:00Z",
            duration_ms=180000,
            status="finished",
            user="report_user",
            query_type="QUERY",
            statement="SELECT 2",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="eeeeeeeeeeeeeeee:ffffffffffffffff",
            end_time="2026-05-12T10:25:00Z",
            duration_ms=240000,
            status="finished",
            user="other_user",
            query_type="QUERY",
            statement="SELECT 3",
        ),
    ]

    def fake_fetch_impala_query_summaries(**_kwargs):
        return type("Result", (), {"summaries": summaries, "warnings": []})()

    monkeypatch.setattr(module, "fetch_impala_query_summaries", fake_fetch_impala_query_summaries)
    args = module.parse_args(
        direct_impala_owner_raw_args(
            tmp_path,
            "--from-time",
            "2026-05-12T10:00:00Z",
            "--to-time",
            "2026-05-12T11:00:00Z",
            "--source-owner-user",
            "report_user",
            "--source-owner-user",
            "analyst_one",
        )
    )
    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)

    discovery = module.discover_candidates(config, env={})

    assert config.user is None
    assert config.source_owner_user == "report_user"
    assert config.collectable_owner_users == ("analyst_one", "report_user")
    assert discovery.summaries_inspected == 2
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == [
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
        "cccccccccccccccc:dddddddddddddddd",
    ]


def test_batch_recent_owner_raw_cm_adds_owner_user_filter(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        cm_owner_raw_args(
            tmp_path,
            "--source-owner-user",
            "analyst_one",
        )
    )

    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)
    filters = module.build_recent_filters(config)
    _path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)

    assert config.query_profile_source == "cm"
    assert config.user == "analyst_one"
    assert config.source_owner_user == "analyst_one"
    assert config.collectable_owner_users == ("analyst_one",)
    assert params["filter"] == 'user = "analyst_one" AND executing = false'


def test_batch_recent_owner_raw_cm_filters_collectable_owners_client_side(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
            duration_ms=120000,
            status="finished",
            user="analyst_one",
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="cccccccccccccccc:dddddddddddddddd",
            duration_ms=180000,
            status="finished",
            user="report_user",
            query_type="QUERY",
            statement="SELECT 2",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="eeeeeeeeeeeeeeee:ffffffffffffffff",
            duration_ms=240000,
            status="finished",
            user="other_user",
            query_type="QUERY",
            statement="SELECT 3",
        ),
    ]

    def fake_fetch_page(_client, filters, page_token):
        _path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)
        calls.append((params, page_token))
        return module.cm_profiles.CMQueryPage(items=summaries)

    monkeypatch.setattr(module, "make_cm_http_client", lambda config, env: object())
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    args = module.parse_args(
        cm_owner_raw_args(
            tmp_path,
            "--source-owner-user",
            "report_user",
            "--source-owner-user",
            "analyst_one",
        )
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    discovery = module.discover_candidates(config, env=auth_env())

    assert config.user is None
    assert config.collectable_owner_users == ("analyst_one", "report_user")
    assert calls[0][0]["filter"] == "executing = false"
    assert discovery.summaries_inspected == 2
    assert [
        candidate.summary.query_id for candidate in discovery.candidates if candidate.selected
    ] == [
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
        "cccccccccccccccc:dddddddddddddddd",
    ]


def test_batch_recent_config_cluster_loads_owner_raw_source_visibility(tmp_path):
    module = load_batch_module()
    config_path = write_query_doctor_config(
        tmp_path,
        source_visibility="safe",
        clusters=[
            {
                "id": "cm",
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "prod_cluster",
                "service": "impala",
            },
            {
                "id": "direct-impala",
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
                "metadata_kerberos_service_name": "hive",
                "metadata_kerberos_host_fqdn": "impala-lb.example.com",
                "source_visibility": "owner_raw",
                "source_owner_user": "analyst_one",
            },
        ],
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--config-cluster",
            "direct-impala",
            "--metadata-mode",
            "off",
        ]
    )

    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)

    assert config.query_profile_source == "impala"
    assert config.impala_profile_hosts == ("impalad-1.example.com",)
    assert config.metadata_kerberos_service_name == "hive"
    assert config.metadata_kerberos_host_fqdn == "impala-lb.example.com"
    assert config.source_visibility == "owner_raw"
    assert config.source_owner_user == "analyst_one"
    assert config.user == "analyst_one"


def test_batch_recent_single_config_cluster_is_used_by_default(tmp_path):
    module = load_batch_module()
    config_path = write_query_doctor_config(
        tmp_path,
        clusters=[
            {
                "id": "cm",
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "prod_cluster",
                "service": "impala",
            },
        ],
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--metadata-mode",
            "off",
        ]
    )

    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.cm_url == "https://cm.example.com:7183/"
    assert config.cluster == "prod_cluster"
    assert config.service == "impala"


def test_batch_recent_active_cluster_key_selects_default_config_cluster(tmp_path):
    module = load_batch_module()
    config_path = write_query_doctor_config(
        tmp_path,
        active_cluster_key="direct-impala",
        clusters=[
            {
                "id": "cm",
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "prod_cluster",
                "service": "impala",
            },
            {
                "id": "direct-impala",
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
            },
        ],
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--metadata-mode",
            "off",
        ]
    )

    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)

    assert config.query_profile_source == "impala"
    assert config.impala_profile_hosts == ("impalad-1.example.com",)
    assert config.cm_url is None


def test_batch_recent_multi_cluster_config_requires_selection(tmp_path):
    module = load_batch_module()
    config_path = write_query_doctor_config(
        tmp_path,
        clusters=[
            {
                "id": "cm-a",
                "cm_url": "https://cm-a.example.com:7183/",
                "cluster": "cluster_a",
                "service": "impala",
            },
            {
                "id": "cm-b",
                "cm_url": "https://cm-b.example.com:7183/",
                "cluster": "cluster_b",
                "service": "impala",
            },
        ],
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--metadata-mode",
            "off",
        ]
    )

    with pytest.raises(ValueError, match="pass --config-cluster or set active_cluster_key"):
        module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)


def test_batch_recent_config_cluster_can_be_overridden_by_cli_owner_flags(tmp_path):
    module = load_batch_module()
    config_path = write_query_doctor_config(
        tmp_path,
        clusters=[
            {
                "id": "direct-impala",
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
                "source_visibility": "owner_raw",
                "source_owner_user": "config_owner",
            }
        ],
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--config-cluster",
            "direct-impala",
            "--source-owner-user",
            "cli_owner",
            "--metadata-mode",
            "off",
        ]
    )

    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)

    assert config.source_visibility == "owner_raw"
    assert config.source_owner_user == "cli_owner"
    assert config.user == "cli_owner"


def test_batch_recent_config_cluster_rejects_unknown_cluster_id(tmp_path):
    module = load_batch_module()
    config_path = write_query_doctor_config(
        tmp_path,
        clusters=[
            {
                "id": "direct-impala",
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
            }
        ],
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--config-cluster",
            "missing",
            "--metadata-mode",
            "off",
        ]
    )

    with pytest.raises(ValueError, match="--config-cluster 'missing' was not found"):
        module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)


def test_batch_recent_owner_raw_fails_closed_for_service_principal(tmp_path):
    module = load_batch_module()
    args = module.parse_args(direct_impala_owner_raw_config_args(tmp_path))

    with pytest.raises(ValueError, match="requires at least one collectable source_owner_user"):
        module.build_batch_config(
            args,
            env={"KRB5_PRINCIPAL": "query-doctor/host.example.com@EXAMPLE.COM"},
            cwd=tmp_path,
            repo_root=REPO_DIR,
        )


def test_batch_recent_owner_raw_fails_closed_for_explicit_service_principal(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        direct_impala_owner_raw_config_args(
            tmp_path,
            "--source-owner-user",
            "impala/host.example.com@EXAMPLE.COM",
        )
    )

    with pytest.raises(ValueError, match="requires at least one collectable source_owner_user"):
        module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)


def test_batch_recent_owner_raw_rejects_conflicting_user_filter(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        direct_impala_owner_raw_config_args(
            tmp_path,
            "--source-owner-user",
            "analyst_one",
            "--user",
            "other_user",
        )
    )

    with pytest.raises(ValueError, match="requires recent_user to match a collectable"):
        module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)


def test_batch_recent_direct_impala_profile_collection_uses_impala_collector(monkeypatch, tmp_path):
    module = load_batch_module()
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--prometheus-url",
        "http://prometheus.example.com:9090",
        "--prometheus-metrics-profile",
        "ambari-hadoop",
        "--prometheus-step-sec",
        "45",
        "--prometheus-timeseries-padding-sec",
        "180",
        "--prometheus-timeout-sec",
        "20",
        "--impala-profile-prefer-json",
        "--impala-profile-collect-docs",
        "--impala-collect-admission-context",
    )
    case = direct_impala_collection_case(module, tmp_path)
    calls = []

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        write_case(out / "aaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbb", healthy_facts())
        return completed()

    monkeypatch.setattr(module.batch_case_processing, "run_subprocess", fake_run)

    module.collect_case_profile(config, case, env={}, repo_root=REPO_DIR)

    assert case.collection_status == "ok"
    assert case.actual_case_dir == case.wrapper_dir / "aaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbb"
    assert command_uses_role(calls[0], "collect_impala_profile")
    assert "--cm-url" not in calls[0]
    assert calls[0][calls[0].index("--host") + 1] == "impalad-1.example.com"
    assert "--prefer-json-profile" in calls[0]
    assert "--collect-profile-docs" in calls[0]
    assert "--collect-admission-context" in calls[0]
    assert calls[0][calls[0].index("--prometheus-url") + 1] == "http://prometheus.example.com:9090"
    assert "--collect-prometheus-timeseries" in calls[0]
    assert calls[0][calls[0].index("--prometheus-metrics-profile") + 1] == "ambari-hadoop"
    assert calls[0][calls[0].index("--prometheus-step-sec") + 1] == "45"
    assert calls[0][calls[0].index("--prometheus-timeseries-padding-sec") + 1] == "180"
    assert calls[0][calls[0].index("--prometheus-timeout-sec") + 1] == "20"
    assert "--metadata-source-tables-out" not in calls[0]


def test_batch_recent_direct_impala_profile_collection_reads_metadata_source_tables(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    config = build_direct_impala_config(
        module,
        tmp_path,
        "--metadata-coordinator",
        "impala.example.net:21000",
        "--metadata-top-limit",
        "1",
        metadata_mode="on",
    )
    case = direct_impala_collection_case(
        module,
        tmp_path,
        metadata_source_tables=("existing.table",),
    )
    calls = []

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        source_tables_out = Path(cmd[cmd.index("--metadata-source-tables-out") + 1])
        assert source_tables_out.parent == out
        source_tables_out.write_text(
            json.dumps(["example_warehouse.real_table"]) + "\n",
            encoding="utf-8",
        )
        write_case(out / "aaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbb", healthy_facts())
        return completed()

    monkeypatch.setattr(module.batch_case_processing, "run_subprocess", fake_run)

    module.collect_case_profile(config, case, env={}, repo_root=REPO_DIR)

    collect_cmd = calls[0]
    assert case.collection_status == "ok"
    assert command_uses_role(collect_cmd, "collect_impala_profile")
    assert "--metadata-source-tables-out" in collect_cmd
    assert case.metadata_source_tables == (
        "existing.table",
        "example_warehouse.real_table",
    )


def test_batch_recent_cm_profile_collection_keeps_selected_cluster_with_config_path(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    config_path = tmp_path / "query-doctor-config.json"
    config_path.write_text(
        json.dumps(
            {
                "clusters": [
                    {
                        "id": "cm-prod",
                        "cm_url": "https://cm-prod.example.net:7183",
                        "cluster": "prod_cluster",
                        "service": "impala-prod",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--cm-url",
            "https://selected-cm.example.net:7183",
            "--cluster",
            "selected_cluster",
            "--service",
            "selected_impala",
            "--out",
            str(batch_dir(tmp_path)),
            "--cm-inspect-limit",
            "5",
            "--select-limit",
            "2",
            "--metadata-mode",
            "off",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)
    case = module.CaseResult(
        index=1,
        query_id="aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
        duration_sec=120.0,
        user=None,
        pool=None,
        query_type="QUERY",
        sql_verb="SELECT",
        wrapper_dir=batch_dir(tmp_path) / "cases" / "case-001",
    )
    calls = []

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        write_case(out / "aaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbb", healthy_facts())
        return completed()

    monkeypatch.setattr(module.batch_case_processing, "run_subprocess", fake_run)

    module.collect_case_profile(config, case, env=auth_env(), repo_root=REPO_DIR)

    collect_cmd = calls[0]
    assert case.collection_status == "ok"
    assert command_uses_role(collect_cmd, "collect_cm")
    assert collect_cmd[collect_cmd.index("--config") + 1] == str(config_path)
    assert collect_cmd[collect_cmd.index("--cm-url") + 1] == "https://selected-cm.example.net:7183"
    assert collect_cmd[collect_cmd.index("--cluster") + 1] == "selected_cluster"
    assert collect_cmd[collect_cmd.index("--service") + 1] == "selected_impala"


def base_args(tmp_path: Path) -> list[str]:
    return [
        "--out",
        str(batch_dir(tmp_path)),
        "--cm-url",
        "https://cm.example.net:7183",
        "--cluster",
        "cluster",
        "--service",
        "impala",
        "--cm-inspect-limit",
        "5",
        "--select-limit",
        "2",
    ]


def batch_dir(tmp_path: Path) -> Path:
    return tmp_path / "query-doctor-batch"


def write_query_doctor_config(tmp_path: Path, **config: object) -> Path:
    config_path = tmp_path / "query-doctor-config.json"
    config_path.write_text(
        json.dumps({"out": str(batch_dir(tmp_path)), **config}),
        encoding="utf-8",
    )
    return config_path


def direct_impala_args(
    tmp_path: Path,
    *extra: str,
    metadata_mode: str = "off",
) -> list[str]:
    return [
        "--query-profile-source",
        "impala",
        "--impala-profile-host",
        "impalad-1.example.com",
        "--out",
        str(batch_dir(tmp_path)),
        "--cm-inspect-limit",
        "5",
        "--select-limit",
        "2",
        "--metadata-mode",
        metadata_mode,
        *extra,
    ]


def build_batch_config_from_args(module, args: list[str], tmp_path: Path, *, env=None, cwd=None):
    return module.build_batch_config(
        module.parse_args(args),
        env={} if env is None else env,
        cwd=tmp_path if cwd is None else cwd,
        repo_root=REPO_DIR,
    )


def build_direct_impala_config(
    module,
    tmp_path: Path,
    *extra: str,
    metadata_mode: str = "off",
    env=None,
):
    return build_batch_config_from_args(
        module,
        direct_impala_args(tmp_path, *extra, metadata_mode=metadata_mode),
        tmp_path,
        env=env,
    )


def build_cm_query_config(module, tmp_path: Path, *extra: str, env=None, cwd=REPO_DIR):
    return build_batch_config_from_args(
        module,
        [
            "--out",
            str(batch_dir(tmp_path)),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            *extra,
        ],
        tmp_path,
        env=auth_env() if env is None else env,
        cwd=cwd,
    )


def build_cm_config(module, tmp_path: Path, *extra: str, env=None, cwd=REPO_DIR):
    return build_batch_config_from_args(
        module,
        [*base_args(tmp_path), *extra],
        tmp_path,
        env=auth_env() if env is None else env,
        cwd=cwd,
    )


def direct_impala_owner_raw_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--query-profile-source",
        "impala",
        "--impala-profile-host",
        "impalad-1.example.com",
        "--out",
        str(batch_dir(tmp_path)),
        "--select-limit",
        "5",
        "--metadata-mode",
        "off",
        "--no-min-duration-filter",
        "--source-visibility",
        "owner_raw",
        *extra,
    ]


def direct_impala_owner_raw_config_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--query-profile-source",
        "impala",
        "--impala-profile-host",
        "impalad-1.example.com",
        "--out",
        str(batch_dir(tmp_path)),
        "--metadata-mode",
        "off",
        "--source-visibility",
        "owner_raw",
        *extra,
    ]


def cm_owner_raw_args(tmp_path: Path, *extra: str) -> list[str]:
    return base_args(tmp_path) + [
        "--metadata-mode",
        "off",
        "--no-min-duration-filter",
        "--source-visibility",
        "owner_raw",
        *extra,
    ]


def auth_env() -> dict[str, str]:
    return {"CM_PASSWORD": "secret", "CM_USERNAME": "user"}


def direct_impala_collection_case(module, tmp_path: Path, **overrides):
    values = {
        "index": 1,
        "query_id": "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
        "duration_sec": 120.0,
        "user": None,
        "pool": None,
        "query_type": "QUERY",
        "sql_verb": "SELECT",
        "wrapper_dir": batch_dir(tmp_path) / "cases" / "case-001",
    }
    values.update(overrides)
    return module.CaseResult(**values)


def candidate(
    module, query_id: str, duration_ms: int, *, selected: bool = True, statement: str = "SELECT 1"
):
    return module.cm_profiles.RecentQueryCandidate(
        summary=module.cm_profiles.CMQuerySummary(
            query_id=query_id,
            duration_ms=duration_ms,
            query_type="QUERY",
            statement=statement,
            user="analyst",
            pool="root.analytics",
        ),
        selected=selected,
        reason="selected: SELECT-like user query" if selected else "excluded",
        sql_verb="SELECT",
    )


def completed(returncode: int = 0, *, stdout=None, stderr=None):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def allow_metadata_auth_preflight(monkeypatch):
    from query_doctor.impala.kerberos_preflight import KerberosTicketCheck
    from query_doctor.recent import batch_config

    monkeypatch.setattr(
        batch_config,
        "check_kerberos_ticket_cache",
        lambda env: KerberosTicketCheck(True),
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def read_collector_summary_progress(path: Path) -> dict:
    events = [
        event
        for event in read_jsonl(path)
        if event.get("stage") == "recent_history_collector_summary"
    ]
    assert len(events) == 1
    return events[0]


def collector_summary_progress_from_payload(payload: dict) -> dict:
    return {
        "stage": "recent_history_collector_summary",
        "summary_kind": payload["summary_kind"],
        "status": payload["status"],
        "observed_at_iso": payload["observed_at_iso"],
        "discover_only": payload["discover_only"],
        "history_backend": payload["history_backend"],
        "summaries_inspected": payload["summaries_inspected"],
        "candidates_discovered": payload["candidates_discovered"],
        "selected_count": payload["selected_count"],
        "summaries_recorded": payload["summaries_recorded"],
        "profile_jobs_planned": payload["profile_jobs_planned"],
        "query_log_at_capacity": payload["query_log_at_capacity"],
        "query_log_continuity_status": payload["query_log_continuity_status"],
        "issue_codes": payload["issue_codes"],
        "raw_output": payload["raw_output"],
        "sensitive_value_echo": payload["sensitive_value_echo"],
    }


def read_batch_summary(tmp_path: Path) -> dict:
    return json.loads((batch_dir(tmp_path) / "batch_summary.json").read_text())


def read_batch_summary_markdown(tmp_path: Path) -> str:
    return (batch_dir(tmp_path) / "batch_summary.md").read_text(encoding="utf-8")


def patch_discovered_candidates(
    module,
    monkeypatch,
    selected,
    *,
    query_log_at_capacity=False,
    query_log_oldest_completed_at_iso=None,
):
    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(
            selected,
            [],
            "client-side",
            None,
            query_log_at_capacity=query_log_at_capacity,
            query_log_oldest_completed_at_iso=query_log_oldest_completed_at_iso,
        ),
    )


def command_query_id(cmd):
    return cmd[cmd.index("--query-id") + 1]


def assert_non_negative_number(value):
    assert isinstance(value, (int, float))
    assert value >= 0


def write_case(case_dir: Path, facts: str) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "profile_digest.md").write_text("# digest\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(facts, encoding="utf-8")


def write_collected_case_from_command(cmd, facts=None) -> None:
    query_id = command_query_id(cmd)
    out = Path(cmd[cmd.index("--out") + 1])
    write_case(out / query_id.replace(":", "_"), healthy_facts() if facts is None else facts)


def case_result(
    module,
    *,
    index: int,
    query_id: str,
    score: int,
    duration_sec=None,
    cardinality=None,
    memory=None,
    zero_row_gaps=None,
    zero_memory_gaps=None,
    backend_data_skew=False,
    host_tail=None,
    execution_tail=None,
):
    return module.CaseResult(
        index=index,
        query_id=query_id,
        duration_sec=duration_sec,
        user=None,
        pool=None,
        query_type="QUERY",
        sql_verb="SELECT",
        wrapper_dir=Path(f"/tmp/query-doctor-test/case-{index:03d}"),
        score=score,
        cardinality_anomaly_count=cardinality,
        memory_anomaly_count=memory,
        zero_row_estimate_gap_count=zero_row_gaps,
        zero_memory_estimate_gap_count=zero_memory_gaps,
        backend_data_skew=backend_data_skew,
        host_tail_candidate_count=host_tail,
        execution_tail_candidate_count=execution_tail,
    )


def batch_summary_test_config(module, tmp_path: Path, **overrides):
    values = {
        "out": batch_dir(tmp_path),
        "cm_url": "https://cm.example.net:7183",
        "cluster": "cluster",
        "service": "impala",
        "cm_username": None,
        "ca_bundle": None,
        "verify_tls": True,
        "recent_window_minutes": 60,
        "cm_inspect_limit": 5,
        "triage_profile_limit": 4,
        "metadata_top_limit": 0,
        "min_duration_sec": None,
        "max_duration_sec": None,
        "order": "duration-desc",
        "include_failed": False,
        "include_running": False,
        "user": None,
        "pool": None,
        "query_type": None,
        "max_profile_bytes": 1000,
        "collect_cm_events": False,
        "cm_events_max_events": 50,
        "collect_cm_timeseries": False,
        "cm_metrics_profile": "auto",
        "cm_timeseries_top_limit": 0,
        "cm_timeseries_padding_sec": 0,
        "max_timeseries_bytes": 1000,
        "max_timeseries_points": 100,
        "metadata_mode": "off",
        "metadata_coordinator": None,
        "metadata_auth": "kerberos",
        "metadata_protocol": "hs2",
        "metadata_kerberos_service_name": None,
        "metadata_ssl": False,
        "metadata_ca_cert": None,
        "metadata_timeout_sec": 30,
        "metadata_max_tables": None,
        "metadata_max_output_bytes": None,
        "metadata_redact": True,
        "top_reports": 0,
        "jobs": 1,
        "cm_jobs": 1,
        "metadata_jobs": 1,
        "allow_high_jobs": False,
        "discover_only": False,
        "overwrite": False,
        "config_path": None,
        "progress_jsonl": None,
        "krb5ccname": None,
        "from_time": None,
        "to_time": None,
        "only_running": False,
    }
    values.update(overrides)
    return module.BatchConfig(**values)


def build_summary_for_test_cases(
    module,
    config,
    cases,
    *,
    discovery=None,
    warnings=(),
    summaries_inspected: int = 1,
    discovery_seconds: float = 1.2,
    total_seconds: float = 3.4,
):
    if discovery is None:
        discovery = module.DiscoveryResult(
            [], [], "client-side", None, summaries_inspected=summaries_inspected
        )
    return module.build_summary(
        config,
        discovery,
        cases,
        list(warnings),
        discovery_seconds=discovery_seconds,
        total_seconds=total_seconds,
    )


def write_batch_summary_test_outputs(module, tmp_path: Path, summary: dict) -> None:
    batch_dir(tmp_path).mkdir()
    module.write_batch_outputs(batch_dir(tmp_path), summary)


def optimizer_support(**overrides):
    from query_doctor.recent.optimizer_rewrite_support import OptimizerRewriteSupport

    return OptimizerRewriteSupport(**overrides)


def safe_material_draft_support(**overrides):
    values = {
        "status": "sql_draft_supported",
        "label": "SQL draft eligible",
        "reason": "Python-owned recipe is available",
        "risk_mode": "standard",
        "risk_reasons": (),
        "rewriteability_bucket": "safe_material_draft",
        "rewriteability_label": "Safe material draft",
    }
    values.update(overrides)
    return optimizer_support(**values)


def adjacent_shape_support(**overrides):
    values = {
        "status": "guidance_only",
        "label": "Guidance only",
        "reason": "No Python-owned recipe is available",
        "risk_mode": "standard",
        "risk_reasons": (),
        "draft_eligibility": "no_recipe",
        "rewriteability_bucket": "recipe_adjacent_shape",
        "rewriteability_label": "Recipe-adjacent shape",
    }
    values.update(overrides)
    return optimizer_support(**values)


def no_draft_recipe_support(**overrides):
    values = {
        "status": "draft_disabled",
        "label": "Recipe detected; draft unavailable",
        "reason": "Deterministic draft unavailable",
        "risk_mode": "standard",
        "risk_reasons": (),
        "draft_eligibility": "deterministic_draft_unavailable",
        "rewriteability_bucket": "recipe_detected_no_draft",
        "rewriteability_label": "Recipe detected, no draft",
    }
    values.update(overrides)
    return optimizer_support(**values)


def human_review_support(**overrides):
    values = {
        "status": "guidance_only",
        "label": "Guidance only",
        "reason": "SQL shape exceeds current safe draft thresholds",
        "risk_mode": "recommendations_only",
        "risk_reasons": (),
        "draft_eligibility": "disabled_by_safety_thresholds",
        "rewriteability_bucket": "human_review_only",
        "rewriteability_label": "Human review only",
    }
    values.update(overrides)
    return optimizer_support(**values)


def guidance_human_review_support(**overrides):
    values = {
        "status": "guidance_only",
        "label": "Guidance only",
        "reason": "No Python-owned recipe is available",
        "risk_mode": "standard",
        "risk_reasons": (),
        "rewriteability_bucket": "human_review_only",
        "rewriteability_label": "Human review only",
    }
    values.update(overrides)
    return optimizer_support(**values)


def stats_likely_support(**overrides):
    values = {
        "status": "not_candidate",
        "label": "Not an optimization candidate",
        "reason": "Stats bottleneck is primary",
        "risk_mode": "unknown",
        "risk_reasons": (),
        "rewriteability_bucket": "stats_likely",
        "rewriteability_label": "Stats likely",
    }
    values.update(overrides)
    return optimizer_support(**values)


def no_recipe_not_rewriteable_support(**overrides):
    values = {
        "status": "guidance_only",
        "label": "Guidance only",
        "reason": "No Python-owned recipe is available",
        "risk_mode": "standard",
        "risk_reasons": (),
        "draft_eligibility": "no_recipe",
        "rewriteability_bucket": "not_rewriteable",
        "rewriteability_label": "Not rewriteable",
    }
    values.update(overrides)
    return optimizer_support(**values)


def case_with_optimizer_support(module, *, index: int, query_id: str, support, score: int = 1):
    case = case_result(module, index=index, query_id=query_id, score=score)
    case.optimizer_rewrite_support = support
    return case


def suspicious_facts() -> str:
    return "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 2",
            "- Cardinality anomalies: 1",
            "- Memory anomalies: 1",
            "",
            "## Referenced Tables",
            "- `db.table_a`",
            "",
            "## Table Metadata Context",
            "- SHOW TABLE STATS status: ok",
            "- table stats row-count completeness: available",
            "- column stats completeness: incomplete/unknown",
            "",
            "### Cardinality estimate errors [medium]",
            "- operator 01 underestimated rows",
            "",
            "### Memory estimate errors [medium]",
            "- operator 02 underestimated memory",
            "",
        ]
    )


def promotable_suspicious_facts() -> str:
    return "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 4",
            "- Memory anomalies: 1",
            "- Zero/unknown row estimate gaps: 2",
            "- Zero/unknown memory estimate gaps: 1",
            "",
            "## Referenced Tables",
            "- `db.table_a`",
            "",
            "### Spill or scratch I/O [medium]",
            "- Detected non-zero spill/scratch metric evidence in digest lines.",
            "",
        ]
    )


def promotable_suspicious_bad_metadata_facts() -> str:
    return promotable_suspicious_facts() + "\n".join(
        [
            "",
            "## Table Metadata Context",
            "- SHOW TABLE STATS status: error",
            "- table stats row-count completeness: missing/unknown",
            "- column stats completeness: incomplete/unknown",
            "- SHOW COLUMN STATS status: too_large",
            "",
        ]
    )


def healthy_facts() -> str:
    return "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 1",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Referenced Tables",
            "- not_observed: no referenced table names were parsed from SQL inputs or profile digest.",
            "",
            "## Table Metadata Context",
            "- context file: not_observed",
            "",
        ]
    )


def view_metadata_facts() -> str:
    return "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 1",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Referenced Tables",
            "- `db.view_a`",
            "",
            "## Table Metadata Context",
            "- context file: present",
            "- table metadata facts: supported",
            "- tables requested: 1",
            "- read-only statements only: yes",
            "",
            "### Table: db.view_a",
            "",
            "- object type: view",
            "- SHOW CREATE TABLE status: ok",
            "- SHOW TABLE STATS status: not_applicable",
            "- SHOW COLUMN STATS status: not_applicable",
            "- table stats rows: unknown",
            "- table stats row-count completeness: not_available",
            "- table stats size: unknown",
            "- column stats columns observed: 0",
            "- column stats missing/unknown markers: 0",
            "- column stats completeness: not_available",
            "",
        ]
    )


def test_batch_recent_help_works(capsys):
    module = load_batch_module()

    with pytest.raises(SystemExit) as exc_info:
        module.parse_args(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--cm-inspect-limit" in output
    assert "--stop" not in output
    assert "--top-reports" in output
    assert "--jobs" in output
    assert "--cm-jobs" in output
    assert "--metadata-jobs" in output
    assert "--allow-high-jobs" in output
    assert "--overwrite" in output


def test_jobs_default_is_one(tmp_path):
    module = load_batch_module()
    config = build_cm_config(module, tmp_path)

    assert config.jobs == 1
    assert config.cm_jobs == 1
    assert config.metadata_jobs == 5


def test_metadata_jobs_default_and_hard_cap_contract(tmp_path, capsys):
    module = load_batch_module()
    config = build_cm_config(module, tmp_path)

    assert module.MAX_METADATA_JOBS == 5
    assert config.metadata_jobs == module.MAX_METADATA_JOBS

    result = module.main(
        base_args(tmp_path) + ["--metadata-jobs", str(module.MAX_METADATA_JOBS + 1)], env=auth_env()
    )

    assert result == 2
    assert f"--metadata-jobs must be <= {module.MAX_METADATA_JOBS}" in capsys.readouterr().err


def test_batch_config_values_override_internal_defaults(tmp_path):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_out = tmp_path / "query-doctor-config-batch"
    config_path.write_text(
        json.dumps(
            {
                "cm_url": "https://config-cm.example.net:7183",
                "cm_user": "config_user",
                "cluster": "config_cluster",
                "service": "config_impala",
                "out": str(config_out),
                "ca_bundle": "/tmp/config-ca.pem",
                "insecure_skip_verify": True,
                "recent_window_minutes": 120,
                "recent_cm_jobs": 12,
                "recent_cm_summary_limit": 333,
                "recent_profile_analysis_limit": 44,
                "recent_metadata_jobs": 3,
                "recent_metadata_top_limit": 6,
                "recent_min_duration_sec": 7.5,
                "recent_max_duration_sec": 99.0,
                "recent_order": "recent",
                "recent_include_failed": True,
                "recent_include_running": True,
                "recent_user": "impala_config_user",
                "recent_pool": "root.config",
                "query_type": "QUERY",
                "max_profile_bytes": 123456,
                "collect_cm_timeseries": True,
                "cm_metrics_profile": "cm6.2.1",
                "recent_cm_timeseries_top_limit": 7,
                "cm_timeseries_padding_sec": 180,
                "max_timeseries_bytes": 3145728,
                "max_timeseries_points": 4000,
                "metadata_coordinator": "impala-config.example.net:21000",
                "metadata_auth": "kerberos",
                "metadata_protocol": "hs2",
                "metadata_kerberos_service_name": "hive",
                "metadata_kerberos_host_fqdn": "impala-lb.example.net",
                "metadata_ssl": True,
                "metadata_ca_cert": "/tmp/impala-ca.pem",
                "metadata_timeout_sec": 55,
                "metadata_max_tables": 9,
                "metadata_max_output_bytes": 7777,
                "metadata_redact": True,
                "krb5ccname": "FILE:/tmp/krb5cc_config_batch",
                "recent_collect_workload_history": True,
                "recent_workload_history_path": "history/workload.jsonl",
                "recent_workload_history_max_bytes": 4096,
                "recent_history_db": "history/recent.sqlite",
                "recent_history_summary_retention_days": 30,
                "recent_history_profile_job_retention_days": 14,
                "recent_history_analysis_cache_retention_days": 45,
                "recent_history_profile_artifact_retention_days": 60,
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(["--config", str(config_path)])
    config = module.build_batch_config(
        args, env={"CM_PASSWORD": "secret"}, cwd=tmp_path, repo_root=REPO_DIR
    )

    assert config.out == config_out
    assert config.cm_url == "https://config-cm.example.net:7183"
    assert config.cm_username == "config_user"
    assert config.cluster == "config_cluster"
    assert config.service == "config_impala"
    assert config.ca_bundle == "/tmp/config-ca.pem"
    assert config.verify_tls is False
    assert config.recent_window_minutes == 120
    assert config.cm_jobs == 12
    assert config.cm_inspect_limit == 333
    assert config.triage_profile_limit == 44
    assert config.metadata_jobs == 3
    assert config.metadata_top_limit == 6
    assert config.min_duration_sec == 7.5
    assert config.max_duration_sec == 99.0
    assert config.order == "recent"
    assert config.include_failed is True
    assert config.include_running is True
    assert config.user == "impala_config_user"
    assert config.pool == "root.config"
    assert config.query_type == "QUERY"
    assert config.max_profile_bytes == 123456
    assert config.collect_cm_timeseries is True
    assert config.cm_metrics_profile == "cm6"
    assert config.cm_timeseries_top_limit == 7
    assert config.cm_timeseries_padding_sec == 180
    assert config.max_timeseries_bytes == 3145728
    assert config.max_timeseries_points == 4000
    assert config.metadata_coordinator == "impala-config.example.net:21000"
    assert config.metadata_protocol == "hs2"
    assert config.metadata_kerberos_service_name == "hive"
    assert config.metadata_kerberos_host_fqdn == "impala-lb.example.net"
    assert config.metadata_ssl is True
    assert config.metadata_ca_cert == "/tmp/impala-ca.pem"
    assert config.metadata_timeout_sec == 55
    assert config.metadata_max_tables == 9
    assert config.metadata_max_output_bytes == 7777
    assert config.metadata_redact is True
    assert config.krb5ccname == "FILE:/tmp/krb5cc_config_batch"
    assert config.collect_workload_history is True
    assert config.workload_history_path == (tmp_path / "history" / "workload.jsonl").resolve()
    assert config.workload_history_max_bytes == 4096
    assert config.recent_history_backend == "sqlite"
    assert config.recent_history_db == (tmp_path / "history" / "recent.sqlite").resolve()
    assert config.recent_history_summary_retention_days == 30
    assert config.recent_history_profile_job_retention_days == 14
    assert config.recent_history_analysis_cache_retention_days == 45
    assert config.recent_history_profile_artifact_retention_days == 60


def test_batch_cli_accepts_workload_history_options(tmp_path):
    module = load_batch_module()
    history_path = tmp_path / "history" / "workload.jsonl"

    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--collect-workload-history",
            "--workload-history-path",
            str(history_path),
            "--workload-history-max-bytes",
            "8192",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.collect_workload_history is True
    assert config.workload_history_path == history_path
    assert config.workload_history_max_bytes == 8192


def test_batch_cli_accepts_recent_history_db_option(tmp_path):
    module = load_batch_module()
    history_db = tmp_path / "history" / "recent.sqlite"

    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--recent-history-db",
            str(history_db),
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.recent_history_backend == "sqlite"
    assert config.recent_history_db == history_db


def test_batch_cli_accepts_postgres_recent_history_backend(tmp_path):
    module = load_batch_module()

    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--recent-history-backend",
            "postgres",
            "--recent-history-postgres-dsn-env",
            "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.recent_history_backend == "postgres"
    assert config.recent_history_db is None
    assert config.recent_history_postgres_dsn_env == "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN"


def test_batch_cli_accepts_recent_history_retention_options(tmp_path):
    module = load_batch_module()
    history_db = tmp_path / "history" / "recent.sqlite"

    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--recent-history-db",
            str(history_db),
            "--recent-history-summary-retention-days",
            "30",
            "--recent-history-profile-job-retention-days",
            "14",
            "--recent-history-analysis-cache-retention-days",
            "45",
            "--recent-history-profile-artifact-retention-days",
            "60",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.recent_history_summary_retention_days == 30
    assert config.recent_history_profile_job_retention_days == 14
    assert config.recent_history_analysis_cache_retention_days == 45
    assert config.recent_history_profile_artifact_retention_days == 60


def test_batch_cli_rejects_recent_history_retention_without_backend(tmp_path):
    module = load_batch_module()

    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--recent-history-summary-retention-days",
            "30",
        ]
    )

    with pytest.raises(
        ValueError, match="recent history retention requires recent_history_backend"
    ):
        module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)


def test_batch_cli_rejects_invalid_postgres_recent_history_dsn_env(tmp_path):
    module = load_batch_module()

    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--recent-history-backend",
            "postgres",
            "--recent-history-postgres-dsn-env",
            "postgres-dsn",
        ]
    )

    with pytest.raises(ValueError, match="recent_history_postgres_dsn_env"):
        module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)


def test_batch_recent_history_db_records_discovered_summaries_without_sql(tmp_path, monkeypatch):
    module = load_batch_module()
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    history_db = tmp_path / "history" / "recent.sqlite"
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                180_000,
                statement="SELECT secret_column FROM sensitive_table",
            )
        ],
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-db",
            str(history_db),
        ],
        env=auth_env(),
    )

    assert result == 0
    payloads = SqliteRecentHistoryStore(history_db).load_payloads()
    assert len(payloads) == 1
    assert payloads[0]["query_id"] == "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"
    assert payloads[0]["statement_present"] is True
    assert "SELECT secret_column" not in json.dumps(payloads[0], sort_keys=True)
    summary = read_batch_summary(tmp_path)
    assert summary["selected_count"] == 1
    assert summary["recent_history"] == {
        "schema_version": 1,
        "enabled": True,
        "backend": "sqlite",
        "status": "recorded",
        "summaries_recorded": 1,
        "profile_jobs_planned": 1,
    }
    jobs = SqliteRecentHistoryStore(history_db).load_profile_jobs()
    assert len(jobs) == 1
    assert jobs[0]["query_id"] == "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"


def test_batch_recent_writes_raw_free_collector_run_summary(tmp_path, monkeypatch):
    module = load_batch_module()
    collector_summary = tmp_path / "collector-summary.json"
    progress_path = tmp_path / "progress.jsonl"
    history_db = tmp_path / "history" / "recent.sqlite"
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                180_000,
                statement="SELECT secret_column FROM sensitive_table",
            )
        ],
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-db",
            str(history_db),
            "--recent-history-collector-summary-json",
            str(collector_summary),
            "--progress-jsonl",
            str(progress_path),
        ],
        env=auth_env(),
    )

    assert result == 0
    payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert payload == {
        "summary_kind": "query_doctor_recent_history_collector_v1",
        "status": "recorded",
        "observed_at_iso": payload["observed_at_iso"],
        "discover_only": True,
        "history_backend": "sqlite",
        "summaries_inspected": 1,
        "candidates_discovered": 1,
        "selected_count": 1,
        "summaries_recorded": 1,
        "profile_jobs_planned": 1,
        "query_log_at_capacity": False,
        "query_log_continuity_status": "not_applicable",
        "issue_codes": [],
        "raw_output": False,
        "sensitive_value_echo": False,
    }
    assert read_collector_summary_progress(progress_path) == (
        collector_summary_progress_from_payload(payload)
    )
    payload_text = json.dumps(payload, sort_keys=True)
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "SELECT secret_column" not in payload_text
    assert "SELECT secret_column" not in progress_text
    assert "sensitive_table" not in payload_text
    assert "sensitive_table" not in progress_text
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in payload_text
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in progress_text
    assert str(history_db) not in payload_text
    assert str(history_db) not in progress_text


def test_batch_recent_collector_summary_blocks_when_query_log_continuity_is_unproven(
    tmp_path,
    monkeypatch,
):
    module = load_batch_module()
    collector_summary = tmp_path / "collector-summary.json"
    history_db = tmp_path / "history" / "recent.sqlite"
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                180_000,
                statement="SELECT secret_column FROM sensitive_table",
            )
        ],
        query_log_at_capacity=True,
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--query-profile-source",
            "impala",
            "--impala-profile-host",
            "impalad-1.example.com",
            "--discover-only",
            "--recent-history-db",
            str(history_db),
            "--recent-history-collector-summary-json",
            str(collector_summary),
        ],
        env={},
    )

    assert result == 0
    payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert payload["status"] == "warning"
    assert payload["query_log_at_capacity"] is True
    assert payload["query_log_continuity_status"] == "unproven"
    assert payload["issue_codes"] == ["impala_query_log_continuity_unproven"]
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in json.dumps(payload, sort_keys=True)


def test_batch_recent_collector_accepts_at_capacity_query_log_with_overlap(
    tmp_path,
    monkeypatch,
):
    module = load_batch_module()
    collector_summary = tmp_path / "collector-summary.json"
    collector_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_collector_v1",
                "observed_at_iso": "2026-05-12T10:16:00Z",
                "raw_output": False,
                "sensitive_value_echo": False,
            }
        ),
        encoding="utf-8",
    )
    history_db = tmp_path / "history" / "recent.sqlite"
    patch_discovered_candidates(
        module,
        monkeypatch,
        [candidate(module, "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb", 180_000)],
        query_log_at_capacity=True,
        query_log_oldest_completed_at_iso="2026-05-12T10:15:00Z",
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--query-profile-source",
            "impala",
            "--impala-profile-host",
            "impalad-1.example.com",
            "--discover-only",
            "--recent-history-db",
            str(history_db),
            "--recent-history-collector-summary-json",
            str(collector_summary),
        ],
        env={},
    )

    assert result == 0
    payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert payload["status"] == "recorded"
    assert payload["query_log_at_capacity"] is True
    assert payload["query_log_continuity_status"] == "confirmed"
    assert payload["issue_codes"] == []


def test_query_log_continuity_detects_forward_gap():
    module = load_batch_module()

    status = module.query_log_continuity_status(
        direct_impala=True,
        query_log_at_capacity=True,
        oldest_completed_at_iso="2026-05-12T10:17:00Z",
        previous_summary={"observed_at_iso": "2026-05-12T10:16:00Z"},
    )

    assert status == "gap_detected"
    assert module.collector_issue_codes(
        status="warning",
        recent_history_status="recorded",
        query_log_continuity_status=status,
    ) == ["impala_query_log_gap_detected"]


def test_batch_recent_collector_summary_marks_disabled_backend(tmp_path, monkeypatch):
    module = load_batch_module()
    collector_summary = tmp_path / "collector-summary.json"
    progress_path = tmp_path / "progress.jsonl"
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                180_000,
                statement="SELECT secret_column FROM sensitive_table",
            )
        ],
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-collector-summary-json",
            str(collector_summary),
            "--progress-jsonl",
            str(progress_path),
        ],
        env=auth_env(),
    )

    assert result == 0
    payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert payload == {
        "summary_kind": "query_doctor_recent_history_collector_v1",
        "status": "disabled",
        "observed_at_iso": payload["observed_at_iso"],
        "discover_only": True,
        "history_backend": "disabled",
        "summaries_inspected": 1,
        "candidates_discovered": 1,
        "selected_count": 1,
        "summaries_recorded": 0,
        "profile_jobs_planned": 0,
        "query_log_at_capacity": False,
        "query_log_continuity_status": "not_applicable",
        "issue_codes": ["recent_history_disabled"],
        "raw_output": False,
        "sensitive_value_echo": False,
    }
    assert read_collector_summary_progress(progress_path) == (
        collector_summary_progress_from_payload(payload)
    )
    payload_text = json.dumps(payload, sort_keys=True)
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "SELECT secret_column" not in payload_text
    assert "SELECT secret_column" not in progress_text
    assert "sensitive_table" not in payload_text
    assert "sensitive_table" not in progress_text
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in payload_text
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in progress_text


def test_batch_recent_collector_summary_marks_discovery_failure_raw_free(
    tmp_path,
    monkeypatch,
):
    module = load_batch_module()
    collector_summary = tmp_path / "collector-summary.json"
    progress_path = tmp_path / "progress.jsonl"
    history_db = tmp_path / "history" / "recent.sqlite"

    def fail_discovery(config, env):
        raise RuntimeError("SELECT secret_column FROM sensitive_table")

    monkeypatch.setattr(module, "discover_candidates", fail_discovery)

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-db",
            str(history_db),
            "--recent-history-collector-summary-json",
            str(collector_summary),
            "--progress-jsonl",
            str(progress_path),
        ],
        env=auth_env(),
    )

    assert result == 1
    payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert payload == {
        "summary_kind": "query_doctor_recent_history_collector_v1",
        "status": "failed",
        "observed_at_iso": payload["observed_at_iso"],
        "discover_only": True,
        "history_backend": "sqlite",
        "summaries_inspected": 0,
        "candidates_discovered": 0,
        "selected_count": 0,
        "summaries_recorded": 0,
        "profile_jobs_planned": 0,
        "query_log_at_capacity": False,
        "query_log_continuity_status": "not_applicable",
        "issue_codes": ["discovery_failed"],
        "raw_output": False,
        "sensitive_value_echo": False,
    }
    assert read_collector_summary_progress(progress_path) == (
        collector_summary_progress_from_payload(payload)
    )
    payload_text = json.dumps(payload, sort_keys=True)
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "SELECT secret_column" not in payload_text
    assert "SELECT secret_column" not in progress_text
    assert "sensitive_table" not in payload_text
    assert "sensitive_table" not in progress_text
    assert str(history_db) not in payload_text
    assert str(history_db) not in progress_text


def test_batch_recent_collector_summary_marks_recent_history_warning_raw_free(
    tmp_path,
    monkeypatch,
):
    module = load_batch_module()
    collector_summary = tmp_path / "collector-summary.json"
    progress_path = tmp_path / "progress.jsonl"
    history_db = tmp_path / "history" / "recent.sqlite"
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                180_000,
                statement="SELECT secret_column FROM sensitive_table",
            )
        ],
    )
    monkeypatch.setattr(
        module,
        "persist_recent_history",
        lambda candidates, *, config, env: (0, "recent history unavailable"),
    )
    monkeypatch.setattr(
        module,
        "enqueue_recent_profile_jobs",
        lambda candidates, *, config, env: (0, None),
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-db",
            str(history_db),
            "--recent-history-collector-summary-json",
            str(collector_summary),
            "--progress-jsonl",
            str(progress_path),
        ],
        env=auth_env(),
    )

    assert result == 0
    payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert payload == {
        "summary_kind": "query_doctor_recent_history_collector_v1",
        "status": "warning",
        "observed_at_iso": payload["observed_at_iso"],
        "discover_only": True,
        "history_backend": "sqlite",
        "summaries_inspected": 1,
        "candidates_discovered": 1,
        "selected_count": 1,
        "summaries_recorded": 0,
        "profile_jobs_planned": 0,
        "query_log_at_capacity": False,
        "query_log_continuity_status": "not_applicable",
        "issue_codes": ["recent_history_warning"],
        "raw_output": False,
        "sensitive_value_echo": False,
    }
    assert read_collector_summary_progress(progress_path) == (
        collector_summary_progress_from_payload(payload)
    )
    payload_text = json.dumps(payload, sort_keys=True)
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "SELECT secret_column" not in payload_text
    assert "SELECT secret_column" not in progress_text
    assert "sensitive_table" not in payload_text
    assert "sensitive_table" not in progress_text
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in payload_text
    assert "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb" not in progress_text
    assert str(history_db) not in payload_text
    assert str(history_db) not in progress_text


def test_batch_recent_history_retention_prunes_old_raw_free_rows(tmp_path, monkeypatch):
    from dataclasses import replace

    from query_doctor.recent.history_store import history_record_from_candidate
    from query_doctor.recent.profile_budget import (
        ANALYSIS_CACHE_SCHEMA_VERSION,
        PROFILE_ARTIFACT_SCHEMA_VERSION,
        PROFILE_JOB_STATUS_COMPLETED,
        ProfileBudgetPolicy,
        RecentAnalysisCacheRecord,
        RecentProfileArtifactRecord,
        plan_recent_profile_jobs,
    )
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    module = load_batch_module()
    history_db = tmp_path / "history" / "recent.sqlite"
    store = SqliteRecentHistoryStore(history_db)
    old_candidate = candidate(
        module,
        "old-summary-query",
        180_000,
        statement="SELECT secret_column FROM sensitive_table",
    )
    old_record = history_record_from_candidate(
        old_candidate,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2000-01-01T00:00:00+00:00",
    )
    old_job = replace(
        plan_recent_profile_jobs(
            [old_record],
            policy=ProfileBudgetPolicy(max_jobs=1),
            planned_at_iso="2000-01-01T00:00:00+00:00",
        )[0],
        status=PROFILE_JOB_STATUS_COMPLETED,
        updated_at_iso="2000-01-01T00:00:00+00:00",
    )
    store.upsert_summaries([old_record])
    store.enqueue_profile_jobs([old_job])
    store.store_analysis_cache_records(
        [
            RecentAnalysisCacheRecord(
                schema_version=ANALYSIS_CACHE_SCHEMA_VERSION,
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                query_id="old-cache-query",
                profile_fingerprint="profile_fingerprint_v1",
                analyzer_contract="profile_digest_analysis_json_v1",
                recorded_at_iso="2000-01-01T00:00:00+00:00",
                status="ready",
                payload={
                    "diagnosis_status": "old",
                    "statement": "SELECT secret_column FROM sensitive_table",
                },
            )
        ]
    )
    assert (
        store.store_profile_artifact_records(
            [
                RecentProfileArtifactRecord(
                    schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
                    engine="impala",
                    source_kind="cm",
                    source_key="cm:cluster:impala",
                    query_id="old-artifact-query",
                    profile_fingerprint="profile_fingerprint_v1",
                    artifact_contract="profile_artifact_v1",
                    recorded_at_iso="2000-01-01T00:00:00+00:00",
                    status="available",
                    storage_kind="local",
                    storage_key="sha256_oldprofile",
                    size_bytes=4096,
                )
            ]
        )
        == 1
    )
    assert (
        store.store_profile_artifact_records(
            [
                RecentProfileArtifactRecord(
                    schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
                    engine="impala",
                    source_kind="cm",
                    source_key="cm:cluster:impala",
                    query_id="unsafe-artifact-query",
                    profile_fingerprint="profile_fingerprint_v1",
                    artifact_contract="profile_artifact_v1",
                    recorded_at_iso="2000-01-01T00:00:00+00:00",
                    status="available",
                    storage_kind="local",
                    storage_key="/private/tmp/query-doctor-secret/profile.txt",
                    size_bytes=4096,
                )
            ]
        )
        == 0
    )
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "new-summary-query",
                180_000,
                statement="SELECT other_secret FROM newer_table",
            )
        ],
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-db",
            str(history_db),
            "--recent-history-summary-retention-days",
            "1",
            "--recent-history-profile-job-retention-days",
            "1",
            "--recent-history-analysis-cache-retention-days",
            "1",
            "--recent-history-profile-artifact-retention-days",
            "1",
        ],
        env=auth_env(),
    )

    assert result == 0
    summary = read_batch_summary(tmp_path)
    assert summary["recent_history"]["retention"] == {
        "enabled": True,
        "status": "pruned",
        "summaries_deleted": 1,
        "profile_jobs_deleted": 1,
        "analysis_cache_deleted": 1,
        "profile_artifacts_deleted": 1,
        "total_deleted": 4,
    }
    payload_text = json.dumps(SqliteRecentHistoryStore(history_db).load_payloads(), sort_keys=True)
    assert "new-summary-query" in payload_text
    assert "old-summary-query" not in payload_text
    assert "SELECT secret_column" not in payload_text
    assert "newer_table" not in payload_text
    remaining_jobs = SqliteRecentHistoryStore(history_db).load_profile_jobs()
    assert len(remaining_jobs) == 1
    assert remaining_jobs[0]["query_id"] == "new-summary-query"
    assert remaining_jobs[0]["status"] == "pending"
    assert (
        SqliteRecentHistoryStore(history_db).load_analysis_cache_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="old-cache-query",
            profile_fingerprint="profile_fingerprint_v1",
            analyzer_contract="profile_digest_analysis_json_v1",
        )
        is None
    )
    assert (
        SqliteRecentHistoryStore(history_db).load_profile_artifact_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="old-artifact-query",
            profile_fingerprint="profile_fingerprint_v1",
            artifact_contract="profile_artifact_v1",
        )
        is None
    )
    history_text = json.dumps(SqliteRecentHistoryStore(history_db).load_payloads(), sort_keys=True)
    assert "/private/tmp/query-doctor-secret" not in history_text
    assert "profile.txt" not in history_text


def test_batch_recent_postgres_history_backend_missing_dsn_warns_safely(tmp_path, monkeypatch):
    module = load_batch_module()
    patch_discovered_candidates(
        module,
        monkeypatch,
        [
            candidate(
                module,
                "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb",
                180_000,
                statement="SELECT secret_column FROM sensitive_table",
            )
        ],
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--discover-only",
            "--recent-history-backend",
            "postgres",
            "--recent-history-postgres-dsn-env",
            "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN",
        ],
        env=auth_env(),
    )

    assert result == 0
    summary = read_batch_summary(tmp_path)
    assert summary["recent_history"] == {
        "schema_version": 1,
        "enabled": True,
        "backend": "postgres",
        "status": "warning",
        "summaries_recorded": 0,
        "profile_jobs_planned": 0,
    }
    warnings_text = json.dumps(summary["warnings"], sort_keys=True)
    assert "Recent history store was not updated" in warnings_text
    assert "Recent profile jobs were not planned" in warnings_text
    assert "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN" not in warnings_text
    assert "SELECT secret_column" not in warnings_text


def test_batch_skips_workload_history_without_opt_in(tmp_path, monkeypatch):
    module = load_batch_module()
    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    def fail_history_update(*args, **kwargs):
        raise AssertionError("workload history must be opt-in")

    monkeypatch.setattr(module, "update_summary_with_workload_history", fail_history_update)

    result = module.main(base_args(tmp_path), env=auth_env())

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert "workload_history" not in payload


def test_batch_workload_history_opt_in_records_empty_status(tmp_path, monkeypatch):
    module = load_batch_module()
    history_path = tmp_path / "history" / "workload.jsonl"
    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    result = module.main(
        [
            *base_args(tmp_path),
            "--collect-workload-history",
            "--workload-history-path",
            str(history_path),
        ],
        env=auth_env(),
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert payload["workload_history"]["enabled"] is True
    assert payload["workload_history"]["append_status"] == "empty"
    assert payload["workload_history"]["appended_record_count"] == 0
    assert not history_path.exists()
    summary_md = read_batch_summary_markdown(tmp_path)
    assert "## Workload History" in summary_md
    assert "- append status: empty" in summary_md
    assert str(history_path) not in summary_md


def test_batch_config_accepts_web_metadata_redaction_flags(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        [
            *base_args(tmp_path),
            "--metadata-mode",
            "off",
            "--metadata-no-redact-identifiers",
            "--metadata-no-redact-hosts",
        ]
    )

    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.redact_identifiers is False
    assert config.redact_hosts is False


def test_batch_config_ca_bundle_expands_home_directory(tmp_path, monkeypatch):
    module = load_batch_module()
    home = tmp_path / "home"
    home.mkdir()
    config_out = tmp_path / "query-doctor-config-batch"
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(
        json.dumps(
            {
                "cm_url": "https://config-cm.example.net:7183",
                "cm_user": "config_user",
                "cluster": "config_cluster",
                "service": "config_impala",
                "out": str(config_out),
                "ca_bundle": "~/cm-chain.pem",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    args = module.parse_args(["--config", str(config_path)])
    config = module.build_batch_config(
        args, env={"CM_PASSWORD": "secret"}, cwd=tmp_path, repo_root=REPO_DIR
    )

    assert config.ca_bundle == str(home / "cm-chain.pem")


def test_batch_cli_values_override_local_config(tmp_path):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_out = tmp_path / "query-doctor-config-batch"
    config_path.write_text(
        json.dumps(
            {
                "cm_url": "https://config-cm.example.net:7183",
                "username": "config_user",
                "cluster": "config_cluster",
                "service": "config_impala",
                "out": str(config_out),
                "recent_window_minutes": 120,
                "recent_cm_summary_limit": 333,
                "recent_profile_analysis_limit": 44,
                "recent_metadata_top_limit": 6,
                "recent_min_duration_sec": 7.5,
                "recent_order": "recent",
                "recent_include_failed": True,
                "metadata_timeout_sec": 55,
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--out",
            str(batch_dir(tmp_path)),
            "--cm-url",
            "https://cli-cm.example.net:7183",
            "--cluster",
            "cli_cluster",
            "--service",
            "cli_impala",
            "--recent-window-minutes",
            "30",
            "--cm-inspect-limit",
            "10",
            "--triage-profile-limit",
            "3",
            "--metadata-top-limit",
            "2",
            "--min-duration-sec",
            "1",
            "--order",
            "duration-asc",
            "--include-running",
            "--metadata-timeout-sec",
            "9",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    assert config.out == batch_dir(tmp_path)
    assert config.cm_url == "https://cli-cm.example.net:7183"
    assert config.cluster == "cli_cluster"
    assert config.service == "cli_impala"
    assert config.recent_window_minutes == 30
    assert config.cm_inspect_limit == 10
    assert config.triage_profile_limit == 3
    assert config.metadata_top_limit == 2
    assert config.min_duration_sec == 1
    assert config.order == "duration-asc"
    assert config.include_failed is True
    assert config.include_running is True
    assert config.metadata_timeout_sec == 9


def test_batch_out_missing_from_cli_and_config_fails_after_config_loading(tmp_path, capsys):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(
        json.dumps(
            {
                "cm_url": "https://config-cm.example.net:7183",
                "cluster": "config_cluster",
                "service": "config_impala",
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(["--config", str(config_path)])
    with pytest.raises(
        ValueError, match="missing required output directory: provide --out or config field out"
    ):
        module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)

    result = module.main(["--config", str(config_path)], env=auth_env())

    assert result == 2
    assert (
        "missing required output directory: provide --out or config field out"
        in capsys.readouterr().err
    )


def test_batch_unknown_config_field_still_fails_fast_with_optional_out(tmp_path):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(
        json.dumps(
            {
                "cm_url": "https://config-cm.example.net:7183",
                "cluster": "config_cluster",
                "service": "config_impala",
                "out": str(tmp_path / "query-doctor-config-batch"),
                "profile_analysis_limit": 50,
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(["--config", str(config_path)])
    with pytest.raises(
        module.cm_profiles.ConfigError, match="Unknown config field profile_analysis_limit"
    ):
        module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)


def test_invalid_jobs_values_fail_clearly(tmp_path, capsys):
    module = load_batch_module()

    with pytest.raises(SystemExit):
        module.parse_args(base_args(tmp_path) + ["--jobs", "0"])
    assert "must be a positive integer" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        module.parse_args(base_args(tmp_path) + ["--jobs", "-1"])
    assert "must be a positive integer" in capsys.readouterr().err

    result = module.main(base_args(tmp_path) + ["--jobs", "5"], env=auth_env())

    assert result == 2
    assert "--jobs must be <= 4" in capsys.readouterr().err


def test_cm_jobs_and_metadata_jobs_have_separate_caps(tmp_path, capsys):
    module = load_batch_module()

    result = module.main(base_args(tmp_path) + ["--cm-jobs", "101"], env=auth_env())

    assert result == 2
    assert "--cm-jobs must be <= 100" in capsys.readouterr().err

    result = module.main(base_args(tmp_path) + ["--metadata-jobs", "6"], env=auth_env())

    assert result == 2
    assert "--metadata-jobs must be <= 5" in capsys.readouterr().err


def test_triage_profile_limit_hard_cap_is_5000(tmp_path, capsys):
    module = load_batch_module()
    args = [
        "--out",
        str(batch_dir(tmp_path)),
        "--cm-url",
        "https://cm.example.net:7183",
        "--cluster",
        "cluster",
        "--service",
        "impala",
        "--cm-inspect-limit",
        "5000",
        "--triage-profile-limit",
        "5001",
    ]

    result = module.main(args, env=auth_env())

    assert result == 2
    assert "--triage-profile-limit must be <= 5000" in capsys.readouterr().err


def test_impala_query_list_max_bytes_reaches_the_batch_config(tmp_path):
    # The discovery fetch takes this bound as an argument. Without it wired
    # through the config, a coordinator whose /queries?json is larger than the
    # default cap can never be scanned at all.
    module = load_batch_module()

    default_config = module.build_batch_config(
        module.parse_args(direct_impala_args(tmp_path)),
        env=auth_env(),
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )

    assert default_config.impala_query_list_max_bytes == 5 * 1024 * 1024

    raised_config = module.build_batch_config(
        module.parse_args(
            direct_impala_args(
                tmp_path,
                "--impala-query-list-max-bytes",
                str(64 * 1024 * 1024),
            )
        ),
        env=auth_env(),
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )

    assert raised_config.impala_query_list_max_bytes == 64 * 1024 * 1024


def test_impala_query_list_max_bytes_hard_cap(tmp_path, capsys):
    module = load_batch_module()

    result = module.main(
        direct_impala_args(
            tmp_path,
            "--impala-query-list-max-bytes",
            str(256 * 1024 * 1024 + 1),
        ),
        env=auth_env(),
    )

    assert result == 2
    assert "--impala-query-list-max-bytes must be <= 268435456" in capsys.readouterr().err


def test_high_jobs_require_explicit_safe_mode(tmp_path, monkeypatch):
    module = load_batch_module()

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    result = module.main(
        base_args(tmp_path) + ["--jobs", "5", "--metadata-mode", "off", "--allow-high-jobs"],
        env=auth_env(),
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert payload["jobs"] == 5


def test_high_jobs_allows_100_in_safe_mode(tmp_path, monkeypatch):
    module = load_batch_module()

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    result = module.main(
        base_args(tmp_path) + ["--jobs", "100", "--metadata-mode", "off", "--allow-high-jobs"],
        env=auth_env(),
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert payload["jobs"] == 100


def test_jobs_above_high_cap_fails(tmp_path, capsys):
    module = load_batch_module()

    result = module.main(
        base_args(tmp_path) + ["--jobs", "101", "--metadata-mode", "off", "--allow-high-jobs"],
        env=auth_env(),
    )

    assert result == 2
    assert "--jobs must be <= 100" in capsys.readouterr().err


@pytest.mark.parametrize("metadata_mode", ["auto", "on", "dry-run"])
def test_allow_high_jobs_accepts_metadata_modes_when_reports_are_disabled(
    tmp_path, monkeypatch, metadata_mode
):
    module = load_batch_module()

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    result = module.main(
        base_args(tmp_path)
        + ["--jobs", "5", "--metadata-mode", metadata_mode, "--allow-high-jobs"],
        env=auth_env(),
    )

    assert result == 0


def test_allow_high_jobs_rejects_top_reports(tmp_path, capsys):
    module = load_batch_module()

    result = module.main(
        base_args(tmp_path)
        + ["--jobs", "5", "--metadata-mode", "off", "--top-reports", "1", "--allow-high-jobs"],
        env=auth_env(),
    )

    assert result == 2
    assert "--allow-high-jobs requires --top-reports 0" in capsys.readouterr().err


def test_missing_out_path_gets_created(tmp_path, monkeypatch):
    module = load_batch_module()

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    out = tmp_path / "query-doctor-new-batch"
    args = base_args(tmp_path)
    args[1] = str(out)
    result = module.main(args + ["--discover-only"], env=auth_env())

    assert result == 0
    assert out.is_dir()
    assert (out / "batch_summary.json").exists()


def test_existing_empty_out_path_succeeds(tmp_path, monkeypatch):
    module = load_batch_module()

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "client-side", None),
    )

    out = batch_dir(tmp_path)
    out.mkdir()

    result = module.main(base_args(tmp_path) + ["--discover-only"], env=auth_env())

    assert result == 0
    assert (out / "batch_summary.json").exists()


def test_empty_discovery_with_warning_exits_success_and_writes_summary(tmp_path, monkeypatch):
    module = load_batch_module()

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(
            [],
            ["CM returned no matching query summaries"],
            "client-side",
            None,
        ),
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("no profile subprocesses should run without selected candidates")

    monkeypatch.setattr(module, "run_subprocess", fail_run)

    result = module.main(base_args(tmp_path) + ["--no-min-duration-filter"], env=auth_env())

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert payload["summaries_inspected"] == 0
    assert payload["selected_count"] == 0
    assert payload["duration_filter"] == "none"
    assert payload["discovery_failed"] is False
    assert payload["warnings"] == ["CM returned no matching query summaries"]
    assert (batch_dir(tmp_path) / "batch_summary.md").exists()


def test_zero_selected_candidates_after_discovery_exits_success(tmp_path, monkeypatch):
    module = load_batch_module()
    excluded = candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000, selected=False)
    excluded = module.cm_profiles.RecentQueryCandidate(
        summary=excluded.summary,
        selected=False,
        reason="excluded: not analyzable query text",
        sql_verb="CREATE",
    )

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([excluded], [], "client-side", None),
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("no profile subprocesses should run without selected candidates")

    monkeypatch.setattr(module, "run_subprocess", fail_run)

    result = module.main(base_args(tmp_path) + ["--no-min-duration-filter"], env=auth_env())

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert payload["summaries_inspected"] == 1
    assert payload["selected_count"] == 0
    assert payload["candidate_exclusion_count"] == 1
    assert payload["candidate_reason_counts"] == {"excluded: not analyzable query text": 1}
    assert payload["candidate_reason_sql_verb_counts"] == {
        "excluded: not analyzable query text": {"CREATE": 1}
    }
    assert payload["discovery_failed"] is False
    assert payload["cases"] == []
    summary_md = read_batch_summary_markdown(tmp_path)
    assert "- excluded candidates: 1" in summary_md
    assert "## Candidate Selection Breakdown" in summary_md
    assert "- excluded: not analyzable query text: 1" in summary_md


def test_batch_reuses_prior_analyzed_profile_for_matching_query_id(tmp_path, monkeypatch):
    module = load_batch_module()
    query_id = "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"
    prior_root = tmp_path / "reuse-root"
    prior_out = prior_root / "query-doctor-web-batch-old"
    prior_wrapper = prior_out / "cases" / "case-001"
    prior_case = prior_wrapper / query_id.replace(":", "_")
    write_case(prior_case, healthy_facts())
    (prior_case / "analysis.json").write_text("{}", encoding="utf-8")
    prior_out.mkdir(parents=True, exist_ok=True)
    (prior_out / "batch_summary.json").write_text(
        json.dumps(
            {
                "mode": "recent-query-batch",
                "query_profile_source": "cm",
                "source_visibility": "safe",
                "include_running": False,
                "only_running": False,
                "collect_cm_timeseries": False,
                "collect_prometheus_timeseries": False,
                "runtime_metrics_provider": "none",
                "metadata_top_limit": 0,
                "profile_reuse_contract": {
                    "version": "recent_analyzed_profile_reuse_v1",
                    "case_artifact_contract": "profile_digest_analysis_json_v1",
                    "query_profile_source": "cm",
                    "source_visibility": "safe",
                    "privacy_mode": True,
                    "redact_identifiers": True,
                    "redact_hosts": True,
                    "metadata_top_limit": 0,
                    "collect_cm_timeseries": False,
                    "collect_prometheus_timeseries": False,
                    "runtime_metrics_provider": "none",
                },
                "cases": [
                    {
                        "query_id": query_id,
                        "case_dir": str(prior_wrapper),
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    patch_discovered_candidates(module, monkeypatch, [candidate(module, query_id, 120_000)])

    def fail_run_subprocess(*args, **kwargs):
        raise AssertionError("collector and analyzer subprocesses should not run for reused cases")

    monkeypatch.setattr(module, "run_subprocess", fail_run_subprocess)

    result = module.main(
        [
            *base_args(tmp_path),
            "--metadata-mode",
            "off",
            "--no-min-duration-filter",
            "--reuse-analyzed-profiles-from",
            str(prior_root),
        ],
        env=auth_env(),
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert payload["profile_reused_case_count"] == 1
    assert payload["profile_reuse"]["status_counts"] == {"reused": 1}
    assert payload["cases"][0]["profile_reuse_status"] == "reused"
    assert payload["cases"][0]["collection_status"] == "ok"
    assert payload["cases"][0]["analysis_status"] == "ok"
    assert payload["cases"][0]["case_dir"] == str(batch_dir(tmp_path) / "cases" / "case-001")
    assert (
        batch_dir(tmp_path)
        / "cases"
        / "case-001"
        / query_id.replace(":", "_")
        / "analysis_facts.md"
    ).is_file()
    assert str(prior_wrapper) not in json.dumps(payload)
    summary_md = read_batch_summary_markdown(tmp_path)
    assert "- reused analyzed profiles: 1" in summary_md


def test_batch_reuse_requires_profile_reuse_contract(tmp_path):
    module = load_batch_module()
    from query_doctor.recent.case_reuse import summary_is_compatible

    config = build_cm_config(
        module,
        tmp_path,
        "--metadata-mode",
        "off",
        "--reuse-analyzed-profiles-from",
        str(tmp_path),
    )

    assert (
        summary_is_compatible(
            {
                "mode": "recent-query-batch",
                "query_profile_source": "cm",
                "source_visibility": "safe",
                "include_running": False,
                "only_running": False,
                "collect_cm_timeseries": False,
                "collect_prometheus_timeseries": False,
                "runtime_metrics_provider": "none",
                "metadata_top_limit": 0,
            },
            config,
        )
        is False
    )


def test_analyzed_profile_reuse_is_disabled_for_owner_raw(tmp_path):
    module = load_batch_module()
    from query_doctor.recent.case_reuse import analyzed_profile_reuse_skip_reason

    config = build_cm_config(
        module,
        tmp_path,
        "--source-visibility",
        "owner_raw",
        "--source-owner-user",
        "analyst",
        "--user",
        "analyst",
        "--reuse-analyzed-profiles-from",
        str(tmp_path),
    )

    assert analyzed_profile_reuse_skip_reason(config) == "source_visibility_not_safe"


def test_existing_non_empty_out_path_fails_without_overwrite(tmp_path, monkeypatch, capsys):
    module = load_batch_module()

    def fail_discovery(*args, **kwargs):
        raise AssertionError("discovery must not run when --out is stale")

    monkeypatch.setattr(module, "discover_candidates", fail_discovery)
    out = batch_dir(tmp_path)
    out.mkdir()
    (out / "stale.txt").write_text("old\n", encoding="utf-8")

    result = module.main(base_args(tmp_path), env=auth_env())

    assert result == 2
    assert "output directory exists and is not empty" in capsys.readouterr().err
    assert (out / "stale.txt").exists()


def test_existing_non_empty_out_path_overwrite_removes_stale_files(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)
    calls = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([selected], [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            case_dir = out / query_id.replace(":", "_")
            write_case(case_dir, healthy_facts())
            if "--collect-cm-timeseries" in cmd:
                (case_dir / "cm_timeseries_context.json").write_text(
                    '{"available": true}\n', encoding="utf-8"
                )
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)
    out = batch_dir(tmp_path)
    stale_dir = out / "cases" / "case-999" / "stale_nested_case"
    stale_dir.mkdir(parents=True)
    (stale_dir / "analysis_facts.md").write_text("stale\n", encoding="utf-8")

    result = module.main(base_args(tmp_path) + ["--overwrite"], env=auth_env())

    assert result == 0
    assert not stale_dir.exists()
    payload = json.loads((out / "batch_summary.json").read_text())
    assert payload["selected_count"] == 1
    assert [case["case_dir"] for case in payload["cases"]] == [str(out / "cases" / "case-001")]
    collect_calls = [cmd for cmd in calls if command_uses_role(cmd, "collect_cm")]
    assert all("--no-collect-cm-timeseries" in cmd for cmd in collect_calls)


def test_repo_local_output_path_is_rejected_even_without_overwrite(tmp_path):
    module = load_batch_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="outside the repository"):
        module.prepare_batch_output_dir(repo_root / "batch", repo_root=repo_root, overwrite=False)


def test_overwrite_refuses_repo_local_output_path(tmp_path):
    module = load_batch_module()
    repo_root = tmp_path / "repo"
    out = repo_root / "batch"
    out.mkdir(parents=True)
    (out / "stale.txt").write_text("old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the repository"):
        module.prepare_batch_output_dir(out, repo_root=repo_root, overwrite=True)

    assert (out / "stale.txt").exists()


def test_overwrite_refuses_tmp_itself():
    module = load_batch_module()

    with pytest.raises(ValueError, match="temp root itself"):
        module.prepare_batch_output_dir(Path("/tmp"), repo_root=REPO_DIR, overwrite=True)


def test_overwrite_allows_safe_tmp_query_doctor_path(tmp_path):
    module = load_batch_module()
    out = tmp_path / "query-doctor-safe"
    out.mkdir()
    (out / "stale.txt").write_text("old\n", encoding="utf-8")

    module.prepare_batch_output_dir(out, repo_root=REPO_DIR, overwrite=True)

    assert out.is_dir()
    assert not (out / "stale.txt").exists()


def test_overwrite_refuses_filesystem_root():
    module = load_batch_module()

    with pytest.raises(ValueError, match="filesystem root"):
        module.prepare_batch_output_dir(Path("/"), repo_root=REPO_DIR, overwrite=True)


@pytest.mark.parametrize(
    "system_path", ["/etc/query-doctor-batch", "/usr/query-doctor-batch", "/var/query-doctor-batch"]
)
def test_overwrite_refuses_system_like_paths(system_path):
    module = load_batch_module()

    with pytest.raises(ValueError, match="system directory"):
        module.prepare_batch_output_dir(Path(system_path), repo_root=REPO_DIR, overwrite=True)


def test_overwrite_refuses_direct_child_of_home():
    module = load_batch_module()

    with pytest.raises(ValueError, match="direct child of the home directory"):
        module.prepare_batch_output_dir(
            Path.home() / "query-doctor-batch", repo_root=REPO_DIR, overwrite=True
        )


def test_output_path_must_be_query_doctor_prefixed(tmp_path):
    module = load_batch_module()

    with pytest.raises(ValueError, match="must start with query-doctor-"):
        module.prepare_batch_output_dir(tmp_path / "batch", repo_root=REPO_DIR, overwrite=False)


def test_output_symlink_is_rejected(tmp_path):
    module = load_batch_module()
    target = tmp_path / "query-doctor-target"
    target.mkdir()
    out = tmp_path / "query-doctor-link"
    try:
        out.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported in this test environment")

    with pytest.raises(ValueError, match="must not be a symlink"):
        module.prepare_batch_output_dir(out, repo_root=REPO_DIR, overwrite=True)


def test_missing_cm_auth_fails_before_collection(tmp_path, monkeypatch, capsys):
    module = load_batch_module()

    def fail_discovery(*args, **kwargs):
        raise AssertionError("discovery must not run without CM auth")

    monkeypatch.setattr(module, "discover_candidates", fail_discovery)

    result = module.main(base_args(tmp_path), env={})

    assert result == 2
    assert "CM auth env is not set" in capsys.readouterr().err


def test_metadata_auth_preflight_requires_ticket_before_discovery(tmp_path, monkeypatch, capsys):
    module = load_batch_module()

    def fail_discovery(*args, **kwargs):
        raise AssertionError("discovery must not run when metadata auth cannot work")

    monkeypatch.setattr(module, "discover_candidates", fail_discovery)

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-top-limit",
            "1",
        ],
        env=auth_env(),
    )

    assert result == 2
    error_output = capsys.readouterr().err
    assert "KRB5CCNAME is required before metadata collection can use Kerberos" in error_output


def test_metadata_auth_preflight_expired_ticket_error_is_raw_free(tmp_path, monkeypatch, capsys):
    module = load_batch_module()
    from query_doctor.impala.kerberos_preflight import KerberosTicketCheck
    from query_doctor.recent import batch_config

    def fail_discovery(*args, **kwargs):
        raise AssertionError("discovery must not run when metadata auth cannot work")

    monkeypatch.setattr(module, "discover_candidates", fail_discovery)
    monkeypatch.setattr(
        batch_config,
        "check_kerberos_ticket_cache",
        lambda env: KerberosTicketCheck(
            False,
            "Kerberos ticket cache is missing or expired; refresh it before metadata collection.",
        ),
    )

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-top-limit",
            "1",
        ],
        env={**auth_env(), "KRB5CCNAME": "FILE:/tmp/krb5cc_private_cache"},
    )

    assert result == 2
    error_output = capsys.readouterr().err
    assert "Kerberos ticket cache is missing or expired" in error_output
    assert "krb5cc_private_cache" not in error_output


def test_batch_recent_loads_explicit_qdcreds_cm_env_before_preflight(tmp_path, monkeypatch, capsys):
    module = load_batch_module()
    env_file = tmp_path / "cm-ro.env"
    env_file.write_text(
        "export CM_USERNAME=cm_user\nCM_PASSWORD='secret-password'\n", encoding="utf-8"
    )
    captured_env = {}

    def fake_discovery(config, *, env):
        captured_env.update(env)
        return module.DiscoveryResult(
            candidates=[],
            warnings=[],
            duration_filter_mode="server-side",
            server_filter_expression="duration >= 60s",
            summaries_inspected=0,
        )

    monkeypatch.setattr(module, "discover_candidates", fake_discovery)

    result = module.main(
        base_args(tmp_path) + ["--discover-only", "--metadata-mode", "off"],
        env={"QD_CM_ENV": str(env_file)},
    )

    assert result == 0
    assert captured_env["CM_USERNAME"] == "cm_user"
    assert captured_env["CM_PASSWORD"] == "secret-password"
    output = capsys.readouterr().out
    assert "secret-password" not in output
    assert (batch_dir(tmp_path) / "batch_summary.json").exists()


def test_batch_recent_does_not_override_existing_cm_auth_from_qdcreds(tmp_path):
    module = load_batch_module()
    env_file = tmp_path / "cm-ro.env"
    env_file.write_text(
        "CM_USERNAME=file_user\nCM_PASSWORD=file-secret\nCM_TOKEN=file-token\n",
        encoding="utf-8",
    )

    loaded = module.load_local_cm_env(
        {
            "QD_CM_ENV": str(env_file),
            "CM_USERNAME": "env_user",
            "CM_TOKEN": "env-token",
        },
        allow_default=False,
    )

    assert loaded["CM_USERNAME"] == "env_user"
    assert loaded["CM_TOKEN"] == "env-token"
    assert loaded["CM_PASSWORD"] == "file-secret"


def test_batch_recent_cm_env_ignores_non_cm_keys(tmp_path):
    module = load_batch_module()
    env_file = tmp_path / "cm-ro.env"
    env_file.write_text(
        "CM_USERNAME=cm_user\n"
        "QD_KRB5_PRINCIPAL=analyst_one@EXAMPLE.COM\n"
        "QD_SOURCE_OWNER_USER=explicit_owner\n"
        "KRB5_PRINCIPAL=service/example@EXAMPLE.COM\n"
        "KRB5CCNAME=FILE:/tmp/krb5cc_query_doctor_custom\n"
        "UNSUPPORTED_OWNER=ignored\n",
        encoding="utf-8",
    )

    loaded = module.load_local_cm_env({"QD_CM_ENV": str(env_file)}, allow_default=False)

    assert loaded["CM_USERNAME"] == "cm_user"
    assert "QD_KRB5_PRINCIPAL" not in loaded
    assert "QD_SOURCE_OWNER_USER" not in loaded
    assert "KRB5_PRINCIPAL" not in loaded
    assert "KRB5CCNAME" not in loaded
    assert "UNSUPPORTED_OWNER" not in loaded


def test_discovery_select_limit_adds_large_no_report_backfill_reserve(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        [
            "--out",
            str(batch_dir(tmp_path)),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--cm-inspect-limit",
            "130",
            "--select-limit",
            "100",
            "--metadata-mode",
            "off",
            "--top-reports",
            "0",
        ]
    )
    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)

    assert module.discovery_select_limit(config) == 110


def test_discovery_select_limit_leaves_non_calibration_runs_unchanged(tmp_path):
    module = load_batch_module()
    base_args = [
        "--out",
        str(batch_dir(tmp_path)),
        "--cm-url",
        "https://cm.example.net:7183",
        "--cluster",
        "cluster",
        "--service",
        "impala",
        "--cm-inspect-limit",
        "130",
        "--select-limit",
        "100",
        "--metadata-mode",
        "off",
    ]

    report_config = module.build_batch_config(
        module.parse_args([*base_args, "--top-reports", "1"]),
        env={},
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )
    discover_only_config = module.build_batch_config(
        module.parse_args([*base_args, "--top-reports", "0", "--discover-only"]),
        env={},
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )
    small_config = module.build_batch_config(
        module.parse_args(
            [
                "--out",
                str(batch_dir(tmp_path)),
                "--cm-url",
                "https://cm.example.net:7183",
                "--cluster",
                "cluster",
                "--service",
                "impala",
                "--cm-inspect-limit",
                "130",
                "--select-limit",
                "99",
                "--metadata-mode",
                "off",
                "--top-reports",
                "0",
            ]
        ),
        env={},
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )

    assert module.discovery_select_limit(report_config) == 100
    assert module.discovery_select_limit(discover_only_config) == 100
    assert module.discovery_select_limit(small_config) == 99


def test_retain_backfilled_case_results_keeps_requested_successes(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        [
            "--out",
            str(batch_dir(tmp_path)),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--cm-inspect-limit",
            "130",
            "--select-limit",
            "100",
            "--metadata-mode",
            "off",
            "--top-reports",
            "0",
        ]
    )
    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)
    cases = [
        case_result(module, index=index, query_id=f"query-{index}", score=0)
        for index in range(1, 111)
    ]
    for case in cases[:5]:
        case.collection_status = "failed"
        case.analysis_status = "not_started"
        case.failure_category = "collection"
    for case in cases[5:105]:
        case.collection_status = "ok"
        case.analysis_status = "ok"

    retained, warning = module.retain_backfilled_case_results(config, cases)

    assert len(retained) == 100
    assert sum(1 for case in retained if case.analysis_status == "ok") == 100
    assert warning is not None
    assert "retained 100 successfully analyzed cases" in warning


def test_retain_backfilled_case_results_keeps_all_when_target_not_met(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        [
            "--out",
            str(batch_dir(tmp_path)),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--cm-inspect-limit",
            "130",
            "--select-limit",
            "100",
            "--metadata-mode",
            "off",
            "--top-reports",
            "0",
        ]
    )
    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)
    cases = [
        case_result(module, index=index, query_id=f"query-{index}", score=0)
        for index in range(1, 111)
    ]
    for case in cases[:15]:
        case.collection_status = "failed"
        case.analysis_status = "not_started"
        case.failure_category = "collection"
    for case in cases[15:]:
        case.collection_status = "ok"
        case.analysis_status = "ok"

    retained, warning = module.retain_backfilled_case_results(config, cases)

    assert retained == cases
    assert warning is not None
    assert "retained all 110 processed candidates with 95 successful analyses" in warning


def test_candidate_discovery_respects_bounded_limit_and_triage_profile_limit(monkeypatch):
    module = load_batch_module()
    calls = []

    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id=f"q{i}:id", duration_ms=i * 1000, query_type="QUERY", statement="SELECT 1"
        )
        for i in range(1, 6)
    ]

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        calls.append((filters.limit, page_token))
        if page_token == "5":
            return module.cm_profiles.CMQueryPage(items=[])
        return module.cm_profiles.CMQueryPage(items=summaries)

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    args = module.parse_args(
        [
            "--out",
            "/tmp/query-doctor-batch",
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--cm-inspect-limit",
            "5",
            "--triage-profile-limit",
            "5",
            "--min-duration-sec",
            "0",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=REPO_DIR, repo_root=REPO_DIR)

    discovery = module.discover_candidates(config, env=auth_env())

    assert calls == [(20, None)]
    assert config.triage_profile_limit == 5
    assert sum(1 for item in discovery.candidates if item.selected) == 5
    assert discovery.scan_too_broad is False


def test_candidate_discovery_candidate_limit_keeps_top_bounded_results(monkeypatch, tmp_path):
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id=f"q{i}:id", duration_ms=i * 1000, query_type="QUERY", statement="SELECT 1"
        )
        for i in range(1, 7)
    ]

    monkeypatch.setattr(module, "make_cm_http_client", lambda config, env: object())
    monkeypatch.setattr(
        module.cm_profiles,
        "fetch_cm_query_summary_page",
        lambda client, filters, page_token: module.cm_profiles.CMQueryPage(items=summaries),
    )

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "6",
        "--triage-profile-limit",
        "5",
        "--min-duration-sec",
        "0",
        "--order",
        "duration-desc",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    selected = [item.summary.query_id for item in discovery.candidates if item.selected]
    assert selected == ["q2:id", "q3:id", "q4:id", "q5:id", "q6:id"]
    assert discovery.scan_too_broad is False
    assert "selected the top 5 by scan order" in discovery.warnings[-1]


def test_batch_recent_candidate_limit_ignores_filtered_metadata_statements(monkeypatch, tmp_path):
    module = load_batch_module()

    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id=f"show-{index}:id",
            duration_ms=1000,
            query_type="QUERY",
            statement="SHOW CREATE TABLE db.table_name",
        )
        for index in range(8)
    ] + [
        module.cm_profiles.CMQuerySummary(
            query_id=f"select-{index}:id",
            duration_ms=1000,
            query_type="QUERY",
            statement="SELECT 1",
        )
        for index in range(4)
    ]

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        return module.cm_profiles.CMQueryPage(items=summaries)

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "5",
        "--triage-profile-limit",
        "5",
        "--no-min-duration-filter",
        "--query-type",
        "QUERY",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert discovery.scan_too_broad is False
    assert discovery.summaries_inspected == 12
    assert sum(1 for item in discovery.candidates if item.selected) == 4
    assert (
        sum(
            1
            for item in discovery.candidates
            if item.reason == "excluded: admin or metadata statement"
        )
        == 8
    )


def test_batch_recent_raw_scan_cap_marks_discovery_too_broad_even_when_candidates_are_filtered(
    monkeypatch, tmp_path
):
    module = load_batch_module()

    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id=f"show-{index}:id",
            duration_ms=1000,
            query_type="QUERY",
            statement="SHOW CREATE TABLE db.table_name",
        )
        for index in range(20)
    ]

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        return module.cm_profiles.CMQueryPage(items=summaries)

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "5",
        "--triage-profile-limit",
        "5",
        "--no-min-duration-filter",
        "--query-type",
        "QUERY",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert len(discovery.candidates) == 20
    assert sum(1 for candidate in discovery.candidates if candidate.selected) == 0
    assert {candidate.reason for candidate in discovery.candidates} == {
        "excluded: admin or metadata statement"
    }
    assert discovery.summaries_inspected == 20
    assert discovery.scan_too_broad is True
    assert discovery.raw_summary_scan_cap_hit is True
    assert "CM summary raw scan cap was reached" in discovery.warnings[-1]

    summary = build_summary_for_test_cases(
        module,
        config,
        [],
        discovery=discovery,
        warnings=discovery.warnings,
        discovery_seconds=1.0,
        total_seconds=1.2,
    )
    assert summary["cm_summary_raw_scan_cap_hit"] is True
    assert summary["scan_too_broad"] is True
    assert summary["candidate_exclusion_count"] == 20
    assert summary["candidate_reason_counts"] == {"excluded: admin or metadata statement": 20}


def test_batch_recent_default_web_filters_do_not_add_hidden_constraints(tmp_path):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(
        json.dumps(
            {
                "cm_url": "https://config-cm.example.net:7183",
                "cluster": "config_cluster",
                "service": "config_impala",
                "out": str(tmp_path / "query-doctor-config-batch"),
                "user": "generic_collector_user",
                "pool": "root.generic",
                "status": "failed",
            }
        ),
        encoding="utf-8",
    )
    now = module.cm_profiles.datetime(2026, 5, 2, 12, 0, 0, tzinfo=module.cm_profiles.timezone.utc)

    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--recent-window-minutes",
            "120",
            "--cm-inspect-limit",
            "5000",
            "--triage-profile-limit",
            "200",
            "--no-min-duration-filter",
            "--query-type",
            "QUERY",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)
    filters = module.build_recent_filters(config)
    path, params = module.cm_profiles.build_cm_query_summary_page_request(filters, now=now)

    assert path.endswith("/impalaQueries")
    assert config.min_duration_sec is None
    assert config.max_duration_sec is None
    assert config.user is None
    assert config.pool is None
    assert config.include_failed is False
    assert config.include_running is False
    assert filters.query_type == "QUERY"
    assert params == {
        "from": "2026-05-02T10:00:00Z",
        "to": "2026-05-02T12:00:00Z",
        "limit": 1000,
        "filter": 'query_type = "QUERY" AND executing = false',
    }


def test_batch_recent_explicit_time_window_overrides_relative_window(tmp_path):
    module = load_batch_module()
    args = module.parse_args(
        base_args(tmp_path)
        + [
            "--recent-window-minutes",
            "120",
            "--from-time",
            "2026-05-02T21:00:00Z",
            "--to-time",
            "2026-05-02T22:00:00Z",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=REPO_DIR, repo_root=REPO_DIR)
    filters = module.build_recent_filters(config)
    path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)

    assert path.endswith("/impalaQueries")
    assert filters.since_minutes is None
    assert params["from"] == "2026-05-02T21:00:00Z"
    assert params["to"] == "2026-05-02T22:00:00Z"


def test_batch_recent_discovery_paginates_large_cm_summary_scan(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        calls.append((filters.page_size, page_token))
        offset = int(page_token or 0)
        count = 1000 if offset == 0 else 3
        return module.cm_profiles.CMQueryPage(
            items=[
                module.cm_profiles.CMQuerySummary(
                    query_id=f"q{offset + index}:id",
                    duration_ms=1000 + index,
                    query_type="QUERY",
                    statement="SELECT 1",
                )
                for index in range(count)
            ]
        )

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "1500",
        "--triage-profile-limit",
        "1500",
        "--no-min-duration-filter",
        "--query-type",
        "QUERY",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert calls == [(1000, None), (1000, "1000")]
    assert len(discovery.candidates) == 1003
    assert sum(1 for item in discovery.candidates if item.selected) == 1003


def test_batch_recent_discovery_time_shards_after_cm_scan_limit_warning(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_summary(query_id, duration_ms):
        return module.cm_profiles.CMQuerySummary(
            query_id=query_id,
            duration_ms=duration_ms,
            query_type="QUERY",
            statement="SELECT 1",
        )

    def fake_fetch_page(client, filters, page_token):
        _path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)
        calls.append(params)
        if (
            filters.from_time == "2026-05-02T00:00:00Z"
            and filters.to_time == "2026-05-02T02:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(
                items=[fake_summary("partial:id", 3000)],
                warnings=[
                    "Impala query scan limit reached. Last end time considered is 2026-05-02T01:59:00Z"
                ],
            )
        if (
            filters.from_time == "2026-05-02T01:00:00Z"
            and filters.to_time == "2026-05-02T02:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(
                items=[
                    fake_summary("newest:id", 5000),
                    fake_summary("duplicate:id", 4000),
                ],
            )
        if (
            filters.from_time == "2026-05-02T00:00:00Z"
            and filters.to_time == "2026-05-02T01:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(
                items=[
                    fake_summary("oldest:id", 2000),
                    fake_summary("duplicate:id", 4000),
                ],
            )
        return module.cm_profiles.CMQueryPage(items=[])

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "5",
        "--triage-profile-limit",
        "5",
        "--from-time",
        "2026-05-02T00:00:00Z",
        "--to-time",
        "2026-05-02T02:00:00Z",
        "--no-min-duration-filter",
        "--query-type",
        "QUERY",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert discovery.time_sharded is True
    assert discovery.time_shard_count == 2
    assert discovery.time_shard_minutes == 60
    assert discovery.time_shard_min_minutes == 15
    assert discovery.time_shard_scan_limit_warning_count == 0
    assert discovery.summaries_inspected == 3
    assert [candidate.summary.query_id for candidate in discovery.candidates] == [
        "newest:id",
        "duplicate:id",
        "oldest:id",
    ]
    assert calls[0]["from"] == "2026-05-02T00:00:00Z"
    assert calls[0]["to"] == "2026-05-02T02:00:00Z"
    assert calls[1]["from"] == "2026-05-02T01:00:00Z"
    assert calls[1]["to"] == "2026-05-02T02:00:00Z"
    assert calls[2]["from"] == "2026-05-02T00:00:00Z"
    assert calls[2]["to"] == "2026-05-02T01:00:00Z"

    summary = build_summary_for_test_cases(
        module,
        config,
        [],
        discovery=discovery,
        warnings=discovery.warnings,
        discovery_seconds=1.0,
        total_seconds=1.2,
    )
    assert summary["time_sharded"] is True
    assert summary["time_shard_count"] == 2
    assert summary["time_shard_minutes"] == 60
    assert summary["time_shard_min_minutes"] == 15


def test_batch_recent_discovery_splits_scan_limited_time_shards(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_summary(query_id, duration_ms):
        return module.cm_profiles.CMQuerySummary(
            query_id=query_id,
            duration_ms=duration_ms,
            query_type="QUERY",
            statement="SELECT 1",
        )

    def fake_fetch_page(client, filters, page_token):
        _path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)
        calls.append(params)
        if (
            filters.from_time == "2026-05-02T00:00:00Z"
            and filters.to_time == "2026-05-02T02:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(
                items=[fake_summary("broad-partial:id", 6000)],
                warnings=["Impala query scan limit reached."],
            )
        if (
            filters.from_time == "2026-05-02T01:00:00Z"
            and filters.to_time == "2026-05-02T02:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(items=[fake_summary("newest-hour:id", 5000)])
        if (
            filters.from_time == "2026-05-02T00:00:00Z"
            and filters.to_time == "2026-05-02T01:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(
                items=[fake_summary("hour-partial:id", 4000)],
                warnings=["Impala query scan limit reached."],
            )
        if (
            filters.from_time == "2026-05-02T00:30:00Z"
            and filters.to_time == "2026-05-02T01:00:00Z"
        ):
            return module.cm_profiles.CMQueryPage(items=[fake_summary("oldest-half-new:id", 3000)])
        if (
            filters.from_time == "2026-05-02T00:00:00Z"
            and filters.to_time == "2026-05-02T00:30:00Z"
        ):
            return module.cm_profiles.CMQueryPage(items=[fake_summary("oldest-half-old:id", 2000)])
        return module.cm_profiles.CMQueryPage(items=[])

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "5",
        "--triage-profile-limit",
        "5",
        "--from-time",
        "2026-05-02T00:00:00Z",
        "--to-time",
        "2026-05-02T02:00:00Z",
        "--no-min-duration-filter",
        "--query-type",
        "QUERY",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert discovery.time_sharded is True
    assert discovery.time_shard_count == 4
    assert discovery.time_shard_scan_limit_warning_count == 1
    assert [candidate.summary.query_id for candidate in discovery.candidates] == [
        "newest-hour:id",
        "oldest-half-new:id",
        "oldest-half-old:id",
    ]
    assert [call["from"] for call in calls] == [
        "2026-05-02T00:00:00Z",
        "2026-05-02T01:00:00Z",
        "2026-05-02T00:00:00Z",
        "2026-05-02T00:30:00Z",
        "2026-05-02T00:00:00Z",
    ]


def test_batch_recent_discovery_stops_when_cm_window_exceeds_limit(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        calls.append((filters.page_size, page_token))
        return module.cm_profiles.CMQueryPage(
            items=[
                module.cm_profiles.CMQuerySummary(
                    query_id=f"q{index}:id",
                    duration_ms=1000 + index,
                    query_type="QUERY",
                    statement="SELECT 1",
                )
                for index in range(1000)
            ]
        )

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_query_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "5000",
        "--triage-profile-limit",
        "5000",
        "--no-min-duration-filter",
        "--query-type",
        "QUERY",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert calls[-1] == (1000, "19000")
    assert len(discovery.candidates) == 20000
    assert sum(1 for candidate in discovery.candidates if candidate.selected) == 5000
    assert discovery.summaries_inspected == 20000
    assert discovery.scan_too_broad is True
    assert "CM summary raw scan cap was reached" in discovery.warnings[-1]
    summary = build_summary_for_test_cases(
        module,
        config,
        [],
        discovery=discovery,
        warnings=discovery.warnings,
        discovery_seconds=1.0,
        total_seconds=1.2,
    )
    assert summary["summaries_inspected"] == 20000
    assert summary["scan_too_broad"] is True
    assert summary["cm_summary_safety_cap_hit"] is True
    assert summary["candidate_exclusion_count"] == 20000
    assert summary["candidate_reason_counts"] == {
        "eligible but not selected because recent-select limit was reached": 15000,
        "selected: SELECT-like user query": 5000,
    }


def test_select_limit_remains_deprecated_alias_for_triage_profile_limit(tmp_path):
    module = load_batch_module()
    config = build_cm_config(module, tmp_path, "--select-limit", "3")

    assert config.triage_profile_limit == 3


def test_server_side_filter_expression_is_used_when_existing_cm_filter_supports_duration():
    module = load_batch_module()
    filters = module.cm_profiles.CMQueryFilters(
        cluster="cluster",
        service="impala",
        since_hours=1,
        since_minutes=60,
        limit=10,
        min_duration_sec=60,
        server_duration_filter=True,
    )

    path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)

    assert path.endswith("/impalaQueries")
    assert params["filter"] == "queryDuration > 60s"
    assert (
        module.classify_duration_filter_mode(params["filter"], min_duration_sec=60) == "server-side"
    )


def test_batch_discovery_passes_duration_filter_before_inspect_limit(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)
        calls.append((path, params, page_token))
        return module.cm_profiles.CMQueryPage(
            items=[
                module.cm_profiles.CMQuerySummary(
                    query_id="long:id",
                    duration_ms=12000,
                    query_type="QUERY",
                    statement="SELECT 1",
                )
            ]
        )

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "1000",
        "--select-limit",
        "200",
        "--min-duration-sec",
        "10.001",
        "--pool",
        "root.analytics",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert discovery.duration_filter_mode == "server-side"
    assert calls[0][1]["limit"] == 1000
    assert (
        calls[0][1]["filter"]
        == 'queryDuration > 11s AND pool = "root.analytics" AND executing = false'
    )


def test_batch_discovery_passes_pool_filter_without_duration_before_inspect_limit(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)
        calls.append((path, params, page_token))
        return module.cm_profiles.CMQueryPage(
            items=[
                module.cm_profiles.CMQuerySummary(
                    query_id="pool-match:id",
                    duration_ms=12000,
                    query_type="QUERY",
                    statement="SELECT 1",
                    pool="root.analytics",
                )
            ]
        )

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_config(
        module,
        tmp_path,
        "--cm-inspect-limit",
        "1000",
        "--select-limit",
        "200",
        "--no-min-duration-filter",
        "--pool",
        "root.analytics",
    )

    discovery = module.discover_candidates(config, env=auth_env())

    assert discovery.duration_filter_mode == "none"
    assert calls[0][1]["limit"] == 1000
    assert calls[0][1]["filter"] == 'pool = "root.analytics" AND executing = false'
    assert sum(1 for item in discovery.candidates if item.selected) == 1


def test_batch_discovery_uses_executing_filter_for_running_only(tmp_path):
    module = load_batch_module()
    config = build_cm_config(
        module,
        tmp_path,
        "--only-running",
        "--include-running",
        "--no-min-duration-filter",
    )
    filters = module.build_recent_filters(config)

    _path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)

    assert params["filter"] == "executing = true"


def test_batch_discovery_omits_executing_filter_when_running_is_included(tmp_path):
    module = load_batch_module()
    config = build_cm_config(
        module,
        tmp_path,
        "--include-running",
        "--no-min-duration-filter",
    )
    filters = module.build_recent_filters(config)

    _path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)

    assert "filter" not in params


def test_duration_filter_reports_client_side_when_no_predicate(monkeypatch):
    module = load_batch_module()

    monkeypatch.setattr(
        module.cm_profiles, "build_cm_query_filter_expression", lambda filters: None
    )

    assert (
        module.classify_duration_filter_mode(
            module.cm_profiles.build_cm_query_filter_expression(
                module.cm_profiles.CMQueryFilters(
                    cluster="cluster",
                    service="impala",
                    since_hours=1,
                    limit=10,
                    min_duration_sec=60,
                )
            ),
            min_duration_sec=60,
        )
        == "client-side"
    )


def test_server_side_zero_results_falls_back_to_client_side_bounded_discovery(
    monkeypatch, tmp_path
):
    module = load_batch_module()
    calls = []

    def fake_client(config, env):
        return object()

    def fake_fetch_page(client, filters, page_token):
        path, params = module.cm_profiles.build_cm_query_summary_page_request(filters)
        calls.append(params)
        if "queryDuration" in params.get("filter", ""):
            return module.cm_profiles.CMQueryPage(items=[])
        return module.cm_profiles.CMQueryPage(
            items=[
                module.cm_profiles.CMQuerySummary(
                    query_id="short:id",
                    duration_ms=1000,
                    query_type="QUERY",
                    statement="SELECT 1",
                ),
                module.cm_profiles.CMQuerySummary(
                    query_id="long:id",
                    duration_ms=11000,
                    query_type="QUERY",
                    statement="SELECT 1",
                ),
            ]
        )

    monkeypatch.setattr(module, "make_cm_http_client", fake_client)
    monkeypatch.setattr(module.cm_profiles, "fetch_cm_query_summary_page", fake_fetch_page)

    config = build_cm_config(module, tmp_path, "--min-duration-sec", "10")

    discovery = module.discover_candidates(config, env=auth_env())

    assert discovery.duration_filter_mode == "server-side-fallback-client-side"
    assert "filter" in calls[0]
    assert calls[1]["filter"] == "executing = false"
    assert [(item.summary.query_id, item.selected) for item in discovery.candidates] == [
        ("short:id", False),
        ("long:id", True),
    ]


def test_client_side_duration_backstop_remains_applied():
    module = load_batch_module()
    summaries = [
        module.cm_profiles.CMQuerySummary(
            query_id="short:id",
            duration_ms=1000,
            query_type="QUERY",
            statement="SELECT 1",
        ),
        module.cm_profiles.CMQuerySummary(
            query_id="long:id",
            duration_ms=61000,
            query_type="QUERY",
            statement="SELECT 1",
        ),
    ]

    candidates = module.cm_profiles.select_recent_query_candidates(
        summaries,
        select_limit=2,
        min_duration_sec=60,
        order="duration-desc",
    )

    assert [(item.summary.query_id, item.selected) for item in candidates] == [
        ("short:id", False),
        ("long:id", True),
    ]
    assert candidates[0].reason == "excluded: duration below recent-min-duration-sec"


def test_discover_only_does_not_collect_profiles(tmp_path, monkeypatch, capsys):
    module = load_batch_module()
    selected = candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([selected], [], "client-side", None),
    )

    def fail_subprocess(*args, **kwargs):
        raise AssertionError("discover-only must not run subprocesses")

    monkeypatch.setattr(module, "run_subprocess", fail_subprocess)

    result = module.main(base_args(tmp_path) + ["--discover-only"], env=auth_env())

    assert result == 0
    payload = read_batch_summary(tmp_path)
    markdown = read_batch_summary_markdown(tmp_path)
    assert payload["selected_count"] == 1
    assert_non_negative_number(payload["total_seconds"])
    assert_non_negative_number(payload["discovery_seconds"])
    assert payload["cases"][0]["collection_status"] == "not_started"
    assert payload["cases"][0]["cm_collect_seconds"] is None
    assert payload["cases"][0]["analysis_seconds"] is None
    assert payload["cases"][0]["report_seconds"] is None
    output = capsys.readouterr().out
    assert "[batch] discovery:" in output
    assert "[batch] total:" in output
    assert "total seconds" in markdown
    assert "timings sec" in markdown


def test_selected_query_ids_are_collected_individually_and_pipeline_uses_stop_after_analysis(
    tmp_path, monkeypatch, capsys
):
    module = load_batch_module()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]
    calls = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(selected, [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--collect-cm-timeseries",
            "--cm-metrics-profile",
            "cm6.2.1",
            "--cm-timeseries-padding-sec",
            "180",
            "--max-timeseries-bytes",
            "3145728",
            "--max-timeseries-points",
            "4000",
            "--cm-timeseries-top-limit",
            "1",
        ],
        env=auth_env(),
    )

    assert result == 0
    collect_calls = [cmd for cmd in calls if command_uses_role(cmd, "collect_cm")]
    pipeline_calls = [cmd for cmd in calls if command_uses_role(cmd, "pipeline")]
    initial_collect_calls = [cmd for cmd in collect_calls if "--no-collect-cm-timeseries" in cmd]
    metrics_collect_calls = [cmd for cmd in collect_calls if "--collect-cm-timeseries" in cmd]
    assert [cmd[cmd.index("--query-id") + 1] for cmd in initial_collect_calls] == [
        "aaaaaaaaaaaaaaaa:0000000000000001",
        "bbbbbbbbbbbbbbbb:0000000000000002",
    ]
    assert all("--limit" in cmd and cmd[cmd.index("--limit") + 1] == "1" for cmd in collect_calls)
    assert [cmd[cmd.index("--query-id") + 1] for cmd in metrics_collect_calls] == [
        "bbbbbbbbbbbbbbbb:0000000000000002"
    ]
    assert all(cmd[cmd.index("--cm-metrics-profile") + 1] == "cm6" for cmd in metrics_collect_calls)
    assert all(
        cmd[cmd.index("--cm-timeseries-padding-sec") + 1] == "180" for cmd in metrics_collect_calls
    )
    assert all(
        cmd[cmd.index("--max-timeseries-bytes") + 1] == "3145728" for cmd in metrics_collect_calls
    )
    assert all(
        cmd[cmd.index("--max-timeseries-points") + 1] == "4000" for cmd in metrics_collect_calls
    )
    assert all("--stop-after-analysis" in cmd for cmd in pipeline_calls)
    assert all(
        "--metadata-failure-policy" in cmd
        and cmd[cmd.index("--metadata-failure-policy") + 1] == "continue"
        for cmd in pipeline_calls
    )
    payload = read_batch_summary(tmp_path)
    for case in payload["cases"]:
        assert_non_negative_number(case["cm_collect_seconds"])
        assert_non_negative_number(case["analysis_seconds"])
        assert case["report_seconds"] is None
        assert_non_negative_number(case["total_seconds"])
    output = capsys.readouterr().out
    assert "[batch] case-001 collection:" in output
    assert "[batch] case-001 analyzer triage:" in output
    assert not (batch_dir(tmp_path) / "progress.jsonl").exists()


def test_batch_collects_cm_events_once_for_explicit_scan_window(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)]
    calls = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(selected, [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        if command_uses_role(cmd, "cm_events"):
            cluster_context = Path(cmd[cmd.index("--cluster-context-json") + 1])
            cluster_context.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "product": "cluster_doctor",
                        "status": "degraded_service_candidate",
                        "available": True,
                        "signal_counts": {"metastore_error_event": 1},
                        "signals": [],
                        "sources": [],
                        "limitations": [],
                        "next_checks": [],
                        "guardrail": "Cluster context is not standalone root-cause proof.",
                    }
                ),
                encoding="utf-8",
            )
        elif command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--from-time",
            "2026-05-06T09:00:00Z",
            "--to-time",
            "2026-05-06T10:00:00Z",
            "--collect-cm-events",
            "--cm-events-max-events",
            "5",
            "--progress-jsonl",
            str(batch_dir(tmp_path) / "progress.jsonl"),
        ],
        env=auth_env(),
    )

    assert result == 0
    event_calls = [cmd for cmd in calls if command_uses_role(cmd, "cm_events")]
    assert len(event_calls) == 1
    event_cmd = event_calls[0]
    assert event_cmd[event_cmd.index("--from-time") + 1] == "2026-05-06T09:00:00Z"
    assert event_cmd[event_cmd.index("--to-time") + 1] == "2026-05-06T10:00:00Z"
    assert event_cmd[event_cmd.index("--max-events") + 1] == "5"
    assert "--redact-identifiers" not in event_cmd
    assert "--no-redact-hosts" not in event_cmd
    payload = read_batch_summary(tmp_path)
    assert payload["collect_cm_events"] is True
    assert payload["cm_events_max_events"] == 5
    assert payload["cluster_context"]["status"] == "degraded_service_candidate"
    assert payload["cluster_context"]["signal_counts"] == {"metastore_error_event": 1}
    events = read_jsonl(batch_dir(tmp_path) / "progress.jsonl")
    assert any(event["stage"] == "cm_events" and event["status"] == "done" for event in events)


def test_metadata_refresh_runs_after_ranking_for_top_triage_cases(tmp_path, monkeypatch):
    module = load_batch_module()
    allow_metadata_auth_preflight(monkeypatch)
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]
    pipeline_modes = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(
            selected, [], "server-side", "queryDuration > 10s"
        ),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            metadata_mode = cmd[cmd.index("--metadata-mode") + 1]
            if metadata_mode != "off":
                assert cmd[cmd.index("--metadata-kerberos-service-name") + 1] == "hive"
            case_dir = Path(command_args(cmd, "pipeline")[0])
            pipeline_modes.append((case_dir.name, metadata_mode))
            facts = (
                promotable_suspicious_facts() if "bbbbbbbb" in str(case_dir) else healthy_facts()
            )
            (case_dir / "analysis_facts.md").write_text(facts, encoding="utf-8")
            if metadata_mode != "off":
                (case_dir / "impala_context.json").write_text(
                    json.dumps({"tables": ["db.table"], "results": [{"status": "ok"}]}),
                    encoding="utf-8",
                )
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-impala-shell",
            "impala-shell",
            "--metadata-kerberos-service-name",
            "hive",
            "--metadata-top-limit",
            "1",
        ],
        env={**auth_env(), "KRB5CCNAME": "FILE:/tmp/krb5cc_test"},
    )

    assert result == 0
    analyzer_passes = [item for item in pipeline_modes if item[1] == "off"]
    metadata_passes = [item for item in pipeline_modes if item[1] == "on"]
    assert len(analyzer_passes) == 2
    assert len(metadata_passes) == 1
    assert "bbbbbbbbbbbbbbbb_0000000000000002" in metadata_passes[0][0]
    payload = read_batch_summary(tmp_path)
    assert payload["triage_profile_limit"] == 2
    assert payload["select_limit"] == 2
    assert payload["metadata_top_limit"] == 1
    refreshed = [case for case in payload["cases"] if case["metadata_refreshed"]]
    not_requested = [
        case for case in payload["cases"] if case["metadata_status"] == "not_requested"
    ]
    assert len(refreshed) == 1
    assert refreshed[0]["query_id"] == "bbbbbbbbbbbbbbbb:0000000000000002"
    assert refreshed[0]["triage_rank"] == 1
    assert not_requested


def test_cm_timeseries_refresh_parallelizes_top_cases(tmp_path, monkeypatch):
    module = load_batch_module()
    from query_doctor.recent import case_processing

    config = build_cm_config(
        module,
        tmp_path,
        "--collect-cm-timeseries",
        "--cm-timeseries-top-limit",
        "4",
        "--cm-jobs",
        "4",
    )
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    cases = []
    for index in range(1, 5):
        wrapper_dir = batch_dir(tmp_path) / "cases" / f"case-{index:03d}"
        actual_case_dir = wrapper_dir / f"case_{index:03d}"
        actual_case_dir.mkdir(parents=True)
        cases.append(
            module.CaseResult(
                index=index,
                query_id=f"aaaaaaaaaaaaaaaa:{index:016d}",
                duration_sec=60.0 + index,
                user=None,
                pool=None,
                query_type="QUERY",
                sql_verb="SELECT",
                wrapper_dir=wrapper_dir,
                actual_case_dir=actual_case_dir,
                collection_status="ok",
                analysis_status="ok",
                score=100 - index,
            )
        )
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_refresh(config, case, *, env, repo_root, progress):
        nonlocal active, max_active
        progress.emit(
            stage="cm_timeseries_refresh",
            case_id=f"case-{case.index:03d}",
            status="started",
        )
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        progress.emit(
            stage="cm_timeseries_refresh",
            case_id=f"case-{case.index:03d}",
            status="done",
        )
        return case

    monkeypatch.setattr(case_processing, "refresh_case_cm_timeseries", fake_refresh)
    progress = module.ProgressWriter(progress_path)
    try:
        case_processing.refresh_top_cm_timeseries(
            config,
            cases,
            env=auth_env(),
            repo_root=REPO_DIR,
            progress=progress,
        )
    finally:
        progress.close()

    assert max_active > 1
    events = read_jsonl(progress_path)
    started = next(
        event
        for event in events
        if event["stage"] == "cm_timeseries_refresh"
        and event["status"] == "started"
        and "case_id" not in event
    )
    assert started["total"] == 4
    assert started["jobs"] == 4


def test_cm_timeseries_refresh_timeout_emits_safe_failure(tmp_path, monkeypatch):
    module = load_batch_module()
    from query_doctor.recent import case_processing

    config = build_cm_config(
        module,
        tmp_path,
        "--collect-cm-timeseries",
        "--cm-timeseries-top-limit",
        "1",
    )
    wrapper_dir = batch_dir(tmp_path) / "cases" / "case-001"
    actual_case_dir = wrapper_dir / "case_001"
    actual_case_dir.mkdir(parents=True)
    case = module.CaseResult(
        index=1,
        query_id="aaaaaaaaaaaaaaaa:0000000000000001",
        duration_sec=60.0,
        user=None,
        pool=None,
        query_type="QUERY",
        sql_verb="SELECT",
        wrapper_dir=wrapper_dir,
        actual_case_dir=actual_case_dir,
        collection_status="ok",
        analysis_status="ok",
        score=50,
    )

    def fake_run(cmd, cwd, env):
        assert command_uses_role(cmd, "collect_cm")
        assert "--collect-cm-timeseries" in cmd
        return completed(case_processing.SUBPROCESS_TIMEOUT_RETURN_CODE)

    monkeypatch.setattr(case_processing, "run_subprocess", fake_run)
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    progress = module.ProgressWriter(progress_path)
    try:
        case_processing.refresh_case_cm_timeseries(
            config,
            case,
            env=auth_env(),
            repo_root=REPO_DIR,
            progress=progress,
        )
    finally:
        progress.close()

    events = read_jsonl(progress_path)
    failed = next(
        event
        for event in events
        if event["stage"] == "cm_timeseries_refresh" and event["status"] == "failed"
    )
    assert failed["case_id"] == "case-001"
    assert failed["reason"] == "runtime_metrics_refresh_timeout"
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "aaaaaaaaaaaaaaaa:0000000000000001" not in progress_text
    assert str(actual_case_dir) not in progress_text


@pytest.mark.parametrize(
    ("context_names", "expected_names"),
    [
        (
            ("runtime_metrics_context.json", "cm_timeseries_context.json"),
            ("runtime_metrics_context.json", "cm_timeseries_context.json"),
        ),
        (
            ("cm_timeseries_context.json",),
            ("cm_timeseries_context.json",),
        ),
    ],
)
def test_cm_timeseries_refresh_copies_available_runtime_contexts(
    tmp_path, monkeypatch, context_names, expected_names
):
    module = load_batch_module()
    from query_doctor.recent import case_processing

    args = module.parse_args(
        base_args(tmp_path)
        + [
            "--collect-cm-timeseries",
            "--cm-timeseries-top-limit",
            "1",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR)
    wrapper_dir = batch_dir(tmp_path) / "cases" / "case-001"
    actual_case_dir = wrapper_dir / "case_001"
    actual_case_dir.mkdir(parents=True)
    case = module.CaseResult(
        index=1,
        query_id="aaaaaaaaaaaaaaaa:0000000000000001",
        duration_sec=60.0,
        user=None,
        pool=None,
        query_type="QUERY",
        sql_verb="SELECT",
        wrapper_dir=wrapper_dir,
        actual_case_dir=actual_case_dir,
        collection_status="ok",
        analysis_status="ok",
        score=50,
    )
    context = {
        "available": True,
        "source": "cm_timeseries",
        "source_label": "Cloudera Manager time-series metrics",
        "queries": [],
    }

    def fake_run(cmd, cwd, env):
        assert command_uses_role(cmd, "collect_cm")
        assert "--collect-cm-timeseries" in cmd
        out_dir = Path(cmd[cmd.index("--out") + 1])
        refresh_case_dir = out_dir / "aaaaaaaaaaaaaaaa_0000000000000001"
        write_case(refresh_case_dir, healthy_facts())
        for context_name in context_names:
            (refresh_case_dir / context_name).write_text(json.dumps(context), encoding="utf-8")
        return completed()

    def fake_run_analysis_pass(config, case, *, env, repo_root, metadata_mode):
        assert metadata_mode == "off"
        for expected_name in expected_names:
            assert (case.actual_case_dir / expected_name).exists()
        for unexpected_name in {
            "runtime_metrics_context.json",
            "cm_timeseries_context.json",
        } - set(expected_names):
            assert not (case.actual_case_dir / unexpected_name).exists()
        case.analysis_status = "ok"

    monkeypatch.setattr(case_processing, "run_subprocess", fake_run)
    monkeypatch.setattr(case_processing, "run_analysis_pass", fake_run_analysis_pass)
    monkeypatch.setattr(case_processing, "score_case", lambda case: setattr(case, "score", 42))
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    progress = module.ProgressWriter(progress_path)
    try:
        refreshed = case_processing.refresh_case_cm_timeseries(
            config,
            case,
            env=auth_env(),
            repo_root=REPO_DIR,
            progress=progress,
        )
    finally:
        progress.close()

    assert refreshed.score == 42
    for expected_name in expected_names:
        copied_context = json.loads((actual_case_dir / expected_name).read_text(encoding="utf-8"))
        assert copied_context == context
    events = read_jsonl(progress_path)
    assert any(
        event["stage"] == "cm_timeseries_refresh" and event["status"] == "done" for event in events
    )


def test_metadata_mode_off_skips_metadata_refresh(tmp_path, monkeypatch):
    module = load_batch_module()
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    selected = [candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)]
    pipeline_modes = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(
            selected, [], "server-side", "queryDuration > 10s"
        ),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), suspicious_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            pipeline_modes.append(cmd[cmd.index("--metadata-mode") + 1])
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(suspicious_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "off",
            "--metadata-top-limit",
            "1",
            "--progress-jsonl",
            str(progress_path),
        ],
        env=auth_env(),
    )

    assert result == 0
    assert pipeline_modes == ["off"]
    payload = read_batch_summary(tmp_path)
    assert payload["cases"][0]["metadata_refreshed"] is False
    assert payload["cases"][0]["metadata_status"] == "not_requested"
    events = read_jsonl(progress_path)
    assert any(
        event["stage"] == "metadata_refresh"
        and event["status"] == "skipped"
        and event["reason"] == "metadata disabled"
        for event in events
    )


def test_metadata_top_limit_zero_emits_skipped_progress(tmp_path, monkeypatch):
    module = load_batch_module()
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    selected = [candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)]

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(
            selected, [], "server-side", "queryDuration > 10s"
        ),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), suspicious_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(suspicious_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-impala-shell",
            "impala-shell",
            "--metadata-top-limit",
            "0",
            "--progress-jsonl",
            str(progress_path),
        ],
        env={**auth_env(), "KRB5CCNAME": "FILE:/tmp/krb5cc_test"},
    )

    assert result == 0
    events = read_jsonl(progress_path)
    assert any(
        event["stage"] == "metadata_refresh"
        and event["status"] == "skipped"
        and event["reason"] == "metadata_top_limit=0"
        for event in events
    )
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "aaaaaaaaaaaaaaaa" not in progress_text
    assert "SELECT" not in progress_text


def test_no_eligible_cases_emits_skipped_metadata_refresh_progress(tmp_path, monkeypatch):
    module = load_batch_module()
    allow_metadata_auth_preflight(monkeypatch)
    progress_path = batch_dir(tmp_path) / "progress.jsonl"

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([], [], "server-side", "queryDuration > 10s"),
    )

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-impala-shell",
            "impala-shell",
            "--metadata-top-limit",
            "1",
            "--progress-jsonl",
            str(progress_path),
        ],
        env={**auth_env(), "KRB5CCNAME": "FILE:/tmp/krb5cc_test"},
    )

    assert result == 0
    events = read_jsonl(progress_path)
    assert any(
        event["stage"] == "metadata_refresh"
        and event["status"] == "skipped"
        and event["reason"] == "no eligible cases"
        for event in events
    )


@pytest.mark.parametrize(
    ("initial_env", "expected_cache"),
    [
        (auth_env(), "FILE:/tmp/krb5cc_config_cache"),
        ({**auth_env(), "KRB5CCNAME": "FILE:/tmp/krb5cc_env_cache"}, "FILE:/tmp/krb5cc_env_cache"),
    ],
)
def test_batch_recent_passes_effective_krb5ccname_to_pipeline(
    tmp_path, monkeypatch, initial_env, expected_cache
):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(
        json.dumps({"krb5ccname": "FILE:/tmp/krb5cc_config_cache"}), encoding="utf-8"
    )
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    selected = [candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)]
    pipeline_envs = []
    commands = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(selected, [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        commands.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            pipeline_envs.append(dict(env))
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--config",
            str(config_path),
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.com:21000",
            "--metadata-impala-shell",
            "impala-shell",
            "--progress-jsonl",
            str(progress_path),
        ],
        env=initial_env,
    )

    assert result == 0
    assert pipeline_envs
    assert all(env["KRB5CCNAME"] == expected_cache for env in pipeline_envs)
    for cmd in commands:
        assert expected_cache not in cmd
    summary_text = (batch_dir(tmp_path) / "batch_summary.json").read_text(encoding="utf-8")
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "krb5cc_config_cache" not in summary_text
    assert "krb5cc_env_cache" not in summary_text
    assert "krb5cc_config_cache" not in progress_text
    assert "krb5cc_env_cache" not in progress_text


@pytest.mark.parametrize("value", [123, "", "FILE:/tmp/krb5cc_bad\ncache"])
def test_local_config_rejects_invalid_krb5ccname(tmp_path, value):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(json.dumps({"krb5ccname": value}), encoding="utf-8")

    with pytest.raises(module.cm_profiles.ConfigError):
        module.cm_profiles.load_local_config(str(config_path), cwd=tmp_path)


def test_local_config_accepts_krb5ccname_alias_and_still_rejects_secrets(tmp_path):
    module = load_batch_module()
    config_path = tmp_path / "cm-config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata_krb5ccname": "FILE:/tmp/krb5cc_alias_cache",
                "metadata_coordinator": "impala-config.example.net:21000",
                "metadata_auth": "kerberos",
                "metadata_protocol": "hs2",
                "metadata_timeout_sec": 30,
                "metadata_max_tables": 5,
                "metadata_max_output_bytes": 2097152,
                "metadata_redact": True,
            }
        ),
        encoding="utf-8",
    )

    loaded = module.cm_profiles.load_local_config(str(config_path), cwd=tmp_path)
    assert loaded["krb5ccname"] == "FILE:/tmp/krb5cc_alias_cache"
    assert loaded["metadata_coordinator"] == "impala-config.example.net:21000"
    assert loaded["metadata_auth"] == "kerberos"
    assert loaded["metadata_redact"] is True

    secret_config = tmp_path / "secret-config.json"
    secret_config.write_text(json.dumps({"token": "not-allowed"}), encoding="utf-8")
    with pytest.raises(module.cm_profiles.ConfigError):
        module.cm_profiles.load_local_config(str(secret_config), cwd=tmp_path)

    auth_header_config = tmp_path / "auth-header-config.json"
    auth_header_config.write_text(json.dumps({"auth_header": "not-allowed"}), encoding="utf-8")
    with pytest.raises(module.cm_profiles.ConfigError):
        module.cm_profiles.load_local_config(str(auth_header_config), cwd=tmp_path)


def test_progress_jsonl_records_sanitized_successful_batch(tmp_path, monkeypatch):
    module = load_batch_module()
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    selected = [
        candidate(
            module,
            "aaaaaaaaaaaaaaaa:0000000000000001",
            61000,
            statement="SELECT secret_column FROM table",
        ),
        candidate(
            module,
            "bbbbbbbbbbbbbbbb:0000000000000002",
            62000,
            statement="SELECT another_secret FROM table",
        ),
    ]

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(
            selected, [], "server-side", "queryDuration > 10s"
        ),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path) + ["--progress-jsonl", str(progress_path)], env=auth_env()
    )

    assert result == 0
    events = read_jsonl(progress_path)
    assert {"stage": "discovery", "status": "started"} in events
    discovery_done = next(
        event for event in events if event["stage"] == "discovery" and event["status"] == "done"
    )
    assert discovery_done["summaries_inspected"] == 2
    assert discovery_done["candidates_selected"] == 2
    assert discovery_done["duration_filter"] == ">= 60 sec"
    assert any(
        event["stage"] == "case_processing" and event["status"] == "started" for event in events
    )
    assert any(
        event["stage"] == "case"
        and event["case_id"] == "case-001"
        and event["status"] == "collection_started"
        for event in events
    )
    assert any(
        event["stage"] == "case"
        and event["case_id"] == "case-001"
        and event["status"] == "collection_done"
        for event in events
    )
    assert any(
        event["stage"] == "case"
        and event["case_id"] == "case-001"
        and event["status"] == "analysis_done"
        for event in events
    )
    assert events[-1]["stage"] == "batch"
    assert events[-1]["status"] == "done"
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "aaaaaaaaaaaaaaaa" not in progress_text
    assert "secret_column" not in progress_text
    assert "another_secret" not in progress_text


def test_parallel_case_processing_streams_analysis_after_each_collection(tmp_path, monkeypatch):
    module = load_batch_module()
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    analysis_started = threading.Event()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(selected, [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            if query_id.startswith("bbbb"):
                analysis_started.wait(timeout=1.0)
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            if "aaaaaaaaaaaaaaaa" in str(case_dir):
                analysis_started.set()
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + ["--jobs", "2", "--cm-jobs", "2", "--progress-jsonl", str(progress_path)],
        env=auth_env(),
    )

    assert result == 0
    events = read_jsonl(progress_path)
    first_analysis_started = next(
        index
        for index, event in enumerate(events)
        if event["stage"] == "case"
        and event["case_id"] == "case-001"
        and event["status"] == "analysis_started"
    )
    second_collection_done = next(
        index
        for index, event in enumerate(events)
        if event["stage"] == "case"
        and event["case_id"] == "case-002"
        and event["status"] == "collection_done"
    )
    assert first_analysis_started < second_collection_done


def test_progress_jsonl_records_failed_case_without_secrets(tmp_path, monkeypatch):
    module = load_batch_module()
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(selected, [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            if query_id.startswith("aaaa"):
                return completed(1, stderr="HTTP 500 Server Error raw-subprocess-secret")
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path) + ["--progress-jsonl", str(progress_path)], env=auth_env()
    )

    assert result == 0
    events = read_jsonl(progress_path)
    failed = [event for event in events if event["stage"] == "case" and event["status"] == "failed"]
    assert failed
    assert failed[0]["case_id"] == "case-001"
    assert failed[0]["phase"] == "collection"
    text = progress_path.read_text(encoding="utf-8")
    assert "CM_PASSWORD" not in text
    assert "secret" not in text


def test_jobs_two_processes_cases_with_deterministic_dirs(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]
    discovery_calls = []
    calls = []

    def fake_discover(config, env):
        discovery_calls.append(config.jobs)
        return module.DiscoveryResult(selected, [], "client-side", None)

    monkeypatch.setattr(module, "discover_candidates", fake_discover)

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            write_case(out / query_id.replace(":", "_"), healthy_facts())
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            case_dir = Path(command_args(cmd, "pipeline")[0])
            (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(base_args(tmp_path) + ["--jobs", "2"], env=auth_env())

    assert result == 0
    assert discovery_calls == [2]
    collect_calls = [cmd for cmd in calls if command_uses_role(cmd, "collect_cm")]
    pipeline_calls = [cmd for cmd in calls if command_uses_role(cmd, "pipeline")]
    assert sorted(cmd[cmd.index("--query-id") + 1] for cmd in collect_calls) == [
        "aaaaaaaaaaaaaaaa:0000000000000001",
        "bbbbbbbbbbbbbbbb:0000000000000002",
    ]
    assert all("--query-id" in cmd and "--redact" in cmd for cmd in collect_calls)
    assert all("--limit" in cmd and cmd[cmd.index("--limit") + 1] == "1" for cmd in collect_calls)
    assert all("--stop-after-analysis" in cmd for cmd in pipeline_calls)
    payload = read_batch_summary(tmp_path)
    assert payload["jobs"] == 2
    assert {case["case_dir"] for case in payload["cases"]} == {
        str(batch_dir(tmp_path) / "cases" / "case-001"),
        str(batch_dir(tmp_path) / "cases" / "case-002"),
    }


def test_full_report_called_only_for_top_n_bad_cases(tmp_path, monkeypatch, capsys):
    module = load_batch_module()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]
    report_calls = []
    analyzer_done = []

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult(selected, [], "client-side", None),
    )

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = cmd[cmd.index("--query-id") + 1]
            out = Path(cmd[cmd.index("--out") + 1])
            facts = suspicious_facts() if query_id.startswith("bbbb") else healthy_facts()
            write_case(out / query_id.replace(":", "_"), facts)
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            analyzer_done.append(Path(command_args(cmd, "pipeline")[0]).name)
        elif command_uses_role(cmd, "pipeline"):
            assert len(analyzer_done) == 2
            report_calls.append(cmd)
            Path(command_args(cmd, "pipeline")[0], "diagnosis.md").write_text(
                "# report\n", encoding="utf-8"
            )
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path) + ["--top-reports", "1", "--jobs", "2"], env=auth_env()
    )

    assert result == 0
    assert len(report_calls) == 1
    assert len(analyzer_done) == 2
    assert "bbbbbbbbbbbbbbbb_0000000000000002" in command_args(report_calls[0], "pipeline")[0]
    assert "--metadata-failure-policy" not in report_calls[0]
    payload = read_batch_summary(tmp_path)
    cases_by_id = {case["query_id"]: case for case in payload["cases"]}
    assert_non_negative_number(cases_by_id["bbbbbbbbbbbbbbbb:0000000000000002"]["report_seconds"])
    assert cases_by_id["aaaaaaaaaaaaaaaa:0000000000000001"]["report_seconds"] is None
    assert "[batch] case-002 report:" in capsys.readouterr().out


def test_scoring_ranks_suspicious_cases_and_zero_for_healthy():
    module = load_batch_module()

    suspicious_score, suspicious_reasons = module.score_analysis_facts(
        suspicious_facts(),
        metadata_status="collected",
    )
    healthy_score, healthy_reasons = module.score_analysis_facts(
        healthy_facts(),
        metadata_status="skipped",
    )

    assert suspicious_score > healthy_score
    assert "cardinality estimate anomalies: 1" in suspicious_reasons
    assert healthy_score == 0
    assert healthy_reasons == ["no analyzer-supported suspicious facts"]


def test_batch_ranking_uses_score_then_structured_tie_breakers():
    module = load_batch_module()
    cases = [
        case_result(module, index=1, query_id="q-score-low", score=10, duration_sec=999),
        case_result(module, index=2, query_id="q-duration-low", score=20, duration_sec=100),
        case_result(
            module, index=3, query_id="q-card-low", score=20, duration_sec=200, cardinality=1
        ),
        case_result(
            module,
            index=4,
            query_id="q-memory-low",
            score=20,
            duration_sec=200,
            cardinality=2,
            memory=1,
        ),
        case_result(
            module,
            index=5,
            query_id="q-skew-low",
            score=20,
            duration_sec=200,
            cardinality=2,
            memory=2,
            backend_data_skew=False,
            host_tail=9,
        ),
        case_result(
            module,
            index=6,
            query_id="q-tail-low",
            score=20,
            duration_sec=200,
            cardinality=2,
            memory=2,
            backend_data_skew=True,
            host_tail=1,
        ),
        case_result(
            module,
            index=7,
            query_id="q-tail-high",
            score=20,
            duration_sec=200,
            cardinality=2,
            memory=2,
            backend_data_skew=True,
            host_tail=3,
        ),
    ]

    ranked = sorted(cases, key=module.batch_ranking_key)

    assert [case.query_id for case in ranked] == [
        "q-tail-high",
        "q-tail-low",
        "q-skew-low",
        "q-memory-low",
        "q-card-low",
        "q-duration-low",
        "q-score-low",
    ]


def test_batch_ranking_uses_stable_query_id_and_index_tie_breakers():
    module = load_batch_module()
    cases = [
        case_result(module, index=3, query_id="query-b", score=5, duration_sec=10),
        case_result(module, index=2, query_id="query-a", score=5, duration_sec=10),
        case_result(module, index=1, query_id="query-a", score=5, duration_sec=10),
    ]

    ranked = sorted(cases, key=module.batch_ranking_key)

    assert [(case.query_id, case.index) for case in ranked] == [
        ("query-a", 1),
        ("query-a", 2),
        ("query-b", 3),
    ]


def test_case_summary_includes_structured_scoring_components():
    module = load_batch_module()
    case = case_result(
        module,
        index=1,
        query_id="aaaaaaaaaaaaaaaa:0000000000000001",
        score=22,
        duration_sec=123.4,
        cardinality=5,
        memory=4,
        zero_row_gaps=2,
        zero_memory_gaps=1,
        backend_data_skew=True,
        host_tail=2,
        execution_tail=1,
    )
    case.collection_status = "ok"
    case.analysis_status = "ok"
    case.metadata_status = "skipped"
    case.user = "analyst_one"

    summary = module.case_to_summary(case)

    assert summary["user"] == "analyst_one"
    assert summary["duration_sec"] == 123.4
    assert summary["cardinality_anomaly_count"] == 5
    assert summary["memory_anomaly_count"] == 4
    assert summary["zero_row_estimate_gap_count"] == 2
    assert summary["zero_memory_estimate_gap_count"] == 1
    assert summary["backend_data_skew"] is True
    assert summary["host_tail_candidate_count"] == 2
    assert summary["execution_tail_candidate_count"] == 1
    assert summary["metadata_status"] == "skipped"
    assert summary["table_stats_status"] == "not_checked"
    assert summary["collection_status"] == "ok"
    assert summary["analysis_status"] == "ok"
    assert summary["score_severity"] == "high"
    assert summary["scoring_evidence_source"] == "not_scored"
    assert summary["scoring_fallback_reason"] is None
    assert summary["workload_fingerprint"].startswith("wf_")


def test_case_primary_bottleneck_distribution_counts_labels_and_confidence():
    module = load_batch_module()
    stats = case_result(module, index=1, query_id="query-1", score=1)
    stats.case_primary_bottleneck = {
        "label": "stats",
        "confidence": "high",
        "reasons": ["stats_candidate_supported"],
    }
    mixed = case_result(module, index=2, query_id="query-2", score=1)
    mixed.case_primary_bottleneck = {
        "label": "mixed",
        "confidence": "medium",
        "reasons": ["competing_stats_and_non_stats"],
    }
    unknown = case_result(module, index=3, query_id="query-3", score=1)
    unknown.case_primary_bottleneck = {
        "label": "unknown",
        "confidence": "low",
        "reasons": ["no_primary_branch_supported"],
    }
    memory = case_result(module, index=4, query_id="query-4", score=1)
    memory.case_primary_bottleneck = {
        "label": "runtime_memory",
        "confidence": "medium",
        "reasons": ["memory_pressure_spill_scratch_supported"],
    }
    missing = case_result(module, index=5, query_id="query-5", score=1)

    distribution = module.case_primary_bottleneck_distribution(
        [stats, mixed, unknown, memory, missing]
    )

    assert distribution["total_cases"] == 5
    assert distribution["classified_cases"] == 4
    assert distribution["not_classified_cases"] == 1
    assert distribution["label_counts"] == {
        "mixed": 1,
        "not_classified": 1,
        "runtime_memory": 1,
        "stats": 1,
        "unknown": 1,
    }
    assert distribution["confidence_counts"] == {
        "high": 1,
        "low": 1,
        "medium": 2,
        "unknown": 1,
    }
    assert distribution["unknown_cases"] == 1
    assert distribution["mixed_cases"] == 1
    assert distribution["unknown_or_not_classified_cases"] == 2
    assert distribution["medium_or_better_confidence_cases"] == 3
    assert distribution["unknown_rate"] == 0.2
    assert distribution["mixed_rate"] == 0.2
    assert distribution["unknown_or_not_classified_rate"] == 0.4
    assert distribution["medium_or_better_confidence_rate"] == 0.6


def test_case_summary_attaches_workload_fingerprint_from_analysis_json(tmp_path):
    from query_doctor.recent.workload_fingerprint import compute_workload_fingerprint

    module = load_batch_module()
    case = case_result(
        module,
        index=1,
        query_id="aaaaaaaaaaaaaaaa:0000000000000001",
        score=10,
        duration_sec=88,
    )
    case.actual_case_dir = tmp_path / "case-001"
    case.actual_case_dir.mkdir()
    analysis = {
        "query_shape": {
            "top_level_join_count": 2,
            "cte_count": 1,
            "set_operation_count": 0,
            "aggregate_present": True,
            "window_present": False,
        },
        "operators": [
            {"operator_name": "HDFS SCAN"},
            {"operator_name": "KUDU SCAN"},
            {"operator_name": "EXCHANGE"},
            {"operator_name": "HASH AGGREGATE"},
        ],
        "referenced_tables": ["example_warehouse.dim_date", "example_warehouse.fact_sales"],
    }
    (case.actual_case_dir / "analysis.json").write_text(
        json.dumps(analysis),
        encoding="utf-8",
    )

    summary = module.case_to_summary(case)

    assert (
        summary["workload_fingerprint"]
        == compute_workload_fingerprint(
            summary,
            analysis,
        ).fingerprint
    )
    assert summary["workload_fingerprint_incomplete"] is False
    assert summary["workload_fingerprint_incomplete_fields"] == []
    assert summary["workload_shape"] == {
        "aggregate_present": True,
        "cte_count": 1,
        "exchange_count": 1,
        "join_count": 2,
        "query_type": "query",
        "referenced_tables": ["example_warehouse.dim_date", "example_warehouse.fact_sales"],
        "scan_count": 2,
        "set_operation_count": 0,
        "sql_verb": "select",
        "window_present": False,
    }
    assert (
        compute_workload_fingerprint(summary, None).fingerprint == summary["workload_fingerprint"]
    )


def test_batch_summary_builds_in_scan_workload_groups(tmp_path):
    module = load_batch_module()
    config = batch_summary_test_config(
        module,
        tmp_path,
        cm_inspect_limit=20,
        triage_profile_limit=10,
    )

    def make_case(index: int, family: str, duration: float) -> object:
        case = case_result(
            module,
            index=index,
            query_id=f"query-{index}",
            score=10 + index,
            duration_sec=duration,
        )
        case.pool = "root.analytics"
        case.case_primary_bottleneck = {
            "label": "stats" if family in {"a", "b"} else "sql_shape",
            "confidence": "medium",
            "reasons": ["stats_candidate_supported"],
        }
        case.actual_case_dir = tmp_path / f"case-{index:03d}"
        case.actual_case_dir.mkdir()
        shape_by_family = {
            "a": {
                "top_level_join_count": 1,
                "cte_count": 0,
                "set_operation_count": 0,
                "aggregate_present": True,
                "window_present": False,
            },
            "b": {
                "top_level_join_count": 2,
                "cte_count": 1,
                "set_operation_count": 0,
                "aggregate_present": False,
                "window_present": False,
            },
            "c": {
                "top_level_join_count": 0,
                "cte_count": 0,
                "set_operation_count": 0,
                "aggregate_present": False,
                "window_present": True,
            },
            "single": {
                "top_level_join_count": 0,
                "cte_count": 0,
                "set_operation_count": 1,
                "aggregate_present": False,
                "window_present": False,
            },
        }
        tables_by_family = {
            "a": ["example_warehouse.fact_sales", "example_warehouse.dim_date"],
            "b": ["example_warehouse.fact_orders", "example_warehouse.dim_customer"],
            "c": ["example_warehouse.session_events"],
            "single": ["example_warehouse.one_off"],
        }
        (case.actual_case_dir / "analysis.json").write_text(
            json.dumps(
                {
                    "query_shape": shape_by_family[family],
                    "operators": [
                        {"operator_name": "HDFS SCAN"},
                        {"operator_name": "EXCHANGE"},
                    ],
                    "referenced_tables": tables_by_family[family],
                }
            ),
            encoding="utf-8",
        )
        return case

    cases = [
        make_case(1, "a", 10),
        make_case(2, "a", 20),
        make_case(3, "a", 30),
        make_case(4, "a", 40),
        make_case(5, "b", 50),
        make_case(6, "b", 60),
        make_case(7, "b", 70),
        make_case(8, "c", 80),
        make_case(9, "c", 90),
        make_case(10, "single", 100),
    ]

    summary = build_summary_for_test_cases(
        module,
        config,
        cases,
        summaries_inspected=10,
    )

    workload_groups = summary["workload_groups"]
    assert workload_groups["schema_version"] == 1
    assert len(workload_groups["groups"]) == 3
    assert {group["member_count"] for group in workload_groups["groups"]} == {2, 3, 4}
    assert all(group["fingerprint"].startswith("wf_") for group in workload_groups["groups"])
    assert all("member_case_ids" in group for group in workload_groups["groups"])
    grouped_member_ids = {
        case_id for group in workload_groups["groups"] for case_id in group["member_case_ids"]
    }
    assert "case-010" not in grouped_member_ids
    assert all(case["group_fingerprint"].startswith("wf_") for case in summary["cases"])
    singleton = next(case for case in summary["cases"] if case["case_index"] == 10)
    assert singleton["workload_group_member_count"] == 1


def test_batch_summary_excludes_incomplete_workload_fingerprint_from_groups(tmp_path):
    from query_doctor.recent.workload_fingerprint import compute_workload_fingerprint

    module = load_batch_module()
    config = batch_summary_test_config(
        module,
        tmp_path,
        triage_profile_limit=2,
    )
    case = case_result(module, index=1, query_id="query-1", score=10, duration_sec=15)

    summary = build_summary_for_test_cases(module, config, [case])

    assert summary["workload_groups"] == {"schema_version": 1, "groups": []}
    case_summary = summary["cases"][0]
    assert case_summary["workload_fingerprint"].startswith("wf_")
    assert case_summary["workload_shape"] == {
        "aggregate_present": False,
        "cte_count": 0,
        "exchange_count": 0,
        "join_count": 0,
        "query_type": "query",
        "referenced_tables": [],
        "scan_count": 0,
        "set_operation_count": 0,
        "sql_verb": "select",
        "window_present": False,
    }
    assert case_summary["workload_fingerprint_incomplete"] is True
    assert set(case_summary["workload_fingerprint_incomplete_fields"]) >= {
        "join_count",
        "referenced_tables",
        "scan_count",
    }
    recomputed = compute_workload_fingerprint(case_summary, None)
    assert recomputed.fingerprint == case_summary["workload_fingerprint"]
    assert recomputed.shape["incomplete"] is True
    assert "workload_group_member_count" not in case_summary


def test_case_primary_unknown_breakdown_uses_safe_aggregate_categories(tmp_path):
    module = load_batch_module()
    unknown = case_result(
        module,
        index=1,
        query_id="query-1",
        score=19,
        duration_sec=125,
        cardinality=5,
    )
    unknown.case_primary_bottleneck = {
        "label": "unknown",
        "confidence": "low",
        "reasons": ["no_primary_branch_supported"],
    }
    unknown.metadata_status = "collected"
    unknown.actual_case_dir = tmp_path / "case-001"
    unknown.actual_case_dir.mkdir()
    (unknown.actual_case_dir / "analysis.json").write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "hdfs_or_storage_bottleneck",
                        "operators": [{"label": "raw table name", "time_ms": 50_000}],
                    },
                    {"id": "unexpected_internal_detail"},
                ],
                "top_operators_by_time": [
                    {
                        "operator_name": "HDFS SCAN NODE 04",
                        "label": "example_catalog.example_schema.table",
                    }
                ],
                "evidence_quality": {
                    "level": "medium",
                    "limitations": ["runtime_metrics_unavailable", "path:/tmp/raw"],
                },
                "runtime_diagnosis": {"summary": "storage HDFS strongest plausible signal"},
                "stats_metadata_quality": {
                    "stats_primary_bottleneck": "not_supported",
                    "stats_context": "stats_gap_without_row_estimate_evidence",
                },
            }
        ),
        encoding="utf-8",
    )
    stats = case_result(module, index=2, query_id="query-2", score=5, duration_sec=70)
    stats.case_primary_bottleneck = {"label": "stats", "confidence": "high", "reasons": []}

    breakdown = module.case_primary_unknown_breakdown([unknown, stats])

    assert breakdown["total_cases"] == 1
    assert breakdown["analysis_json_cases"] == 1
    assert breakdown["metadata_status_counts"] == {"collected": 1}
    assert breakdown["duration_bucket_counts"] == {"120_200s": 1}
    assert breakdown["score_severity_counts"] == {"high": 1}
    assert breakdown["finding_id_counts"] == {
        "hdfs_or_storage_bottleneck": 1,
        "other": 1,
    }
    assert breakdown["top_time_operator_counts"] == {"HDFS SCAN": 1}
    assert breakdown["evidence_limitation_counts"] == {
        "other": 1,
        "runtime_metrics_unavailable": 1,
    }
    assert breakdown["runtime_diagnosis_summary_counts"] == {"storage_or_hdfs": 1}


def test_optimizer_rewriteability_distribution_counts_buckets():
    module = load_batch_module()

    safe = case_with_optimizer_support(
        module,
        index=1,
        query_id="query-1",
        support=safe_material_draft_support(),
    )
    adjacent = case_with_optimizer_support(
        module,
        index=2,
        query_id="query-2",
        support=adjacent_shape_support(
            no_recipe_review_track="cte_no_downstream_filter_review",
            cte_graph_shape="single_cte",
            cte_predicate_pushdown_status="blocked_no_downstream_filter",
            cte_boundary_reasons=("aggregate_boundary", "no_downstream_filter_for_pushdown"),
            derived_predicate_pushdown_status="blocked_no_downstream_filter",
            derived_boundary_reasons=("projection_not_simple",),
        ),
    )
    adjacent_actionable = case_with_optimizer_support(
        module,
        index=7,
        query_id="query-7",
        support=adjacent_shape_support(
            no_recipe_review_track="cte_no_downstream_filter_review",
            cte_graph_shape="single_cte",
            cte_predicate_pushdown_status="candidate",
        ),
    )
    no_draft = case_with_optimizer_support(
        module,
        index=3,
        query_id="query-3",
        support=no_draft_recipe_support(
            recipe_id="linear_cte_predicate_pushdown",
            draft_unavailable_reasons=("no_deterministic_draft", "no_copyable_predicate"),
            draft_unavailable_class="predicate_not_copyable",
            draft_unavailable_class_label="Predicate not copyable",
            cte_pushdown_conjunct_decision_counts={"unsupported_predicate": 2},
        ),
    )
    no_draft_lineage = case_with_optimizer_support(
        module,
        index=6,
        query_id="query-6",
        support=no_draft_recipe_support(
            recipe_id="cte_dag_predicate_pushdown",
            draft_unavailable_reasons=("final_cte_lineage_unavailable",),
            draft_unavailable_class="cte_lineage_limit",
            draft_unavailable_class_label="CTE lineage limit",
        ),
    )
    stats = case_with_optimizer_support(
        module,
        index=4,
        query_id="query-4",
        support=stats_likely_support(),
    )
    missing = case_result(module, index=5, query_id="query-5", score=1)

    distribution = module.optimizer_rewriteability_distribution(
        [safe, adjacent, adjacent_actionable, no_draft, no_draft_lineage, stats, missing]
    )

    assert distribution["total_cases"] == 7
    assert distribution["optimization_candidate_cases"] == 5
    assert distribution["bucket_counts"] == {
        "recipe_detected_no_draft": 2,
        "recipe_adjacent_shape": 2,
        "safe_material_draft": 1,
        "stats_likely": 1,
        "unknown": 1,
    }
    assert distribution["safe_material_draft_cases"] == 1
    assert distribution["recipe_detected_no_draft_cases"] == 2
    assert distribution["recipe_detected_no_draft_actionable_cases"] == 1
    assert distribution["recipe_detected_no_draft_structural_boundary_cases"] == 1
    assert distribution["recipe_detected_no_draft_validation_or_materiality_cases"] == 0
    assert distribution["recipe_detected_no_draft_other_cases"] == 0
    assert distribution["recipe_detected_no_draft_actionability_counts"] == {
        "actionable": 1,
        "structural_boundary": 1,
    }
    assert distribution["recipe_detected_no_draft_recipe_counts"] == {
        "cte_dag_predicate_pushdown": 1,
        "linear_cte_predicate_pushdown": 1,
    }
    assert distribution["recipe_detected_no_draft_eligibility_counts"] == {
        "deterministic_draft_unavailable": 2
    }
    assert distribution["recipe_detected_no_draft_class_counts"] == {
        "cte_lineage_limit": 1,
        "predicate_not_copyable": 1,
    }
    assert distribution["recipe_detected_no_draft_class_recipe_counts"] == {
        "cte_lineage_limit": {"cte_dag_predicate_pushdown": 1},
        "predicate_not_copyable": {"linear_cte_predicate_pushdown": 1},
    }
    assert distribution["recipe_detected_no_draft_class_recipe_reason_counts"] == {
        "cte_lineage_limit": {"cte_dag_predicate_pushdown": {"final_cte_lineage_unavailable": 1}},
        "predicate_not_copyable": {
            "linear_cte_predicate_pushdown": {
                "no_copyable_predicate": 1,
                "no_deterministic_draft": 1,
            }
        },
    }
    assert distribution["recipe_detected_no_draft_reason_counts"] == {
        "final_cte_lineage_unavailable": 1,
        "no_copyable_predicate": 1,
        "no_deterministic_draft": 1,
    }
    assert distribution["recipe_detected_no_draft_cte_pushdown_decision_counts"] == {
        "unsupported_predicate": 2
    }
    assert distribution["recipe_adjacent_shape_cases"] == 2
    assert distribution["recipe_adjacent_actionable_cases"] == 1
    assert distribution["recipe_adjacent_structural_boundary_cases"] == 1
    assert distribution["recipe_adjacent_other_cases"] == 0
    assert distribution["recipe_adjacent_actionability_counts"] == {
        "actionable": 1,
        "structural_boundary": 1,
    }
    assert distribution["recipe_adjacent_cte_graph_counts"] == {"single_cte": 2}
    assert distribution["recipe_adjacent_cte_predicate_pushdown_counts"] == {
        "candidate": 1,
        "blocked_no_downstream_filter": 1,
    }
    assert distribution["recipe_adjacent_cte_boundary_reason_counts"] == {
        "aggregate_boundary": 1,
        "no_downstream_filter_for_pushdown": 1,
    }
    assert distribution["recipe_adjacent_derived_predicate_pushdown_counts"] == {
        "blocked_no_downstream_filter": 1,
        "no_derived_table": 1,
    }
    assert distribution["recipe_adjacent_derived_boundary_reason_counts"] == {
        "projection_not_simple": 1
    }
    assert distribution["stats_likely_cases"] == 1
    assert distribution["safe_material_draft_rate"] == 0.1429
    assert distribution["recipe_backlog_rate"] == 0.5714
    assert distribution["recipe_backlog_actionable_cases"] == 2
    assert distribution["recipe_backlog_actionable_rate"] == 0.2857
    assert distribution["stats_likely_rate"] == 0.1429


def test_optimizer_rewriteability_distribution_counts_no_recipe_review_tracks():
    module = load_batch_module()

    aggregate = case_with_optimizer_support(
        module,
        index=1,
        query_id="query-1",
        support=no_recipe_not_rewriteable_support(
            no_recipe_review_track="aggregate_or_distinct_review"
        ),
    )
    set_operation = case_with_optimizer_support(
        module,
        index=2,
        query_id="query-2",
        support=no_recipe_not_rewriteable_support(no_recipe_review_track="set_operation_research"),
    )
    source_unavailable = case_with_optimizer_support(
        module,
        index=3,
        query_id="query-3",
        support=optimizer_support(
            status="source_unavailable",
            label="Source unavailable",
            reason="Source SQL is unavailable for trusted draft classification",
            risk_mode="unknown",
            risk_reasons=(),
            draft_eligibility="source_unavailable",
            rewriteability_bucket="human_review_only",
            rewriteability_label="Human review only",
            no_recipe_review_track="source_unavailable",
        ),
    )
    missing_track = case_with_optimizer_support(
        module,
        index=4,
        query_id="query-4",
        support=no_recipe_not_rewriteable_support(),
    )

    distribution = module.optimizer_rewriteability_distribution(
        [aggregate, set_operation, source_unavailable, missing_track]
    )

    assert distribution["no_recipe_review_track_counts"] == {
        "aggregate_or_distinct_review": 1,
        "set_operation_research": 1,
        "source_unavailable": 1,
        "unknown": 1,
    }


def test_optimizer_rewriteability_distribution_counts_human_review_guardrails():
    module = load_batch_module()

    supported = case_with_optimizer_support(
        module,
        index=1,
        query_id="query-1",
        support=human_review_support(
            risk_reasons=(
                "cte_body_validation_not_proven",
                "sql_payload_too_large_for_safe_rewrite",
                "/tmp/raw SELECT secret",
            ),
        ),
    )

    distribution = module.optimizer_rewriteability_distribution([supported])

    assert distribution["human_review_only_cases"] == 1
    assert distribution["human_review_only_status_counts"] == {"guidance_only": 1}
    assert distribution["human_review_only_draft_eligibility_counts"] == {
        "disabled_by_safety_thresholds": 1
    }
    assert distribution["human_review_only_risk_reason_counts"] == {
        "cte_body_validation_not_proven": 1,
        "other": 1,
        "sql_payload_too_large_for_safe_rewrite": 1,
    }


def test_optimizer_funnel_counts_batch_rewrite_path():
    module = load_batch_module()

    ready = case_with_optimizer_support(
        module,
        index=1,
        query_id="query-1",
        support=safe_material_draft_support(
            recipe_detected=True,
            draft_eligibility="safe_to_attempt",
        ),
    )
    no_draft = case_with_optimizer_support(
        module,
        index=2,
        query_id="query-2",
        support=no_draft_recipe_support(
            recipe_detected=True,
            draft_unavailable_reasons=("final_select_join_boundary",),
            draft_unavailable_class="shape_boundary",
            draft_unavailable_class_label="Shape boundary",
        ),
    )
    adjacent = case_with_optimizer_support(
        module,
        index=3,
        query_id="query-3",
        support=adjacent_shape_support(
            draft_eligibility="unknown",
            cte_predicate_pushdown_status="candidate",
        ),
    )
    threshold = case_with_optimizer_support(
        module,
        index=4,
        query_id="query-4",
        support=human_review_support(
            reason="SQL shape exceeds safe thresholds",
            risk_reasons=("too_long",),
        ),
    )
    stats = case_with_optimizer_support(
        module,
        index=5,
        query_id="query-5",
        support=stats_likely_support(),
    )
    missing = case_result(module, index=6, query_id="query-6", score=1)

    funnel = module.optimizer_funnel([ready, no_draft, adjacent, threshold, stats, missing])

    assert funnel == {
        "total_cases": 6,
        "optimization_candidate_cases": 4,
        "recipe_detected_cases": 2,
        "draft_ready_cases": 1,
        "trusted_sql_draft_produced_cases": 0,
        "trusted_sql_draft_produced_note": (
            "Recent batch scans classify draft readiness only; trusted SQL drafts "
            "are produced later by explicit selected-case optimizer actions."
        ),
        "recipe_detected_no_draft_cases": 1,
        "recipe_detected_no_draft_actionable_cases": 0,
        "recipe_detected_no_draft_structural_boundary_cases": 1,
        "recipe_detected_no_draft_validation_or_materiality_cases": 0,
        "recipe_detected_no_draft_other_cases": 0,
        "recipe_adjacent_shape_cases": 1,
        "recipe_adjacent_actionable_cases": 1,
        "recipe_adjacent_structural_boundary_cases": 0,
        "recipe_adjacent_other_cases": 0,
        "draft_disabled_by_safety_threshold_cases": 1,
        "source_unavailable_cases": 0,
        "stats_likely_cases": 1,
        "human_review_only_cases": 1,
        "not_rewriteable_cases": 0,
        "unknown_cases": 1,
        "recipe_detected_rate": 0.5,
        "draft_ready_rate": 0.25,
        "trusted_sql_draft_produced_rate": 0.0,
        "recipe_backlog_rate": 0.5,
        "recipe_backlog_actionable_cases": 1,
        "recipe_backlog_actionable_rate": 0.25,
    }


def test_batch_summary_markdown_includes_no_draft_class_recipe_counts(tmp_path):
    module = load_batch_module()

    config = batch_summary_test_config(module, tmp_path)
    no_draft = case_with_optimizer_support(
        module,
        index=1,
        query_id="query-1",
        score=10,
        support=no_draft_recipe_support(
            recipe_id="cte_dag_predicate_pushdown",
            draft_unavailable_reasons=("final_cte_lineage_unavailable",),
            draft_unavailable_class="cte_lineage_limit",
            draft_unavailable_class_label="CTE lineage limit",
        ),
    )

    summary = build_summary_for_test_cases(module, config, [no_draft])

    write_batch_summary_test_outputs(module, tmp_path, summary)
    summary_md = read_batch_summary_markdown(tmp_path)
    assert "- no-draft actionability: structural_boundary=1" in summary_md
    assert "- no-draft classes: cte_lineage_limit=1" in summary_md
    assert (
        "- no-draft classes by recipe: cte_lineage_limit: cte_dag_predicate_pushdown=1"
    ) in summary_md
    assert (
        "- no-draft class/recipe reasons: "
        "cte_lineage_limit/cte_dag_predicate_pushdown: final_cte_lineage_unavailable=1"
    ) in summary_md


def test_batch_summary_markdown_includes_adjacent_shape_breakdown(tmp_path):
    module = load_batch_module()

    config = batch_summary_test_config(module, tmp_path)
    adjacent = case_with_optimizer_support(
        module,
        index=1,
        query_id="query-1",
        score=10,
        support=adjacent_shape_support(
            no_recipe_review_track="cte_no_downstream_filter_review",
            cte_graph_shape="single_cte",
            cte_predicate_pushdown_status="blocked_no_downstream_filter",
            cte_boundary_reasons=("aggregate_boundary", "no_downstream_filter_for_pushdown"),
            derived_predicate_pushdown_status="blocked_no_downstream_filter",
            derived_boundary_reasons=("projection_not_simple",),
        ),
    )

    summary = build_summary_for_test_cases(module, config, [adjacent])

    write_batch_summary_test_outputs(module, tmp_path, summary)
    summary_md = read_batch_summary_markdown(tmp_path)
    assert "- adjacent CTE graphs: single_cte=1" in summary_md
    assert "- adjacent actionability: structural_boundary=1" in summary_md
    assert "- adjacent CTE predicate status: blocked_no_downstream_filter=1" in summary_md
    assert (
        "- adjacent CTE boundary reasons: aggregate_boundary=1, no_downstream_filter_for_pushdown=1"
    ) in summary_md
    assert "- adjacent derived predicate status: blocked_no_downstream_filter=1" in summary_md
    assert "- adjacent derived boundary reasons: projection_not_simple=1" in summary_md
    assert "- no-recipe review tracks: cte_no_downstream_filter_review=1" in summary_md


def test_batch_summary_includes_primary_bottleneck_distribution(tmp_path):
    module = load_batch_module()

    config = batch_summary_test_config(module, tmp_path)
    stats = case_result(module, index=1, query_id="query-1", score=10)
    stats.case_primary_bottleneck = {"label": "stats", "confidence": "high", "reasons": []}
    stats.scoring_evidence_source = "analysis_json"
    unknown = case_result(module, index=2, query_id="query-2", score=3)
    unknown.case_primary_bottleneck = {"label": "unknown", "confidence": "low", "reasons": []}
    unknown.scoring_evidence_source = "markdown_fallback"
    unknown.scoring_fallback_reason = "analysis_json_missing"
    human_review = case_with_optimizer_support(
        module,
        index=3,
        query_id="query-3",
        score=4,
        support=human_review_support(
            risk_reasons=(
                "cte_body_validation_not_proven",
                "sql_payload_too_large_for_safe_rewrite",
            ),
        ),
    )

    summary = build_summary_for_test_cases(
        module,
        config,
        [stats, unknown, human_review],
        summaries_inspected=3,
    )

    distribution = summary["case_primary_bottleneck_distribution"]
    assert distribution["label_counts"] == {"not_classified": 1, "stats": 1, "unknown": 1}
    assert distribution["unknown_rate"] == 0.3333
    scoring_distribution = summary["scoring_evidence_source_distribution"]
    assert scoring_distribution["source_counts"] == {
        "analysis_json": 1,
        "markdown_fallback": 1,
        "not_scored": 1,
    }
    assert scoring_distribution["fallback_cases"] == 1
    assert scoring_distribution["fallback_rate"] == 0.3333
    assert scoring_distribution["fallback_reason_counts"] == {"analysis_json_missing": 1}
    rewriteability = summary["optimizer_rewriteability_distribution"]
    assert rewriteability["bucket_counts"] == {"human_review_only": 1, "unknown": 2}
    optimizer_funnel = summary["optimizer_funnel"]
    assert optimizer_funnel["optimization_candidate_cases"] == 1
    assert optimizer_funnel["draft_ready_cases"] == 0
    write_batch_summary_test_outputs(module, tmp_path, summary)
    summary_md = read_batch_summary_markdown(tmp_path)
    funnel_json = json.loads((batch_dir(tmp_path) / "optimizer_funnel.json").read_text())
    assert funnel_json == optimizer_funnel
    assert "## Primary Bottleneck Distribution" in summary_md
    assert "## Scoring Evidence Source" in summary_md
    assert "- analysis JSON cases: 1 / 3" in summary_md
    assert "- markdown fallback cases: 1 (0.3333)" in summary_md
    assert "- sources: analysis_json=1, markdown_fallback=1, not_scored=1" in summary_md
    assert "- fallback reasons: analysis_json_missing=1" in summary_md
    assert "- classified cases: 2 / 3" in summary_md
    assert "- unknown cases: 1 (0.3333)" in summary_md
    assert "- labels: not_classified=1, stats=1, unknown=1" in summary_md
    assert "## Optimizer Rewriteability Distribution" in summary_md
    assert "- optimization candidate cases: 1 / 3" in summary_md
    assert "- buckets: human_review_only=1, unknown=2" in summary_md
    assert (
        "- human-review guardrails: "
        "cte_body_validation_not_proven=1, sql_payload_too_large_for_safe_rewrite=1"
    ) in summary_md
    assert "- human-review draft eligibility: disabled_by_safety_thresholds=1" in summary_md
    assert "## Optimizer Funnel" in summary_md
    assert "- draft-ready cases: 0 (0.0)" in summary_md
    assert "- trusted SQL draft produced cases: 0 (0.0)" in summary_md


def test_case_summary_includes_query_optimization_candidate(tmp_path):
    module = load_batch_module()
    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Cardinality anomalies: 3",
                "- Memory anomalies: 1",
                "",
                "## Findings",
                "",
                "### Large intermediate or exchange traffic [high]",
                "",
                "- TotalBytesSent is large relative to the configured threshold.",
                "- TotalBytesSent: 55.0 GiB",
                "",
                "## Action Cards",
                "",
                "### Card 1: Severe cardinality underestimation before high-cost operator",
                "",
                "Finding:",
                "- operator: 02:HASH JOIN",
                "- actual rows: 5.00M",
                "- estimated rows: 10.00K",
                "- actual/estimated ratio: 500x",
                "- peak memory: 20.00 GiB",
                "- peak/estimated memory ratio: 40.0x",
            ]
        ),
        encoding="utf-8",
    )
    (case.actual_case_dir / "original_query.sql").write_text(
        "\n".join(
            [
                "WITH base AS (",
                "  SELECT user_id, event_ts, bytes_sent",
                "  FROM example_events.fact_events",
                "  WHERE event_date = '2026-05-01'",
                "), filtered AS (",
                "  SELECT user_id, bytes_sent",
                "  FROM base",
                ")",
                "SELECT user_id, bytes_sent",
                "FROM filtered",
                "WHERE bytes_sent > 0",
            ]
        ),
        encoding="utf-8",
    )

    module.score_case(case)
    (case.actual_case_dir / "analysis.json").write_text(
        json.dumps(
            {
                "cardinality_anomalies": [
                    {
                        "operator_id": "02",
                        "operator_name": "HASH JOIN",
                        "join_kind": "INNER JOIN",
                        "is_partitioned": True,
                    }
                ],
                "memory_anomalies": [
                    {
                        "operator_id": "05",
                        "operator_name": "AGGREGATE",
                    }
                ],
                "top_operators_by_time": [
                    {
                        "operator_id": "07",
                        "operator_name": "EXCHANGE",
                    }
                ],
                "findings": [{"id": "large_intermediate_or_exchange_traffic"}],
            }
        ),
        encoding="utf-8",
    )
    summary = module.case_to_summary(case)

    candidate = summary["query_optimization_candidate"]
    assert candidate["tier"] in {"medium", "high"}
    assert candidate["score"] > 0
    assert "join row expansion or cardinality mismatch with join evidence" in candidate["reasons"]
    rewrite_support = summary["optimizer_rewrite_support"]
    assert rewrite_support["status"] == "recipe_detected"
    assert rewrite_support["recipe_id"] == "linear_cte_predicate_pushdown"
    assert rewrite_support["recipe_detected"] is True
    assert rewrite_support["draft_eligibility"] == "safe_to_attempt"
    assert rewrite_support["cte_predicate_path_status"] == "single_dependency_path"
    assert rewrite_support["cte_projection_preservation_status"] == "simple_projection_preserved"
    assert rewrite_support["cte_simple_projection_count"] == 2
    assert rewrite_support["cte_expression_projection_count"] == 0
    locator_ids = {locator["id"] for locator in summary["source_locators"]["query_optimization"]}
    assert "sql_cte_block" in locator_ids
    assert "sql_final_select_filter" in locator_ids
    assert "plan_cardinality_anomaly" in locator_ids
    assert "plan_memory_anomaly" in locator_ids
    assert "plan_top_time_operator" in locator_ids
    assert {
        "id": "plan_cardinality_anomaly",
        "detail": "node 02 HASH JOIN (inner join, partitioned)",
    } in summary["source_locators"]["query_optimization"]
    assert all(
        "coordinate" not in locator for locator in summary["source_locators"]["query_optimization"]
    )
    assert all(
        "line_span" not in locator for locator in summary["source_locators"]["query_optimization"]
    )
    assert all(
        "line_span_source" not in locator
        for locator in summary["source_locators"]["query_optimization"]
    )

    coordinate_summary = module.case_to_summary(case, include_source_coordinates=True)
    coordinate_locators = coordinate_summary["source_locators"]["query_optimization"]
    assert {
        "id": "sql_cte_block",
        "coordinate": "lines 1-8",
        "line_span": {"start_line": 1, "end_line": 8},
        "line_span_source": "line_range_from_sql_parser",
        "detail": "2 CTEs",
    } in coordinate_locators
    assert {
        "id": "sql_final_select_filter",
        "coordinate": "line 11",
        "line_span": {"start_line": 11, "end_line": 11},
        "line_span_source": "line_range_from_sql_parser",
        "detail": "predicate near final SELECT",
    } in coordinate_locators


def test_case_summary_includes_stats_optimization_candidate(tmp_path):
    module = load_batch_module()
    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.metadata_status = "collected"
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Cardinality anomalies: 3",
                "- Memory anomalies: 1",
                "- Zero/unknown row estimate gaps: 1",
                "",
                "## CM Query Context",
                "- status: succeeded",
                "- query_state: FINISHED",
                "- duration: 2.00m",
                "- bytes_read: 120.00 GiB",
                "- bytes_sent: 55.00 GiB",
                "- memory_aggregate_peak: 20.00 GiB",
                "",
                "## Table Metadata Context",
                "- table stats row-count completeness: missing/unknown",
                "- column stats completeness: incomplete/unknown",
                "",
                "## Action Cards",
                "",
                "### Card 1: Severe cardinality underestimation before high-cost operator",
                "",
                "Finding:",
                "- operator: 02:HASH JOIN",
                "- actual rows: 5.00M",
                "- estimated rows: 10.00K",
                "- actual/estimated ratio: 500x",
                "- peak memory: 20.00 GiB",
                "- peak/estimated memory ratio: 40.0x",
                "",
                "### Large intermediate or exchange traffic [high]",
                "",
                "- TotalBytesSent: 55.0 GiB",
            ]
        ),
        encoding="utf-8",
    )

    module.score_case(case)
    (case.actual_case_dir / "analysis.json").write_text(
        json.dumps(
            {
                "cardinality_anomalies": [
                    {
                        "operator_id": "03",
                        "operator_name": "HASH JOIN",
                    }
                ],
                "memory_anomalies": [
                    {
                        "operator_id": "06",
                        "operator_name": "AGGREGATE",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    summary = module.case_to_summary(case)

    candidate = summary["stats_optimization_candidate"]
    assert candidate["tier"] == "high"
    assert candidate["need_type"] == "table_and_column_stats"
    assert candidate["speed_benefit"] in {"high", "medium"}
    assert "compare EXPLAIN before and after stats collection" in candidate["required_confirmation"]
    stats_locator_ids = {locator["id"] for locator in summary["source_locators"]["stats_refresh"]}
    assert "metadata_referenced_stats" in stats_locator_ids
    assert "metadata_table_stats" in stats_locator_ids
    assert "plan_cardinality_anomaly" in stats_locator_ids


def test_case_summary_includes_profile_admission_source_locators():
    module = load_batch_module()
    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.case_primary_bottleneck = {
        "label": "runtime_admission",
        "confidence": "high",
        "reasons": [
            "admission_wait_source_profile_resource_facts",
            "admission_wait_source_profile_timing_facts",
        ],
    }

    summary = module.case_to_summary(case)

    assert summary["source_locators"]["runtime_admission"] == [
        {"id": "runtime_admission_window", "detail": "case runtime window"},
        {
            "id": "profile_resource_admission_evidence",
            "detail": "query-specific admission result or resource wait",
        },
        {
            "id": "profile_timing_admission_evidence",
            "detail": "query timeline admission phase",
        },
    ]


def test_batch_summary_includes_source_coordinates_for_safe_and_owner_raw(tmp_path):
    module = load_batch_module()

    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Cardinality anomalies: 3",
                "",
                "## Findings",
                "### Join bottleneck [medium]",
                "- join evidence",
            ]
        ),
        encoding="utf-8",
    )
    (case.actual_case_dir / "original_query.sql").write_text(
        "\n".join(
            [
                "WITH base AS (",
                "  SELECT user_id, amount",
                "  FROM example_orders.fact_orders",
                ")",
                "SELECT user_id, amount",
                "FROM base",
                "WHERE amount > 0",
            ]
        ),
        encoding="utf-8",
    )
    case.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=80,
        tier="high",
        confidence="medium",
        impact="high",
        reasons=("join row expansion or cardinality mismatch with join evidence",),
        counter_signals=(),
        suggested_review_areas=("join keys and join cardinality",),
    )
    case.optimizer_rewrite_support = adjacent_shape_support(
        reason="Manual review",
        draft_eligibility="unknown",
        cte_count=1,
        cte_predicate_origin_status="final_select_filter",
    )

    safe_args = module.parse_args(
        [
            "--query-profile-source",
            "impala",
            "--impala-profile-host",
            "impalad-1.example.com",
            "--out",
            str(batch_dir(tmp_path)),
            "--metadata-mode",
            "off",
        ]
    )
    safe_config = module.build_batch_config(safe_args, env={}, cwd=tmp_path, repo_root=REPO_DIR)
    owner_args = module.parse_args(
        [
            "--query-profile-source",
            "impala",
            "--impala-profile-host",
            "impalad-1.example.com",
            "--out",
            str(batch_dir(tmp_path)),
            "--metadata-mode",
            "off",
            "--source-visibility",
            "owner_raw",
            "--source-owner-user",
            "analyst_one",
        ]
    )
    owner_config = module.build_batch_config(owner_args, env={}, cwd=tmp_path, repo_root=REPO_DIR)
    cm_owner_args = module.parse_args(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "off",
            "--no-min-duration-filter",
            "--source-visibility",
            "owner_raw",
            "--source-owner-user",
            "analyst_one",
        ]
    )
    cm_owner_config = module.build_batch_config(
        cm_owner_args, env=auth_env(), cwd=tmp_path, repo_root=REPO_DIR
    )
    discovery = module.DiscoveryResult([], [], "client-side", None, summaries_inspected=1)

    safe_summary = build_summary_for_test_cases(
        module,
        safe_config,
        [case],
        discovery=discovery,
        discovery_seconds=1.0,
        total_seconds=2.0,
    )
    owner_summary = build_summary_for_test_cases(
        module,
        owner_config,
        [case],
        discovery=discovery,
        discovery_seconds=1.0,
        total_seconds=2.0,
    )
    cm_owner_summary = build_summary_for_test_cases(
        module,
        cm_owner_config,
        [case],
        discovery=discovery,
        discovery_seconds=1.0,
        total_seconds=2.0,
    )

    safe_locators = safe_summary["cases"][0]["source_locators"]["query_optimization"]
    owner_locators = owner_summary["cases"][0]["source_locators"]["query_optimization"]
    cm_owner_locators = cm_owner_summary["cases"][0]["source_locators"]["query_optimization"]

    assert_line_locator(safe_locators, "lines 1-4", 1, 4)
    assert_line_locator(safe_locators, "line 7", 7, 7)
    assert_line_locator(owner_locators, "lines 1-4", 1, 4)
    assert_line_locator(owner_locators, "line 7", 7, 7)
    assert_line_locator(cm_owner_locators, "lines 1-4", 1, 4)
    assert_line_locator(cm_owner_locators, "line 7", 7, 7)


def test_score_case_prefers_structured_analysis_json_for_stats_candidate(tmp_path):
    module = load_batch_module()
    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.metadata_status = "collected"
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Cardinality anomalies: 0",
                "",
                "## Table Metadata Context",
                "- table stats row-count completeness: available",
                "- column stats completeness: complete",
                "",
                "## Action Cards",
                "",
                "### Large intermediate or exchange traffic [high]",
                "- TotalBytesSent: 55.0 GiB",
            ]
        ),
        encoding="utf-8",
    )
    (case.actual_case_dir / "analysis.json").write_text(
        json.dumps(
            {
                "cardinality_anomalies": [
                    {"operator_name": "HASH JOIN", "rows_actual_to_estimated_ratio": 500},
                    {"operator_name": "HASH JOIN", "rows_actual_to_estimated_ratio": 100},
                    {"operator_name": "AGGREGATE", "rows_actual_to_estimated_ratio": 50},
                ],
                "memory_anomalies": [],
                "zero_row_estimate_gaps": [],
                "zero_memory_estimate_gaps": [],
                "findings": [{"id": "large_intermediate_or_exchange_traffic"}],
                "stats_metadata_quality": {
                    "status": "limited",
                    "table_stats": "missing/unknown",
                    "column_stats": "complete",
                    "tables_with_missing_table_stats": 1,
                    "tables_with_incomplete_column_stats": 0,
                    "stats_primary_bottleneck": "candidate_supported",
                },
                "case_primary_bottleneck": {
                    "label": "stats",
                    "confidence": "medium",
                    "reasons": ["stats_candidate_supported"],
                },
            }
        ),
        encoding="utf-8",
    )

    module.score_case(case)

    assert case.score == 11
    assert case.scoring_evidence_source == "analysis_json"
    assert case.scoring_fallback_reason is None
    assert case.stats_optimization_candidate is not None
    assert case.stats_optimization_candidate.need_type == "table_stats"
    assert (
        "missing or unknown table/partition row-count stats"
        in case.stats_optimization_candidate.reasons
    )
    summary = module.case_to_summary(case)
    assert summary["case_primary_bottleneck"] == {
        "label": "stats",
        "confidence": "medium",
        "reasons": ["stats_candidate_supported"],
    }


def candidate_case_for_cap_tests(module, *, label: str, confidence: str = "high"):
    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.case_primary_bottleneck = {
        "label": label,
        "confidence": confidence,
        "reasons": [f"{label}_supported"],
    }
    case.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=82,
        tier="high",
        confidence="high",
        impact="high",
        reasons=("join row expansion or cardinality mismatch with join evidence",),
        counter_signals=(),
        suggested_review_areas=("join keys and join cardinality",),
    )
    case.stats_optimization_candidate = module.StatsOptimizationCandidateScore(
        score=80,
        tier="high",
        confidence="high",
        impact="high",
        need_type="table_and_column_stats",
        table_stats_need="critical",
        column_stats_need="critical",
        speed_benefit="high",
        reasons=("missing or unknown table/partition row-count stats",),
        counter_signals=(),
        suggested_review_areas=("table/partition row counts",),
        required_confirmation=("compare EXPLAIN before and after stats collection",),
    )
    return case


def test_primary_stats_caps_query_optimization_only():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    case = candidate_case_for_cap_tests(module, label="stats")

    apply_primary_bottleneck_caps(case)

    assert case.query_optimization_candidate.tier == "low"
    assert (
        "primary_bottleneck_is_stats; rewrite is secondary"
        in case.query_optimization_candidate.counter_signals
    )
    assert case.stats_optimization_candidate.tier == "high"


def test_primary_stats_cap_prevents_rewrite_support_promotion(tmp_path):
    module = load_batch_module()
    case = case_result(
        module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0, duration_sec=120
    )
    case.metadata_status = "collected"
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Cardinality anomalies: 3",
                "",
                "## Action Cards",
                "",
                "### Card 1: Severe cardinality underestimation before high-cost operator",
                "- operator: 02:HASH JOIN",
                "- actual/estimated ratio: 500x",
                "",
                "### Large intermediate or exchange traffic [high]",
                "- TotalBytesSent is large relative to the configured threshold.",
                "- TotalBytesSent: 55.0 GiB",
            ]
        ),
        encoding="utf-8",
    )
    (case.actual_case_dir / "analysis.json").write_text(
        json.dumps(
            {
                "case_primary_bottleneck": {
                    "label": "stats",
                    "confidence": "high",
                    "reasons": ["stats_candidate_supported"],
                },
                "cardinality_anomalies": [
                    {"operator_name": "HASH JOIN", "rows_actual_to_estimated_ratio": 500}
                ],
                "stats_metadata_quality": {
                    "status": "limited",
                    "table_stats": "missing/unknown",
                    "column_stats": "complete",
                    "tables_with_missing_table_stats": 1,
                    "tables_with_incomplete_column_stats": 0,
                    "stats_primary_bottleneck": "candidate_supported",
                },
            }
        ),
        encoding="utf-8",
    )
    (case.actual_case_dir / "original_query.sql").write_text(
        "\n".join(
            [
                "WITH base AS (",
                "  SELECT user_id, bytes_sent",
                "  FROM example_events.fact_events",
                "), filtered AS (",
                "  SELECT user_id, bytes_sent",
                "  FROM base",
                ")",
                "SELECT user_id, bytes_sent",
                "FROM filtered",
                "WHERE bytes_sent > 0",
            ]
        ),
        encoding="utf-8",
    )

    module.score_case(case)

    assert case.query_optimization_candidate.tier == "low"
    assert case.optimizer_rewrite_support.status == "not_candidate"
    assert case.optimizer_rewrite_support.draft_eligibility == "not_candidate"


def test_primary_sql_shape_caps_stats_optimization_only():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    case = candidate_case_for_cap_tests(module, label="sql_shape")

    apply_primary_bottleneck_caps(case)

    assert case.query_optimization_candidate.tier == "high"
    assert case.stats_optimization_candidate.tier == "low"
    assert (
        "primary_bottleneck_is_sql_shape; stats refresh unlikely primary"
        in case.stats_optimization_candidate.counter_signals
    )


def test_primary_runtime_bottleneck_caps_both_action_candidates():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    case = candidate_case_for_cap_tests(module, label="runtime_admission")

    apply_primary_bottleneck_caps(case)

    assert case.query_optimization_candidate.tier == "low"
    assert case.stats_optimization_candidate.tier == "low"
    assert (
        "primary_bottleneck_is_runtime_admission"
        in case.query_optimization_candidate.counter_signals
    )
    assert (
        "primary_bottleneck_is_runtime_admission"
        in case.stats_optimization_candidate.counter_signals
    )


def test_primary_runtime_memory_caps_both_action_candidates_if_high_confidence():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    case = candidate_case_for_cap_tests(module, label="runtime_memory")

    apply_primary_bottleneck_caps(case)

    assert case.query_optimization_candidate.tier == "low"
    assert case.stats_optimization_candidate.tier == "low"
    assert (
        "primary_bottleneck_is_runtime_memory" in case.query_optimization_candidate.counter_signals
    )
    assert (
        "primary_bottleneck_is_runtime_memory" in case.stats_optimization_candidate.counter_signals
    )


def test_primary_client_fetch_tail_caps_both_action_candidates():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    case = candidate_case_for_cap_tests(module, label="client_fetch_tail")

    apply_primary_bottleneck_caps(case)

    assert case.query_optimization_candidate.tier == "low"
    assert case.stats_optimization_candidate.tier == "low"
    assert (
        "primary_bottleneck_is_client_fetch_tail; rewrite is secondary"
        in case.query_optimization_candidate.counter_signals
    )
    assert (
        "primary_bottleneck_is_client_fetch_tail"
        in case.stats_optimization_candidate.counter_signals
    )


def test_primary_caps_only_apply_for_high_confidence():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    case = candidate_case_for_cap_tests(module, label="stats", confidence="medium")

    apply_primary_bottleneck_caps(case)

    assert case.query_optimization_candidate.tier == "high"
    assert case.stats_optimization_candidate.tier == "high"


def test_mixed_query_shape_primary_soft_caps_stats_candidate_only():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    mixed = candidate_case_for_cap_tests(module, label="mixed")
    mixed.case_primary_bottleneck["reasons"] = ["competing_stats", "competing_sql_shape"]

    apply_primary_bottleneck_caps(mixed)

    assert mixed.query_optimization_candidate.tier == "high"
    assert mixed.stats_optimization_candidate.tier == "medium"
    assert (
        "mixed_primary_includes_sql_shape; stats refresh requires EXPLAIN confirmation"
        in mixed.stats_optimization_candidate.counter_signals
    )


def test_mixed_runtime_primary_soft_caps_stats_candidate_only():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    mixed = candidate_case_for_cap_tests(module, label="mixed")
    mixed.case_primary_bottleneck["reasons"] = [
        "competing_stats",
        "competing_runtime_data_movement",
    ]

    apply_primary_bottleneck_caps(mixed)

    assert mixed.query_optimization_candidate.tier == "high"
    assert mixed.stats_optimization_candidate.tier == "medium"
    assert (
        "mixed_primary_includes_runtime_data_movement; stats refresh is not first action"
        in mixed.stats_optimization_candidate.counter_signals
    )


def test_mixed_memory_primary_soft_caps_stats_candidate_only():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    mixed = candidate_case_for_cap_tests(module, label="mixed")
    mixed.case_primary_bottleneck["reasons"] = [
        "competing_stats",
        "competing_runtime_memory",
    ]

    apply_primary_bottleneck_caps(mixed)

    assert mixed.query_optimization_candidate.tier == "high"
    assert mixed.stats_optimization_candidate.tier == "medium"
    assert (
        "mixed_primary_includes_runtime_memory; stats refresh is not first action"
        in mixed.stats_optimization_candidate.counter_signals
    )


def test_unknown_primary_does_not_cap_candidates():
    module = load_batch_module()
    from query_doctor.recent.batch_scoring import apply_primary_bottleneck_caps

    unknown = candidate_case_for_cap_tests(module, label="unknown")

    apply_primary_bottleneck_caps(unknown)

    assert unknown.query_optimization_candidate.tier == "high"
    assert unknown.stats_optimization_candidate.tier == "high"


def test_query_optimization_ranking_is_separate_from_triage_score():
    module = load_batch_module()
    high_candidate = case_result(module, index=1, query_id="opt-high", score=3, duration_sec=120)
    high_candidate.analysis_status = "ok"
    high_candidate.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=80,
        tier="high",
        confidence="high",
        impact="high",
        reasons=("join row expansion or cardinality mismatch with join evidence",),
        counter_signals=(),
        suggested_review_areas=("join keys and join cardinality",),
    )
    medium_candidate = case_result(
        module, index=2, query_id="opt-medium", score=30, duration_sec=500
    )
    medium_candidate.analysis_status = "ok"
    medium_candidate.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=45,
        tier="medium",
        confidence="medium",
        impact="medium",
        reasons=("large scan volume with comparatively small downstream row count",),
        counter_signals=(),
        suggested_review_areas=("filter placement",),
    )
    low_candidate = case_result(module, index=3, query_id="opt-low", score=31, duration_sec=700)
    low_candidate.analysis_status = "ok"
    low_candidate.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=20,
        tier="low",
        confidence="low",
        impact="high",
        reasons=("expensive query without query-shape evidence",),
        counter_signals=("no query-shape opportunity evidence",),
        suggested_review_areas=(),
    )

    ranked = module.rank_cases_for_query_optimization(
        [low_candidate, medium_candidate, high_candidate]
    )

    assert [case.query_id for case in ranked] == ["opt-high", "opt-medium"]
    assert high_candidate.query_optimization_rank == 1
    assert medium_candidate.query_optimization_rank == 2
    assert low_candidate.query_optimization_rank is None


def test_query_optimization_ranking_prefers_draftable_rewriteability():
    module = load_batch_module()

    guidance = case_result(module, index=1, query_id="guidance-only", score=3, duration_sec=500)
    guidance.analysis_status = "ok"
    guidance.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=90,
        tier="high",
        confidence="high",
        impact="high",
        reasons=("large exchange volume before downstream processing",),
        counter_signals=(),
        suggested_review_areas=("pre-aggregation before exchange",),
    )
    guidance.optimizer_rewrite_support = guidance_human_review_support()
    draftable = case_result(module, index=2, query_id="draftable", score=2, duration_sec=100)
    draftable.analysis_status = "ok"
    draftable.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=70,
        tier="high",
        confidence="medium",
        impact="medium",
        reasons=("large exchange volume before downstream processing",),
        counter_signals=(),
        suggested_review_areas=("filter placement",),
    )
    draftable.optimizer_rewrite_support = safe_material_draft_support()

    ranked = module.rank_cases_for_query_optimization([guidance, draftable])

    assert [case.query_id for case in ranked] == ["draftable", "guidance-only"]
    assert draftable.query_optimization_rank == 1
    assert guidance.query_optimization_rank == 2


def test_stats_optimization_ranking_is_separate_from_triage_score():
    module = load_batch_module()
    high_candidate = case_result(module, index=1, query_id="stats-high", score=3, duration_sec=120)
    high_candidate.analysis_status = "ok"
    high_candidate.stats_optimization_candidate = module.StatsOptimizationCandidateScore(
        score=80,
        tier="high",
        confidence="medium",
        impact="high",
        need_type="table_and_column_stats",
        table_stats_need="critical",
        column_stats_need="critical",
        speed_benefit="high",
        reasons=("missing or unknown table/partition row-count stats",),
        counter_signals=(),
        suggested_review_areas=("table/partition row counts",),
        required_confirmation=("compare EXPLAIN before and after stats collection",),
    )
    medium_candidate = case_result(
        module, index=2, query_id="stats-medium", score=30, duration_sec=500
    )
    medium_candidate.analysis_status = "ok"
    medium_candidate.stats_optimization_candidate = module.StatsOptimizationCandidateScore(
        score=45,
        tier="medium",
        confidence="medium",
        impact="medium",
        need_type="table_stats",
        table_stats_need="high",
        column_stats_need="low",
        speed_benefit="medium",
        reasons=("incomplete table/partition stats",),
        counter_signals=(),
        suggested_review_areas=("table/partition row counts",),
        required_confirmation=("compare EXPLAIN before and after stats collection",),
    )
    low_candidate = case_result(module, index=3, query_id="stats-low", score=31, duration_sec=700)
    low_candidate.analysis_status = "ok"
    low_candidate.stats_optimization_candidate = module.StatsOptimizationCandidateScore(
        score=20,
        tier="low",
        confidence="low",
        impact="high",
        need_type="insufficient_metadata",
        table_stats_need="unknown",
        column_stats_need="unknown",
        speed_benefit="unknown",
        reasons=("metadata is insufficient for stats classification",),
        counter_signals=("metadata was not collected or is insufficient",),
        suggested_review_areas=(),
        required_confirmation=("compare EXPLAIN before and after stats collection",),
    )

    ranked = module.rank_cases_for_stats_optimization(
        [low_candidate, medium_candidate, high_candidate]
    )

    assert [case.query_id for case in ranked] == ["stats-high", "stats-medium"]
    assert high_candidate.stats_optimization_rank == 1
    assert medium_candidate.stats_optimization_rank == 2
    assert low_candidate.stats_optimization_rank is None


def test_table_stats_status_is_derived_from_analysis_facts(tmp_path):
    module = load_batch_module()
    case = case_result(module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0)
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(suspicious_facts(), encoding="utf-8")

    module.inspect_case_outputs(case)

    assert case.table_stats_status == "available"
    assert module.case_to_summary(case)["table_stats_status"] == "available"


def test_table_stats_status_reports_missing_metadata(tmp_path):
    module = load_batch_module()
    case = case_result(module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0)
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(
        promotable_suspicious_bad_metadata_facts(), encoding="utf-8"
    )

    module.inspect_case_outputs(case)

    assert case.table_stats_status == "missing"


def test_case_score_severity_separates_high_from_suspicious():
    module = load_batch_module()
    suspicious = case_result(
        module,
        index=1,
        query_id="suspicious",
        score=20,
        duration_sec=120,
        cardinality=2,
        memory=1,
    )
    high_from_cardinality = case_result(
        module,
        index=2,
        query_id="high-cardinality",
        score=12,
        duration_sec=120,
        cardinality=5,
    )
    high_from_combination = case_result(
        module,
        index=3,
        query_id="high-combination",
        score=13,
        duration_sec=120,
        cardinality=3,
        memory=2,
    )
    failed = case_result(module, index=4, query_id="failed", score=0)
    failed.analysis_status = "failed"
    failed_metadata = case_result(module, index=5, query_id="failed-metadata", score=0)
    failed_metadata.metadata_status = "failed"
    failed_report = case_result(module, index=6, query_id="failed-report", score=0)
    failed_report.report_validation_status = "failed"
    failed_category = case_result(module, index=7, query_id="failed-category", score=0)
    failed_category.failure_category = "metadata_collection_failed"
    clean_low_primary = case_result(module, index=8, query_id="clean-low-primary", score=0)
    clean_low_primary.case_primary_bottleneck = {
        "label": "sql_shape",
        "confidence": "low",
        "reasons": ["join_top_finding"],
    }
    clean_medium_primary = case_result(module, index=9, query_id="clean-medium-primary", score=0)
    clean_medium_primary.case_primary_bottleneck = {
        "label": "runtime_data_movement",
        "confidence": "medium",
        "reasons": ["large_intermediate_or_exchange_top_finding"],
    }
    clean_high_primary = case_result(module, index=10, query_id="clean-high-primary", score=0)
    clean_high_primary.case_primary_bottleneck = {
        "label": "client_fetch_tail",
        "confidence": "high",
        "reasons": ["client_fetch_wait_top_finding"],
    }

    assert module.case_score_severity(suspicious) == "suspicious"
    assert module.case_score_severity(high_from_cardinality) == "high"
    assert module.case_score_severity(high_from_combination) == "high"
    assert module.case_score_severity(failed) == "failed"
    assert module.case_score_severity(failed_metadata) == "failed"
    assert module.case_score_severity(failed_report) == "failed"
    assert module.case_score_severity(failed_category) == "failed"
    assert module.case_score_severity(clean_low_primary) == "clean"
    assert module.case_score_severity(clean_medium_primary) == "suspicious"
    assert module.case_score_severity(clean_high_primary) == "suspicious"


def test_metadata_refresh_candidates_respect_limit_then_fill_remaining_budget():
    module = load_batch_module()
    config = module.BatchConfig(
        out=Path("/tmp/query-doctor-test/batch"),
        cm_url="https://cm.example",
        cluster="cluster",
        service="impala",
        cm_username=None,
        ca_bundle=None,
        verify_tls=True,
        recent_window_minutes=60,
        cm_inspect_limit=4,
        triage_profile_limit=4,
        metadata_top_limit=3,
        min_duration_sec=None,
        max_duration_sec=None,
        order="duration-desc",
        include_failed=True,
        include_running=False,
        user=None,
        pool=None,
        query_type="QUERY",
        max_profile_bytes=1024,
        collect_cm_events=False,
        cm_events_max_events=50,
        collect_cm_timeseries=False,
        cm_metrics_profile=module.cm_profiles.DEFAULT_CM_METRICS_PROFILE,
        cm_timeseries_top_limit=10,
        cm_timeseries_padding_sec=120,
        max_timeseries_bytes=2097152,
        max_timeseries_points=2000,
        metadata_mode="on",
        metadata_coordinator="impala.example.net:21000",
        metadata_auth="kerberos",
        metadata_protocol="hs2",
        metadata_kerberos_service_name=None,
        metadata_ssl=False,
        metadata_ca_cert=None,
        metadata_timeout_sec=30,
        metadata_max_tables=None,
        metadata_max_output_bytes=None,
        metadata_redact=True,
        top_reports=0,
        cm_jobs=4,
        jobs=4,
        metadata_jobs=1,
        allow_high_jobs=False,
        discover_only=False,
        overwrite=False,
        config_path=None,
        progress_jsonl=None,
        krb5ccname=None,
    )
    clean = case_result(module, index=1, query_id="clean", score=0)
    low_suspicious = case_result(
        module, index=2, query_id="low-suspicious", score=12, cardinality=2
    )
    promotable_suspicious = case_result(
        module, index=3, query_id="promotable-suspicious", score=23, cardinality=2
    )
    high = case_result(module, index=4, query_id="high", score=12, cardinality=5)
    high_lower_score = case_result(module, index=5, query_id="high-low-score", score=10, memory=4)
    cases = [clean, low_suspicious, promotable_suspicious, high, high_lower_score]
    for case in cases:
        case.analysis_status = "ok"
        case.metadata_status = "skipped"

    selected = module.metadata_refresh_candidates(config, cases)

    assert [case.query_id for case in selected] == [
        "high",
        "high-low-score",
        "promotable-suspicious",
    ]
    assert low_suspicious.metadata_status == "not_requested"
    assert clean.metadata_status == "not_requested"

    for case in cases:
        case.metadata_status = "skipped"
    fill_config = module.replace(config, metadata_top_limit=5)

    filled_selected = module.metadata_refresh_candidates(fill_config, cases)

    assert [case.query_id for case in filled_selected] == [
        "high",
        "high-low-score",
        "promotable-suspicious",
        "low-suspicious",
        "clean",
    ]


def test_metadata_refresh_candidates_include_top_optimization_candidates():
    module = load_batch_module()
    high = case_result(module, index=1, query_id="high", score=12, cardinality=5, duration_sec=120)
    optimization_high = case_result(module, index=2, query_id="opt-high", score=4, duration_sec=300)
    optimization_high.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=72,
        tier="high",
        confidence="medium",
        impact="high",
        reasons=("large exchange volume before downstream processing",),
        counter_signals=("metadata missing",),
        suggested_review_areas=("pre-aggregation before exchange",),
    )
    optimization_medium = case_result(
        module, index=3, query_id="opt-medium", score=3, duration_sec=600
    )
    optimization_medium.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=48,
        tier="medium",
        confidence="medium",
        impact="high",
        reasons=("backend data skew supports distribution review",),
        counter_signals=("metadata missing",),
        suggested_review_areas=("data distribution",),
    )
    suspicious = case_result(
        module, index=4, query_id="suspicious", score=23, cardinality=2, duration_sec=180
    )
    clean = case_result(module, index=5, query_id="clean", score=0, duration_sec=900)
    cases = [clean, suspicious, optimization_medium, high, optimization_high]
    for case in cases:
        case.analysis_status = "ok"
        case.metadata_status = "skipped"

    selected = module.select_metadata_refresh_candidates(cases, limit=4)

    assert [case.query_id for case in selected] == ["high", "opt-high", "opt-medium", "suspicious"]
    assert clean.metadata_status == "skipped"


def test_metadata_refresh_candidates_can_fill_remaining_budget_for_direct_impala():
    module = load_batch_module()
    high = case_result(module, index=1, query_id="high", score=12, cardinality=5, duration_sec=120)
    clean = case_result(module, index=2, query_id="clean", score=0, duration_sec=300)
    for case in (high, clean):
        case.analysis_status = "ok"
        case.metadata_status = "skipped"

    selected = module.select_metadata_refresh_candidates(
        [clean, high], limit=2, include_remaining=True
    )

    assert [case.query_id for case in selected] == ["high", "clean"]


def test_top_metadata_refresh_does_not_spend_limit_on_placeholder_only_tables(
    tmp_path, monkeypatch
):
    module = load_batch_module()
    args = module.parse_args(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-top-limit",
            "2",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=REPO_DIR, repo_root=REPO_DIR)
    placeholder = case_result(
        module, index=1, query_id="placeholder-only", score=40, duration_sec=300
    )
    collectable_high = case_result(
        module, index=2, query_id="collectable-high", score=40, duration_sec=200
    )
    collectable_next = case_result(
        module, index=3, query_id="collectable-next", score=40, duration_sec=100
    )
    cases = [placeholder, collectable_high, collectable_next]

    def facts_with_tables(*tables: str) -> str:
        return "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Parsed operators: 2",
                "- Cardinality anomalies: 5",
                "- Memory anomalies: 0",
                "",
                "## SQL Context",
                "- default_database: `warehouse`",
                "",
                "## Referenced Tables",
                *(f"- `{table}`" for table in tables),
                "",
            ]
        )

    for case, tables in (
        (placeholder, ("db.table",)),
        (collectable_high, ("example_warehouse.table_a",)),
        (collectable_next, ("example_warehouse.table_b",)),
    ):
        case.analysis_status = "ok"
        case.metadata_status = "skipped"
        case.actual_case_dir = tmp_path / f"case-{case.index:03d}" / "actual"
        write_case(case.actual_case_dir, facts_with_tables(*tables))
        module.inspect_case_outputs(case)

    refreshed = []

    def fake_refresh(config, case, *, env, repo_root, progress):
        refreshed.append(case.query_id)
        case.metadata_refreshed = True
        case.metadata_status = "collected"
        return case

    monkeypatch.setattr(module.batch_case_processing, "refresh_case_metadata", fake_refresh)
    progress = module.ProgressWriter(None)

    module.batch_case_processing.refresh_top_metadata(
        config, cases, env=auth_env(), repo_root=REPO_DIR, progress=progress
    )

    assert refreshed == ["collectable-high", "collectable-next"]
    assert placeholder.metadata_refreshed is False
    assert placeholder.metadata_status == "not_requested"

    summary = build_summary_for_test_cases(
        module,
        config,
        cases,
        discovery=module.DiscoveryResult([], [], "none", None),
        discovery_seconds=0.0,
        total_seconds=0.0,
    )
    rows = {row["query_id"]: row for row in summary["cases"]}

    assert rows["placeholder-only"]["referenced_table_count"] == 1
    assert rows["placeholder-only"]["collectable_metadata_table_count"] == 0
    assert rows["placeholder-only"]["metadata_status"] == "not_requested"
    assert rows["collectable-high"]["collectable_metadata_table_count"] == 1
    assert rows["collectable-next"]["collectable_metadata_table_count"] == 1
    assert summary["collectable_metadata_table_count_distribution"] == {"0": 1, "1": 2}


def test_top_metadata_refresh_uses_discovery_statement_refs_before_redaction(tmp_path, monkeypatch):
    module = load_batch_module()
    args = module.parse_args(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-top-limit",
            "1",
        ]
    )
    config = module.build_batch_config(args, env=auth_env(), cwd=REPO_DIR, repo_root=REPO_DIR)
    case = case_result(module, index=1, query_id="redacted-facts", score=40, duration_sec=300)
    case.analysis_status = "ok"
    case.metadata_status = "skipped"
    case.metadata_source_tables = ("example_warehouse.real_table",)
    case.actual_case_dir = tmp_path / "case-001" / "actual"
    write_case(
        case.actual_case_dir,
        "\n".join(
            [
                "# Query Doctor Analysis Facts",
                "",
                "## Summary",
                "- Parsed operators: 2",
                "- Cardinality anomalies: 5",
                "",
                "## SQL Context",
                "- default_database: `warehouse`",
                "",
                "## Referenced Tables",
                "- `db.table`",
                "",
            ]
        ),
    )

    refreshed = []

    def fake_refresh(config, case, *, env, repo_root, progress):
        refreshed.append(case.query_id)
        case.metadata_refreshed = True
        case.metadata_status = "collected"
        return case

    monkeypatch.setattr(module.batch_case_processing, "refresh_case_metadata", fake_refresh)

    module.batch_case_processing.refresh_top_metadata(
        config, [case], env=auth_env(), repo_root=REPO_DIR, progress=module.ProgressWriter(None)
    )

    assert refreshed == ["redacted-facts"]


def test_metadata_subprocess_env_carries_source_tables_without_mutating_base_env():
    module = load_batch_module()
    case = case_result(module, index=1, query_id="source-env", score=0)
    case.metadata_source_tables = ("example_warehouse.real_table",)
    base_env = {"KRB5CCNAME": "FILE:/tmp/cache"}

    case_env = module.batch_case_processing.metadata_subprocess_env(base_env, case)

    assert base_env == {"KRB5CCNAME": "FILE:/tmp/cache"}
    assert json.loads(case_env["QD_METADATA_SOURCE_TABLES_JSON"]) == [
        "example_warehouse.real_table"
    ]


def test_metadata_refresh_optimization_candidates_prefer_draftable_rewriteability():
    module = load_batch_module()

    human_review = case_result(module, index=1, query_id="human-review", score=4, duration_sec=900)
    human_review.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=95,
        tier="high",
        confidence="high",
        impact="high",
        reasons=("large exchange volume before downstream processing",),
        counter_signals=("metadata missing",),
        suggested_review_areas=("pre-aggregation before exchange",),
    )
    human_review.optimizer_rewrite_support = guidance_human_review_support()
    draftable = case_result(module, index=2, query_id="draftable", score=3, duration_sec=300)
    draftable.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=70,
        tier="high",
        confidence="medium",
        impact="medium",
        reasons=("large exchange volume before downstream processing",),
        counter_signals=("metadata missing",),
        suggested_review_areas=("filter placement",),
    )
    draftable.optimizer_rewrite_support = safe_material_draft_support()
    cases = [human_review, draftable]
    for case in cases:
        case.analysis_status = "ok"
        case.metadata_status = "skipped"

    selected = module.select_metadata_refresh_candidates(cases, limit=1)

    assert [case.query_id for case in selected] == ["draftable"]


def test_metadata_refresh_candidates_respect_limit_with_optimization_candidates():
    module = load_batch_module()
    high = case_result(module, index=1, query_id="high", score=12, cardinality=5, duration_sec=120)
    optimization = case_result(module, index=2, query_id="opt", score=4, duration_sec=900)
    optimization.query_optimization_candidate = module.QueryOptimizationCandidateScore(
        score=80,
        tier="high",
        confidence="medium",
        impact="high",
        reasons=("large exchange volume before downstream processing",),
        counter_signals=("metadata missing",),
        suggested_review_areas=("pre-aggregation before exchange",),
    )
    suspicious = case_result(
        module, index=3, query_id="suspicious", score=23, cardinality=2, duration_sec=180
    )
    cases = [suspicious, optimization, high]
    for case in cases:
        case.analysis_status = "ok"
        case.metadata_status = "skipped"

    selected = module.select_metadata_refresh_candidates(cases, limit=2)

    assert [case.query_id for case in selected] == ["high", "opt"]


def test_metadata_refresh_query_optimization_candidates_use_remaining_budget():
    module = load_batch_module()
    optimization_cases = []
    for index in range(1, 26):
        case = case_result(
            module,
            index=index,
            query_id=f"opt-{index:02d}",
            score=4,
            duration_sec=300 + index,
        )
        case.query_optimization_candidate = module.QueryOptimizationCandidateScore(
            score=55,
            tier="medium",
            confidence="medium",
            impact="medium",
            reasons=("large exchange volume before downstream processing",),
            counter_signals=("metadata missing",),
            suggested_review_areas=("filter placement",),
        )
        case.analysis_status = "ok"
        case.metadata_status = "skipped"
        optimization_cases.append(case)

    selected = module.select_metadata_refresh_candidates(optimization_cases, limit=25)

    assert len(selected) == 25
    assert [case.query_id for case in selected] == [
        f"opt-{index:02d}" for index in range(25, 0, -1)
    ]


def test_metadata_refresh_candidates_apply_bad_and_suspicious_policy_limits():
    module = load_batch_module()
    high_cases = [
        case_result(module, index=index, query_id=f"high-{index}", score=12, cardinality=5)
        for index in range(1, 55)
    ]
    suspicious_cases = [
        case_result(
            module, index=100 + index, query_id=f"suspicious-{index}", score=23, cardinality=2
        )
        for index in range(1, 25)
    ]
    for case in high_cases + suspicious_cases:
        case.analysis_status = "ok"
        case.metadata_status = "skipped"

    selected = module.select_metadata_refresh_candidates(high_cases + suspicious_cases, limit=70)

    assert sum(1 for case in selected if case.query_id.startswith("high-")) == 50
    assert sum(1 for case in selected if case.query_id.startswith("suspicious-")) == 20


def test_scoring_uses_summary_counts_not_detail_bullets():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 5",
            "- Memory anomalies: 4",
            "",
            "### Cardinality estimate errors [medium]",
            "- Operators:",
            "- operator 01 underestimated rows",
            "- operator 02 underestimated rows",
            "- operator 03 underestimated rows",
            "- operator 04 underestimated rows",
            "- operator 05 underestimated rows",
            "- Detail wrapper that should not change the count",
            "",
            "### Memory estimate errors [medium]",
            "- Operators:",
            "- operator 01 underestimated memory",
            "- operator 02 underestimated memory",
            "- operator 03 underestimated memory",
            "- operator 04 underestimated memory",
            "- Detail wrapper that should not change the count",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 20
    assert "cardinality estimate anomalies: 5" in reasons
    assert "memory estimate anomalies: 4" in reasons
    assert "cardinality estimate anomalies: 7" not in reasons
    assert "memory estimate anomalies: 6" not in reasons


def test_scoring_zero_anomaly_counts_and_negative_evidence_do_not_score():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 0",
            "- Zero/unknown memory estimate gaps: 0",
            "",
            "### Cardinality estimate errors [medium]",
            "- No cardinality estimate anomaly was parsed.",
            "",
            "### Memory estimate errors [medium]",
            "- No memory estimate anomaly was parsed.",
            "",
            "### Spill or scratch I/O [medium]",
            "- No non-zero spill/scratch I/O evidence was parsed.",
            "- spill/scratch evidence: not_observed",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 12",
            "- host tail candidates: 0",
            "- data skew: unknown (not enough comparable backends)",
            "- execution skew: not_observed",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_scores_zero_estimate_gaps():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 2",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 1",
            "- Zero/unknown memory estimate gaps: 1",
            "",
            "## Zero/unknown row estimate gaps",
            "| operator | time | actual rows | estimated rows | rows ratio | peak mem | est. peak mem | mem ratio |",
            "| 12:HASH JOIN | 2s | 2.00M | 0 | n/a | 256.00 MiB | 0 B | n/a |",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score > 0
    assert "zero/unknown row estimate gaps: 1" in reasons
    assert "zero/unknown memory estimate gaps: 1" in reasons
    assert "cardinality estimate anomalies" not in " ".join(reasons)
    assert "memory estimate anomalies" not in " ".join(reasons)


def test_scoring_does_not_score_zero_gap_labels_when_counts_are_zero():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 1",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 0",
            "- Zero/unknown memory estimate gaps: 0",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_suppresses_short_stats_hygiene_only_attention():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 1",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 0",
            "- Zero/unknown memory estimate gaps: 0",
            "",
            "## CM Query Context",
            "- duration: 12.0s",
            "",
            "## Table Metadata Context",
            "- table stats row-count completeness: missing/unknown",
            "- column stats completeness: incomplete/unknown",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_keeps_longer_stats_hygiene_attention():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 1",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 0",
            "- Zero/unknown memory estimate gaps: 0",
            "",
            "## CM Query Context",
            "- duration: 1.0m",
            "",
            "## Table Metadata Context",
            "- table stats row-count completeness: missing/unknown",
            "- column stats completeness: incomplete/unknown",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 3
    assert reasons == [
        "table stats row-count completeness missing/unknown",
        "column stats completeness incomplete/unknown",
    ]


def test_scoring_prefers_structured_limited_memory_pressure_over_legacy_findings():
    module = load_batch_module()
    from query_doctor.recent.query_optimization_score import (
        has_supported_spill_scratch_evidence as query_has_supported_spill,
    )

    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 0",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Memory Pressure Evidence",
            "",
            "- status: context_only",
            "- evidence_tier: context_only",
            "- promotion_policy: limited",
            "- section_mapping: limited",
            "- finding_supported: no",
            "- spill_or_scratch_evidence_count: 0",
            "- limited_spill_or_scratch_counter_count: 1",
            "- limitations:",
            (
                "  - Non-zero spill/scratch counters were parsed as limited context, but "
                "this profile dialect or section is not mapped for memory-pressure promotion."
            ),
            "",
            "## Findings",
            "",
            "### Spill or scratch I/O [medium]",
            "- Detected non-zero spill/scratch metric evidence in digest lines.",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]
    assert query_has_supported_spill(facts) is False


def test_scoring_scores_supported_spill_and_backend_facts():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "### Spill or scratch I/O [medium]",
            "- Detected non-zero spill/scratch metric evidence in digest lines.",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 12",
            "- host tail candidates: 2",
            "- data skew: yes (max rows are 5.2x median)",
            "- execution skew: no",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 17
    assert "spill/scratch evidence: non-zero metrics" in reasons
    assert "host-tail candidates: 2" in reasons
    assert "backend data skew evidence" in reasons
    assert all("mentioned by analyzer" not in reason for reason in reasons)


def test_scoring_marks_long_running_host_tail_as_high_severity():
    module = load_batch_module()
    case = case_result(
        module,
        index=1,
        query_id="long-tail",
        score=13,
        duration_sec=3238,
        host_tail=1,
    )

    assert module.case_score_severity(case) == "high"


def test_scoring_does_not_mark_long_running_write_path_tail_as_high_severity():
    module = load_batch_module()
    case = case_result(
        module,
        index=1,
        query_id="long-write-path-tail",
        score=8,
        duration_sec=3238,
        host_tail=1,
        execution_tail=0,
    )

    assert module.case_score_severity(case) == "suspicious"


def test_scoring_boosts_long_running_host_tail_score():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## CM Query Context",
            "- duration: 54.0m",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 28",
            "- host tail candidates: 1",
            "- data skew: no (F03: assigned/read work appears comparable)",
            "- execution skew: yes",
            "- write-path anomaly: no",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 16
    assert "host-tail candidates: 1" in reasons
    assert "long-running query with host tail: 54.0m" in reasons


def test_scoring_does_not_long_running_boost_write_path_only_tail():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## CM Query Context",
            "- duration: 54.0m",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 28",
            "- host tail candidates: 1",
            "- execution tail candidates: 0",
            "- read-rate tail candidates: 0",
            "- write-path tail candidates: 1",
            "- data skew: no (F03: assigned/read work appears comparable)",
            "- execution skew: no",
            "- write-path anomaly: yes",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 8
    assert reasons == ["host-tail candidates: 1"]


def test_scoring_can_use_normalized_tail_candidates_when_summary_counts_missing():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## CM Query Context",
            "- duration: 54.0m",
            "",
            "## Backend / Host Tail Evidence",
            "",
            "### Summary",
            "",
            "- data skew: no",
            "- execution skew: yes",
            "- write-path anomaly: no",
            "",
            "### Normalized tail candidates",
            "",
            "| host | fragment | family | metric_key | worst | peer | gap | ratio |",
            "|---|---|---|---|---:|---:|---:|---:|",
            "| host_01 | F03 | execution | execution_time_ms | 54.00m | 26.40m | 27.60m | 2.05x |",
            "| host_01 | F03 | read_rate | read_rate_bps | 40.00 MiB/s | 100.00 MiB/s | 60.00 MiB/s | 2.50x |",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 16
    assert reasons == [
        "host-tail candidates: 1",
        "long-running query with host tail: 54.0m",
    ]


def test_scoring_boosts_severe_backend_data_skew_without_claiming_tail():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 9",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 28",
            "- host tail candidates: 0",
            "- data skew: yes (rows produced max/min ratio is 52.4x)",
            "- execution skew: no",
            "- write-path anomaly: unknown",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 10
    assert "backend data skew evidence" in reasons
    assert "severe backend data skew ratio: 52.4x" in reasons
    assert all("tail" not in reason for reason in reasons)


def test_scoring_does_not_boost_context_only_scan_skew():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 9",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Scan Skew Evidence",
            "- status: context_only",
            "- evidence_tier: context_only",
            "- finding_supported: no",
            "- primary_supported: no",
            "- skew_ratio: n/a",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 28",
            "- host tail candidates: 0",
            "- data skew: yes (rows produced max/min ratio is 52.4x)",
            "- execution skew: no",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_does_not_boost_moderate_backend_data_skew():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 9",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 28",
            "- host tail candidates: 0",
            "- data skew: yes (rows produced max/min ratio is 5.2x)",
            "- execution skew: no",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 2
    assert reasons == ["backend data skew evidence"]


def test_scoring_adds_small_bonus_only_for_correlated_cm_metrics():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## CM Metrics Facts",
            "- status: available",
            "- coverage: 4/4 metrics ok, 40 points",
            "- daemon_memory_growth: observed",
            "- network_io_spike: observed",
            "",
            "## CM Metrics Correlation",
            "- status: available",
            "- correlated_signals: 2",
            "- context_only_signals: 0",
            "- daemon_memory_growth: correlated (metric=observed, strength=moderate)",
            "- network_io_spike: correlated (metric=observed, strength=moderate)",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 4
    assert reasons == ["Runtime metrics correlated signals: 2"]


def test_scoring_accepts_provider_neutral_runtime_metrics_correlation_heading():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Runtime Metrics Correlation",
            "- status: available",
            "- correlated_signals: 2",
            "- context_only_signals: 0",
            "- daemon_memory_growth: correlated (metric=observed, strength=moderate)",
            "- network_io_spike: correlated (metric=observed, strength=moderate)",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 4
    assert reasons == ["Runtime metrics correlated signals: 2"]


def test_scoring_does_not_score_context_only_cm_metrics():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## CM Metrics Facts",
            "- status: available",
            "- coverage: 4/4 metrics ok, 40 points",
            "- daemon_memory_growth: observed",
            "- network_io_spike: observed",
            "",
            "## CM Metrics Correlation",
            "- status: available",
            "- correlated_signals: 0",
            "- context_only_signals: 2",
            "- daemon_memory_growth: context_only (metric=observed, strength=weak)",
            "- network_io_spike: context_only (metric=observed, strength=weak)",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_ignores_matching_labels_outside_owned_sections():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 0",
            "- Zero/unknown memory estimate gaps: 0",
            "",
            "## CM Query Context",
            "- duration: 1.0m",
            "",
            "## Backend / Host Tail Evidence",
            "- backend rows parsed: 0",
            "- host tail candidates: 0",
            "- data skew: no",
            "- execution skew: no",
            "",
            "## CM Metrics Correlation",
            "- status: available",
            "- correlated_signals: 0",
            "",
            "## Cluster Runtime Context",
            "- duration: 2h",
            "- host tail candidates: 9",
            "- data skew: yes (rows produced max/min ratio is 99.0x)",
            "- correlated_signals: 99",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_uses_exact_markdown_heading_matches():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary By Operator",
            "- Cardinality anomalies: 9",
            "- Memory anomalies: 9",
            "",
            "## Summary",
            "- Parsed operators: 8",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "- Zero/unknown row estimate gaps: 0",
            "- Zero/unknown memory estimate gaps: 0",
            "",
            "## Backend / Host Tail Evidence",
            "- host tail candidates: 0",
            "- execution tail candidates: 0",
            "- data skew: no",
            "- execution skew: no",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_scoring_preserves_flat_legacy_facts_compatibility():
    module = load_batch_module()
    facts = "\n".join(
        [
            "- Parsed operators: 7",
            "- Cardinality anomalies: 2",
            "- Memory anomalies: 1",
            "- table stats row-count completeness: available",
        ]
    )

    score, reasons = module.score_analysis_facts(facts)

    assert score == 8
    assert reasons == [
        "cardinality estimate anomalies: 2",
        "memory estimate anomalies: 1",
    ]


def test_scoring_metadata_failure_still_scores():
    module = load_batch_module()
    facts = "\n".join(
        [
            "# Query Doctor Analysis Facts",
            "",
            "## Summary",
            "- Parsed operators: 1",
            "- Cardinality anomalies: 0",
            "- Memory anomalies: 0",
            "",
            "## Table Metadata Context",
            "- SHOW CREATE TABLE status: error",
            "",
        ]
    )

    score, reasons = module.score_analysis_facts(facts, metadata_status="failed")

    assert score == 3
    assert reasons == ["metadata collection failed for referenced table"]


def test_scoring_does_not_penalize_view_stats_not_applicable():
    module = load_batch_module()

    score, reasons = module.score_analysis_facts(
        view_metadata_facts(),
        metadata_status="collected",
    )

    assert score == 0
    assert reasons == ["no analyzer-supported suspicious facts"]


def test_collection_failure_recorded_and_batch_continues(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]

    patch_discovered_candidates(module, monkeypatch, selected)

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = command_query_id(cmd)
            if query_id.startswith("aaaa"):
                return completed(1, stderr="HTTP 500 Server Error")
            write_collected_case_from_command(cmd)
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(base_args(tmp_path) + ["--jobs", "2"], env=auth_env())

    assert result == 0
    payload = read_batch_summary(tmp_path)
    statuses = {case["query_id"]: case["collection_status"] for case in payload["cases"]}
    assert statuses["aaaaaaaaaaaaaaaa:0000000000000001"] == "failed"
    assert statuses["bbbbbbbbbbbbbbbb:0000000000000002"] == "ok"
    failed = next(
        case for case in payload["cases"] if case["query_id"] == "aaaaaaaaaaaaaaaa:0000000000000001"
    )
    assert_non_negative_number(failed["cm_collect_seconds"])
    assert failed["analysis_seconds"] is None
    assert failed["report_seconds"] is None
    assert failed["failure_category"] == "profile_collection_failed"
    assert failed["failure_reason"] == "Cloudera Manager profile collection returned HTTP 500."
    assert "raw-subprocess-secret" not in json.dumps(payload)


@pytest.mark.parametrize("analyzer_jobs", [1, 2])
def test_cm_http_5xx_collection_circuit_breaker_stops_new_profile_jobs(
    tmp_path, monkeypatch, analyzer_jobs
):
    module = load_batch_module()
    progress_path = batch_dir(tmp_path) / "progress.jsonl"
    selected = [
        candidate(module, f"{index:016x}:0000000000000001", 61000 + index) for index in range(1, 13)
    ]

    patch_discovered_candidates(module, monkeypatch, selected)
    collected_query_ids = []

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            collected_query_ids.append(command_query_id(cmd))
            return completed(1, stderr="HTTP 500 Server Error raw-subprocess-secret")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--jobs",
            str(analyzer_jobs),
            "--cm-jobs",
            "2",
            "--progress-jsonl",
            str(progress_path),
        ],
        env=auth_env(),
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert any(
        "Cloudera Manager profile collection was stopped after repeated HTTP 5xx responses"
        in warning
        for warning in payload["warnings"]
    )
    cases = payload["cases"]
    assert any(case["collection_status"] == "skipped" for case in cases)
    assert len(set(collected_query_ids)) < len(selected)
    assert "raw-subprocess-secret" not in json.dumps(payload)

    events = read_jsonl(progress_path)
    stopped = [
        event
        for event in events
        if event["stage"] == "profile_collection" and event["status"] == "stopped"
    ]
    assert stopped
    assert stopped[0]["reason"] == "cm_http_5xx_circuit_breaker"
    assert stopped[0]["http_5xx_failures"] >= 5


def test_cm_http_5xx_collection_circuit_breaker_does_not_stop_direct_impala(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [
        candidate(module, f"{index:016x}:0000000000000001", 61000 + index) for index in range(1, 8)
    ]

    patch_discovered_candidates(module, monkeypatch, selected)
    collected_query_ids = []

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_impala_profile"):
            collected_query_ids.append(command_query_id(cmd))
            return completed(1, stderr="HTTP 500 Server Error")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--query-profile-source",
            "impala",
            "--impala-profile-host",
            "impalad-1.example.com",
            "--jobs",
            "1",
            "--cm-jobs",
            "2",
        ],
        env={},
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    assert len(set(collected_query_ids)) == len(selected)
    assert not any("repeated HTTP 5xx" in warning for warning in payload["warnings"])
    assert {case["collection_status"] for case in payload["cases"]} == {"failed"}


def test_collection_transient_failure_retried_before_analysis(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
    ]

    patch_discovered_candidates(module, monkeypatch, selected)

    attempts = 0

    def fake_run(cmd, cwd, env):
        nonlocal attempts
        if command_uses_role(cmd, "collect_cm"):
            attempts += 1
            if attempts == 1:
                return completed(1, stderr="temporary collector failure")
            write_collected_case_from_command(cmd)
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(base_args(tmp_path), env=auth_env())

    assert result == 0
    assert attempts == 2
    payload = read_batch_summary(tmp_path)
    [case] = payload["cases"]
    assert case["collection_status"] == "ok"
    assert case["analysis_status"] == "ok"
    assert case["failure_category"] is None


def test_collection_client_error_is_not_retried(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000)]

    patch_discovered_candidates(module, monkeypatch, selected)

    attempts = 0

    def fake_run(cmd, cwd, env):
        nonlocal attempts
        if command_uses_role(cmd, "collect_cm"):
            attempts += 1
            return completed(1, stderr="HTTP 404 Not Found")
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(base_args(tmp_path), env=auth_env())

    assert result == 0
    assert attempts == 1
    payload = read_batch_summary(tmp_path)
    [case] = payload["cases"]
    assert case["collection_status"] == "failed"
    assert case["failure_category"] == "profile_collection_failed"
    assert case["failure_reason"] == "Cloudera Manager profile collection returned HTTP 404."


def test_collection_timeout_recorded_safely_and_batch_continues(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]

    patch_discovered_candidates(module, monkeypatch, selected)

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            query_id = command_query_id(cmd)
            if query_id.startswith("aaaa"):
                return completed(module.batch_case_processing.SUBPROCESS_TIMEOUT_RETURN_CODE)
            write_collected_case_from_command(cmd)
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(base_args(tmp_path) + ["--jobs", "2"], env=auth_env())

    assert result == 0
    payload = read_batch_summary(tmp_path)
    timed_out = next(
        case for case in payload["cases"] if case["query_id"] == "aaaaaaaaaaaaaaaa:0000000000000001"
    )
    assert timed_out["collection_status"] == "timeout"
    assert timed_out["failure_category"] == "profile_collection_timeout"
    assert (
        timed_out["failure_reason"]
        == "Profile collection timed out before a profile digest was produced."
    )
    assert "tool.py" not in json.dumps(payload)


def test_metadata_failure_recorded_and_batch_continues(tmp_path, monkeypatch):
    module = load_batch_module()
    allow_metadata_auth_preflight(monkeypatch)
    selected = [
        candidate(module, "aaaaaaaaaaaaaaaa:0000000000000001", 61000),
        candidate(module, "bbbbbbbbbbbbbbbb:0000000000000002", 62000),
    ]

    patch_discovered_candidates(module, monkeypatch, selected)

    def fake_run(cmd, cwd, env):
        if command_uses_role(cmd, "collect_cm"):
            write_collected_case_from_command(cmd)
        elif command_uses_role(cmd, "pipeline") and "--stop-after-analysis" in cmd:
            assert cmd[cmd.index("--metadata-failure-policy") + 1] == "continue"
            metadata_mode = cmd[cmd.index("--metadata-mode") + 1]
            case_dir = Path(command_args(cmd, "pipeline")[0])
            if "aaaaaaaa" in str(case_dir):
                facts = (
                    promotable_suspicious_bad_metadata_facts()
                    if metadata_mode != "off"
                    else promotable_suspicious_facts()
                )
                (case_dir / "analysis_facts.md").write_text(facts, encoding="utf-8")
            else:
                (case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
            if metadata_mode != "off" and "aaaaaaaa" in str(case_dir):
                (case_dir / "impala_context.json").write_text(
                    json.dumps({"tables": ["db.table"], "results": [{"status": "error"}]}),
                    encoding="utf-8",
                )
                return completed(0)
        return completed()

    monkeypatch.setattr(module, "run_subprocess", fake_run)

    result = module.main(
        base_args(tmp_path)
        + [
            "--metadata-mode",
            "on",
            "--metadata-coordinator",
            "impala.example.net:21000",
            "--metadata-impala-shell",
            "impala-shell",
            "--metadata-top-limit",
            "1",
        ],
        env={**auth_env(), "KRB5CCNAME": "FILE:/tmp/krb5cc_test"},
    )

    assert result == 0
    payload = read_batch_summary(tmp_path)
    failed = [
        case
        for case in payload["cases"]
        if case["failure_category"] == "metadata_collection_failed"
    ]
    assert failed
    assert (
        failed[0]["failure_reason"]
        == "Metadata collection failed for this case; deterministic profile facts may still be available."
    )
    assert failed[0]["analysis_status"] == "ok"
    assert failed[0]["metadata_status"] == "failed"
    assert failed[0]["score"] > 0
    assert failed[0]["score_severity"] == "failed"
    assert "metadata collection failed for referenced table" in failed[0]["score_reasons"]
    assert_non_negative_number(failed[0]["cm_collect_seconds"])
    assert_non_negative_number(failed[0]["analysis_seconds"])
    assert failed[0]["report_seconds"] is None


def test_metadata_mixed_ok_and_error_is_partial(tmp_path):
    module = load_batch_module()
    case = case_result(module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0)
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
    (case.actual_case_dir / "impala_context.json").write_text(
        json.dumps({"tables": ["db.table"], "results": [{"status": "ok"}, {"status": "error"}]}),
        encoding="utf-8",
    )

    module.inspect_case_outputs(case)

    assert case.metadata_status == "partial"
    assert case.collected_metadata_table_count == 1


def test_metadata_mixed_ok_and_limited_status_is_partial(tmp_path):
    module = load_batch_module()
    case = case_result(module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0)
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
    (case.actual_case_dir / "impala_context.json").write_text(
        json.dumps(
            {
                "tables": ["db.table"],
                "results": [
                    {"status": "ok"},
                    {"status": "timeout"},
                    {"status": "too_large"},
                    {"status": "not_applicable"},
                ],
            }
        ),
        encoding="utf-8",
    )

    module.inspect_case_outputs(case)

    assert case.metadata_status == "partial"
    assert case.too_large_count == 1
    assert case.collected_metadata_table_count == 1


def test_metadata_limited_status_without_ok_is_failed(tmp_path):
    module = load_batch_module()
    case = case_result(module, index=1, query_id="aaaaaaaaaaaaaaaa:0000000000000001", score=0)
    case.actual_case_dir = tmp_path / "case"
    case.actual_case_dir.mkdir()
    (case.actual_case_dir / "analysis_facts.md").write_text(healthy_facts(), encoding="utf-8")
    (case.actual_case_dir / "impala_context.json").write_text(
        json.dumps({"tables": ["db.table"], "results": [{"status": "timeout"}]}),
        encoding="utf-8",
    )

    module.inspect_case_outputs(case)

    assert case.metadata_status == "failed"


def test_summaries_do_not_contain_full_sql_or_secrets(tmp_path, monkeypatch):
    module = load_batch_module()
    selected = candidate(
        module,
        "aaaaaaaaaaaaaaaa:0000000000000001",
        61000,
        statement="SELECT secret_password FROM example_guarded.table",
    )

    monkeypatch.setattr(
        module,
        "discover_candidates",
        lambda config, env: module.DiscoveryResult([selected], [], "client-side", None),
    )

    result = module.main(base_args(tmp_path) + ["--discover-only"], env=auth_env())

    assert result == 0
    text = (batch_dir(tmp_path) / "batch_summary.json").read_text()
    assert "SELECT secret_password" not in text
    assert "example_guarded.table" not in text
    assert "secret" not in text
    assert "aaaaaaaaaaaaaaaa:0000000000000001" in text


def test_run_subprocess_uses_argv_list_and_shell_false(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_subprocess_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return completed()

    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

    result = module.run_subprocess([sys.executable, "tool.py"], cwd=tmp_path, env={})

    assert result.returncode == 0
    assert calls[0][0] == [sys.executable, "tool.py"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == module.batch_case_processing.DEFAULT_SUBPROCESS_TIMEOUT_SEC


def test_run_subprocess_applies_stage_timeout_and_returns_safe_timeout(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []
    cmd = module.command_prefix(REPO_DIR, "collect_cm")

    def fake_subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

    result = module.run_subprocess(cmd, cwd=tmp_path, env={})

    assert result.returncode == module.batch_case_processing.SUBPROCESS_TIMEOUT_RETURN_CODE
    assert calls[0][1]["timeout"] == module.batch_case_processing.PROFILE_COLLECTION_TIMEOUT_SEC


def test_run_subprocess_applies_runtime_metrics_refresh_timeout(monkeypatch, tmp_path):
    module = load_batch_module()
    calls = []

    def fake_subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        return completed()

    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

    cm_cmd = module.command_prefix(REPO_DIR, "collect_cm") + ["--collect-cm-timeseries"]
    prometheus_cmd = module.command_prefix(REPO_DIR, "collect_impala_profile") + [
        "--collect-prometheus-timeseries"
    ]

    assert module.run_subprocess(cm_cmd, cwd=tmp_path, env={}).returncode == 0
    assert module.run_subprocess(prometheus_cmd, cwd=tmp_path, env={}).returncode == 0

    assert calls[0][1]["timeout"] == (
        module.batch_case_processing.RUNTIME_METRICS_REFRESH_TIMEOUT_SEC
    )
    assert calls[1][1]["timeout"] == (
        module.batch_case_processing.RUNTIME_METRICS_REFRESH_TIMEOUT_SEC
    )


def test_direct_impala_onboarding_template_builds_bounded_history_config(tmp_path):
    module = load_batch_module()
    template = REPO_DIR / "query-doctor-config.direct-impala.example.json"
    args = module.parse_args(
        [
            "--config",
            str(template),
            "--out",
            str(tmp_path / "query-doctor-direct-impala-readiness"),
            "--discover-only",
            "--metadata-mode",
            "off",
            "--top-reports",
            "0",
        ]
    )

    config = module.build_batch_config(args, env={}, cwd=tmp_path, repo_root=REPO_DIR)
    module.preflight(config, env={})

    assert config.query_profile_source == "impala"
    assert config.impala_profile_hosts == ("impalad-coordinator.example.com",)
    assert config.impala_profile_scheme == "https"
    assert config.include_running is True
    assert config.discover_only is True
    assert config.metadata_mode == "off"
    assert config.top_reports == 0
    assert config.recent_history_backend == "sqlite"
    assert (
        config.recent_history_db
        == (tmp_path / "query-doctor-state" / "recent-history.sqlite3").resolve()
    )
    assert config.recent_history_summary_retention_days == 30
