import json
import os
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
from query_doctor.cm.query_discovery import classify_recent_query_candidate
from query_doctor.recent.history_store import (
    RecentHistoryStoreError,
    RecentHistoryRetentionPolicy,
    history_record_from_candidate,
    persist_recent_history_with_store,
)
from query_doctor.recent.profile_budget import (
    ANALYSIS_CACHE_SCHEMA_VERSION,
    DEFAULT_PROFILE_BUDGET_MIN_SUSPICION_SCORE,
    PROFILE_ARTIFACT_SCHEMA_VERSION,
    PROFILE_JOB_STATUS_COMPLETED,
    PROFILE_JOB_STATUS_FAILED,
    PROFILE_JOB_STATUS_LEASED,
    PROFILE_JOB_STATUS_PENDING,
    PROFILE_STATUS_ANALYZED,
    PROFILE_STATUS_FAILED,
    PROFILE_STATUS_PENDING,
    PROFILE_STATUS_PROCESSING,
    PROFILE_STATUS_RETRY_PENDING,
    ProfileBudgetPolicy,
    RecentAnalysisCacheRecord,
    RecentProfileArtifactRecord,
    plan_recent_profile_jobs,
)
from query_doctor.recent.summary_suspicion import score_recent_summary_suspicion
from query_doctor.recent.postgres_history_store import PostgresRecentHistoryStore
from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore


def profile_budget_job(query_id: str):
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id=query_id,
            duration_ms=3_700_000,
            status="failed",
            query_type="QUERY",
            statement="SELECT secret_column FROM sensitive_table",
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    return plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )[0]


def profile_job_key(job):
    return {
        "engine": job.engine,
        "source_kind": job.source_kind,
        "source_key": job.source_key,
        "query_id": job.query_id,
    }


def analysis_cache_record(
    payload: dict[str, object],
    *,
    query_id: str = "query-cache",
    recorded_at_iso: str = "2026-07-03T10:30:00+00:00",
    status: str = "ready",
):
    return RecentAnalysisCacheRecord(
        schema_version=ANALYSIS_CACHE_SCHEMA_VERSION,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id=query_id,
        profile_fingerprint="profile_fingerprint_v1",
        analyzer_contract="profile_digest_analysis_json_v1",
        recorded_at_iso=recorded_at_iso,
        status=status,
        payload=payload,
    )


def profile_artifact_record(
    *,
    query_id: str = "query-artifact",
    profile_fingerprint: str = "profile_fingerprint_v1",
    artifact_contract: str = "profile_artifact_v1",
    recorded_at_iso: str = "2026-07-03T10:30:00+00:00",
    storage_key: str = "sha256_deadbeef",
    status: str = "available",
):
    return RecentProfileArtifactRecord(
        schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id=query_id,
        profile_fingerprint=profile_fingerprint,
        artifact_contract=artifact_contract,
        recorded_at_iso=recorded_at_iso,
        status=status,
        storage_kind="local",
        storage_key=storage_key,
        size_bytes=4096,
    )


def summary_history_record(query_id: str, *, recorded_at_iso: str):
    return history_record_from_candidate(
        RecentQueryCandidate(
            summary=CMQuerySummary(
                query_id=query_id,
                duration_ms=180_000,
                status="finished",
                query_type="QUERY",
                statement="SELECT secret_column FROM sensitive_table",
            ),
            selected=True,
            reason="selected: SELECT-like user query",
            sql_verb="SELECT",
        ),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso=recorded_at_iso,
    )


def test_recent_history_keeps_query_id_that_reads_as_host_port():
    query_id = "db4d1e2f3a4b5c6d:7a8b9c0d00000000"

    record = summary_history_record(query_id, recorded_at_iso="2026-07-03T10:05:00+00:00")
    job = profile_budget_job(query_id)

    assert record.query_id == query_id
    assert job.query_id == query_id


def test_impala_exception_counts_as_a_failure_everywhere_failed_does():
    def summary(status: str) -> CMQuerySummary:
        return CMQuerySummary(
            query_id="q",
            duration_ms=938_000,
            status=status,
            query_type="QUERY",
            statement="SELECT one FROM two",
        )

    scores = {
        s: score_recent_summary_suspicion(summary(s)) for s in ("exception", "failed", "error")
    }
    verdicts = {
        s: classify_recent_query_candidate(summary(s), min_duration_sec=8)[:2]
        for s in ("exception", "failed", "error")
    }

    assert "failed_or_error_status" in scores["exception"].reasons
    assert scores["exception"].score == scores["failed"].score == scores["error"].score
    assert verdicts["exception"] == verdicts["failed"] == verdicts["error"]
    assert verdicts["exception"] == (False, "excluded: failed query")
    # The score alone clears the profile-budget floor, so the query is still
    # collected; it is the selected path it drops out of, exactly like failed.
    assert scores["exception"].score >= DEFAULT_PROFILE_BUDGET_MIN_SUSPICION_SCORE


