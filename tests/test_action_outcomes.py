import json

import pytest

from query_doctor.web.action_outcomes import (
    SCHEMA_VERSION,
    ActionOutcomeRecord,
    action_outcome_record_from_case,
    append_action_outcome,
    load_action_outcomes,
    summarize_action_outcomes,
    summarize_workload_action_outcomes,
    workload_outcome_summary_text,
)
from query_doctor.web.models import WebError
from query_doctor.web.ui.outcomes import render_action_outcomes_page


def outcome_record(
    *,
    recommendation_id: str = "stats_refresh_review.v1",
    applied: str = "yes",
    outcome: str = "improved",
    workload_fingerprint: str = "wf_1234567890abcdef12345678",
    verification_status=None,
) -> ActionOutcomeRecord:
    verification_status = verification_status or (
        "comparable_rerun" if applied == "yes" else "not_applicable"
    )
    return ActionOutcomeRecord(
        schema_version=SCHEMA_VERSION,
        recorded_at_iso="2026-05-18T00:00:00+00:00",
        workload_fingerprint=workload_fingerprint,
        case_fingerprint="cf_1234567890abcdef12345678",
        case_id_local="case-001",
        recommendation_id=recommendation_id,
        applied=applied,
        outcome=outcome,
        verification_status=verification_status,
        note_redacted="",
    )


def test_action_outcome_record_is_raw_free_and_loadable(tmp_path):
    record = action_outcome_record_from_case(
        case_id="case-1176",
        case={
            "query_id": "abc:def",
            "workload_fingerprint": "wf_1234567890abcdef12345678",
        },
        recommendation_id="stats_refresh_review.v1",
        form={
            "applied": ["yes"],
            "outcome": ["improved"],
            "verification_status": ["comparable_rerun"],
            "note": ["/Users/example/case"],
        },
    )
    path = append_action_outcome(record, path=tmp_path / "action_outcomes.jsonl")

    text = path.read_text(encoding="utf-8")
    assert "abc:def" not in text
    assert "/Users/example" not in text
    assert "case-1176" in text

    loaded = load_action_outcomes(path=path)
    assert len(loaded) == 1
    assert loaded[0].case_id_local == "case-1176"
    assert loaded[0].recommendation_id == "stats_refresh_review.v1"
    assert loaded[0].applied == "yes"
    assert loaded[0].outcome == "improved"
    assert loaded[0].verification_status == "comparable_rerun"
    assert loaded[0].note_redacted == "<local path hidden>"


def test_latest_case_action_outcomes_matches_fingerprints_not_history_row_numbers(tmp_path):
    from dataclasses import replace

    from query_doctor.web.action_outcomes import case_fingerprint, latest_case_action_outcomes

    workload = "wf_1234567890abcdef12345678"
    record = outcome_record()
    same_case = replace(
        record,
        workload_fingerprint=workload,
        case_fingerprint=case_fingerprint(workload, "synthetic-case"),
        case_id_local="case-002",
    )
    latest = replace(same_case, case_id_local="case-007", outcome="no_change")
    other_case = replace(
        same_case, case_fingerprint=case_fingerprint(workload, "other-case"), outcome="worsened"
    )
    other_workload = replace(same_case, workload_fingerprint=f"wf_{'a' * 24}")
    path = tmp_path / "action_outcomes.jsonl"
    for item in [same_case, latest, other_case, other_workload]:
        append_action_outcome(item, path=path)

    found = latest_case_action_outcomes(workload, "synthetic-case", path=path)

    assert found == {same_case.recommendation_id: latest}
    assert latest_case_action_outcomes(workload, "missing-case", path=path) == {}
    assert latest_case_action_outcomes("", "synthetic-case", path=path) == {}
    assert latest_case_action_outcomes(workload, "", path=path) == {}


