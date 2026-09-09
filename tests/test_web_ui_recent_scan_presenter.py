import json
from pathlib import Path

from query_doctor.recent.batch_models import CaseResult
from query_doctor.recent.batch_summary import case_score_severity as batch_case_score_severity
from query_doctor.web.presenters.recent_scan import (
    case_score_severity,
    present_recent_scan_action_candidates,
    present_recent_scan_case_detail,
    present_recent_scan_case_row,
    present_recent_scan_metadata,
    present_recent_scan_score_reason,
    present_recent_scan_score_reasons,
    present_recent_scan_status_summary,
    present_recent_scan_summary,
    present_recent_scan_technical_details,
    present_report_action,
    present_source_locators,
    primary_bottleneck_reason_label,
    safe_display_text,
)
from query_doctor.web.action_outcomes import (
    SCHEMA_VERSION,
    ActionOutcomeRecord,
    append_action_outcome,
    summarize_workload_action_outcomes,
)
from query_doctor.web.presenters.recent_scan_case_summary import (
    confidence_summary,
    primary_bottleneck_summary,
    review_anchor_summary,
)
from query_doctor.web.presenters.recent_scan_diagnostic_facts import (
    diagnostic_fact_by_id,
    verdict_kpi_facts,
)
from query_doctor.web.presenters.recent_scan_diagnostic_questions import (
    present_recent_scan_diagnostic_questions,
)
from query_doctor.web.presenters.workload_detail import present_workload_detail
from query_doctor.web.presenters.recent_scan_evidence_labels import (
    evidence_quality_label,
    evidence_stats_label,
)
from query_doctor.web.ui.action_candidates import (
    render_action_candidate_decision_findings,
    render_action_candidate_findings,
    render_action_candidate_findings_view,
)
from query_doctor.web.ui.html_helpers import escape_value
from query_doctor.web.ui.metadata_details import (
    metadata_score_reasons,
    render_metadata_fact_table_row_view,
    render_metadata_facts_body,
    render_metadata_facts_view,
)
from query_doctor.web.ui.llm_actions import (
    present_optimized_query_action,
    render_optimized_query_outcome,
)
from query_doctor.web.ui.recent_scan_details import (
    render_case_status_summary,
    render_case_status_summary_view,
    render_recent_scan_case_detail_view,
    render_diagnostic_questions_view,
    render_score_reason_card_view,
    render_score_reason_explanations,
    render_score_reason_explanations_view,
    render_technical_details,
    render_technical_details_view,
)
from query_doctor.web.details_facts import (
    parse_cm_metrics_facts,
    parse_data_movement_facts,
    parse_query_context_facts,
    parse_runtime_metrics_facts,
    parse_source_provenance_facts,
    parse_stats_quality_facts,
    parse_table_metadata_context_facts,
)
from query_doctor.web.ui import recent_scan_results as recent_scan_results_ui
from query_doctor.web.ui.recent_scan_results import render_batch_summary
from query_doctor.web.ui.workload_detail import render_workload_detail_view


REPO_DIR = Path(__file__).resolve().parents[1]
PRIMARY_BOTTLENECK_FIXTURE_DIR = REPO_DIR / "tests" / "fixtures" / "primary_bottleneck_fixtures"
from query_doctor.web.ui.runtime_metrics import (
    render_cluster_runtime_context_section,
    render_cm_metrics_section,
    render_data_movement_evidence_section,
)


def test_recent_scan_presenters_are_available():
    assert callable(present_recent_scan_summary)
    assert callable(case_score_severity)
    assert "profile_digest" not in safe_display_text("profile_digest.md")


def test_recent_scan_details_renderers_are_available():
    assert callable(render_recent_scan_case_detail_view)
    assert escape_value("<unsafe>") == "&lt;unsafe&gt;"


FORBIDDEN_DISPLAY_FRAGMENTS = (
    "/Users/",
    "/tmp/",
    "10.20.30.40",
    "case_dir",
    "internal_example_host",
    "BEGIN PROFILE",
    "Query Timeline",
    "SHOW CREATE TABLE",
    "raw stdout",
    "raw stderr",
    "CM_PASSWORD",
    "CM_TOKEN",
    "KRB5CCNAME",
    "metadata_coordinator",
    "metadata_auth",
    "metadata_path",
    "qwen",
    "ollama",
)


def assert_no_forbidden_fragments(value):
    text = repr(value)
    for fragment in FORBIDDEN_DISPLAY_FRAGMENTS:
        assert fragment not in text


def assert_contains_in_order(text: str, fragments: list[str]) -> None:
    cursor = 0
    for fragment in fragments:
        index = text.find(fragment, cursor)
        assert index >= 0, fragment
        cursor = index + len(fragment)


def render_typed_case_detail(case_id: str, case: dict[str, object]) -> str:
    return render_recent_scan_case_detail_view(present_recent_scan_case_detail(case_id, case))


def test_render_batch_summary_presents_scan_summary_once(monkeypatch):
    summary = {
        "cases": [
            recent_scan_contract_case(
                query_id="render-once",
                score=10,
                score_severity="suspicious",
                duration_sec=12,
            )
        ],
        "selected_count": 1,
        "summaries_inspected": 1,
    }
    original_presenter = recent_scan_results_ui.present_recent_scan_summary
    call_count = 0

    def counted_presenter(summary_payload, *, workload_outcome_metrics=None):
        nonlocal call_count
        call_count += 1
        return original_presenter(
            summary_payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )

    monkeypatch.setattr(
        recent_scan_results_ui,
        "present_recent_scan_summary",
        counted_presenter,
    )

    html = recent_scan_results_ui.render_batch_summary(summary, query_group="stats")

    assert call_count == 1
    assert "No medium/high stats-to-check candidates were found." in html


def test_render_batch_summary_paginates_large_result_groups():
    cases = [
        recent_scan_contract_case(
            case_index=index,
            query_id=f"query-{index:03d}",
            score=10,
            duration_sec=index,
        )
        for index in range(1, 261)
    ]
    summary = {
        "cases": cases,
        "selected_count": len(cases),
        "summaries_inspected": len(cases),
    }

    first_page = recent_scan_results_ui.render_batch_summary(summary, query_group="all")
    scoped_first_page = recent_scan_results_ui.render_batch_summary(
        summary,
        query_group="all",
        extra_query={
            "inbox_source": "cm",
            "inbox_workflow": "finished",
            "inbox_window": "60",
            "path": "/tmp/private",
        },
    )

    assert "Rows 1-250 of 260; page 1 of 2" in first_page
    assert first_page.count('data-href="/batch/case/') == 250
    assert 'href="/?query_group=all&amp;results_page=2#recent-results"' in first_page
    assert 'data-href="/batch/case/case-250"' in first_page
    assert 'data-href="/batch/case/case-251"' not in first_page

    second_page = recent_scan_results_ui.render_batch_summary(
        summary,
        query_group="all",
        results_page=2,
    )

    assert "Rows 251-260 of 260; page 2 of 2" in second_page
    assert second_page.count('data-href="/batch/case/') == 10
    assert 'href="/?query_group=all#recent-results"' in second_page
    assert 'data-href="/batch/case/case-250"' not in second_page
    assert 'data-href="/batch/case/case-251"' in second_page
    assert (
        recent_scan_results_ui.results_page_href(
            "all",
            2,
            only_with_spills=True,
            base_path="/running",
        )
        == "/running?query_group=all&only_with_spills=on&results_page=2#recent-results"
    )
    assert (
        recent_scan_results_ui.results_page_href(
            "all",
            2,
            only_with_spills=True,
            base_path="/running",
            extra_query={"inbox_source": "cm", "inbox_window": "60", "path": "/tmp/private"},
        )
        == "/running?query_group=all&inbox_source=cm&inbox_window=60"
        "&only_with_spills=on&results_page=2#recent-results"
    )
    assert (
        'href="/?query_group=all&inbox_source=cm&inbox_workflow=finished'
        '&inbox_window=60#recent-results"' in scoped_first_page
    )
    assert (
        'href="/?query_group=all&amp;inbox_source=cm&amp;inbox_workflow=finished'
        '&amp;inbox_window=60&amp;results_page=2#recent-results"' in scoped_first_page
    )
    assert "/tmp/private" not in scoped_first_page
    assert "path=" not in scoped_first_page


