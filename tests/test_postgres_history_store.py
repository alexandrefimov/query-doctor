import json
import re

import pytest

from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
from query_doctor.recent.history_store import (
    RecentHistoryStoreError,
    RecentHistoryRetentionPolicy,
    history_record_from_candidate,
)
from query_doctor.recent.postgres_history_store import (
    POSTGRES_RECENT_ANALYSIS_CACHE_SELECT,
    POSTGRES_RECENT_ANALYSIS_CACHE_UPSERT,
    POSTGRES_RECENT_ANALYSIS_CACHE_PRUNE,
    POSTGRES_RECENT_DETAILS_READY_PAYLOADS_SELECT,
    POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT,
    POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH,
    POSTGRES_RECENT_PROFILE_ARTIFACT_SELECT,
    POSTGRES_RECENT_PROFILE_ARTIFACT_UPSERT,
    POSTGRES_RECENT_PROFILE_ARTIFACT_PRUNE,
    POSTGRES_RECENT_PROFILE_JOB_CLAIM,
    POSTGRES_RECENT_PROFILE_JOB_COMPLETE,
    POSTGRES_RECENT_PROFILE_JOB_FAIL,
    POSTGRES_RECENT_PROFILE_JOB_PRUNE,
    POSTGRES_RECENT_PROFILE_JOB_REQUEUE_COUNT,
    POSTGRES_RECENT_PROFILE_JOB_REQUEUE_SELECT,
    POSTGRES_RECENT_PROFILE_JOB_REQUEUE_UPDATE,
    POSTGRES_RECENT_QUERY_SUMMARY_DDL,
    POSTGRES_RECENT_PROFILE_JOB_INSERT,
    POSTGRES_RECENT_PROFILE_JOB_RENEW_LEASE,
    POSTGRES_RECENT_QUERY_SUMMARY_PRUNE,
    POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_REQUEUE,
    POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
    POSTGRES_RECENT_QUERY_SUMMARY_UPSERT,
    PostgresRecentHistoryStore,
    profile_job_to_postgres_row,
    record_to_postgres_row,
)
from query_doctor.recent.profile_budget import (
    ANALYSIS_CACHE_SCHEMA_VERSION,
    ANALYSIS_CACHE_STORAGE_COLUMNS,
    PROFILE_ARTIFACT_SCHEMA_VERSION,
    PROFILE_ARTIFACT_STORAGE_COLUMNS,
    PROFILE_JOB_STATUS_COMPLETED,
    PROFILE_JOB_STATUS_FAILED,
    PROFILE_JOB_STATUS_LEASED,
    PROFILE_JOB_STATUS_PENDING,
    PROFILE_JOB_STORAGE_COLUMNS,
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


class FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self.executed: list[str] = []
        self.execute_calls = []
        self.executemany_calls: list[tuple[str, list[dict[str, object]]]] = []
        self.rows = list(rows or [])
        self.rowcounts = list(rowcount) if isinstance(rowcount, (list, tuple)) else []
        self.rowcount = 0 if self.rowcounts else rowcount

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None):
        self.executed.append(statement)
        self.execute_calls.append((statement, params))
        if self.rowcounts:
            self.rowcount = self.rowcounts.pop(0)

    def executemany(self, statement, rows):
        self.executemany_calls.append((statement, list(rows)))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, rows=None, rowcount=0):
        self.cursor_obj = FakeCursor(rows=rows, rowcount=rowcount)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self.cursor_obj


