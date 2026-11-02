"""Stable source IDs keep independent collectors and retained jobs apart."""

from dataclasses import replace
from pathlib import Path

import pytest

from query_doctor.cli import batch_recent
from query_doctor.config.contract import ConfigError, normalize_config_value
from query_doctor.recent.batch_config import build_batch_config
from query_doctor.recent.history_store import recent_history_source_key


def config(tmp_path, *extra):
    args = batch_recent.parse_args(
        [
            "--out",
            str(tmp_path / "query-doctor-output"),
            "--query-profile-source",
            "impala",
            "--impala-profile-host",
            "coordinator.example.com",
            "--metadata-mode",
            "off",
            *extra,
        ]
    )
    return build_batch_config(
        args, env={}, cwd=tmp_path, repo_root=Path(__file__).resolve().parents[1]
    )


def test_unconfigured_source_preserves_legacy_key(tmp_path):
    current = config(tmp_path)
    assert recent_history_source_key(current) == "impala-daemon:1-hosts"
    assert (
        recent_history_source_key(replace(current, impala_profile_hosts=("other.example.com",)))
        == "impala-daemon:1-hosts"
    )


def test_explicit_source_is_stable_across_host_count_and_separates_collectors(tmp_path):
    current = config(tmp_path, "--recent-history-source-id", "source-b")
    key = recent_history_source_key(current)
    assert key == "configured:source-b"
    assert (
        recent_history_source_key(
            replace(current, impala_profile_hosts=("a.example.com", "b.example.com"))
        )
        == key
    )
    assert recent_history_source_key(replace(current, recent_history_source_id="source-c")) != key
    assert recent_history_source_key(config(tmp_path)) != key


@pytest.mark.parametrize(
    "value", ["", " ", "https://example.com", "source/a", "a\nb", "a" * 97, 1, True]
)
def test_config_rejects_invalid_source_ids_without_echo(value):
    with pytest.raises(ConfigError) as error:
        normalize_config_value("recent_history_source_id", value)
    assert str(value) not in str(error.value) or value in ("", " ", 1, True)


def test_explicit_empty_cli_id_does_not_fall_back_to_legacy(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, "--recent-history-source-id", "")


def test_worker_forwards_the_same_source_identity(tmp_path):
    from query_doctor.cli.recent_profile_worker import parse_args

    args = parse_args(["--recent-history-source-id", "source-b"])
    assert args.batch_args.recent_history_source_id == "source-b"


def test_config_file_source_id_and_cli_precedence(tmp_path):
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"recent_history_source_id": "source-config"}))
    assert config(tmp_path, "--config", str(path)).recent_history_source_id == "source-config"
    assert (
        config(
            tmp_path, "--config", str(path), "--recent-history-source-id", "source-cli"
        ).recent_history_source_id
        == "source-cli"
    )


def test_overlapping_query_ids_remain_distinct_in_retained_history(tmp_path):
    from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
    from query_doctor.recent.case_identity import history_case_ref
    from query_doctor.recent.history_store import history_record_from_candidate
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(query_id="same-query", status="FINISHED", statement="select 1"),
        selected=True,
        reason="selected",
        sql_verb="SELECT",
    )
    store = SqliteRecentHistoryStore(tmp_path / "history.db")
    records = [
        history_record_from_candidate(
            candidate,
            engine="impala",
            source_kind="impala",
            source_key=key,
            recorded_at_iso="2026-01-01T00:00:00+00:00",
        )
        for key in ("impala-daemon:1-hosts", "configured:source-b")
    ]
    store.upsert_summaries(records)
    payloads = store.load_payloads()
    assert len(payloads) == 2
    assert len({history_case_ref(row) for row in payloads}) == 2


def test_cluster_profile_can_override_root_source_id(tmp_path):
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "recent_history_source_id": "root",
                "active_cluster_key": "service-b",
                "clusters": [
                    {
                        "id": "service-b",
                        "cluster_type": "impala",
                        "recent_history_source_id": "service-b",
                    }
                ],
            }
        )
    )
    assert (
        config(
            tmp_path, "--config", str(path), "--config-cluster", "service-b"
        ).recent_history_source_id
        == "service-b"
    )
