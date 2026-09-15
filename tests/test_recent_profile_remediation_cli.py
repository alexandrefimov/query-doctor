import json

import pytest

from query_doctor.cli import recent_profile_remediation as cli
from query_doctor.recent.history_store import RecentSummaryHistoryRecord
from query_doctor.recent.profile_budget import (
    PROFILE_JOB_STATUS_FAILED,
    PROFILE_JOB_STATUS_PENDING,
    PROFILE_STATUS_PENDING,
    ProfileBudgetPolicy,
    RecentProfileJobRequeueResult,
    plan_recent_profile_jobs,
)
from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore


SECRET_DSN = "postgresql://query_doctor:secret@private-host.example.net/query_doctor"


@pytest.mark.parametrize("mode", ["--dry-run", "--apply"])
def test_postgres_remediation_cli_uses_existing_schema(mode, monkeypatch, capsys):
    from query_doctor.recent.postgres_history_store import PostgresRecentHistoryStore

    calls = []

    class ExistingSchemaStore:
        def requeue_failed_profile_jobs(self, **kwargs):
            calls.append(kwargs)
            assert kwargs.get("prepare_schema") is False
            return RecentProfileJobRequeueResult(
                matched_failed_jobs=2,
                selected_failed_jobs=1,
                requeued_jobs=0 if kwargs["dry_run"] else 1,
                dry_run=kwargs["dry_run"],
            )

    monkeypatch.setattr(
        PostgresRecentHistoryStore,
        "from_env",
        lambda *args, **kwargs: ExistingSchemaStore(),
    )
    status = cli.main(
        [
            "--backend",
            "postgres",
            mode,
            "--max-jobs",
            "1",
            "--json",
            "--engine",
            "impala",
            "--source-kind",
            "impala",
            "--source-key",
            "impala-daemon:1-hosts",
        ],
        env={"QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN": SECRET_DSN},
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert status == 0
    assert len(calls) == 1
    assert calls[0]["engine"] == "impala"
    assert calls[0]["source_kind"] == "impala"
    assert calls[0]["source_key"] == "impala-daemon:1-hosts"
    assert calls[0]["max_jobs"] == 1
    assert calls[0]["dry_run"] is (mode == "--dry-run")
    assert payload["status"] == ("dry_run" if mode == "--dry-run" else "applied")
    assert payload["raw_output"] is False
    assert SECRET_DSN not in output.out + output.err
    assert "impala-daemon:1-hosts" not in output.out + output.err


@pytest.mark.parametrize("mode", ["--dry-run", "--apply"])
def test_postgres_remediation_cli_schema_failure_is_blocked_without_ddl(
    mode,
    monkeypatch,
    capsys,
):
    from query_doctor.recent.postgres_history_store import PostgresRecentHistoryStore

    calls = []

    class MissingSchemaCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=None):
            calls.append(statement)
            raise RuntimeError(SECRET_DSN)

    class MissingSchemaConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return MissingSchemaCursor()

    store = PostgresRecentHistoryStore(
        SECRET_DSN,
        connect=lambda dsn: MissingSchemaConnection(),
    )
    monkeypatch.setattr(PostgresRecentHistoryStore, "from_env", lambda *a, **kw: store)
    monkeypatch.setattr(
        store,
        "initialize",
        lambda: pytest.fail("remediation must not initialize schema"),
    )
    status = cli.main(
        ["--backend", "postgres", mode, "--json", "--fail-on-warning"],
        env={"QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN": SECRET_DSN},
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert status == 1
    assert payload["status"] == "blocked"
    assert payload["issue_codes"] == ["recent_profile_remediation_failed"]
    assert payload["remediation"]["requeued_jobs"] == 0
    assert len(calls) == 1
    assert calls[0].lstrip().startswith("SELECT")
    assert SECRET_DSN not in output.out + output.err


def summary_record(query_id: str) -> RecentSummaryHistoryRecord:
    return RecentSummaryHistoryRecord(
        schema_version=1,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id=query_id,
        recorded_at_iso="2026-07-03T10:05:00+00:00",
        start_time=None,
        end_time=None,
        duration_ms=3_700_000,
        status="failed",
        query_state="EXCEPTION",
        user=None,
        pool=None,
        query_type="QUERY",
        sql_verb="select",
        statement_present=True,
        admission_result=None,
        admission_wait_ms=None,
        rows_produced=None,
        bytes_read=None,
        bytes_sent=None,
        memory_aggregate_peak=None,
        memory_per_node_peak=None,
        suspicion_score=200,
        suspicion_level="critical",
        suspicion_reasons=("failed_or_error_status",),
        selected=True,
        selected_reason="selected_recent_candidate",
    )


def seed_failed_job(store: SqliteRecentHistoryStore, query_id: str) -> None:
    summary = summary_record(query_id)
    [job] = plan_recent_profile_jobs(
        [summary],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )
    store.upsert_summaries([summary])
    store.enqueue_profile_jobs([job])
    store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert store.fail_profile_job(
        engine=job.engine,
        source_kind=job.source_kind,
        source_key=job.source_key,
        query_id=job.query_id,
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:12:00+00:00",
        error_code="profile-fetch-permanent",
        retry=False,
    )


def test_recent_profile_remediation_cli_dry_run_does_not_mutate_or_echo_raw(
    tmp_path,
    capsys,
):
    db = tmp_path / "recent.sqlite"
    store = SqliteRecentHistoryStore(db)
    seed_failed_job(store, "query-failed")

    status = cli.main(
        [
            "--backend",
            "sqlite",
            "--sqlite-db",
            str(db),
            "--max-jobs",
            "1",
            "--json",
        ],
        env={},
    )

    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload, sort_keys=True)
    rows = store.load_profile_jobs()
    assert status == 0
    assert payload["summary_kind"] == cli.SUMMARY_KIND
    assert payload["status"] == "dry_run"
    assert payload["mode"] == "dry_run"
    assert payload["remediation"]["matched_failed_jobs"] == 1
    assert payload["remediation"]["selected_failed_jobs"] == 1
    assert payload["remediation"]["requeued_jobs"] == 0
    assert payload["raw_output"] is False
    assert rows[0]["status"] == PROFILE_JOB_STATUS_FAILED
    assert str(db) not in serialized
    assert "query-failed" not in serialized
    assert "secret" not in serialized