def test_summary_suspicion_scores_failed_long_expensive_summary():
    score = score_recent_summary_suspicion(
        CMQuerySummary(
            query_id="query-1",
            duration_ms=3_700_000,
            status="failed",
            admission_wait_ms=300_000,
            bytes_read=1024**4,
            memory_aggregate_peak=100 * 1024**3,
        )
    )

    assert score.level == "critical"
    assert score.score >= 200
    assert score.reasons == (
        "failed_or_error_status",
        "duration_ge_1h",
        "admission_wait_ge_5m",
        "memory_peak_ge_100gib",
        "bytes_read_ge_1tib",
    )


def test_summary_suspicion_scores_large_read_with_small_result():
    def score(**fields):
        values = {
            "query_id": "q",
            "duration_ms": 1_800,
            "status": "finished",
            "query_type": "QUERY",
            "bytes_read": 12 * 1024**3,
            "rows_produced": 1,
        }
        values.update(fields)
        return score_recent_summary_suspicion(CMQuerySummary(**values))

    short_heavy = score()
    assert short_heavy.reasons == ("large_read_small_result",)
    assert short_heavy.level == "low"
    # A short query used to score zero here; it now reaches the profile budget.
    assert short_heavy.score >= DEFAULT_PROFILE_BUDGET_MIN_SUSPICION_SCORE

    assert score(bytes_read=200 * 1024**3, rows_produced=100_000).reasons == (
        "bytes_read_ge_100gib",
        "large_read_small_result",
    )
    assert score(query_type="select").reasons == ("large_read_small_result",)

    for fields in (
        {"query_type": "DML"},
        {"query_type": "DDL"},
        {"query_type": None},
        {"rows_produced": 100_001},
        {"rows_produced": None},
        {"bytes_read": 10 * 1024**3 - 1},
    ):
        assert score(**fields).reasons == (), fields


def test_recent_history_store_upserts_raw_free_summary_payload(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="query-1",
            start_time="2026-07-03T10:00:00Z",
            end_time="2026-07-03T10:03:00Z",
            duration_ms=180_000,
            status="finished",
            user="analyst@example.com",
            pool="root.analytics",
            query_type="QUERY",
            statement="SELECT secret_column FROM sensitive_table",
            admission_wait_ms=61_000,
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )

    store = SqliteRecentHistoryStore(db_path)
    assert store.upsert_summaries([record]) == 1
    assert store.upsert_summaries([record]) == 1

    payloads = store.load_payloads()
    assert store.count_summaries() == 1
    assert payloads[0]["query_id"] == "query-1"
    assert payloads[0]["source_key"] == "cm:cluster:impala"
    assert payloads[0]["statement_present"] is True
    assert payloads[0]["sql_verb"] == "select"
    assert payloads[0]["suspicion_level"] == "low"
    payload_text = json.dumps(payloads[0], sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def failed_summary_record(query_id: str, *, status: str, statement: str | None):
    return history_record_from_candidate(
        RecentQueryCandidate(
            summary=CMQuerySummary(
                query_id=query_id,
                end_time="2026-07-03T10:03:00Z",
                status=status,
                query_type="QUERY",
                statement=statement,
            ),
            selected=True,
            reason="selected: SELECT-like user query",
            sql_verb="SELECT",
        ),
        engine="impala",
        source_kind="impala",
        source_key="impala-daemon:1-hosts",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )


def test_recent_history_record_classifies_failure_and_fingerprints_statement(tmp_path):
    record = failed_summary_record(
        "query-parse",
        status="ParseException: Syntax error in line 1:\nselect * frm secret_table",
        statement="select * frm secret_table where day = '2026-09-01'",
    )
    rerun = failed_summary_record(
        "query-parse-rerun",
        status="ParseException: Syntax error in line 1:\nselect * frm secret_table",
        statement="SELECT *  FRM secret_table WHERE day = '2026-09-02'",
    )

    assert record.schema_version == 2
    assert record.error_class == "ParseException"
    assert record.statement_fingerprint is not None
    assert record.statement_fingerprint.startswith("sf_")
    assert record.statement_fingerprint == rerun.statement_fingerprint

    store = SqliteRecentHistoryStore(tmp_path / "recent.sqlite")
    store.upsert_summaries([record])
    [payload] = store.load_payloads()
    assert payload["error_class"] == "ParseException"
    assert payload["statement_fingerprint"] == record.statement_fingerprint
    payload_text = json.dumps(payload, sort_keys=True)
    for raw in ("secret_table", "Syntax error", "frm", "2026-09-01"):
        assert raw not in payload_text


def test_recent_history_record_leaves_new_fields_null_without_status_or_statement():
    record = failed_summary_record("query-bare", status="exception", statement=None)

    assert record.error_class is None
    assert record.statement_fingerprint is None


def test_sqlite_store_adds_new_columns_to_an_existing_table_and_keeps_old_rows_null(tmp_path):
    import sqlite3

    from query_doctor.recent.sqlite_history_store import SQLITE_RECENT_QUERY_SUMMARY_DDL

    db_path = tmp_path / "recent.sqlite"
    old_ddl = SQLITE_RECENT_QUERY_SUMMARY_DDL.replace(
        "    error_class TEXT,\n    statement_fingerprint TEXT,\n", ""
    )
    assert old_ddl != SQLITE_RECENT_QUERY_SUMMARY_DDL
    with sqlite3.connect(db_path) as connection:
        connection.executescript(old_ddl)
        connection.execute(
            "INSERT INTO recent_query_summary (schema_version, engine, source_kind, source_key,"
            " query_id, recorded_at_iso, statement_present, suspicion_score, suspicion_level,"
            " suspicion_reasons_json, selected, profile_status, payload_json)"
            " VALUES (1, 'impala', 'impala', 'impala-daemon:1-hosts', 'query-old',"
            " '2026-07-03T10:05:00+00:00', 0, 0, 'none', '[]', 0, 'not_collected', '{}')"
        )

    store = SqliteRecentHistoryStore(db_path)
    store.initialize()
    store.initialize()

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recent_query_summary)")}
        old_row = connection.execute(
            "SELECT schema_version, error_class, statement_fingerprint FROM recent_query_summary"
        ).fetchone()
    assert {"error_class", "statement_fingerprint"} <= columns
    assert old_row == (1, None, None)


