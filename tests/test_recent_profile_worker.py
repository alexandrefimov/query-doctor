import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from query_doctor.cli import batch_recent
from query_doctor.cli import recent_profile_worker as cli
from query_doctor.recent.history_store import recent_history_source_key
from query_doctor.recent.profile_budget import (
    PROFILE_JOB_STATUS_AGED_OUT,
    PROFILE_JOB_STATUS_FAILED,
    PROFILE_JOB_STATUS_PENDING,
    PROFILE_STATUS_ANALYZED,
    PROFILE_STATUS_RETRY_PENDING,
    PROFILE_STATUS_FAILED,
    ProfileBudgetPolicy,
    RecentProfileJobRecord,
    plan_recent_profile_jobs,
)
from query_doctor.recent.profile_worker import (
    RECENT_PROFILE_WORKER_SUMMARY_KIND,
    RecentProfileWorkerJobOutcome,
    RecentProfileWorkerOptions,
    run_recent_profile_worker,
)
from query_doctor.recent.profile_worker_processor import (
    cleanup_worker_case_dir,
    process_recent_profile_job,
)
from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore


def auth_env() -> dict[str, str]:
    return {"CM_PASSWORD": "secret", "CM_USERNAME": "user"}


def cm_config(tmp_path, history_db):
    args = batch_recent.parse_args(
        [
            "--out",
            str(tmp_path / "query-doctor-worker-out"),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ]
    )
    return batch_recent.build_batch_config(
        args,
        env=auth_env(),
        cwd=tmp_path,
        repo_root=batch_recent.REPO_DIR,
    )


def profile_history_record(query_id: str, *, source_key: str = "cm:cluster:impala"):
    from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
    from query_doctor.recent.history_store import history_record_from_candidate

    return history_record_from_candidate(
        RecentQueryCandidate(
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
        ),
        engine="impala",
        source_kind="cm",
        source_key=source_key,
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )


def profile_job(query_id: str, *, source_key: str = "cm:cluster:impala") -> RecentProfileJobRecord:
    record = profile_history_record(query_id, source_key=source_key)
    return plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )[0]


def enqueue_history_profile_job(
    store: SqliteRecentHistoryStore,
    config,
    query_id: str,
) -> RecentProfileJobRecord:
    record = profile_history_record(query_id, source_key=recent_history_source_key(config))
    [job] = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )
    store.upsert_summaries([record])
    store.enqueue_profile_jobs([job])
    return job


def test_recent_profile_worker_claims_matching_source_and_stores_raw_free_cache(tmp_path):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    store = SqliteRecentHistoryStore(history_db)
    matching = profile_job("query-match", source_key=recent_history_source_key(config))
    other_source = profile_job("query-other", source_key="cm:other:impala")
    store.enqueue_profile_jobs([matching, other_source])

    def processor(job, _config, _env, _repo_root):
        assert job.query_id == "query-match"
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_deadbeef",
            analysis_payload={
                "diagnosis_status": "ok",
                "raw_sql": "SELECT secret_column FROM sensitive_table",
                "case_dir": "/private/tmp/query-doctor-secret",
            },
            artifact_storage_key="sha256_deadbeef",
            artifact_size_bytes=4096,
        )

    result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=5, lease_owner="worker-A"),
        now=datetime(2026, 7, 3, 10, 10, tzinfo=timezone.utc),
        processor=processor,
    )

    assert result.safe_payload()["summary_kind"] == RECENT_PROFILE_WORKER_SUMMARY_KIND
    assert result.jobs_claimed == 1
    assert result.jobs_completed == 1
    assert result.analysis_cache_records == 1
    assert result.profile_artifact_records == 1
    rows = {str(row["query_id"]): row for row in store.load_profile_jobs()}
    assert rows["query-match"]["status"] == "completed"
    assert rows["query-other"]["status"] == PROFILE_JOB_STATUS_PENDING
    loaded_cache = store.load_analysis_cache_record(
        engine="impala",
        source_kind="cm",
        source_key=recent_history_source_key(config),
        query_id="query-match",
        profile_fingerprint="sha256_deadbeef",
        analyzer_contract="profile_digest_analysis_json_v1",
    )
    assert loaded_cache is not None
    assert dict(loaded_cache.payload) == {"diagnosis_status": "ok"}
    loaded_artifact = store.load_profile_artifact_record(
        engine="impala",
        source_kind="cm",
        source_key=recent_history_source_key(config),
        query_id="query-match",
        profile_fingerprint="sha256_deadbeef",
        artifact_contract="profile_artifact_v1",
    )
    assert loaded_artifact is not None
    assert loaded_artifact.storage_key == "sha256_deadbeef"
    serialized = json.dumps(result.safe_payload(), sort_keys=True)
    assert "query-match" not in serialized
    assert "SELECT secret_column" not in serialized
    assert "/private/tmp" not in serialized
    assert "secret" not in json.dumps(loaded_cache.safe_payload(), sort_keys=True)