def primary_bottleneck_fixture(name: str):
    return json.loads((PRIMARY_BOTTLENECK_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def recent_scan_contract_case(**updates: object) -> dict[str, object]:
    case: dict[str, object] = {
        "user": "analyst",
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "ok",
        "score": 0,
        "duration_sec": 10,
        "cardinality_anomaly_count": 0,
        "memory_anomaly_count": 0,
        "zero_row_estimate_gap_count": 0,
        "zero_memory_estimate_gap_count": 0,
        "backend_data_skew": False,
        "host_tail_candidate_count": 0,
        "score_reasons": [],
    }
    case.update(updates)
    case["score_severity"] = recent_scan_contract_engine_score_severity(case)
    return case


def recent_scan_contract_engine_score_severity(case: dict[str, object]) -> str:
    result = CaseResult(
        index=1,
        query_id=str(case.get("query_id") or "contract-query"),
        duration_sec=float(case.get("duration_sec") or 0),
        user=str(case.get("user") or "analyst"),
        pool=None,
        query_type=None,
        sql_verb=None,
        wrapper_dir=Path("contract-case"),
    )
    result.collection_status = str(case.get("collection_status") or "ok")
    result.analysis_status = str(case.get("analysis_status") or "ok")
    result.metadata_status = str(case.get("metadata_status") or "ok")
    result.report_validation_status = str(case.get("report_validation_status") or "not_run")
    failure_category = case.get("failure_category")
    result.failure_category = str(failure_category) if failure_category else None
    result.score = int(case.get("score") or 0)
    result.cardinality_anomaly_count = int(case.get("cardinality_anomaly_count") or 0)
    result.memory_anomaly_count = int(case.get("memory_anomaly_count") or 0)
    result.zero_row_estimate_gap_count = int(case.get("zero_row_estimate_gap_count") or 0)
    result.zero_memory_estimate_gap_count = int(case.get("zero_memory_estimate_gap_count") or 0)
    result.backend_data_skew = bool(case.get("backend_data_skew"))
    result.host_tail_candidate_count = int(case.get("host_tail_candidate_count") or 0)
    return batch_case_score_severity(result)


def recent_scan_problematic_details_contract_cases() -> tuple[tuple[str, dict[str, object]], ...]:
    cases = (
        (
            "query_shape_candidate_high",
            recent_scan_contract_case(
                score=45,
                cardinality_anomaly_count=6,
                case_primary_bottleneck={
                    "label": "sql_shape",
                    "confidence": "medium",
                    "reasons": ["cardinality_anomalies_6"],
                },
                query_optimization_candidate={
                    "tier": "high",
                    "score": 82,
                    "impact": "high",
                    "confidence": "medium",
                    "reasons": ["join and exchange shape evidence"],
                    "suggested_review_areas": ["join order and pre-aggregation"],
                },
            ),
        ),
        (
            "stats_candidate_medium",
            recent_scan_contract_case(
                score=40,
                zero_row_estimate_gap_count=5,
                case_primary_bottleneck={
                    "label": "stats",
                    "confidence": "medium",
                    "reasons": ["stats_candidate_supported"],
                },
                stats_optimization_candidate={
                    "tier": "medium",
                    "score": 63,
                    "impact": "medium",
                    "confidence": "medium",
                    "need_type": "table_and_column_stats",
                    "speed_benefit": "medium",
                    "reasons": ["estimate mismatch with missing stats evidence"],
                    "suggested_review_areas": ["table stats and join columns"],
                    "required_confirmation": ["compare EXPLAIN estimates"],
                },
            ),
        ),
        (
            "admission_primary",
            recent_scan_contract_case(
                score=35,
                case_primary_bottleneck={
                    "label": "runtime_admission",
                    "confidence": "high",
                    "reasons": ["admission_wait_source_cm_query_context"],
                },
            ),
        ),
        (
            "sql_shape_low_candidate",
            recent_scan_contract_case(
                score=54,
                cardinality_anomaly_count=8,
                memory_anomaly_count=5,
                zero_row_estimate_gap_count=4,
                zero_memory_estimate_gap_count=3,
                backend_data_skew=True,
                host_tail_candidate_count=3,
                case_primary_bottleneck={
                    "label": "sql_shape",
                    "confidence": "medium",
                    "reasons": ["cardinality_anomalies_8"],
                },
                query_optimization_candidate={
                    "tier": "low",
                    "score": 25,
                    "impact": "low",
                    "confidence": "low",
                },
                stats_optimization_candidate={"tier": "not_likely", "score": 5},
            ),
        ),
        (
            "runtime_skew_primary",
            recent_scan_contract_case(
                score=38,
                backend_data_skew=True,
                host_tail_candidate_count=4,
                case_primary_bottleneck={
                    "label": "runtime_skew",
                    "confidence": "medium",
                    "reasons": ["tail_candidates_4"],
                },
            ),
        ),
        (
            "data_movement_primary",
            recent_scan_contract_case(
                score=32,
                cardinality_anomaly_count=4,
                case_primary_bottleneck={
                    "label": "runtime_data_movement",
                    "confidence": "medium",
                    "reasons": ["cardinality_anomalies_4"],
                },
            ),
        ),
        (
            "storage_primary",
            recent_scan_contract_case(
                score=31,
                case_primary_bottleneck={
                    "label": "runtime_storage",
                    "confidence": "low",
                    "reasons": ["tail_candidates_2"],
                },
            ),
        ),
        (
            "mixed_primary",
            recent_scan_contract_case(
                score=33,
                cardinality_anomaly_count=3,
                memory_anomaly_count=2,
                case_primary_bottleneck={
                    "label": "mixed",
                    "confidence": "low",
                    "reasons": ["cardinality_anomalies_3"],
                },
            ),
        ),
        (
            "failed_collection",
            recent_scan_contract_case(
                collection_status="failed",
                failure_reason="Profile collection command failed before a profile digest was produced.",
            ),
        ),
        ("failed_analysis", recent_scan_contract_case(analysis_status="failed")),
        ("failed_metadata", recent_scan_contract_case(metadata_status="failed")),
        (
            "failed_report",
            recent_scan_contract_case(report_validation_status="failed"),
        ),
        (
            "failure_category_only",
            recent_scan_contract_case(failure_category="metadata_collection_failed"),
        ),
        ("high_score_only", recent_scan_contract_case(score=35)),
        ("suspicious_score_only", recent_scan_contract_case(score=5)),
    )
    return tuple(
        (
            name,
            {
                **case,
                "case_index": index,
                "query_id": f"{name}:id",
            },
        )
        for index, (name, case) in enumerate(cases, start=1)
    )


def assert_problematic_detail_contract(name: str, case_id: str, case: dict[str, object]) -> None:
    view = present_recent_scan_case_detail(case_id, case)
    action_view = present_recent_scan_action_candidates(view)
    score_view = present_recent_scan_score_reasons(view)
    html = render_recent_scan_case_detail_view(view)

    assert view.score_severity in {"failed", "high", "suspicious"}, name
    assert action_view.cards, name
    assert score_view.reasons, name
    assert "No prioritized rewrite or stats action" not in html, name
    assert "No positive deterministic score reasons" not in html, name
    assert "Where to inspect" in html, name
    if view.score_severity == "failed":
        assert (
            '<h2 class="case-verdict-title">Processing did not finish - '
            "diagnosis is not trustworthy yet</h2>" in html
        ), name
        assert "No supported problem signal is classified yet" not in html, name
        assert "Supported analyzer signals need review" not in html, name
        failure_reason = str(case.get("failure_reason") or "").strip()
        if failure_reason:
            assert failure_reason in html, name
            assert any(
                failure_reason in str(getattr(reason, "title", ""))
                or failure_reason in str(getattr(reason, "explanation", ""))
                for reason in score_view.reasons
            ), name
    for card in action_view.cards:
        assert card.why.strip(), name
        assert card.change_direction.strip(), name
        assert card.verification.strip(), name
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(score_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_summary_view_empty_scan_message_and_scope():
    view = present_recent_scan_summary(
        {
            "selected_count": 0,
            "summaries_inspected": 0,
            "duration_filter": None,
            "recent_window_minutes": 120,
            "from_time": "2026-05-02T21:00:00Z",
            "to_time": "2026-05-02T22:00:00Z",
            "query_type_filter": "QUERY",
            "include_failed": False,
            "include_running": False,
            "duration_filter_mode": "server-side",
            "cm_inspect_limit": 5000,
            "metadata_top_limit": 3,
            "candidate_exclusion_count": 4,
            "candidate_reason_counts": {
                "excluded: not analyzable query text": 3,
                "excluded: failed query": 1,
            },
            "candidate_reason_sql_verb_counts": {
                "excluded: not analyzable query text": {
                    "unknown": 1,
                    "CREATE": 2,
                    "ALTER": 4,
                    "DROP": 3,
                },
            },
            "warnings": ["CM returned no matching query summaries"],
            "cases": [],
        }
    )

    assert (
        view.empty_message
        == "No matching queries found for this hour bucket. Try another hour or changing filters."
    )
    assert "Query summaries inspected: 0" in view.scope_parts
    assert "Duration filter: none" in view.scope_parts
    assert "Duration filtering: server-side" in view.scope_parts
    assert "Scan time window: 2026-05-02T21:00:00Z -> 2026-05-02T22:00:00Z" in view.scope_parts
    assert "Metadata budget: up to 3 bad/suspicious cases" in view.scope_parts
    assert "CM match limit: 5000" not in view.scope_parts
    assert "User filter: all users" in view.scope_parts
    assert "Pool filter: all pools" in view.scope_parts
    assert "Analyzed queries: 0" in view.scope_parts
    assert "Excluded before analysis: 4" in view.scope_parts
    assert (
        "Top exclusions: not analyzable query text: 3 (ALTER 4, DROP 3, CREATE 2, other 1); failed query: 1"
        in view.scope_parts
    )
    assert view.warning_messages == ("CM returned no matching query summaries",)
    assert view.rows == ()


def test_recent_scan_summary_view_source_timeout_is_not_empty_match_message():
    view = present_recent_scan_summary(
        {
            "selected_count": 0,
            "summaries_inspected": 0,
            "duration_filter": None,
            "warnings": ["CM request failed: <urlopen error [Errno 60] Operation timed out>"],
            "cases": [],
        }
    )

    assert "could not read CM query summaries" in view.empty_message
    assert "does not prove that no queries exist" in view.empty_message
    assert "No matching queries found" not in view.empty_message
    assert view.warning_messages == (
        "CM request failed: <urlopen error [Errno 60] Operation timed out>",
    )


def test_recent_scan_summary_view_labels_unfiltered_query_type():
    view = present_recent_scan_summary(
        {
            "selected_count": 0,
            "summaries_inspected": 0,
            "duration_filter": None,
            "query_type_filter": "all",
            "cases": [],
        }
    )

    assert "Query type: all supported" in view.scope_parts
    assert "Query type: QUERY" not in view.scope_parts


def test_recent_scan_summary_view_discovery_failure_is_clear_and_sanitized():
    view = present_recent_scan_summary(
        {
            "selected_count": 0,
            "summaries_inspected": 0,
            "duration_filter": "none",
            "discovery_failed": True,
            "warnings": ["raw stdout /tmp/case CM_PASSWORD"],
            "cases": [],
        }
    )

    assert view.empty_message == (
        "Recent scan discovery failed before case selection. Check CM connectivity and access settings, then run again."
    )
    assert view.warning_messages == (
        "[subprocess output hidden] <local path hidden> [hidden field]",
    )
    assert_no_forbidden_fragments(view)


def test_recent_scan_summary_view_warnings_hide_infrastructure_identifiers():
    view = present_recent_scan_summary(
        {
            "selected_count": 0,
            "summaries_inspected": 0,
            "duration_filter": "none",
            "warnings": [
                "Coordinator: impalad-01.example.org "
                "backend=10.20.30.40 user=example_analyst owner=example_analyst@example.com",
            ],
            "cases": [],
        }
    )

    assert len(view.warning_messages) == 1
    warning = view.warning_messages[0]
    for fragment in (
        "impalad-01.example.org",
        "10.20.30.40",
        "user=example_analyst",
        "example_analyst@example.com",
    ):
        assert fragment not in warning
    assert "Coordinator: host_01" in warning
    assert "backend=host_02" in warning
    assert "user=<user>" in warning
    assert "owner=<email>" in warning
    assert_no_forbidden_fragments(view)


def test_recent_scan_case_row_view_sanitizes_forbidden_values_and_statuses():
    view = present_recent_scan_case_row(
        1,
        {
            "case_index": 1,
            "query_id": "abc /Users/example/case_dir",
            "score": 42,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
            "report_generated": True,
            "report_validation_status": "failed_partial_untrusted",
            "score_reasons": [
                "BEGIN PROFILE Query Timeline raw stdout raw stderr /tmp/case CM_TOKEN qwen3-coder ollama",
            ],
        },
    )

    assert view.case_id == "case-001"
    assert view.report_status == "partial untrusted"
    assert view.score_value == 42
    assert view.has_failure is False
    assert (
        view.status_summary
        == "collection ok; analysis ok; metadata skipped; report partial untrusted"
    )
    assert view.signal_summary == "positive score from detailed analyzer reasons"
    assert_no_forbidden_fragments(view)


def test_recent_scan_case_row_view_adds_compact_signal_summary():
    view = present_recent_scan_case_row(
        2,
        {
            "case_index": 2,
            "query_id": "def",
            "score": 9,
            "cardinality_anomaly_count": 3,
            "memory_anomaly_count": 2,
            "backend_data_skew": True,
            "host_tail_candidate_count": 1,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "table_stats_status": "available",
            "report_generated": True,
            "report_validation_status": "passed",
        },
    )

    assert view.signal_summary == "cardinality 3; memory 2; skew observed; host-tail 1"
    assert (
        view.status_summary
        == "collection ok; analysis ok; metadata collected; report validated report"
    )
    assert_no_forbidden_fragments(view)


def test_recent_scan_case_row_view_surfaces_profile_worker_failure_code():
    view = present_recent_scan_case_row(
        4,
        {
            "case_index": 4,
            "query_id": "retry",
            "score": 0,
            "collection_status": "summary_history",
            "analysis_status": "profile_retry_pending",
            "metadata_status": "not_collected",
            "failure_category": "profile_fetch_http_503",
        },
    )

    assert view.signal_summary == "profile processing failure profile_fetch_http_503"
    assert_no_forbidden_fragments(view)


def test_recent_scan_case_row_view_adds_primary_bottleneck_summary():
    view = present_recent_scan_case_row(
        1,
        {
            "case_index": 1,
            "query_id": "abc",
            "score": 31,
            "cardinality_anomaly_count": 4,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "case_primary_bottleneck": {
                "label": "stats",
                "confidence": "high",
                "reasons": ["stats_candidate_supported", "cardinality_anomalies_4"],
            },
        },
    )

    assert view.primary_bottleneck.summary == "Stats (High confidence)"
    assert view.primary_bottleneck.reason_summary == (
        "stats gaps match estimate-mismatch evidence; 4 cardinality anomalies"
    )
    assert_no_forbidden_fragments(view)


def test_recent_scan_metadata_view_available_normalizes_statement_labels():
    view = present_recent_scan_metadata(
        {
            "metadata_status": "collected",
            "referenced_table_count": 1,
            "collected_metadata_table_count": 1,
            "too_large_count": 0,
            "score_reasons": ["SHOW CREATE TABLE stats metadata /Users/example"],
        },
        {
            "statement_counts": {"ok": 1, "error": 1, "not_applicable": 0, "too_large": 0},
            "tables": [
                {
                    "table": "/tmp/raw-table",
                    "object type": "view",
                    "statements": {
                        "SHOW CREATE TABLE": "error",
                        "SHOW TABLE STATS": "not_applicable",
                        "SHOW COLUMN STATS": "ok",
                    },
                    "table stats row-count completeness": "missing/unknown",
                    "column stats completeness": "available",
                    "column stats columns observed": "-1",
                    "column stats missing/unknown markers": "-1",
                    "column stats NDV-missing columns": "1",
                    "column stats size-missing columns": "1",
                    "column stats all-missing columns": "0",
                    "partition columns": "SHOW CREATE TABLE",
                    "partition count": 5,
                    "partitions with known row count": 4,
                    "partitions with unknown row count": 1,
                    "partitions with zero row count": 2,
                    "file format": "qwen-model",
                }
            ],
        },
    )

    assert view.unavailable is False
    assert ("metadata coverage", "metadata command errors present") in view.summary_items
    assert view.summary_items[-2][0] == "metadata command status"
    assert view.summary_items[-1][0] == "stats coverage"
    assert view.tables[0].statements == {
        "create metadata": "error",
        "table stats": "not_applicable",
        "column stats": "ok",
    }
    assert view.tables[0].observed_columns == "unknown"
    assert view.tables[0].missing_markers == "unknown"
    assert "create metadata: error" in view.tables[0].limitations
    assert "table stats: not_applicable" in view.tables[0].limitations
    assert "partition row counts: 4/5 known, 1 unknown, 2 zero" in view.tables[0].limitations
    assert "column stats detail: 1 NDV missing, 1 size missing" in view.tables[0].limitations
    assert "-1" not in render_metadata_fact_table_row_view(view.tables[0])
    assert_no_forbidden_fragments(view)


def test_recent_scan_metadata_view_renderers_use_typed_view_models():
    case = {
        "metadata_status": "collected",
        "referenced_table_count": 1,
        "collected_metadata_table_count": 1,
    }
    metadata = {
        "statement_counts": {"ok": 2, "error": 0, "not_applicable": 0, "too_large": 0},
        "tables": [
            {
                "table": "example_analytics.safe_table",
                "object type": "table",
                "statements": {
                    "SHOW CREATE TABLE": "ok",
                    "SHOW TABLE STATS": "ok",
                    "SHOW COLUMN STATS": "ok",
                },
                "table stats row-count completeness": "available",
                "column stats completeness": "available",
            }
        ],
    }
    view = present_recent_scan_metadata(case, metadata)
    equivalent_view = present_recent_scan_metadata(case, metadata)

    assert render_metadata_facts_view(view) == render_metadata_facts_body(view)
    assert render_metadata_facts_view(view) == render_metadata_facts_body(equivalent_view)
    assert render_metadata_fact_table_row_view(
        view.tables[0]
    ) == render_metadata_fact_table_row_view(equivalent_view.tables[0])


def test_web_metadata_fact_parsers_normalize_stats_placeholders():
    metadata = parse_table_metadata_context_facts(
        "\n".join(
            [
                "## Table Metadata Context",
                "",
                "### Table: db.fact",
                "",
                "- SHOW TABLE STATS status: ok",
                "- table stats rows: -1",
                "- table stats row-count completeness: -1",
                "- table stats size: NULL",
                "- column stats columns observed: -1",
                "- column stats missing/unknown markers: N/A",
                "- column stats completeness: NULL",
                "",
                "## Stats Metadata Quality",
                "",
                "- status: -1",
                "- table_stats: -1",
                "- column_stats: NULL",
                "- non_stats_bottleneck_categories: none",
                "- interpretation: no raw -1 placeholder should be normalized inside prose",
                "- guardrail: keep prose as written",
            ]
        )
    )
    stats = parse_stats_quality_facts(
        "\n".join(
            [
                "## Stats Metadata Quality",
                "",
                "- status: -1",
                "- table_stats: -1",
                "- column_stats: NULL",
                "- non_stats_bottleneck_categories: none",
                "- interpretation: no raw -1 placeholder should be normalized inside prose",
                "- guardrail: keep prose as written",
            ]
        )
    )

    table = metadata["tables"][0]
    assert table["table stats rows"] == "unknown"
    assert table["table stats row-count completeness"] == "unknown"
    assert table["table stats size"] == "unknown"
    assert table["column stats columns observed"] == "unknown"
    assert table["column stats missing/unknown markers"] == "unknown"
    assert table["column stats completeness"] == "unknown"
    assert stats["status"] == "unknown"
    assert stats["table_stats"] == "unknown"
    assert stats["column_stats"] == "unknown"
    assert stats["non_stats_bottleneck_categories"] == "none"
    assert stats["interpretation"] == "no raw -1 placeholder should be normalized inside prose"
    assert stats["guardrail"] == "keep prose as written"


def test_recent_scan_metadata_view_partial_and_skipped_states_are_clear():
    partial_view = present_recent_scan_metadata(
        {
            "metadata_status": "partial",
            "referenced_table_count": 2,
            "collected_metadata_table_count": 1,
            "too_large_count": 1,
            "score_reasons": ["metadata collection failed"],
        },
        None,
    )
    skipped_view = present_recent_scan_metadata({"metadata_status": "skipped"}, None)

    assert partial_view.unavailable is False
    assert "Safe aggregate metadata facts" in partial_view.fallback_note
    assert "batch_summary.json" not in partial_view.fallback_note
    assert ("metadata status", "partial") in partial_view.summary_items
    assert ("metadata coverage", "partial; no table rows available") in partial_view.summary_items
    assert ("metadata coverage", "not collected for this case") in skipped_view.summary_items
    assert skipped_view.unavailable is True

    partial_html = render_metadata_facts_body(partial_view)
    assert "Metadata collection was partial." in partial_html
    assert (
        "Profile-based findings remain valid; metadata evidence for follow-up may be limited."
        in partial_html
    )


def test_recent_scan_metadata_view_distinguishes_not_applicable_and_empty_collected_states():
    not_applicable_view = present_recent_scan_metadata(
        {
            "metadata_status": "collected",
            "referenced_table_count": 1,
            "collected_metadata_table_count": 1,
        },
        {
            "statement_counts": {"ok": 1, "error": 0, "not_applicable": 2, "too_large": 0},
            "tables": [{"table": "db.safe_view", "object type": "view", "statements": {}}],
        },
    )
    empty_collected_view = present_recent_scan_metadata(
        {
            "metadata_status": "collected",
            "referenced_table_count": 1,
            "collected_metadata_table_count": 1,
        },
        {"statement_counts": {}, "tables": []},
    )
    not_attempted_view = present_recent_scan_metadata({"metadata_status": "not_attempted"}, None)

    assert (
        "metadata coverage",
        "some metadata commands not applicable",
    ) in not_applicable_view.summary_items
    assert "not a missing-stats signal by itself" in render_metadata_facts_body(not_applicable_view)
    assert (
        "metadata coverage",
        "collected status but no table rows available",
    ) in empty_collected_view.summary_items
    assert "Treat stats coverage as unknown" in render_metadata_facts_body(empty_collected_view)
    assert ("metadata coverage", "not requested for this case") in not_attempted_view.summary_items
    assert not_attempted_view.unavailable is True


def test_recent_scan_case_detail_view_report_action_and_safe_fields():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "failed",
            "failure_category": "raw stderr /tmp/failure",
            "score_reasons": ["metadata failed"],
        },
        None,
        report_state={
            "status": "partial_untrusted",
            "partial": True,
            "trusted": False,
            "error": "report failed at /Users/example/case_dir with CM_PASSWORD",
        },
    )

    assert view.report_status == "partial untrusted"
    assert view.report_action.partial_untrusted is True
    assert view.report_action.show_open_link is False
    assert view.query_id == "abc"
    assert view.signal_summary == "no positive analyzer signals"
    assert (
        view.status_summary
        == "collection ok; analysis ok; metadata failed; report partial untrusted"
    )
    assert view.has_spill is False
    assert_no_forbidden_fragments(view)


def test_recent_scan_case_detail_view_renderer_uses_typed_view_model():
    case = {
        "query_id": "abc",
        "score": 8,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "score_reasons": ["cardinality estimate anomalies: 2"],
    }
    metadata_facts = {
        "summary": {"status": "available", "table_stats": "available"},
        "tables": [],
    }
    view = present_recent_scan_case_detail("case-001", case, metadata_facts)

    view_html = render_recent_scan_case_detail_view(view)

    assert "Finished Queries details" in view_html
    assert "Jump to section" not in view_html
    assert '<section id="case-overview" class="case-verdict"' in view_html
    assert "Verdict" in view_html
    assert "Start with the recommendation below" not in view_html
    assert '<section id="action-plan"' in view_html
    assert "Recommended change" in view_html
    assert "cardinality estimate anomalies" in view_html
    assert_no_forbidden_fragments(view_html)


def test_recent_scan_case_detail_view_keeps_static_labels_english_with_russian_body():
    case = {
        "query_id": "abc",
        "score": 8,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "score_reasons": ["cardinality estimate anomalies: 2"],
        "case_primary_bottleneck": {
            "label": "sql_shape",
            "confidence": "medium",
            "reasons": ["cardinality_anomalies_2"],
        },
        "query_optimization_candidate": {
            "tier": "medium",
            "score": 67,
            "impact": "medium",
            "confidence": "medium",
            "reasons": ["query-shape evidence"],
            "suggested_review_areas": ["join order"],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    view_html = render_recent_scan_case_detail_view(view, language="ru")

    assert "Finished Queries details" in view_html
    assert "Verdict" in view_html
    assert "Recommended change" in view_html
    assert "Diagnostics and evidence" in view_html
    assert "Reports and optimizer" in view_html
    assert "Детали завершенного запроса" not in view_html
    assert "Завершенные запросы детали" not in view_html
    assert "Вердикт" not in view_html
    assert "Рекомендуемое изменение" not in view_html
    assert "Диагностика и доказательства" not in view_html
    assert "Отчеты и оптимизатор" not in view_html
    assert "Query-shape recommendation" in view_html
    assert "Сила кандидата:" in view_html
    assert "Проверьте join order" in view_html
    assert "Сравните EXPLAIN до и после изменения" in view_html
    assert "Runtime profile содержит" in view_html
    assert "Review join order" not in view_html
    assert "Compare EXPLAIN before and after the change" not in view_html
    assert "Strong: analyzer findings with metadata context" in view_html
    assert "Рекомендация по форме запроса" not in view_html
    assert "Candidate strength:" not in view_html
    assert_no_forbidden_fragments(view_html)


def test_recent_scan_results_table_keeps_header_english_and_localizes_finding_body():
    summary = {
        "selected_count": 1,
        "summaries_inspected": 1,
        "cases": [
            {
                "case_index": 1,
                "query_id": "abc",
                "user": "analyst",
                "score": 40,
                "duration_sec": 30,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "collected",
                "score_reasons": ["cardinality estimate anomalies: 2"],
                "case_primary_bottleneck": {
                    "label": "sql_shape",
                    "confidence": "medium",
                    "reasons": ["cardinality_anomalies_2"],
                },
            }
        ],
    }

    html = render_batch_summary(summary, query_group="bad", language="ru")

    assert "<th>Finding</th>" in html
    assert "<th>Сигнал</th>" not in html
    assert "SQL shape (средняя уверенность)" in html
    assert "Основной сигнал:" not in html
    assert "аномалии оценки строк" not in html
    assert "Primary:" not in html
    assert "cardinality estimate anomalies" not in html
    assert_no_forbidden_fragments(html)


def test_query_shape_recommendation_localizes_russian_body_copy_only():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "abc",
            "score": 8,
            "score_severity": "suspicious",
            "_detail_optimization_rank": 1,
            "query_optimization_candidate": {
                "score": 88,
                "tier": "high",
                "confidence": "medium",
                "impact": "high",
                "reasons": ["join row expansion or cardinality mismatch with join evidence"],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
            "source_locators": {
                "query_optimization": [
                    {
                        "id": "sql_final_select_filter",
                        "coordinate": "line 18",
                        "detail": "predicate near final SELECT",
                    },
                    {
                        "id": "plan_cardinality_anomaly",
                        "detail": "node 02 HASH JOIN",
                    },
                ]
            },
        },
    )

    html = render_action_candidate_decision_findings(view, language="ru")

    assert "Query-shape recommendation" in html
    assert "What to try" in html
    assert "How to verify" in html
    assert "Попробуйте уменьшить количество строк раньше" in html
    assert "Используйте отмеченный estimate-mismatch operator" in html
    assert "Сравните EXPLAIN до и после изменения" in html
    assert "затем выполните сопоставимый повторный запуск" in html
    assert "Сила кандидата: Высокое. Оценка: 88/100." in html
    assert '<div class="action-candidate-meta"' in html
    assert '<details class="analysis-subdetails action-candidate-meta"' not in html
    assert "Try to reduce rows earlier" not in html
    assert "after the change, check whether fewer rows" not in html
    assert "Compare EXPLAIN before and after the change" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_detail_view_without_trusted_artifacts_renders_no_artifact_blocks():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "collection_status": "ok",
            "analysis_status": "ok",
            "report_status": "not_run",
            "metadata_status": "not_collected",
        },
    )

    view_html = render_recent_scan_case_detail_view(
        view,
        trusted_report_html=None,
        trusted_optimized_query=None,
        trusted_optimizer_recommendations=None,
        optimizer_manual_guidance=None,
    )

    assert "inline-report" not in view_html
    assert "LLM report result" not in view_html
    assert "optimized-query-copy" not in view_html
    assert "Validated SQL draft" not in view_html
    assert_no_forbidden_fragments(view_html)


def test_recent_scan_action_plan_includes_trusted_optimizer_recommendations():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": ["cardinality estimate anomalies: 2"],
        },
    )

    view_html = render_recent_scan_case_detail_view(
        view,
        optimized_query_state=present_optimized_query_action(
            {
                "status": "generated",
                "output_kind": "recommendations_only",
                "risk_mode": "recommendations_only",
                "source_scope": "read_only_statement",
            }
        ),
        trusted_optimizer_recommendations="- Review the join/filter placement.",
    )
    action_plan_html = view_html.split('<section id="action-plan"', 1)[1].split(
        '<section id="diagnostics"', 1
    )[0]

    assert "Optimizer recommendations" in action_plan_html
    assert "Review the join/filter placement." in action_plan_html
    assert 'href="#query-optimizer-result"' in action_plan_html
    assert "Query LLM optimizer recommendations" in view_html
    assert_no_forbidden_fragments(view_html)


def test_recent_scan_action_plan_links_trusted_optimizer_draft_without_sql():
    source_sql = "SELECT secret_customer_id FROM production_table"
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": ["cardinality estimate anomalies: 2"],
        },
    )

    view_html = render_recent_scan_case_detail_view(
        view,
        optimized_query_state=present_optimized_query_action(
            {
                "status": "generated",
                "output_kind": "sql_draft",
                "risk_mode": "rewrite_allowed",
                "source_scope": "read_only_statement",
            }
        ),
        trusted_optimized_query=source_sql,
    )
    action_plan_html = view_html.split('<section id="action-plan"', 1)[1].split(
        '<section id="diagnostics"', 1
    )[0]

    assert "Validated optimizer draft available" in action_plan_html
    assert "Open optimizer result" in action_plan_html
    assert 'href="#query-optimizer-result"' in action_plan_html
    assert source_sql not in action_plan_html
    assert source_sql in view_html


def test_recent_scan_status_summary_view_renderer_matches_legacy_adapter():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "collection_status": "ok",
            "analysis_status": "failed",
            "metadata_status": "partial",
            "score_reasons": [],
        },
        report_state={
            "status": "partial_untrusted",
            "partial": True,
            "trusted": False,
        },
    )
    status_view = present_recent_scan_status_summary(view)

    assert render_case_status_summary_view(status_view) == render_case_status_summary(view)
    assert "LLM report" in render_case_status_summary_view(status_view)
    assert_no_forbidden_fragments(status_view)
    assert_no_forbidden_fragments(render_case_status_summary_view(status_view))


def test_recent_scan_score_reason_view_renderers_use_typed_presenters():
    raw_reason = (
        "metadata collection failed SHOW CREATE TABLE raw stderr "
        "/Users/example/case_dir qwen3-coder"
    )
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "failed",
            "score_reasons": [raw_reason],
        },
    )
    score_reasons_view = present_recent_scan_score_reasons(view)
    score_reason_view = present_recent_scan_score_reason(raw_reason)

    assert render_score_reason_explanations_view(
        score_reasons_view
    ) == render_score_reason_explanations(view)
    assert "Metadata collection failed" in render_score_reason_card_view(score_reason_view)
    assert_no_forbidden_fragments(score_reasons_view)
    assert_no_forbidden_fragments(score_reason_view)
    assert_no_forbidden_fragments(render_score_reason_explanations(view))


def test_bad_recent_scan_details_explain_attention_without_raw_score_reasons():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 27,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "partial",
            "score_reasons": [],
            "cardinality_anomaly_count": 3,
            "memory_anomaly_count": 2,
            "backend_data_skew": True,
            "case_primary_bottleneck": {
                "label": "sql_shape",
                "confidence": "low",
                "reasons": ["join_top_finding"],
            },
        },
    )

    score_reasons_view = present_recent_scan_score_reasons(view)
    html = render_score_reason_explanations(view)

    assert view.score_severity == "high"
    assert "No positive deterministic score reasons" not in html
    assert [reason.title for reason in score_reasons_view.reasons[:3]] == [
        "cardinality estimate anomalies: 3",
        "memory estimate anomalies: 2",
        "backend data skew evidence",
    ]
    assert "Runtime profile contains operators where estimated rows diverge" in html
    assert "Primary bottleneck classification selected the safest review direction" in html
    assert_no_forbidden_fragments(score_reasons_view)
    assert_no_forbidden_fragments(html)


def test_failed_recent_scan_details_explain_processing_follow_up():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "collection_status": "ok",
            "analysis_status": "failed",
            "metadata_status": "partial",
            "failure_reason": "Deterministic analysis or metadata collection failed before it completed.",
            "score_reasons": [],
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    actions_html = render_action_candidate_findings(view)
    page_html = render_recent_scan_case_detail_view(view)
    score_html = render_score_reason_explanations(view)

    assert view.score_severity == "failed"
    assert len(action_view.cards) == 1
    assert action_view.cards[0].title == "Processing failure follow-up"
    assert action_view.cards[0].recommendation_id == "processing_failure_follow_up.v1"
    assert "analysis failed" in action_view.cards[0].body
    assert "Deterministic analysis or metadata collection failed before it completed." in (
        action_view.cards[0].body
    )
    assert "Deterministic analysis or metadata collection failed before it completed." in score_html
    assert "not by a root-cause diagnosis" in action_view.cards[0].why
    assert (
        '<h2 class="case-verdict-title">Processing did not finish - diagnosis is not trustworthy yet</h2>'
        in page_html
    )
    assert "No supported problem signal is classified yet" not in page_html
    assert "Supported analyzer signals need review" not in page_html
    assert "Re-run analysis successfully before generating reports for this case." in page_html
    assert "Generate LLM report" not in page_html
    assert "Processing failure follow-up" in actions_html
    assert "No prioritized rewrite or stats action" not in page_html
    assert "No positive deterministic score reasons" not in score_html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(actions_html)
    assert_no_forbidden_fragments(page_html)


