"""Recent batch metadata refresh selection policy."""

from __future__ import annotations

from query_doctor.recent.batch_config import (
    BAD_METADATA_REFRESH_LIMIT,
    SUSPICIOUS_METADATA_PROMOTION_SCORE_FLOOR,
    SUSPICIOUS_METADATA_REFRESH_LIMIT,
)
from query_doctor.recent.batch_models import BatchConfig, CaseResult
from query_doctor.recent.command_args import metadata_source_configured
from query_doctor.recent.batch_summary import batch_ranking_key, case_score_severity
from query_doctor.recent.query_optimization_score import (
    IMPACT_ORDER,
    TIER_ORDER,
    optimizer_rewriteability_rank,
)


def rank_cases_for_metadata(cases: list[CaseResult]) -> list[CaseResult]:
    ranked = sorted(
        [case for case in cases if case.analysis_status == "ok"],
        key=batch_ranking_key,
    )
    for rank, case in enumerate(ranked, start=1):
        case.triage_rank = rank
    return ranked


def metadata_refresh_candidates(config: BatchConfig, cases: list[CaseResult]) -> list[CaseResult]:
    ranked = rank_cases_for_metadata(cases)
    if metadata_refresh_skip_reason(config, ranked) is not None:
        mark_metadata_not_requested(ranked)
        return []
    candidates = select_metadata_refresh_candidates_for_config(config, ranked)
    refreshed_ids = {id(case) for case in candidates}
    mark_metadata_not_requested([case for case in ranked if id(case) not in refreshed_ids])
    return candidates


def metadata_refresh_skip_reason(config: BatchConfig, ranked_cases: list[CaseResult]) -> str | None:
    if config.metadata_mode == "off":
        return "metadata disabled"
    if not metadata_source_configured(config):
        return "metadata not configured"
    if config.metadata_top_limit <= 0:
        return "metadata_top_limit=0"
    if not ranked_cases:
        return "no eligible cases"
    return None


def mark_metadata_not_requested(cases: list[CaseResult]) -> None:
    for case in cases:
        if case.metadata_status in {"skipped", "not_observed"}:
            case.metadata_status = "not_requested"


def select_metadata_refresh_candidates_for_config(
    config: BatchConfig, ranked_cases: list[CaseResult]
) -> list[CaseResult]:
    return select_metadata_refresh_candidates(
        ranked_cases,
        config.metadata_top_limit,
        include_remaining=True,
    )


def select_metadata_refresh_candidates(
    ranked_cases: list[CaseResult],
    limit: int,
    *,
    include_remaining: bool = False,
) -> list[CaseResult]:
    if limit <= 0:
        return []
    remaining = limit
    selected: list[CaseResult] = []
    selected_ids: set[int] = set()

    bad_limit = min(BAD_METADATA_REFRESH_LIMIT, remaining)
    bad = [case for case in ranked_cases if case_score_severity(case) == "high"][:bad_limit]
    selected.extend(bad)
    selected_ids.update(id(case) for case in bad)
    remaining -= len(bad)

    query_optimization = [
        case
        for case in sorted(ranked_cases, key=query_optimization_metadata_key)
        if id(case) not in selected_ids and metadata_query_optimization_candidate(case)
    ][:remaining]
    selected.extend(query_optimization)
    selected_ids.update(id(case) for case in query_optimization)
    remaining -= len(query_optimization)

    suspicious_limit = min(SUSPICIOUS_METADATA_REFRESH_LIMIT, remaining)
    suspicious = [
        case
        for case in ranked_cases
        if id(case) not in selected_ids
        and case_score_severity(case) == "suspicious"
        and suspicious_can_be_promoted_by_metadata(case)
    ][:suspicious_limit]
    selected.extend(suspicious)
    selected_ids.update(id(case) for case in suspicious)
    remaining -= len(suspicious)

    if include_remaining and remaining > 0:
        selected.extend([case for case in ranked_cases if id(case) not in selected_ids][:remaining])

    return selected


def metadata_query_optimization_candidate(case: CaseResult) -> bool:
    candidate = case.query_optimization_candidate
    return candidate is not None and candidate.tier in {"high", "medium"}


def query_optimization_metadata_key(case: CaseResult) -> tuple[object, ...]:
    candidate = case.query_optimization_candidate
    if candidate is None:
        return (0, 0, 0, 0, 0.0, 999999, case.query_id)
    triage_rank = case.triage_rank if case.triage_rank is not None else 999999
    return (
        -TIER_ORDER.get(candidate.tier, 0),
        -optimizer_rewriteability_rank(
            case.optimizer_rewrite_support.to_dict()
            if case.optimizer_rewrite_support is not None
            else None
        ),
        -candidate.score,
        -IMPACT_ORDER.get(candidate.impact, 0),
        -(case.duration_sec or 0.0),
        triage_rank,
        case.query_id,
    )


def suspicious_can_be_promoted_by_metadata(case: CaseResult) -> bool:
    return case.score >= SUSPICIOUS_METADATA_PROMOTION_SCORE_FLOOR
