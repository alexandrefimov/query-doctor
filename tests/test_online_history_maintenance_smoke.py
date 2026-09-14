import json
from pathlib import Path

from query_doctor.cli import batch_recent
from query_doctor.cli import recent_history_operator_readiness
from query_doctor.cli import recent_profile_remediation
from query_doctor.cli import recent_profile_worker
from query_doctor.recent import profile_worker_processor
from query_doctor.recent.postgres_readiness import SUMMARY_KIND as POSTGRES_READINESS_KIND
from query_doctor.recent.profile_budget import (
    PROFILE_JOB_STATUS_COMPLETED,
    PROFILE_JOB_STATUS_FAILED,
)
from query_doctor.recent.profile_worker import RecentProfileWorkerJobOutcome
from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore
from web_server_test_support import REPO_DIR, load_web_module


SUCCESS_QUERY_ID = "maintenance-success-query"
FAILED_QUERY_ID = "maintenance-failed-query"


def test_online_history_maintenance_loop_smoke_is_raw_free(tmp_path, monkeypatch, capsys):
    history_db = tmp_path / "recent-history.sqlite"
    worker_summary = tmp_path / "profile-worker-summary.json"
    remediation_summary = tmp_path / "profile-remediation-summary.json"
    postgres_summary = tmp_path / "postgres-readiness-summary.json"
    operator_summary = tmp_path / "operator-readiness.json"

    monkeypatch.setattr(
        batch_recent,
        "discover_candidates",
        lambda config, env: batch_recent.DiscoveryResult(
            [
                _candidate(SUCCESS_QUERY_ID, end_time="2026-07-03T10:20:00Z"),
                _candidate(FAILED_QUERY_ID, end_time="2026-07-03T10:19:00Z"),
            ],
            [],
            "client-side",
            None,
        ),
    )
    monkeypatch.setattr(batch_recent, "run_subprocess", _unexpected_subprocess)
    monkeypatch.setattr(
        profile_worker_processor,
        "process_recent_profile_job",
        _process_profile_job,
    )

    discover_status = batch_recent.main(
        [
            *_batch_args(tmp_path),
            "--discover-only",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ],
        env=_auth_env(),
    )
    discover_output = capsys.readouterr()
    assert discover_status == 0
    assert "SELECT secret_column" not in discover_output.out
    assert "private_table" not in discover_output.out

    store = SqliteRecentHistoryStore(history_db)
    assert {row["query_id"]: row["status"] for row in store.load_profile_jobs()} == {
        FAILED_QUERY_ID: "pending",
        SUCCESS_QUERY_ID: "pending",
    }

    worker_status = recent_profile_worker.main(
        [
            "--profile-worker-max-jobs",
            "2",
            "--profile-worker-lease-owner",
            "maintenance-smoke-worker",
            "--summary-json",
            str(worker_summary),
            "--json",
            *_batch_args(tmp_path),
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ],
        env=_auth_env(),
    )
    worker_stdout = capsys.readouterr().out
    worker_payload = _read_json(worker_summary)
    assert worker_status == 0
    assert json.loads(worker_stdout) == worker_payload
    assert worker_payload["status"] == "done"
    assert worker_payload["jobs_claimed"] == 2
    assert worker_payload["jobs_completed"] == 1
    assert worker_payload["jobs_failed"] == 1
    assert worker_payload["profile_backlog_health"] == {
        "pending_jobs": 0,
        "retry_pending_jobs": 0,
        "leased_jobs": 0,
        "stale_leased_jobs": 0,
        "failed_jobs": 1,
    }
    assert {row["query_id"]: row["status"] for row in store.load_profile_jobs()} == {
        FAILED_QUERY_ID: PROFILE_JOB_STATUS_FAILED,
        SUCCESS_QUERY_ID: PROFILE_JOB_STATUS_COMPLETED,
    }

    remediation_status = recent_profile_remediation.main(
        [
            "--backend",
            "sqlite",
            "--sqlite-db",
            str(history_db),
            "--engine",
            "impala",
            "--source-kind",
            "cm",
            "--source-key",
            "cm:cluster:impala",
            "--max-jobs",
            "5",
            "--summary-json",
            str(remediation_summary),
            "--json",
        ],
        env={},
    )
    remediation_stdout = capsys.readouterr().out
    remediation_payload = _read_json(remediation_summary)
    assert remediation_status == 0
    assert json.loads(remediation_stdout) == remediation_payload
    assert remediation_payload["status"] == "dry_run"
    assert remediation_payload["remediation"] == {
        "matched_failed_jobs": 1,
        "selected_failed_jobs": 1,
        "requeued_jobs": 0,
        "skipped_due_to_limit": 0,
        "dry_run": True,
    }
    assert {row["query_id"]: row["status"] for row in store.load_profile_jobs()}[
        FAILED_QUERY_ID
    ] == PROFILE_JOB_STATUS_FAILED

    postgres_summary.write_text(
        json.dumps(
            {
                "summary_kind": POSTGRES_READINESS_KIND,
                "status": "ready",
                "backend": "postgres",
                "schema_initialized": True,
                "checks": [{"id": "schema", "status": "ready", "summary": "Schema ready"}],
                "issue_codes": [],
                "raw_output": False,
                "sensitive_value_echo": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    operator_status = recent_history_operator_readiness.main(
        [
            "--postgres-readiness-summary-json",
            str(postgres_summary),
            "--profile-worker-summary-json",
            str(worker_summary),
            "--profile-remediation-summary-json",
            str(remediation_summary),
            "--summary-json",
            str(operator_summary),
            "--json",
            "--fail-on-warning",
        ]
    )
    operator_stdout = capsys.readouterr().out
    operator_payload = _read_json(operator_summary)
    assert operator_status == 1
    assert json.loads(operator_stdout) == operator_payload
    assert operator_payload["status"] == "blocked"
    assert operator_payload["issue_codes"] == ["profile_worker_backlog_failed_jobs"]
    assert operator_payload["accepted_summary_count"] == 3
    assert (
        operator_payload["operations"]["profile_worker"]["profile_backlog_health"]["failed_jobs"]
        == 1
    )
    assert operator_payload["operations"]["profile_remediation"]["selected_failed_jobs"] == 1

    web_config = tmp_path / "web-config.json"
    web_config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_operator_readiness_summary_json": "operator-readiness.json",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    web = load_web_module()
    settings = web.WebSettings(config=web_config, repo_dir=REPO_DIR, no_llm=True)
    body = web.render_batch_page(settings)
    all_recent_body = web.render_batch_page(settings, history_view="all_recent")

    assert "Details ready" in body
    assert SUCCESS_QUERY_ID in body
    assert FAILED_QUERY_ID not in body
    assert 'data-href="/batch/case/' in body
    assert "All recent queries" in all_recent_body
    assert SUCCESS_QUERY_ID in all_recent_body
    assert FAILED_QUERY_ID in all_recent_body
    assert (
        '<span class="query-inbox-metric"><strong>profile loop</strong>'
        "<span>1 analyzed / 1 failed</span></span>" in all_recent_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>collector freshness</strong>' in all_recent_body
    )
    assert '<span class="query-inbox-metric"><strong>last planning</strong>' in all_recent_body
    assert (
        '<span class="query-inbox-metric"><strong>profile worker</strong>'
        "<span>2 claimed / 1 completed / 1 failed</span></span>" in all_recent_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile backlog</strong>'
        "<span>0 pending / 0 retry / 0 leased / 0 stale / 1 failed</span></span>" in all_recent_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>backlog next step</strong>'
        "<span>Run profile remediation dry-run before requeueing terminal failed profile jobs."
        "</span></span>" in all_recent_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile remediation</strong>'
        "<span>dry_run / 1 matched / 1 selected / 0 requeued</span></span>" in all_recent_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>remediation next step</strong>'
        "<span>Review the bounded count, then rerun remediation with --apply.</span></span>"
        in all_recent_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>details ready</strong>'
        "<span>1/1 analyzed</span></span>" in all_recent_body
    )

    _assert_raw_free_summary(worker_payload)
    _assert_raw_free_summary(remediation_payload)
    _assert_raw_free_summary(operator_payload)
    for unsafe in _unsafe_markers():
        assert unsafe not in body
        assert unsafe not in all_recent_body


def _batch_args(tmp_path: Path) -> list[str]:
    return [
        "--out",
        str(tmp_path / "query-doctor-batch"),
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


def _auth_env() -> dict[str, str]:
    return {"CM_PASSWORD": "secret", "CM_USERNAME": "user"}


def _candidate(query_id: str, *, end_time: str) -> batch_recent.cm_profiles.RecentQueryCandidate:
    return batch_recent.cm_profiles.RecentQueryCandidate(
        summary=batch_recent.cm_profiles.CMQuerySummary(
            query_id=query_id,
            start_time="2026-07-03T10:00:00Z",
            end_time=end_time,
            duration_ms=600_000,
            status="FAILED",
            query_state="EXCEPTION",
            user="analyst",
            pool="root.analytics",
            query_type="QUERY",
            statement="SELECT secret_column FROM private_table",
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )


def _process_profile_job(job, _config, _env, _repo_root):
    if job.query_id == SUCCESS_QUERY_ID:
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_maintenance_success",
            analysis_payload={
                "score": 91,
                "score_severity": "high",
                "score_reasons": ["runtime signal"],
                "analysis_status": "ok",
                "collection_status": "ok",
                "metadata_status": "not_collected",
                "referenced_table_count": 2,
                "collectable_metadata_table_count": 1,
                "collected_metadata_table_count": 0,
                "raw_sql": "SELECT secret_column FROM private_table",
                "case_dir": "/private/tmp/query-doctor-secret",
                "profile_fingerprint": "profile_fingerprint_v1",
            },
            artifact_storage_key="sha256_maintenance_success",
            artifact_size_bytes=4096,
        )
    return RecentProfileWorkerJobOutcome(
        status="failed",
        error_code="profile-fetch-permanent",
        retry=False,
    )


def _unexpected_subprocess(*_args, **_kwargs):
    raise AssertionError("maintenance smoke must not run subprocesses")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_raw_free_summary(payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, sort_keys=True)
    assert SUCCESS_QUERY_ID not in rendered
    assert FAILED_QUERY_ID not in rendered
    for unsafe in _unsafe_markers():
        assert unsafe not in rendered


def _unsafe_markers() -> tuple[str, ...]:
    return (
        "SELECT secret_column",
        "private_table",
        "/private/tmp",
        "query-doctor-secret",
        "sha256_maintenance_success",
        "profile_fingerprint_v1",
        "cm.example.net",
        "operator-readiness.json",
    )