def test_failed_collection_verdict_overrides_positive_analyzer_signals():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 40,
            "collection_status": "failed",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": ["cardinality estimate anomalies: 6"],
            "cardinality_anomaly_count": 6,
            "query_optimization_candidate": {
                "tier": "high",
                "score": 82,
                "impact": "high",
                "confidence": "medium",
            },
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    page_html = render_recent_scan_case_detail_view(view)

    assert view.score_severity == "failed"
    assert len(action_view.cards) == 1
    assert action_view.cards[0].title == "Processing failure follow-up"
    assert "collection failed" in action_view.cards[0].body
    assert (
        '<h2 class="case-verdict-title">Processing did not finish - diagnosis is not trustworthy yet</h2>'
        in page_html
    )
    assert "Query-shape recommendation" not in page_html
    assert "Generate LLM report" not in page_html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(page_html)


def test_problematic_recent_scan_details_have_complete_user_path_contract():
    for name, case in recent_scan_problematic_details_contract_cases():
        case_id = f"case-{case['case_index']:03d}"
        assert_problematic_detail_contract(name, case_id, case)


def test_recent_results_problematic_rows_link_to_contract_compliant_details():
    cases = [case for _name, case in recent_scan_problematic_details_contract_cases()]
    summary = {
        "selected_count": len(cases),
        "summaries_inspected": len(cases),
        "cases": cases,
    }
    bad_html = render_batch_summary(summary, query_group="bad")
    suspicious_html = render_batch_summary(summary, query_group="suspicious")

    for name, case in recent_scan_problematic_details_contract_cases():
        case_id = f"case-{case['case_index']:03d}"
        view = present_recent_scan_case_detail(case_id, case)
        expected_row = bad_html if view.score_severity in {"failed", "high"} else suspicious_html
        assert str(case["query_id"]) in expected_row, name
        assert f'data-href="/batch/case/{case_id}"' in expected_row, name
        assert_problematic_detail_contract(name, case_id, case)


def test_recent_scan_case_detail_runtime_verdict_uses_cluster_context_safely():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
        },
        runtime_diagnosis_facts={
            "status": "available",
            "summary": "Network/exchange pressure is the strongest plausible follow-up hypothesis from deterministic facts.",
        },
        cluster_runtime_context_facts={
            "summary": {
                "status": "available",
                "collection_status": "collected",
                "coverage": "4/4 metrics ok, 40 points",
                "scoring_contribution": "+4 triage score points from 2 correlated CM metric signal(s), capped at +6",
            },
            "signal_rollup": {
                "correlated_signals": "Daemon memory growth, Network I/O spike",
                "context_only_signals": "Admission/pool pressure",
            },
            "limitations": ["CM metrics are bounded context only."],
        },
    )

    assert view.runtime_verdict.title == "Correlated runtime context"
    assert view.runtime_verdict.badge_class == "batch-status--warning"
    assert "not standalone root-cause proof" in view.runtime_verdict.summary
    assert (
        "correlated signals: Daemon memory growth, Network I/O spike"
        in view.runtime_verdict.reasons
    )
    assert "coverage: 4/4 metrics ok, 40 points" in view.runtime_verdict.reasons
    assert_no_forbidden_fragments(view.runtime_verdict)


def test_recent_scan_case_detail_runtime_verdict_handles_missing_context():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
        },
    )

    assert view.runtime_verdict.title == "Runtime context not collected"
    assert view.runtime_verdict.badge_class == "batch-status--neutral"
    assert "Cloudera Manager" not in view.runtime_verdict.summary
    assert (
        "Profile and metadata findings remain the primary evidence" in view.runtime_verdict.summary
    )


def test_recent_scan_case_detail_runtime_context_shows_prometheus_coverage():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
        },
        cluster_runtime_context_facts={
            "summary": {
                "status": "available",
                "source_label": "Prometheus runtime metrics",
                "collection_status": "collected",
                "coverage": "14/14 metrics ok, 1461 points",
                "metrics_profile": "ambari-hadoop",
            },
            "signal_rollup": {"not_observed_signals": "Network I/O spike"},
        },
    )

    assert (
        "source_label",
        "Prometheus runtime metrics",
    ) in view.cluster_runtime_context.summary_items
    assert (
        "coverage",
        "14/14 metrics ok, 1461 points",
    ) in view.cluster_runtime_context.summary_items
    html = render_cluster_runtime_context_section(view.cluster_runtime_context)
    assert "Prometheus runtime metrics" in html
    assert "14/14 metrics ok, 1461 points" in html
    assert_no_forbidden_fragments(view.cluster_runtime_context)
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_detail_renders_data_movement_evidence_safely():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 33,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
        },
        data_movement_facts={
            "summary": {
                "status": "supported",
                "evidence_tier": "strong",
                "finding_supported": "yes",
                "primary_supported": "yes",
                "total_bytes_sent": "17.00 GiB",
                "exchange_operator_count": "4",
                "exchange_elapsed": "8m 10s",
                "exchange_elapsed_share": "73.0%",
                "guardrail": "Data movement facts require deterministic profile support.",
            },
            "limitations": ["Stability labels are not available for this profile."],
        },
    )

    html = render_data_movement_evidence_section(view.data_movement)
    detail_html = render_recent_scan_case_detail_view(view)

    assert not view.data_movement.unavailable
    assert ("evidence tier", "strong") in view.data_movement.summary_items
    assert '<details class="analysis-subdetails" aria-label="Data movement evidence">' in html
    assert "finding supported" in html
    assert "primary supported" in html
    assert "exchange elapsed share" in html
    assert "73.0%" in html
    assert "Stability labels are not available" in html
    assert (
        '<details class="analysis-subdetails" aria-label="Data movement evidence">' in detail_html
    )
    assert_no_forbidden_fragments(view.data_movement)
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_detail_evidence_labels_summarize_safe_next_action():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 41,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "cardinality_anomaly_count": 3,
            "memory_anomaly_count": 2,
            "score_reasons": ["cardinality estimate mismatch"],
            "query_optimization_candidate": {
                "score": 82,
                "tier": "high",
                "impact": "high",
                "confidence": "medium",
                "reasons": ["large exchange/intermediate data movement"],
                "suggested_review_areas": ["exchange payload"],
            },
        },
        {
            "statement_counts": {"ok": 2, "error": 1, "not_applicable": 0, "too_large": 0},
            "tables": [
                {
                    "table": "db.fact_sales",
                    "object type": "table",
                    "statements": {
                        "SHOW CREATE TABLE": "ok",
                        "SHOW TABLE STATS": "error",
                        "SHOW COLUMN STATS": "ok",
                    },
                }
            ],
        },
        runtime_diagnosis_facts={
            "status": "available",
            "summary": "Network/exchange pressure is supported as a follow-up hypothesis.",
        },
        cluster_runtime_context_facts={
            "summary": {
                "status": "available",
                "collection_status": "collected",
                "coverage": "4/4 metrics ok",
            },
            "signal_rollup": {"correlated_signals": "Network I/O spike"},
        },
    )

    metadata_summary = dict(view.metadata.summary_items)

    assert view.signal_summary == "cardinality 3; memory 2"
    assert view.runtime_verdict.title == "Correlated runtime context"
    assert metadata_summary["metadata command status"] == (
        "2 ok / 1 error / 0 not_applicable / 0 too_large"
    )
    assert evidence_stats_label(view) == "No Medium/High stats-refresh candidate"
    assert_no_forbidden_fragments(view)


def test_recent_scan_case_detail_evidence_labels_are_raw_free():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc /tmp/case_dir",
            "score": 33,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "table_stats_status": "missing",
            "score_reasons": [
                "BEGIN PROFILE Query Timeline SHOW CREATE TABLE raw stdout raw stderr "
                "/Users/example/case_dir CM_TOKEN qwen3-coder ollama",
            ],
            "case_primary_bottleneck": {
                "label": "SELECT * FROM example_guarded_table",
                "confidence": "high",
                "reasons": [
                    "operator_02 SELECT secret_col FROM example_guarded_table /tmp/raw qwen3-coder raw stderr"
                ],
            },
        },
        evidence_quality_facts={
            "score": "88 /tmp/case_dir",
            "level": "high qwen3-coder raw stdout",
        },
        stats_quality_facts={
            "status": "available raw stderr",
            "stats_context": "stats_present_with_row_estimate_evidence",
            "interpretation": "metadata_path /Users/example/case_dir qwen3-coder",
        },
    )

    labels = (
        evidence_quality_label(view),
        evidence_stats_label(view),
        primary_bottleneck_summary(view),
        confidence_summary(view),
    )

    assert_no_forbidden_fragments(labels)
    assert "SELECT" not in repr(labels)
    assert "example_guarded_table" not in repr(labels)


def test_recent_scan_case_summary_helpers_keep_supported_values():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 25,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": ["cardinality estimate anomalies: 2"],
            "query_optimization_candidate": {
                "tier": "medium",
                "score": 42,
                "impact": "medium",
                "confidence": "medium",
                "suggested_review_areas": ["exchange payload"],
            },
        },
        metadata_facts={
            "summary": {"status": "available", "table_stats": "available"},
            "tables": [],
        },
    )
    assert primary_bottleneck_summary(view) == (
        "Query shape is worth a rewrite review: exchange payload"
    )
    assert review_anchor_summary(view) == "Review exchange payload"
    assert "analyzer findings" in confidence_summary(view)
    assert "stats:" in confidence_summary(view)
    assert_no_forbidden_fragments(
        (
            primary_bottleneck_summary(view),
            review_anchor_summary(view),
            confidence_summary(view),
        )
    )


def test_recent_scan_case_summary_helpers_are_raw_free():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc /tmp/case_dir",
            "score": 40,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": [
                "BEGIN PROFILE Query Timeline SHOW CREATE TABLE raw stdout raw stderr "
                "/Users/example/case_dir CM_TOKEN qwen3-coder ollama"
            ],
            "query_optimization_candidate": {
                "tier": "high",
                "score": 88,
                "impact": "high",
                "confidence": "medium",
                "suggested_review_areas": [
                    "SELECT secret_col FROM example_guarded_table /tmp/raw qwen3-coder"
                ],
            },
            "optimizer_rewrite_support": {
                "status": "guidance_only",
                "rewriteability_bucket": "not_rewriteable",
                "label": "SELECT secret_col FROM example_guarded_table /tmp/raw qwen3-coder",
                "reason": "raw stderr /Users/example/case_dir",
            },
            "case_primary_bottleneck": {
                "label": "SELECT * FROM example_guarded_table",
                "confidence": "high",
                "reasons": [
                    "operator_02 SELECT secret_col FROM example_guarded_table /tmp/raw qwen3-coder raw stderr"
                ],
            },
        },
        evidence_quality_facts={
            "score": "88 /tmp/case_dir",
            "level": "high qwen3-coder raw stdout",
        },
        stats_quality_facts={
            "status": "available raw stderr",
            "stats_context": "stats_present_with_row_estimate_evidence",
            "interpretation": "metadata_path /Users/example/case_dir qwen3-coder",
        },
    )

    labels = (
        primary_bottleneck_summary(view),
        review_anchor_summary(view),
        confidence_summary(view),
    )
    assert_no_forbidden_fragments(labels)
    assert "SELECT" not in repr(labels)
    assert "example_guarded_table" not in repr(labels)


def test_recent_scan_case_verdict_falls_back_to_action_candidate_summary():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 36,
            "score_severity": "high",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_requested",
            "cardinality_anomaly_count": 4,
            "memory_anomaly_count": 3,
            "query_optimization_candidate": {
                "tier": "high",
                "score": 82,
                "impact": "high",
                "confidence": "high",
                "reasons": [
                    "join row expansion with cardinality mismatch",
                    "large exchange/intermediate data movement",
                ],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
        },
    )

    html = render_recent_scan_case_detail_view(view)

    assert primary_bottleneck_summary(view) == (
        "Query shape is worth a rewrite review: join row expansion with cardinality mismatch"
    )
    assert "Not classified" not in html
    assert '<h2 class="case-verdict-title">Query shape is worth a rewrite review</h2>' in html
    assert '<p class="case-verdict-signal">join row expansion with cardinality mismatch</p>' in html
    assert 'class="case-verdict-meta"' in html
    assert 'class="case-query-line"' not in html
    assert 'class="case-verdict-card"' not in html
    assert "Query shape is worth a rewrite review" in html
    assert "join row expansion with cardinality mismatch" in html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_verdict_labels_clean_candidate_as_follow_up():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "score_severity": "clean",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_requested",
            "query_optimization_candidate": {
                "tier": "medium",
                "score": 55,
                "impact": "medium",
                "confidence": "medium",
                "reasons": ["join row expansion with cardinality mismatch"],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
        },
    )

    priority_fact = diagnostic_fact_by_id(view.diagnostic_facts, "priority")
    html = render_recent_scan_case_detail_view(view)

    assert priority_fact is not None
    assert priority_fact.value == "Query-shape follow-up · score 0"
    assert priority_fact.severity == "warning"
    assert "Query-shape follow-up · score 0" in html
    assert "Clean · unknown" not in html
    assert "Clean · 0" not in html
    assert "Query-shape recommendation" in html
    assert "batch-status--warning" in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_verdict_keeps_clean_low_confidence_primary_cautious():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "score_severity": "clean",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_requested",
            "case_primary_bottleneck": {
                "label": "sql_shape",
                "confidence": "low",
                "reasons": ["join_top_finding"],
            },
            "query_optimization_candidate": {
                "tier": "low",
                "score": 10,
                "impact": "low",
                "confidence": "low",
                "reasons": ["join row expansion with cardinality mismatch"],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
        },
    )

    priority_fact = diagnostic_fact_by_id(view.diagnostic_facts, "priority")
    action_view = present_recent_scan_action_candidates(view)
    html = render_recent_scan_case_detail_view(view)

    assert primary_bottleneck_summary(view) == "No supported problem signal is classified yet"
    assert priority_fact is not None
    assert priority_fact.value == "Clean · 0"
    assert len(action_view.cards) == 1
    assert action_view.cards[0].title == "No supported change direction"
    assert action_view.cards[0].recommendation_id == "no_supported_change.v1"
    assert "did not identify a suspicious problem signal" in action_view.cards[0].why
    assert "Do not change SQL, collect stats, or tune runtime settings" in (
        action_view.cards[0].guardrails
    )
    assert (
        '<h2 class="case-verdict-title">No supported problem signal is classified yet</h2>' in html
    )
    assert_contains_in_order(
        html,
        [
            "No supported change direction",
            "Why this query matters",
            "This query is not currently prioritized for analyst action",
            "Where to inspect",
            "Score evidence and source coverage",
            "What to try",
            "No supported change is recommended for this selected case",
            "How to verify",
            "On the next comparable scan or rerun",
        ],
    )
    assert "Query shape is worth a rewrite review" not in html
    assert "No prioritized rewrite or stats action" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_verdict_promotes_clean_strong_primary_to_suspicious():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "score_severity": "clean",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_requested",
            "score_reasons": ["client fetch wait evidence"],
            "case_primary_bottleneck": {
                "label": "client_fetch_tail",
                "confidence": "high",
                "reasons": ["client_fetch_wait_top_finding"],
            },
        },
    )

    priority_fact = diagnostic_fact_by_id(view.diagnostic_facts, "priority")
    action_view = present_recent_scan_action_candidates(view)
    html = render_recent_scan_case_detail_view(view)

    assert view.score_severity == "suspicious"
    assert priority_fact is not None
    assert priority_fact.value == "Medium priority · 0"
    assert action_view.cards
    assert action_view.cards[0].title == "Diagnostic follow-up"
    assert action_view.cards[0].source_locators
    assert "Client fetch wait may be stretching the tail" in html
    assert "No supported problem signal is classified yet" not in html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_verdict_names_signals_when_primary_is_unknown():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 8,
            "score_severity": "suspicious",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_requested",
            "cardinality_anomaly_count": 2,
            "memory_anomaly_count": 1,
            "case_primary_bottleneck": {
                "label": "unknown",
                "confidence": "low",
                "reasons": ["no_primary_branch_supported"],
            },
        },
    )

    html = render_recent_scan_case_detail_view(view)

    assert primary_bottleneck_summary(view) == (
        "Supported analyzer signals need review: cardinality 2; memory 1"
    )
    assert '<h2 class="case-verdict-title">Supported analyzer signals need review</h2>' in html
    assert '<p class="case-verdict-signal">cardinality 2; memory 1</p>' in html
    assert "No single supported bottleneck is classified yet" not in html
    assert present_recent_scan_action_candidates(view).cards[0].title == "Diagnostic follow-up"
    assert_no_forbidden_fragments(html)


def test_recent_scan_action_candidate_uses_memory_estimate_context_follow_up():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 18,
            "score_severity": "suspicious",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_requested",
            "memory_anomaly_count": 2,
            "case_primary_bottleneck": {
                "label": "unknown",
                "confidence": "low",
                "reasons": ["memory_estimate_context_only", "data_movement_context_only"],
            },
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    card = action_view.cards[0]
    html = render_action_candidate_findings(view)

    assert view.primary_bottleneck.label == "Unknown"
    assert view.primary_bottleneck.reason_tokens == (
        "memory_estimate_context_only",
        "data_movement_context_only",
    )
    assert card.title == "Memory estimate evidence follow-up"
    assert card.recommendation_id == "diagnostic_follow_up.v1"
    assert "not a root-cause claim" in card.why
    assert "Do not change SQL or runtime settings from memory estimates alone" in (
        card.change_direction
    )
    assert "spill/scratch counters" in card.change_direction
    assert "comparable rerun" in card.verification
    assert [locator.label for locator in card.source_locators] == [
        "Stats and estimate evidence",
        "Memory, spill, and scratch evidence",
        "Source coverage and limitations",
    ]
    assert "Memory estimate evidence follow-up" in html
    assert "memory estimate evidence is context only" in html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_primary_bottleneck_labels_unknown_supporting_reasons():
    assert (
        primary_bottleneck_reason_label("codegen_finding_not_primary_supported")
        == "codegen finding is not primary-supported"
    )
    assert (
        primary_bottleneck_reason_label("scan_skew_medium_supporting_only")
        == "scan skew is supporting only"
    )
    assert (
        primary_bottleneck_reason_label("data_movement_context_only")
        == "data movement is context only"
    )
    assert (
        primary_bottleneck_reason_label("memory_estimate_context_only")
        == "memory estimate evidence is context only"
    )
    assert (
        primary_bottleneck_reason_label("wall_clock_not_explained_by_mapped_operators")
        == "mapped operators do not explain wall clock"
    )


def test_recent_scan_primary_bottleneck_labels_aggregate_memory_shape_reason():
    assert (
        primary_bottleneck_reason_label("aggregate_memory_estimate_top_finding")
        == "aggregate memory-estimate shape is the top finding"
    )


def test_recent_scan_case_detail_summary_helpers_use_primary_bottleneck():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "runtime_admission",
            "confidence": "high",
            "reasons": ["admission_wait_share_80pct"],
        },
    }
    view = present_recent_scan_case_detail(
        "case-001",
        case,
    )

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Admission or queueing signals need runtime follow-up: admission wait share 80%"
    )
    assert (
        present_recent_scan_action_candidates(view).cards[0].title == "Admission/runtime follow-up"
    )
    assert "primary bottleneck" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_admission_follow_up_points_to_profile_facts_safely():
    case = {
        "query_id": "abc",
        "score": 17,
        "score_severity": "high",
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "runtime_admission",
            "confidence": "high",
            "reasons": [
                "admission_wait_source_profile_resource_facts",
                "admission_wait_source_profile_timing_facts",
            ],
        },
        "source_locators": {
            "runtime_admission": [
                {"id": "runtime_admission_window", "detail": "case runtime window"},
                {
                    "id": "profile_resource_admission_evidence",
                    "detail": "query-specific admission result or resource wait",
                },
                {
                    "id": "profile_timing_admission_evidence",
                    "detail": "query timeline admission phase",
                },
                {"id": "unknown_locator", "detail": "SELECT secret_col FROM db_table"},
            ],
        },
    }

    action_view = present_recent_scan_action_candidates(
        present_recent_scan_case_detail("case-001", case)
    )
    html = render_typed_case_detail("case-001", case)

    assert action_view.cards[0].title == "Admission/runtime follow-up"
    assert [locator.label for locator in action_view.cards[0].source_locators] == [
        "Runtime: admission and pool timeline",
        "Profile: resource admission facts",
        "Profile: timing admission facts",
    ]
    assert "Runtime: admission and pool timeline: case runtime window" in html
    assert (
        "Profile: resource admission facts: query-specific admission result or resource wait"
        in html
    )
    assert "Profile: timing admission facts: query timeline admission phase" in html
    assert "unknown_locator" not in html
    assert "secret_col" not in html
    assert "db_table" not in html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_source_locators_accept_typed_line_spans_without_raw_coordinates():
    locators = present_source_locators(
        {
            "query_optimization": [
                {
                    "id": "sql_final_select_filter",
                    "line_span": {"start_line": 7, "end_line": 9},
                    "detail": "predicate near final SELECT",
                },
                {
                    "id": "plan_cardinality_anomaly",
                    "coordinate": "SELECT secret_col FROM example_guarded_table",
                    "line_span": {"start_line": 12, "end_line": 12},
                    "line_span_source": "SELECT secret_col FROM example_guarded_table",
                    "detail": "node 02 HASH JOIN",
                },
                {
                    "id": "sql_cte_block",
                    "coordinate": "lines 2-4",
                    "detail": "2 CTEs",
                },
            ]
        }
    )

    query_locators = locators["query_optimization"]
    assert query_locators[0].coordinate == "lines 7-9"
    assert query_locators[0].line_span == (7, 9)
    assert query_locators[0].line_span_source == "line_range_from_sql_parser"
    assert query_locators[1].coordinate == "line 12"
    assert query_locators[1].line_span == (12, 12)
    assert query_locators[1].line_span_source == "line_range_from_sql_parser"
    assert query_locators[2].coordinate == "lines 2-4"
    assert query_locators[2].line_span == (2, 4)
    assert query_locators[2].line_span_source == "line_range_from_legacy_coordinate"
    assert_no_forbidden_fragments(query_locators)