def test_postgres_history_store_initializes_and_upserts_raw_free_rows():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    record = history_record_from_candidate(
        RecentQueryCandidate(
            summary=CMQuerySummary(
                query_id="query-1",
                duration_ms=180_000,
                query_type="QUERY",
                statement="SELECT secret_column FROM sensitive_table",
                bytes_read=1024,
            ),
            selected=True,
            reason="selected: SELECT-like user query",
            sql_verb="SELECT",
        ),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    assert store.upsert_summaries([record]) == 1

    ddl_cursor = connections[0].cursor_obj
    upsert_cursor = connections[1].cursor_obj
    assert len(ddl_cursor.executed) == len(POSTGRES_RECENT_QUERY_SUMMARY_DDL)
    assert "jsonb" in "\n".join(ddl_cursor.executed)
    assert any(
        "recent_query_summary_latest_idx" in statement
        and "COALESCE(end_time, start_time, recorded_at_iso)" in statement
        and "query_id" in statement
        for statement in ddl_cursor.executed
    )
    assert "recent_query_summary.profile_status" in POSTGRES_RECENT_QUERY_SUMMARY_UPSERT
    assert upsert_cursor.executemany_calls[0][0] == POSTGRES_RECENT_QUERY_SUMMARY_UPSERT
    rows = upsert_cursor.executemany_calls[0][1]
    assert rows[0]["statement_present"] is True
    assert rows[0]["selected"] is True
    payload_text = json.dumps(rows[0], sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def test_postgres_history_store_initializes_once_per_store():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    store.initialize()
    store.initialize()

    assert len(connections) == 1
    assert len(connections[0].cursor_obj.executed) == len(POSTGRES_RECENT_QUERY_SUMMARY_DDL)


def test_postgres_history_store_enqueues_profile_jobs_raw_free():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    record = history_record_from_candidate(
        RecentQueryCandidate(
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
        ),
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

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    assert store.enqueue_profile_jobs(jobs) == 1

    ddl_cursor = connections[0].cursor_obj
    insert_cursor = connections[1].cursor_obj
    ddl_text = "\n".join(ddl_cursor.executed)
    assert "recent_profile_job" in ddl_text
    assert "recent_analysis_cache" in ddl_text
    assert "recent_profile_artifact" in ddl_text
    assert insert_cursor.executemany_calls[0][0] == POSTGRES_RECENT_PROFILE_JOB_INSERT
    assert (
        insert_cursor.executemany_calls[1][0] == POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE
    )
    assert insert_cursor.executemany_calls[1][1][0]["profile_status"] == PROFILE_STATUS_PENDING
    rows = insert_cursor.executemany_calls[0][1]
    assert rows[0]["status"] == "pending"
    assert rows[0]["priority_score"] >= 100
    payload_text = json.dumps(rows[0], sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def test_optional_filter_parameters_are_typed_for_postgres():
    # A parameter whose only other use is `IS NULL` gives Postgres nothing to
    # infer a type from, and it refuses the statement:
    #
    #   AmbiguousParameter: could not determine data type of parameter $1
    #   LINE 6:  ($1 IS NULL OR engine = $1)
    #
    # The fake cursor these tests use does not parse SQL, so the whole optional
    # filter path passed here while failing against a real database.
    statements = {
        "POSTGRES_RECENT_PROFILE_JOB_CLAIM": POSTGRES_RECENT_PROFILE_JOB_CLAIM,
        "POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH": POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH,
    }
    untyped = re.compile(r"%\((\w+_filter)\)s\s+IS NULL")
    for name, sql in statements.items():
        assert not untyped.search(sql), f"{name} compares a bare parameter with IS NULL"
        assert "::text IS NULL" in sql, f"{name} lost its optional filter casts"


def test_postgres_history_store_claims_profile_jobs_with_skip_locked():
    record = history_record_from_candidate(
        RecentQueryCandidate(
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
        ),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    job = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )[0]
    claimed_row = {
        **profile_job_to_postgres_row(job),
        "status": PROFILE_JOB_STATUS_LEASED,
        "attempts": 1,
        "lease_owner": "worker_a",
        "lease_until_iso": "2026-07-03T10:20:00+00:00",
        "updated_at_iso": "2026-07-03T10:10:00+00:00",
    }
    claim_values = tuple(claimed_row[column] for column in PROFILE_JOB_STORAGE_COLUMNS)
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        rows = [claim_values] if len(connections) == 1 else []
        connection = FakeConnection(rows=rows)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )

    claim_cursor = connections[1].cursor_obj
    statement, params = claim_cursor.execute_calls[0]
    assert statement == POSTGRES_RECENT_PROFILE_JOB_CLAIM
    assert "FOR UPDATE SKIP LOCKED" in statement
    assert "ORDER BY priority_score DESC, summary_end_time DESC" in statement
    assert params["lease_owner"] == "worker_a"
    assert params["pending_status"] == "pending"
    assert params["leased_status"] == "leased"
    assert (
        claim_cursor.executemany_calls[0][0] == POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE
    )
    assert claim_cursor.executemany_calls[0][1][0]["profile_status"] == PROFILE_STATUS_PROCESSING
    assert [job.query_id for job in claimed] == ["query-1"]
    assert claimed[0].status == "leased"
    assert claimed[0].attempts == 1
    payload_text = json.dumps([job.safe_payload() for job in claimed], sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def test_postgres_history_store_renews_profile_job_lease_with_owner_guard():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection(rowcount=1 if len(connections) == 1 else 0)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    assert store.renew_profile_job_lease(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-1",
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:25:00+00:00",
        now_iso="2026-07-03T10:12:00+00:00",
    )

    statement, params = connections[1].cursor_obj.execute_calls[0]
    assert statement == POSTGRES_RECENT_PROFILE_JOB_RENEW_LEASE
    assert params["engine"] == "impala"
    assert params["source_kind"] == "cm"
    assert params["source_key"] == "cm:cluster:impala"
    assert params["query_id"] == "query-1"
    assert params["lease_owner"] == "worker_a"
    assert params["lease_until_iso"] == "2026-07-03T10:25:00+00:00"
    assert params["now_iso"] == "2026-07-03T10:12:00+00:00"
    assert params["leased_status"] == PROFILE_JOB_STATUS_LEASED


def test_postgres_history_store_completes_profile_job_with_owner_guard():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection(rowcount=1 if len(connections) == 1 else 0)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    assert store.complete_profile_job(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-1",
        lease_owner="worker-A",
        completed_at_iso="2026-07-03T10:14:00+00:00",
    )

    statement, params = connections[1].cursor_obj.execute_calls[0]
    assert statement == POSTGRES_RECENT_PROFILE_JOB_COMPLETE
    assert "last_error_code = NULL" in statement
    assert "last_error_at_iso = NULL" in statement
    assert params["completed_status"] == PROFILE_JOB_STATUS_COMPLETED
    assert params["completed_at_iso"] == "2026-07-03T10:14:00+00:00"
    assert params["leased_status"] == PROFILE_JOB_STATUS_LEASED
    assert params["lease_owner"] == "worker_a"
    status_statement, status_params = connections[1].cursor_obj.execute_calls[1]
    assert status_statement == POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE
    assert status_params["profile_status"] == PROFILE_STATUS_ANALYZED


def test_postgres_history_store_fails_profile_job_as_retry_or_terminal():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection(rowcount=1 if len(connections) in {1, 2} else 0)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    assert store.fail_profile_job(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-retry",
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:12:00+00:00",
        error_code="profile-fetch-http-503",
        retry=True,
    )
    assert store.fail_profile_job(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-terminal",
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:13:00+00:00",
        error_code="profile-fetch-permanent",
        retry=False,
    )

    retry_statement, retry_params = connections[1].cursor_obj.execute_calls[0]
    terminal_statement, terminal_params = connections[2].cursor_obj.execute_calls[0]
    assert retry_statement == POSTGRES_RECENT_PROFILE_JOB_FAIL
    assert terminal_statement == POSTGRES_RECENT_PROFILE_JOB_FAIL
    assert retry_params["next_status"] == PROFILE_JOB_STATUS_PENDING
    assert retry_params["last_error_code"] == "profile_fetch_http_503"
    assert terminal_params["next_status"] == PROFILE_JOB_STATUS_FAILED
    assert terminal_params["last_error_code"] == "profile_fetch_permanent"
    assert retry_params["leased_status"] == PROFILE_JOB_STATUS_LEASED
    assert terminal_params["lease_owner"] == "worker_a"
    retry_status_statement, retry_status_params = connections[1].cursor_obj.execute_calls[1]
    terminal_status_statement, terminal_status_params = connections[2].cursor_obj.execute_calls[1]
    assert retry_status_statement == POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE
    assert terminal_status_statement == POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE
    assert retry_status_params["profile_status"] == PROFILE_STATUS_RETRY_PENDING
    assert terminal_status_params["profile_status"] == PROFILE_STATUS_FAILED


def test_postgres_history_store_requeues_failed_profile_jobs_with_bounded_update():
    requeue_key = ("impala", "cm", "cm:cluster:impala", "query-requeue")

    class RequeueCursor:
        def __init__(self):
            self.execute_calls = []
            self.executemany_calls = []
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement, params=None):
            self.execute_calls.append((statement, params))
            if statement == POSTGRES_RECENT_PROFILE_JOB_REQUEUE_COUNT:
                self.rows = [(2,)]
            elif statement in {
                POSTGRES_RECENT_PROFILE_JOB_REQUEUE_SELECT,
                POSTGRES_RECENT_PROFILE_JOB_REQUEUE_UPDATE,
            }:
                self.rows = [requeue_key]
            else:
                self.rows = []

        def executemany(self, statement, rows):
            self.executemany_calls.append((statement, list(rows)))

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class RequeueConnection:
        def __init__(self):
            self.cursor_obj = RequeueCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return self.cursor_obj

    connections: list[FakeConnection | RequeueConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection() if not connections else RequeueConnection()
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    dry_run = store.requeue_failed_profile_jobs(
        max_jobs=1,
        requeued_at_iso="2026-07-03T10:30:00+00:00",
        dry_run=True,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
    )

    dry_cursor = connections[1].cursor_obj
    assert dry_run.safe_payload() == {
        "matched_failed_jobs": 2,
        "selected_failed_jobs": 1,
        "requeued_jobs": 0,
        "skipped_due_to_limit": 1,
        "dry_run": True,
    }
    assert dry_cursor.execute_calls[0][0] == POSTGRES_RECENT_PROFILE_JOB_REQUEUE_COUNT
    assert dry_cursor.execute_calls[1][0] == POSTGRES_RECENT_PROFILE_JOB_REQUEUE_SELECT
    assert dry_cursor.executemany_calls == []

    applied = store.requeue_failed_profile_jobs(
        max_jobs=1,
        requeued_at_iso="2026-07-03T10:31:00+00:00",
        dry_run=False,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
    )

    apply_cursor = connections[2].cursor_obj
    update_statement, update_params = apply_cursor.execute_calls[1]
    assert applied.safe_payload() == {
        "matched_failed_jobs": 2,
        "selected_failed_jobs": 1,
        "requeued_jobs": 1,
        "skipped_due_to_limit": 1,
        "dry_run": False,
    }
    assert update_statement == POSTGRES_RECENT_PROFILE_JOB_REQUEUE_UPDATE
    assert "FOR UPDATE SKIP LOCKED" in update_statement
    assert "attempts = 0" in update_statement
    assert update_params["pending_status"] == PROFILE_JOB_STATUS_PENDING
    assert update_params["failed_status"] == PROFILE_JOB_STATUS_FAILED
    assert update_params["limit"] == 1
    assert apply_cursor.executemany_calls[0][0] == (
        POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_REQUEUE
    )
    summary_params = apply_cursor.executemany_calls[0][1][0]
    assert summary_params["pending_profile_status"] == PROFILE_STATUS_PENDING
    assert summary_params["failed_profile_status"] == PROFILE_STATUS_FAILED
    assert "query-requeue" not in json.dumps(applied.safe_payload(), sort_keys=True)


def test_postgres_history_store_summarizes_profile_backlog_health():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        rows = [(2, 1, 3, 1, 4)] if len(connections) == 1 else []
        connection = FakeConnection(rows=rows)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    health = store.summarize_profile_backlog_health(
        now_iso="2026-07-03T10:30:00+00:00",
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
    )

    cursor = connections[1].cursor_obj
    statement, params = cursor.execute_calls[0]
    assert statement == POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH
    assert "summary.profile_status" in statement
    assert "job.last_error_code IS NOT NULL" in statement
    assert params["pending_status"] == PROFILE_JOB_STATUS_PENDING
    assert params["retry_profile_status"] == PROFILE_STATUS_RETRY_PENDING
    assert params["leased_status"] == PROFILE_JOB_STATUS_LEASED
    assert params["failed_status"] == PROFILE_JOB_STATUS_FAILED
    assert params["now_iso"] == "2026-07-03T10:30:00+00:00"
    assert params["engine_filter"] == "impala"
    assert params["source_kind_filter"] == "cm"
    assert params["source_key_filter"] == "cm:cluster:impala"
    assert health.safe_payload() == {
        "pending_jobs": 2,
        "retry_pending_jobs": 1,
        "leased_jobs": 3,
        "stale_leased_jobs": 1,
        "failed_jobs": 4,
    }


def test_postgres_history_store_upserts_analysis_cache_raw_free():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    record = RecentAnalysisCacheRecord(
        schema_version=ANALYSIS_CACHE_SCHEMA_VERSION,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-cache",
        profile_fingerprint="profile_fingerprint_v1",
        analyzer_contract="profile_digest_analysis_json_v1",
        recorded_at_iso="2026-07-03T10:30:00+00:00",
        status="ready",
        payload={
            "diagnosis_status": "ok",
            "profile_resource_facts": {"peak_memory_label": "high"},
            "statement": "SELECT secret_column FROM sensitive_table",
            "case_dir": "/private/tmp/query-doctor-secret",
        },
    )

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    assert store.store_analysis_cache_records([record]) == 1

    upsert_cursor = connections[1].cursor_obj
    statement, rows = upsert_cursor.executemany_calls[0]
    assert statement == POSTGRES_RECENT_ANALYSIS_CACHE_UPSERT
    assert rows[0]["profile_fingerprint"] == "profile_fingerprint_v1"
    assert rows[0]["analyzer_contract"] == "profile_digest_analysis_json_v1"
    payload = json.loads(str(rows[0]["payload_json"]))
    assert payload == {
        "diagnosis_status": "ok",
        "profile_resource_facts": {"peak_memory_label": "high"},
    }
    payload_text = json.dumps(rows[0], sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text
    assert "/private/tmp" not in payload_text


def test_postgres_history_store_loads_analysis_cache_by_compatibility_key():
    row = {
        "schema_version": ANALYSIS_CACHE_SCHEMA_VERSION,
        "engine": "impala",
        "source_kind": "cm",
        "source_key": "cm:cluster:impala",
        "query_id": "query-cache",
        "profile_fingerprint": "profile_fingerprint_v1",
        "analyzer_contract": "profile_digest_analysis_json_v1",
        "recorded_at_iso": "2026-07-03T10:30:00+00:00",
        "status": "ready",
        "payload_json": {
            "diagnosis_status": "ok",
            "statement": "SELECT secret_column FROM sensitive_table",
        },
    }
    values = tuple(row[column] for column in ANALYSIS_CACHE_STORAGE_COLUMNS)
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        rows = [values] if len(connections) == 1 else []
        connection = FakeConnection(rows=rows)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    loaded = store.load_analysis_cache_record(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-cache",
        profile_fingerprint="profile_fingerprint_v1",
        analyzer_contract="profile_digest_analysis_json_v1",
    )

    load_cursor = connections[1].cursor_obj
    statement, params = load_cursor.execute_calls[0]
    assert statement == POSTGRES_RECENT_ANALYSIS_CACHE_SELECT
    assert params["profile_fingerprint"] == "profile_fingerprint_v1"
    assert params["analyzer_contract"] == "profile_digest_analysis_json_v1"
    assert loaded is not None
    assert dict(loaded.payload) == {"diagnosis_status": "ok"}
    payload_text = json.dumps(loaded.safe_payload(), sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def test_postgres_history_store_upserts_profile_artifacts_raw_free():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    unsafe = RecentProfileArtifactRecord(
        schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-artifact",
        profile_fingerprint="profile_fingerprint_v1",
        artifact_contract="profile_artifact_v1",
        recorded_at_iso="2026-07-03T10:30:00+00:00",
        status="available",
        storage_kind="local",
        storage_key="/private/tmp/query-doctor-secret/profile.txt",
        size_bytes=4096,
    )
    record = RecentProfileArtifactRecord(
        schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-artifact",
        profile_fingerprint="profile_fingerprint_v1",
        artifact_contract="profile_artifact_v1",
        recorded_at_iso="2026-07-03T10:30:00+00:00",
        status="available",
        storage_kind="local",
        storage_key="sha256_deadbeef",
        size_bytes=4096,
    )

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    assert store.store_profile_artifact_records([unsafe]) == 0
    assert connections == []
    assert store.store_profile_artifact_records([record]) == 1

    upsert_cursor = connections[1].cursor_obj
    statement, rows = upsert_cursor.executemany_calls[0]
    assert statement == POSTGRES_RECENT_PROFILE_ARTIFACT_UPSERT
    assert rows[0]["profile_fingerprint"] == "profile_fingerprint_v1"
    assert rows[0]["artifact_contract"] == "profile_artifact_v1"
    assert rows[0]["storage_kind"] == "fingerprint_only"
    assert rows[0]["storage_key"] == "sha256_deadbeef"
    payload_text = json.dumps(rows[0], sort_keys=True)
    assert "/private/tmp" not in payload_text
    assert "profile.txt" not in payload_text


def test_postgres_history_store_loads_profile_artifact_by_compatibility_key():
    row = {
        "schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "engine": "impala",
        "source_kind": "cm",
        "source_key": "cm:cluster:impala",
        "query_id": "query-artifact",
        "profile_fingerprint": "profile_fingerprint_v1",
        "artifact_contract": "profile_artifact_v1",
        "recorded_at_iso": "2026-07-03T10:30:00+00:00",
        "status": "available",
        "storage_kind": "local",
        "storage_key": "sha256_deadbeef",
        "size_bytes": 4096,
    }
    values = tuple(row[column] for column in PROFILE_ARTIFACT_STORAGE_COLUMNS)
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        rows = [values] if len(connections) == 1 else []
        connection = FakeConnection(rows=rows)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    loaded = store.load_profile_artifact_record(
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-artifact",
        profile_fingerprint="profile_fingerprint_v1",
        artifact_contract="profile_artifact_v1",
    )

    load_cursor = connections[1].cursor_obj
    statement, params = load_cursor.execute_calls[0]
    assert statement == POSTGRES_RECENT_PROFILE_ARTIFACT_SELECT
    assert params["profile_fingerprint"] == "profile_fingerprint_v1"
    assert params["artifact_contract"] == "profile_artifact_v1"
    assert loaded is not None
    assert loaded.storage_key == "sha256_deadbeef"
    assert loaded.storage_kind == "fingerprint_only"
    payload_text = json.dumps(loaded.safe_payload(), sort_keys=True)
    assert "/private/tmp" not in payload_text
    assert "profile.txt" not in payload_text


def test_postgres_history_store_counts_and_loads_summary_payloads():
    load_rows = [
        (
            json.dumps({"query_id": "query-new", "profile_status": "stale"}),
            PROFILE_STATUS_PROCESSING,
        ),
        ({"query_id": "query-old"}, PROFILE_STATUS_ANALYZED),
        ("not-json", PROFILE_STATUS_FAILED),
    ]
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        if len(connections) == 1:
            connection = FakeConnection(rows=[(2,)])
        elif len(connections) == 2:
            connection = FakeConnection(rows=load_rows)
        else:
            connection = FakeConnection()
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    assert store.count_summaries() == 2
    assert store.load_payloads() == [
        {"query_id": "query-new", "profile_status": PROFILE_STATUS_PROCESSING},
        {"query_id": "query-old", "profile_status": PROFILE_STATUS_ANALYZED},
    ]
    count_statement, _ = connections[1].cursor_obj.execute_calls[0]
    load_statement, _ = connections[2].cursor_obj.execute_calls[0]
    assert "SELECT COUNT(*) FROM recent_query_summary" in count_statement
    assert "profile_status" in load_statement
    assert "COALESCE(end_time, start_time, recorded_at_iso) DESC" in load_statement


def test_postgres_history_store_loads_materialized_payloads_raw_free():
    load_rows = [
        (
            {"query_id": "query-materialized", "profile_status": "stale"},
            PROFILE_STATUS_ANALYZED,
            {
                "score": 72,
                "score_severity": "high",
                "raw_sql": "SELECT secret_column FROM sensitive_table",
            },
        )
    ]
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection(rows=load_rows if len(connections) == 1 else [])
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    assert store.load_materialized_payloads(limit=500) == [
        {
            "query_id": "query-materialized",
            "profile_status": PROFILE_STATUS_ANALYZED,
            "analysis_cache_payload": {
                "score": 72,
                "score_severity": "high",
            },
        }
    ]
    statement, params = connections[1].cursor_obj.execute_calls[0]
    assert statement == POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT
    assert params == {
        "artifact_contract": "profile_artifact_v1",
        "artifact_status": "available",
        "analyzer_contract": "profile_digest_analysis_json_v1",
        "analysis_status": "ready",
        "analyzed_profile_status": "analyzed",
        "details_ready_only": False,
        "limit": 500,
    }
    assert "WITH newest_summary_keys AS" in statement
    assert "LIMIT %(limit)s" in statement


def test_postgres_details_ready_read_materializes_latest_artifacts_once():
    connections: list[FakeConnection] = []

    class DetailsReadyCursor(FakeCursor):
        def execute(self, statement, params=None):
            super().execute(statement, params)
            if statement == "SELECT COUNT(*) FROM recent_query_summary":
                self.rows = [(233_036,)]
            else:
                self.rows = []

    class DetailsReadyConnection(FakeConnection):
        def __init__(self):
            self.cursor_obj = DetailsReadyCursor()

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = DetailsReadyConnection()
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    assert store.load_materialized_payloads_with_count(
        limit=500,
        prepare_schema=False,
        details_ready_only=True,
    ) == ([], 233_036)

    statement, params = connections[0].cursor_obj.execute_calls[0]
    assert statement == POSTGRES_RECENT_DETAILS_READY_PAYLOADS_SELECT
    assert "WITH latest_available_artifacts AS" in statement
    assert "DISTINCT ON" in statement
    assert "SELECT candidate.profile_fingerprint" not in statement
    assert params["details_ready_only"] is True
    assert params["limit"] == 500


def test_postgres_history_store_loads_materialized_payloads_and_count_together():
    load_rows = [
        (
            {"query_id": "query-materialized"},
            PROFILE_STATUS_ANALYZED,
            {"score": 72},
        )
    ]
    connections: list[FakeConnection] = []

    class SnapshotCursor(FakeCursor):
        def execute(self, statement, params=None):
            super().execute(statement, params)
            if statement == POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT:
                self.rows = load_rows
            elif statement == "SELECT COUNT(*) FROM recent_query_summary":
                self.rows = [(233_036,)]

    class SnapshotConnection(FakeConnection):
        def __init__(self):
            self.cursor_obj = SnapshotCursor()

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = SnapshotConnection()
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    payloads, retained_count = store.load_materialized_payloads_with_count(limit=500)

    assert payloads == [
        {
            "query_id": "query-materialized",
            "profile_status": PROFILE_STATUS_ANALYZED,
            "analysis_cache_payload": {"score": 72},
        }
    ]
    assert retained_count == 233_036
    assert len(connections) == 1
    statements = connections[0].cursor_obj.executed
    assert statements[: len(POSTGRES_RECENT_QUERY_SUMMARY_DDL)] == list(
        POSTGRES_RECENT_QUERY_SUMMARY_DDL
    )
    assert statements[-2:] == [
        POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT,
        "SELECT COUNT(*) FROM recent_query_summary",
    ]


def test_postgres_online_history_reads_do_not_prepare_schema():
    connections: list[FakeConnection] = []

    class ReadOnlyCursor(FakeCursor):
        def execute(self, statement, params=None):
            super().execute(statement, params)
            if statement == "SELECT COUNT(*) FROM recent_query_summary":
                self.rows = [(233_036,)]
            elif statement == POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH:
                self.rows = [(2, 1, 3, 1, 4)]
            else:
                self.rows = []

    class ReadOnlyConnection(FakeConnection):
        def __init__(self):
            self.cursor_obj = ReadOnlyCursor()

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = ReadOnlyConnection()
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)

    payloads, retained_count = store.load_materialized_payloads_with_count(
        limit=500,
        prepare_schema=False,
    )
    health = store.summarize_profile_backlog_health(
        now_iso="2026-07-03T10:30:00+00:00",
        prepare_schema=False,
    )

    assert payloads == []
    assert retained_count == 233_036
    assert health.safe_payload() == {
        "pending_jobs": 2,
        "retry_pending_jobs": 1,
        "leased_jobs": 3,
        "stale_leased_jobs": 1,
        "failed_jobs": 4,
    }
    statements = [
        statement for connection in connections for statement in connection.cursor_obj.executed
    ]
    assert statements == [
        POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT,
        "SELECT COUNT(*) FROM recent_query_summary",
        POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH,
    ]


def test_postgres_history_store_prunes_history_with_terminal_job_guard():
    connections: list[FakeConnection] = []

    def connect(dsn):
        assert dsn == "postgresql://query-doctor-history"
        connection = FakeConnection(rowcount=[2, 3, 4, 5] if len(connections) == 1 else 0)
        connections.append(connection)
        return connection

    store = PostgresRecentHistoryStore("postgresql://query-doctor-history", connect=connect)
    result = store.prune_history(
        policy=RecentHistoryRetentionPolicy(
            summary_cutoff_iso="2026-07-02T00:00:00+00:00",
            profile_job_cutoff_iso="2026-07-02T01:00:00+00:00",
            analysis_cache_cutoff_iso="2026-07-02T02:00:00+00:00",
            profile_artifact_cutoff_iso="2026-07-02T03:00:00+00:00",
        )
    )

    assert result.summaries_deleted == 2
    assert result.profile_jobs_deleted == 3
    assert result.analysis_cache_deleted == 4
    assert result.profile_artifacts_deleted == 5
    assert result.safe_payload() == {
        "summaries_deleted": 2,
        "profile_jobs_deleted": 3,
        "analysis_cache_deleted": 4,
        "profile_artifacts_deleted": 5,
        "total_deleted": 14,
    }
    prune_cursor = connections[1].cursor_obj
    statements = [statement for statement, _params in prune_cursor.execute_calls]
    assert statements == [
        POSTGRES_RECENT_QUERY_SUMMARY_PRUNE,
        POSTGRES_RECENT_PROFILE_JOB_PRUNE,
        POSTGRES_RECENT_ANALYSIS_CACHE_PRUNE,
        POSTGRES_RECENT_PROFILE_ARTIFACT_PRUNE,
    ]
    assert prune_cursor.execute_calls[0][1] == {"cutoff_iso": "2026-07-02T00:00:00+00:00"}
    assert prune_cursor.execute_calls[1][1] == {
        "cutoff_iso": "2026-07-02T01:00:00+00:00",
        "completed_status": PROFILE_JOB_STATUS_COMPLETED,
        "failed_status": PROFILE_JOB_STATUS_FAILED,
    }
    assert prune_cursor.execute_calls[2][1] == {"cutoff_iso": "2026-07-02T02:00:00+00:00"}
    assert prune_cursor.execute_calls[3][1] == {"cutoff_iso": "2026-07-02T03:00:00+00:00"}


def test_postgres_history_store_requires_dsn_from_env():
    with pytest.raises(RecentHistoryStoreError):
        PostgresRecentHistoryStore.from_env(
            "QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN",
            env={},
        )


def test_postgres_row_uses_json_strings_and_boolean_values():
    record = history_record_from_candidate(
        RecentQueryCandidate(
            summary=CMQuerySummary(query_id="query-1", status="failed"),
            selected=False,
            reason="excluded",
            sql_verb=None,
        ),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )

    row = record_to_postgres_row(record)

    assert row["selected"] is False
    assert row["statement_present"] is False
    assert json.loads(str(row["suspicion_reasons_json"])) == ["failed_or_error_status"]
    assert json.loads(str(row["payload_json"]))["query_id"] == "query-1"


def test_postgres_profile_job_row_uses_json_strings():
    record = history_record_from_candidate(
        RecentQueryCandidate(
            summary=CMQuerySummary(query_id="query-1", status="failed"),
            selected=False,
            reason="excluded",
            sql_verb=None,
        ),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    jobs = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )

    row = profile_job_to_postgres_row(jobs[0])

    assert row["status"] == "pending"
    assert json.loads(str(row["priority_reasons_json"])) == ["failed_or_error_status"]