def test_sqlite_listing_upsert_keeps_error_class_the_worker_recorded(tmp_path):
    store = SqliteRecentHistoryStore(tmp_path / "recent.sqlite")
    record = failed_summary_record("query-daemon", status="exception", statement="SELECT 1")
    store.upsert_summaries([record])
    key = {
        "engine": record.engine,
        "source_kind": record.source_kind,
        "source_key": record.source_key,
        "query_id": record.query_id,
    }

    assert store.record_summary_error_class(**key, error_class="ParseException: frm x") is False
    assert store.record_summary_error_class(**key, error_class="ParseException") is True
    store.upsert_summaries([record])

    [payload] = store.load_payloads()
    assert payload["error_class"] == "ParseException"
    assert payload["statement_fingerprint"] == record.statement_fingerprint
    assert (
        store.record_summary_error_class(
            **(key | {"query_id": "query-missing"}), error_class="ParseException"
        )
        is False
    )


def test_recent_history_store_loads_newest_payloads_first(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(db_path)
    records = []
    for query_id, end_time in (
        ("query-old", "2026-07-03T10:03:00Z"),
        ("query-new", "2026-07-03T11:03:00Z"),
    ):
        records.append(
            history_record_from_candidate(
                RecentQueryCandidate(
                    summary=CMQuerySummary(
                        query_id=query_id,
                        start_time=end_time,
                        end_time=end_time,
                        duration_ms=180_000,
                        status="finished",
                        query_type="QUERY",
                    ),
                    selected=True,
                    reason="selected: test ordering",
                    sql_verb="SELECT",
                ),
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                recorded_at_iso="2026-07-03T10:05:00+00:00",
            )
        )

    assert store.upsert_summaries(records) == 2

    assert [payload["query_id"] for payload in store.load_payloads()] == [
        "query-new",
        "query-old",
    ]


def test_recent_history_store_enqueues_profile_budget_jobs_idempotently(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="query-1",
            duration_ms=3_700_000,
            status="failed",
            query_type="QUERY",
            statement="SELECT secret_column FROM sensitive_table",
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    jobs = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=5, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )

    store = SqliteRecentHistoryStore(db_path)
    assert store.upsert_summaries([record]) == 1
    assert store.enqueue_profile_jobs(jobs) == 1
    assert store.enqueue_profile_jobs(jobs) == 1

    rows = store.load_profile_jobs()
    assert len(rows) == 1
    assert rows[0]["query_id"] == "query-1"
    assert rows[0]["status"] == "pending"
    assert rows[0]["priority_score"] >= 100
    assert "failed_or_error_status" in rows[0]["priority_reasons"]
    rows_text = json.dumps(rows, sort_keys=True)
    assert "SELECT secret_column" not in rows_text
    assert "sensitive_table" not in rows_text


def test_recent_history_store_claims_pending_and_expired_profile_jobs(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="query-1",
            duration_ms=3_700_000,
            status="failed",
            query_type="QUERY",
            statement="SELECT secret_column FROM sensitive_table",
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    jobs = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=5, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )
    store = SqliteRecentHistoryStore(db_path)
    store.enqueue_profile_jobs(jobs)

    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert len(claimed) == 1
    assert claimed[0].query_id == "query-1"
    assert claimed[0].status == "leased"
    assert claimed[0].attempts == 1
    assert claimed[0].lease_owner == "worker_a"
    assert claimed[0].lease_until_iso == "2026-07-03T10:20:00+00:00"

    assert (
        store.claim_profile_jobs(
            max_jobs=1,
            lease_owner="worker-B",
            lease_until_iso="2026-07-03T10:25:00+00:00",
            now_iso="2026-07-03T10:15:00+00:00",
        )
        == []
    )

    reclaimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-B",
        lease_until_iso="2026-07-03T10:40:00+00:00",
        now_iso="2026-07-03T10:30:00+00:00",
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].lease_owner == "worker_b"

    rows_text = json.dumps(store.load_profile_jobs(), sort_keys=True)
    assert "SELECT secret_column" not in rows_text
    assert "sensitive_table" not in rows_text


def test_recent_history_store_claims_fresh_profile_jobs_first_within_priority(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    old_job = replace(
        profile_budget_job("query-old"),
        summary_end_time="2026-07-03T09:00:00+00:00",
    )
    fresh_job = replace(
        profile_budget_job("query-fresh"),
        summary_end_time="2026-07-03T10:00:00+00:00",
    )
    store = SqliteRecentHistoryStore(db_path)
    store.enqueue_profile_jobs([old_job, fresh_job])

    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )

    assert [record.query_id for record in claimed] == ["query-fresh"]