def test_recent_scan_direct_impala_details_show_source_limitations_safely():
    case = {
        "query_id": "abc",
        "score": 17,
        "score_severity": "suspicious",
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "skipped",
        "case_primary_bottleneck": {
            "label": "runtime_admission",
            "confidence": "medium",
        },
    }

    direct_view = present_recent_scan_case_detail(
        "case-001",
        case,
        query_profile_source="impala",
    )
    cm_view = present_recent_scan_case_detail(
        "case-001",
        case,
        query_profile_source="cm",
    )
    html = render_recent_scan_case_detail_view(direct_view)

    assert direct_view.source_limitations == (
        (
            "Cluster event context is unavailable for Direct Impala scans because "
            "Cloudera Manager events are not collected on this source."
        ),
        (
            "Optional Prometheus runtime metrics were not collected for this case; runtime "
            "review should rely on profile and query context."
        ),
        (
            "Bounded Impala metadata is unavailable for this case; stats and table-layout "
            "review remain limited."
        ),
    )
    assert cm_view.source_limitations == ()
    assert "Source coverage and limitations" in html
    assert html.index('<section id="source-limitations"') < html.index(
        '<section id="pipeline-status"'
    )
    assert "Direct Impala source coverage" in html
    assert "Cluster event context is unavailable for Direct Impala scans" in html
    assert "Optional Prometheus runtime metrics were not collected for this case" in html
    assert "Bounded Impala metadata is unavailable for this case" in html
    assert_no_forbidden_fragments(direct_view)
    assert_no_forbidden_fragments(html)


def test_parse_source_provenance_facts_from_analyzer_markdown():
    facts = """
## Source Provenance

- guardrail: Source provenance is a raw-free coverage summary.
- profile: partial; source=Impala daemon profile endpoint; coverage=dialect=classic_text_profile, layout=classic, compatibility=unsupported
  - limitation: Profile source coverage is unknown.
- metrics: none; source=Runtime metrics; coverage=not_collected
  - limitation: Runtime metrics were not collected for this case.
""".strip()

    parsed = parse_source_provenance_facts(facts)

    assert parsed == {
        "guardrail": "Source provenance is a raw-free coverage summary.",
        "items": [
            {
                "kind": "profile",
                "status": "partial",
                "source": "Impala daemon profile endpoint",
                "coverage": "dialect=classic_text_profile, layout=classic, compatibility=unsupported",
                "limitations": ["Profile source coverage is unknown."],
            },
            {
                "kind": "metrics",
                "status": "none",
                "source": "Runtime metrics",
                "coverage": "not_collected",
                "limitations": ["Runtime metrics were not collected for this case."],
            },
        ],
    }


def test_recent_scan_direct_impala_source_limitations_use_safe_source_provenance():
    case = {
        "query_id": "abc",
        "score": 17,
        "score_severity": "suspicious",
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "runtime_admission",
            "confidence": "medium",
        },
    }
    provenance = {
        "items": [
            {
                "kind": "engine",
                "status": "unknown",
                "limitations": [
                    "raw detail SELECT secret_col FROM private_table token=secret-value"
                ],
            },
            {"kind": "profile", "status": "partial"},
            {"kind": "events", "status": "none"},
            {"kind": "metrics", "status": "partial"},
            {"kind": "metadata", "status": "partial"},
        ]
    }

    view = present_recent_scan_case_detail(
        "case-001",
        case,
        source_provenance_facts=provenance,
        query_profile_source="impala",
    )
    html = render_recent_scan_case_detail_view(view)

    assert view.source_limitations == (
        (
            "Cluster event context is unavailable for Direct Impala scans because "
            "Cloudera Manager events are not collected on this source."
        ),
        "Engine identity is unavailable from deterministic profile facts.",
        "Profile source coverage is partial; unsupported profile sections remain limitations.",
        (
            "Optional Prometheus runtime metrics are incomplete or unavailable for this case; "
            "runtime review should rely on profile and query context."
        ),
        (
            "Bounded Impala metadata is partial for this case; stats and table-layout review "
            "remain limited."
        ),
    )
    assert "Engine identity is unavailable from deterministic profile facts." in html
    assert "Profile source coverage is partial" in html
    assert "Optional Prometheus runtime metrics are incomplete or unavailable" in html
    assert "Bounded Impala metadata is partial" in html
    assert "secret_col" not in html
    assert "private_table" not in html
    assert "secret-value" not in html
    assert_no_forbidden_fragments(view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_detail_summary_helpers_use_backend_data_skew_reason():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "runtime_skew",
            "confidence": "medium",
            "reasons": ["backend_data_skew_detected"],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Runtime skew may be stretching execution: backend data skew detected"
    )
    assert "Runtime skew may be stretching execution" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_case_detail_summary_helpers_use_storage_primary_bottleneck():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "runtime_storage",
            "confidence": "high",
            "reasons": ["storage_or_hdfs_runtime_diagnosis"],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Storage or HDFS signals need follow-up: storage/HDFS evidence is the strongest "
        "runtime follow-up"
    )
    assert "Storage or HDFS signals need follow-up" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_case_detail_summary_helpers_use_memory_primary_bottleneck():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "runtime_memory",
            "confidence": "medium",
            "reasons": [
                "memory_pressure_spill_scratch_supported",
                "spill_scratch_counters_2",
            ],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Memory pressure needs spill/scratch follow-up: selected-query spill/scratch "
        "evidence supports memory pressure; 2 spill/scratch counters"
    )
    assert "Memory pressure needs spill/scratch follow-up" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_case_detail_summary_helpers_use_client_fetch_primary_bottleneck():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "client_fetch_tail",
            "confidence": "medium",
            "reasons": ["client_fetch_wait_top_finding", "client_fetch_wait_share_45pct"],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Client fetch wait may be stretching the tail: client fetch wait is the top finding; "
        "client fetch wait share 45%"
    )
    assert "Client fetch wait may be stretching the tail" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_case_detail_summary_helpers_use_query_shape_reason():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "sql_shape",
            "confidence": "medium",
            "reasons": ["join_top_finding"],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Query shape is worth a rewrite review: join shape is the top finding"
    )
    assert "Query shape is worth a rewrite review" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_case_detail_summary_helpers_explain_mixed_competing_reasons():
    case = {
        "query_id": "abc",
        "score": 17,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "case_primary_bottleneck": {
            "label": "mixed",
            "confidence": "medium",
            "reasons": ["competing_stats", "competing_runtime_data_movement"],
        },
    }
    view = present_recent_scan_case_detail("case-001", case)

    detail_html = render_typed_case_detail("case-001", case)

    assert primary_bottleneck_summary(view) == (
        "Multiple supported signals need review: stats gaps also match estimate evidence; "
        "exchange/data movement also needs review"
    )
    assert "Multiple supported signals need review" in detail_html
    assert_no_forbidden_fragments(primary_bottleneck_summary(view))
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_case_summary_uses_clear_mixed_wording():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 17,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "case_primary_bottleneck": {
                "label": "mixed",
                "confidence": "medium",
                "reasons": ["competing_stats", "competing_sql_shape"],
            },
        },
    )

    assert primary_bottleneck_summary(view) == (
        "Multiple supported signals need review: stats gaps also match estimate evidence; "
        "query shape also needs review"
    )
    assert (
        review_anchor_summary(view) == "No prioritized structural anchor from deterministic facts"
    )
    assert_no_forbidden_fragments(
        (
            primary_bottleneck_summary(view),
            review_anchor_summary(view),
        )
    )


def test_recent_scan_detail_html_renders_mixed_fixture_guidance():
    fixture = primary_bottleneck_fixture("mixed_stats_query_shape_data_movement.json")
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 31,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": ["cardinality estimate anomalies: 2"],
            "case_primary_bottleneck": fixture["expected"],
        },
    )

    html = render_recent_scan_case_detail_view(view)

    assert "Verdict" in html
    assert "Multiple supported signals need review" in html
    assert "stats gaps also match estimate evidence" in html
    assert "query shape also needs review" in html
    assert "exchange/data movement also needs review" in html
    assert "Start with the recommendation below" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_detail_html_renders_storage_fixture_guidance():
    fixture = primary_bottleneck_fixture("runtime_storage_from_runtime_diagnosis.json")
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 31,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "score_reasons": ["storage/HDFS follow-up evidence"],
            "case_primary_bottleneck": fixture["expected"],
        },
    )

    html = render_recent_scan_case_detail_view(view)

    assert "Storage or HDFS signals need follow-up" in html
    assert "storage/HDFS evidence is the strongest runtime follow-up" in html
    assert "Start with the recommendation below" not in html
    assert "read the recommended change and verification path" in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_case_detail_evidence_labels_clean_case_stays_cautious():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "not_attempted",
        },
    )

    metadata_summary = dict(view.metadata.summary_items)

    assert view.signal_summary == "no positive analyzer signals"
    assert primary_bottleneck_summary(view) == "No supported problem signal is classified yet"
    assert view.runtime_verdict.title == "Runtime context not collected"
    assert metadata_summary["metadata coverage"] == "not requested for this case"
    assert evidence_stats_label(view) == "Stats context limited by metadata coverage"
    assert_no_forbidden_fragments(
        (
            view.signal_summary,
            primary_bottleneck_summary(view),
            view.runtime_verdict,
            metadata_summary,
            evidence_stats_label(view),
        )
    )


def test_recent_scan_case_detail_evidence_labels_separate_stats_candidate_quality():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "table_stats_status": "missing_or_incomplete",
            "stats_optimization_candidate": {
                "score": 74,
                "tier": "high",
                "impact": "high",
                "confidence": "medium",
                "need_type": "table_and_column_stats",
                "speed_benefit": "medium",
                "reasons": ["missing table stats before expensive join"],
                "suggested_review_areas": ["table/partition row counts"],
                "required_confirmation": ["compare EXPLAIN before and after stats collection"],
            },
        },
        {
            "statement_counts": {"ok": 3, "error": 0, "not_applicable": 0, "too_large": 0},
            "tables": [
                {
                    "table": "db.fact_sales",
                    "object type": "table",
                    "statements": {
                        "SHOW CREATE TABLE": "ok",
                        "SHOW TABLE STATS": "ok",
                        "SHOW COLUMN STATS": "ok",
                    },
                }
            ],
        },
    )

    assert primary_bottleneck_summary(view) == (
        "Stats gaps are worth checking before a rewrite: missing table stats before expensive join"
    )

    assert evidence_stats_label(view) == "Stats candidate: High impact, Medium confidence"
    assert_no_forbidden_fragments((primary_bottleneck_summary(view), evidence_stats_label(view)))


def test_recent_scan_case_detail_evidence_labels_use_analyzer_stats_quality_without_candidate():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "score": 0,
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "collected",
            "table_stats_status": "not_checked",
        },
        stats_quality_facts={
            "status": "limited",
            "table_stats": "incomplete_or_unknown",
            "column_stats": "complete",
            "row_estimate_evidence": "observed",
            "row_estimate_issue_count": "2",
            "partition_coverage": "limited",
            "stats_context": "stats_gap_with_row_estimate_evidence",
            "interpretation": "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence.",
            "guardrail": "Stats quality is follow-up evidence, not a standalone root cause.",
        },
    )

    assert evidence_stats_label(view) == (
        "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence."
    )
    assert (
        evidence_stats_label(view)
        == "Missing or incomplete stats coverage aligns with row-estimate mismatch evidence."
    )
    assert_no_forbidden_fragments(evidence_stats_label(view))


def test_cm_metrics_section_prioritizes_correlated_rows_and_collapses_full_list():
    view = present_recent_scan_case_detail(
        "case-001",
        {"query_id": "abc"},
        cm_metrics_facts={
            "summary": {"status": "available", "coverage": "2/2 metrics ok"},
            "signals": [
                {
                    "label": "Daemon memory growth",
                    "status": "observed",
                    "basis": "daemon memory grew during the query window",
                },
                {
                    "label": "Host CPU pressure",
                    "status": "not_observed",
                    "basis": "host CPU stayed below the threshold",
                },
            ],
            "correlations": [
                {
                    "label": "Daemon memory growth",
                    "status": "correlated",
                    "metric_status": "observed",
                    "strength": "moderate",
                    "interpretation": "memory aligns with profile pressure",
                },
                {
                    "label": "Host CPU pressure",
                    "status": "context-only",
                    "metric_status": "not_observed",
                    "strength": "none",
                    "interpretation": "CPU is context only",
                },
            ],
        },
    ).cm_metrics

    html = render_cm_metrics_section(view)
    primary_html = html[: html.index("<h3>All collected runtime metrics</h3>")]

    assert "Correlated runtime metric signals" in primary_html
    assert "Daemon memory growth" in primary_html
    assert "Host CPU pressure" not in primary_html
    assert "All collected runtime metrics" in html
    assert "Host CPU pressure" in html
    assert html.count('<div class="batch-table-wrap">') == 2
    assert_no_forbidden_fragments(html)


def test_cm_metrics_parser_accepts_provider_neutral_runtime_metrics_headings():
    facts = """
## Runtime Metrics Facts

- status: available
- coverage: 2/2 metrics ok
- daemon_memory_growth: observed
- daemon_memory_growth_basis: daemon memory grew during the query window

### Runtime metrics limitations

- bounded metric window only

## Runtime Metrics Correlation

- status: available
- coverage: 2/2 metrics ok
- correlated_signals: 1
- context_only_signals: 0
- guardrail: runtime metrics are context only
- daemon_memory_growth: correlated (metric=observed, strength=moderate)
  - basis: profile memory pressure overlaps metric window
  - interpretation: memory aligns with profile pressure
"""

    parsed = parse_runtime_metrics_facts(facts)

    assert parsed is not None
    assert parse_cm_metrics_facts(facts) == parsed
    assert parsed["summary"]["coverage"] == "2/2 metrics ok"
    assert parsed["correlation_summary"]["correlated_signals"] == "1"
    assert parsed["signals"][0]["status"] == "observed"
    assert parsed["correlations"][0]["status"] == "correlated"
    assert parsed["limitations"] == ["bounded metric window only"]
    assert_no_forbidden_fragments(parsed)


def test_data_movement_parser_accepts_safe_analyzer_section():
    facts = """
## Data Movement Evidence

- status: supported
- evidence_tier: strong
- finding_supported: yes
- primary_supported: yes
- total_bytes_sent: 17.00 GiB
- exchange_operator_count: 4
- exchange_elapsed: 8m 10s
- exchange_elapsed_share: 73.0%
- guardrail: Data movement facts require deterministic profile support.
- ignored_raw_field: raw stdout /tmp/case_dir
- limitations:
  - Stability labels are not available for this profile.
"""

    parsed = parse_data_movement_facts(facts)

    assert parsed is not None
    assert parsed["summary"]["status"] == "supported"
    assert parsed["summary"]["evidence_tier"] == "strong"
    assert parsed["summary"]["primary_supported"] == "yes"
    assert parsed["summary"]["exchange_elapsed_share"] == "73.0%"
    assert "ignored_raw_field" not in parsed["summary"]
    assert parsed["limitations"] == ["Stability labels are not available for this profile."]
    assert_no_forbidden_fragments(parsed)


def test_query_context_parser_accepts_allowlisted_context_headings():
    facts = """
## CM Time-Series Context

- window: 2026-05-04T09:59:00Z to 2026-05-04T10:06:00Z

## CM Query Context

- available: yes
- query_id: hidden-from-context-render
- query status: finished
- query_type: QUERY
- pool: root.analytics
- start_time: 2026-05-04T10:00:00Z
- end_time: 2026-05-04T10:05:15Z
- duration: 315s
- admission_result: admitted
- admission_wait: 2.50s
- rows_produced: 12.00M
- bytes_read: 42.00 GiB
- memory_aggregate_peak: 18.00 GiB
- not_allowed: raw stdout /tmp/case_dir
"""

    parsed = parse_query_context_facts(facts)

    assert parsed is not None
    assert parsed["summary"]["status"] == "finished"
    assert parsed["summary"]["query_type"] == "QUERY"
    assert parsed["summary"]["pool"] == "root.analytics"
    assert parsed["summary"]["start_time"] == "2026-05-04T10:00:00Z"
    assert parsed["summary"]["admission_wait"] == "2.50s"
    assert parsed["summary"]["bytes_read"] == "42.00 GiB"
    assert "query_id" not in parsed["summary"]
    assert "not_allowed" not in parsed["summary"]
    assert "2026-05-04T09:59:00Z" not in repr(parsed)
    assert_no_forbidden_fragments(parsed)


def test_recent_scan_details_renders_query_context_as_safe_verdict_chips():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc:def",
            "user": "alice",
            "score": 22,
            "duration_sec": 315,
        },
        query_context_facts={
            "summary": {
                "available": "yes",
                "query_type": "QUERY",
                "pool": "root.analytics",
                "start_time": "2026-05-04T10:00:00Z",
                "end_time": "2026-05-04T10:05:15Z",
                "admission_wait": "2.50s",
                "bytes_read": "42.00 GiB",
                "memory_aggregate_peak": "18.00 GiB",
            }
        },
    )

    html = render_recent_scan_case_detail_view(view)

    assert "query window" in html
    assert '<a href="#runtime-evidence">query window</a>' in html
    assert "2026-05-04T10:00:00Z to 2026-05-04T10:05:15Z" in html
    assert "query type" in html
    assert "root.analytics" in html
    assert "admission wait" in html
    assert "2.50s" in html
    assert "resource footprint" in html
    assert "read 42.00 GiB; peak memory 18.00 GiB" in html
    assert '<details class="analysis-subdetails" aria-label="Query context">' in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_details_builds_ranked_question_oriented_facts():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc:def",
            "user": "alice",
            "score": 22,
            "duration_sec": 315,
            "workload_baseline_duration_sec_p95": 90,
            "workload_baseline_sample_count": 12,
            "workload_regression": "regressed",
            "query_optimization_candidate": {
                "tier": "high",
                "score": 82,
                "impact": "high",
                "confidence": "high",
                "reasons": ["large exchange/intermediate data movement"],
                "suggested_review_areas": ["exchange rows"],
            },
        },
        query_context_facts={
            "summary": {
                "available": "yes",
                "query_type": "QUERY",
                "pool": "root.analytics",
                "start_time": "2026-05-04T10:00:00Z",
                "end_time": "2026-05-04T10:05:15Z",
                "admission_wait": "2.50s",
                "bytes_read": "42.00 GiB",
                "memory_aggregate_peak": "18.00 GiB",
            }
        },
    )

    facts = view.diagnostic_facts
    fact_ids = [fact.fact_id for fact in facts]
    kpi_ids = [fact.fact_id for fact in verdict_kpi_facts(facts)]
    query_window = diagnostic_fact_by_id(facts, "query_window")
    resource_footprint = diagnostic_fact_by_id(facts, "resource_footprint")
    review_anchor = diagnostic_fact_by_id(facts, "review_anchor")

    assert fact_ids[:4] == ["priority", "duration", "main_signal", "query_window"]
    assert kpi_ids == ["duration", "confidence"]
    assert query_window is not None
    assert query_window.question == "When did it run?"
    assert query_window.source_anchor == "runtime-evidence"
    assert resource_footprint is not None
    assert resource_footprint.value == "read 42.00 GiB; peak memory 18.00 GiB"
    assert review_anchor is not None
    assert review_anchor.source_anchor == "action-plan"
    assert_no_forbidden_fragments(facts)


