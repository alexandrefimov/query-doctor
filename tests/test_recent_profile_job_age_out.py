import os
import sqlite3
import uuid

import pytest

from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
from query_doctor.recent.history_store import (
    RecentHistoryRetentionPolicy,
    history_record_from_candidate,
)
from query_doctor.recent.operator_readiness import audit_profile_backlog_health
from query_doctor.recent.postgres_history_store import PostgresRecentHistoryStore
from query_doctor.recent.profile_budget import (
    PROFILE_JOB_STATUS_AGED_OUT,
    ProfileBudgetPolicy,
    plan_recent_profile_jobs,
)
from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore
from query_doctor.web.operator_readiness_status import project_operator_readiness_issue_code


NOW = "2026-07-03T12:00:00+00:00"
CUTOFF = "2026-07-03T09:00:00Z"
SCOPE = {"engine": "impala", "source_kind": "cm", "source_key": "cm:cluster:impala"}
LIVE_POSTGRES_DSN_ENV = "QUERY_DOCTOR_TEST_POSTGRES_DSN"


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, tmp_path):
    """A history store and a raw SQL runner for it; SQL uses ? placeholders."""
    if request.param == "sqlite":
        db_path = tmp_path / "recent-history.sqlite"

        def execute_sqlite(sql, params=()):
            with sqlite3.connect(db_path) as connection:
                return connection.execute(sql, params).fetchall()

        yield SqliteRecentHistoryStore(db_path), execute_sqlite
        return
    dsn = os.environ.get(LIVE_POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"{LIVE_POSTGRES_DSN_ENV} is not set; live Postgres store tests are skipped")
    psycopg = pytest.importorskip("psycopg")
    schema = f"qd_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
    scoped_dsn = psycopg.conninfo.make_conninfo(dsn, options=f"-c search_path={schema}")

    def execute_postgres(sql, params=()):
        with psycopg.connect(scoped_dsn) as connection:
            cursor = connection.execute(sql.replace("?", "%s"), params)
            return cursor.fetchall() if cursor.description else []

    try:
        yield PostgresRecentHistoryStore(scoped_dsn), execute_postgres
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {schema} CASCADE")


def record_and_job(query_id: str, end_time: str | None):
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id=query_id,
            duration_ms=3_700_000,
            status="failed",
            query_type="QUERY",
            end_time=end_time,
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate, recorded_at_iso="2026-07-03T08:05:00+00:00", **SCOPE
    )
    [job] = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T08:06:00+00:00",
    )
    return record, job


def seed(store):
    rows = {
        "old-pending": "2026-07-03T08:00:00Z",
        "old-failed": "2026-07-03T08:10:00Z",
        "old-stale-lease": "2026-07-03T08:20:00Z",
        "old-active-lease": "2026-07-03T08:30:00Z",
        "fresh-pending": "2026-07-03T11:50:00Z",
        "no-end-time": None,
    }
    pairs = {query_id: record_and_job(query_id, end) for query_id, end in rows.items()}
    store.upsert_summaries(record for record, _ in pairs.values())
    store.enqueue_profile_jobs(job for _, job in pairs.values())
    return store


def statuses(execute):
    jobs = dict(execute("SELECT query_id, status FROM recent_profile_job"))
    summaries = dict(execute("SELECT query_id, profile_status FROM recent_query_summary"))
    return jobs, summaries


def test_age_out_moves_only_old_unfinished_jobs(backend):
    store, execute = backend
    seed(store)
    execute(
        "UPDATE recent_profile_job SET status = 'failed', last_error_code = 'profile_fetch_timeout' "
        "WHERE query_id = 'old-failed'"
    )
    execute(
        "UPDATE recent_profile_job SET status = 'leased', lease_until_iso = ? "
        "WHERE query_id = 'old-stale-lease'",
        ("2026-07-03T11:00:00+00:00",),
    )
    execute(
        "UPDATE recent_profile_job SET status = 'leased', lease_until_iso = ? "
        "WHERE query_id = 'old-active-lease'",
        ("2026-07-03T12:30:00+00:00",),
    )

    aged = store.age_out_profile_jobs(cutoff_iso=CUTOFF, now_iso=NOW, **SCOPE)

    job_statuses, summary_statuses = statuses(execute)
    assert aged == 2
    assert job_statuses == {
        "old-pending": PROFILE_JOB_STATUS_AGED_OUT,
        "old-failed": "failed",
        "old-stale-lease": PROFILE_JOB_STATUS_AGED_OUT,
        "old-active-lease": "leased",
        "fresh-pending": "pending",
        "no-end-time": "pending",
    }
    assert summary_statuses["old-pending"] == "failed"
    assert summary_statuses["fresh-pending"] == "pending"
    assert summary_statuses["no-end-time"] == "pending"
    assert execute(
        "SELECT last_error_code FROM recent_profile_job WHERE query_id = 'old-failed'"
    ) == [("profile_fetch_timeout",)]
    assert store.age_out_profile_jobs(cutoff_iso=CUTOFF, now_iso=NOW, **SCOPE) == 0


