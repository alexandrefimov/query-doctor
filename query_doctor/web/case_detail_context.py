"""Finished/Running case detail resolution helpers for the web server."""

from __future__ import annotations

import json
import re
from dataclasses import replace

from query_doctor.web.jobs import WebJobStore
from query_doctor.web.models import WebSettings
from query_doctor.web.presenters.recent_scan import case_score_severity, present_recent_scan_summary
from query_doctor.web.recent_history_inbox import (
    HISTORY_VIEW_ALL_RECENT,
    recent_history_inbox_summary_from_settings,
)
from query_doctor.web.trusted_artifacts import decorate_cases_with_optimizer_artifact_status
from query_doctor.web.ui.recent_scan_results import (
    filter_rows_by_query_group,
    sort_rows_for_query_group,
)

OPTIMIZER_CASE_NOT_ACTIONABLE_REASON = (
    "Optimizer is available only for suspicious or bad selected cases."
)
FAILED_CASE_OPTIMIZER_UNAVAILABLE_REASON = (
    "Optimizer requires successful deterministic processing for this case. Re-run analysis first."
)
OPTIMIZER_DRAFT_NOT_ELIGIBLE_REASON = (
    "Query Doctor already classified this query shape as not eligible for an optimizer job. "
    "Use the query-shape review areas instead."
)
OPTIMIZER_DRAFT_DISABLED_BY_GUARDRAILS_REASON = (
    "Trusted SQL draft generation is disabled for this query shape by safety and validation "
    "guardrails. Use the query-shape review areas instead."
)
OPTIMIZER_DRAFT_UNAVAILABLE_REASON = (
    "A supported optimizer recipe was detected, but deterministic checks could not build a "
    "trusted SQL draft for this concrete query shape."
)
OPTIMIZER_NO_RECIPE_REASON = (
    "No supported deterministic optimizer recipe is available for this query shape. Use the "
    "query-shape review areas instead."
)
OPTIMIZER_SOURCE_CLASSIFICATION_UNAVAILABLE_REASON = (
    "Source SQL is unavailable or outside the optimizer read-only scope for this case."
)
OPTIMIZER_REWRITE_SUPPORT_ELIGIBLE_DRAFT = "safe_to_attempt"
OPTIMIZER_REWRITE_SUPPORT_ELIGIBLE_STATUSES = {
    "sql_draft_supported",
    "sql_draft_attemptable",
    "recipe_detected",
}
ACTION_TERMINAL_OR_VISIBLE_STATUSES = {
    "running",
    "generated",
    "partial_untrusted",
    "failed",
    "cancelled",
}


def batch_page_settings(settings: WebSettings, job_store: WebJobStore) -> WebSettings:
    if settings.batch_summary is not None:
        return settings
    latest = job_store.latest_batch_summary()
    if latest is None:
        return settings
    return replace(settings, batch_summary=latest, corpus_summary=None, corpus_summary_root=None)


def running_page_settings(settings: WebSettings, job_store: WebJobStore) -> WebSettings:
    latest = job_store.latest_running_summary()
    if latest is None:
        return replace(settings, batch_summary=None, corpus_summary=None, corpus_summary_root=None)
    return replace(settings, batch_summary=latest, corpus_summary=None, corpus_summary_root=None)


def running_detail_kwargs() -> dict[str, str]:
    return {
        "workflow_title": "Running Queries",
        "list_href": "/running#recent-results",
        "detail_base_path": "/running/case",
        "active_nav": "running",
    }


def resolve_running_case_detail_settings(
    settings: WebSettings,
    job_store: WebJobStore,
    case_id: str,
) -> tuple[WebSettings, dict[str, object] | None]:
    running_settings = running_page_settings(settings, job_store)
    running_summary = load_batch_summary(running_settings)
    running_case = (
        find_batch_case(running_summary, case_id) if running_summary is not None else None
    )
    if running_case is not None:
        running_case = case_with_detail_ranks(running_summary, case_id, running_case)
    return running_settings, running_case