def test_recent_profile_worker_exhausts_retry_budget_without_raw_error(tmp_path):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    store = SqliteRecentHistoryStore(history_db)
    job = replace(profile_job("query-retry"), attempts=2)
    store.enqueue_profile_jobs([job])

    def processor(_job, _config, _env, _repo_root):
        return RecentProfileWorkerJobOutcome(
            status="retry",
            retry=True,
            error_code="profile-fetch-http-503",
        )

    result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, max_attempts=3),
        now=datetime(2026, 7, 3, 10, 10, tzinfo=timezone.utc),
        processor=processor,
    )

    assert result.jobs_retried == 0
    assert result.jobs_failed == 1
    assert result.safe_payload()["next_step"] == (
        "Review normalized worker error codes, fix collection or materialization "
        "settings, then requeue according to operator policy."
    )
    rows = store.load_profile_jobs()
    assert rows[0]["status"] == PROFILE_JOB_STATUS_FAILED
    assert rows[0]["last_error_code"] == "profile_fetch_http_503_retry_exhausted"
    assert result.safe_payload()["observed_at_iso"] == "2026-07-03T10:10:00+00:00"
    assert "profile-fetch-http-503" not in json.dumps(result.safe_payload(), sort_keys=True)


def test_recent_profile_worker_recovers_retry_into_materialized_history_row(tmp_path):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    store = SqliteRecentHistoryStore(history_db)
    enqueue_history_profile_job(store, config, "query-recover")

    def retry_processor(_job, _config, _env, _repo_root):
        return RecentProfileWorkerJobOutcome(
            status="retry",
            retry=True,
            error_code="profile-fetch-http-503",
        )

    retry_result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, lease_owner="worker-A"),
        now=datetime(2026, 7, 3, 10, 10, tzinfo=timezone.utc),
        processor=retry_processor,
    )

    assert retry_result.jobs_claimed == 1
    assert retry_result.jobs_retried == 1
    retry_summary = retry_result.safe_payload()
    assert retry_summary["profile_backlog_health"] == {
        "pending_jobs": 0,
        "retry_pending_jobs": 1,
        "leased_jobs": 0,
        "stale_leased_jobs": 0,
        "failed_jobs": 0,
        "window_hours": 12,
        "window_completed_jobs": 0,
        "window_failed_jobs": 0,
        "window_not_found_jobs": 0,
    }
    assert retry_summary["profile_backlog_next_step"] == (
        "Let the profile worker retry pending rows; investigate repeated normalized "
        "error codes if retry backlog persists."
    )
    [retry_row] = store.load_profile_jobs()
    assert retry_row["status"] == PROFILE_JOB_STATUS_PENDING
    assert retry_row["attempts"] == 1
    assert retry_row["last_error_code"] == "profile_fetch_http_503"
    [retry_payload] = store.load_payloads()
    assert retry_payload["profile_status"] == PROFILE_STATUS_RETRY_PENDING
    assert retry_payload["profile_last_error_code"] == "profile_fetch_http_503"

    def success_processor(_job, _config, _env, _repo_root):
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_recovered_profile",
            analysis_payload={
                "score": 88,
                "score_severity": "high",
                "score_reasons": ["runtime signal"],
                "analysis_status": "ok",
                "collection_status": "ok",
                "raw_sql": "SELECT secret_column FROM sensitive_table",
                "case_dir": "/private/tmp/query-doctor-secret",
            },
            artifact_storage_key="sha256_recovered_profile",
            artifact_size_bytes=4096,
        )

    success_result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, lease_owner="worker-B"),
        now=datetime(2026, 7, 3, 10, 30, tzinfo=timezone.utc),
        processor=success_processor,
    )

    assert success_result.jobs_claimed == 1
    assert success_result.jobs_completed == 1
    assert success_result.analysis_cache_records == 1
    assert success_result.profile_artifact_records == 1
    assert success_result.safe_payload()["profile_backlog_health"] == {
        "pending_jobs": 0,
        "retry_pending_jobs": 0,
        "leased_jobs": 0,
        "stale_leased_jobs": 0,
        "failed_jobs": 0,
        "window_hours": 12,
        "window_completed_jobs": 1,
        "window_failed_jobs": 0,
        "window_not_found_jobs": 0,
    }
    [completed_row] = store.load_profile_jobs()
    assert completed_row["status"] == "completed"
    assert completed_row["attempts"] == 2
    assert completed_row["last_error_code"] is None
    [payload] = store.load_materialized_payloads()
    assert payload["profile_status"] == PROFILE_STATUS_ANALYZED
    assert "profile_last_error_code" not in payload
    assert payload["analysis_cache_payload"] == {
        "score": 88,
        "score_severity": "high",
        "score_reasons": ["runtime signal"],
        "analysis_status": "ok",
        "collection_status": "ok",
    }
    rendered = json.dumps(payload, sort_keys=True)
    assert "profile_fetch_http_503" not in rendered
    assert "SELECT secret_column" not in rendered
    assert "/private/tmp" not in rendered
    assert "sha256_recovered_profile" not in rendered


