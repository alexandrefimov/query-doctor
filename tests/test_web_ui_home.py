import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
from query_doctor.recent.history_store import history_record_from_candidate
from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore
from query_doctor.web.recent_history_inbox import recent_history_summary_from_payloads
from query_doctor.web.ui.query_inbox import QueryInboxScopeFilters
from web_server_test_support import REPO_DIR, load_web_module
from query_doctor.web.ui import layout
from query_doctor.web.ui.recent_scan_results import render_batch_summary


def compact_css(css: str) -> str:
    return "".join(css.split())


def assert_css_contains(styles: str, snippet: str) -> None:
    assert compact_css(snippet) in compact_css(styles)


def _write_single_recent_history_row(history_db: Path) -> None:
    store = SqliteRecentHistoryStore(history_db)
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="operator-ready-query",
            start_time="2026-07-03T10:00:00Z",
            end_time="2026-07-03T10:10:00Z",
            duration_ms=600_000,
            status="FINISHED",
            user="analyst",
            pool="root.default",
            query_type="QUERY",
            statement="SELECT secret_column FROM private_table",
        ),
        selected=True,
        reason="selected: long-running summary",
        sql_verb="SELECT",
    )
    store.upsert_summaries(
        [
            history_record_from_candidate(
                candidate,
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                recorded_at_iso="2026-07-03T10:05:00+00:00",
            )
        ]
    )


def test_query_inbox_uses_online_recent_history_store(tmp_path, monkeypatch):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(history_db)
    records = []
    for index in range(1, 302):
        candidate = RecentQueryCandidate(
            summary=CMQuerySummary(
                query_id=f"query-{index:03d}",
                start_time=f"2026-07-{(index // 24) + 1:02d}T{index % 24:02d}:00:00Z",
                end_time=f"2026-07-{(index // 24) + 1:02d}T{index % 24:02d}:10:00Z",
                duration_ms=600_000 + index,
                status="FAILED",
                user="analyst",
                pool="root.default",
                query_type="QUERY",
                statement="SELECT secret_column FROM private_table",
            ),
            selected=index <= 10,
            reason="selected: long-running summary",
            sql_verb="SELECT",
        )
        records.append(
            history_record_from_candidate(
                candidate,
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                recorded_at_iso="2026-07-03T10:05:00+00:00",
            )
        )
    store.upsert_summaries(records)
    config = tmp_path / "web-config.json"
    config.write_text(
        '{"recent_history_backend":"sqlite","recent_history_db":"recent-history.sqlite"}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(
        module.WebSettings(config=config, repo_dir=REPO_DIR),
        history_view="all_recent",
    )

    assert "All recent queries" in body
    assert "Showing the newest retained summaries" in body
    assert "Online history" in body
    assert "Rows 1-250 of 301; page 1 of 2" in body
    assert "query-301" in body
    assert "query-052" in body
    assert 'data-href="/batch/case/' not in body
    assert "secret_column" not in body
    assert "private_table" not in body