def test_recent_scan_details_groups_diagnostics_by_user_questions():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc:def",
            "user": "alice",
            "score": 22,
            "duration_sec": 315,
            "workload_baseline_duration_sec_p95": 90,
            "workload_baseline_sample_count": 12,
            "workload_regression": "regressed",
            "table_stats_status": "missing_or_incomplete",
            "query_optimization_candidate": {
                "tier": "high",
                "score": 82,
                "impact": "high",
                "confidence": "high",
                "reasons": ["large exchange/intermediate data movement"],
                "suggested_review_areas": ["exchange rows"],
            },
        },
        query_context_facts={
            "summary": {
                "available": "yes",
                "query_type": "QUERY",
                "pool": "root.analytics",
                "start_time": "2026-05-04T10:00:00Z",
                "end_time": "2026-05-04T10:05:15Z",
                "admission_wait": "2.50s",
                "bytes_read": "42.00 GiB",
                "memory_aggregate_peak": "18.00 GiB",
            }
        },
    )

    questions = present_recent_scan_diagnostic_questions(view)
    html = render_diagnostic_questions_view(questions)
    detail_html = render_recent_scan_case_detail_view(view)
    group_ids = [group.group_id for group in questions.groups]
    facts_by_group = {
        group.group_id: [fact.fact_id for fact in group.facts] for group in questions.groups
    }

    assert group_ids == [
        "time_and_work",
        "normality",
        "queue_or_cluster",
    ]
    assert facts_by_group["time_and_work"] == [
        "query_window",
        "resource_footprint",
        "query_type",
    ]
    assert facts_by_group["queue_or_cluster"] == [
        "admission_wait",
        "pool",
    ]
    all_question_fact_ids = [
        fact_id for group_facts in facts_by_group.values() for fact_id in group_facts
    ]
    assert len(all_question_fact_ids) == len(set(all_question_fact_ids))
    assert '<section id="diagnostic-questions"' in html
    assert "Coverage checks" in html
    assert "coverage, limitations, and supporting context" in html
    assert "Technical facts grouped" not in html
    assert "What looks wrong?" not in html
    assert "Queue or cluster?" in html
    assert '<a href="#runtime-evidence">query window</a>' in html
    assert detail_html.index('<section id="diagnostic-questions"') < detail_html.index(
        '<section id="pipeline-status"'
    )
    assert detail_html.index('<section id="pipeline-status"') < detail_html.index(
        '<section id="runtime-evidence"'
    )
    assert '<section id="metadata-evidence"' in detail_html
    assert '<section id="score-evidence"' in detail_html
    assert_no_forbidden_fragments(questions)
    assert_no_forbidden_fragments(html)


def test_recent_scan_typed_details_helpers_sanitize_score_reasons():
    case = {
        "query_id": "abc",
        "score": 31,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "collected",
        "score_reasons": [
            "metadata collection failed SHOW CREATE TABLE raw stdout /Users/example/case_dir qwen3-coder ollama",
        ],
    }

    reasons = metadata_score_reasons(case)
    reason_html = render_score_reason_card_view(
        present_recent_scan_score_reason(case["score_reasons"][0])
    )
    detail_html = render_typed_case_detail("case-001", case)

    assert reasons
    assert_no_forbidden_fragments(reasons)
    assert_no_forbidden_fragments(reason_html)
    assert_no_forbidden_fragments(detail_html)
    assert "Verdict" in detail_html
    assert "Jump to section" not in detail_html
    assert 'class="panel batch-panel case-detail-panel"' in detail_html
    assert '<section id="case-overview" class="case-verdict"' in detail_html
    assert '<section id="analysis-summary"' not in detail_html
    assert "[metadata statement hidden]" in detail_html
    assert "[model setting hidden]" in detail_html


def test_recent_scan_metadata_fallback_note_is_sanitized_by_presenter_boundary():
    view = present_recent_scan_metadata(
        {
            "metadata_status": "partial",
            "score_reasons": [
                "Metadata fallback used analysis_facts.md from /Users/example/case_dir "
                "after raw stderr from qwen3-coder"
            ],
        },
        None,
    )
    html = render_metadata_facts_body(view)

    assert_no_forbidden_fragments(html)
    assert "Safe aggregate metadata facts" in html
    assert "&lt;local path hidden&gt;" in html
    assert "[model setting hidden]" in html


def test_optimized_query_action_view_sanitizes_state_before_rendering():
    view = present_optimized_query_action(
        {
            "status": "generated",
            "output_kind": "no_rewrite",
            "fallback_reason": "validation_failed",
            "source_scope": "read_only_statement",
            "risk_mode": "recommendations_only",
            "risk_reasons": [
                "cte_body_validation_not_proven",
                "/Users/example/case_dir qwen3-coder",
            ],
            "error": "failed at /Users/example/case_dir with qwen3-coder near SELECT secret FROM db_table",
            "source_available": True,
        }
    )
    html = render_optimized_query_outcome(view)

    assert "failed at <local path hidden> with [model setting hidden]" in view.error
    assert "SELECT" not in view.error
    assert "secret" not in view.error
    assert "No trusted rewrite" in html
    assert_no_forbidden_fragments(view)
    assert_no_forbidden_fragments(html)


def test_optimized_query_action_view_labels_deterministic_draft_unavailable():
    view = present_optimized_query_action(
        {
            "status": "generated",
            "output_kind": "no_rewrite",
            "fallback_reason": "deterministic_draft_unavailable",
            "source_scope": "read_only_statement",
            "risk_mode": "conservative_rewrite",
            "risk_reasons": ["cte_body_validation_not_proven"],
            "source_available": True,
        }
    )

    html = render_optimized_query_outcome(view)

    assert "Deterministic draft unavailable" in html
    assert "could not construct a deterministic draft" in html
    assert "deterministic_draft_unavailable" not in html
    assert_no_forbidden_fragments(html)


def test_optimized_query_outcome_keeps_static_labels_english_with_russian_body():
    view = present_optimized_query_action(
        {
            "status": "generated",
            "output_kind": "recommendations_only",
            "fallback_reason": "synthetic_demo_recommendations",
            "source_scope": "read_only_statement",
            "risk_mode": "recommendations_only",
            "risk_reasons": ["cte_body_validation_not_proven"],
            "source_available": True,
        }
    )

    html = render_optimized_query_outcome(view, language="ru")

    assert "Recommendations only" in html
    assert "Outcome: Recommendations only" in html
    assert "Source scope: Read-only statement" in html
    assert "Risk mode: Recommendations only" in html
    assert "Guardrails: Эквивалентность CTE body" in html
    assert "Reason: Synthetic demo recommendations" in html
    assert "Manual validation: Not needed" in html
    assert "Форма запроса недостаточно безопасна" in html
    assert "Только рекомендации" not in html
    assert "Результат:" not in html
    assert "Режим проверки:" not in html
    assert "Ограничения:" not in html
    assert "Ручная проверка:" not in html
    assert_no_forbidden_fragments(html)


def test_technical_details_are_hidden_when_all_values_are_empty():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
            "report_generated": False,
        },
    )

    assert render_technical_details(view) == ""


def test_recent_scan_technical_details_view_renderer_matches_legacy_adapter():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "query_id": "abc",
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
            "failure_category": "raw stderr /tmp/case_dir",
            "cm_collect_seconds": 0,
            "analysis_seconds": 1.25,
            "report_seconds": "0s",
            "total_seconds": 3.5,
        },
    )
    technical_view = present_recent_scan_technical_details(view)

    assert render_technical_details_view(technical_view) == render_technical_details(view)
    assert "Query Doctor processing timings" in render_technical_details_view(technical_view)
    assert "analysis seconds" in render_technical_details_view(technical_view)
    assert "cm collect seconds" not in render_technical_details_view(technical_view)
    assert_no_forbidden_fragments(technical_view)
    assert_no_forbidden_fragments(render_technical_details_view(technical_view))


def test_report_action_view_trusted_report_is_explicit_action_only():
    view = present_report_action({"status": "generated", "trusted": True, "partial": True})

    assert view.status == "generated"
    assert view.trusted is True
    assert view.partial_untrusted is False
    assert view.show_open_link is True
    assert view.button_label == "Generate LLM report"


def test_report_action_view_sanitizes_error_with_browser_error_policy():
    view = present_report_action(
        {
            "status": "failed",
            "error": "failed at /Users/example/case_dir near SELECT secret FROM db_table",
        }
    )

    assert "<local path hidden>" in view.error
    assert "SELECT" not in view.error
    assert "secret" not in view.error
    assert "case_dir" not in view.error


def test_recent_scan_summary_renderer_uses_presenter_safe_values():
    html = render_batch_summary(
        {
            "selected_count": 1,
            "summaries_inspected": 1,
            "duration_filter": "none /Users/example",
            "recent_window_minutes": 120,
            "query_type_filter": "QUERY",
            "include_failed": False,
            "include_running": False,
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "abc /tmp/case_dir",
                    "user": "alice /tmp/case_dir CM_TOKEN",
                    "score": 7,
                    "collection_status": "ok",
                    "analysis_status": "ok",
                    "metadata_status": "skipped",
                    "table_stats_status": "missing",
                    "score_reasons": [
                        "BEGIN PROFILE Query Timeline SHOW CREATE TABLE raw stdout raw stderr "
                        "CM_TOKEN KRB5CCNAME metadata_coordinator metadata_auth metadata_path qwen ollama",
                    ],
                }
            ],
        },
        query_group="suspicious",
    )

    for fragment in FORBIDDEN_DISPLAY_FRAGMENTS:
        assert fragment not in html
    assert "local path hidden" in html
    assert "metadata statement hidden" not in html
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Priority</th>"
        "<th>Duration</th></tr>"
    ) in html
    assert "<th>At a glance</th>" not in html
    assert '<a class="batch-finding-link" href="/batch/case/case-001">' in html
    assert 'title="table stats missing">Missing</span>' not in html
    assert "positive score from detailed analyzer reasons" in html
    assert "collection ok; analysis ok; metadata skipped; report not_run" not in html


def test_recent_scan_summary_renders_workload_groups_safely():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "case-one:id",
                "user": "alice",
                "score": 31,
                "score_severity": "high",
                "duration_sec": 10,
                "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 20,
            },
            {
                "case_index": 2,
                "query_id": "case-two:id",
                "user": "alice",
                "score": 25,
                "score_severity": "suspicious",
                "duration_sec": 20,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 20,
            },
            {
                "case_index": 3,
                "query_id": "incomplete:id",
                "score": 20,
                "score_severity": "suspicious",
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_fingerprint_incomplete": True,
            },
            {
                "case_index": 4,
                "query_id": "single:id",
                "score": 18,
                "score_severity": "suspicious",
                "workload_fingerprint": "wf_cccccccccccccccccccccccc",
                "group_fingerprint": "wf_cccccccccccccccccccccccc",
                "workload_group_member_count": 1,
            },
            {
                "case_index": 5,
                "query_id": "runtime-one:id",
                "user": "platform",
                "score": 29,
                "score_severity": "high",
                "duration_sec": 40,
                "workload_fingerprint": "wf_dddddddddddddddddddddddd",
                "group_fingerprint": "wf_dddddddddddddddddddddddd",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 50,
                "case_primary_bottleneck": {"label": "runtime_admission", "confidence": "high"},
            },
            {
                "case_index": 6,
                "query_id": "runtime-two:id",
                "user": "platform",
                "score": 21,
                "score_severity": "high",
                "duration_sec": 50,
                "collection_status": "cancelled",
                "workload_fingerprint": "wf_dddddddddddddddddddddddd",
                "group_fingerprint": "wf_dddddddddddddddddddddddd",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 50,
                "case_primary_bottleneck": {"label": "runtime_admission", "confidence": "medium"},
            },
            {
                "case_index": 7,
                "query_id": "quiet-one:id",
                "user": "svc",
                "score": 0,
                "score_severity": "clean",
                "duration_sec": 8,
                "workload_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "group_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 9,
            },
            {
                "case_index": 8,
                "query_id": "quiet-two:id",
                "user": "svc",
                "score": 0,
                "score_severity": "clean",
                "duration_sec": 9,
                "workload_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "group_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 9,
            },
        ],
        "workload_groups": {
            "schema_version": 1,
            "groups": [
                {
                    "fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                    "member_count": 2,
                    "member_case_ids": ["case-001", "case-002", "../case-999"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 2,
                        "cte_count": 1,
                        "set_operation_count": 0,
                        "aggregate_present": True,
                        "window_present": False,
                        "scan_count": 3,
                        "exchange_count": 2,
                        "referenced_tables": [
                            "example_warehouse.fact_sales",
                            "example_warehouse.dim_customer",
                            "unsafe/profile.txt",
                        ],
                    },
                    "aggregates": {
                        "count": 2,
                        "duration_sec_total": 30,
                        "duration_sec_p50": 10,
                        "duration_sec_p95": 20,
                        "pool_top": "root.analytics /tmp/raw",
                        "primary_bottleneck_top": "stats",
                        "score_top": "high",
                    },
                    "baseline": {
                        "schema_version": 1,
                        "regression": "strong",
                        "sample_count": 3,
                        "duration_sec_p95": 12.5,
                    },
                },
                {
                    "fingerprint": "wf_dddddddddddddddddddddddd",
                    "member_count": 2,
                    "member_case_ids": ["case-005", "case-006"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 1,
                        "cte_count": 0,
                        "set_operation_count": 0,
                        "scan_count": 1,
                        "exchange_count": 1,
                        "referenced_tables": ["example_warehouse.runtime_table"],
                    },
                    "aggregates": {
                        "count": 2,
                        "duration_sec_total": 90,
                        "duration_sec_p50": 40,
                        "duration_sec_p95": 50,
                        "pool_top": "root.analytics",
                        "primary_bottleneck_top": "runtime_admission",
                        "score_top": "high",
                    },
                },
                {
                    "fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                    "member_count": 2,
                    "member_case_ids": ["case-007", "case-008"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 0,
                        "cte_count": 0,
                        "set_operation_count": 0,
                        "scan_count": 1,
                        "exchange_count": 0,
                        "referenced_tables": ["example_warehouse.quiet_table"],
                    },
                    "aggregates": {
                        "count": 2,
                        "duration_sec_total": 17,
                        "duration_sec_p50": 8,
                        "duration_sec_p95": 9,
                        "pool_top": "root.quiet",
                        "primary_bottleneck_top": "unknown",
                        "score_top": "clean",
                    },
                    "baseline": {
                        "schema_version": 1,
                        "regression": "none",
                        "sample_count": 4,
                        "duration_sec_p95": 10,
                    },
                },
            ],
        },
        "workload_history": {
            "schema_version": 1,
            "enabled": True,
            "loaded_record_count": 7,
            "appended_record_count": 1,
            "append_status": "ok",
            "regression_counts": {
                "strong": 1,
                "mild": 0,
                "none": 2,
                "unknown": 0,
                "unsafe": 9,
            },
            "path": "/tmp/raw-history.jsonl",
        },
    }

    workload_outcome_metrics = summarize_workload_action_outcomes(
        [
            ActionOutcomeRecord(
                schema_version=SCHEMA_VERSION,
                recorded_at_iso="2026-05-18T00:00:00+00:00",
                workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                case_fingerprint="cf_aaaaaaaaaaaaaaaaaaaaaaaa",
                case_id_local="case-001",
                recommendation_id="stats_refresh_review.v1",
                applied="yes",
                outcome="improved",
                verification_status="comparable_rerun",
            ),
            ActionOutcomeRecord(
                schema_version=SCHEMA_VERSION,
                recorded_at_iso="2026-05-18T00:05:00+00:00",
                workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                case_fingerprint="cf_bbbbbbbbbbbbbbbbbbbbbbbb",
                case_id_local="case-002",
                recommendation_id="stats_refresh_review.v1",
                applied="yes",
                outcome="no_change",
                verification_status="comparable_rerun",
            ),
            ActionOutcomeRecord(
                schema_version=SCHEMA_VERSION,
                recorded_at_iso="2026-05-18T00:10:00+00:00",
                workload_fingerprint="wf_dddddddddddddddddddddddd",
                case_fingerprint="cf_dddddddddddddddddddddddd",
                case_id_local="case-005",
                recommendation_id="runtime_admission_check.v1",
                applied="skip",
                outcome="not_applicable",
                verification_status="not_applicable",
            ),
        ]
    )

    html = render_batch_summary(
        summary,
        query_group="bad",
        workload_outcome_metrics=workload_outcome_metrics,
    )

    assert 'id="scan-context"' in html
    assert "Coverage, scan notes, and compact follow-up links for this result set." in html
    assert "Workload history: enabled; loaded 7; appended 1; regressions strong=1, none=2" in html
    assert "Workload follow-up" in html
    assert "Open repeated patterns when one query row is not enough." in html
    assert "Baseline slowdown" in html
    assert "Admission/runtime review" in html
    assert "Low-value repeat" in html
    assert "Open Workload Details;" in html
    assert 'href="/batch/workload/wf_aaaaaaaaaaaaaaaaaaaaaaaa"' in html
    assert 'href="/batch/workload/wf_dddddddddddddddddddddddd"' in html
    assert 'href="/batch/workload/wf_eeeeeeeeeeeeeeeeeeeeeeee"' in html
    assert 'href="/?query_group=workloads#recent-results">Repeated workloads</a>' in html
    assert 'href="/?query_group=regressions#recent-results">Regressed workloads</a>' in html
    assert "Repeated workload details" not in html
    assert "Workload patterns" not in html
    assert "Top workload to review" not in html
    assert "Workloads to review" not in html
    assert "Pattern categories" not in html
    assert "Top regressions" not in html
    assert "Admission/runtime workloads" not in html
    assert "Stats-gap workloads" not in html
    assert "Spill-heavy workloads" not in html
    assert "Failed/cancelled workloads" not in html
    assert "Low-value noise" not in html
    assert "Workload views" not in html
    assert 'href="#workload-action-queue">Workloads to review</a>' not in html
    assert 'id="workload-action-queue"' not in html
    assert 'id="workload-admin-digest"' not in html
    assert 'id="workload-groups"' not in html
    assert "<th>Signal / evidence</th>" not in html
    assert "<th>Open next</th>" not in html
    assert "<th>Pattern</th>" not in html
    assert "Workload details: representative cases and local baseline block." not in html
    assert "Workload p95 versus baseline p95 under comparable scan scope." not in html
    assert "Representative Details: pool, admission wait, and runtime context facts." not in html
    assert "Admission/runtime signal count and workload p95 under comparable load." not in html
    assert "Workload details and representative cases before planning one change." not in html
    assert (
        "Rerun a comparable scan after the change and confirm p95 moves toward the baseline."
        not in html
    )
    assert "top workload impact 90s." not in html
    assert "admission/runtime 1" not in html
    assert "admission/runtime 1; status issues 1" not in html
    assert "<th>Outcomes</th>" not in html
    assert (
        "2 recorded; 2 applied; 2 comparable reruns; improved 1, no change 1; "
        "last applied action Stats refresh review: no change; "
        "family signal Stats refresh review: improved 1/2 comparable reruns, no change 1; "
        "feedback sample below threshold (2/5 comparable reruns); "
        "next check stats signal count and workload p95"
    ) in html
    assert (
        "1 recorded; 0 applied; 0 comparable reruns; no verified rerun outcomes; "
        "last applied action none yet; "
        "family signal Admission/runtime check: no verified rerun records yet; "
        "feedback sample below threshold (0/5 comparable reruns); "
        "next check admission/runtime signal count and workload p95" in html
    )
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Priority</th>"
        "<th>Duration</th></tr>" in html
    )
    assert '<a class="batch-finding-link" href="/batch/case/case-001">' in html
    assert "strong regression; current p95 20s; baseline p95 12.5s; history samples 3." not in html
    assert "Stats gaps: group primary aggregate; 2 member rows." not in html
    assert "Spill-heavy: 1 of 2 member rows." not in html
    assert "Admission/runtime: 2 of 2 member rows." not in html
    assert "Status issues: 1 of 2 member rows." not in html
    assert (
        "No regression, no failed/high/suspicious rows, no spill, "
        "and no stats/admission/runtime or rewrite-review hints."
    ) not in html
    assert "<strong>Loaded records:</strong> 7" not in html
    assert "<strong>Appended records:</strong> 1" not in html
    assert "<strong>Append status:</strong> ok" not in html
    assert "<strong>Regressions:</strong> strong=1, none=2" not in html
    assert "raw-history" not in html
    assert 'href="/batch/workload/wf_aaaaaaaaaaaaaaaaaaaaaaaa">Detail</a>' not in html
    assert "strong; baseline p95 12.5s; n=3" not in html
    assert "wf_aaaaaaaa" in html
    assert "case-001, case-002" not in html
    assert "case-999" not in html
    assert "example_warehouse.fact_sales" not in html
    assert "profile.txt" not in html
    assert "wf_bbbbbbbb" not in html
    assert 'href="/batch/workload/wf_cccccccccccccccccccccccc"' not in html
    assert_no_forbidden_fragments(html)

    status_filtered_html = render_batch_summary(
        summary,
        query_group="bad",
        workload_admin_signal="status_issues",
        workload_outcome_metrics=workload_outcome_metrics,
    )
    assert "admission/runtime 1; status issues 1" not in status_filtered_html
    assert "Owner</td><td>platform</td>" not in status_filtered_html
    assert "workload_group_scope" not in status_filtered_html
    assert "workload_group_name" not in status_filtered_html
    assert "Pool and owner breakdown" not in status_filtered_html
    assert_no_forbidden_fragments(status_filtered_html)

    owner_status_html = render_batch_summary(
        summary,
        query_group="bad",
        workload_admin_scope="owner",
        workload_admin_signal="status_issues",
        workload_outcome_metrics=workload_outcome_metrics,
    )
    assert "Owner</td><td>platform</td>" not in owner_status_html
    assert "Pool</td><td>root.analytics</td>" not in owner_status_html
    assert_no_forbidden_fragments(owner_status_html)

    focused_groups_html = render_batch_summary(
        summary,
        query_group="bad",
        workload_admin_signal="status_issues",
        workload_group_scope="owner",
        workload_group_name="platform",
        workload_group_signal="status_issues",
        workload_outcome_metrics=workload_outcome_metrics,
    )
    assert "Repeated workload details" not in focused_groups_html
    assert "Workload focus" not in focused_groups_html
    assert "Owner: platform; Failed/cancelled" not in focused_groups_html
    assert "case-005, case-006" not in focused_groups_html
    assert "case-001, case-002" not in focused_groups_html
    assert "All repeated workloads</a>" not in focused_groups_html
    assert_no_forbidden_fragments(focused_groups_html)

    invalid_filter_html = render_batch_summary(
        summary,
        query_group="bad",
        workload_admin_scope="case_dir",
        workload_admin_signal="raw/profile.txt",
        workload_group_scope="owner",
        workload_group_name="case_dir",
        workload_group_signal="raw/profile.txt",
        workload_outcome_metrics=workload_outcome_metrics,
    )
    assert "case_dir" not in invalid_filter_html
    assert "raw/profile.txt" not in invalid_filter_html
    assert "Repeated workload details" not in invalid_filter_html
    assert "Workload follow-up" in invalid_filter_html
    assert_no_forbidden_fragments(invalid_filter_html)