def test_backlog_health_counts_outcomes_inside_the_window(backend):
    store, execute = backend
    seed(store)
    execute(
        "UPDATE recent_profile_job SET status = 'completed', updated_at_iso = ? "
        "WHERE query_id IN ('fresh-pending', 'old-pending')",
        ("2026-07-03T11:55:00+00:00",),
    )
    execute(
        "UPDATE recent_profile_job SET status = 'failed', updated_at_iso = ? "
        "WHERE query_id = 'old-failed'",
        ("2026-07-03T08:00:00+00:00",),
    )

    windowed = store.summarize_profile_backlog_health(
        now_iso=NOW,
        window_start_iso="2026-07-03T09:00:00+00:00",
        window_hours=3,
        **SCOPE,
    ).safe_payload()
    plain = store.summarize_profile_backlog_health(now_iso=NOW, **SCOPE).safe_payload()

    assert windowed["window_hours"] == 3
    assert windowed["window_completed_jobs"] == 2
    assert windowed["window_failed_jobs"] == 0
    assert windowed["failed_jobs"] == 1
    assert "window_hours" not in plain


def test_retention_prunes_aged_out_jobs(backend):
    store, execute = backend
    seed(store)
    store.age_out_profile_jobs(cutoff_iso=CUTOFF, now_iso=NOW, **SCOPE)

    result = store.prune_history(
        policy=RecentHistoryRetentionPolicy(profile_job_cutoff_iso="2026-07-04T00:00:00+00:00")
    )

    job_statuses, _ = statuses(execute)
    assert result.profile_jobs_deleted == 4
    assert PROFILE_JOB_STATUS_AGED_OUT not in job_statuses.values()


def test_not_found_job_ages_out_and_counts_apart_in_the_window(backend):
    store, execute = backend
    seed(store)
    # Four jobs age out by query end time; they must not count as not found.
    assert store.age_out_profile_jobs(cutoff_iso=CUTOFF, now_iso=NOW, **SCOPE) == 4
    claimed = store.claim_profile_jobs(
        max_jobs=10,
        lease_owner="worker-a",
        lease_until_iso="2026-07-03T12:30:00+00:00",
        now_iso=NOW,
        **SCOPE,
    )
    assert sorted(job.query_id for job in claimed) == ["fresh-pending", "no-end-time"]

    assert store.fail_profile_job(
        **SCOPE,
        query_id="fresh-pending",
        lease_owner="worker-a",
        failed_at_iso="2026-07-03T12:01:00+00:00",
        error_code="profile_not_found",
        retry=False,
        aged_out=True,
    )

    job_statuses, summary_statuses = statuses(execute)
    assert job_statuses["fresh-pending"] == PROFILE_JOB_STATUS_AGED_OUT
    assert summary_statuses["fresh-pending"] == "failed"
    assert execute(
        "SELECT last_error_code FROM recent_profile_job WHERE query_id = 'fresh-pending'"
    ) == [("profile_not_found",)]
    health = store.summarize_profile_backlog_health(
        now_iso=NOW,
        window_start_iso="2026-07-03T09:00:00+00:00",
        window_hours=3,
        **SCOPE,
    ).safe_payload()
    assert (health["window_failed_jobs"], health["window_not_found_jobs"]) == (0, 1)
    assert health["failed_jobs"] == 0


def worker_summary(**window):
    backlog = {
        "pending_jobs": 0,
        "retry_pending_jobs": 0,
        "leased_jobs": 0,
        "stale_leased_jobs": 0,
        "failed_jobs": 4,
        **window,
    }
    return {"profile_backlog_health": backlog}


def readiness(summary, **kwargs):
    checks, issues = [], []
    audit_profile_backlog_health(checks, issues, summary, **kwargs)
    [failed_check] = [check for check in checks if check["id"] == "profile_backlog_failed_jobs"]
    return failed_check["status"], issues


def test_readiness_accepts_a_small_share_of_failures_in_the_window():
    summary = worker_summary(window_hours=3, window_completed_jobs=396, window_failed_jobs=4)

    assert readiness(summary) == ("ready", [])


def test_readiness_blocks_when_failures_exceed_the_accepted_share():
    summary = worker_summary(window_hours=3, window_completed_jobs=90, window_failed_jobs=10)

    status, issues = readiness(summary)

    assert status == "blocked"
    assert issues == ["profile_worker_backlog_failed_jobs"]
    assert readiness(summary, max_failed_share=0.2) == ("ready", [])