def test_online_history_page_presents_shared_summary_once(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web.ui import query_inbox, recent_scan_results, recent_scan_view_cache

    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    config = tmp_path / "web-config.json"
    config.write_text(
        '{"recent_history_backend":"sqlite","recent_history_db":"recent-history.sqlite"}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    original_presenter = recent_scan_view_cache.present_recent_scan_summary
    call_count = 0

    def counted_presenter(summary_payload, *, workload_outcome_metrics=None):
        nonlocal call_count
        call_count += 1
        return original_presenter(
            summary_payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )

    for presenter_module in (
        recent_scan_view_cache,
        recent_scan_results,
        query_inbox,
    ):
        monkeypatch.setattr(
            presenter_module,
            "present_recent_scan_summary",
            counted_presenter,
            raising=False,
        )
    monkeypatch.setattr(
        recent_scan_results,
        "workload_outcome_metrics_by_fingerprint",
        lambda: {"unmatched-workload": object()},
    )

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert call_count == 1
    assert "No Details ready" in body

    module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert call_count == 2


def test_completed_online_history_job_reuses_outcome_enriched_summary_view(monkeypatch):
    module = load_web_module()
    from query_doctor.web.action_outcomes import (
        SCHEMA_VERSION,
        ActionOutcomeRecord,
        summarize_workload_action_outcomes,
    )
    from query_doctor.web.ui import recent_scan_results, recent_scan_view_cache

    workload_fingerprint = f"wf_{'a' * 24}"
    summary = {
        "mode": "recent-history-online",
        "cases": [
            {
                "case_index": 1,
                "query_id": "outcome-ready-query",
                "user": "analyst",
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "ok",
                "score": 10,
                "score_severity": "suspicious",
                "duration_sec": 10,
                "score_reasons": [],
                "workload_fingerprint": workload_fingerprint,
            }
        ],
        "selected_count": 1,
        "summaries_inspected": 1,
    }
    metrics = summarize_workload_action_outcomes(
        [
            ActionOutcomeRecord(
                schema_version=SCHEMA_VERSION,
                recorded_at_iso="2026-07-03T10:05:00+00:00",
                workload_fingerprint=workload_fingerprint,
                case_fingerprint=f"cf_{'b' * 24}",
                case_id_local="case-001",
                recommendation_id="query_optimization_review.v1",
                applied="yes",
                outcome="improved",
                verification_status="comparable_rerun",
            )
        ],
        min_applied=1,
    )
    monkeypatch.setattr(
        recent_scan_results,
        "workload_outcome_metrics_by_fingerprint",
        lambda: metrics,
    )
    original_presenter = recent_scan_view_cache.present_recent_scan_summary
    presenter_metric_flags = []
    presented_outcomes = []

    def counted_presenter(summary_payload, *, workload_outcome_metrics=None):
        presenter_metric_flags.append(bool(workload_outcome_metrics))
        view = original_presenter(
            summary_payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )
        presented_outcomes.append(view.rows[0].action_outcome_summary)
        return view

    monkeypatch.setattr(
        recent_scan_view_cache,
        "present_recent_scan_summary",
        counted_presenter,
    )
    job = module.WebJobSnapshot(
        job_id="0123456789abcdef0123456789abcdef",
        query_id="",
        report_mode="user",
        status="ok",
        stage_label="Complete",
        progress=100,
        kind="batch",
    )

    body = module.render_batch_page(
        module.WebSettings(
            config=Path(".query-doctor-cm.local.json"),
            corpus_summary=summary,
        ),
        job=job,
        query_group="all",
    )

    assert presenter_metric_flags == [True]
    assert len(presented_outcomes) == 1
    assert "1 recorded; 1 applied; 1 comparable reruns; improved 1" in presented_outcomes[0]
    assert "outcome-ready-query" in body


def test_query_inbox_online_history_shows_configured_collector_summary(tmp_path, monkeypatch):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    collector_summary = tmp_path / "collector-summary.json"
    collector_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_collector_v1",
                "status": "idle",
                "observed_at_iso": "2026-07-03T10:00:00+00:00",
                "discover_only": True,
                "history_backend": "sqlite",
                "summaries_inspected": 0,
                "candidates_discovered": 0,
                "selected_count": 0,
                "summaries_recorded": 0,
                "profile_jobs_planned": 0,
                "issue_codes": [],
                "raw_output": False,
                "sensitive_value_echo": False,
                "next_step": "untrusted retained text query-123",
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_collector_summary_json": "collector-summary.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert "No Details ready" in body
    assert (
        '<span class="query-inbox-metric"><strong>producer status</strong>'
        "<span>idle / 0 rows / 0 jobs</span></span>" in body
    )
    assert '<span class="query-inbox-metric"><strong>last producer run</strong>' in body
    assert (
        '<span class="query-inbox-metric"><strong>producer next step</strong>'
        "<span>Collector ran but found no retained summaries; check the scan window "
        "and filters if this is unexpected.</span></span>" in body
    )
    assert "collector-summary.json" not in body
    assert "untrusted retained text" not in body
    assert "query-123" not in body


def test_query_inbox_online_history_smoke_reads_batch_collector_summary(
    tmp_path,
    monkeypatch,
):
    from query_doctor.cli import batch_recent

    module = load_web_module()
    history_db = Path("history") / "recent.sqlite"
    collector_summary = Path("history") / "collector-summary.json"
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="producer-smoke-query",
            start_time="2026-07-03T10:00:00Z",
            end_time="2026-07-03T10:10:00Z",
            duration_ms=600_000,
            status="FINISHED",
            user="analyst",
            pool="root.default",
            query_type="QUERY",
            statement="SELECT secret_column FROM private_table",
        ),
        selected=True,
        reason="selected: long-running summary",
        sql_verb="SELECT",
    )
    monkeypatch.setattr(
        batch_recent,
        "discover_candidates",
        lambda config, env: batch_recent.DiscoveryResult(
            [candidate],
            [],
            "client-side",
            1,
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = batch_recent.main(
        [
            "--out",
            "query-doctor-batch",
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--discover-only",
            "--metadata-mode",
            "off",
            "--top-reports",
            "0",
            "--recent-history-db",
            str(history_db),
            "--recent-history-collector-summary-json",
            str(collector_summary),
        ],
        env={"CM_PASSWORD": "secret", "CM_USERNAME": "user"},
    )
    assert result == 0
    collector_payload = json.loads(collector_summary.read_text(encoding="utf-8"))
    assert collector_payload["status"] == "recorded"
    assert collector_payload["summaries_recorded"] == 1
    assert collector_payload["profile_jobs_planned"] == 1
    collector_payload_text = json.dumps(collector_payload, sort_keys=True)
    assert "producer-smoke-query" not in collector_payload_text
    assert "secret_column" not in collector_payload_text
    assert "private_table" not in collector_payload_text
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": str(history_db),
                "recent_history_collector_summary_json": str(collector_summary),
            }
        ),
        encoding="utf-8",
    )

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert "No Details ready" in body
    assert "Check All recent" in body
    assert (
        '<span class="query-inbox-metric"><strong>producer status</strong>'
        "<span>recorded / 1 rows / 1 jobs</span></span>" in body
    )
    assert '<span class="query-inbox-metric"><strong>last producer run</strong>' in body
    assert "collector-summary.json" not in body
    assert "secret_column" not in body
    assert "private_table" not in body
    assert str(tmp_path) not in body


@pytest.mark.parametrize(
    ("collector_payload", "expected_status", "expected_next_step"),
    [
        (
            {
                "summary_kind": "query_doctor_recent_history_collector_v1",
                "status": "failed",
                "observed_at_iso": "2026-07-03T10:00:00+00:00",
                "discover_only": True,
                "history_backend": "sqlite",
                "summaries_inspected": 0,
                "candidates_discovered": 0,
                "selected_count": 0,
                "summaries_recorded": 0,
                "profile_jobs_planned": 0,
                "issue_codes": ["discovery_failed"],
                "raw_output": False,
                "sensitive_value_echo": False,
                "next_step": "untrusted failed producer text query-456",
            },
            "failed / 0 rows / 0 jobs",
            "Check the Recent summary collector job and configured history credentials.",
        ),
        (
            {
                "summary_kind": "query_doctor_recent_history_collector_v1",
                "status": "warning",
                "observed_at_iso": "2026-07-03T10:00:00+00:00",
                "discover_only": True,
                "history_backend": "sqlite",
                "summaries_inspected": 3,
                "candidates_discovered": 2,
                "selected_count": 1,
                "summaries_recorded": 1,
                "profile_jobs_planned": 2,
                "issue_codes": ["recent_history_warning", "unexpected retained text"],
                "raw_output": False,
                "sensitive_value_echo": False,
                "next_step": "untrusted warning producer text query-789",
            },
            "warning / 1 rows / 2 jobs",
            "Check recent-history store and profile-job planning warnings before relying on producer health.",
        ),
    ],
)
def test_query_inbox_online_history_projects_producer_problem_states_raw_free(
    tmp_path,
    monkeypatch,
    collector_payload,
    expected_status,
    expected_next_step,
):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    collector_summary = tmp_path / "collector-summary.json"
    collector_summary.write_text(json.dumps(collector_payload), encoding="utf-8")
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_collector_summary_json": "collector-summary.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert "No Details ready" in body
    assert (
        '<span class="query-inbox-metric"><strong>producer status</strong>'
        f"<span>{expected_status}</span></span>" in body
    )
    assert '<span class="query-inbox-metric"><strong>last producer run</strong>' in body
    assert (
        '<span class="query-inbox-metric"><strong>producer next step</strong>'
        f"<span>{expected_next_step}</span></span>" in body
    )
    assert "collector-summary.json" not in body
    assert "untrusted" not in body
    assert "query-456" not in body
    assert "query-789" not in body
    assert "unexpected retained text" not in body
    assert "secret_column" not in body
    assert "private_table" not in body