def test_recent_scan_workload_groups_accept_broad_case_ids():
    summary = {
        "cases": [
            {
                "case_index": 1000,
                "query_id": "case-thousand:id",
                "score": 20,
                "score_severity": "suspicious",
                "duration_sec": 12,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 14,
            },
            {
                "case_index": 1176,
                "query_id": "case-broad:id",
                "score": 10,
                "score_severity": "suspicious",
                "duration_sec": 14,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 14,
            },
        ],
        "workload_groups": {
            "schema_version": 1,
            "groups": [
                {
                    "fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                    "member_count": 2,
                    "member_case_ids": ["case-1000", "case-1176", "../case-001"],
                    "aggregates": {
                        "count": 2,
                        "duration_sec_p95": 14,
                        "duration_sec_total": 26,
                    },
                }
            ],
        },
    }

    view = present_recent_scan_summary(summary)
    detail = present_workload_detail(summary, "wf_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert view.workload_groups.groups[0].member_case_ids == ("case-1000", "case-1176")
    assert detail is not None
    assert detail.member_case_ids == ("case-1000", "case-1176")


def test_recent_scan_summary_derives_workload_groups_from_repeated_safe_rows():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "case-one:id",
                "user": "alice",
                "score": 31,
                "score_severity": "high",
                "duration_sec": 10,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "case_primary_bottleneck": {"label": "stats", "confidence": "medium"},
                "stats_optimization_candidate": {
                    "tier": "medium",
                    "score": 65,
                    "impact": "medium",
                    "confidence": "medium",
                },
            },
            {
                "case_index": 2,
                "query_id": "case-two:id",
                "user": "alice /tmp/raw",
                "score": 12,
                "score_severity": "suspicious",
                "duration_sec": 25,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "case_primary_bottleneck": {"label": "stats", "confidence": "medium"},
                "stats_optimization_candidate": {
                    "tier": "medium",
                    "score": 45,
                    "impact": "medium",
                    "confidence": "medium",
                },
            },
            {
                "case_index": 3,
                "query_id": "incomplete-one:id",
                "score": 20,
                "score_severity": "suspicious",
                "duration_sec": 5,
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_fingerprint_incomplete": True,
            },
            {
                "case_index": 4,
                "query_id": "incomplete-two:id",
                "score": 20,
                "score_severity": "suspicious",
                "duration_sec": 6,
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_fingerprint_incomplete": True,
            },
        ],
        "workload_groups": {"schema_version": 1, "groups": []},
    }

    view = present_recent_scan_summary(summary)

    assert len(view.workload_groups.groups) == 1
    group = view.workload_groups.groups[0]
    assert group.fingerprint == "wf_aaaaaaaaaaaaaaaaaaaaaaaa"
    assert group.member_count == 2
    assert group.duration_sec_total == 35.0
    assert group.duration_sec_p95 == 25.0
    assert group.primary_bottleneck_top == "stats"
    assert group.score_top == "high"
    assert group.baseline_sample_count == 0
    assert group.regression == "unknown"
    assert group.shape_summary == "row-level fingerprint only; SQL shape not materialized"
    assert group.table_summary == "not materialized"
    assert group.member_case_ids == ("case-001", "case-002")
    assert len(view.workload_digest.action_queue) == 1
    assert view.workload_digest.action_queue[0].signal == "Stats review"

    detail = present_workload_detail(summary, "wf_aaaaaaaaaaaaaaaaaaaaaaaa")
    assert detail is not None
    assert detail.frequent_short_summary == (
        "Fits Frequent short: 2 runs and workload p95 25s within the 60s threshold."
    )
    assert "No local baseline is available for this fingerprint." in detail.limitations
    assert [case.case_id for case in detail.representatives] == ["case-001", "case-002"]

    html = render_batch_summary(summary, query_group="bad")

    assert "Workload follow-up" in html
    assert "Stats review" in html
    assert "Open Workload Details;" in html
    assert 'href="/batch/workload/wf_aaaaaaaaaaaaaaaaaaaaaaaa"' in html
    assert "wf_bbbbbbbb" not in html
    assert "/tmp/raw" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_summary_derives_groups_from_resolved_stale_incomplete_fields():
    workload_shape = {
        "sql_verb": "select",
        "query_type": "query",
        "join_count": 0,
        "cte_count": 0,
        "set_operation_count": 0,
        "aggregate_present": True,
        "window_present": False,
        "scan_count": 4,
        "exchange_count": 2,
        "referenced_tables": ["example_warehouse.safe_table"],
    }
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "case-one:id",
                "user": "svc",
                "score": 31,
                "score_severity": "high",
                "duration_sec": 10,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_shape": workload_shape,
                "workload_fingerprint_incomplete": True,
                "workload_fingerprint_incomplete_fields": [
                    "join_count",
                    "set_operation_count",
                ],
                "case_primary_bottleneck": {"label": "stats", "confidence": "medium"},
            },
            {
                "case_index": 2,
                "query_id": "case-two:id",
                "user": "svc",
                "score": 25,
                "score_severity": "suspicious",
                "duration_sec": 20,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_shape": workload_shape,
                "workload_fingerprint_incomplete": True,
                "workload_fingerprint_incomplete_fields": [
                    "join_count",
                    "set_operation_count",
                ],
                "case_primary_bottleneck": {"label": "stats", "confidence": "medium"},
            },
            {
                "case_index": 3,
                "query_id": "incomplete-one:id",
                "score": 20,
                "score_severity": "suspicious",
                "duration_sec": 5,
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_shape": {**workload_shape, "referenced_tables": []},
                "workload_fingerprint_incomplete": True,
                "workload_fingerprint_incomplete_fields": ["referenced_tables"],
            },
            {
                "case_index": 4,
                "query_id": "incomplete-two:id",
                "score": 20,
                "score_severity": "suspicious",
                "duration_sec": 6,
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_shape": {**workload_shape, "referenced_tables": []},
                "workload_fingerprint_incomplete": True,
                "workload_fingerprint_incomplete_fields": ["referenced_tables"],
            },
        ],
        "workload_groups": {"schema_version": 1, "groups": []},
    }

    view = present_recent_scan_summary(summary)

    assert len(view.workload_groups.groups) == 1
    group = view.workload_groups.groups[0]
    assert group.fingerprint == "wf_aaaaaaaaaaaaaaaaaaaaaaaa"
    assert group.member_count == 2
    assert group.member_case_ids == ("case-001", "case-002")
    assert [row.workload_fingerprint_short for row in view.rows] == [
        "wf_aaaaaaaa",
        "wf_aaaaaaaa",
        "",
        "",
    ]

    detail = present_workload_detail(summary, "wf_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert detail is not None
    assert detail.member_count == 2
    assert [case.case_id for case in detail.representatives] == ["case-001", "case-002"]
    assert "No local baseline is available for this fingerprint." in detail.limitations


def test_recent_scan_workload_detail_presents_representative_cases_safely():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "case-one:id",
                "user": "alice",
                "score": 31,
                "score_severity": "high",
                "duration_sec": 10,
                "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 3,
                "workload_group_duration_sec_p95": 90,
                "case_primary_bottleneck": {"label": "stats", "confidence": "medium"},
            },
            {
                "case_index": 2,
                "query_id": "case-two:id",
                "user": "bob /tmp/raw",
                "score": 12,
                "score_severity": "suspicious",
                "duration_sec": 90,
                "collection_status": "failed",
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 3,
                "workload_group_duration_sec_p95": 90,
                "case_primary_bottleneck": {"label": "sql_shape", "confidence": "medium"},
            },
            {
                "case_index": 3,
                "query_id": "case-three:id",
                "user": "carol",
                "score": 44,
                "score_severity": "high",
                "duration_sec": 30,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 3,
                "workload_group_duration_sec_p95": 90,
                "case_primary_bottleneck": {"label": "runtime_admission", "confidence": "high"},
            },
        ],
        "workload_groups": {
            "schema_version": 1,
            "groups": [
                {
                    "fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                    "member_count": 3,
                    "member_case_ids": ["case-001", "case-002", "case-003"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 1,
                        "cte_count": 0,
                        "set_operation_count": 0,
                        "scan_count": 2,
                        "exchange_count": 1,
                        "referenced_tables": ["example_warehouse.safe_table"],
                    },
                    "aggregates": {
                        "count": 3,
                        "duration_sec_total": 130,
                        "duration_sec_p50": 30,
                        "duration_sec_p95": 90,
                        "pool_top": "root.analytics /tmp/raw",
                        "primary_bottleneck_top": "runtime_admission",
                        "score_top": "high",
                    },
                    "baseline": {
                        "schema_version": 1,
                        "regression": "mild",
                        "sample_count": 8,
                        "duration_sec_p95": 40,
                    },
                }
            ],
        },
    }

    workload_outcome_metrics = summarize_workload_action_outcomes(
        [
            ActionOutcomeRecord(
                schema_version=SCHEMA_VERSION,
                recorded_at_iso="2026-05-18T00:00:00+00:00",
                workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                case_fingerprint="cf_aaaaaaaaaaaaaaaaaaaaaaaa",
                case_id_local="case-001",
                recommendation_id="query_optimization_review.v1",
                applied="yes",
                outcome="worsened",
                verification_status="comparable_rerun",
            )
        ]
    )
    view = present_workload_detail(
        summary,
        "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
        workload_outcome_metrics=workload_outcome_metrics,
    )
    assert view is not None
    summary_view = present_recent_scan_summary(
        summary,
        workload_outcome_metrics=workload_outcome_metrics,
    )
    queue_entry = summary_view.workload_digest.action_queue[0]
    assert queue_entry.fingerprint == view.fingerprint
    first_detail_action = view.action_hints[0]
    assert first_detail_action.title == queue_entry.signal
    assert first_detail_action.evidence == queue_entry.evidence
    assert first_detail_action.where_to_look == queue_entry.review_anchor
    assert first_detail_action.verification_metric == queue_entry.verification_metric
    assert first_detail_action.verification == queue_entry.verification
    assert [case.role for case in view.representatives] == [
        "Best Details case",
        "Slowest",
        "Strongest signal",
    ]

    html = render_workload_detail_view(view)

    assert "Repeated workload: Baseline slowdown" in html
    assert "Workload decision" in html
    assert "3 similar queries" in html
    assert "Workload snapshot" in html
    assert "Coverage and shape" in html
    assert "Outside Frequent short: workload p95 90s exceeds the 60s threshold." in html
    assert "observed total 130s; p95 impact about 270s" in html
    assert "root.analytics" in html
    assert "alice (1/3)" in html
    assert "Admission/runtime 1/3; SQL shape 1/3; Stats 1/3" in html
    assert "Some rows failed collection or analysis, so inspect row status first." in html
    assert "Representative queries" in html
    assert "Additional workload checks" in html
    assert "Why this pattern matters" in html
    assert "Why" in html
    assert "Where to inspect" in html
    assert "What to try" in html
    assert "What to try next" in html
    assert "How to verify" in html
    assert "Outcomes" in html
    assert (
        "1 recorded; 1 applied; 1 comparable reruns; worsened 1; "
        "last applied action Query optimization review: worsened; "
        "family signal Query optimization review: improved 0/1 comparable reruns, worsened 1; "
        "feedback sample below threshold (1/5 comparable reruns); "
        "next check query-shape signal count and workload p95"
    ) in html
    assert "Baseline slowdown" in html
    assert "Admission/runtime review" in html
    assert "Stats review" in html
    assert "Query-shape review" in html
    assert "Spill follow-up" in html
    assert "Status follow-up" in html
    assert "mild regression; current p95 90s; baseline p95 40s." in html
    assert "Workload details: representative cases and local baseline block." in html
    assert "Workload p95 versus baseline p95 under comparable scan scope." in html
    assert (
        "Rerun a comparable scan after the change and confirm p95 moves toward the baseline."
        in html
    )
    assert (
        "Record rerun outcomes from a representative Details recommendation after a comparable rerun"
        in html
    )
    assert "<th>Open</th>" in html
    assert 'href="/batch/case/case-001#action-plan">Recommendation</a>' in html
    assert "1 of 3 selected rows have stats candidate or primary-signal facts." in html
    assert "Best Details case" in html
    assert "Slowest" in html
    assert "Strongest signal" in html
    assert 'href="/batch/case/case-003"' in html
    assert "case-three:id" in html
    assert "Admission/runtime" in html
    assert "runtime_admission" not in html
    assert "local path hidden" in html
    assert "/tmp/raw" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_workload_action_queue_uses_optimizer_review_track():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "aggregate-one:id",
                "user": "analyst",
                "score": 34,
                "score_severity": "high",
                "duration_sec": 70,
                "workload_fingerprint": "wf_ffffffffffffffffffffffff",
                "group_fingerprint": "wf_ffffffffffffffffffffffff",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 90,
                "query_optimization_candidate": {
                    "score": 82,
                    "tier": "high",
                    "confidence": "medium",
                    "impact": "high",
                    "reasons": ["operator memory pressure with no supported rewrite shape"],
                    "suggested_review_areas": [],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                    "no_recipe_review_track": "grouped_aggregate_review",
                },
            },
            {
                "case_index": 2,
                "query_id": "aggregate-two:id",
                "user": "analyst",
                "score": 31,
                "score_severity": "suspicious",
                "duration_sec": 90,
                "workload_fingerprint": "wf_ffffffffffffffffffffffff",
                "group_fingerprint": "wf_ffffffffffffffffffffffff",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 90,
                "query_optimization_candidate": {
                    "score": 79,
                    "tier": "medium",
                    "confidence": "medium",
                    "impact": "high",
                    "reasons": ["operator memory pressure with no supported rewrite shape"],
                    "suggested_review_areas": [],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                    "no_recipe_review_track": "grouped_aggregate_review",
                },
            },
        ],
        "workload_groups": {
            "schema_version": 1,
            "groups": [
                {
                    "fingerprint": "wf_ffffffffffffffffffffffff",
                    "member_count": 2,
                    "member_case_ids": ["case-001", "case-002"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 0,
                        "cte_count": 0,
                        "set_operation_count": 0,
                        "scan_count": 1,
                        "exchange_count": 1,
                        "aggregate_present": True,
                        "referenced_tables": ["example_warehouse.safe_rollup"],
                    },
                    "aggregates": {
                        "count": 2,
                        "duration_sec_total": 160,
                        "duration_sec_p50": 70,
                        "duration_sec_p95": 90,
                        "pool_top": "root.analytics",
                        "primary_bottleneck_top": "unknown",
                        "score_top": "high",
                    },
                }
            ],
        },
    }

    view = present_recent_scan_summary(summary)
    assert len(view.workload_digest.action_queue) == 1
    entry = view.workload_digest.action_queue[0]

    assert entry.signal == "Query-shape review"
    assert "top review track grouped aggregate (2)" in entry.evidence
    assert entry.next_step == "Workload details for grouped aggregate review."
    assert "Representative Details: Review track: grouped aggregate" in entry.review_anchor
    assert "grouping grain, aggregate input rows, stats freshness" in entry.review_anchor
    assert "Grouped-aggregate input rows, grouping-grain estimates" in entry.verification_metric
    workload_view = present_workload_detail(summary, "wf_ffffffffffffffffffffffff")
    assert workload_view is not None
    detail_action = workload_view.action_hints[0]
    assert detail_action.title == entry.signal
    assert detail_action.evidence == entry.evidence
    assert detail_action.where_to_look == entry.review_anchor
    assert "Review grouped aggregate grain first" in detail_action.change_direction
    assert detail_action.verification_metric == entry.verification_metric
    assert detail_action.verification == entry.verification

    html = render_batch_summary(summary, query_group="bad")
    assert "Query-shape review" in html
    assert "top review track grouped aggregate (2)" in html
    assert "Open Workload Details;" in html
    assert "Workload details for grouped aggregate review." not in html
    assert "Review grouped aggregate grain first" not in html
    assert "Grouped-aggregate input rows, grouping-grain estimates" not in html
    assert_no_forbidden_fragments(view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_workload_action_queue_uses_mixed_optimizer_review_track():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "mixed-track-one:id",
                "user": "analyst",
                "score": 34,
                "score_severity": "high",
                "duration_sec": 70,
                "workload_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "group_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 90,
                "query_optimization_candidate": {
                    "score": 82,
                    "tier": "high",
                    "confidence": "medium",
                    "impact": "high",
                    "reasons": ["operator memory pressure with no supported rewrite shape"],
                    "suggested_review_areas": [],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                    "no_recipe_review_track": "derived_no_downstream_filter_review",
                },
            },
            {
                "case_index": 2,
                "query_id": "mixed-track-two:id",
                "user": "analyst",
                "score": 31,
                "score_severity": "suspicious",
                "duration_sec": 90,
                "workload_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "group_fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 90,
                "query_optimization_candidate": {
                    "score": 79,
                    "tier": "medium",
                    "confidence": "medium",
                    "impact": "high",
                    "reasons": ["operator memory pressure with no supported rewrite shape"],
                    "suggested_review_areas": [],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                    "no_recipe_review_track": "nested_query_boundary",
                },
            },
        ],
        "workload_groups": {
            "schema_version": 1,
            "groups": [
                {
                    "fingerprint": "wf_eeeeeeeeeeeeeeeeeeeeeeee",
                    "member_count": 2,
                    "member_case_ids": ["case-001", "case-002"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 1,
                        "cte_count": 0,
                        "set_operation_count": 0,
                        "scan_count": 2,
                        "exchange_count": 1,
                        "aggregate_present": True,
                        "referenced_tables": ["example_warehouse.safe_rollup"],
                    },
                    "aggregates": {
                        "count": 2,
                        "duration_sec_total": 160,
                        "duration_sec_p50": 70,
                        "duration_sec_p95": 90,
                        "pool_top": "root.analytics",
                        "primary_bottleneck_top": "unknown",
                        "score_top": "high",
                    },
                }
            ],
        },
    }

    view = present_recent_scan_summary(summary)
    assert len(view.workload_digest.action_queue) == 1
    entry = view.workload_digest.action_queue[0]

    assert entry.signal == "Query-shape review"
    assert "top review track mixed query-shape (2)" in entry.evidence
    assert entry.next_step == "Workload details for mixed query-shape review."
    assert "Representative Details: Review track: mixed query-shape review" in entry.review_anchor
    assert "per-case query-shape review tracks" in entry.review_anchor
    assert "Per-case query-shape review count" in entry.verification_metric
    workload_view = present_workload_detail(summary, "wf_eeeeeeeeeeeeeeeeeeeeeeee")
    assert workload_view is not None
    detail_action = workload_view.action_hints[0]
    assert detail_action.title == entry.signal
    assert detail_action.evidence == entry.evidence
    assert detail_action.where_to_look == entry.review_anchor
    assert "Review selected cases by their listed query-shape tracks first" in (
        detail_action.change_direction
    )
    assert detail_action.verification_metric == entry.verification_metric
    assert detail_action.verification == entry.verification
    html = render_batch_summary(summary, query_group="bad")
    assert "top review track mixed query-shape (2)" in html
    assert "Open Workload Details;" in html
    assert "Workload details for mixed query-shape review." not in html
    assert "Review selected cases by their listed query-shape tracks first" not in html
    assert_no_forbidden_fragments(view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_workload_detail_explains_frequent_short_drilldown_safely():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "short-one:id",
                "user": "svc",
                "score": 10,
                "score_severity": "suspicious",
                "duration_sec": 25,
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 30,
                "case_primary_bottleneck": {
                    "label": "runtime_admission",
                    "confidence": "medium",
                },
            },
            {
                "case_index": 2,
                "query_id": "short-two:id",
                "user": "svc",
                "score": 8,
                "score_severity": "suspicious",
                "duration_sec": 30,
                "workload_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "group_fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 30,
                "case_primary_bottleneck": {
                    "label": "runtime_admission",
                    "confidence": "medium",
                },
            },
        ],
        "workload_groups": {
            "schema_version": 1,
            "groups": [
                {
                    "fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                    "member_count": 2,
                    "member_case_ids": ["case-001", "case-002"],
                    "shape": {
                        "sql_verb": "select",
                        "query_type": "query",
                        "join_count": 0,
                        "cte_count": 0,
                        "set_operation_count": 0,
                        "scan_count": 1,
                        "exchange_count": 0,
                        "referenced_tables": ["example_warehouse.safe_table"],
                    },
                    "aggregates": {
                        "count": 2,
                        "duration_sec_total": 55,
                        "duration_sec_p50": 25,
                        "duration_sec_p95": 30,
                        "pool_top": "root.small",
                        "primary_bottleneck_top": "runtime_admission",
                        "score_top": "suspicious",
                    },
                }
            ],
        },
    }

    view = present_workload_detail(summary, "wf_bbbbbbbbbbbbbbbbbbbbbbbb")
    assert view is not None

    html = render_workload_detail_view(view)

    assert "Fits Frequent short: 2 runs and workload p95 30s within the 60s threshold." in html
    assert "observed total 55s; p95 impact about 60s" in html
    assert "root.small; svc (2/2)" in html
    assert "Admission/runtime 2/2" in html
    assert "No local baseline is available for this fingerprint." in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_detail_shows_workload_group_context_safely():
    detail_html = render_typed_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "detail:id",
            "score": 31,
            "score_severity": "high",
            "duration_sec": 78,
            "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
            "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
            "workload_group_member_count": 3,
            "workload_group_duration_sec_p95": 98.5,
            "workload_baseline_duration_sec_p95": 40.0,
            "workload_baseline_sample_count": 7,
            "workload_regression": "mild",
        },
    )

    assert "workload group" in detail_html
    assert "Similar queries in this scan: 3 · p95 98.5s" in detail_html
    assert "workload baseline" in detail_html
    assert "baseline p95 40.0s (last 7 batches)" in detail_html
    assert "regression: mild" in detail_html
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_primary_bottleneck_output_is_sanitized():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "safe:id",
                    "score": 31,
                    "score_severity": "high",
                    "case_primary_bottleneck": {
                        "label": "SELECT * FROM example_guarded_table",
                        "confidence": "high",
                        "reasons": [
                            "operator_02 SELECT secret_col FROM example_guarded_table /tmp/raw qwen3-coder raw stderr",
                        ],
                    },
                }
            ]
        },
        query_group="bad",
    )
    detail_html = render_typed_case_detail(
        "case-001",
        {
            "query_id": "safe:id",
            "score": 31,
            "score_severity": "high",
            "case_primary_bottleneck": {
                "label": "SELECT * FROM example_guarded_table",
                "confidence": "high",
                "reasons": [
                    "operator_02 SELECT secret_col FROM example_guarded_table /tmp/raw qwen3-coder raw stderr",
                ],
            },
        },
    )

    assert "Primary: Unknown (Unknown confidence)" not in html
    assert "positive score from detailed analyzer reasons" in html
    assert "Supported analyzer signals need review" in detail_html

    for body in (html, detail_html):
        assert "SELECT" not in body
        assert "secret_col" not in body
        assert "example_guarded_table" not in body
        assert_no_forbidden_fragments(body)