def test_recent_profile_remediation_cli_apply_requeues_failed_jobs_raw_free(
    tmp_path,
    capsys,
):
    db = tmp_path / "recent.sqlite"
    store = SqliteRecentHistoryStore(db)
    seed_failed_job(store, "query-failed")

    status = cli.main(
        [
            "--backend",
            "sqlite",
            "--sqlite-db",
            str(db),
            "--max-jobs",
            "1",
            "--apply",
            "--json",
        ],
        env={},
    )

    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload, sort_keys=True)
    rows = store.load_profile_jobs()
    retained_payloads = store.load_payloads()
    assert status == 0
    assert payload["status"] == "applied"
    assert payload["mode"] == "apply"
    assert payload["remediation"]["requeued_jobs"] == 1
    assert payload["next_step"] == "Run the Recent profile worker to process the requeued jobs."
    assert rows[0]["status"] == PROFILE_JOB_STATUS_PENDING
    assert rows[0]["attempts"] == 0
    assert rows[0]["last_error_code"] is None
    assert retained_payloads[0]["profile_status"] == PROFILE_STATUS_PENDING
    assert str(db) not in serialized
    assert "query-failed" not in serialized
    assert "secret" not in serialized


def test_recent_profile_remediation_cli_blocks_missing_postgres_dsn_without_echo(capsys):
    status = cli.main(
        [
            "--backend",
            "postgres",
            "--postgres-dsn-env",
            "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN",
            "--max-jobs",
            "10",
            "--apply",
            "--json",
            "--fail-on-warning",
        ],
        env={"QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN": ""},
    )

    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload, sort_keys=True)
    assert status == 1
    assert payload["status"] == "blocked"
    assert payload["issue_codes"] == ["postgres_dsn_env_missing"]
    assert "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN" not in serialized
    assert "secret" not in serialized
    assert "private-host" not in serialized
    assert SECRET_DSN not in serialized