def test_query_inbox_online_history_marks_unavailable_producer_summary_raw_free(
    tmp_path,
    monkeypatch,
):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_collector_summary_json": "missing-collector-summary.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert "No Details ready" in body
    assert (
        '<span class="query-inbox-metric"><strong>producer status</strong>'
        "<span>unavailable / 0 rows / 0 jobs</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>producer next step</strong>'
        "<span>Refresh the retained collector-run summary before relying on producer health.</span>"
        "</span>" in body
    )
    assert "missing-collector-summary.json" not in body
    assert "secret_column" not in body
    assert "private_table" not in body


def test_query_inbox_online_history_blocks_unsafe_collector_summary(tmp_path, monkeypatch):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    collector_summary = tmp_path / "collector-summary.json"
    collector_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_collector_v1",
                "status": "recorded",
                "observed_at_iso": "2026-07-03T10:00:00+00:00",
                "summaries_recorded": 1,
                "profile_jobs_planned": 1,
                "raw_sql": "SELECT secret_column FROM private_table",
                "raw_output": False,
                "sensitive_value_echo": False,
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_collector_summary_json": "collector-summary.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert (
        '<span class="query-inbox-metric"><strong>producer status</strong>'
        "<span>blocked / 0 rows / 0 jobs</span></span>" in body
    )
    assert "secret_column" not in body
    assert "private_table" not in body
    assert "collector-summary.json" not in body


def test_query_inbox_online_history_shows_configured_operator_readiness(tmp_path, monkeypatch):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    operator_summary = tmp_path / "operator-readiness.json"
    operator_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_operator_readiness_v1",
                "status": "ready",
                "accepted_summary_count": 5,
                "evidence_summary_count": 5,
                "collector_summary_present": True,
                "retention_summary_present": True,
                "remediation_summary_present": True,
                "issue_codes": [],
                "raw_output": False,
                "sensitive_value_echo": False,
                "operations": {
                    "postgres_readiness": {
                        "accepted": True,
                        "status": "ready",
                        "schema_initialized": True,
                        "check_count": 4,
                        "issue_count": 0,
                    },
                    "profile_worker": {
                        "accepted": True,
                        "status": "done",
                        "jobs_claimed": 2,
                        "jobs_completed": 1,
                        "jobs_retried": 1,
                        "jobs_failed": 0,
                        "jobs_lease_lost": 0,
                        "analysis_cache_records": 5,
                        "profile_artifact_records": 4,
                        "profile_backlog_health": {
                            "pending_jobs": 2,
                            "retry_pending_jobs": 1,
                            "leased_jobs": 1,
                            "stale_leased_jobs": 1,
                            "failed_jobs": 3,
                        },
                        "next_step": "untrusted retained text query-123",
                        "profile_backlog_next_step": "untrusted retained backlog text query-123",
                        "issue_count": 0,
                    },
                    "collector_summary": {
                        "present": True,
                        "accepted": True,
                        "status": "recorded",
                        "observed_at_iso": "2026-07-03T10:08:00+00:00",
                        "discover_only": True,
                        "history_backend": "postgres",
                        "summaries_inspected": 3,
                        "candidates_discovered": 3,
                        "selected_count": 2,
                        "summaries_recorded": 2,
                        "profile_jobs_planned": 2,
                        "next_step": "untrusted retained text query-123",
                        "issue_count": 0,
                    },
                    "retention": {
                        "present": True,
                        "accepted": True,
                        "status": "pruned",
                        "summaries_deleted": 3,
                        "profile_jobs_deleted": 2,
                        "analysis_cache_deleted": 1,
                        "profile_artifacts_deleted": 1,
                        "total_deleted": 7,
                        "issue_count": 0,
                    },
                    "profile_remediation": {
                        "present": True,
                        "accepted": True,
                        "status": "applied",
                        "mode": "apply",
                        "matched_failed_jobs": 5,
                        "selected_failed_jobs": 2,
                        "requeued_jobs": 2,
                        "skipped_due_to_limit": 3,
                        "next_step": "untrusted retained text query-123",
                        "issue_count": 0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_operator_readiness_summary_json": "operator-readiness.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert "No Details ready" in body
    assert (
        '<span class="query-inbox-metric"><strong>operator readiness</strong>'
        "<span>ready</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness evidence</strong>'
        "<span>5/5 summaries</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>history schema</strong>'
        "<span>ready / 4 checks</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>operator collector</strong>'
        "<span>recorded / 2 recorded / 2 jobs / 3 inspected</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>collector observed</strong>'
        "<span>2026-07-03T10:08:00+00:00</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>collector handoff next step</strong>'
        "<span>Run the Recent profile worker to process planned profile jobs.</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile worker</strong>'
        "<span>2 claimed / 1 completed / 0 failed / 1 retried</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>worker materialization</strong>'
        "<span>5 cache / 4 artifacts</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>worker next step</strong>'
        "<span>Let the next worker run retry pending jobs; investigate repeated normalized "
        "error codes.</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile backlog</strong>'
        "<span>2 pending / 1 retry / 1 leased / 1 stale / 3 failed</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>backlog next step</strong>'
        "<span>Run the Recent profile worker to reclaim expired leases; check worker "
        "lease duration if stale leases persist.</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>history retention</strong>'
        "<span>7 deleted / 3 summaries</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile remediation</strong>'
        "<span>apply / 5 matched / 2 selected / 2 requeued</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>remediation next step</strong>'
        "<span>Run the Recent profile worker to process the requeued jobs.</span></span>" in body
    )
    assert "secret_column" not in body
    assert "private_table" not in body
    assert "operator-readiness.json" not in body
    assert "untrusted retained text" not in body
    assert "query-123" not in body


def test_query_inbox_operator_readiness_blocks_unsafe_configured_summary(tmp_path, monkeypatch):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    operator_summary = tmp_path / "operator-readiness.json"
    operator_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_operator_readiness_v1",
                "status": "ready",
                "accepted_summary_count": 3,
                "evidence_summary_count": 3,
                "raw_sql": "SELECT secret_column FROM private_table",
                "operations": {
                    "profile_worker": {
                        "accepted": True,
                        "status": "done",
                        "jobs_claimed": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_operator_readiness_summary_json": "operator-readiness.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert (
        '<span class="query-inbox-metric"><strong>operator readiness</strong>'
        "<span>blocked</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness issues</strong>'
        "<span>1</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness reasons</strong>'
        "<span>summary_unsafe</span></span>" in body
    )
    assert "raw_sql" not in body
    assert "secret_column" not in body
    assert "private_table" not in body
    assert "operator-readiness.json" not in body


def test_query_inbox_operator_readiness_shows_allowlisted_blocked_reasons_only(
    tmp_path,
    monkeypatch,
):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    operator_summary = tmp_path / "operator-readiness.json"
    operator_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_operator_readiness_v1",
                "status": "blocked",
                "accepted_summary_count": 1,
                "evidence_summary_count": 3,
                "retention_summary_present": False,
                "remediation_summary_present": False,
                "issue_codes": [
                    "postgres_readiness_summary_missing",
                    "profile_worker_summary_backlog_health_missing",
                    "query-123",
                    "raw_sql",
                ],
                "raw_output": False,
                "sensitive_value_echo": False,
                "operations": {
                    "postgres_readiness": {"accepted": False},
                    "profile_worker": {"accepted": False},
                    "retention": {"present": False, "accepted": False},
                    "profile_remediation": {"present": False, "accepted": False},
                },
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_operator_readiness_summary_json": "operator-readiness.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert (
        '<span class="query-inbox-metric"><strong>operator readiness</strong>'
        "<span>blocked</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness evidence</strong>'
        "<span>1/3 summaries</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness issues</strong>'
        "<span>4</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness reasons</strong>'
        "<span>postgres_readiness_summary_missing / "
        "profile_worker_summary_backlog_health_missing / unknown_issue / +1 more</span></span>"
        in body
    )
    assert "query-123" not in body
    assert "raw_sql" not in body
    assert "operator-readiness.json" not in body


def test_query_inbox_operator_readiness_blocks_wrong_kind_configured_summary(
    tmp_path,
    monkeypatch,
):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    _write_single_recent_history_row(history_db)
    operator_summary = tmp_path / "operator-readiness.json"
    operator_summary.write_text(
        json.dumps(
            {
                "summary_kind": "query_doctor_recent_history_operator_readiness_v0",
                "status": "ready",
                "accepted_summary_count": 3,
                "evidence_summary_count": 3,
                "issue_codes": [],
                "raw_output": False,
                "sensitive_value_echo": False,
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_operator_readiness_summary_json": "operator-readiness.json",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(module.WebSettings(config=config, repo_dir=REPO_DIR))

    assert (
        '<span class="query-inbox-metric"><strong>operator readiness</strong>'
        "<span>blocked</span></span>" in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>readiness reasons</strong>'
        "<span>summary_kind_drift</span></span>" in body
    )
    assert "operator-readiness.json" not in body


def test_query_inbox_history_filter_uses_history_with_latest_summary(tmp_path, monkeypatch):
    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(history_db)
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="history-query",
            start_time="2026-07-03T10:00:00Z",
            end_time="2026-07-03T10:10:00Z",
            duration_ms=600_000,
            status="FINISHED",
            user="analyst",
            pool="root.default",
            query_type="QUERY",
            statement="SELECT secret_column FROM private_table",
        ),
        selected=True,
        reason="selected: long-running summary",
        sql_verb="SELECT",
    )
    store.upsert_summaries(
        [
            history_record_from_candidate(
                candidate,
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                recorded_at_iso="2026-07-03T10:05:00+00:00",
            )
        ]
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        '{"recent_history_backend":"sqlite","recent_history_db":"recent-history.sqlite"}',
        encoding="utf-8",
    )
    batch_summary = tmp_path / "batch_summary.json"
    batch_summary.write_text('{"mode":"recent-query-batch","cases":[]}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    body = module.render_batch_page(
        module.WebSettings(config=config, repo_dir=REPO_DIR, batch_summary=batch_summary),
        inbox_scope_filters=QueryInboxScopeFilters(source="history"),
        query_group="all",
        history_view="all_recent",
    )

    assert "All recent queries" in body
    assert "Showing the newest retained summaries" in body
    assert "history-query" in body
    assert "No matching inbox scope" not in body
    assert "secret_column" not in body
    assert "private_table" not in body


def test_online_recent_history_keeps_first_screen_bounded():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    payloads = []
    for index in range(600):
        if index >= 550:
            profile_status = "analyzed"
            analysis_cache_payload = {"analysis_status": "ok", "collection_status": "ok"}
        elif index >= 500:
            profile_status = "pending"
            analysis_cache_payload = None
        else:
            profile_status = "not_collected"
            analysis_cache_payload = None
        payload = {
            "query_id": f"query-{index:03d}",
            "start_time": f"2026-07-03T{index % 24:02d}:00:00Z",
            "end_time": f"2026-07-03T{index % 24:02d}:01:00Z",
            "duration_ms": 60_000 + index,
            "suspicion_score": index % 100,
            "suspicion_level": "low",
            "profile_status": profile_status,
        }
        if analysis_cache_payload is not None:
            payload["analysis_cache_payload"] = analysis_cache_payload
        payloads.append(payload)

    summary = recent_history_summary_from_payloads(
        payloads,
        backend="sqlite",
        retained_count=6164,
        profile_backlog_health={
            "pending_jobs": 12,
            "retry_pending_jobs": 0,
            "leased_jobs": 0,
            "stale_leased_jobs": 0,
            "failed_jobs": 0,
        },
    )

    assert summary["selected_count"] == 500
    assert summary["summaries_inspected"] == 6164
    assert summary["triage_profile_limit"] == 500
    assert summary["history_profile_status_counts"] == {
        "not_collected": 500,
        "pending": 50,
        "analyzed": 50,
    }
    assert summary["history_details_ready_count"] == 50
    assert len(summary["cases"]) == 500
    assert summary["warnings"] == [
        "Online history retained 6164 summary rows; showing the newest 500 rows."
    ]
    coverage = summary["materialized_case_index"]["coverage"]
    assert coverage["retained_summary_count"] == 6164
    assert coverage["displayed_summary_count"] == 500
    status_body = render_query_inbox_status(query_inbox_status_from_summary(summary))
    assert (
        '<span class="query-inbox-metric"><strong>history rows</strong>'
        "<span>6164 retained / 500 shown</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile loop</strong>'
        "<span>50 queued / 50 analyzed</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>details ready</strong>'
        "<span>50/50 analyzed</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile backlog</strong>'
        "<span>12 pending / 0 retry / 0 leased / 0 stale / 0 failed</span></span>" in status_body
    )
    body = render_batch_summary(summary, query_group="all", title="Online History")
    assert "Retained 6164 summaries -&gt; Showing 500 rows" in body
    assert "Profile analysis ready: 50/50" in body
    assert "Analyzed 500 cases" not in body


def test_online_recent_history_projects_collector_freshness_raw_free():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = recent_history_summary_from_payloads(
        [
            {
                "query_id": "query-old",
                "duration_ms": 60_000,
                "suspicion_score": 20,
                "suspicion_level": "low",
                "profile_status": "pending",
                "recorded_at_iso": "2026-07-03T09:00:00+00:00",
                "statement": "SELECT secret_column FROM private_table",
            },
            {
                "query_id": "query-new",
                "duration_ms": 120_000,
                "suspicion_score": 40,
                "suspicion_level": "medium",
                "profile_status": "pending",
                "recorded_at_iso": "2026-07-03T10:00:00Z",
                "raw_sql": "SELECT secret_column FROM private_table",
            },
        ],
        backend="sqlite",
        retained_count=2,
        now=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
    )

    freshness = summary["history_collector_freshness"]
    assert freshness["status"] == "stale"
    assert freshness["retained_summary_count"] == 2
    assert freshness["displayed_summary_count"] == 2
    assert freshness["stale_after_minutes"] == 120
    assert freshness["age_minutes"] == 210
    assert freshness["latest_recorded_at_iso"] == "2026-07-03T10:00:00+00:00"

    status_body = render_query_inbox_status(query_inbox_status_from_summary(summary))
    assert (
        '<span class="query-inbox-metric"><strong>collector freshness</strong>'
        "<span>stale</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>last planning</strong>'
        "<span>3 h ago</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>collector next step</strong>'
        "<span>Use New scan to refresh retained summaries, or check the scheduled Recent "
        "summary collector.</span></span>" in status_body
    )
    freshness_text = json.dumps(freshness, sort_keys=True)
    assert "secret_column" not in freshness_text
    assert "private_table" not in freshness_text
    assert "query-old" not in freshness_text
    assert "query-new" not in freshness_text
    assert "secret_column" not in status_body
    assert "private_table" not in status_body


def test_online_recent_history_collector_freshness_handles_empty_and_unknown():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    empty_summary = recent_history_summary_from_payloads(
        [],
        backend="sqlite",
        retained_count=0,
        now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    assert empty_summary["history_collector_freshness"]["status"] == "empty"
    empty_body = render_query_inbox_status(query_inbox_status_from_summary(empty_summary))
    assert (
        '<span class="query-inbox-metric"><strong>collector freshness</strong>'
        "<span>empty</span></span>" in empty_body
    )

    unknown_summary = recent_history_summary_from_payloads(
        [{"query_id": "query-without-recorded-at"}],
        backend="sqlite",
        retained_count=1,
        now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    assert unknown_summary["history_collector_freshness"]["status"] == "unknown"
    unknown_body = render_query_inbox_status(query_inbox_status_from_summary(unknown_summary))
    assert (
        '<span class="query-inbox-metric"><strong>collector next step</strong>'
        "<span>Run a discover-only Recent refresh to refresh retained summary freshness "
        "evidence.</span></span>" in unknown_body
    )


def test_details_ready_view_does_not_infer_collector_freshness_from_ready_rows():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = recent_history_summary_from_payloads(
        [
            {
                "profile_status": "analyzed",
                "recorded_at_iso": "2026-07-03T09:00:00+00:00",
                "analysis_cache_payload": {"analysis_status": "analyzed"},
            }
        ],
        backend="sqlite",
        retained_count=20,
        now=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
        history_view="details_ready",
    )

    status_body = render_query_inbox_status(query_inbox_status_from_summary(summary))
    assert summary["history_collector_freshness"]["scope"] == "details_ready"
    assert "collector freshness" not in status_body
    assert "last planning" not in status_body


def test_online_recent_history_projects_collector_run_summary_raw_free():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = recent_history_summary_from_payloads(
        [],
        backend="sqlite",
        retained_count=0,
        collector_run={
            "status": "idle",
            "observed_at_iso": "2026-07-03T09:30:00+00:00",
            "history_backend": "sqlite",
            "summaries_recorded": 0,
            "profile_jobs_planned": 0,
            "raw_sql": "SELECT secret_column FROM private_table",
            "query_id": "query-123",
        },
        now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )

    status_body = render_query_inbox_status(
        query_inbox_status_from_summary(
            summary,
            now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        )
    )

    assert (
        '<span class="query-inbox-metric"><strong>producer status</strong>'
        "<span>idle / 0 rows / 0 jobs</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>last producer run</strong>'
        "<span>30 min ago</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>producer next step</strong>'
        "<span>Collector ran but found no retained summaries; check the scan window "
        "and filters if this is unexpected.</span></span>" in status_body
    )
    assert "secret_column" not in status_body
    assert "private_table" not in status_body
    assert "query-123" not in status_body


def test_online_recent_history_projects_profile_worker_states_raw_free():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = recent_history_summary_from_payloads(
        [
            {
                "query_id": "query-pending",
                "duration_ms": 60_000,
                "suspicion_score": 45,
                "suspicion_level": "medium",
                "profile_status": "pending",
                "statement": "SELECT secret_column FROM private_table",
            },
            {
                "query_id": "query-analyzed",
                "duration_ms": 120_000,
                "suspicion_score": 20,
                "suspicion_level": "low",
                "profile_status": "analyzed",
                "analysis_cache_payload": {
                    "score": 72,
                    "score_severity": "suspicious",
                    "analysis_status": "ok",
                    "collection_status": "ok",
                    "raw_sql": "SELECT secret_column FROM private_table",
                },
                "raw_sql": "SELECT secret_column FROM private_table",
            },
            {
                "query_id": "query-processing",
                "duration_ms": 150_000,
                "suspicion_score": 30,
                "suspicion_level": "medium",
                "profile_status": "processing",
                "subprocess_output": "secret worker output",
            },
            {
                "query_id": "query-retry",
                "duration_ms": 160_000,
                "suspicion_score": 32,
                "suspicion_level": "medium",
                "profile_status": "retry_pending",
                "profile_last_error_code": "profile-fetch-http-503",
            },
            {
                "query_id": "query-failed",
                "duration_ms": 180_000,
                "suspicion_score": 0,
                "suspicion_level": "none",
                "profile_status": "failed",
                "profile_last_error_code": "profile-fetch-permanent",
                "profile_path": "/private/tmp/query-doctor-secret/profile.txt",
            },
        ],
        backend="sqlite",
        retained_count=3,
    )

    cases = {str(case["query_id"]): case for case in summary["cases"]}
    assert cases["query-pending"]["analysis_status"] == "profile_pending"
    assert cases["query-analyzed"]["collection_status"] == "ok"
    assert cases["query-analyzed"]["analysis_status"] == "ok"
    assert cases["query-processing"]["analysis_status"] == "profile_processing"
    assert cases["query-retry"]["analysis_status"] == "profile_retry_pending"
    assert cases["query-retry"]["failure_category"] == "profile_fetch_http_503"
    assert cases["query-failed"]["analysis_status"] == "failed"
    assert cases["query-failed"]["failure_category"] == "profile_fetch_permanent"
    assert summary["history_profile_status_counts"] == {
        "pending": 1,
        "processing": 1,
        "retry_pending": 1,
        "analyzed": 1,
        "failed": 1,
    }

    status = query_inbox_status_from_summary(summary)
    status_body = render_query_inbox_status(status)
    body = render_batch_summary(summary, query_group="all", title="Online History")

    assert "query-pending" in body
    assert "query-analyzed" in body
    assert "query-processing" in body
    assert "query-retry" in body
    assert "query-failed" in body
    assert 'data-href="/batch/case/case-001"' not in body
    assert 'data-href="/batch/case/recent-case-002"' in body
    assert 'data-href="/batch/case/case-003"' not in body
    assert 'data-href="/batch/case/case-004"' not in body
    assert 'data-href="/batch/case/case-005"' not in body
    assert (
        '<span class="query-inbox-metric"><strong>profile loop</strong>'
        "<span>2 queued / 1 active / 1 analyzed / 1 failed</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile states</strong>'
        "<span>1 pending / 1 retry / 1 processing / 1 analyzed / 1 failed</span></span>"
        in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile errors</strong>'
        "<span>profile_fetch_http_503 x1 / profile_fetch_permanent x1</span></span>" in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>profile next step</strong>'
        "<span>Review normalized profile errors before requeueing failed rows.</span></span>"
        in status_body
    )
    assert (
        '<span class="query-inbox-metric"><strong>details ready</strong>'
        "<span>1/1 analyzed</span></span>" in status_body
    )
    assert "secret_column" not in body
    assert "private_table" not in body
    assert "/private/tmp" not in body
    assert "secret_column" not in status_body
    assert "private_table" not in status_body
    assert "/private/tmp" not in status_body
    assert "secret worker output" not in status_body
    assert "profile_fetch_http_503" in body
    assert "profile_fetch_permanent" in body


def test_online_recent_history_links_only_materialized_analyzed_rows_raw_free():
    summary = recent_history_summary_from_payloads(
        [
            {
                "query_id": "query-materialized",
                "duration_ms": 90_000,
                "suspicion_score": 12,
                "suspicion_level": "low",
                "profile_status": "analyzed",
                "analysis_cache_payload": {
                    "score": 82,
                    "score_severity": "high",
                    "score_reasons": ["runtime signal"],
                    "analysis_status": "ok",
                    "collection_status": "ok",
                    "metadata_status": "not_collected",
                    "referenced_table_count": 3,
                    "collectable_metadata_table_count": 2,
                    "collected_metadata_table_count": 0,
                    "too_large_count": 1,
                    "cm_collect_seconds": 1.25,
                    "analysis_seconds": 0.75,
                    "total_seconds": 2.0,
                    "raw_sql": "SELECT secret_column FROM private_table",
                },
            },
            {
                "query_id": "query-analyzed-without-cache",
                "duration_ms": 120_000,
                "suspicion_score": 25,
                "suspicion_level": "medium",
                "profile_status": "analyzed",
            },
        ],
        backend="sqlite",
        retained_count=2,
    )

    cases = {str(case["query_id"]): case for case in summary["cases"]}
    assert cases["query-materialized"]["case_index"] == 1
    assert cases["query-materialized"]["score"] == 82
    assert cases["query-materialized"]["analysis_status"] == "ok"
    assert cases["query-materialized"]["collectable_metadata_table_count"] == 2
    assert cases["query-materialized"]["cm_collect_seconds"] == 1.25
    assert "case_index" not in cases["query-analyzed-without-cache"]

    body = render_batch_summary(summary, query_group="all", title="Online History")

    assert 'data-href="/batch/case/recent-case-001"' in body
    assert '<a class="batch-row-action" href="/batch/case/recent-case-001">Open Details</a>' in body
    assert 'data-href="/batch/case/case-002"' not in body
    assert "secret_column" not in body
    assert "private_table" not in body


def test_online_recent_history_all_recent_view_explains_non_openable_rows():
    summary = recent_history_summary_from_payloads(
        [
            {
                "query_id": "query-materialized",
                "profile_status": "analyzed",
                "analysis_cache_payload": {"score": 72, "analysis_status": "ok"},
            },
            {
                "query_id": "query-analyzed-without-cache",
                "profile_status": "analyzed",
            },
            {"query_id": "query-pending", "profile_status": "pending"},
        ],
        backend="sqlite",
        retained_count=10_000,
        history_view="all_recent",
    )

    body = render_batch_summary(summary, query_group="all", title="Online History")

    assert 'data-href="/batch/case/recent-case-001"' in body
    assert "Details unavailable" in body
    assert "Queued" in body


def test_package_layout_renderers_are_available():
    from query_doctor.web.ui import layout

    assert layout.BRAND_MARK_SVG
    assert callable(layout.render_favicon_link)
    assert callable(layout.render_shared_styles)
    assert callable(layout.render_app_header)
    assert callable(layout.render_app_footer)
    assert callable(layout.render_client_script)
    assert callable(layout.render_static_stylesheet_link)
    assert callable(layout.render_script_link)


def test_detail_job_polling_preserves_current_anchor():
    from query_doctor.web.ui import layout

    script = layout.render_client_script()

    assert "function detailJobRedirectTarget(progressElement)" in script
    assert "window.location.hash && target.indexOf('#') === -1" in script
    assert "new URL(redirectTarget, window.location.href).href === window.location.href" in script
    assert "window.location.reload()" in script


def test_detail_job_polling_applies_progress_view():
    from query_doctor.web.ui import layout

    script = layout.render_client_script()

    assert "function escapeHtml(value)" in script
    assert "replace(/[&<>\"']/g" in script
    assert "function safeProgressStepState(value)" in script
    assert (
        "function applyProgressView(progressElement, progressView, fallbackStage, fallbackProgress)"
        in script
    )
    assert "progressElement.querySelector('.progress-stage')" in script
    assert "progressElement.querySelector('.progress-fill')" in script
    assert "progressElement.querySelector('.batch-progress-steps')" in script
    assert "batch-progress-step--' + stepState" in script
    assert (
        "applyProgressView(progressElement, data.progress_view, data.stage, data.progress)"
        in script
    )
    assert "applyProgressView(jobPanel, data.progress_view, data.stage, data.progress)" in script


def test_package_progress_renderers_are_available():
    from query_doctor.web.ui import progress

    assert progress.WEB_STAGES
    assert callable(progress.render_pending_progress_panel)
    assert callable(progress.render_job_panel)


def test_package_page_renderers_are_available():
    from query_doctor.web.ui import pages

    assert callable(pages.render_page)
    assert callable(pages.render_query_page)
    assert callable(pages.render_batch_page)
    assert callable(pages.render_batch_case_detail_view_page)
    assert callable(pages.render_error_panel)


def test_web_render_page_escapes_user_input():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings, query_id="<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_web_render_page_contains_reference_local_ui_shell():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings)
    styles = layout.render_shared_styles()

    assert "Query Doctor" in body
    assert "Big Data query diagnostics" in body
    assert 'class="app-footer"' in body
    assert "footer-copy" not in body
    assert 'https://github.com/alexandrefimov/Query-Doctor"' in body
    assert "https://pypi.org/project/query-doctor/" in body
    assert "https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/README.md" in body
    assert "https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/security-model.md" in body
    assert 'href="https://github.com/alexandrefimov/Query-Doctor/issues"' not in body
    assert 'href="https://github.com/alexandrefimov"' not in body
    assert body.count('class="footer-separator" aria-hidden="true">|</span>') == 3
    assert body.index(">GitHub</a>") < body.index(">PyPI</a>")
    assert body.index(">PyPI</a>") < body.index(">Docs</a>")
    assert body.index(">Docs</a>") < body.index(">Trust</a>")
    assert 'target="_blank" rel="noopener noreferrer"' in body
    assert "impala-query-doctor" not in body
    assert "Impala Doctor" not in body
    assert "demo-watermark" not in body
    assert "page-shell" not in body
    assert "run-panel" in body
    assert "Known Query ID" in body
    assert "Analyze one explicit Impala query by Query ID." in body
    assert "One explicit Query ID. Query Doctor collects or reuses the profile" in body
    assert "prepares the Python report" in body
    assert "does not auto-run LLM or optimizer actions" in body
    assert '<label for="query_id">Query ID</label>' in body
    assert 'id="profile-upload-form"' not in body
    assert "Query ID or case path" not in body
    assert "Analyze one explicit Impala query with deterministic profile facts." not in body
    assert "Saved case paths are supported by the CLI pipeline for now." not in body
    assert "case path" not in body
    assert '<details class="info-popover"><summary aria-label="Query ID help">i</summary>' in body
    assert "CM: unknown/not checked" not in body
    assert "Kerberos: unknown/not checked" not in body
    assert "Metadata collector: CLI only" not in body
    assert ".hero-card:after" not in body
    assert '<link rel="stylesheet" href="/static/app.css">' in body
    assert '<script src="/static/theme-bootstrap.js"></script>' in body
    assert '<script src="/static/app.js"></script>' in body
    assert "<style>" not in body
    assert "color-scheme:light" not in body
    assert "color-scheme:light" in styles
    assert "html[data-theme=dark]" in styles
    assert "--bg:#fff" in styles
    assert "--bg:#0f1214" in styles
    assert "--surface:#fff" in styles
    assert "--surface:#0f1214" in styles
    assert_css_contains(
        styles, ".page{display:flex;flex-direction:column;min-height:100vh;margin:0;padding:0;"
    )
    assert_css_contains(styles, ".app-header{position:sticky;top:0;z-index:30;")
    assert_css_contains(
        styles,
        ".page>.panel,.page>section,.page>details{width:100%;max-width:1560px;margin:0 auto;",
    )
    assert_css_contains(styles, ".page>.panel+.panel{border-top:1px solid var(--border)}")
    assert_css_contains(
        styles, ".app-footer{display:flex;align-items:center;margin-top:auto;"
    )
    assert_css_contains(styles, ".footer-separator{color:var(--muted-2);font-weight:400")
    assert "data-design" not in styles
    assert "design-icon-review" not in styles
    assert "max-height:66vh" not in body
    assert "overflow-wrap:anywhere" in styles
    assert "Интеллектуальный анализ Impala-запросов по Query ID" not in body
    assert "Mode" not in body
    assert "Редактировать идентификаторы" not in body
    assert "Анализировать" not in body
    assert '<button class="run-button" type="submit">Run</button>' in body
    assert 'name="mode"' not in body
    assert '<input type="radio" name="mode" value="admin" checked>' not in body
    assert ".segmented label:focus-within" not in styles
    assert_css_contains(
        styles,
        ".segmented input:checked+span,.segmented input:checked+label{color:#fff;background:var(--accent);",
    )
    assert_css_contains(styles, ".segmented label{min-width:58px;display:grid;place-items:stretch;")
    assert_css_contains(
        styles, ".segmented span{display:grid;place-items:center;width:100%;height:100%;"
    )
    assert_css_contains(styles, ".engine-control{display:grid;align-items:start;")
    assert_css_contains(
        styles, ".engine-segmented,.workflow-segmented{gap:0;width:100%;min-height:44px;"
    )
    assert_css_contains(
        styles,
        ".engine-segmented span,.workflow-segmented span{align-content:center;justify-items:start;",
    )
    assert_css_contains(styles, ".manual-inputs-hidden{display:none!important}")
    assert body.index('id="query_id"') < body.index(
        '<button class="run-button" type="submit">Run</button>'
    )
    assert "Локальный демо-сервер: только явный Query ID" not in body
    assert "Validated before render" not in body
    assert "Analyzer-owned facts" not in body
    assert "LLM writes wording only" not in body
    assert "Local-first" not in body
    assert "Safe by default" not in body
    assert "validated report · analyzer facts · local-first · safe by default" not in body
    assert "How Query ID diagnosis works" not in body
    assert "Validated reports from this session appear after a run." not in body
    assert "This MVP UI does not expose a separate reports list yet." not in body
    assert "Checking Query ID" not in body
    assert "This usually takes a few seconds to a couple of minutes." not in body

    query_body = module.render_query_page(settings)
    assert 'id="profile-upload-form"' in query_body
    assert 'action="/profile/upload" enctype="multipart/form-data"' in query_body
    assert '<label for="profile_file">Exported profile</label>' in query_body
    assert "The uploaded profile is staged locally" in query_body

    public_demo_body = module.render_query_page(
        module.WebSettings(config=Path(".query-doctor-cm.local.json"), public_demo=True)
    )
    assert 'id="profile-upload-form"' not in public_demo_body
    assert 'action="/profile/upload"' not in public_demo_body


def test_web_render_page_sets_brand_favicon():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings)

    assert '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,' in body
    assert "www.w3.org" not in body
    assert body.index("<title>Query Doctor</title>") < body.index('rel="icon"')
    assert body.index('rel="icon"') < body.index('src="/static/theme-bootstrap.js"')
    assert body.index('src="/static/theme-bootstrap.js"') < body.index('href="/static/app.css"')


def test_web_render_page_contains_theme_toggle():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings)
    styles = layout.render_shared_styles()
    scripts = layout.read_static_asset_text("theme-bootstrap.js") + layout.render_client_script()

    assert 'id="theme-toggle"' in body
    assert 'aria-label="Switch to dark theme"' in body
    assert "query-doctor-theme" in scripts
    assert "prefers-color-scheme: dark" in scripts
    assert "Switch to light theme" in scripts
    assert_css_contains(
        styles,
        ".theme-toggle{display:inline-grid;place-items:center;width:30px;"
        "height:30px;min-width:30px;flex:0 0 30px;border:1px solid var(--border-strong)",
    )
    assert_css_contains(styles, "background:var(--control);color:var(--accent-strong)")
    assert_css_contains(
        styles,
        "html[data-theme=dark] .theme-toggle{"
        "border-color:var(--border-strong);background:var(--control);color:var(--accent-strong)",
    )
    assert_css_contains(styles, ".theme-toggle svg{width:18px;height:18px}")
    assert_css_contains(styles, ".theme-toggle .theme-icon-light{display:none}")
    assert_css_contains(styles, ".theme-toggle .theme-icon-dark{display:block}")
    assert_css_contains(
        styles, "html[data-theme=dark] .theme-toggle .theme-icon-light{display:block}"
    )
    assert_css_contains(
        styles, "html[data-theme=dark] .theme-toggle .theme-icon-dark{display:none}"
    )


def test_web_render_page_shows_config_language_indicator_without_local_path(tmp_path):
    module = load_web_module()
    config = tmp_path / "query-doctor-config.json"
    settings = module.WebSettings(config=config, language="ru")

    body = module.render_page(settings)
    styles = layout.render_shared_styles()

    assert '<html lang="ru">' in body
    assert 'class="language-indicator"' in body
    assert ">RU</span>" in body
    assert "Глобальная настройка языка" in body
    assert "Help, Details и новыми отчетами" in body
    assert "поле language" in body
    assert "локальном конфиге" in body
    assert "query-doctor-config.json" not in body
    assert str(tmp_path) not in body
    assert_css_contains(styles, ".language-indicator{display:inline-grid;")
    assert_css_contains(styles, "grid-template-columns:minmax(0,1fr)auto44px")


def test_web_render_page_omits_design_toggle():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings)
    styles = layout.render_shared_styles()
    scripts = layout.render_client_script()

    assert 'id="design-toggle"' not in body
    assert 'aria-label="Switch to green design"' not in body
    assert "query-doctor-design" not in (
        layout.read_static_asset_text("theme-bootstrap.js") + scripts
    )
    assert "data-design" not in (layout.read_static_asset_text("theme-bootstrap.js") + scripts)
    assert "['serious', 'command']" not in (
        layout.read_static_asset_text("theme-bootstrap.js") + scripts
    )
    assert (
        "document.documentElement.setAttribute('data-theme', theme)"
        in layout.read_static_asset_text("theme-bootstrap.js")
    )
    assert "Switch to blue design" not in scripts
    assert "Switch to green design" not in scripts
    assert "design-toggle" not in styles
    assert "Switch to classic design" not in scripts
    assert "Switch to command design" not in scripts
    assert "Switch to review design" not in scripts
    assert "['serious', 'classic', 'command', 'review']" not in scripts
    assert "design-icon-classic" not in body
    assert "design-icon-review" not in body
    assert 'id="theme-toggle"' in body
    assert ".query-doctor-cm.local.json" not in body


def test_web_render_page_contains_optimizer_copy_handler():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings)
    script = layout.render_client_script()

    assert "data-copy-optimized-query" in script
    assert "data-optimized-query-block" in script
    assert "navigator.clipboard.writeText" in script
    assert "fallbackCopyCode" in script
    assert "Copy query" in script


def test_web_static_script_contains_csp_safe_row_navigation_handler():
    script = layout.render_client_script()

    assert "[data-href]" in script
    assert "rowNavigationTarget" in script
    assert "a, button, input, select, textarea, summary, form" in script
    assert "summary, details, form" not in script
    assert "window.location.assign(row.getAttribute('data-href'))" in script
    assert "window.open(row.getAttribute('data-href')" not in script
    assert "onclick=" not in script
    assert "onkeydown=" not in script


def test_recent_scan_default_empty_group_points_to_follow_up_tabs():
    body = render_batch_summary(
        {
            "selected_count": 1,
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "abc:def",
                    "score": 5,
                    "score_severity": "suspicious",
                    "collection_status": "ok",
                    "analysis_status": "ok",
                    "metadata_status": "skipped",
                    "score_reasons": ["memory estimate anomalies: 1"],
                }
            ],
        }
    )

    assert "No queries requiring attention were found." in body
    assert "Worth reviewing <span>1</span>" in body


def test_recent_scan_optimizer_ready_group_is_removed():
    body = render_batch_summary(
        {
            "selected_count": 1,
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "abc:def",
                    "score": 5,
                    "score_severity": "suspicious",
                    "collection_status": "ok",
                    "analysis_status": "ok",
                    "metadata_status": "skipped",
                    "query_optimization_candidate": {
                        "tier": "medium",
                        "score": 3,
                        "impact": "medium",
                        "confidence": "medium",
                    },
                    "score_reasons": ["memory estimate anomalies: 1"],
                }
            ],
        },
        query_group="optimizer_ready",
    )

    assert "Optimizer-ready" not in body
    assert "No queries requiring attention were found." in body
    assert "<summary>More filters</summary>" not in body
    assert "batch-filter-more" not in body
    assert "Rewrite opportunities <span>1</span>" in body


def test_web_render_page_omits_modes_even_when_report_mode_is_passed():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings, report_mode="admin")

    assert 'name="mode"' not in body
    assert '<input type="radio" name="mode" value="admin" checked>' not in body
    assert '<input type="radio" name="mode" value="user" checked>' not in body


def test_web_home_page_links_brand_and_readme_navigation():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_page(settings)

    assert '<a class="brand" href="/" aria-label="Query Doctor home">' in body
    assert '<a class="nav-link nav-link--active" href="/">Query Inbox</a>' in body
    assert 'href="/optimizer">Query Optimizer</a>' not in body
    assert 'href="/query">Specific Query</a>' not in body
    assert 'href="/running">Running Queries</a>' not in body
    assert '<a class="nav-link" href="/help">Help</a>' in body
    assert "Demo guide" not in body
    assert body.index('href="/">Query Inbox</a>') < body.index('href="/help">Help</a>')
    assert '<a class="nav-link" href="/readme">README</a>' not in body
    assert "Settings" not in body