def test_recent_profile_worker_requires_analysis_cache_for_completed_job(tmp_path):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    store = SqliteRecentHistoryStore(history_db)
    enqueue_history_profile_job(store, config, "query-missing-cache")

    def processor(_job, _config, _env, _repo_root):
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_missing_cache",
            artifact_storage_key="sha256_missing_cache",
            artifact_size_bytes=4096,
        )

    result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1),
        processor=processor,
    )

    assert result.jobs_completed == 0
    assert result.jobs_failed == 1
    assert result.analysis_cache_records == 0
    assert result.profile_artifact_records == 0
    [row] = store.load_profile_jobs()
    assert row["status"] == PROFILE_JOB_STATUS_FAILED
    assert row["last_error_code"] == "recent_profile_worker_missing_analysis_cache"
    [payload] = store.load_payloads()
    assert payload["profile_status"] == PROFILE_STATUS_FAILED
    assert store.load_materialized_payloads()[0].get("analysis_cache_payload") is None


def test_recent_profile_worker_rejects_unsafe_artifact_metadata_before_analyzed(tmp_path):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    store = SqliteRecentHistoryStore(history_db)
    enqueue_history_profile_job(store, config, "query-unsafe-artifact")

    def processor(_job, _config, _env, _repo_root):
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_unsafe_artifact",
            analysis_payload={"analysis_status": "ok", "collection_status": "ok"},
            artifact_storage_key="/private/tmp/query-doctor-secret/profile.txt",
            artifact_size_bytes=4096,
        )

    result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1),
        processor=processor,
    )

    assert result.jobs_completed == 0
    assert result.jobs_failed == 1
    assert result.analysis_cache_records == 1
    assert result.profile_artifact_records == 0
    [row] = store.load_profile_jobs()
    assert row["status"] == PROFILE_JOB_STATUS_FAILED
    assert row["last_error_code"] == "recent_profile_worker_artifact_metadata_rejected"
    [payload] = store.load_payloads()
    assert payload["profile_status"] == PROFILE_STATUS_FAILED
    materialized = store.load_materialized_payloads()[0]
    assert materialized.get("analysis_cache_payload") is None
    assert "/private/tmp" not in json.dumps(result.safe_payload(), sort_keys=True)


def test_recent_profile_worker_requires_artifact_metadata_for_completed_job(tmp_path):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    store = SqliteRecentHistoryStore(history_db)
    enqueue_history_profile_job(store, config, "query-missing-artifact")

    def processor(_job, _config, _env, _repo_root):
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_missing_artifact",
            analysis_payload={"analysis_status": "ok", "collection_status": "ok"},
        )

    result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1),
        processor=processor,
    )

    assert result.jobs_completed == 0
    assert result.jobs_failed == 1
    assert result.analysis_cache_records == 0
    assert result.profile_artifact_records == 0
    [row] = store.load_profile_jobs()
    assert row["status"] == PROFILE_JOB_STATUS_FAILED
    assert row["last_error_code"] == "recent_profile_worker_missing_artifact_metadata"
    [payload] = store.load_payloads()
    assert payload["profile_status"] == PROFILE_STATUS_FAILED
    assert store.load_materialized_payloads()[0].get("analysis_cache_payload") is None


def test_recent_profile_worker_default_processor_runs_analysis_only_without_raw_payload(
    tmp_path,
    monkeypatch,
):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    job = profile_job("query-process", source_key=recent_history_source_key(config))
    calls: dict[str, object] = {}

    def fake_collect(_config, case, *, env, repo_root, collect_cm_timeseries):
        calls["collect_cm_timeseries"] = collect_cm_timeseries
        case.collection_status = "ok"
        # Collection extracts these from the statement, before redaction.
        case.metadata_source_tables = ("marts.orders",)
        case.actual_case_dir = case.wrapper_dir / "collected-case"
        case.actual_case_dir.mkdir(parents=True)
        (case.actual_case_dir / "profile_digest.md").write_text("# digest\n", encoding="utf-8")

    def fake_analysis(_config, case, *, env, repo_root):
        # No metadata_mode argument: the worker must not override the config.
        calls["analysis_source_tables"] = env.get("QD_METADATA_SOURCE_TABLES_JSON")
        case.analysis_status = "ok"

    def fake_summary(case):
        return {
            "collection_status": case.collection_status,
            "analysis_status": case.analysis_status,
            "metadata_status": "not_collected",
            "referenced_table_count": 3,
            "collectable_metadata_table_count": 2,
            "collected_metadata_table_count": 0,
            "too_large_count": 1,
            "score": 10,
            "score_reasons": ["duration_ge_1h"],
            "cm_collect_seconds": 1.25,
            "analysis_seconds": 0.75,
            "total_seconds": 2.0,
            "case_dir": str(case.wrapper_dir),
            "raw_sql": "SELECT secret_column FROM sensitive_table",
        }

    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.collect_case_profile",
        fake_collect,
    )
    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.run_analysis_pass",
        fake_analysis,
    )
    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.score_case",
        lambda case: None,
    )
    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.case_to_summary",
        fake_summary,
    )

    outcome = process_recent_profile_job(job, config, auth_env(), batch_recent.REPO_DIR)

    assert outcome.status == "completed"
    assert calls == {
        "collect_cm_timeseries": False,
        "analysis_source_tables": '["marts.orders"]',
    }
    assert outcome.profile_fingerprint is not None
    assert outcome.profile_fingerprint.startswith("sha256_")
    assert outcome.artifact_storage_key == outcome.profile_fingerprint
    assert outcome.analysis_payload == {
        "analysis_status": "ok",
        "collection_status": "ok",
        "metadata_status": "not_collected",
        "referenced_table_count": 3,
        "collectable_metadata_table_count": 2,
        "collected_metadata_table_count": 0,
        "too_large_count": 1,
        "score": 10,
        "score_reasons": ["duration_ge_1h"],
        "cm_collect_seconds": 1.25,
        "analysis_seconds": 0.75,
        "total_seconds": 2.0,
        "case_artifact_contract": "profile_digest_analysis_json_v1",
    }
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []


def test_recent_profile_worker_default_processor_cleans_temp_case_after_collection_retry(
    tmp_path,
    monkeypatch,
):
    history_db = tmp_path / "recent.sqlite"
    config = cm_config(tmp_path, history_db)
    job = profile_job("query-process-retry", source_key=recent_history_source_key(config))

    def fake_collect(_config, case, *, env, repo_root, collect_cm_timeseries):
        case.wrapper_dir.mkdir(parents=True)
        (case.wrapper_dir / "profile.err").write_text(
            "unsafe local failure detail", encoding="utf-8"
        )
        case.collection_status = "failed"
        case.failure_category = "profile_collection_timeout"

    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.collect_case_profile",
        fake_collect,
    )

    outcome = process_recent_profile_job(job, config, auth_env(), batch_recent.REPO_DIR)

    assert outcome.status == "retry"
    assert outcome.retry is True
    assert outcome.error_code == "profile_collection_timeout"
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []


@pytest.mark.parametrize("source", ["Cloudera Manager", "Impala profile endpoint"])
@pytest.mark.parametrize("http_status", [401, 403, 404, 429, 500, 503, 599])
def test_recent_profile_worker_default_processor_retains_fixed_http_code(
    tmp_path, monkeypatch, source, http_status
):
    config = cm_config(tmp_path, tmp_path / "recent.sqlite")
    job = profile_job("query-http", source_key=recent_history_source_key(config))

    def fake_collect(_config, case, **_kwargs):
        case.collection_status = "failed"
        case.failure_category = "profile_collection_failed"
        case.failure_reason = f"{source} profile collection returned HTTP {http_status}."

    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.collect_case_profile", fake_collect
    )
    outcome = process_recent_profile_job(job, config, auth_env(), batch_recent.REPO_DIR)

    assert outcome.error_code == f"profile_fetch_http_{http_status}"
    # Retaining the code must not change retries, including for client errors.
    assert outcome.status == "retry"
    assert outcome.retry is True
    assert outcome.analysis_payload is None


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "Profile collection command failed before a profile digest was produced.",
        "HTTP Error 403: SENSITIVE_SENTINEL",
        "Cloudera Manager profile collection returned HTTP 403. SENSITIVE_SENTINEL",
        "Cloudera Manager profile collection returned HTTP 403 SENSITIVE_SENTINEL.",
        "Cloudera Manager profile collection returned HTTP 600.",
        "Cloudera Manager profile collection returned HTTP 099.",
        "Cloudera Manager profile collection returned HTTP ４０３.",
        "Cloudera Manager profile collection returned HTTP " + "4" * 100_000 + ".",
        "Impala profile endpoint profile collection timed out. SENSITIVE_SENTINEL",
        "SENSITIVE_SENTINEL Impala profile endpoint profile collection timed out.",
        "Impala profile endpoint request timed out safely.",
    ],
    ids=[
        "missing",
        "generic",
        "raw",
        "suffix",
        "embedded",
        "invalid-high",
        "invalid-low",
        "unicode",
        "oversized",
        "timeout-suffix",
        "timeout-prefix",
        "noncanonical-timeout",
    ],
)
def test_recent_profile_worker_default_processor_rejects_noncanonical_fetch_reason(
    tmp_path, monkeypatch, reason
):
    config = cm_config(tmp_path, tmp_path / "recent.sqlite")
    job = profile_job("query-http", source_key=recent_history_source_key(config))

    def fake_collect(_config, case, **_kwargs):
        case.collection_status = "failed"
        case.failure_category = "profile_collection_failed"
        case.failure_reason = reason

    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.collect_case_profile", fake_collect
    )
    outcome = process_recent_profile_job(job, config, auth_env(), batch_recent.REPO_DIR)

    assert outcome.error_code == "profile_collection_failed"
    assert outcome.retry is True


