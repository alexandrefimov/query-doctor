from __future__ import annotations

import pytest

from query_doctor.web import case_detail_context, routes
from query_doctor.web.action_outcomes import case_fingerprint
from query_doctor.web.models import WebSettings
from query_doctor.web.jobs import WebJobStore
from query_doctor.web.recent_history_inbox import recent_history_summary_from_payloads
from query_doctor.web.ui.recent_scan_results import batch_case_id


def history_payload(query_id: str, *, source_key: str = "source-a"):
    return {
        "engine": "impala",
        "source_kind": "impala",
        "source_key": source_key,
        "query_id": query_id,
        "profile_status": "analyzed",
        "analysis_cache_payload": {
            "analysis_status": "ok",
            "collection_status": "ok",
            "score": 80,
            "score_severity": "high",
            "workload_fingerprint": "wf_1234567890abcdef12345678",
        },
    }


def summary(payloads, history_view="details_ready"):
    return recent_history_summary_from_payloads(
        payloads, backend="postgres", history_view=history_view
    )


@pytest.mark.parametrize("history_view", ["details_ready", "all_recent"])
def test_history_selection_survives_new_results(tmp_path, monkeypatch, history_view):
    target = history_payload("selected-query")
    before = summary([target], history_view)
    selected_id = batch_case_id(before["cases"][0])
    after = summary([history_payload("new-query"), target], history_view)
    monkeypatch.setattr(
        case_detail_context,
        "recent_history_inbox_summary_from_settings",
        lambda *_a, **_k: after,
    )

    _, selected = case_detail_context.resolve_online_history_case_detail_settings(
        WebSettings(config=tmp_path / "config.json"), selected_id
    )

    assert selected is not None
    assert selected["query_id"] == "selected-query"


@pytest.mark.parametrize("history_view", ["details_ready", "all_recent"])
def test_outcome_submit_stays_bound_to_selected_query(tmp_path, monkeypatch, history_view):
    target = history_payload("selected-query")
    selected_id = batch_case_id(summary([target], history_view)["cases"][0])
    after = summary([history_payload("new-query"), target], history_view)
    monkeypatch.setattr(
        case_detail_context,
        "recent_history_inbox_summary_from_settings",
        lambda *_a, **_k: after,
    )
    saved = []
    monkeypatch.setattr(routes, "append_action_outcome", saved.append)

    response = routes.route_action_outcome_post(
        f"/batch/case/{selected_id}/outcome/query_optimization_review.v1",
        {"applied": ["yes"], "outcome": ["improved"], "verification_status": ["comparable_rerun"]},
        WebSettings(config=tmp_path / "config.json"),
        WebJobStore(),
    )

    assert response.status == 303
    assert len(saved) == 1
    assert saved[0].case_fingerprint == case_fingerprint(
        target["analysis_cache_payload"]["workload_fingerprint"], "selected-query"
    )


def test_removed_selection_does_not_fall_back_to_another_row(tmp_path, monkeypatch):
    selected_id = batch_case_id(summary([history_payload("selected-query")])["cases"][0])
    after = summary([history_payload("replacement-query")])
    monkeypatch.setattr(
        case_detail_context,
        "recent_history_inbox_summary_from_settings",
        lambda *_a, **_k: after,
    )

    _, selected = case_detail_context.resolve_online_history_case_detail_settings(
        WebSettings(config=tmp_path / "config.json"), selected_id
    )

    assert selected is None


def test_history_case_references_are_source_bound_and_raw_free():
    target = history_payload("selected-query")
    first = batch_case_id(summary([target])["cases"][0])
    second = batch_case_id(summary([dict(target, source_key="source-b")])["cases"][0])

    assert first != second
    assert "selected-query" not in first
    assert "source-a" not in first
    assert first == batch_case_id(summary([target], "all_recent")["cases"][0])


def test_history_reference_does_not_use_truncated_source_identity():
    shared_prefix = "s" * 256
    first = batch_case_id(
        summary([history_payload("selected-query", source_key=shared_prefix + "a")])["cases"][0]
    )
    second = batch_case_id(
        summary([history_payload("selected-query", source_key=shared_prefix + "b")])["cases"][0]
    )

    assert first != second


def test_history_selection_is_not_shadowed_by_a_batch_row(tmp_path, monkeypatch):
    retained = summary([history_payload("selected-query")])
    selected_id = batch_case_id(retained["cases"][0])
    monkeypatch.setattr(
        case_detail_context,
        "recent_history_inbox_summary_from_settings",
        lambda *_a, **_k: retained,
    )
    settings = WebSettings(
        config=tmp_path / "config.json",
        corpus_summary={"cases": [{"case_index": 1, "query_id": "batch-query"}]},
    )

    _, selected = case_detail_context.resolve_case_detail_settings(
        settings, WebJobStore(), selected_id
    )

    assert selected["query_id"] == "selected-query"


def test_old_positional_history_link_is_rejected():
    retained = summary([history_payload("replacement-query")])

    assert case_detail_context.find_batch_case(retained, "case-001") is None
    assert case_detail_context.find_batch_case(retained, "recent-case-001") is None


def test_expired_outcome_selection_does_not_write(tmp_path, monkeypatch):
    selected_id = batch_case_id(summary([history_payload("selected-query")])["cases"][0])
    retained = summary([history_payload("replacement-query")])
    monkeypatch.setattr(
        case_detail_context,
        "recent_history_inbox_summary_from_settings",
        lambda *_a, **_k: retained,
    )
    saved = []
    monkeypatch.setattr(routes, "append_action_outcome", saved.append)

    response = routes.route_action_outcome_post(
        f"/batch/case/{selected_id}/outcome/query_optimization_review.v1",
        {"applied": ["no"]},
        WebSettings(config=tmp_path / "config.json"),
        WebJobStore(),
    )

    assert response.status == 404
    assert saved == []


def test_analyzed_row_without_query_identity_is_not_openable():
    retained = summary([history_payload("")])

    assert batch_case_id(retained["cases"][0]) is None
    assert retained["cases"][0]["analysis_status"] == "details_unavailable"