def resolve_case_detail_settings(
    settings: WebSettings,
    job_store: WebJobStore,
    case_id: str,
) -> tuple[WebSettings, dict[str, object] | None]:
    batch_settings = batch_page_settings(settings, job_store)
    summary = load_batch_summary(batch_settings)
    case = find_batch_case(summary, case_id) if summary is not None else None
    if case is not None:
        return batch_settings, case_with_detail_ranks(summary, case_id, case)
    running_settings = running_page_settings(settings, job_store)
    if running_settings.batch_summary != batch_settings.batch_summary:
        running_summary = load_batch_summary(running_settings)
        running_case = (
            find_batch_case(running_summary, case_id) if running_summary is not None else None
        )
        if running_case is not None:
            return running_settings, case_with_detail_ranks(running_summary, case_id, running_case)
    history_settings, history_case = resolve_online_history_case_detail_settings(
        settings,
        case_id,
    )
    if history_case is not None:
        return history_settings, history_case
    return batch_settings, None


def resolve_online_history_case_detail_settings(
    settings: WebSettings,
    case_id: str,
) -> tuple[WebSettings, dict[str, object] | None]:
    history_view = HISTORY_VIEW_ALL_RECENT if case_id.startswith("recent-case-") else ""
    summary = recent_history_inbox_summary_from_settings(
        settings,
        history_view=history_view,
    )
    case = find_batch_case(summary, case_id) if summary is not None else None
    if case is None:
        return settings, None
    history_settings = replace(
        settings,
        batch_summary=None,
        corpus_summary=summary,
        corpus_summary_root=None,
    )
    return history_settings, case_with_detail_ranks(summary, case_id, case)


def load_batch_summary(settings: WebSettings) -> dict[str, object] | None:
    if settings.corpus_summary is not None:
        return decorate_cases_with_optimizer_artifact_status(settings.corpus_summary)
    summary_path = settings.batch_summary
    if summary_path is None:
        return None
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        decorate_cases_with_optimizer_artifact_status(payload)
        if isinstance(payload, dict)
        else None
    )


def case_with_detail_ranks(
    summary: dict[str, object] | None,
    case_id: str,
    case: dict[str, object],
) -> dict[str, object]:
    detail_fields = batch_case_detail_source_fields(summary)
    detail_fields.update(batch_case_detail_rank_fields(summary, case_id))
    if not detail_fields:
        return case
    decorated = dict(case)
    decorated.update(detail_fields)
    return decorated


def batch_case_detail_source_fields(summary: dict[str, object] | None) -> dict[str, str]:
    if not isinstance(summary, dict):
        return {}
    source = str(summary.get("query_profile_source") or "").strip().lower()
    if source not in {"cm", "impala"}:
        return {}
    return {"_detail_query_profile_source": source}


def batch_case_detail_rank_fields(
    summary: dict[str, object] | None, case_id: str
) -> dict[str, int]:
    if not isinstance(summary, dict):
        return {}
    view = present_recent_scan_summary(summary)
    result: dict[str, int] = {}
    for row in view.rows:
        if row.case_id == case_id:
            result["_detail_overall_rank"] = row.rank
            break
    for group, key in (
        ("optimization", "_detail_optimization_rank"),
        ("stats", "_detail_stats_rank"),
    ):
        rows = sort_rows_for_query_group(filter_rows_by_query_group(view.rows, group), group)
        for display_rank, row in enumerate(rows, start=1):
            if row.case_id == case_id:
                result[key] = display_rank
                break
    return result


def find_batch_case(summary: dict[str, object], case_id: str) -> dict[str, object] | None:
    if not re.fullmatch(r"(?:recent-)?case-[0-9]{3,}", case_id):
        return None
    cases = summary.get("cases")
    if not isinstance(cases, list):
        return None
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_ref = str(case.get("case_ref") or "").strip().lower()
        if case_ref:
            if case_ref == case_id:
                return case
            continue
        try:
            index = int(case.get("case_index"))
        except (TypeError, ValueError):
            continue
        if f"case-{index:03d}" == case_id:
            return case
    return None


def case_allows_llm_report(case: dict[str, object]) -> bool:
    return case_score_severity(case) in {"high", "suspicious"}


def case_allows_query_optimizer(case: dict[str, object]) -> bool:
    return case_score_severity(case) in {"high", "suspicious"} and case_has_optimizer_job_support(
        case
    )