def test_recent_scan_summary_filters_query_groups():
    summary = {
        "selected_count": 4,
        "summaries_inspected": 12,
        "collect_cm_timeseries": False,
        "collect_prometheus_timeseries": False,
        "runtime_metrics_provider": "none",
        "cases": [
            {
                "case_index": 1,
                "query_id": "bad:id",
                "user": "alice",
                "score": 31,
                "score_severity": "high",
                "duration_sec": 12,
                "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 30,
                "workload_baseline_duration_sec_p95": 10,
                "workload_baseline_sample_count": 5,
                "workload_regression": "strong",
                "query_optimization_candidate": {
                    "score": 12,
                    "tier": "medium",
                    "confidence": "medium",
                    "impact": "medium",
                    "reasons": ["large exchange/intermediate data movement"],
                    "counter_signals": [],
                    "suggested_review_areas": ["exchange payload"],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                },
            },
            {
                "case_index": 2,
                "query_id": "suspicious:id",
                "user": "bob",
                "score": 12,
                "score_severity": "suspicious",
                "duration_sec": 30,
                "workload_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "group_fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                "workload_group_member_count": 2,
                "workload_group_duration_sec_p95": 30,
            },
            {
                "case_index": 3,
                "query_id": "good:id",
                "user": "carol",
                "score": 0,
                "score_severity": "clean",
                "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                "stats_optimization_candidate": {
                    "score": 72,
                    "tier": "high",
                    "confidence": "medium",
                    "impact": "high",
                    "need_type": "table_and_column_stats",
                    "speed_benefit": "medium",
                    "reasons": ["missing table stats before expensive join"],
                    "counter_signals": [],
                    "suggested_review_areas": ["table/partition row counts"],
                    "required_confirmation": ["compare EXPLAIN before and after stats collection"],
                },
            },
            {
                "case_index": 4,
                "query_id": "ready:id",
                "user": "dave",
                "score": 0,
                "score_severity": "clean",
                "duration_sec": 80,
                "_optimizer_artifact_status": "trusted_draft",
                "query_optimization_candidate": {
                    "score": 55,
                    "tier": "medium",
                    "confidence": "medium",
                    "impact": "medium",
                    "reasons": ["join row expansion or cardinality mismatch with join evidence"],
                    "counter_signals": [],
                    "suggested_review_areas": ["join keys and join cardinality"],
                },
                "optimizer_rewrite_support": {
                    "status": "sql_draft_supported",
                    "label": "SQL draft eligible",
                    "rewriteability_bucket": "safe_material_draft",
                    "rewriteability_label": "Safe material draft",
                },
            },
        ],
    }

    bad_html = render_batch_summary(summary, query_group="bad")
    suspicious_html = render_batch_summary(summary, query_group="suspicious")
    workloads_html = render_batch_summary(summary, query_group="workloads")
    frequent_short_html = render_batch_summary(summary, query_group="frequent_short")
    regressions_html = render_batch_summary(summary, query_group="regressions")
    optimization_html = render_batch_summary(summary, query_group="optimization")
    stats_html = render_batch_summary(summary, query_group="stats")
    zero_outcomes_html = render_batch_summary(summary, action_outcomes_recorded=0)
    recorded_outcomes_html = render_batch_summary(summary, action_outcomes_recorded=2)
    spilled_stats_html = render_batch_summary(summary, query_group="stats", only_with_spills=True)
    spilled_suspicious_html = render_batch_summary(
        summary, query_group="suspicious", only_with_spills=True
    )

    assert "bad:id" in bad_html
    assert '<td class="batch-cell--user">alice</td>' in bad_html
    assert "suspicious:id" not in bad_html
    assert "good:id" not in bad_html
    assert "suspicious:id" in suspicious_html
    assert "bad:id" not in suspicious_html
    assert "bad:id" not in workloads_html
    assert "suspicious:id" not in workloads_html
    assert "good:id" not in workloads_html
    assert "Repeated workload: 2 similar queries" in workloads_html
    assert 'data-href="/batch/workload/wf_aaaaaaaaaaaaaaaaaaaaaaaa"' in workloads_html
    assert (
        '<a class="batch-finding-link" href="/batch/workload/wf_aaaaaaaaaaaaaaaaaaaaaaaa">'
        in workloads_html
    )
    assert "alice (1/2)" in workloads_html
    assert "row-level fingerprint only; SQL shape not materialized" in workloads_html
    assert "Frequent short workload: 2 similar queries" in frequent_short_html
    assert "current scan impact about 60s" in frequent_short_html
    assert "suspicious:id" in frequent_short_html
    assert "bad:id" not in frequent_short_html
    assert "Frequent short limitations:" in frequent_short_html
    assert (
        "Ranks only the 4 analyzed cases selected from 12 scanned summaries." in frequent_short_html
    )
    assert "Some analyzed cases have no complete workload fingerprint" in frequent_short_html
    assert "duration and repetition do not prove admission pressure" in frequent_short_html
    assert "Frequent short limitations:" not in stats_html
    assert_no_forbidden_fragments(frequent_short_html)
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Runs</th>"
        "<th>Duration</th><th>Workload p95</th><th>Workload impact</th><th>Primary</th>"
    ) in frequent_short_html
    assert "Regressed workload: Strong" in regressions_html
    assert "bad:id" in regressions_html
    assert "suspicious:id" not in regressions_html
    assert (
        "<th>Rank</th><th>Workload</th><th>Priority</th><th>p95</th>"
        "<th>Total impact</th><th>Top owner</th></tr>"
    ) in workloads_html
    assert "<th>Runs</th>" not in workloads_html
    assert "<th>p50</th>" not in workloads_html
    assert "<th>Pool</th>" not in workloads_html
    assert "<th>Primary</th>" not in workloads_html
    assert "bad:id" in optimization_html
    assert "ready:id" in optimization_html
    assert "suspicious:id" not in optimization_html
    assert "good:id" in stats_html
    assert "bad:id" not in stats_html
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Candidate</th>"
        "<th>Duration</th><th>Need</th><th>Speed benefit</th><th>Confidence</th>"
    ) in stats_html
    assert (
        '<td class="batch-cell--reason">table/partition stats first, then column stats</td>'
        in stats_html
    )
    assert (
        '<td class="batch-cell--reason">compare EXPLAIN before and after stats collection</td>'
        not in stats_html
    )
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Candidate</th>"
        "<th>Duration</th><th>Impact</th><th>Confidence</th><th>Rewrite support</th>"
    ) in optimization_html
    assert "Review: exchange payload" in optimization_html
    assert "Needs attention <span>1</span>" in stats_html
    assert "Worth reviewing <span>1</span>" in stats_html
    assert "View" in stats_html
    assert "<summary>More filters</summary>" not in stats_html
    assert "batch-filter-more" not in stats_html
    assert "Only queries with spills" in stats_html
    assert "Sort" in stats_html
    assert "Start time" in stats_html
    assert "Optimizer-ready" not in stats_html
    assert "Rewrite opportunities <span>2</span>" in stats_html
    assert "Repeated workloads <span>1</span>" in stats_html
    assert "Frequent short <span>1</span>" in stats_html
    assert "Regressed workloads <span>1</span>" in stats_html
    assert "<span>Rewrite draft-ready: 1</span>" not in stats_html
    assert "<span>Rewrite recipe backlog: 0</span>" not in stats_html
    assert "<span>Rewrite review-only: 1</span>" not in stats_html
    assert stats_html.index('class="batch-table-wrap"') < stats_html.index(
        'class="batch-results-context"'
    )
    assert (
        '<section id="scan-context" class="batch-results-context" aria-label="Scan context">'
        in (stats_html)
    )
    assert "<h2>Scan context</h2>" in stats_html
    assert "Coverage, scan notes, and compact follow-up links for this result set." in stats_html
    assert 'class="batch-context-block batch-context-scan-details"' in stats_html
    assert '<div class="batch-context-title">Coverage</div>' in stats_html
    assert 'class="batch-context-block batch-context-notes"' not in stats_html
    assert '<details class="batch-notices" aria-label="Scan notes" open>' not in stats_html
    assert '<div class="batch-context-title">Scan notes</div>' not in stats_html
    assert "<strong>Rewrite guidance</strong>" not in stats_html
    assert (
        "Open Details for the supported next step, verification anchor, and rewrite scope."
        not in stats_html
    )
    assert "<strong>Action outcomes</strong>" not in zero_outcomes_html
    assert '<strong>Action outcomes</strong><span><a href="/outcomes">2</a> recorded</span>' in (
        recorded_outcomes_html
    )
    assert "Stats to check <span>1</span>" in stats_html
    assert "Good queries" not in stats_html
    assert "good:id" in spilled_stats_html
    assert "suspicious:id" not in spilled_suspicious_html
    assert (
        "No rows worth reviewing with spill evidence matched this result filter."
        in spilled_suspicious_html
    )
    assert "Clear the spill filter to see all rows in this group." in spilled_suspicious_html
    assert 'href="/?query_group=stats&only_with_spills=on#recent-results"' in stats_html
    assert 'class="batch-spill-toggle batch-spill-toggle--active"' in spilled_stats_html
    assert 'href="/?query_group=stats#recent-results"' in spilled_stats_html
    assert "Worth reviewing <span>0</span>" in spilled_stats_html
    assert "Repeated workloads <span>1</span>" in spilled_stats_html
    assert "Frequent short <span>1</span>" in spilled_stats_html
    assert "Regressed workloads <span>1</span>" in spilled_stats_html


def test_recent_scan_optimization_group_includes_low_tier_review_guidance():
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "review:id",
                "user": "alice",
                "score": 20,
                "score_severity": "suspicious",
                "duration_sec": 30,
                "query_optimization_candidate": {
                    "score": 35,
                    "tier": "low",
                    "confidence": "medium",
                    "impact": "medium",
                    "reasons": ["large exchange volume before downstream processing"],
                    "counter_signals": [],
                    "suggested_review_areas": ["exchange payload"],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                },
            },
            {
                "case_index": 2,
                "query_id": "skip:id",
                "user": "bob",
                "score": 0,
                "score_severity": "clean",
                "duration_sec": 5,
                "query_optimization_candidate": {
                    "score": 20,
                    "tier": "low",
                    "confidence": "low",
                    "impact": "low",
                    "reasons": ["moderate runtime"],
                    "counter_signals": ["no query-shape opportunity evidence"],
                    "suggested_review_areas": [],
                },
                "optimizer_rewrite_support": {
                    "status": "not_candidate",
                    "label": "Not an optimization candidate",
                    "reason": "No medium/high optimization candidate evidence",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
                },
            },
        ]
    }

    html = render_batch_summary(summary, query_group="optimization")

    assert "review:id" in html
    assert "skip:id" not in html
    assert "Rewrite opportunities <span>1</span>" in html
    assert "<span>Rewrite review-only: 1</span>" not in html
    assert "Review guidance only" in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_stats_candidate_output_is_sanitized_and_sorted():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "low:id",
                    "user": "alice",
                    "score": 0,
                    "score_severity": "clean",
                    "duration_sec": 999,
                    "stats_optimization_candidate": {
                        "score": 20,
                        "tier": "medium",
                        "confidence": "low",
                        "impact": "high",
                        "need_type": "insufficient_metadata",
                        "speed_benefit": "unknown",
                        "reasons": ["BEGIN PROFILE SELECT * FROM /tmp/raw"],
                        "counter_signals": ["metadata was not collected or is insufficient"],
                        "suggested_review_areas": ["SHOW CREATE TABLE"],
                        "required_confirmation": [
                            "compare EXPLAIN before and after stats collection"
                        ],
                    },
                },
                {
                    "case_index": 2,
                    "query_id": "high:id",
                    "user": "bob",
                    "score": 7,
                    "score_severity": "suspicious",
                    "duration_sec": 20,
                    "stats_optimization_candidate": {
                        "score": 80,
                        "tier": "high",
                        "confidence": "medium",
                        "impact": "medium",
                        "need_type": "table_stats",
                        "speed_benefit": "medium",
                        "reasons": ["missing or unknown table/partition row-count stats"],
                        "counter_signals": [],
                        "suggested_review_areas": ["table/partition row counts"],
                        "required_confirmation": [
                            "compare EXPLAIN before and after stats collection"
                        ],
                    },
                },
            ]
        },
        query_group="stats",
    )

    assert html.index("high:id") < html.index("low:id")
    assert '<td class="batch-cell--compact">1</td><td class="batch-cell--summary">' in html
    assert '<td class="batch-cell--summary"><strong>Stats candidate: High</strong>' in html
    assert '<td class="batch-cell--query-id">high:id</td>' in html
    for fragment in FORBIDDEN_DISPLAY_FRAGMENTS:
        assert fragment not in html
    assert "raw profile hidden" in html
    assert "metadata statement hidden" in html
    assert "SELECT *" not in html
    assert '<td class="batch-cell--user">bob</td>' in html
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Candidate</th>"
        "<th>Duration</th><th>Need</th><th>Speed benefit</th><th>Confidence</th>"
    ) in html


def test_recent_scan_optimization_candidate_output_is_sanitized_and_sorted():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "low:id",
                    "user": "alice",
                    "score": 0,
                    "score_severity": "clean",
                    "duration_sec": 999,
                    "query_optimization_candidate": {
                        "score": 15,
                        "tier": "medium",
                        "confidence": "low",
                        "impact": "high",
                        "reasons": ["BEGIN PROFILE SELECT * FROM /tmp/raw"],
                        "counter_signals": ["no query-shape opportunity evidence"],
                        "suggested_review_areas": ["SHOW CREATE TABLE"],
                    },
                    "optimizer_rewrite_support": {
                        "status": "guidance_only",
                        "label": "Guidance only",
                        "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                    },
                },
                {
                    "case_index": 2,
                    "query_id": "high:id",
                    "user": "bob",
                    "score": 7,
                    "score_severity": "suspicious",
                    "duration_sec": 20,
                    "query_optimization_candidate": {
                        "score": 80,
                        "tier": "high",
                        "confidence": "medium",
                        "impact": "medium",
                        "reasons": [
                            "join row expansion or cardinality mismatch with join evidence"
                        ],
                        "counter_signals": [],
                        "suggested_review_areas": ["join keys and join cardinality"],
                    },
                    "optimizer_rewrite_support": {
                        "status": "recipe_detected",
                        "label": "Rewrite recipe detected",
                        "reason": "Linear CTE predicate pushdown recipe is available; an explicit optimizer run and validation are still required",
                        "cte_count": 2,
                        "cte_graph_shape": "linear_chain",
                        "cte_predicate_origin_status": "final_select_filter",
                        "cte_predicate_path_status": "single_dependency_path",
                        "cte_projection_preservation_status": "simple_projection_preserved",
                        "cte_simplification_status": "pass_through_candidate",
                        "cte_simple_projection_count": 2,
                        "cte_expression_projection_count": 0,
                        "cte_boundary_reasons": [
                            "cte_body_validation_not_proven",
                            "pass_through_cte",
                            "/tmp/raw SELECT * FROM cte_1",
                        ],
                    },
                    "source_locators": {
                        "query_optimization": [
                            {
                                "id": "sql_final_select_filter",
                                "coordinate": "line 24",
                                "detail": "predicate near final SELECT",
                            },
                            {
                                "id": "plan_cardinality_anomaly",
                                "detail": "node 02 HASH JOIN",
                            },
                        ]
                    },
                },
            ]
        },
        query_group="optimization",
    )

    assert html.index("high:id") < html.index("low:id")
    assert '<td class="batch-cell--compact">1</td><td class="batch-cell--summary">' in html
    assert (
        '<td class="batch-cell--summary"><strong>Query optimization candidate: High</strong>'
        in html
    )
    assert '<td class="batch-cell--query-id">high:id</td>' in html
    assert "<th>Rewrite support</th>" in html
    assert "Recipe found" in html
    assert "Guidance only" in html
    assert "Linear CTE predicate pushdown recipe is available" in html
    assert (
        "Facts: 2 CTEs; linear CTE chain; final SELECT filter; single dependency path; simple projections preserved"
        in html
    )
    assert (
        "Guardrails: pass-through simplification candidate; 2 simple projections; CTE body validation not proven; pass-through CTE"
        in html
    )
    for fragment in FORBIDDEN_DISPLAY_FRAGMENTS:
        assert fragment not in html
    assert "raw profile hidden" in html
    assert "metadata statement hidden" in html
    assert "SELECT *" not in html
    assert "cte_1" not in html
    assert "join row expansion or cardinality mismatch with join evidence" in html
    assert "cardinality mismatch [SQL hidden]" not in html
    assert 'class="source-location-chip source-location-chip--sql"' in html
    assert ">line 24</span>" in html


def test_recent_scan_optimization_candidate_tie_breaks_by_optimizer_status():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "partial:id",
                    "duration_sec": 400,
                    "_optimizer_artifact_status": "partial_untrusted",
                    "query_optimization_candidate": {
                        "score": 70,
                        "tier": "high",
                        "confidence": "high",
                        "impact": "high",
                    },
                },
                {
                    "case_index": 2,
                    "query_id": "trusted:id",
                    "duration_sec": 100,
                    "_optimizer_artifact_status": "trusted_draft",
                    "query_optimization_candidate": {
                        "score": 70,
                        "tier": "high",
                        "confidence": "high",
                        "impact": "high",
                    },
                },
            ]
        },
        query_group="optimization",
    )

    assert html.index("trusted:id") < html.index("partial:id")
    assert "Open draft" not in html
    assert "Validate manually" not in html


def test_recent_scan_optimizer_artifact_status_tokens_are_not_rendered():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "case-one",
                    "_optimizer_artifact_status": "partial_untrusted",
                    "query_optimization_candidate": {
                        "score": 70,
                        "tier": "high",
                        "confidence": "high",
                        "impact": "high",
                    },
                },
                {
                    "case_index": 2,
                    "query_id": "case-two",
                    "_optimizer_artifact_status": "trusted_draft",
                    "query_optimization_candidate": {
                        "score": 70,
                        "tier": "high",
                        "confidence": "high",
                        "impact": "high",
                    },
                },
            ]
        },
        query_group="optimization",
    )

    assert "case-one" in html
    assert "case-two" in html
    assert "_optimizer_artifact_status" not in html
    assert "partial_untrusted" not in html
    assert "trusted_draft" not in html