def test_action_outcomes_skip_malformed_and_unknown_records(tmp_path):
    path = tmp_path / "action_outcomes.jsonl"
    valid = {
        "schema_version": 1,
        "recorded_at_iso": "2026-05-18T00:00:00+00:00",
        "workload_fingerprint": "wf_1234567890abcdef12345678",
        "case_fingerprint": "cf_1234567890abcdef12345678",
        "case_id_local": "case-001",
        "recommendation_id": "query_optimization_review.v1",
        "applied": "skip",
        "outcome": "not_applicable",
        "note_redacted": "",
    }
    invalid = dict(valid, recommendation_id="raw_unknown.v1")
    path.write_text(
        "{not json}\n" + json.dumps(invalid) + "\n" + json.dumps(valid) + "\n",
        encoding="utf-8",
    )

    loaded = load_action_outcomes(path=path)

    assert len(loaded) == 1
    assert loaded[0].recommendation_id == "query_optimization_review.v1"
    assert loaded[0].verification_status == "not_applicable"


def test_legacy_action_outcome_records_load_as_unverified_feedback(tmp_path):
    path = tmp_path / "action_outcomes.jsonl"
    legacy = {
        "schema_version": 1,
        "recorded_at_iso": "2026-05-18T00:00:00+00:00",
        "workload_fingerprint": "wf_1234567890abcdef12345678",
        "case_fingerprint": "cf_1234567890abcdef12345678",
        "case_id_local": "case-001",
        "recommendation_id": "query_optimization_review.v1",
        "applied": "yes",
        "outcome": "improved",
        "note_redacted": "",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    loaded = load_action_outcomes(path=path)
    metrics = summarize_action_outcomes(loaded, min_applied=1)

    assert len(loaded) == 1
    assert loaded[0].schema_version == 1
    assert loaded[0].verification_status == "legacy_unverified"
    assert metrics[0].applied_count == 1
    assert metrics[0].comparable_rerun_count == 0
    assert metrics[0].unverified_applied_count == 1
    assert metrics[0].min_sample_met is False


def test_action_outcome_rejects_unknown_recommendation_id():
    with pytest.raises(WebError):
        action_outcome_record_from_case(
            case_id="case-001",
            case={
                "query_id": "abc:def",
                "workload_fingerprint": "wf_1234567890abcdef12345678",
            },
            recommendation_id="freeform.v1",
            form={
                "applied": ["yes"],
                "outcome": ["improved"],
                "verification_status": ["comparable_rerun"],
            },
        )


def test_action_outcome_requires_comparable_rerun_verification_for_applied_result():
    with pytest.raises(WebError):
        action_outcome_record_from_case(
            case_id="case-001",
            case={
                "query_id": "abc:def",
                "workload_fingerprint": "wf_1234567890abcdef12345678",
            },
            recommendation_id="stats_refresh_review.v1",
            form={"applied": ["yes"], "outcome": ["improved"]},
        )


def test_action_outcome_metrics_apply_min_sample_threshold():
    metrics = summarize_action_outcomes(
        [
            outcome_record(outcome="improved"),
            outcome_record(outcome="improved"),
            outcome_record(outcome="no_change"),
            outcome_record(outcome="worsened"),
            outcome_record(applied="skip", outcome="not_applicable"),
            outcome_record(applied="no", outcome="not_applicable"),
            outcome_record(
                recommendation_id="runtime_admission_check.v1",
                outcome="improved",
            ),
        ],
        min_applied=4,
    )

    assert [metric.recommendation_id for metric in metrics] == [
        "stats_refresh_review.v1",
        "runtime_admission_check.v1",
    ]
    stats_metric = metrics[0]
    assert stats_metric.total_records == 6
    assert stats_metric.applied_count == 4
    assert stats_metric.comparable_rerun_count == 4
    assert stats_metric.unverified_applied_count == 0
    assert stats_metric.not_applied_count == 1
    assert stats_metric.skipped_count == 1
    assert stats_metric.improved_count == 2
    assert stats_metric.no_change_count == 1
    assert stats_metric.worsened_count == 1
    assert stats_metric.improvement_rate == 0.5
    assert stats_metric.min_sample_met is True
    assert metrics[1].min_sample_met is False


def test_workload_action_outcome_metrics_group_safe_workload_rollups():
    metrics = summarize_workload_action_outcomes(
        [
            outcome_record(workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa"),
            outcome_record(
                workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                outcome="no_change",
            ),
            outcome_record(
                workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                recommendation_id="runtime_admission_check.v1",
                applied="skip",
                outcome="not_applicable",
            ),
            outcome_record(workload_fingerprint="wf_bbbbbbbbbbbbbbbbbbbbbbbb"),
            outcome_record(workload_fingerprint="/tmp/raw-path"),
        ]
    )

    assert sorted(metrics) == [
        "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
        "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    metric = metrics["wf_aaaaaaaaaaaaaaaaaaaaaaaa"]
    assert metric.total_records == 3
    assert metric.applied_count == 2
    assert metric.comparable_rerun_count == 2
    assert metric.unverified_applied_count == 0
    assert metric.skipped_count == 1
    assert metric.improved_count == 1
    assert metric.no_change_count == 1
    assert metric.last_recommendation_id == "runtime_admission_check.v1"
    assert metric.last_applied == "skip"
    assert metric.last_outcome == "not_applicable"
    assert metric.last_applied_recommendation_id == "stats_refresh_review.v1"
    assert metric.last_applied_outcome == "no_change"
    assert metric.family_signal.recommendation_id == "stats_refresh_review.v1"
    assert metric.family_signal.applied_count == 2
    assert metric.family_signal.comparable_rerun_count == 2
    assert metric.family_signal.min_sample_met is False
    assert metric.family_signal.min_applied == 5
    assert [signal.recommendation_id for signal in metric.family_signals] == [
        "runtime_admission_check.v1",
        "stats_refresh_review.v1",
    ]
    assert workload_outcome_summary_text(metric) == (
        "3 recorded; 2 applied; 2 comparable reruns; improved 1, no change 1; "
        "last applied action Stats refresh review: no change; "
        "family signal Stats refresh review: improved 1/2 comparable reruns, no change 1; "
        "feedback sample below threshold (2/5 comparable reruns); "
        "next check stats signal count and workload p95"
    )
    calibrated_metric = summarize_workload_action_outcomes(
        [
            outcome_record(workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa"),
            outcome_record(
                workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                outcome="no_change",
            ),
        ],
        min_applied=2,
    )["wf_aaaaaaaaaaaaaaaaaaaaaaaa"]
    assert calibrated_metric.family_signal.min_sample_met is True
    assert "feedback sample threshold met (2/2 comparable reruns)" in workload_outcome_summary_text(
        calibrated_metric
    )
    assert (
        "family signal Admission/runtime check: no verified rerun records yet; "
        "feedback sample below threshold (0/2 comparable reruns); "
        "next check admission/runtime signal count and workload p95"
    ) in workload_outcome_summary_text(
        calibrated_metric,
        recommendation_id="runtime_admission_check.v1",
    )


def test_action_outcome_metrics_page_renders_safe_aggregate_only(tmp_path, monkeypatch):
    outcome_path = tmp_path / "action_outcomes.jsonl"
    monkeypatch.setenv("QUERY_DOCTOR_ACTION_OUTCOMES_PATH", str(outcome_path))
    for record in [
        outcome_record(workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa"),
        outcome_record(workload_fingerprint="wf_bbbbbbbbbbbbbbbbbbbbbbbb"),
        outcome_record(workload_fingerprint="wf_cccccccccccccccccccccccc"),
        outcome_record(outcome="no_change", workload_fingerprint="wf_dddddddddddddddddddddddd"),
        outcome_record(outcome="unsure", workload_fingerprint="wf_eeeeeeeeeeeeeeeeeeeeeeee"),
    ]:
        append_action_outcome(record, path=outcome_path)

    html = render_action_outcomes_page()

    assert "5 recorded" in html
    assert "improved in 3 of 5 comparable reruns (60%)" in html
    assert "Comparable reruns" in html
    assert "Stats refresh review" in html
    assert "case-001" not in html
    assert "cf_1234567890abcdef12345678" not in html
    assert str(outcome_path) not in html


def test_action_outcomes_page_uses_compact_empty_state(tmp_path, monkeypatch):
    outcome_path = tmp_path / "action_outcomes.jsonl"
    monkeypatch.setenv("QUERY_DOCTOR_ACTION_OUTCOMES_PATH", str(outcome_path))

    html = render_action_outcomes_page()

    assert "0 recorded" in html
    assert "No feedback recorded yet" in html
    assert 'class="outcomes-empty-state"' in html
    assert 'href="/">Open Query Inbox</a>' in html
    assert "No recommendation metrics yet." not in html
    assert "No action outcomes recorded yet." not in html