def case_score_allows_query_optimizer(case: dict[str, object]) -> bool:
    return case_score_severity(case) in {"high", "suspicious"}


def case_has_optimizer_job_support(case: dict[str, object]) -> bool:
    support = optimizer_rewrite_support(case)
    if not support:
        return True
    eligibility = str(support.get("draft_eligibility") or "").strip().lower()
    if eligibility:
        return eligibility == OPTIMIZER_REWRITE_SUPPORT_ELIGIBLE_DRAFT
    status = str(support.get("status") or "").strip().lower()
    return status in OPTIMIZER_REWRITE_SUPPORT_ELIGIBLE_STATUSES


def optimizer_rewrite_support(case: dict[str, object]) -> dict[str, object]:
    support = case.get("optimizer_rewrite_support")
    return support if isinstance(support, dict) else {}


def optimizer_unavailable_reason_for_case(case: dict[str, object]) -> str:
    severity = case_score_severity(case)
    if severity == "failed":
        return FAILED_CASE_OPTIMIZER_UNAVAILABLE_REASON
    if severity not in {"high", "suspicious"}:
        return OPTIMIZER_CASE_NOT_ACTIONABLE_REASON

    support = optimizer_rewrite_support(case)
    if not support:
        return ""
    eligibility = str(support.get("draft_eligibility") or "").strip().lower()
    status = str(support.get("status") or "").strip().lower()
    if eligibility == OPTIMIZER_REWRITE_SUPPORT_ELIGIBLE_DRAFT:
        return ""
    if not eligibility and status in OPTIMIZER_REWRITE_SUPPORT_ELIGIBLE_STATUSES:
        return ""
    if eligibility == "disabled_by_safety_thresholds":
        return OPTIMIZER_DRAFT_DISABLED_BY_GUARDRAILS_REASON
    if eligibility == "deterministic_draft_unavailable":
        return OPTIMIZER_DRAFT_UNAVAILABLE_REASON
    if eligibility == "no_recipe":
        return OPTIMIZER_NO_RECIPE_REASON
    if eligibility in {"source_unavailable", "not_candidate"} or status in {
        "source_unavailable",
        "not_candidate",
    }:
        if eligibility == "source_unavailable" or status == "source_unavailable":
            return OPTIMIZER_SOURCE_CLASSIFICATION_UNAVAILABLE_REASON
        return OPTIMIZER_DRAFT_NOT_ELIGIBLE_REASON
    if status in {"guidance_only", "draft_disabled"}:
        return OPTIMIZER_DRAFT_NOT_ELIGIBLE_REASON
    return OPTIMIZER_DRAFT_NOT_ELIGIBLE_REASON


def optimizer_state_for_case(
    case: dict[str, object],
    optimized_query_state: dict[str, object],
) -> dict[str, object]:
    if case_allows_query_optimizer(case):
        return optimized_query_state
    severity = case_score_severity(case)
    status = str(optimized_query_state.get("status") or "not_run")
    if status in ACTION_TERMINAL_OR_VISIBLE_STATUSES:
        return optimized_query_state
    if severity == "failed":
        unavailable = dict(optimized_query_state)
        unavailable.update(
            {
                "status": "unavailable",
                "running": False,
                "trusted": False,
                "partial": False,
                "source_available": False,
                "unavailable_reason": FAILED_CASE_OPTIMIZER_UNAVAILABLE_REASON,
                "error": "",
            }
        )
        return unavailable
    unavailable_reason = optimizer_unavailable_reason_for_case(case)
    if unavailable_reason and severity in {"high", "suspicious"}:
        unavailable = dict(optimized_query_state)
        unavailable.update(
            {
                "status": "unavailable",
                "running": False,
                "trusted": False,
                "partial": False,
                "unavailable_reason": unavailable_reason,
                "error": "",
            }
        )
        return unavailable
    if status == "unavailable" and severity != "clean":
        return optimized_query_state
    hidden = dict(optimized_query_state)
    hidden.update(
        {
            "status": "hidden",
            "running": False,
            "source_available": False,
            "unavailable_reason": OPTIMIZER_CASE_NOT_ACTIONABLE_REASON,
        }
    )
    return hidden
