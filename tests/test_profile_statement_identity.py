import json
from dataclasses import replace

import pytest
from test_recent_history_store import any_history_store  # noqa: F401

from query_doctor.cm.profile_parsing import extract_query_id_from_profile_text
from query_doctor.recent.profile_statement_identity import profile_statement_fingerprint
from query_doctor.recent.statement_identity import statement_fingerprint


SQL = "SELECT private_column\nFROM private_table WHERE day = '2026-01-01'"
PROFILE = f"Query (id=abc:def):\n  Summary:\n    Sql Statement: {SQL}\n    Coordinator: worker.example.invalid\n"


def test_native_header_and_statement_identity():
    assert extract_query_id_from_profile_text(PROFILE) == "abc:def"
    assert profile_statement_fingerprint(PROFILE, query_id="abc:def") == statement_fingerprint(SQL)
    assert profile_statement_fingerprint(
        json.dumps({"details": PROFILE}), query_id="abc:def"
    ) == statement_fingerprint(SQL)


@pytest.mark.parametrize(
    "text",
    [
        PROFILE.replace("abc:def", "abc:fed"),
        PROFILE.replace("Query (id=abc:def):", ""),
        PROFILE.replace("    Coordinator: worker.example.invalid\n", ""),
        PROFILE.replace(SQL, SQL + "..."),
        PROFILE.replace(SQL, ""),
        PROFILE + PROFILE,
        "Query (id=abc:fed):\n" + PROFILE,
        PROFILE.replace(SQL, SQL + "\n    Coordinator: fake"),
    ],
)
def test_partial_or_ambiguous_profile_has_no_full_identity(text):
    assert profile_statement_fingerprint(text, query_id="abc:def") is None


def test_promote_only_owned_job_and_preserve_across_rediscovery(any_history_store):
    from test_recent_profile_worker import profile_history_record, profile_job

    store = any_history_store
    record = replace(
        profile_history_record("abc:def", source_key="configured:service-a"),
        source_kind="impala",
        statement_fingerprint=statement_fingerprint("SELECT x..."),
    )
    other = replace(record, source_key="other-source")
    store.upsert_summaries([record, other])
    store.enqueue_profile_jobs(
        [replace(profile_job("abc:def", source_key=record.source_key), source_kind="impala")]
    )
    store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="old",
        now_iso="2026-07-03T10:07:00+00:00",
        lease_until_iso="2026-07-03T10:08:00+00:00",
    )
    store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="new",
        now_iso="2026-07-03T10:09:00+00:00",
        lease_until_iso="2026-07-03T10:20:00+00:00",
    )
    params = dict(
        engine=record.engine,
        source_kind=record.source_kind,
        source_key=record.source_key,
        query_id=record.query_id,
        completed_at_iso="2026-07-03T10:10:00+00:00",
        statement_fingerprint=statement_fingerprint(SQL),
    )
    assert not store.complete_profile_job(**params, lease_owner="old")
    assert all(
        p["statement_fingerprint"] == record.statement_fingerprint for p in store.load_payloads()
    )
    assert not store.complete_profile_job(
        **{**params, "completed_at_iso": "2026-07-03T10:21:00+00:00"}, lease_owner="new"
    )
    assert store.complete_profile_job(**params, lease_owner="new")
    store.upsert_summaries([record, other])
    rows = store.load_payloads()
    promoted = next(p for p in rows if p["source_key"] == record.source_key)
    assert promoted["statement_fingerprint"] == statement_fingerprint(SQL)
    assert promoted["statement_fingerprint_source"] == "impala_profile_sql_statement_v1"
    assert (
        next(p for p in rows if p["source_key"] == other.source_key)["statement_fingerprint"]
        == other.statement_fingerprint
    )
    assert "private_table" not in json.dumps(rows)
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    expression = (
        "json_extract(payload_json,'$.statement_fingerprint')"
        if isinstance(store, SqliteRecentHistoryStore)
        else "payload_json->>'statement_fingerprint'"
    )
    with store._connect() as connection:
        pairs = connection.execute(
            f"SELECT statement_fingerprint,{expression} FROM recent_query_summary"
        ).fetchall()
    assert all(a == b for a, b in pairs)


def test_collector_to_worker_promotes_before_redaction_and_cleans_up(tmp_path, monkeypatch):
    import io
    import subprocess
    from contextlib import redirect_stdout, redirect_stderr
    from test_recent_profile_worker import cm_config, profile_history_record, PageResponse
    from query_doctor.cli import batch_recent, collect_impala_profile
    from query_doctor.recent.history_store import recent_history_source_key
    from query_doctor.recent.profile_budget import ProfileBudgetPolicy, plan_recent_profile_jobs
    from query_doctor.recent.profile_worker import (
        run_recent_profile_worker,
        RecentProfileWorkerOptions,
    )
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    config = replace(
        cm_config(tmp_path, tmp_path / "history.sqlite"),
        query_profile_source="impala",
        impala_profile_hosts=("coordinator.example.invalid",),
    )
    record = replace(
        profile_history_record("abc:def", source_key=recent_history_source_key(config)),
        source_kind="impala",
        statement_fingerprint=statement_fingerprint("SELECT private_column..."),
    )
    store = SqliteRecentHistoryStore(config.recent_history_db)
    store.upsert_summaries([record])
    store.enqueue_profile_jobs(
        plan_recent_profile_jobs(
            [record],
            policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
            planned_at_iso="2026-07-03T10:06:00+00:00",
        )
    )

    def collect(cmd, *, cwd, env):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = collect_impala_profile.main(
                cmd[cmd.index("--query-id") :],
                opener=lambda _request, timeout: PageResponse(PROFILE),
            )
        return subprocess.CompletedProcess(cmd, rc, out.getvalue(), err.getvalue())

    def analyze(_config, case, *, env, repo_root):
        case.analysis_status = "ok"

    monkeypatch.setattr("query_doctor.recent.case_processing.run_subprocess", collect)
    monkeypatch.setattr("query_doctor.recent.profile_worker_processor.run_analysis_pass", analyze)
    result = run_recent_profile_worker(
        store=store,
        config=config,
        env={},
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1),
    )
    assert result.jobs_completed == 1
    [payload] = store.load_payloads()
    assert payload["statement_fingerprint"] == statement_fingerprint(SQL)
    assert payload["statement_fingerprint_source"] == "impala_profile_sql_statement_v1"
    assert "private_table" not in json.dumps(payload)
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []
