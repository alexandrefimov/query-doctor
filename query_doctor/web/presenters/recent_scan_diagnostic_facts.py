"""Question-oriented diagnostic facts for Recent scan Details pages."""

from __future__ import annotations

from typing import Any

from query_doctor.web.presenters.recent_scan_case_summary import (
    confidence_summary,
    primary_bottleneck_summary,
    review_anchor_summary,
)
from query_doctor.web.presenters.recent_scan_models import (
    RecentScanCaseDetailView,
    RecentScanDiagnosticFactView,
)
from query_doctor.web.presenters.recent_scan_detail_values import (
    candidate_is_visible,
    candidate_detail_value,
    table_stats_status_detail_label,
    table_stats_status_is_visible,
    workload_baseline_detail_value,
    workload_group_detail_value,
)

VERDICT_KPI_FACT_IDS = ("duration", "confidence")
VERDICT_CHIP_FACT_IDS = (
    "query_window",
    "resource_footprint",
    "workload_baseline",
    "workload_group",
    "admission_wait",
    "cluster_context",
    "spill",
    "table_stats",
    "pool",
    "query_type",
    "user",
)
DEFAULT_VERDICT_CHIP_LIMIT = 6


def present_recent_scan_diagnostic_facts(
    view: RecentScanCaseDetailView,
) -> tuple[RecentScanDiagnosticFactView, ...]:
    facts: list[RecentScanDiagnosticFactView] = [
        RecentScanDiagnosticFactView(
            "priority",
            "How urgent is it?",
            "priority",
            diagnostic_priority_value(view),
            severity=diagnostic_priority_severity(view),
            relevance=100,
            interpretation="Deterministic score severity for triage.",
            source_anchor="score-evidence",
        ),
        RecentScanDiagnosticFactView(
            "duration",
            "How long did it run?",
            "duration",
            diagnostic_duration_value(view),
            severity=duration_fact_severity(view),
            relevance=96,
            interpretation="Elapsed query duration with baseline context when available.",
            source_anchor="runtime-evidence",
        ),
        RecentScanDiagnosticFactView(
            "main_signal",
            "What looks wrong?",
            "main signal",
            primary_bottleneck_summary(view),
            severity=view.score_severity or "neutral",
            relevance=94,
            interpretation="Highest-priority supported signal from analyzer facts.",
            source_anchor="action-plan",
        ),
        RecentScanDiagnosticFactView(
            "confidence",
            "How strong is the evidence?",
            "confidence",
            confidence_summary(view),
            severity="neutral",
            relevance=88,
            interpretation="Evidence quality and stats confidence for this case.",
            source_anchor="score-evidence",
        ),
    ]
    facts.extend(query_context_facts(view))
    facts.extend(workload_facts(view))
    facts.extend(action_route_facts(view))
    facts.extend(supporting_signal_facts(view))
    return tuple(sorted(facts, key=lambda fact: (-fact.relevance, fact.fact_id)))


def diagnostic_priority_value(view: RecentScanCaseDetailView) -> str:
    candidate_label = clean_case_candidate_follow_up_label(view)
    if candidate_label:
        return f"{candidate_label} · score {score_display_value(view.score)}"
    return f"{diagnostic_severity_label(view.score_severity)} · {score_display_value(view.score)}"


def diagnostic_priority_severity(view: RecentScanCaseDetailView) -> str:
    if clean_case_candidate_follow_up_label(view):
        return "warning"
    return view.score_severity or "neutral"


def clean_case_candidate_follow_up_label(view: RecentScanCaseDetailView) -> str:
    if str(view.score_severity or "").strip().lower() != "clean":
        return ""
    optimization_visible = candidate_is_visible(view.optimization_candidate)
    stats_visible = candidate_is_visible(view.stats_candidate)
    if optimization_visible and stats_visible:
        return "Multiple follow-ups"
    if optimization_visible:
        return "Query-shape follow-up"
    if stats_visible:
        return "Stats follow-up"
    return ""


def diagnostic_severity_label(severity: Any) -> str:
    normalized = str(severity or "").strip().lower()
    if normalized == "failed":
        return "Failed"
    if normalized == "high":
        return "High priority"
    if normalized == "suspicious":
        return "Medium priority"
    if normalized == "clean":
        return "Clean"
    return "Unknown priority"


def score_display_value(score: Any) -> str:
    text = "" if score is None else str(score).strip()
    if text.lower() in {"", "unknown", "none"}:
        return "unknown"
    return text


