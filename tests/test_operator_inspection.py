import json

import pytest

from query_doctor.recent.operator_inspection import exchange_inspection_summary


def exchange(identity="01", **values):
    return {
        "operator_id": identity,
        "operator_name": "EXCHANGE",
        "time_ms": 12,
        "actual_rows": 24,
        "row_conclusion_state": "supported",
        **values,
    }


def test_projection_is_bounded_deterministic_and_does_not_retain_source_text():
    analysis = {
        "operators": [
            exchange(
                str(i),
                time_ms=i,
                label="SELECT secret FROM private",
                evidence_lines=["private-host"],
                bytes_sent=999,
            )
            for i in range(5)
        ],
        "totals": {"TotalBytesSent": {"bytes": 1000}},
    }
    result = exchange_inspection_summary(analysis)
    assert result == exchange_inspection_summary(analysis)
    assert len(result["operators"]) == 3 and result["operators_truncated"]
    assert [op["time_ms"] for op in result["operators"]] == [4, 3, 2]
    assert result["query_bytes_sent"] == 1000
    text = json.dumps(result)
    assert all(
        word not in text for word in ("SELECT", "private", 'bytes_sent": 999', "operator_id")
    )
    assert all(len(op["operator_ref"]) == 73 for op in result["operators"])


@pytest.mark.parametrize("analysis", [None, {}, {"operators": "not a list"}])
def test_missing_operator_data_is_unavailable(analysis):
    result = exchange_inspection_summary(analysis)
    assert result["status"] == "unavailable" and result["operators"] == []


def test_ambiguous_and_invalid_identifiers_are_not_retained():
    result = exchange_inspection_summary(
        {"operators": [exchange("01"), exchange("1"), exchange("raw/source"), exchange("02")]}
    )
    assert result["identity_incomplete"] and len(result["operators"]) == 1
    assert "raw/source" not in json.dumps(result)


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), 2**64, "12"])
def test_invalid_counters_are_missing_not_zero(value):
    result = exchange_inspection_summary(
        {"operators": [exchange(time_ms=value, actual_rows=value)]}
    )
    op = result["operators"][0]
    assert op["time_ms"] is None and op["observed_rows"] is None


def test_row_guardrail_and_explicit_zero_are_preserved():
    result = exchange_inspection_summary(
        {
            "operators": [
                exchange(actual_rows=0),
                exchange(
                    "02", actual_rows=100, row_conclusion_state="limited_by_exec_node_completeness"
                ),
            ]
        }
    )
    assert {op["observed_rows"] for op in result["operators"]} == {0, None}
    assert {op["row_state"] for op in result["operators"]} == {"available", "unavailable"}


def test_regular_case_summary_retains_slice_in_cache_after_source_cleanup(tmp_path):
    from query_doctor.recent.batch_models import CaseResult
    from query_doctor.recent.batch_summary import case_to_summary
    from query_doctor.recent.profile_worker_processor import analysis_cache_payload

    case = CaseResult(
        1, "synthetic", None, None, None, "QUERY", "SELECT", tmp_path, actual_case_dir=tmp_path
    )
    source = tmp_path / "analysis.json"
    source.write_text(json.dumps({"operators": [exchange()]}))
    payload = analysis_cache_payload(case_to_summary(case))
    source.unlink()
    assert payload["operator_inspection"]["status"] == "available"
    assert len(payload["operator_inspection"]["operators"]) == 1


def test_legacy_cache_projection_is_unavailable_without_mutating_stored_payload():
    from query_doctor.web.recent_history_inbox import _project_analysis_cache_payload

    legacy = {"analysis_status": "ok"}
    result = _project_analysis_cache_payload(legacy)
    assert result["operator_inspection"]["status"] == "unavailable"
    assert legacy == {"analysis_status": "ok"}


def test_cache_sanitizer_keeps_missing_state_and_observed_zero():
    from query_doctor.recent.profile_budget import safe_analysis_cache_payload

    summary = exchange_inspection_summary(
        {
            "operators": [
                exchange(actual_rows=0),
                exchange("02", row_conclusion_state="limited_by_exec_node_completeness"),
            ]
        }
    )
    result = safe_analysis_cache_payload({"operator_inspection": summary})["operator_inspection"]
    assert {op["row_state"] for op in result["operators"]} == {"available", "unavailable"}
    assert any(op.get("observed_rows") == 0 for op in result["operators"])
    assert any(
        op["row_state"] == "unavailable" and op.get("observed_rows") is None
        for op in result["operators"]
    )