def test_recent_scan_optimization_candidate_tie_breaks_by_rewriteability_bucket():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "guidance:id",
                    "duration_sec": 400,
                    "query_optimization_candidate": {
                        "score": 90,
                        "tier": "high",
                        "confidence": "high",
                        "impact": "high",
                    },
                    "optimizer_rewrite_support": {
                        "status": "guidance_only",
                        "label": "Guidance only",
                        "reason": "No Python-owned recipe is available",
                        "rewriteability_bucket": "human_review_only",
                        "rewriteability_label": "Human review only",
                    },
                },
                {
                    "case_index": 2,
                    "query_id": "draftable:id",
                    "duration_sec": 100,
                    "query_optimization_candidate": {
                        "score": 70,
                        "tier": "high",
                        "confidence": "medium",
                        "impact": "medium",
                    },
                    "optimizer_rewrite_support": {
                        "status": "sql_draft_supported",
                        "label": "SQL draft eligible",
                        "reason": "Python-owned recipe is available",
                        "rewriteability_bucket": "safe_material_draft",
                        "rewriteability_label": "Safe material draft",
                    },
                },
            ]
        },
        query_group="optimization",
    )

    assert html.index("draftable:id") < html.index("guidance:id")
    assert "SQL eligible" in html
    assert "Human review" in html


def test_recent_scan_optimization_candidate_explains_human_review_guardrails():
    html = render_batch_summary(
        {
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "human-review:id",
                    "duration_sec": 400,
                    "query_optimization_candidate": {
                        "score": 90,
                        "tier": "high",
                        "confidence": "high",
                        "impact": "high",
                    },
                    "optimizer_rewrite_support": {
                        "status": "draft_disabled",
                        "label": "Recipe detected; draft disabled",
                        "reason": "SELECT * FROM example_guarded_table /tmp/raw qwen3-coder",
                        "risk_reasons": [
                            "cte_body_validation_not_proven",
                            "sql_payload_too_large_for_safe_rewrite",
                            "/tmp/raw SELECT secret",
                        ],
                        "draft_eligibility": "disabled_by_safety_thresholds",
                        "rewriteability_bucket": "human_review_only",
                        "rewriteability_label": "Human review only",
                    },
                },
            ]
        },
        query_group="optimization",
    )

    assert "Human review" in html
    assert "Human review only" in html
    assert "Trusted SQL draft disabled by safety and validation guardrails" in html
    assert (
        "Guardrails: CTE body validation not proven; SQL payload too large for safe rewrite" in html
    )
    assert "SELECT" not in html
    assert "example_guarded_table" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_optimization_candidate_explains_review_guidance_only():
    case = {
        "case_index": 1,
        "query_id": "review-only:id",
        "duration_sec": 400,
        "_detail_optimization_rank": 1,
        "query_optimization_candidate": {
            "score": 80,
            "tier": "high",
            "confidence": "medium",
            "impact": "high",
            "reasons": ["operator memory pressure with no supported rewrite shape"],
            "suggested_review_areas": ["joins, exchange boundaries, and memory-heavy operators"],
        },
        "optimizer_rewrite_support": {
            "status": "guidance_only",
            "label": "Guidance only",
            "reason": "No Python-owned SQL rewrite recipe is available for this shape",
            "rewriteability_bucket": "not_rewriteable",
            "rewriteability_label": "Not rewriteable",
            "no_recipe_review_track": "filtered_scalar_aggregate_review",
        },
    }

    summary_html = render_batch_summary({"cases": [case]}, query_group="optimization")
    detail_html = render_typed_case_detail("case-001", case)

    assert "Review only" in summary_html
    assert "Review guidance only" in summary_html
    assert "No trusted SQL draft shape detected" in summary_html
    assert "Verdict" in detail_html
    assert "Recommended change" in detail_html
    assert "Start with the recommendation below" not in detail_html
    assert (
        "No trusted SQL draft will be generated for this case by the current deterministic optimizer"
        in detail_html
    )
    assert "Rewriteability: Not rewriteable" in detail_html
    assert "Facts: Review track: filtered scalar aggregate" in detail_html
    assert "Review filtered scalar aggregate input first" in detail_html
    assert (
        "predicate selectivity, partition pruning, stats freshness, and aggregate input rows"
        in detail_html
    )
    assert "Compare EXPLAIN scan pruning, aggregate input rows, and estimate quality" in detail_html
    assert "No trusted SQL draft shape detected" in detail_html
    assert_no_forbidden_fragments(summary_html)
    assert_no_forbidden_fragments(detail_html)


def test_recent_scan_optimization_candidate_ignores_unknown_no_recipe_review_track():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "unknown-track:id",
            "duration_sec": 400,
            "_detail_optimization_rank": 1,
            "query_optimization_candidate": {
                "score": 80,
                "tier": "high",
                "confidence": "medium",
                "impact": "high",
                "reasons": ["operator memory pressure with no supported rewrite shape"],
                "suggested_review_areas": [],
            },
            "optimizer_rewrite_support": {
                "status": "guidance_only",
                "label": "Guidance only",
                "reason": "SELECT * FROM example_guarded_table /tmp/raw qwen3-coder",
                "rewriteability_bucket": "not_rewriteable",
                "rewriteability_label": "Not rewriteable",
                "no_recipe_review_track": "SELECT secret_col FROM example_guarded_table",
            },
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    query_action = action_view.cards[0]

    assert query_action.change_direction == (
        "Review query shape; make only row-reduction or shape changes that can be explained "
        "by the listed deterministic facts or by a trusted optimizer outcome."
    )
    assert "secret_col" not in repr(action_view)
    assert "example_guarded_table" not in repr(action_view)
    assert "qwen3-coder" not in repr(action_view)
    assert_no_forbidden_fragments(action_view)


def test_recent_scan_detail_shows_candidate_context_safely():
    html = render_typed_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "detail:id",
            "user": "alice",
            "score": 7,
            "score_severity": "suspicious",
            "duration_sec": 120,
            "_detail_optimization_rank": 2,
            "_detail_stats_rank": 4,
            "query_optimization_candidate": {
                "score": 80,
                "tier": "high",
                "confidence": "medium",
                "impact": "high",
                "reasons": ["join row expansion or cardinality mismatch with join evidence"],
                "counter_signals": [
                    "some cardinality mismatch may also require statistics refresh"
                ],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
            "optimizer_rewrite_support": {
                "status": "guidance_only",
                "label": "Guidance only",
                "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                "rewriteability_bucket": "recipe_adjacent_shape",
                "rewriteability_label": "Recipe-adjacent shape",
                "no_recipe_review_track": "cte_simplification_review",
                "cte_count": 3,
                "cte_graph_shape": "cte_dag",
                "cte_predicate_origin_status": "mixed_downstream_filters",
                "cte_predicate_path_status": "mixed_dependency_paths",
                "cte_projection_preservation_status": "named_expression_projection",
                "cte_simplification_status": "pass_through_candidate",
                "cte_simple_projection_count": 1,
                "cte_expression_projection_count": 2,
                "cte_boundary_reasons": [
                    "cte_body_validation_not_proven",
                    "fanin_cte_graph",
                    "set_operation_boundary",
                ],
            },
            "stats_optimization_candidate": {
                "score": 55,
                "tier": "medium",
                "confidence": "medium",
                "impact": "medium",
                "need_type": "table_stats",
                "speed_benefit": "medium",
                "reasons": ["missing or unknown table/partition row-count stats"],
                "counter_signals": ["query shape may still need SQL review"],
                "suggested_review_areas": ["table/partition row counts"],
                "required_confirmation": ["compare EXPLAIN before and after stats collection"],
                "evidence_detail": ["partition row-count coverage partial: 6/10 known, 4 unknown"],
            },
            "source_locators": {
                "query_optimization": [
                    {
                        "id": "sql_final_select_filter",
                        "coordinate": "line 11",
                        "detail": "SELECT secret_col FROM example_guarded_table /tmp/raw",
                    },
                    {
                        "id": "plan_cardinality_anomaly",
                        "coordinate": "SELECT secret_col FROM example_guarded_table",
                        "detail": "node 02 HASH JOIN",
                    },
                    {"id": "unknown_locator", "detail": "raw stdout /Users/example"},
                ],
                "stats_refresh": [
                    {"id": "metadata_referenced_stats", "detail": "3 referenced tables"},
                    {"id": "plan_memory_anomaly", "detail": "node 05 AGGREGATE"},
                ],
            },
        },
    )

    assert "Query-shape recommendation" in html
    assert "Stats maintenance recommendation" in html
    assert "alice" in html
    assert "Candidate strength: High. Score: 80/100. Rank: #2." in html
    assert "Rewrite support: Guidance only" in html
    assert "Rewriteability: Recipe-adjacent shape" in html
    assert (
        "Facts: 3 CTEs; Review track: CTE simplification; CTE DAG; mixed downstream filters; mixed dependency paths"
        in html
    )
    assert (
        "Guardrails: pass-through simplification candidate; 1 simple projection, 2 expression projections; CTE body validation not proven; fan-in CTE graph; set-operation boundary"
        in html
    )
    assert "Candidate strength: Medium. Score: 55/100. Rank: #4." in html
    assert "join row expansion or cardinality mismatch with join evidence" in html
    assert "Need: table/partition stats" in html
    assert "Keep in mind: some cardinality mismatch may also require statistics update" in html
    assert "Where to inspect" in html
    assert "Why this deserves attention" in html
    assert "What to change" in html
    assert "How to verify" in html
    assert "Try to reduce rows earlier: move the final SELECT filter closer" in html
    assert "Compare the CTE dependency path, output columns" in html
    assert (
        "after the change, check whether fewer rows or better estimates feed that operator" in html
    )
    assert (
        "Structured metadata detail: partition row-count coverage partial: 6/10 known, 4 unknown"
    ) in html
    assert "Confirm and refresh the partition row-count gaps for referenced physical tables" in html
    assert "Use the marked memory-pressure operator as a secondary before/after anchor" in html
    assert "compare EXPLAIN before and after stats collection" in html
    assert "SQL: final SELECT filter (line 11)" in html
    assert 'class="source-location-chip source-location-chip--sql"' in html
    assert 'class="redacted-source-map"' in html
    assert "source text hidden" in html
    assert ">line 11</span>" in html
    assert "source-location-chip--plan" not in html
    assert "Plan: estimate-mismatch operator: node 02 HASH JOIN" in html
    assert "Metadata: referenced table stats: 3 referenced tables" in html
    assert "Plan: memory-pressure operator: node 05 AGGREGATE" in html
    assert "unknown_locator" not in html
    assert "secret_col" not in html
    assert "example_guarded_table" not in html
    for fragment in FORBIDDEN_DISPLAY_FRAGMENTS:
        assert fragment not in html


def test_recent_scan_action_candidate_view_renderer_matches_legacy_adapter():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "detail:id",
            "user": "alice /tmp/case_dir",
            "score": 7,
            "score_severity": "suspicious",
            "_detail_optimization_rank": 2,
            "_detail_stats_rank": 4,
            "table_stats_status": "missing_or_incomplete",
            "query_optimization_candidate": {
                "score": 80,
                "tier": "high",
                "confidence": "medium",
                "impact": "high",
                "reasons": ["join row expansion SELECT raw stdout /Users/example"],
                "counter_signals": [
                    "some cardinality mismatch may also require statistics refresh"
                ],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
            "optimizer_rewrite_support": {
                "status": "guidance_only",
                "label": "Guidance only",
                "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                "rewriteability_bucket": "recipe_adjacent_shape",
                "rewriteability_label": "Recipe-adjacent shape",
                "cte_count": 3,
                "cte_graph_shape": "cte_dag",
            },
            "stats_optimization_candidate": {
                "score": 55,
                "tier": "medium",
                "confidence": "medium",
                "impact": "medium",
                "need_type": "table_stats",
                "speed_benefit": "medium",
                "reasons": ["missing or unknown table/partition row-count stats"],
                "suggested_review_areas": ["table/partition row counts"],
                "required_confirmation": ["compare EXPLAIN before and after stats collection"],
            },
        },
    )
    action_view = present_recent_scan_action_candidates(view)
    query_action = action_view.cards[0]
    stats_action = action_view.cards[1]

    assert render_action_candidate_findings_view(action_view) == render_action_candidate_findings(
        view
    )
    assert "Query-shape recommendation" in render_action_candidate_findings_view(action_view)
    assert "Stats maintenance recommendation" in render_action_candidate_findings_view(action_view)
    assert [fact.fact_id for fact in query_action.supporting_facts] == [
        "signals",
        "table_stats",
    ]
    assert [fact.fact_id for fact in stats_action.supporting_facts] == [
        "table_stats",
        "signals",
    ]
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(render_action_candidate_findings_view(action_view))


def test_recent_scan_query_action_card_preserves_stats_split_caveat():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "shape-caveat:id",
            "duration_sec": 120,
            "_detail_optimization_rank": 1,
            "query_optimization_candidate": {
                "score": 64,
                "tier": "medium",
                "confidence": "medium",
                "impact": "medium",
                "reasons": ["join row expansion or cardinality mismatch with join evidence"],
                "counter_signals": [
                    "admission wait is a material runtime component",
                    "very short query",
                    "metadata was not collected, so stats-vs-query-shape split is unconfirmed",
                    "some cardinality mismatch may also require statistics refresh",
                ],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
            "optimizer_rewrite_support": {
                "status": "guidance_only",
                "label": "Guidance only",
                "reason": "No Python-owned SQL rewrite recipe is available for this shape",
                "rewriteability_bucket": "human_review_only",
                "rewriteability_label": "Human review only",
            },
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    query_card = action_view.cards[0]
    html = render_action_candidate_findings_view(action_view)

    assert query_card.title == "Query-shape recommendation"
    assert "stats-vs-query-shape split is unconfirmed" in query_card.why
    assert "may also require statistics update" in query_card.why
    assert "rerun under comparable load" in query_card.verification
    assert "stats-vs-query-shape split is unconfirmed" in html
    assert "may also require statistics update" in html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_stats_action_card_preserves_generic_column_stats_caveat():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "stats-caveat:id",
            "duration_sec": 120,
            "_detail_stats_rank": 1,
            "stats_optimization_candidate": {
                "score": 62,
                "tier": "medium",
                "confidence": "medium",
                "impact": "medium",
                "need_type": "column_stats",
                "speed_benefit": "medium",
                "reasons": ["missing or incomplete column statistics"],
                "counter_signals": [
                    "metadata collection was partial",
                    "query shape may still need SQL review",
                    "column stats gap is not tied to specific join/filter columns",
                ],
                "suggested_review_areas": ["column statistics"],
                "required_confirmation": [
                    "compare EXPLAIN before and after stats collection",
                ],
                "evidence_detail": ["column stats incomplete/unknown"],
            },
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    stats_card = action_view.cards[0]
    html = render_action_candidate_findings_view(action_view)

    assert stats_card.title == "Stats maintenance recommendation"
    assert "not tied to specific join/filter columns" in stats_card.why
    assert "not tied to specific join/filter columns" in html
    assert "metadata collection was partial" in stats_card.why
    assert "rerun under comparable load" in stats_card.verification
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_action_candidate_falls_back_for_high_score_without_candidate_tiers():
    view = present_recent_scan_case_detail(
        "case-170",
        {
            "case_index": 170,
            "query_id": "54b40ea45c167c6:111b8bcd00000000",
            "user": "analyst",
            "score": 27,
            "duration_sec": 5.178,
            "score_reasons": [
                "cardinality estimate anomalies: 3",
                "memory estimate anomalies: 2",
                "backend data skew evidence",
            ],
            "cardinality_anomaly_count": 3,
            "memory_anomaly_count": 2,
            "backend_data_skew": True,
            "case_primary_bottleneck": {
                "label": "sql_shape",
                "confidence": "low",
                "reasons": ["join_top_finding"],
            },
            "query_optimization_candidate": {
                "score": 35,
                "tier": "low",
                "confidence": "high",
                "impact": "low",
                "reasons": ["large exchange/intermediate volume"],
                "suggested_review_areas": ["data distribution", "pre-aggregation before exchange"],
            },
            "stats_optimization_candidate": {
                "score": 37,
                "tier": "low",
                "confidence": "low",
                "impact": "low",
                "need_type": "not_likely_stats_issue",
            },
        },
    )

    action_view = present_recent_scan_action_candidates(view)
    html = render_action_candidate_findings(view)

    assert len(action_view.cards) == 1
    assert action_view.cards[0].title == "SQL shape follow-up"
    assert action_view.cards[0].recommendation_id == "diagnostic_follow_up.v1"
    assert "No Medium/High rewrite or stats candidate was selected" in action_view.cards[0].body
    assert "This is a review direction, not a root-cause claim." in action_view.cards[0].why
    assert "SQL shape follow-up" in html
    assert "Inspect the join, aggregation, filter, and exchange shape" in html
    assert "No prioritized rewrite or stats action" not in html
    assert_no_forbidden_fragments(action_view)
    assert_no_forbidden_fragments(html)


def test_recent_scan_action_candidate_card_renders_owner_coordinate_guidance():
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "owner-coordinate:id",
            "score": 82,
            "score_severity": "high",
            "_detail_optimization_rank": 1,
            "query_optimization_candidate": {
                "score": 88,
                "tier": "high",
                "confidence": "medium",
                "impact": "high",
                "reasons": ["join row expansion or cardinality mismatch with join evidence"],
                "counter_signals": ["stats refresh may still be needed"],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
            "optimizer_rewrite_support": {
                "status": "guidance_only",
                "label": "Guidance only",
                "reason": "Manual review only",
                "rewriteability_bucket": "recipe_adjacent_shape",
                "rewriteability_label": "Recipe-adjacent shape",
                "cte_count": 2,
                "cte_graph_shape": "linear_chain",
                "cte_predicate_origin_status": "final_select_filter",
            },
            "source_locators": {
                "query_optimization": [
                    {
                        "id": "sql_final_select_filter",
                        "coordinate": "line 18",
                        "detail": "predicate near final SELECT",
                    },
                    {
                        "id": "plan_cardinality_anomaly",
                        "detail": "node 02 HASH JOIN (inner join, partitioned)",
                    },
                ]
            },
        },
        query_context_facts={
            "summary": {
                "available": "yes",
                "bytes_read": "42.00 GiB",
                "memory_aggregate_peak": "18.00 GiB",
            }
        },
    )

    html = render_action_candidate_findings(view)
    action_view = present_recent_scan_action_candidates(view)
    action = action_view.cards[0]

    assert [fact.fact_id for fact in action.supporting_facts] == ["resource_footprint", "signals"]
    assert_contains_in_order(
        html,
        [
            "Query-shape recommendation",
            "What to change",
            "Try to reduce rows earlier: move the final SELECT filter closer",
            "after the change, check whether fewer rows or better estimates feed that operator",
            "How to verify",
            "Compare EXPLAIN before and after the change",
            "Where to inspect",
            "source-location-chip source-location-chip--sql",
            "SQL: final SELECT filter (line 18): predicate near final SELECT",
            "Plan: estimate-mismatch operator: node 02 HASH JOIN (inner join, partitioned)",
            "redacted-source-map",
            "source text hidden",
            "Why this deserves attention",
            "Deterministic analysis found",
            "join row expansion or cardinality mismatch with join evidence",
            "Start where the late SQL filter meets the flagged plan operator",
            "Evidence behind this recommendation",
            "resource footprint",
            "read 42.00 GiB; peak memory 18.00 GiB",
            "signals",
            "Technical guardrails",
            "Rewrite support: Guidance only",
            "Candidate details",
            "Candidate strength: High. Score: 88/100. Rank: #1. Impact: High. Confidence: Medium.",
        ],
    )
    assert review_anchor_summary(view) == (
        "SQL: final SELECT filter (line 18): predicate near final SELECT; "
        "Plan: estimate-mismatch operator: node 02 HASH JOIN (inner join, partitioned)"
    )
    assert "Review first:" not in html
    assert 'class="reason-card action-candidate-card"' in html
    assert "owner-coordinate:id" not in html
    assert_no_forbidden_fragments(html)


def test_recent_scan_action_candidate_renderer_includes_outcome_controls(tmp_path, monkeypatch):
    outcome_path = tmp_path / "action_outcomes.jsonl"
    monkeypatch.setenv("QUERY_DOCTOR_ACTION_OUTCOMES_PATH", str(outcome_path))
    for index, outcome in enumerate(
        ("improved", "improved", "improved", "no_change", "unsure"), start=1
    ):
        append_action_outcome(
            ActionOutcomeRecord(
                schema_version=SCHEMA_VERSION,
                recorded_at_iso="2026-05-18T00:00:00+00:00",
                workload_fingerprint=f"wf_{index:024x}"[-27:],
                case_fingerprint=f"cf_{index:024x}"[-27:],
                case_id_local="case-001",
                recommendation_id="query_optimization_review.v1",
                applied="yes",
                outcome=outcome,
                verification_status="comparable_rerun",
            ),
            path=outcome_path,
        )
    view = present_recent_scan_case_detail(
        "case-001",
        {
            "case_index": 1,
            "query_id": "detail:id",
            "workload_fingerprint": "wf_1234567890abcdef12345678",
            "score": 7,
            "score_severity": "suspicious",
            "query_optimization_candidate": {
                "score": 80,
                "tier": "high",
                "confidence": "medium",
                "impact": "high",
                "reasons": ["join row expansion"],
                "suggested_review_areas": ["join keys and join cardinality"],
            },
        },
    )

    html = render_action_candidate_findings(view)

    assert "/batch/case/case-001/outcome/query_optimization_review.v1" in html
    assert "<summary>Record rerun outcome</summary>" in html
    assert "Comparable rerun result" in html
    assert "Local feedback so far: improved in 3 of 5 comparable reruns (60%)" in html
    assert "detail:id" not in html
    assert str(outcome_path) not in html
    assert_no_forbidden_fragments(html)