def diagnostic_duration_value(view: RecentScanCaseDetailView) -> str:
    duration = str(view.duration_sec or "").strip()
    duration_text = (
        f"{duration}s" if duration and duration.lower() not in {"unknown", "none"} else "unknown"
    )
    if view.workload_baseline_sample_count > 0:
        baseline = str(view.workload_baseline_duration_sec_p95 or "").strip()
        baseline_text = (
            f"baseline p95 {baseline}s"
            if baseline and baseline.lower() not in {"unknown", "none"}
            else "baseline p95 unknown"
        )
        return (
            f"{duration_text}; {baseline_text}; "
            f"{view.workload_regression}, n={view.workload_baseline_sample_count}"
        )
    if view.workload_group_member_count > 1:
        p95 = str(view.workload_group_duration_sec_p95 or "").strip()
        if p95 and p95.lower() not in {"unknown", "none"}:
            return (
                f"{duration_text}; scan p95 {p95}s across "
                f"{view.workload_group_member_count} similar"
            )
    return duration_text


def duration_fact_severity(view: RecentScanCaseDetailView) -> str:
    if view.workload_regression == "regressed":
        return "warning"
    return view.score_severity or "neutral"


def query_context_facts(view: RecentScanCaseDetailView) -> tuple[RecentScanDiagnosticFactView, ...]:
    if view.query_context.unavailable:
        return ()
    items = dict(view.query_context.summary_items)
    facts: list[RecentScanDiagnosticFactView] = []
    add_fact(
        facts,
        fact_id="query_window",
        question="When did it run?",
        label="query window",
        value=query_context_window_value(items),
        relevance=90,
        source_anchor="runtime-evidence",
        interpretation="Safe query start/end window from collected context.",
    )
    add_fact(
        facts,
        fact_id="admission_wait",
        question="Did it wait before running?",
        label="admission wait",
        value=meaningful_query_context_value(items.get("admission wait")),
        severity="warning",
        relevance=86,
        source_anchor="runtime-evidence",
        interpretation="Admission wait helps separate queueing from execution time.",
    )
    add_fact(
        facts,
        fact_id="resource_footprint",
        question="How much work did it do?",
        label="resource footprint",
        value=query_context_resource_value(items),
        relevance=84,
        source_anchor="runtime-evidence",
        interpretation="Safe aggregate resource footprint from query context.",
    )
    add_fact(
        facts,
        fact_id="pool",
        question="Where did it run?",
        label="pool",
        value=meaningful_query_context_value(items.get("pool")),
        relevance=58,
        source_anchor="runtime-evidence",
        interpretation="Resource pool that admitted the query.",
    )
    add_fact(
        facts,
        fact_id="query_type",
        question="What kind of query is it?",
        label="query type",
        value=meaningful_query_context_value(items.get("query type")),
        relevance=48,
        source_anchor="runtime-evidence",
        interpretation="Safe statement type from collected query context.",
    )
    return tuple(facts)


def query_context_window_value(items: dict[str, Any]) -> str:
    start = meaningful_query_context_value(items.get("start time"))
    end = meaningful_query_context_value(items.get("end time"))
    if start and end:
        return f"{start} to {end}"
    if start:
        return start
    return end


def query_context_resource_value(items: dict[str, Any]) -> str:
    parts: list[str] = []
    bytes_read = meaningful_query_context_value(items.get("bytes read"))
    peak_memory = meaningful_query_context_value(items.get("peak memory"))
    rows = meaningful_query_context_value(items.get("rows produced"))
    if bytes_read:
        parts.append(f"read {bytes_read}")
    if peak_memory:
        parts.append(f"peak memory {peak_memory}")
    if rows:
        parts.append(f"rows {rows}")
    return "; ".join(parts[:2])


def meaningful_query_context_value(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "unknown", "none", "not_reported"}:
        return ""
    return text


def workload_facts(view: RecentScanCaseDetailView) -> tuple[RecentScanDiagnosticFactView, ...]:
    facts: list[RecentScanDiagnosticFactView] = []
    if view.workload_baseline_sample_count > 0:
        add_fact(
            facts,
            fact_id="workload_baseline",
            question="Is this normal for similar work?",
            label="workload baseline",
            value=workload_baseline_detail_value(view),
            severity=duration_fact_severity(view),
            relevance=82,
            source_anchor="runtime-evidence",
            interpretation="Comparison against previous matching workload batches.",
        )
    if view.workload_group_member_count > 1:
        add_fact(
            facts,
            fact_id="workload_group",
            question="Is this normal in this scan?",
            label="workload group",
            value=workload_group_detail_value(view),
            relevance=66,
            source_anchor="runtime-evidence",
            interpretation="Comparison against similar queries in the current scan.",
        )
    return tuple(facts)