def test_readiness_message_does_not_round_a_share_down_to_the_limit():
    # 52 of 1000 is 5.2%; whole percents printed "failed at 5% ... above 5%".
    summary = worker_summary(window_hours=3, window_completed_jobs=948, window_failed_jobs=52)
    checks, issues = [], []

    audit_profile_backlog_health(checks, issues, summary)

    [failed_check] = [check for check in checks if check["id"] == "profile_backlog_failed_jobs"]
    assert failed_check["status"] == "blocked"
    assert "failed at 5.2% in the last 3 h, above the accepted 5.0%" in failed_check["summary"]


def test_every_readiness_issue_code_is_shown_by_its_name():
    import inspect
    import re

    from query_doctor.recent import operator_readiness

    emitted = set(
        re.findall(r'issues\.append\("([a-z0-9_]+)"\)', inspect.getsource(operator_readiness))
    )
    assert "profile_worker_backlog_stale_leases" in emitted
    for code in sorted(emitted):
        assert project_operator_readiness_issue_code(code) == code


def test_readiness_passes_an_empty_window():
    summary = worker_summary(window_hours=3, window_completed_jobs=0, window_failed_jobs=0)

    assert readiness(summary) == ("ready", [])


def test_readiness_blocks_when_nothing_completed_and_profiles_were_not_found():
    summary = worker_summary(
        window_hours=3, window_completed_jobs=0, window_failed_jobs=0, window_not_found_jobs=7
    )
    checks, issues = [], []

    audit_profile_backlog_health(checks, issues, summary)

    statuses_by_id = {check["id"]: check["status"] for check in checks}
    assert statuses_by_id["profile_backlog_not_found_only"] == "blocked"
    assert issues == ["profile_worker_backlog_profiles_not_found"]
    assert project_operator_readiness_issue_code(issues[0]) == issues[0]


def test_readiness_accepts_not_found_profiles_next_to_completed_jobs():
    summary = worker_summary(
        window_hours=3, window_completed_jobs=50, window_failed_jobs=0, window_not_found_jobs=30
    )

    assert readiness(summary) == ("ready", [])


def test_readiness_keeps_the_old_rule_for_summaries_without_a_window():
    status, issues = readiness(worker_summary())

    assert status == "blocked"
    assert issues == ["profile_worker_backlog_failed_jobs"]


def worker_config(tmp_path, *extra):
    from query_doctor.cli import batch_recent

    args = batch_recent.parse_args(
        [
            "--out",
            str(tmp_path / "query-doctor-worker-out"),
            "--cm-url",
            "https://cm.example.invalid:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(tmp_path / "recent-history.sqlite"),
            *extra,
        ]
    )
    return batch_recent.build_batch_config(
        args,
        env={"CM_PASSWORD": "secret", "CM_USERNAME": "user"},
        cwd=tmp_path,
        repo_root=batch_recent.REPO_DIR,
    )


def run_worker(tmp_path, *extra):
    from datetime import datetime, timezone

    from query_doctor.recent.profile_worker import (
        RecentProfileWorkerJobOutcome,
        RecentProfileWorkerOptions,
        run_recent_profile_worker,
    )

    store = seed(SqliteRecentHistoryStore(tmp_path / "recent-history.sqlite"))
    processed = []

    def processor(job, *_args):
        processed.append(job.query_id)
        return RecentProfileWorkerJobOutcome(status="completed")

    result = run_recent_profile_worker(
        store=store,
        config=worker_config(tmp_path, *extra),
        env={},
        repo_root=tmp_path,
        options=RecentProfileWorkerOptions(max_jobs=10),
        now=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        processor=processor,
    )
    return result, processed


def test_worker_ages_out_old_jobs_before_claiming(tmp_path):
    result, processed = run_worker(tmp_path, "--profile-job-max-age-hours", "3")

    payload = result.safe_payload()
    assert result.jobs_aged_out == 4
    assert sorted(processed) == ["fresh-pending", "no-end-time"]
    assert result.status == "done"
    assert payload["jobs_aged_out"] == 4
    assert payload["profile_backlog_health"]["window_hours"] == 3


def test_worker_max_age_zero_disables_aging_out(tmp_path):
    result, processed = run_worker(tmp_path, "--profile-job-max-age-hours", "0")

    assert result.jobs_aged_out == 0
    assert len(processed) == 6
    assert "window_hours" not in result.safe_payload()["profile_backlog_health"]


def test_config_contract_accepts_the_max_age_key():
    from query_doctor.config.contract import ConfigError, normalize_config_value

    assert normalize_config_value("recent_profile_job_max_age_hours", 0) == 0
    assert normalize_config_value("recent_profile_job_max_age_hours", 3) == 3
    with pytest.raises(ConfigError):
        normalize_config_value("recent_profile_job_max_age_hours", -1)