def test_recent_history_store_completes_profile_job_for_current_lease_owner(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    job = profile_budget_job("query-complete")
    store = SqliteRecentHistoryStore(db_path)
    store.enqueue_profile_jobs([job])

    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert [record.query_id for record in claimed] == ["query-complete"]
    assert store.renew_profile_job_lease(
        **profile_job_key(job),
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:25:00+00:00",
        now_iso="2026-07-03T10:12:00+00:00",
    )
    assert not store.complete_profile_job(
        **profile_job_key(job),
        lease_owner="worker-B",
        completed_at_iso="2026-07-03T10:13:00+00:00",
    )
    assert store.complete_profile_job(
        **profile_job_key(job),
        lease_owner="worker-A",
        completed_at_iso="2026-07-03T10:14:00+00:00",
    )

    assert (
        store.claim_profile_jobs(
            max_jobs=1,
            lease_owner="worker-C",
            lease_until_iso="2026-07-03T11:20:00+00:00",
            now_iso="2026-07-03T11:10:00+00:00",
        )
        == []
    )
    rows = store.load_profile_jobs()
    assert rows[0]["status"] == PROFILE_JOB_STATUS_COMPLETED
    assert rows[0]["attempts"] == 1
    assert rows[0]["lease_owner"] is None
    assert rows[0]["lease_until_iso"] is None
    assert rows[0]["last_error_code"] is None
    rows_text = json.dumps(rows, sort_keys=True)
    assert "SELECT secret_column" not in rows_text
    assert "sensitive_table" not in rows_text


def test_recent_history_store_retries_and_terminally_fails_profile_jobs(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    retry_job = profile_budget_job("query-retry")
    terminal_job = profile_budget_job("query-terminal")
    store = SqliteRecentHistoryStore(db_path)
    store.enqueue_profile_jobs([retry_job, terminal_job])

    claimed = store.claim_profile_jobs(
        max_jobs=2,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert {record.query_id for record in claimed} == {"query-retry", "query-terminal"}
    assert store.fail_profile_job(
        **profile_job_key(retry_job),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:12:00+00:00",
        error_code="profile-fetch-http-503",
        retry=True,
    )
    assert store.fail_profile_job(
        **profile_job_key(terminal_job),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:13:00+00:00",
        error_code="profile-fetch-permanent",
        retry=False,
    )

    rows = {str(row["query_id"]): row for row in store.load_profile_jobs()}
    assert rows["query-retry"]["status"] == PROFILE_JOB_STATUS_PENDING
    assert rows["query-retry"]["attempts"] == 1
    assert rows["query-retry"]["lease_owner"] is None
    assert rows["query-retry"]["last_error_code"] == "profile_fetch_http_503"
    assert rows["query-terminal"]["status"] == PROFILE_JOB_STATUS_FAILED
    assert rows["query-terminal"]["last_error_code"] == "profile_fetch_permanent"

    reclaimed = store.claim_profile_jobs(
        max_jobs=2,
        lease_owner="worker-B",
        lease_until_iso="2026-07-03T10:40:00+00:00",
        now_iso="2026-07-03T10:30:00+00:00",
    )
    assert [record.query_id for record in reclaimed] == ["query-retry"]
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].lease_owner == "worker_b"
    rows_text = json.dumps(store.load_profile_jobs(), sort_keys=True)
    assert "SELECT secret_column" not in rows_text
    assert "sensitive_table" not in rows_text


def test_recent_history_store_summarizes_profile_backlog_health(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    active_job = profile_budget_job("query-active")
    retry_job = profile_budget_job("query-retry")
    stale_job = profile_budget_job("query-stale")
    failed_job = profile_budget_job("query-terminal")
    pending_job = profile_budget_job("query-pending")
    store = SqliteRecentHistoryStore(db_path)
    store.enqueue_profile_jobs([active_job, retry_job, stale_job, failed_job])
    store.claim_profile_jobs(
        max_jobs=4,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert store.renew_profile_job_lease(
        **profile_job_key(active_job),
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:45:00+00:00",
        now_iso="2026-07-03T10:15:00+00:00",
    )
    assert store.fail_profile_job(
        **profile_job_key(retry_job),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:16:00+00:00",
        error_code="profile-fetch-http-503",
        retry=True,
    )
    assert store.fail_profile_job(
        **profile_job_key(failed_job),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:17:00+00:00",
        error_code="profile-fetch-permanent",
        retry=False,
    )
    store.enqueue_profile_jobs([pending_job])

    health = store.summarize_profile_backlog_health(
        now_iso="2026-07-03T10:30:00+00:00",
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
    )

    assert health.safe_payload() == {
        "pending_jobs": 1,
        "retry_pending_jobs": 1,
        "leased_jobs": 2,
        "stale_leased_jobs": 1,
        "failed_jobs": 1,
    }
    rows = {str(row["query_id"]): row for row in store.load_profile_jobs()}
    assert rows["query-active"]["status"] == PROFILE_JOB_STATUS_LEASED
    assert rows["query-stale"]["status"] == PROFILE_JOB_STATUS_LEASED
    assert rows["query-retry"]["status"] == PROFILE_JOB_STATUS_PENDING
    assert rows["query-terminal"]["status"] == PROFILE_JOB_STATUS_FAILED
    assert "query-active" not in json.dumps(health.safe_payload(), sort_keys=True)


def test_recent_history_store_requeues_terminal_failed_profile_jobs_by_filter(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    failed_summary = summary_history_record(
        "query-failed",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    completed_summary = summary_history_record(
        "query-completed",
        recorded_at_iso="2026-07-03T10:06:00+00:00",
    )
    other_source_summary = replace(
        summary_history_record(
            "query-other-source",
            recorded_at_iso="2026-07-03T10:07:00+00:00",
        ),
        source_key="cm:other:impala",
    )
    jobs = plan_recent_profile_jobs(
        [failed_summary, completed_summary, other_source_summary],
        policy=ProfileBudgetPolicy(max_jobs=3, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:08:00+00:00",
    )
    jobs_by_id = {job.query_id: job for job in jobs}
    store = SqliteRecentHistoryStore(db_path)
    store.upsert_summaries([failed_summary, completed_summary, other_source_summary])
    store.enqueue_profile_jobs(jobs)
    store.claim_profile_jobs(
        max_jobs=3,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert store.fail_profile_job(
        **profile_job_key(jobs_by_id["query-failed"]),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:12:00+00:00",
        error_code="profile-fetch-permanent",
        retry=False,
    )
    assert store.complete_profile_job(
        **profile_job_key(jobs_by_id["query-completed"]),
        lease_owner="worker-A",
        completed_at_iso="2026-07-03T10:13:00+00:00",
    )
    assert store.fail_profile_job(
        **profile_job_key(jobs_by_id["query-other-source"]),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:14:00+00:00",
        error_code="profile-fetch-other",
        retry=False,
    )

    dry_run = store.requeue_failed_profile_jobs(
        max_jobs=1,
        requeued_at_iso="2026-07-03T10:30:00+00:00",
        dry_run=True,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
    )
    assert dry_run.safe_payload() == {
        "matched_failed_jobs": 1,
        "selected_failed_jobs": 1,
        "requeued_jobs": 0,
        "skipped_due_to_limit": 0,
        "dry_run": True,
    }
    assert {row["query_id"]: row["status"] for row in store.load_profile_jobs()}[
        "query-failed"
    ] == PROFILE_JOB_STATUS_FAILED

    applied = store.requeue_failed_profile_jobs(
        max_jobs=1,
        requeued_at_iso="2026-07-03T10:31:00+00:00",
        dry_run=False,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
    )
    assert applied.safe_payload() == {
        "matched_failed_jobs": 1,
        "selected_failed_jobs": 1,
        "requeued_jobs": 1,
        "skipped_due_to_limit": 0,
        "dry_run": False,
    }

    rows = {str(row["query_id"]): row for row in store.load_profile_jobs()}
    assert rows["query-failed"]["status"] == PROFILE_JOB_STATUS_PENDING
    assert rows["query-failed"]["attempts"] == 0
    assert rows["query-failed"]["last_error_code"] is None
    assert rows["query-completed"]["status"] == PROFILE_JOB_STATUS_COMPLETED
    assert rows["query-other-source"]["status"] == PROFILE_JOB_STATUS_FAILED
    payloads = {str(payload["query_id"]): payload for payload in store.load_payloads()}
    assert payloads["query-failed"]["profile_status"] == PROFILE_STATUS_PENDING
    assert payloads["query-completed"]["profile_status"] == PROFILE_STATUS_ANALYZED
    assert payloads["query-other-source"]["profile_status"] == PROFILE_STATUS_FAILED
    serialized = json.dumps([applied.safe_payload(), rows], sort_keys=True)
    assert "SELECT secret_column" not in serialized
    assert "sensitive_table" not in serialized


def test_recent_history_store_projects_current_profile_status_into_payloads(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    summary = summary_history_record("query-status", recorded_at_iso="2026-07-03T10:05:00+00:00")
    job = plan_recent_profile_jobs(
        [summary],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )[0]
    store = SqliteRecentHistoryStore(db_path)
    store.upsert_summaries([summary])

    assert store.load_payloads()[0]["profile_status"] == "not_collected"
    store.enqueue_profile_jobs([job])
    assert store.load_payloads()[0]["profile_status"] == PROFILE_STATUS_PENDING

    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert [record.query_id for record in claimed] == ["query-status"]
    assert store.load_payloads()[0]["profile_status"] == PROFILE_STATUS_PROCESSING

    assert store.fail_profile_job(
        **profile_job_key(job),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:12:00+00:00",
        error_code="profile-fetch-http-503",
        retry=True,
    )
    payload = store.load_payloads()[0]
    assert payload["profile_status"] == PROFILE_STATUS_RETRY_PENDING
    assert payload["profile_last_error_code"] == "profile_fetch_http_503"

    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-B",
        lease_until_iso="2026-07-03T10:40:00+00:00",
        now_iso="2026-07-03T10:30:00+00:00",
    )
    assert [record.query_id for record in claimed] == ["query-status"]
    assert store.complete_profile_job(
        **profile_job_key(job),
        lease_owner="worker-B",
        completed_at_iso="2026-07-03T10:35:00+00:00",
    )
    payload = store.load_payloads()[0]
    assert payload["profile_status"] == PROFILE_STATUS_ANALYZED
    assert "profile_last_error_code" not in payload

    rediscovered = summary_history_record(
        "query-status",
        recorded_at_iso="2026-07-03T10:45:00+00:00",
    )
    store.upsert_summaries([rediscovered])
    payload = store.load_payloads()[0]
    assert payload["profile_status"] == PROFILE_STATUS_ANALYZED
    payload_text = json.dumps(payload, sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def test_recent_history_store_projects_terminal_profile_failure(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    summary = summary_history_record("query-terminal", recorded_at_iso="2026-07-03T10:05:00+00:00")
    job = plan_recent_profile_jobs(
        [summary],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )[0]
    store = SqliteRecentHistoryStore(db_path)
    store.upsert_summaries([summary])
    store.enqueue_profile_jobs([job])
    store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )

    assert store.fail_profile_job(
        **profile_job_key(job),
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:13:00+00:00",
        error_code="profile-fetch-permanent",
        retry=False,
    )

    assert store.load_payloads()[0]["profile_status"] == PROFILE_STATUS_FAILED
    assert store.load_payloads()[0]["profile_last_error_code"] == "profile_fetch_permanent"


def test_recent_history_store_loads_materialized_analysis_payloads(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    summary = replace(
        summary_history_record(
            "query-materialized",
            recorded_at_iso="2026-07-03T10:05:00+00:00",
        ),
        profile_status=PROFILE_STATUS_ANALYZED,
    )
    store = SqliteRecentHistoryStore(db_path)
    store.upsert_summaries([summary])
    store.store_analysis_cache_records(
        [
            analysis_cache_record(
                {
                    "score": 72,
                    "score_severity": "high",
                    "score_reasons": ["runtime evidence available"],
                    "raw_sql": "SELECT secret_column FROM sensitive_table",
                },
                query_id="query-materialized",
            )
        ]
    )
    store.store_profile_artifact_records([profile_artifact_record(query_id="query-materialized")])

    payload = store.load_materialized_payloads()[0]

    assert payload["profile_status"] == PROFILE_STATUS_ANALYZED
    assert payload["analysis_cache_payload"] == {
        "score": 72,
        "score_severity": "high",
        "score_reasons": ["runtime evidence available"],
    }
    rendered = json.dumps(payload, sort_keys=True)
    assert "raw_sql" not in rendered
    assert "profile_fingerprint_v1" not in rendered
    assert "sha256_deadbeef" not in rendered


def test_recent_history_store_limits_materialized_payloads_to_newest_rows(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(db_path)
    store.upsert_summaries(
        [
            summary_history_record("query-old", recorded_at_iso="2026-07-03T10:00:00+00:00"),
            summary_history_record("query-middle", recorded_at_iso="2026-07-03T11:00:00+00:00"),
            summary_history_record("query-new", recorded_at_iso="2026-07-03T12:00:00+00:00"),
        ]
    )

    payloads = store.load_materialized_payloads(limit=2)

    assert [payload["query_id"] for payload in payloads] == ["query-new", "query-middle"]


def test_recent_history_store_details_ready_view_reaches_past_newer_unprocessed_rows(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(db_path)
    ready = replace(
        summary_history_record("query-ready", recorded_at_iso="2026-07-03T10:00:00+00:00"),
        profile_status=PROFILE_STATUS_ANALYZED,
    )
    newer_unprocessed = [
        summary_history_record(
            f"query-new-{index:03d}",
            recorded_at_iso="2026-07-03T12:00:00+00:00",
        )
        for index in range(501)
    ]
    store.upsert_summaries([ready, *newer_unprocessed])
    store.store_analysis_cache_records(
        [analysis_cache_record({"score": 72}, query_id="query-ready")]
    )
    store.store_profile_artifact_records([profile_artifact_record(query_id="query-ready")])

    payloads = store.load_materialized_payloads(limit=500, details_ready_only=True)

    assert [payload["query_id"] for payload in payloads] == ["query-ready"]
    assert payloads[0]["analysis_cache_payload"] == {"score": 72}


@pytest.fixture(params=["sqlite", "postgres"])
def any_history_store(request, tmp_path):
    """SQLite always; live Postgres in a throwaway schema when a DSN is set."""
    if request.param == "sqlite":
        yield SqliteRecentHistoryStore(tmp_path / "recent-history.sqlite")
        return
    dsn = os.environ.get("QUERY_DOCTOR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("QUERY_DOCTOR_TEST_POSTGRES_DSN is not set; live Postgres tests are skipped")
    psycopg = pytest.importorskip("psycopg")
    schema = f"qd_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
    try:
        yield PostgresRecentHistoryStore(
            psycopg.conninfo.make_conninfo(dsn, options=f"-c search_path={schema}")
        )
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {schema} CASCADE")


def test_details_ready_view_takes_the_newest_ready_queries_by_their_latest_artifact(
    any_history_store,
):
    store = any_history_store

    def analyzed(query_id, hour):
        return replace(
            summary_history_record(query_id, recorded_at_iso=f"2026-07-03T{hour:02d}:00:00+00:00"),
            profile_status=PROFILE_STATUS_ANALYZED,
        )

    store.upsert_summaries(
        [
            analyzed("query-a", 8),
            analyzed("query-b", 9),
            # Same time as query-b: the query ID breaks the tie.
            analyzed("query-c", 9),
            analyzed("query-d", 10),
            # Its latest artifact has no ready analysis; an older one does.
            analyzed("query-stale", 11),
            # Newest, but its profile was never analyzed.
            summary_history_record("query-pending", recorded_at_iso="2026-07-03T12:00:00+00:00"),
        ]
    )
    for query_id in ("query-a", "query-b", "query-c", "query-d", "query-stale", "query-pending"):
        store.store_profile_artifact_records([profile_artifact_record(query_id=query_id)])
        store.store_analysis_cache_records(
            [analysis_cache_record({"score": len(query_id)}, query_id=query_id)]
        )
    # query-d's newer artifact is ready too, and the view must use it.
    store.store_profile_artifact_records(
        [
            profile_artifact_record(
                query_id="query-d",
                profile_fingerprint="profile_fingerprint_v2",
                recorded_at_iso="2026-07-03T10:45:00+00:00",
            ),
            profile_artifact_record(
                query_id="query-stale",
                profile_fingerprint="profile_fingerprint_v2",
                recorded_at_iso="2026-07-03T11:45:00+00:00",
            ),
        ]
    )
    store.store_analysis_cache_records(
        [
            replace(
                analysis_cache_record({"score": 99}, query_id="query-d"),
                profile_fingerprint="profile_fingerprint_v2",
            )
        ]
    )

    payloads = store.load_materialized_payloads(limit=500, details_ready_only=True)

    assert [payload["query_id"] for payload in payloads] == [
        "query-d",
        "query-b",
        "query-c",
        "query-a",
    ]
    assert payloads[0]["analysis_cache_payload"] == {"score": 99}
    limited = store.load_materialized_payloads(limit=2, details_ready_only=True)
    assert [payload["query_id"] for payload in limited] == ["query-d", "query-b"]


def test_recent_history_store_upserts_raw_free_analysis_cache(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(db_path)

    assert (
        store.store_analysis_cache_records(
            [
                analysis_cache_record(
                    {
                        "diagnosis_status": "ok",
                        "profile_resource_facts": {"peak_memory_label": "high"},
                        "statement": "SELECT secret_column FROM sensitive_table",
                        "case_dir": "/private/tmp/query-doctor-secret",
                    }
                )
            ]
        )
        == 1
    )
    loaded = store.load_analysis_cache_record(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-cache",
        profile_fingerprint="profile_fingerprint_v1",
        analyzer_contract="profile_digest_analysis_json_v1",
    )
    assert loaded is not None
    assert loaded.status == "ready"
    assert dict(loaded.payload) == {
        "diagnosis_status": "ok",
        "profile_resource_facts": {"peak_memory_label": "high"},
    }

    assert (
        store.store_analysis_cache_records(
            [
                analysis_cache_record(
                    {"diagnosis_status": "reused", "raw_sql": "SELECT secret_column"},
                    status="reused",
                )
            ]
        )
        == 1
    )
    reloaded = store.load_analysis_cache_record(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-cache",
        profile_fingerprint="profile_fingerprint_v1",
        analyzer_contract="profile_digest_analysis_json_v1",
    )
    assert reloaded is not None
    assert reloaded.status == "reused"
    assert dict(reloaded.payload) == {"diagnosis_status": "reused"}

    assert (
        store.load_analysis_cache_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="query-cache",
            profile_fingerprint="profile/fingerprint/v1",
            analyzer_contract="profile_digest_analysis_json_v1",
        )
        is None
    )
    cache_text = json.dumps(reloaded.safe_payload(), sort_keys=True)
    assert "SELECT secret_column" not in cache_text
    assert "sensitive_table" not in cache_text
    assert "/private/tmp" not in cache_text


def test_recent_history_store_upserts_profile_artifact_metadata(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(db_path)
    unsafe_record = profile_artifact_record(
        storage_key="/private/tmp/query-doctor-secret/profile.txt",
    )

    assert unsafe_record.safe_payload() == {}
    assert store.store_profile_artifact_records([unsafe_record]) == 0
    assert store.store_profile_artifact_records([profile_artifact_record()]) == 1
    assert (
        store.store_profile_artifact_records(
            [profile_artifact_record(status="available", storage_key="sha256_rewritten")]
        )
        == 1
    )

    loaded = store.load_profile_artifact_record(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-artifact",
        profile_fingerprint="profile_fingerprint_v1",
        artifact_contract="profile_artifact_v1",
    )
    assert loaded is not None
    assert loaded.storage_key == "sha256_rewritten"
    assert loaded.storage_kind == "fingerprint_only"
    assert loaded.size_bytes == 4096

    assert (
        store.load_profile_artifact_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="query-artifact",
            profile_fingerprint="profile/fingerprint/v1",
            artifact_contract="profile_artifact_v1",
        )
        is None
    )
    artifact_text = json.dumps(loaded.safe_payload(), sort_keys=True)
    assert "/private/tmp" not in artifact_text
    assert "profile.txt" not in artifact_text


def test_recent_history_store_prunes_raw_free_history_without_active_jobs(tmp_path):
    db_path = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(db_path)
    old_summary = summary_history_record(
        "query-old-summary",
        recorded_at_iso="2026-07-01T00:00:00+00:00",
    )
    recent_summary = summary_history_record(
        "query-recent-summary",
        recorded_at_iso="2026-07-03T00:00:00+00:00",
    )
    old_terminal_job = replace(
        profile_budget_job("query-old-terminal"),
        status=PROFILE_JOB_STATUS_COMPLETED,
        updated_at_iso="2026-07-01T00:00:00+00:00",
    )
    old_pending_job = replace(
        profile_budget_job("query-old-pending"),
        status=PROFILE_JOB_STATUS_PENDING,
        updated_at_iso="2026-07-01T00:00:00+00:00",
    )
    recent_terminal_job = replace(
        profile_budget_job("query-recent-terminal"),
        status=PROFILE_JOB_STATUS_COMPLETED,
        updated_at_iso="2026-07-03T00:00:00+00:00",
    )
    store.upsert_summaries([old_summary, recent_summary])
    store.enqueue_profile_jobs([old_terminal_job, old_pending_job, recent_terminal_job])
    store.store_analysis_cache_records(
        [
            analysis_cache_record(
                {"diagnosis_status": "old"},
                query_id="query-old-cache",
                recorded_at_iso="2026-07-01T00:00:00+00:00",
            ),
            analysis_cache_record(
                {"diagnosis_status": "recent"},
                query_id="query-recent-cache",
                recorded_at_iso="2026-07-03T00:00:00+00:00",
            ),
        ]
    )
    store.store_profile_artifact_records(
        [
            profile_artifact_record(
                query_id="query-old-artifact",
                recorded_at_iso="2026-07-01T00:00:00+00:00",
            ),
            profile_artifact_record(
                query_id="query-recent-artifact",
                recorded_at_iso="2026-07-03T00:00:00+00:00",
                storage_key="sha256_recent",
            ),
        ]
    )

    result = store.prune_history(
        policy=RecentHistoryRetentionPolicy(
            summary_cutoff_iso="2026-07-02T00:00:00+00:00",
            profile_job_cutoff_iso="2026-07-02T00:00:00+00:00",
            analysis_cache_cutoff_iso="2026-07-02T00:00:00+00:00",
            profile_artifact_cutoff_iso="2026-07-02T00:00:00+00:00",
        )
    )

    assert result.summaries_deleted == 1
    assert result.profile_jobs_deleted == 1
    assert result.analysis_cache_deleted == 1
    assert result.profile_artifacts_deleted == 1
    assert result.total_deleted == 4
    assert store.count_summaries() == 1
    payloads_text = json.dumps(store.load_payloads(), sort_keys=True)
    assert "query-recent-summary" in payloads_text
    assert "query-old-summary" not in payloads_text
    remaining_jobs = {str(row["query_id"]): row for row in store.load_profile_jobs()}
    assert set(remaining_jobs) == {"query-old-pending", "query-recent-terminal"}
    assert remaining_jobs["query-old-pending"]["status"] == PROFILE_JOB_STATUS_PENDING
    assert (
        store.load_analysis_cache_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="query-old-cache",
            profile_fingerprint="profile_fingerprint_v1",
            analyzer_contract="profile_digest_analysis_json_v1",
        )
        is None
    )
    assert (
        store.load_analysis_cache_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="query-recent-cache",
            profile_fingerprint="profile_fingerprint_v1",
            analyzer_contract="profile_digest_analysis_json_v1",
        )
        is not None
    )
    assert (
        store.load_profile_artifact_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="query-old-artifact",
            profile_fingerprint="profile_fingerprint_v1",
            artifact_contract="profile_artifact_v1",
        )
        is None
    )
    assert (
        store.load_profile_artifact_record(
            engine="impala",
            source_kind="cm",
            source_key="cm:cluster:impala",
            query_id="query-recent-artifact",
            profile_fingerprint="profile_fingerprint_v1",
            artifact_contract="profile_artifact_v1",
        )
        is not None
    )


def test_recent_history_store_warning_is_path_free():
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="query-1",
            duration_ms=180_000,
            query_type="QUERY",
            statement="SELECT secret_column FROM sensitive_table",
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )

    class FailingStore:
        def initialize(self) -> None:
            raise AssertionError("not called directly")

        def upsert_summaries(self, records):
            assert len(list(records)) == 1
            raise RecentHistoryStoreError(
                "could not open /private/tmp/query-doctor-secret/recent.sqlite"
            )

    count, warning = persist_recent_history_with_store(
        FailingStore(),
        [candidate],
        config=SimpleNamespace(
            query_profile_source="cm",
            cluster="cluster",
            service="impala",
            impala_profile_hosts=(),
        ),
    )

    assert count == 0
    assert warning is not None
    assert "/private/tmp" not in warning
    assert "query-doctor-secret" not in warning
    assert "Recent history store was not updated" in warning