def action_route_facts(view: RecentScanCaseDetailView) -> tuple[RecentScanDiagnosticFactView, ...]:
    facts: list[RecentScanDiagnosticFactView] = []
    review_anchor = review_anchor_summary(view)
    if review_anchor != "No prioritized structural anchor from deterministic facts":
        add_fact(
            facts,
            fact_id="review_anchor",
            question="Where should I inspect?",
            label="review anchor",
            value=review_anchor,
            relevance=80,
            source_anchor="action-plan",
            interpretation="Safe structural location from deterministic facts.",
        )
    if candidate_is_visible(view.optimization_candidate):
        add_fact(
            facts,
            fact_id="rewrite_candidate",
            question="What action is likely?",
            label="rewrite",
            value=candidate_detail_value(view.optimization_candidate, view.optimization_rank),
            severity="warning",
            relevance=74,
            source_anchor="action-plan",
            interpretation="Query-shape review candidate selected by deterministic scoring.",
        )
    if candidate_is_visible(view.stats_candidate):
        add_fact(
            facts,
            fact_id="stats_candidate",
            question="What action is likely?",
            label="stats",
            value=candidate_detail_value(view.stats_candidate, view.stats_rank),
            severity="warning",
            relevance=72,
            source_anchor="action-plan",
            interpretation="Stats refresh review candidate selected by deterministic scoring.",
        )
    return tuple(facts)


def supporting_signal_facts(
    view: RecentScanCaseDetailView,
) -> tuple[RecentScanDiagnosticFactView, ...]:
    facts: list[RecentScanDiagnosticFactView] = []
    if not view.cluster_runtime_context.unavailable:
        add_fact(
            facts,
            fact_id="cluster_context",
            question="Was the cluster involved?",
            label="cluster context",
            value=view.runtime_verdict.title,
            severity=runtime_fact_severity(view),
            relevance=78,
            source_anchor="runtime-evidence",
            interpretation="Cluster runtime context is supporting evidence, not standalone proof.",
        )
    if view.signal_summary != "no positive analyzer signals":
        add_fact(
            facts,
            fact_id="signals",
            question="What signals were observed?",
            label="signals",
            value=view.signal_summary,
            severity=view.score_severity or "neutral",
            relevance=70,
            source_anchor="score-evidence",
            interpretation="Positive deterministic analyzer signals for this case.",
        )
    if view.has_spill:
        add_fact(
            facts,
            fact_id="spill",
            question="Did it spill?",
            label="spill",
            value="observed",
            severity="warning",
            relevance=68,
            source_anchor="runtime-evidence",
            interpretation="Spill evidence was parsed from supported facts.",
        )
    if table_stats_status_is_visible(view.table_stats_status):
        add_fact(
            facts,
            fact_id="table_stats",
            question="Are table stats available?",
            label="table stats",
            value=f"table stats {table_stats_status_detail_label(view.table_stats_status)}",
            relevance=62,
            source_anchor="metadata-evidence",
            interpretation="Table statistics status from collected metadata facts.",
        )
    add_fact(
        facts,
        fact_id="user",
        question="Who ran it?",
        label="user",
        value=view.user,
        relevance=40,
        source_anchor="runtime-evidence",
        interpretation="Safe query owner label.",
    )
    return tuple(facts)


def runtime_fact_severity(view: RecentScanCaseDetailView) -> str:
    badge_class = str(view.runtime_verdict.badge_class or "").lower()
    if "warning" in badge_class:
        return "warning"
    if "danger" in badge_class or "failed" in badge_class:
        return "failed"
    return "neutral"


def add_fact(
    facts: list[RecentScanDiagnosticFactView],
    *,
    fact_id: str,
    question: str,
    label: str,
    value: Any,
    relevance: int,
    source_anchor: str,
    interpretation: str,
    severity: str = "neutral",
    unit: str = "",
    value_kind: str = "text",
) -> None:
    if not is_meaningful_fact_value(value):
        return
    facts.append(
        RecentScanDiagnosticFactView(
            fact_id,
            question,
            label,
            value,
            unit=unit,
            severity=severity,
            relevance=relevance,
            interpretation=interpretation,
            source_anchor=source_anchor,
            value_kind=value_kind,
        )
    )


def is_meaningful_fact_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text not in {"", "unknown", "none", "not_reported", "not collected"}


def diagnostic_fact_by_id(
    facts: tuple[RecentScanDiagnosticFactView, ...],
    fact_id: str,
) -> RecentScanDiagnosticFactView | None:
    for fact in facts:
        if fact.fact_id == fact_id:
            return fact
    return None


def verdict_kpi_facts(
    facts: tuple[RecentScanDiagnosticFactView, ...],
) -> tuple[RecentScanDiagnosticFactView, ...]:
    selected = [
        fact
        for fact_id in VERDICT_KPI_FACT_IDS
        if (fact := diagnostic_fact_by_id(facts, fact_id)) is not None
    ]
    return tuple(selected)


def verdict_chip_facts(
    facts: tuple[RecentScanDiagnosticFactView, ...],
    *,
    limit: int = DEFAULT_VERDICT_CHIP_LIMIT,
) -> tuple[RecentScanDiagnosticFactView, ...]:
    chips = [
        fact
        for fact_id in VERDICT_CHIP_FACT_IDS
        if (fact := diagnostic_fact_by_id(facts, fact_id)) is not None
    ]
    return tuple(chips[:limit])
