"""Recent batch summary serialization helpers."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from query_doctor.cli import collect_cm_profiles as cm_profiles
from query_doctor.recent.optimizer_rewrite_support import NO_RECIPE_REVIEW_TRACKS, RECIPE_LABELS
from query_doctor.recent.batch_config import (
    MAX_CM_INSPECT_LIMIT,
    MAX_RAW_CM_SUMMARY_SCAN_LIMIT,
    duration_filter_label,
)
from query_doctor.recent.batch_models import BatchConfig, CaseResult, DiscoveryResult
from query_doctor.recent.operator_inspection import exchange_inspection_summary
from query_doctor.recent.metadata_collectable import update_collectable_metadata_table_count
from query_doctor.recent.query_optimization_score import optimizer_adjacent_actionability
from query_doctor.recent.query_optimization_score import optimizer_no_draft_actionability
from query_doctor.recent.query_optimization_score import query_optimization_sort_key
from query_doctor.recent.source_locators import build_source_locators
from query_doctor.recent.stats_optimization_score import stats_optimization_sort_key
from query_doctor.recent.workload_fingerprint import WorkloadFingerprint
from query_doctor.recent.workload_fingerprint import compute_workload_fingerprint

PRIMARY_BOTTLENECK_LABELS = {
    "stats",
    "sql_shape",
    "runtime_admission",
    "runtime_skew",
    "runtime_data_movement",
    "runtime_memory",
    "runtime_storage",
    "client_fetch_tail",
    "mixed",
    "unknown",
}
PRIMARY_BOTTLENECK_CONFIDENCES = {"high", "medium", "low"}
UNKNOWN_FINDING_IDS = {
    "analytic_bottleneck",
    "cardinality_estimate_errors",
    "hdfs_or_storage_bottleneck",
    "host_execution_tail_suspected",
    "join_bottleneck",
    "large_intermediate_or_exchange_traffic",
    "client_fetch_tail",
    "memory_estimate_errors",
    "sort_bottleneck",
}
UNKNOWN_OPERATOR_CATEGORIES = {
    "AGGREGATE",
    "ANALYTIC",
    "EXCHANGE",
    "HASH JOIN",
    "HDFS SCAN",
    "NESTED LOOP JOIN",
    "SCAN",
    "SORT",
    "UNION",
}
UNKNOWN_LIMITATION_LABELS = {
    "runtime_metrics_unavailable",
    "table_metadata_unavailable",
    "backend_per_host_metrics_unavailable",
    "backend_metrics_not_comparable",
}
UNKNOWN_METADATA_STATUS_LABELS = {
    "collected",
    "failed",
    "not_observed",
    "partial",
    "skipped",
}
UNKNOWN_OPTIMIZATION_TIER_LABELS = {
    "high",
    "medium",
    "low",
    "not_likely",
    "unknown",
}
UNKNOWN_EVIDENCE_QUALITY_LABELS = {
    "high",
    "medium",
    "low",
    "unknown",
}
UNKNOWN_STATS_PRIMARY_LABELS = {
    "candidate_supported",
    "mixed_candidate",
    "not_applicable",
    "not_supported",
    "not_primary_supported",
    "not_supported_by_metadata",
    "unknown",
}
UNKNOWN_STATS_CONTEXT_LABELS = {
    "metadata_unavailable",
    "not_physical_table_stats",
    "stats_gap_without_row_estimate_evidence",
    "stats_present_with_row_estimate_evidence",
    "unknown",
}
REWRITEABILITY_BUCKETS = {
    "safe_material_draft",
    "recipe_detected_no_draft",
    "recipe_adjacent_shape",
    "stats_likely",
    "human_review_only",
    "not_rewriteable",
    "unknown",
}
NO_DRAFT_CLASSES = {
    "validation_or_materiality",
    "cte_lineage_limit",
    "downstream_cte_filter",
    "missing_final_filter",
    "shape_boundary",
    "predicate_not_copyable",
    "other",
}
NO_DRAFT_RECIPE_IDS = set(RECIPE_LABELS) | {"unknown_recipe"}
HUMAN_REVIEW_RISK_REASONS = {
    "cte_body_validation_not_proven",
    "long_sql_payload",
    "many_ctes",
    "many_top_level_joins",
    "nested_query_body_validation_not_proven",
    "set_operations",
    "sql_payload_too_large_for_safe_rewrite",
    "source_visibility_safe_blocks_sql_draft",
    "too_many_ctes_for_safe_rewrite",
    "too_many_top_level_joins_for_safe_rewrite",
}


def runtime_metrics_provider(config: BatchConfig) -> str:
    if config.query_profile_source == "impala" and config.collect_prometheus_timeseries:
        return "prometheus"
    if config.query_profile_source == "cm" and config.collect_cm_timeseries:
        return "cloudera-manager"
    return "none"


PROFILE_REUSE_CONTRACT_VERSION = "recent_analyzed_profile_reuse_v1"


def profile_reuse_contract(config: BatchConfig) -> dict[str, object]:
    return {
        "version": PROFILE_REUSE_CONTRACT_VERSION,
        "case_artifact_contract": "profile_digest_analysis_json_v1",
        "query_profile_source": config.query_profile_source,
        "source_visibility": config.source_visibility,
        "privacy_mode": bool(config.privacy_mode),
        "redact_identifiers": bool(config.redact_identifiers),
        "redact_hosts": bool(config.redact_hosts),
        "metadata_top_limit": config.metadata_top_limit,
        "collect_cm_timeseries": bool(config.collect_cm_timeseries),
        "collect_prometheus_timeseries": bool(config.collect_prometheus_timeseries),
        "runtime_metrics_provider": runtime_metrics_provider(config),
    }


def build_summary(
    config: BatchConfig,
    discovery: DiscoveryResult,
    cases: list[CaseResult],
    warnings: list[str],
    *,
    discovery_seconds: float | None,
    total_seconds: float,
    discovery_failed: bool = False,
    cluster_context: dict[str, object] | None = None,
) -> dict[str, object]:
    rank_cases_for_query_optimization(cases)
    rank_cases_for_stats_optimization(cases)
    selected_count = len(cases)
    inspected = (
        discovery.summaries_inspected
        if discovery.summaries_inspected is not None
        else len(discovery.candidates)
    )
    reason_counts = candidate_reason_counts(discovery.candidates)
    reason_sql_verb_counts = candidate_reason_sql_verb_counts(discovery.candidates)
    primary_distribution = case_primary_bottleneck_distribution(cases)
    primary_unknown_breakdown = case_primary_unknown_breakdown(cases)
    scoring_distribution = scoring_evidence_source_distribution(cases)
    profile_reuse = profile_reuse_distribution(cases)
    rewriteability_distribution = optimizer_rewriteability_distribution(cases)
    optimizer_funnel_summary = optimizer_funnel(cases, rewriteability_distribution)
    for case in cases:
        update_collectable_metadata_table_count(config, case)
    collectable_metadata_distribution = collectable_metadata_table_count_distribution(cases)
    include_source_coordinates = True
    ranked_case_summaries, workload_groups = case_summaries_with_workload_groups(
        cases,
        include_source_coordinates=include_source_coordinates,
    )
    return {
        "mode": "recent-query-batch",
        "out": str(config.out),
        "cm_inspect_limit": config.cm_inspect_limit,
        "triage_profile_limit": config.triage_profile_limit,
        "select_limit": config.triage_profile_limit,
        "metadata_top_limit": config.metadata_top_limit,
        "collect_cm_timeseries": config.collect_cm_timeseries,
        "cm_timeseries_top_limit": config.cm_timeseries_top_limit,
        "query_profile_source": config.query_profile_source,
        "impala_profile_prefer_json": config.impala_profile_prefer_json,
        "impala_profile_collect_docs": config.impala_profile_collect_docs,
        "impala_collect_admission_context": config.impala_collect_admission_context,
        "source_visibility": config.source_visibility,
        "source_owner_filter_present": bool(
            config.source_visibility == "owner_raw" and config.collectable_owner_users
        ),
        "collect_prometheus_timeseries": config.collect_prometheus_timeseries,
        "prometheus_metrics_profile": config.prometheus_metrics_profile,
        "runtime_metrics_provider": runtime_metrics_provider(config),
        "recent_window_minutes": config.recent_window_minutes,
        "from_time": config.from_time,
        "to_time": config.to_time,
        "min_duration_sec": config.min_duration_sec,
        "query_type_filter": config.query_type or "all",
        "include_failed": config.include_failed,
        "include_running": config.include_running,
        "only_running": config.only_running,
        "user_filter_present": bool(config.user),
        "pool_filter_present": bool(config.pool),
        "order": config.order,
        "duration_filter": duration_filter_label(config),
        "duration_filter_mode": discovery.duration_filter_mode,
        "total_seconds": total_seconds,
        "discovery_seconds": discovery_seconds,
        "server_filter_expression_present": bool(discovery.server_filter_expression),
        "summaries_inspected": inspected,
        "cm_summary_safety_cap": MAX_CM_INSPECT_LIMIT,
        "cm_summary_raw_scan_cap": MAX_RAW_CM_SUMMARY_SCAN_LIMIT,
        "cm_summary_page_size": cm_profiles.CM_QUERY_SUMMARY_PAGE_SIZE,
        "cm_summary_safety_cap_hit": bool(discovery.scan_too_broad),
        "cm_summary_raw_scan_cap_hit": bool(discovery.raw_summary_scan_cap_hit),
        "scan_too_broad": bool(discovery.scan_too_broad),
        "time_sharded": bool(discovery.time_sharded),
        "time_shard_count": discovery.time_shard_count,
        "time_shard_minutes": discovery.time_shard_minutes,
        "time_shard_min_minutes": discovery.time_shard_min_minutes,
        "time_shard_scan_limit_warning_count": discovery.time_shard_scan_limit_warning_count,
        "selected_count": selected_count,
        "candidate_reason_counts": reason_counts,
        "candidate_reason_sql_verb_counts": reason_sql_verb_counts,
        "candidate_exclusion_count": max(0, inspected - selected_count),
        "case_primary_bottleneck_distribution": primary_distribution,
        "case_primary_unknown_breakdown": primary_unknown_breakdown,
        "scoring_evidence_source_distribution": scoring_distribution,
        "profile_reuse": profile_reuse,
        "profile_reuse_contract": profile_reuse_contract(config),
        "profile_reused_case_count": profile_reuse["reused_cases"],
        "optimizer_rewriteability_distribution": rewriteability_distribution,
        "optimizer_funnel": optimizer_funnel_summary,
        "collectable_metadata_table_count_distribution": collectable_metadata_distribution,
        "top_reports": config.top_reports,
        "cm_jobs": config.cm_jobs,
        "jobs": config.jobs,
        "metadata_jobs": config.metadata_jobs,
        "collect_cm_events": config.collect_cm_events,
        "cm_events_max_events": config.cm_events_max_events,
        "cluster_context": cluster_context,
        "warnings": [cm_profiles.sanitize_text_for_log(warning) for warning in warnings],
        "discovery_failed": bool(discovery_failed),
        "workload_groups": workload_groups,
        "cases": ranked_case_summaries,
    }


def batch_ranking_key(case: CaseResult) -> tuple[object, ...]:
    return (
        -case.score,
        -(case.duration_sec or 0),
        -(case.cardinality_anomaly_count or 0),
        -(case.memory_anomaly_count or 0),
        0 if case.backend_data_skew is True else 1,
        -(case.host_tail_candidate_count or 0),
        case.query_id,
        case.index,
    )


def candidate_reason_counts(candidates: list[cm_profiles.RecentQueryCandidate]) -> dict[str, int]:
    counts = Counter(
        cm_profiles.sanitize_text_for_log(candidate.reason or "unknown") for candidate in candidates
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def candidate_reason_sql_verb_counts(
    candidates: list[cm_profiles.RecentQueryCandidate],
) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = {}
    for candidate in candidates:
        reason = cm_profiles.sanitize_text_for_log(candidate.reason or "unknown")
        verb = candidate.sql_verb or "unknown"
        grouped.setdefault(reason, Counter())[cm_profiles.sanitize_text_for_log(verb)] += 1
    return {
        reason: dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
        for reason, counts in sorted(grouped.items())
    }


def collectable_metadata_table_count_distribution(cases: list[CaseResult]) -> dict[str, int]:
    counts = Counter(str(case.collectable_metadata_table_count) for case in cases)
    return dict(sorted(counts.items(), key=lambda item: (int(item[0]), item[0])))


def rank_cases_for_query_optimization(cases: list[CaseResult]) -> list[CaseResult]:
    ranked = sorted(
        [
            case
            for case in cases
            if case.analysis_status == "ok"
            and case.query_optimization_candidate is not None
            and case.query_optimization_candidate.tier in {"high", "medium"}
        ],
        key=lambda case: query_optimization_sort_key(case_to_summary(case)),
    )
    for rank, case in enumerate(ranked, start=1):
        case.query_optimization_rank = rank
    return ranked


def rank_cases_for_stats_optimization(cases: list[CaseResult]) -> list[CaseResult]:
    for case in cases:
        case.stats_optimization_rank = None
    ranked = sorted(
        [
            case
            for case in cases
            if case.stats_optimization_candidate
            and case.stats_optimization_candidate.tier in {"high", "medium"}
        ],
        key=lambda case: stats_optimization_sort_key(case_to_summary(case)),
    )
    for rank, case in enumerate(ranked, start=1):
        case.stats_optimization_rank = rank
    return ranked


def case_primary_bottleneck_distribution(cases: list[CaseResult]) -> dict[str, object]:
    label_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    classified = 0
    for case in cases:
        bottleneck = (
            case.case_primary_bottleneck if isinstance(case.case_primary_bottleneck, dict) else {}
        )
        label = str(bottleneck.get("label") or "").strip().lower()
        confidence = str(bottleneck.get("confidence") or "").strip().lower()
        if label in PRIMARY_BOTTLENECK_LABELS:
            classified += 1
            label_counts[label] += 1
        else:
            label_counts["not_classified"] += 1
        if confidence in PRIMARY_BOTTLENECK_CONFIDENCES:
            confidence_counts[confidence] += 1
        else:
            confidence_counts["unknown"] += 1
    total = len(cases)
    unknown_cases = label_counts.get("unknown", 0)
    mixed_cases = label_counts.get("mixed", 0)
    not_classified_cases = label_counts.get("not_classified", 0)
    medium_or_better = confidence_counts.get("high", 0) + confidence_counts.get("medium", 0)
    return {
        "total_cases": total,
        "classified_cases": classified,
        "not_classified_cases": not_classified_cases,
        "label_counts": dict(sorted(label_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "unknown_cases": unknown_cases,
        "mixed_cases": mixed_cases,
        "unknown_or_not_classified_cases": unknown_cases + not_classified_cases,
        "medium_or_better_confidence_cases": medium_or_better,
        "unknown_rate": ratio(unknown_cases, total),
        "mixed_rate": ratio(mixed_cases, total),
        "unknown_or_not_classified_rate": ratio(unknown_cases + not_classified_cases, total),
        "medium_or_better_confidence_rate": ratio(medium_or_better, total),
    }


def scoring_evidence_source_distribution(cases: list[CaseResult]) -> dict[str, object]:
    source_counts = Counter(
        safe_counter_label(case.scoring_evidence_source, default="not_scored") for case in cases
    )
    fallback_reason_counts = Counter(
        safe_counter_label(case.scoring_fallback_reason)
        for case in cases
        if case.scoring_fallback_reason
    )
    fallback_cases = sum(
        1
        for case in cases
        if case.scoring_evidence_source == "markdown_fallback" or bool(case.scoring_fallback_reason)
    )
    total = len(cases)
    return {
        "total_cases": total,
        "source_counts": dict(sorted(source_counts.items())),
        "analysis_json_cases": source_counts.get("analysis_json", 0),
        "markdown_fallback_cases": source_counts.get("markdown_fallback", 0),
        "not_scored_cases": source_counts.get("not_scored", 0),
        "fallback_cases": fallback_cases,
        "fallback_rate": ratio(fallback_cases, total),
        "fallback_reason_counts": dict(sorted(fallback_reason_counts.items())),
    }


def profile_reuse_distribution(cases: list[CaseResult]) -> dict[str, object]:
    status_counts = Counter(
        safe_counter_label(case.profile_reuse_status, default="not_requested") for case in cases
    )
    total = len(cases)
    reused = status_counts.get("reused", 0)
    return {
        "total_cases": total,
        "reused_cases": reused,
        "not_reused_cases": max(0, total - reused),
        "status_counts": dict(sorted(status_counts.items())),
        "reuse_rate": ratio(reused, total),
    }


def case_primary_unknown_breakdown(cases: list[CaseResult]) -> dict[str, object]:
    metadata_status_counts: Counter[str] = Counter()
    query_tier_counts: Counter[str] = Counter()
    stats_tier_counts: Counter[str] = Counter()
    duration_bucket_counts: Counter[str] = Counter()
    score_severity_counts: Counter[str] = Counter()
    finding_id_counts: Counter[str] = Counter()
    top_time_operator_counts: Counter[str] = Counter()
    evidence_quality_level_counts: Counter[str] = Counter()
    evidence_limitation_counts: Counter[str] = Counter()
    runtime_diagnosis_counts: Counter[str] = Counter()
    stats_primary_counts: Counter[str] = Counter()
    stats_context_counts: Counter[str] = Counter()
    analysis_json_cases = 0
    unknown_cases = [case for case in cases if case_primary_label(case) == "unknown"]
    for case in unknown_cases:
        metadata_status_counts[
            known_or_other(case.metadata_status, UNKNOWN_METADATA_STATUS_LABELS)
        ] += 1
        query_tier_counts[
            known_or_other(
                candidate_tier(case.query_optimization_candidate), UNKNOWN_OPTIMIZATION_TIER_LABELS
            )
        ] += 1
        stats_tier_counts[
            known_or_other(
                candidate_tier(case.stats_optimization_candidate), UNKNOWN_OPTIMIZATION_TIER_LABELS
            )
        ] += 1
        duration_bucket_counts[duration_bucket(case.duration_sec)] += 1
        score_severity_counts[case_score_severity(case)] += 1
        analysis = load_case_analysis(case)
        if not isinstance(analysis, dict):
            continue
        analysis_json_cases += 1
        for finding in analysis.get("findings") or []:
            if isinstance(finding, dict):
                finding_id_counts[known_or_other(finding.get("id"), UNKNOWN_FINDING_IDS)] += 1
        top_operator = first_operator_name(analysis.get("top_operators_by_time"))
        top_time_operator_counts[operator_category(top_operator)] += 1
        evidence_quality = analysis.get("evidence_quality")
        if isinstance(evidence_quality, dict):
            evidence_quality_level_counts[
                known_or_other(evidence_quality.get("level"), UNKNOWN_EVIDENCE_QUALITY_LABELS)
            ] += 1
            for limitation in evidence_quality.get("limitations") or []:
                evidence_limitation_counts[
                    known_or_other(limitation, UNKNOWN_LIMITATION_LABELS)
                ] += 1
        runtime_diagnosis = analysis.get("runtime_diagnosis")
        if isinstance(runtime_diagnosis, dict):
            runtime_diagnosis_counts[
                runtime_diagnosis_bucket(runtime_diagnosis.get("summary"))
            ] += 1
        stats_quality = analysis.get("stats_metadata_quality")
        if isinstance(stats_quality, dict):
            stats_primary_counts[
                known_or_other(
                    stats_quality.get("stats_primary_bottleneck"), UNKNOWN_STATS_PRIMARY_LABELS
                )
            ] += 1
            stats_context_counts[
                known_or_other(stats_quality.get("stats_context"), UNKNOWN_STATS_CONTEXT_LABELS)
            ] += 1
    return {
        "total_cases": len(unknown_cases),
        "analysis_json_cases": analysis_json_cases,
        "metadata_status_counts": sorted_counter(metadata_status_counts),
        "query_optimization_tier_counts": sorted_counter(query_tier_counts),
        "stats_optimization_tier_counts": sorted_counter(stats_tier_counts),
        "duration_bucket_counts": sorted_counter(duration_bucket_counts),
        "score_severity_counts": sorted_counter(score_severity_counts),
        "finding_id_counts": sorted_counter(finding_id_counts),
        "top_time_operator_counts": sorted_counter(top_time_operator_counts),
        "evidence_quality_level_counts": sorted_counter(evidence_quality_level_counts),
        "evidence_limitation_counts": sorted_counter(evidence_limitation_counts),
        "runtime_diagnosis_summary_counts": sorted_counter(runtime_diagnosis_counts),
        "stats_primary_bottleneck_counts": sorted_counter(stats_primary_counts),
        "stats_context_counts": sorted_counter(stats_context_counts),
    }


def case_primary_label(case: CaseResult) -> str:
    bottleneck = (
        case.case_primary_bottleneck if isinstance(case.case_primary_bottleneck, dict) else {}
    )
    return str(bottleneck.get("label") or "").strip().lower()


def candidate_tier(candidate: object | None) -> object:
    return getattr(candidate, "tier", None)


def load_case_analysis(case: CaseResult) -> dict[str, object] | None:
    if case.actual_case_dir is None:
        return None
    analysis_path = case.actual_case_dir / "analysis.json"
    if not analysis_path.exists():
        return None
    try:
        payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def safe_counter_label(value: object, *, default: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    safe = "".join(
        character if character.isalnum() or character == "_" else "_" for character in text
    )
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or default


def known_or_other(value: object, known_values: set[str]) -> str:
    label = safe_counter_label(value)
    return label if label in known_values else "other"


def duration_bucket(duration_sec: float | None) -> str:
    if duration_sec is None:
        return "unknown"
    if duration_sec < 60:
        return "lt_60s"
    if duration_sec < 120:
        return "60_120s"
    if duration_sec < 200:
        return "120_200s"
    if duration_sec < 600:
        return "200_600s"
    return "600s_plus"


def first_operator_name(value: object) -> str:
    if not isinstance(value, list):
        return ""
    for item in value:
        if isinstance(item, dict) and item.get("operator_name"):
            return str(item["operator_name"])
    return ""


def operator_category(operator_name: str) -> str:
    name = operator_name.upper()
    for category in sorted(UNKNOWN_OPERATOR_CATEGORIES, key=len, reverse=True):
        if category in name:
            return category
    return "OTHER" if name else "UNKNOWN"


def runtime_diagnosis_bucket(summary: object) -> str:
    text = str(summary or "").strip().lower()
    if not text:
        return "unknown"
    if "hdfs" in text or "storage" in text:
        return "storage_or_hdfs"
    if "admission" in text:
        return "admission"
    if "skew" in text or "tail" in text:
        return "skew_or_tail"
    if "no single" in text:
        return "no_single_runtime_hypothesis"
    return "other"


def sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def optimizer_rewriteability_distribution(cases: list[CaseResult]) -> dict[str, object]:
    bucket_counts: Counter[str] = Counter()
    no_draft_recipe_counts: Counter[str] = Counter()
    no_draft_eligibility_counts: Counter[str] = Counter()
    no_draft_class_counts: Counter[str] = Counter()
    no_draft_class_recipe_counts: dict[str, Counter[str]] = {}
    no_draft_class_recipe_reason_counts: dict[str, dict[str, Counter[str]]] = {}
    no_draft_reason_counts: Counter[str] = Counter()
    no_draft_cte_pushdown_decision_counts: Counter[str] = Counter()
    no_draft_actionability_counts: Counter[str] = Counter()
    no_recipe_review_track_counts: Counter[str] = Counter()
    adjacent_cte_graph_counts: Counter[str] = Counter()
    adjacent_cte_predicate_pushdown_counts: Counter[str] = Counter()
    adjacent_cte_boundary_reason_counts: Counter[str] = Counter()
    adjacent_derived_predicate_pushdown_counts: Counter[str] = Counter()
    adjacent_derived_boundary_reason_counts: Counter[str] = Counter()
    adjacent_actionability_counts: Counter[str] = Counter()
    human_review_status_counts: Counter[str] = Counter()
    human_review_eligibility_counts: Counter[str] = Counter()
    human_review_risk_reason_counts: Counter[str] = Counter()
    optimization_candidate_count = 0
    for case in cases:
        support = case.optimizer_rewrite_support
        if support is None:
            bucket_counts["unknown"] += 1
            continue
        if support.status != "not_candidate":
            optimization_candidate_count += 1
        bucket = str(support.rewriteability_bucket or "unknown").strip().lower()
        normalized_bucket = bucket if bucket in REWRITEABILITY_BUCKETS else "unknown"
        bucket_counts[normalized_bucket] += 1
        if support.draft_eligibility in {"no_recipe", "source_unavailable"}:
            no_recipe_review_track_counts[
                normalize_no_recipe_review_track(support.no_recipe_review_track)
            ] += 1
        if normalized_bucket == "recipe_detected_no_draft":
            recipe_id = normalize_no_draft_recipe_id(support.recipe_id)
            eligibility = str(support.draft_eligibility or "unknown").strip() or "unknown"
            no_draft_class = normalize_no_draft_class(support.draft_unavailable_class)
            no_draft_actionability = optimizer_no_draft_actionability(support.to_dict())
            no_draft_recipe_counts[recipe_id] += 1
            no_draft_eligibility_counts[eligibility] += 1
            no_draft_class_counts[no_draft_class] += 1
            no_draft_actionability_counts[no_draft_actionability] += 1
            no_draft_class_recipe_counts.setdefault(no_draft_class, Counter())[recipe_id] += 1
            no_draft_reasons = tuple(str(reason) for reason in support.draft_unavailable_reasons)
            no_draft_reason_counts.update(no_draft_reasons)
            class_recipe_reason_counts = no_draft_class_recipe_reason_counts.setdefault(
                no_draft_class,
                {},
            ).setdefault(recipe_id, Counter())
            class_recipe_reason_counts.update(no_draft_reasons)
            no_draft_cte_pushdown_decision_counts.update(
                {
                    str(reason): count
                    for reason, count in support.cte_pushdown_conjunct_decision_counts.items()
                    if isinstance(count, int) and count > 0
                }
            )
        elif normalized_bucket == "recipe_adjacent_shape":
            adjacent_actionability = optimizer_adjacent_actionability(support.to_dict())
            adjacent_actionability_counts[adjacent_actionability] += 1
            adjacent_cte_graph_counts[normalize_adjacent_label(support.cte_graph_shape)] += 1
            adjacent_cte_predicate_pushdown_counts[
                normalize_adjacent_label(support.cte_predicate_pushdown_status)
            ] += 1
            adjacent_cte_boundary_reason_counts.update(
                normalize_adjacent_label(reason) for reason in support.cte_boundary_reasons
            )
            adjacent_derived_predicate_pushdown_counts[
                normalize_adjacent_label(support.derived_predicate_pushdown_status)
            ] += 1
            adjacent_derived_boundary_reason_counts.update(
                normalize_adjacent_label(reason) for reason in support.derived_boundary_reasons
            )
        elif normalized_bucket == "human_review_only":
            human_review_status_counts[normalize_adjacent_label(support.status)] += 1
            human_review_eligibility_counts[
                normalize_adjacent_label(support.draft_eligibility)
            ] += 1
            human_review_risk_reason_counts.update(
                normalize_human_review_risk_reason(reason) for reason in support.risk_reasons
            )
    total = len(cases)
    safe_material_draft = bucket_counts.get("safe_material_draft", 0)
    no_draft = bucket_counts.get("recipe_detected_no_draft", 0)
    no_draft_actionable = no_draft_actionability_counts.get("actionable", 0)
    no_draft_structural = no_draft_actionability_counts.get("structural_boundary", 0)
    no_draft_validation = no_draft_actionability_counts.get("validation_or_materiality", 0)
    no_draft_other = no_draft_actionability_counts.get("other", 0)
    recipe_adjacent = bucket_counts.get("recipe_adjacent_shape", 0)
    adjacent_actionable = adjacent_actionability_counts.get("actionable", 0)
    adjacent_structural = adjacent_actionability_counts.get("structural_boundary", 0)
    adjacent_other = adjacent_actionability_counts.get("other", 0)
    stats_likely = bucket_counts.get("stats_likely", 0)
    human_review = bucket_counts.get("human_review_only", 0)
    return {
        "total_cases": total,
        "optimization_candidate_cases": optimization_candidate_count,
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "safe_material_draft_cases": safe_material_draft,
        "recipe_detected_no_draft_cases": no_draft,
        "recipe_detected_no_draft_actionable_cases": no_draft_actionable,
        "recipe_detected_no_draft_structural_boundary_cases": no_draft_structural,
        "recipe_detected_no_draft_validation_or_materiality_cases": no_draft_validation,
        "recipe_detected_no_draft_other_cases": no_draft_other,
        "recipe_detected_no_draft_actionability_counts": dict(
            sorted(no_draft_actionability_counts.items())
        ),
        "recipe_detected_no_draft_recipe_counts": dict(sorted(no_draft_recipe_counts.items())),
        "recipe_detected_no_draft_eligibility_counts": dict(
            sorted(no_draft_eligibility_counts.items())
        ),
        "recipe_detected_no_draft_class_counts": dict(sorted(no_draft_class_counts.items())),
        "recipe_detected_no_draft_class_recipe_counts": {
            no_draft_class: dict(sorted(recipe_counts.items()))
            for no_draft_class, recipe_counts in sorted(no_draft_class_recipe_counts.items())
        },
        "recipe_detected_no_draft_class_recipe_reason_counts": {
            no_draft_class: {
                recipe_id: dict(sorted(reason_counts.items()))
                for recipe_id, reason_counts in sorted(recipe_counts.items())
            }
            for no_draft_class, recipe_counts in sorted(no_draft_class_recipe_reason_counts.items())
        },
        "recipe_detected_no_draft_reason_counts": dict(sorted(no_draft_reason_counts.items())),
        "recipe_detected_no_draft_cte_pushdown_decision_counts": dict(
            sorted(no_draft_cte_pushdown_decision_counts.items())
        ),
        "no_recipe_review_track_counts": dict(sorted(no_recipe_review_track_counts.items())),
        "recipe_adjacent_shape_cases": recipe_adjacent,
        "recipe_adjacent_actionable_cases": adjacent_actionable,
        "recipe_adjacent_structural_boundary_cases": adjacent_structural,
        "recipe_adjacent_other_cases": adjacent_other,
        "recipe_adjacent_actionability_counts": dict(sorted(adjacent_actionability_counts.items())),
        "recipe_adjacent_cte_graph_counts": dict(sorted(adjacent_cte_graph_counts.items())),
        "recipe_adjacent_cte_predicate_pushdown_counts": dict(
            sorted(adjacent_cte_predicate_pushdown_counts.items())
        ),
        "recipe_adjacent_cte_boundary_reason_counts": dict(
            sorted(adjacent_cte_boundary_reason_counts.items())
        ),
        "recipe_adjacent_derived_predicate_pushdown_counts": dict(
            sorted(adjacent_derived_predicate_pushdown_counts.items())
        ),
        "recipe_adjacent_derived_boundary_reason_counts": dict(
            sorted(adjacent_derived_boundary_reason_counts.items())
        ),
        "stats_likely_cases": stats_likely,
        "human_review_only_cases": human_review,
        "human_review_only_status_counts": dict(sorted(human_review_status_counts.items())),
        "human_review_only_draft_eligibility_counts": dict(
            sorted(human_review_eligibility_counts.items())
        ),
        "human_review_only_risk_reason_counts": dict(
            sorted(human_review_risk_reason_counts.items())
        ),
        "safe_material_draft_rate": ratio(safe_material_draft, total),
        "recipe_backlog_rate": ratio(no_draft + recipe_adjacent, total),
        "recipe_backlog_actionable_cases": no_draft_actionable + adjacent_actionable,
        "recipe_backlog_actionable_rate": ratio(no_draft_actionable + adjacent_actionable, total),
        "stats_likely_rate": ratio(stats_likely, total),
        "human_review_only_rate": ratio(human_review, total),
    }


def optimizer_funnel(
    cases: list[CaseResult],
    rewriteability_distribution: dict[str, object] | None = None,
) -> dict[str, object]:
    distribution = rewriteability_distribution or optimizer_rewriteability_distribution(cases)
    bucket_counts = distribution.get("bucket_counts")
    bucket_counts = bucket_counts if isinstance(bucket_counts, dict) else {}
    candidate_cases = int(distribution.get("optimization_candidate_cases") or 0)
    recipe_detected_cases = 0
    draft_ready_cases = 0
    draft_disabled_by_threshold_cases = 0
    source_unavailable_cases = 0
    for case in cases:
        support = case.optimizer_rewrite_support
        if support is None:
            continue
        if support.recipe_detected:
            recipe_detected_cases += 1
        if support.draft_eligibility == "safe_to_attempt":
            draft_ready_cases += 1
        elif support.draft_eligibility == "disabled_by_safety_thresholds":
            draft_disabled_by_threshold_cases += 1
        elif support.draft_eligibility == "source_unavailable":
            source_unavailable_cases += 1
    no_draft_cases = int(distribution.get("recipe_detected_no_draft_cases") or 0)
    no_draft_actionable_cases = int(
        distribution.get("recipe_detected_no_draft_actionable_cases") or 0
    )
    no_draft_structural_cases = int(
        distribution.get("recipe_detected_no_draft_structural_boundary_cases") or 0
    )
    no_draft_validation_cases = int(
        distribution.get("recipe_detected_no_draft_validation_or_materiality_cases") or 0
    )
    no_draft_other_cases = int(distribution.get("recipe_detected_no_draft_other_cases") or 0)
    adjacent_cases = int(distribution.get("recipe_adjacent_shape_cases") or 0)
    adjacent_actionable_cases = int(distribution.get("recipe_adjacent_actionable_cases") or 0)
    adjacent_structural_cases = int(
        distribution.get("recipe_adjacent_structural_boundary_cases") or 0
    )
    adjacent_other_cases = int(distribution.get("recipe_adjacent_other_cases") or 0)
    stats_likely_cases = int(distribution.get("stats_likely_cases") or 0)
    human_review_cases = int(distribution.get("human_review_only_cases") or 0)
    not_rewriteable_cases = int(bucket_counts.get("not_rewriteable") or 0)
    unknown_cases = int(bucket_counts.get("unknown") or 0)
    return {
        "total_cases": len(cases),
        "optimization_candidate_cases": candidate_cases,
        "recipe_detected_cases": recipe_detected_cases,
        "draft_ready_cases": draft_ready_cases,
        "trusted_sql_draft_produced_cases": 0,
        "trusted_sql_draft_produced_note": (
            "Recent batch scans classify draft readiness only; trusted SQL drafts "
            "are produced later by explicit selected-case optimizer actions."
        ),
        "recipe_detected_no_draft_cases": no_draft_cases,
        "recipe_detected_no_draft_actionable_cases": no_draft_actionable_cases,
        "recipe_detected_no_draft_structural_boundary_cases": no_draft_structural_cases,
        "recipe_detected_no_draft_validation_or_materiality_cases": no_draft_validation_cases,
        "recipe_detected_no_draft_other_cases": no_draft_other_cases,
        "recipe_adjacent_shape_cases": adjacent_cases,
        "recipe_adjacent_actionable_cases": adjacent_actionable_cases,
        "recipe_adjacent_structural_boundary_cases": adjacent_structural_cases,
        "recipe_adjacent_other_cases": adjacent_other_cases,
        "draft_disabled_by_safety_threshold_cases": draft_disabled_by_threshold_cases,
        "source_unavailable_cases": source_unavailable_cases,
        "stats_likely_cases": stats_likely_cases,
        "human_review_only_cases": human_review_cases,
        "not_rewriteable_cases": not_rewriteable_cases,
        "unknown_cases": unknown_cases,
        "recipe_detected_rate": ratio(recipe_detected_cases, candidate_cases),
        "draft_ready_rate": ratio(draft_ready_cases, candidate_cases),
        "trusted_sql_draft_produced_rate": 0.0,
        "recipe_backlog_rate": ratio(no_draft_cases + adjacent_cases, candidate_cases),
        "recipe_backlog_actionable_cases": no_draft_actionable_cases + adjacent_actionable_cases,
        "recipe_backlog_actionable_rate": ratio(
            no_draft_actionable_cases + adjacent_actionable_cases,
            candidate_cases,
        ),
    }


def normalize_no_draft_class(value: object) -> str:
    no_draft_class = str(value or "other").strip().lower()
    return no_draft_class if no_draft_class in NO_DRAFT_CLASSES else "other"


def normalize_no_draft_recipe_id(value: object) -> str:
    recipe_id = str(value or "unknown_recipe").strip()
    return recipe_id if recipe_id in NO_DRAFT_RECIPE_IDS else "unknown_recipe"


def normalize_no_recipe_review_track(value: object) -> str:
    track = str(value or "").strip().lower()
    if not track or track == "not_applicable":
        return "unknown"
    return track if track in NO_RECIPE_REVIEW_TRACKS else "unknown"


def normalize_adjacent_label(value: object) -> str:
    label = str(value or "unknown").strip().lower()
    if not label:
        return "unknown"
    if all(character.isalnum() or character == "_" for character in label):
        return label
    return "other"


def normalize_human_review_risk_reason(value: object) -> str:
    label = normalize_adjacent_label(value)
    return label if label in HUMAN_REVIEW_RISK_REASONS else "other"


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def case_summaries_with_workload_groups(
    cases: list[CaseResult],
    *,
    include_source_coordinates: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    records: list[tuple[dict[str, object], WorkloadFingerprint]] = []
    for case in sorted(cases, key=batch_ranking_key):
        analysis = load_case_analysis(case)
        summary = _case_to_summary_base(
            case,
            analysis=analysis,
            include_source_coordinates=include_source_coordinates,
        )
        workload = compute_workload_fingerprint(summary, analysis)
        attach_workload_fingerprint_fields(summary, workload)
        records.append((summary, workload))
    workload_groups = build_workload_groups(records)
    return [summary for summary, _workload in records], workload_groups


def case_to_summary(
    case: CaseResult,
    *,
    include_source_coordinates: bool = False,
) -> dict[str, object]:
    analysis = load_case_analysis(case)
    summary = _case_to_summary_base(
        case,
        analysis=analysis,
        include_source_coordinates=include_source_coordinates,
    )
    summary["operator_inspection"] = exchange_inspection_summary(analysis)
    attach_workload_fingerprint_fields(
        summary,
        compute_workload_fingerprint(summary, analysis),
    )
    return summary


def _case_to_summary_base(
    case: CaseResult,
    *,
    analysis: dict[str, object] | None = None,
    include_source_coordinates: bool = False,
) -> dict[str, object]:
    stage_seconds = [
        value
        for value in (case.cm_collect_seconds, case.analysis_seconds, case.report_seconds)
        if value is not None
    ]
    return {
        "case_index": case.index,
        "candidate_rank": case.candidate_rank,
        "triage_rank": case.triage_rank,
        "query_id": case.query_id,
        "duration_sec": case.duration_sec,
        "user": cm_profiles.sanitize_text_for_log(case.user) if case.user else None,
        "pool": cm_profiles.sanitize_text_for_log(case.pool) if case.pool else None,
        "query_type": case.query_type,
        "sql_verb": case.sql_verb,
        "collection_status": case.collection_status,
        "analysis_status": case.analysis_status,
        "profile_reuse_status": case.profile_reuse_status,
        "metadata_status": case.metadata_status,
        "table_stats_status": case.table_stats_status,
        "referenced_table_count": case.referenced_table_count,
        "collectable_metadata_table_count": case.collectable_metadata_table_count,
        "collected_metadata_table_count": case.collected_metadata_table_count,
        "skipped_due_to_max_table_limit": case.skipped_due_to_max_table_limit,
        "too_large_count": case.too_large_count,
        "score": case.score,
        "score_severity": case_score_severity(case),
        "score_reasons": case.score_reasons,
        "scoring_evidence_source": case.scoring_evidence_source,
        "scoring_fallback_reason": case.scoring_fallback_reason,
        "query_optimization_candidate": case.query_optimization_candidate.to_dict()
        if case.query_optimization_candidate
        else None,
        "optimizer_rewrite_support": case.optimizer_rewrite_support.to_dict()
        if case.optimizer_rewrite_support
        else None,
        "query_optimization_rank": case.query_optimization_rank,
        "stats_optimization_candidate": case.stats_optimization_candidate.to_dict()
        if case.stats_optimization_candidate
        else None,
        "stats_optimization_rank": case.stats_optimization_rank,
        "case_primary_bottleneck": case.case_primary_bottleneck,
        "source_locators": build_source_locators(
            case,
            analysis,
            include_source_coordinates=include_source_coordinates,
        ),
        "cardinality_anomaly_count": case.cardinality_anomaly_count,
        "memory_anomaly_count": case.memory_anomaly_count,
        "zero_row_estimate_gap_count": case.zero_row_estimate_gap_count,
        "zero_memory_estimate_gap_count": case.zero_memory_estimate_gap_count,
        "backend_data_skew": case.backend_data_skew,
        "host_tail_candidate_count": case.host_tail_candidate_count,
        "execution_tail_candidate_count": case.execution_tail_candidate_count,
        "case_dir": str(case.wrapper_dir),
        "report_generated": case.report_generated,
        "report_validation_status": case.report_validation_status,
        "metadata_refreshed": case.metadata_refreshed,
        "failure_category": case.failure_category,
        "failure_reason": cm_profiles.sanitize_text_for_log(case.failure_reason)
        if case.failure_reason
        else None,
        "cm_collect_seconds": case.cm_collect_seconds,
        "analysis_seconds": case.analysis_seconds,
        "report_seconds": case.report_seconds,
        "total_seconds": round(sum(stage_seconds), 3) if stage_seconds else None,
    }


def attach_workload_fingerprint_fields(
    summary: dict[str, object],
    workload: WorkloadFingerprint,
) -> None:
    summary["workload_fingerprint"] = workload.fingerprint
    summary["group_fingerprint"] = workload.fingerprint
    summary["workload_shape"] = workload_group_shape(workload)
    incomplete = bool(workload.shape.get("incomplete"))
    summary["workload_fingerprint_incomplete"] = incomplete
    summary["workload_fingerprint_incomplete_fields"] = (
        workload_fingerprint_incomplete_fields(workload) if incomplete else []
    )


def workload_fingerprint_incomplete_fields(workload: WorkloadFingerprint) -> list[str]:
    raw_fields = workload.shape.get("incomplete_fields")
    if not isinstance(raw_fields, (list, tuple)):
        return []
    fields = {field for item in raw_fields if (field := safe_workload_shape_field(item))}
    return sorted(fields)


def safe_workload_shape_field(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text if all(character.isalnum() or character == "_" for character in text) else ""


def build_workload_groups(
    records: list[tuple[dict[str, object], WorkloadFingerprint]],
) -> dict[str, object]:
    grouped: dict[str, list[tuple[dict[str, object], WorkloadFingerprint]]] = {}
    for summary, workload in records:
        if workload.shape.get("incomplete"):
            continue
        grouped.setdefault(workload.fingerprint, []).append((summary, workload))

    visible_groups: list[dict[str, object]] = []
    for fingerprint, members in sorted(grouped.items()):
        aggregates = workload_group_aggregates([summary for summary, _workload in members])
        member_count = int(aggregates["count"])
        for summary, _workload in members:
            summary["workload_group_member_count"] = member_count
            summary["workload_group_duration_sec_p95"] = aggregates.get("duration_sec_p95")
        if member_count < 2:
            continue
        visible_groups.append(
            {
                "fingerprint": fingerprint,
                "shape": workload_group_shape(members[0][1]),
                "aggregates": aggregates,
                "member_count": member_count,
                "member_case_ids": [
                    case_id
                    for summary, _workload in members
                    if (case_id := local_case_id(summary)) is not None
                ],
            }
        )

    visible_groups.sort(key=workload_group_sort_key)
    return {"schema_version": 1, "groups": visible_groups}


def workload_group_shape(workload: WorkloadFingerprint) -> dict[str, object]:
    return {
        key: value
        for key, value in workload.shape.items()
        if key not in {"incomplete", "incomplete_fields"}
    }


def workload_group_aggregates(summaries: list[dict[str, object]]) -> dict[str, object]:
    durations = sorted(
        value
        for summary in summaries
        if (value := numeric_float(summary.get("duration_sec"))) is not None
    )
    aggregates: dict[str, object] = {
        "count": len(summaries),
        "member_count": len(summaries),
        "duration_sec_total": round(sum(durations), 3) if durations else None,
        "duration_sec_p50": percentile_value(durations, 0.50),
        "duration_sec_p95": percentile_value(durations, 0.95),
        "pool_top": modal_label(summary.get("pool") for summary in summaries),
        "primary_bottleneck_top": modal_label(
            primary_bottleneck_label(summary) for summary in summaries
        ),
        "score_top": modal_label(summary.get("score_severity") for summary in summaries),
    }
    return {key: value for key, value in aggregates.items() if value is not None}


def workload_group_sort_key(group: dict[str, object]) -> tuple[object, ...]:
    aggregates = group.get("aggregates") if isinstance(group.get("aggregates"), dict) else {}
    duration_total = numeric_float(aggregates.get("duration_sec_total")) or 0.0
    member_count = numeric_int(group.get("member_count")) or 0
    return (-duration_total, -member_count, str(group.get("fingerprint") or ""))


def percentile_value(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) * percentile) + 0.999999) - 1))
    return round(values[index], 3)


def modal_label(values: object) -> str | None:
    counter: Counter[str] = Counter()
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            counter[cm_profiles.sanitize_text_for_log(text)] += 1
    if not counter:
        return None
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]


def primary_bottleneck_label(summary: dict[str, object]) -> object:
    primary = summary.get("case_primary_bottleneck")
    return primary.get("label") if isinstance(primary, dict) else None


def local_case_id(summary: dict[str, object]) -> str | None:
    index = numeric_int(summary.get("case_index"))
    if index is None or index <= 0:
        return None
    return f"case-{index:03d}"


def numeric_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def numeric_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def case_score_severity(case: CaseResult) -> str:
    if case_has_processing_failure(case):
        return "failed"
    if case.score <= 0:
        if primary_bottleneck_promotes_attention(case.case_primary_bottleneck):
            return "suspicious"
        return "clean"
    cardinality = case.cardinality_anomaly_count or 0
    memory = case.memory_anomaly_count or 0
    zero_row_gaps = case.zero_row_estimate_gap_count or 0
    zero_memory_gaps = case.zero_memory_estimate_gap_count or 0
    host_tail = case.host_tail_candidate_count or 0
    execution_tail = case.execution_tail_candidate_count
    if execution_tail is None:
        execution_tail = host_tail
    if (
        case.score >= 30
        or cardinality >= 5
        or memory >= 4
        or zero_row_gaps >= 4
        or zero_memory_gaps >= 4
        or (cardinality >= 3 and memory >= 2)
        or (zero_row_gaps >= 2 and zero_memory_gaps >= 2)
        or (case.backend_data_skew is True and host_tail >= 2)
        or (execution_tail >= 1 and (case.duration_sec or 0) >= 1800)
    ):
        return "high"
    return "suspicious"


def primary_bottleneck_promotes_attention(primary: object) -> bool:
    if not isinstance(primary, dict):
        return False
    label = str(primary.get("label") or "").strip().lower()
    confidence = str(primary.get("confidence") or "").strip().lower()
    if label in {"", "unknown", "not_classified", "not classified"}:
        return False
    return confidence in {"medium", "high"}


def case_has_processing_failure(case: CaseResult) -> bool:
    if case.failure_category:
        return True
    return (
        case.collection_status == "failed"
        or case.analysis_status == "failed"
        or case.metadata_status == "failed"
        or case.report_validation_status == "failed"
    )


def write_batch_outputs(out: Path, summary: dict[str, object]) -> None:
    (out / "batch_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    optimizer_funnel_summary = summary.get("optimizer_funnel")
    if isinstance(optimizer_funnel_summary, dict):
        (out / "optimizer_funnel.json").write_text(
            json.dumps(optimizer_funnel_summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    lines = [
        "# Query Doctor Recent Batch Summary",
        "",
        f"- summaries inspected: {summary['summaries_inspected']}",
        f"- selected candidates: {summary['selected_count']}",
        f"- excluded candidates: {summary.get('candidate_exclusion_count', 0)}",
        f"- triage profile limit: {summary['triage_profile_limit']}",
        f"- metadata top limit: {summary['metadata_top_limit']}",
        f"- runtime metrics provider: {summary.get('runtime_metrics_provider') or 'none'}",
        f"- reused analyzed profiles: {summary.get('profile_reused_case_count', 0)}",
        f"- collect runtime metrics: {bool(summary.get('collect_cm_timeseries') or summary.get('collect_prometheus_timeseries'))}",
        f"- runtime metrics top limit: {summary['cm_timeseries_top_limit']}",
        f"- search depth minutes: {summary['recent_window_minutes']}",
        f"- explicit time window: {summary.get('from_time') or 'relative'} -> {summary.get('to_time') or 'relative'}",
        f"- time-sharded discovery: {summary.get('time_sharded', False)}",
        f"- time shard count: {summary.get('time_shard_count', 0)}",
        f"- time shard min minutes: {summary.get('time_shard_min_minutes') or 'n/a'}",
        f"- query type filter: {summary['query_type_filter']}",
        f"- duration filter: {summary['duration_filter']}",
        f"- include failed: {summary['include_failed']}",
        f"- include running: {summary['include_running']}",
        f"- only running: {summary.get('only_running', False)}",
        f"- top reports: {summary['top_reports']}",
        f"- CM jobs: {summary['cm_jobs']}",
        f"- analyzer jobs: {summary['jobs']}",
        f"- metadata jobs: {summary['metadata_jobs']}",
        f"- collect CM events: {summary['collect_cm_events']}",
        f"- CM events max events: {summary['cm_events_max_events']}",
        f"- discovery seconds: {summary['discovery_seconds']}",
        f"- total seconds: {summary['total_seconds']}",
        "",
    ]
    reason_counts = summary.get("candidate_reason_counts")
    if isinstance(reason_counts, dict) and reason_counts:
        lines.extend(["## Candidate Selection Breakdown", ""])
        for reason, count in reason_counts.items():
            lines.append(f"- {reason}: {count}")
        lines.append("")
    workload_history = summary.get("workload_history")
    if isinstance(workload_history, dict):
        regression_counts = workload_history.get("regression_counts")
        rendered_regressions = "none"
        if isinstance(regression_counts, dict) and regression_counts:
            rendered_regressions = ", ".join(
                f"{label}={count}" for label, count in sorted(regression_counts.items())
            )
        lines.extend(
            [
                "## Workload History",
                "",
                f"- loaded records: {workload_history.get('loaded_record_count', 0)}",
                f"- appended records: {workload_history.get('appended_record_count', 0)}",
                f"- append status: {workload_history.get('append_status', 'unknown')}",
                f"- regressions: {rendered_regressions}",
                "",
            ]
        )
    scoring_distribution = summary.get("scoring_evidence_source_distribution")
    if isinstance(scoring_distribution, dict):
        source_counts = scoring_distribution.get("source_counts")
        fallback_reason_counts = scoring_distribution.get("fallback_reason_counts")
        lines.extend(
            [
                "## Scoring Evidence Source",
                "",
                f"- analysis JSON cases: {scoring_distribution.get('analysis_json_cases', 0)} / {scoring_distribution.get('total_cases', 0)}",
                f"- markdown fallback cases: {scoring_distribution.get('markdown_fallback_cases', 0)} ({scoring_distribution.get('fallback_rate', 0.0)})",
                f"- not scored cases: {scoring_distribution.get('not_scored_cases', 0)}",
            ]
        )
        if isinstance(source_counts, dict) and source_counts:
            rendered_sources = ", ".join(
                f"{source}={count}" for source, count in sorted(source_counts.items())
            )
            lines.append(f"- sources: {rendered_sources}")
        if isinstance(fallback_reason_counts, dict) and fallback_reason_counts:
            rendered_reasons = ", ".join(
                f"{reason}={count}" for reason, count in sorted(fallback_reason_counts.items())
            )
            lines.append(f"- fallback reasons: {rendered_reasons}")
        lines.append("")
    primary_distribution = summary.get("case_primary_bottleneck_distribution")
    if isinstance(primary_distribution, dict):
        label_counts = primary_distribution.get("label_counts")
        confidence_counts = primary_distribution.get("confidence_counts")
        lines.extend(
            [
                "## Primary Bottleneck Distribution",
                "",
                f"- classified cases: {primary_distribution.get('classified_cases', 0)} / {primary_distribution.get('total_cases', 0)}",
                f"- unknown cases: {primary_distribution.get('unknown_cases', 0)} ({primary_distribution.get('unknown_rate', 0.0)})",
                f"- mixed cases: {primary_distribution.get('mixed_cases', 0)} ({primary_distribution.get('mixed_rate', 0.0)})",
                f"- not classified cases: {primary_distribution.get('not_classified_cases', 0)}",
                f"- medium-or-better confidence cases: {primary_distribution.get('medium_or_better_confidence_cases', 0)} ({primary_distribution.get('medium_or_better_confidence_rate', 0.0)})",
            ]
        )
        if isinstance(label_counts, dict) and label_counts:
            rendered_labels = ", ".join(
                f"{label}={count}" for label, count in sorted(label_counts.items())
            )
            lines.append(f"- labels: {rendered_labels}")
        if isinstance(confidence_counts, dict) and confidence_counts:
            rendered_confidences = ", ".join(
                f"{label}={count}" for label, count in sorted(confidence_counts.items())
            )
            lines.append(f"- confidence: {rendered_confidences}")
        lines.append("")
    unknown_breakdown = summary.get("case_primary_unknown_breakdown")
    if isinstance(unknown_breakdown, dict) and unknown_breakdown.get("total_cases", 0):
        lines.extend(
            [
                "## Primary Unknown Breakdown",
                "",
                f"- unknown cases: {unknown_breakdown.get('total_cases', 0)}",
                f"- analysis JSON cases: {unknown_breakdown.get('analysis_json_cases', 0)}",
            ]
        )
        for label, key in (
            ("metadata", "metadata_status_counts"),
            ("duration buckets", "duration_bucket_counts"),
            ("score severity", "score_severity_counts"),
            ("query optimization tiers", "query_optimization_tier_counts"),
            ("top findings", "finding_id_counts"),
            ("top time operators", "top_time_operator_counts"),
            ("evidence quality", "evidence_quality_level_counts"),
            ("evidence limitations", "evidence_limitation_counts"),
            ("runtime diagnosis", "runtime_diagnosis_summary_counts"),
            ("stats primary", "stats_primary_bottleneck_counts"),
            ("stats context", "stats_context_counts"),
        ):
            counts = unknown_breakdown.get(key)
            if isinstance(counts, dict) and counts:
                rendered_counts = ", ".join(f"{item}={count}" for item, count in counts.items())
                lines.append(f"- {label}: {rendered_counts}")
        lines.append("")
    rewriteability_distribution = summary.get("optimizer_rewriteability_distribution")
    if isinstance(rewriteability_distribution, dict):
        bucket_counts = rewriteability_distribution.get("bucket_counts")
        lines.extend(
            [
                "## Optimizer Rewriteability Distribution",
                "",
                f"- optimization candidate cases: {rewriteability_distribution.get('optimization_candidate_cases', 0)} / {rewriteability_distribution.get('total_cases', 0)}",
                f"- safe material draft cases: {rewriteability_distribution.get('safe_material_draft_cases', 0)} ({rewriteability_distribution.get('safe_material_draft_rate', 0.0)})",
                f"- recipe backlog cases: {rewriteability_distribution.get('recipe_detected_no_draft_cases', 0)} no-draft, {rewriteability_distribution.get('recipe_adjacent_shape_cases', 0)} adjacent ({rewriteability_distribution.get('recipe_backlog_rate', 0.0)})",
                f"- actionable recipe backlog cases: {rewriteability_distribution.get('recipe_backlog_actionable_cases', 0)} ({rewriteability_distribution.get('recipe_backlog_actionable_rate', 0.0)})",
                f"- stats-likely cases: {rewriteability_distribution.get('stats_likely_cases', 0)} ({rewriteability_distribution.get('stats_likely_rate', 0.0)})",
                f"- human-review-only cases: {rewriteability_distribution.get('human_review_only_cases', 0)} ({rewriteability_distribution.get('human_review_only_rate', 0.0)})",
            ]
        )
        if isinstance(bucket_counts, dict) and bucket_counts:
            rendered_buckets = ", ".join(
                f"{bucket}={count}" for bucket, count in sorted(bucket_counts.items())
            )
            lines.append(f"- buckets: {rendered_buckets}")
        human_review_reasons = rewriteability_distribution.get(
            "human_review_only_risk_reason_counts"
        )
        if isinstance(human_review_reasons, dict) and human_review_reasons:
            rendered_reasons = ", ".join(
                f"{reason}={count}" for reason, count in sorted(human_review_reasons.items())
            )
            lines.append(f"- human-review guardrails: {rendered_reasons}")
        human_review_eligibility = rewriteability_distribution.get(
            "human_review_only_draft_eligibility_counts"
        )
        if isinstance(human_review_eligibility, dict) and human_review_eligibility:
            rendered_eligibility = ", ".join(
                f"{label}={count}" for label, count in sorted(human_review_eligibility.items())
            )
            lines.append(f"- human-review draft eligibility: {rendered_eligibility}")
        no_draft_recipes = rewriteability_distribution.get("recipe_detected_no_draft_recipe_counts")
        if isinstance(no_draft_recipes, dict) and no_draft_recipes:
            rendered_recipes = ", ".join(
                f"{recipe}={count}" for recipe, count in sorted(no_draft_recipes.items())
            )
            lines.append(f"- no-draft recipes: {rendered_recipes}")
        no_draft_eligibility = rewriteability_distribution.get(
            "recipe_detected_no_draft_eligibility_counts"
        )
        if isinstance(no_draft_eligibility, dict) and no_draft_eligibility:
            rendered_eligibility = ", ".join(
                f"{label}={count}" for label, count in sorted(no_draft_eligibility.items())
            )
            lines.append(f"- no-draft eligibility: {rendered_eligibility}")
        no_draft_reasons = rewriteability_distribution.get("recipe_detected_no_draft_reason_counts")
        no_draft_classes = rewriteability_distribution.get("recipe_detected_no_draft_class_counts")
        no_draft_actionability = rewriteability_distribution.get(
            "recipe_detected_no_draft_actionability_counts"
        )
        if isinstance(no_draft_actionability, dict) and no_draft_actionability:
            rendered_actionability = ", ".join(
                f"{label}={count}" for label, count in sorted(no_draft_actionability.items())
            )
            lines.append(f"- no-draft actionability: {rendered_actionability}")
        if isinstance(no_draft_classes, dict) and no_draft_classes:
            rendered_classes = ", ".join(
                f"{label}={count}" for label, count in sorted(no_draft_classes.items())
            )
            lines.append(f"- no-draft classes: {rendered_classes}")
        no_draft_class_recipes = rewriteability_distribution.get(
            "recipe_detected_no_draft_class_recipe_counts"
        )
        if isinstance(no_draft_class_recipes, dict) and no_draft_class_recipes:
            rendered_class_recipes = "; ".join(
                f"{label}: "
                + ", ".join(f"{recipe}={count}" for recipe, count in sorted(recipe_counts.items()))
                for label, recipe_counts in sorted(no_draft_class_recipes.items())
                if isinstance(recipe_counts, dict) and recipe_counts
            )
            if rendered_class_recipes:
                lines.append(f"- no-draft classes by recipe: {rendered_class_recipes}")
        no_draft_class_recipe_reasons = rewriteability_distribution.get(
            "recipe_detected_no_draft_class_recipe_reason_counts"
        )
        if isinstance(no_draft_class_recipe_reasons, dict) and no_draft_class_recipe_reasons:
            rendered_class_recipe_reasons = "; ".join(
                f"{label}/{recipe}: "
                + ", ".join(f"{reason}={count}" for reason, count in sorted(reason_counts.items()))
                for label, recipe_counts in sorted(no_draft_class_recipe_reasons.items())
                if isinstance(recipe_counts, dict)
                for recipe, reason_counts in sorted(recipe_counts.items())
                if isinstance(reason_counts, dict) and reason_counts
            )
            if rendered_class_recipe_reasons:
                lines.append(f"- no-draft class/recipe reasons: {rendered_class_recipe_reasons}")
        if isinstance(no_draft_reasons, dict) and no_draft_reasons:
            rendered_reasons = ", ".join(
                f"{reason}={count}" for reason, count in sorted(no_draft_reasons.items())
            )
            lines.append(f"- no-draft reasons: {rendered_reasons}")
        no_recipe_review_tracks = rewriteability_distribution.get("no_recipe_review_track_counts")
        if isinstance(no_recipe_review_tracks, dict) and no_recipe_review_tracks:
            rendered_tracks = ", ".join(
                f"{track}={count}" for track, count in sorted(no_recipe_review_tracks.items())
            )
            lines.append(f"- no-recipe review tracks: {rendered_tracks}")
        no_draft_decisions = rewriteability_distribution.get(
            "recipe_detected_no_draft_cte_pushdown_decision_counts"
        )
        if isinstance(no_draft_decisions, dict) and no_draft_decisions:
            rendered_decisions = ", ".join(
                f"{reason}={count}" for reason, count in sorted(no_draft_decisions.items())
            )
            lines.append(f"- no-draft CTE predicate decisions: {rendered_decisions}")
        adjacent_actionability = rewriteability_distribution.get(
            "recipe_adjacent_actionability_counts"
        )
        if isinstance(adjacent_actionability, dict) and adjacent_actionability:
            rendered_actionability = ", ".join(
                f"{label}={count}" for label, count in sorted(adjacent_actionability.items())
            )
            lines.append(f"- adjacent actionability: {rendered_actionability}")
        adjacent_cte_graphs = rewriteability_distribution.get("recipe_adjacent_cte_graph_counts")
        if isinstance(adjacent_cte_graphs, dict) and adjacent_cte_graphs:
            rendered_graphs = ", ".join(
                f"{label}={count}" for label, count in sorted(adjacent_cte_graphs.items())
            )
            lines.append(f"- adjacent CTE graphs: {rendered_graphs}")
        adjacent_cte_statuses = rewriteability_distribution.get(
            "recipe_adjacent_cte_predicate_pushdown_counts"
        )
        if isinstance(adjacent_cte_statuses, dict) and adjacent_cte_statuses:
            rendered_statuses = ", ".join(
                f"{label}={count}" for label, count in sorted(adjacent_cte_statuses.items())
            )
            lines.append(f"- adjacent CTE predicate status: {rendered_statuses}")
        adjacent_cte_boundaries = rewriteability_distribution.get(
            "recipe_adjacent_cte_boundary_reason_counts"
        )
        if isinstance(adjacent_cte_boundaries, dict) and adjacent_cte_boundaries:
            rendered_boundaries = ", ".join(
                f"{reason}={count}" for reason, count in sorted(adjacent_cte_boundaries.items())
            )
            lines.append(f"- adjacent CTE boundary reasons: {rendered_boundaries}")
        adjacent_derived_statuses = rewriteability_distribution.get(
            "recipe_adjacent_derived_predicate_pushdown_counts"
        )
        if isinstance(adjacent_derived_statuses, dict) and adjacent_derived_statuses:
            rendered_statuses = ", ".join(
                f"{label}={count}" for label, count in sorted(adjacent_derived_statuses.items())
            )
            lines.append(f"- adjacent derived predicate status: {rendered_statuses}")
        adjacent_derived_boundaries = rewriteability_distribution.get(
            "recipe_adjacent_derived_boundary_reason_counts"
        )
        if isinstance(adjacent_derived_boundaries, dict) and adjacent_derived_boundaries:
            rendered_boundaries = ", ".join(
                f"{reason}={count}" for reason, count in sorted(adjacent_derived_boundaries.items())
            )
            lines.append(f"- adjacent derived boundary reasons: {rendered_boundaries}")
        lines.append("")
    optimizer_funnel_summary = summary.get("optimizer_funnel")
    if isinstance(optimizer_funnel_summary, dict):
        lines.extend(
            [
                "## Optimizer Funnel",
                "",
                f"- optimization candidate cases: {optimizer_funnel_summary.get('optimization_candidate_cases', 0)} / {optimizer_funnel_summary.get('total_cases', 0)}",
                f"- recipe detected cases: {optimizer_funnel_summary.get('recipe_detected_cases', 0)} ({optimizer_funnel_summary.get('recipe_detected_rate', 0.0)})",
                f"- draft-ready cases: {optimizer_funnel_summary.get('draft_ready_cases', 0)} ({optimizer_funnel_summary.get('draft_ready_rate', 0.0)})",
                f"- trusted SQL draft produced cases: {optimizer_funnel_summary.get('trusted_sql_draft_produced_cases', 0)} ({optimizer_funnel_summary.get('trusted_sql_draft_produced_rate', 0.0)})",
                f"- recipe backlog cases: {optimizer_funnel_summary.get('recipe_detected_no_draft_cases', 0)} no-draft, {optimizer_funnel_summary.get('recipe_adjacent_shape_cases', 0)} adjacent ({optimizer_funnel_summary.get('recipe_backlog_rate', 0.0)})",
                f"- actionable recipe backlog cases: {optimizer_funnel_summary.get('recipe_backlog_actionable_cases', 0)} ({optimizer_funnel_summary.get('recipe_backlog_actionable_rate', 0.0)})",
                f"- human review only cases: {optimizer_funnel_summary.get('human_review_only_cases', 0)}",
                f"- stats-likely cases: {optimizer_funnel_summary.get('stats_likely_cases', 0)}",
                f"- not rewriteable cases: {optimizer_funnel_summary.get('not_rewriteable_cases', 0)}",
                f"- unknown cases: {optimizer_funnel_summary.get('unknown_cases', 0)}",
                f"- note: {optimizer_funnel_summary.get('trusted_sql_draft_produced_note', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "| case | query id | duration sec | collection | analysis | metadata | score | facts | report | timings sec |",
            "| --- | --- | ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for case in summary["cases"]:
        assert isinstance(case, dict)
        timings = (
            f"cm={case['cm_collect_seconds']}, "
            f"analysis={case['analysis_seconds']}, "
            f"report={case['report_seconds']}, "
            f"total={case['total_seconds']}"
        )
        facts = (
            f"card={case['cardinality_anomaly_count']}, "
            f"mem={case['memory_anomaly_count']}, "
            f"skew={case['backend_data_skew']}, "
            f"tail={case['host_tail_candidate_count']}, "
            f"refs={case['referenced_table_count']}, "
            f"metadata_collectable={case['collectable_metadata_table_count']}, "
            f"metadata_collected={case['collected_metadata_table_count']}"
        )
        lines.append(
            (
                "| {case_index} | {query_id} | {duration_sec} | {collection_status} | "
                "{analysis_status} | {metadata_status} | {score} | "
                f"{facts} | "
                "{report_validation_status} | "
                f"{timings} |"
            ).format(**case)
        )
    (out / "batch_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