@pytest.mark.parametrize(
    "stderr",
    [
        "TimeoutError: SENSITIVE_SENTINEL",
        "Impala profile endpoint request timed out safely.",
        "Single-query Impala profile collection failed: Last safe error: "
        "Impala profile endpoint request timed out safely. SENSITIVE_SENTINEL",
        "SENSITIVE_SENTINEL Single-query Impala profile collection failed: Last safe error: "
        "Impala profile endpoint request timed out safely.",
    ],
)
def test_direct_impala_noncanonical_terminal_timeout_keeps_generic_reason(tmp_path, stderr):
    import subprocess

    from query_doctor.recent.case_processing import profile_collection_failure_reason

    config = replace(cm_config(tmp_path, tmp_path / "recent.sqlite"), query_profile_source="impala")
    result = subprocess.CompletedProcess([], 4, "", stderr)
    assert profile_collection_failure_reason(config, result) == (
        "Profile collection command failed before a profile digest was produced."
    )


@pytest.mark.parametrize(
    "category,expected_code,retry",
    [
        ("profile_collection_timeout", "profile_collection_timeout", True),
        ("profile_collection_skipped", "profile_collection_skipped", False),
        ("profile_digest_missing", "profile_digest_missing", False),
        (None, "recent_profile_worker_collection_failed", False),
    ],
)
def test_recent_profile_worker_http_reason_does_not_override_other_categories(
    tmp_path, monkeypatch, category, expected_code, retry
):
    config = cm_config(tmp_path, tmp_path / "recent.sqlite")
    job = profile_job("query-http", source_key=recent_history_source_key(config))

    def fake_collect(_config, case, **_kwargs):
        case.collection_status = "failed"
        case.failure_category = category
        case.failure_reason = "Cloudera Manager profile collection returned HTTP 503."

    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.collect_case_profile", fake_collect
    )
    outcome = process_recent_profile_job(job, config, auth_env(), batch_recent.REPO_DIR)

    assert outcome.error_code == expected_code
    assert outcome.retry is retry
    assert outcome.status == ("retry" if retry else "failed")


@pytest.mark.parametrize("attempts", [0, 2])
@pytest.mark.parametrize("http_status", [403, 503])
def test_recent_profile_worker_retains_http_code_through_retry_and_persistence(
    tmp_path, monkeypatch, attempts, http_status
):
    import subprocess

    config = cm_config(tmp_path, tmp_path / "recent.sqlite")
    store = SqliteRecentHistoryStore(config.recent_history_db)
    job = replace(
        profile_job("query-http", source_key=recent_history_source_key(config)),
        attempts=attempts,
    )
    store.upsert_summaries([profile_history_record("query-http", source_key=job.source_key)])
    store.enqueue_profile_jobs([job])
    monkeypatch.setattr(
        "query_doctor.recent.case_processing.run_profile_collection_subprocess",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=f"HTTP Error {http_status}: SENSITIVE_SENTINEL",
        ),
    )
    events = []
    result = run_recent_profile_worker(
        store=store,
        config=config,
        env=auth_env(),
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, max_attempts=3),
        progress=SimpleNamespace(emit=lambda **event: events.append(event)),
    )

    exhausted = attempts == 2
    expected_code = f"profile_fetch_http_{http_status}"
    if exhausted:
        expected_code += "_retry_exhausted"
    [row] = store.load_profile_jobs()
    [payload] = store.load_payloads()
    assert row["last_error_code"] == expected_code
    assert payload["profile_last_error_code"] == expected_code
    assert row["attempts"] == attempts + 1
    expected_status = PROFILE_JOB_STATUS_FAILED if exhausted else PROFILE_JOB_STATUS_PENDING
    assert row["status"] == expected_status
    assert result.jobs_failed == int(exhausted)
    assert result.jobs_retried == int(not exhausted)
    assert result.profile_backlog_health.failed_jobs == int(exhausted)
    assert any(event.get("error_code") == expected_code for event in events)
    assert "SENSITIVE_SENTINEL" not in json.dumps([payload, events, result.safe_payload()])
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []


@pytest.mark.parametrize("attempts", [0, 2])
@pytest.mark.parametrize("prefer_json", [False, True])
@pytest.mark.parametrize(
    "failure", [403, 404, 503, 600, "timeout", "wrapped_timeout", "read_timeout", "transport"]
)
def test_direct_impala_native_failure_reaches_worker_persistence(
    tmp_path, monkeypatch, attempts, prefer_json, failure
):
    import io
    import subprocess
    import urllib.error
    from contextlib import redirect_stderr, redirect_stdout

    from query_doctor.cli import collect_impala_profile

    config = replace(
        cm_config(tmp_path, tmp_path / "recent.sqlite"),
        query_profile_source="impala",
        impala_profile_hosts=("coordinator.example.com",),
        impala_profile_prefer_json=prefer_json,
    )
    record = replace(
        profile_history_record("abc:def", source_key=recent_history_source_key(config)),
        source_kind="impala",
    )
    [job] = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )
    store = SqliteRecentHistoryStore(config.recent_history_db)
    store.upsert_summaries([record])
    store.enqueue_profile_jobs([replace(job, attempts=attempts)])
    endpoints = []
    commands = []
    terminal_outputs = []

    def opener(request, timeout):
        endpoints.append(request.full_url)
        assert timeout == config.impala_profile_timeout_sec
        if isinstance(failure, int):
            raise urllib.error.HTTPError(request.full_url, failure, "SENSITIVE_SENTINEL", {}, None)
        if failure == "timeout":
            raise TimeoutError("SENSITIVE_SENTINEL")
        if failure == "wrapped_timeout":
            raise urllib.error.URLError(TimeoutError("SENSITIVE_SENTINEL"))
        if failure == "read_timeout":
            from contextlib import nullcontext

            def read(_size):
                raise TimeoutError("SENSITIVE_SENTINEL")

            return nullcontext(SimpleNamespace(read=read))
        raise urllib.error.URLError("SENSITIVE_SENTINEL")

    def native_collector(cmd, *, cwd, env):
        commands.append(cmd)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = collect_impala_profile.main(cmd[cmd.index("--query-id") :], opener=opener)
        terminal_outputs.append(stdout.getvalue() + stderr.getvalue())
        return subprocess.CompletedProcess(cmd, rc, stdout.getvalue(), stderr.getvalue())

    monkeypatch.setattr("query_doctor.recent.case_processing.run_subprocess", native_collector)
    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.run_analysis_pass",
        lambda *_args, **_kwargs: pytest.fail("Failed collection must not run analysis"),
    )
    events = []
    result = run_recent_profile_worker(
        store=store,
        config=config,
        env={},
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, max_attempts=3),
        progress=SimpleNamespace(emit=lambda **event: events.append(event)),
    )

    expected_code = "profile_collection_failed"
    if isinstance(failure, int) and 100 <= failure <= 599:
        expected_code = f"profile_fetch_http_{failure}"
    elif failure in {"timeout", "wrapped_timeout", "read_timeout"}:
        expected_code = "profile_fetch_timeout"
    exhausted = attempts == 2
    if exhausted:
        expected_code += "_retry_exhausted"
    [row] = store.load_profile_jobs()
    [payload] = store.load_payloads()
    assert row["last_error_code"] == payload["profile_last_error_code"] == expected_code
    assert row["attempts"] == attempts + 1
    assert row["status"] == (PROFILE_JOB_STATUS_FAILED if exhausted else PROFILE_JOB_STATUS_PENDING)
    assert result.jobs_failed == int(exhausted)
    assert result.jobs_retried == int(not exhausted)
    assert any(event.get("error_code") == expected_code for event in events)
    wrapper_attempts = 1 if failure in {403, 404} else 2
    assert len(commands) == wrapper_attempts
    paths = ["format=json", "format=text", "default"] if prefer_json else ["format=text", "default"]
    observed_paths = [url.split("&")[-1] if "&" in url else "default" for url in endpoints]
    assert observed_paths == paths * wrapper_attempts
    safe_output = json.dumps([terminal_outputs, events, result.safe_payload()])
    for forbidden in ("SENSITIVE_SENTINEL", "coordinator.example.com", "abc:def", str(tmp_path)):
        assert forbidden not in safe_output
    assert "SENSITIVE_SENTINEL" not in json.dumps(payload)
    assert all("profile was not found" not in output for output in terminal_outputs)
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []


IMPALA_QUERY_NOT_FOUND_PAGE = (
    '<html><body><div class="alert alert-danger"><strong>Error:</strong>\n'
    "Query id 0000000000000000:0000000000000000 not found.\n</div>"
    '<pre id="plain_text_profile_field"></pre></body></html>'
)


class PageResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size: int) -> bytes:
        return self.body


@pytest.mark.parametrize("attempts", [0, 2])
@pytest.mark.parametrize("prefer_json", [False, True])
def test_direct_impala_profile_gone_from_the_query_log_ages_out_without_retry(
    tmp_path, monkeypatch, attempts, prefer_json
):
    import io
    import subprocess
    from contextlib import redirect_stderr, redirect_stdout

    from query_doctor.cli import collect_impala_profile

    config = replace(
        cm_config(tmp_path, tmp_path / "recent.sqlite"),
        query_profile_source="impala",
        impala_profile_hosts=("coordinator.example.com",),
        impala_profile_prefer_json=prefer_json,
    )
    record = replace(
        profile_history_record("abc:def", source_key=recent_history_source_key(config)),
        source_kind="impala",
    )
    [job] = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )
    store = SqliteRecentHistoryStore(config.recent_history_db)
    store.upsert_summaries([record])
    store.enqueue_profile_jobs([replace(job, attempts=attempts)])
    endpoints = []
    commands = []
    terminal_outputs = []

    def opener(request, timeout):
        endpoints.append(request.full_url)
        return PageResponse(IMPALA_QUERY_NOT_FOUND_PAGE)

    def native_collector(cmd, *, cwd, env):
        commands.append(cmd)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = collect_impala_profile.main(cmd[cmd.index("--query-id") :], opener=opener)
        terminal_outputs.append(stdout.getvalue() + stderr.getvalue())
        return subprocess.CompletedProcess(cmd, rc, stdout.getvalue(), stderr.getvalue())

    monkeypatch.setattr("query_doctor.recent.case_processing.run_subprocess", native_collector)
    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.run_analysis_pass",
        lambda *_args, **_kwargs: pytest.fail("A missing profile must not run analysis"),
    )
    result = run_recent_profile_worker(
        store=store,
        config=config,
        env={},
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, max_attempts=3),
    )

    [row] = store.load_profile_jobs()
    [payload] = store.load_payloads()
    # Terminal on the first attempt, with the collector run once: a retry
    # cannot bring back a profile the daemon has dropped. The job ages out
    # rather than fails, so readiness does not count it against the worker.
    assert row["status"] == PROFILE_JOB_STATUS_AGED_OUT
    assert row["last_error_code"] == payload["profile_last_error_code"] == "profile_not_found"
    assert payload["profile_status"] == PROFILE_STATUS_FAILED
    assert len(commands) == 1
    assert len(endpoints) == (3 if prefer_json else 2)
    assert row["attempts"] == attempts + 1
    assert (result.jobs_failed, result.jobs_retried, result.jobs_aged_out) == (0, 0, 1)
    health = result.safe_payload()["profile_backlog_health"]
    assert (health["window_failed_jobs"], health["window_not_found_jobs"]) == (0, 1)
    safe_output = json.dumps([terminal_outputs, result.safe_payload()])
    for forbidden in ("coordinator.example.com", "abc:def", "0000000000000000", str(tmp_path)):
        assert forbidden not in safe_output
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []


IMPALA_PARSE_EXCEPTION_PROFILE = (
    "Query (id=abc:def):\n"
    "  Summary:\n"
    "    Query Type: QUERY\n"
    "    Query State: EXCEPTION\n"
    "    Query Status: ParseException: Syntax error in line 1: select * frm secret_table\n"
    "    Sql Statement: select * frm secret_table\n"
)


def test_direct_impala_worker_records_error_class_from_the_profile_status(tmp_path, monkeypatch):
    import io
    import subprocess
    from contextlib import redirect_stderr, redirect_stdout

    from query_doctor.cli import collect_impala_profile

    config = replace(
        cm_config(tmp_path, tmp_path / "recent.sqlite"),
        query_profile_source="impala",
        impala_profile_hosts=("coordinator.example.com",),
    )
    # The daemon listing carries only the state, so the summary has no class.
    record = replace(
        profile_history_record("abc:def", source_key=recent_history_source_key(config)),
        source_kind="impala",
        status="exception",
    )
    assert record.error_class is None
    [job] = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )
    store = SqliteRecentHistoryStore(config.recent_history_db)
    store.upsert_summaries([record])
    store.enqueue_profile_jobs([job])

    def native_collector(cmd, *, cwd, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = collect_impala_profile.main(
                cmd[cmd.index("--query-id") :],
                opener=lambda _request, timeout: PageResponse(IMPALA_PARSE_EXCEPTION_PROFILE),
            )
        return subprocess.CompletedProcess(cmd, rc, stdout.getvalue(), stderr.getvalue())

    def failed_analysis(_config, case, *, env, repo_root):
        # A statement that never parsed has no plan to analyze; the class
        # must still reach the summary.
        case.analysis_status = "failed"
        case.failure_category = "analysis_failed"

    monkeypatch.setattr("query_doctor.recent.case_processing.run_subprocess", native_collector)
    monkeypatch.setattr(
        "query_doctor.recent.profile_worker_processor.run_analysis_pass", failed_analysis
    )
    result = run_recent_profile_worker(
        store=store,
        config=config,
        env={},
        repo_root=batch_recent.REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, max_attempts=1),
    )

    [payload] = store.load_payloads()
    assert payload["error_class"] == "ParseException"
    assert result.jobs_failed == 1
    assert "recent_profile_worker_error_class_failed" not in result.issue_codes
    # The next listing pass knows only the state and must not erase the class.
    store.upsert_summaries([record])
    [payload] = store.load_payloads()
    assert payload["error_class"] == "ParseException"
    safe_output = json.dumps([payload, result.safe_payload()])
    for raw in ("secret_table", "Syntax error", "frm"):
        assert raw not in safe_output
    assert list((config.out / "profile-worker-cases").glob("job-*")) == []


@pytest.mark.parametrize(
    "stderr",
    [
        "Impala profile collection failed on the configured impalad endpoints. "
        "Attempted endpoints: 2. Last safe error: profile was not found on any impalad endpoint.",
        "Single-query Impala profile collection failed: Last safe error: "
        "profile was not found on one impalad endpoint.",
        "Single-query Impala profile collection failed: Last safe error: "
        "profile was not found on any impalad endpoint. SENSITIVE_SENTINEL",
        "SENSITIVE_SENTINEL Single-query Impala profile collection failed: Last safe error: "
        "profile was not found on any impalad endpoint.",
    ],
    ids=["no-prefix", "one-endpoint", "suffix", "prefix"],
)
def test_direct_impala_noncanonical_not_found_stays_a_retryable_failure(tmp_path, stderr):
    import subprocess

    from query_doctor.recent.case_processing import (
        profile_collection_failure_is_retryable,
        profile_collection_failure_reason,
    )

    config = replace(cm_config(tmp_path, tmp_path / "recent.sqlite"), query_profile_source="impala")
    result = subprocess.CompletedProcess([], 4, "", stderr)
    assert profile_collection_failure_reason(config, result) == (
        "Profile collection command failed before a profile digest was produced."
    )
    assert profile_collection_failure_is_retryable(result) is True


def test_recent_profile_worker_cleanup_only_removes_owned_job_dirs(tmp_path):
    worker_root = tmp_path / "worker-root"
    worker_root.mkdir()
    owned_job = worker_root / "job-owned"
    owned_job.mkdir()
    (owned_job / "profile_digest.md").write_text("# digest\n", encoding="utf-8")
    non_job_dir = worker_root / "not-a-job"
    non_job_dir.mkdir()
    sibling_job = tmp_path / "job-sibling"
    sibling_job.mkdir()

    cleanup_worker_case_dir(worker_root, worker_root)
    cleanup_worker_case_dir(non_job_dir, worker_root)
    cleanup_worker_case_dir(sibling_job, worker_root)
    cleanup_worker_case_dir(owned_job, worker_root)

    assert worker_root.is_dir()
    assert non_job_dir.is_dir()
    assert sibling_job.is_dir()
    assert not owned_job.exists()


def test_recent_profile_worker_cli_json_no_jobs_is_raw_free(tmp_path, capsys):
    history_db = tmp_path / "recent.sqlite"
    summary_json = tmp_path / "worker-summary.json"

    rc = cli.main(
        [
            "--json",
            "--summary-json",
            str(summary_json),
            "--out",
            str(tmp_path / "query-doctor-worker-out"),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ],
        env=auth_env(),
    )

    assert rc == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert stdout_payload["summary_kind"] == RECENT_PROFILE_WORKER_SUMMARY_KIND
    assert stdout_payload["jobs_claimed"] == 0
    assert stdout_payload["next_step"] == (
        "No matching jobs were claimed; run a discover-only refresh or check backlog filters."
    )
    serialized = json.dumps(stdout_payload, sort_keys=True)
    assert "cm.example.net" not in serialized
    assert str(history_db) not in serialized
    assert "secret" not in serialized


def test_recent_profile_worker_cli_replaces_summary_atomically(tmp_path, capsys, monkeypatch):
    history_db = tmp_path / "recent.sqlite"
    summary_json = tmp_path / "worker-summary.json"
    replacements = []
    replace = cli.os.replace

    def record_replace(source, destination):
        replacements.append((source, destination))
        replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", record_replace)
    rc = cli.main(
        [
            "--json",
            "--summary-json",
            str(summary_json),
            "--out",
            str(tmp_path / "query-doctor-worker-out"),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ],
        env=auth_env(),
    )

    assert rc == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    assert json.loads(summary_json.read_text(encoding="utf-8")) == stdout_payload
    assert len(replacements) == 1
    temporary_path, destination = replacements[0]
    assert destination == summary_json
    assert temporary_path.parent == summary_json.parent
    assert not temporary_path.exists()


def test_recent_profile_worker_cli_preserves_summary_when_atomic_replace_fails(
    tmp_path, capsys, monkeypatch
):
    history_db = tmp_path / "recent.sqlite"
    summary_json = tmp_path / "worker-summary.json"
    previous_summary = '{"status":"done"}\n'
    summary_json.write_text(previous_summary, encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(cli.os, "replace", fail_replace)
    rc = cli.main(
        [
            "--summary-json",
            str(summary_json),
            "--out",
            str(tmp_path / "query-doctor-worker-out"),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ],
        env=auth_env(),
    )

    assert rc == 2
    assert summary_json.read_text(encoding="utf-8") == previous_summary
    assert not list(tmp_path.glob(".worker-summary.json.*.tmp"))
    assert "could not write summary JSON" in capsys.readouterr().err


def test_recent_profile_worker_cli_ignores_scan_only_selection_limit(tmp_path, capsys):
    history_db = tmp_path / "recent.sqlite"
    summary_json = tmp_path / "worker-summary.json"
    config_path = tmp_path / "query-doctor-config.json"
    config_path.write_text(
        json.dumps(
            {
                "out": str(tmp_path / "query-doctor-worker-out"),
                "recent_history_db": str(history_db),
                "recent_profile_analysis_limit": 5000,
                "clusters": [
                    {
                        "id": "cm",
                        "cm_url": "https://cm.example.net:7183",
                        "cluster": "cluster",
                        "service": "impala",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    rc = cli.main(
        [
            "--json",
            "--summary-json",
            str(summary_json),
            "--config",
            str(config_path),
            "--metadata-mode",
            "off",
        ],
        env=auth_env(),
    )

    assert rc == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert stdout_payload["status"] == "done"
    assert stdout_payload["jobs_claimed"] == 0
    serialized = json.dumps(stdout_payload, sort_keys=True)
    assert "cm.example.net" not in serialized
    assert "cluster" not in serialized
    assert str(history_db) not in serialized
