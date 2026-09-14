"""Recent scan summary, scope, and signal presentation helpers."""

from __future__ import annotations

from typing import Any

from query_doctor.web.presenters.recent_scan_values import (
    numeric_count,
    numeric_value,
    safe_display_text,
    safe_truthy,
)


CANDIDATE_REASON_LABELS = {
    "selected: SELECT-like user query": "SELECT/WITH query",
    "selected: INSERT query": "INSERT query",
    "selected: DELETE query": "DELETE query",
    "selected: UPSERT query": "UPSERT query",
    "selected: CREATE TABLE AS SELECT query": "CREATE TABLE AS SELECT query",
    "selected: query type indicates user query; SQL verb unknown": "QUERY with unknown SQL verb",
    "eligible but not selected because recent-select limit was reached": "analysis cap reached",
    "excluded: user filter mismatch": "user filter mismatch",
    "excluded: pool filter mismatch": "pool filter mismatch",
    "excluded: query type filter mismatch": "query type mismatch",
    "excluded: running query": "running query",
    "excluded: failed query": "failed query",
    "excluded: cancelled query": "cancelled query",
    "excluded: Query Doctor collector smoke statement": "Query Doctor smoke statement",
    "excluded: admin or metadata statement": "admin or metadata statement",
    "excluded: not SELECT-like query text": "not SELECT/WITH query text",
    "excluded: not analyzable query text": "not analyzable query text",
    "excluded: query type is not user QUERY/SELECT": "not user QUERY/SELECT type",
    "excluded: unknown statement type": "unknown statement type",
    "excluded: duration unknown": "duration unknown",
    "excluded: duration below recent-min-duration-sec": "below minimum duration",
    "excluded: duration above recent-max-duration-sec": "above maximum duration",
}


def recent_scan_scope_parts(summary: dict[str, Any]) -> tuple[str, ...]:
    parts: list[str] = []
    summaries = summary.get("summaries_inspected")
    if summary.get("cm_summary_safety_cap_hit"):
        cap = recent_scan_limit_text(summary) or safe_display_text(summaries)
        parts.append(f"Partial CM scan: cap {cap}")
    elif summaries is not None:
        parts.append(f"Query summaries inspected: {safe_display_text(summaries)}")
    if summary.get("from_time") or summary.get("to_time"):
        parts.append(
            "Scan time window: "
            f"{safe_display_text(summary.get('from_time') or 'unknown')} -> "
            f"{safe_display_text(summary.get('to_time') or 'unknown')}"
        )
    elif summary.get("recent_window_minutes") is not None:
        parts.append(
            f"Search depth: {safe_display_text(summary.get('recent_window_minutes'))} minutes"
        )
    parts.append(f"Duration filter: {safe_display_text(summary.get('duration_filter') or 'none')}")
    duration_filter_mode = str(summary.get("duration_filter_mode") or "").strip().lower()
    if duration_filter_mode and duration_filter_mode != "none":
        parts.append(
            f"Duration filtering: {safe_display_text(summary.get('duration_filter_mode'))}"
        )
    if summary.get("triage_profile_limit") is not None:
        parts.append(f"Analyzer limit: {safe_display_text(summary.get('triage_profile_limit'))}")
    if summary.get("metadata_top_limit") is not None:
        metadata_scope = (
            "analyzed cases"
            if str(summary.get("query_profile_source") or "").strip().lower() == "impala"
            else "bad/suspicious cases"
        )
        parts.append(
            f"Metadata budget: up to {safe_display_text(summary.get('metadata_top_limit'))} {metadata_scope}"
        )
    parts.extend(cluster_context_scope_parts(summary))
    if summary.get("query_type_filter") is not None:
        parts.append(f"Query type: {query_type_filter_label(summary.get('query_type_filter'))}")
    if summary.get("only_running"):
        parts.append("Status: running only")
    if summary.get("user_filter_present"):
        parts.append("User filter: set")
    else:
        parts.append("User filter: all users")
    if summary.get("pool_filter_present"):
        parts.append("Pool filter: set")
    else:
        parts.append("Pool filter: all pools")
    if summary.get("cm_jobs") is not None or summary.get("jobs") is not None:
        parts.append(
            "Parallelism: "
            f"CM {safe_display_text(summary.get('cm_jobs') or 'n/a')}, "
            f"analyzer {safe_display_text(summary.get('jobs') or 'n/a')}, "
            f"metadata {safe_display_text(summary.get('metadata_jobs') or 'n/a')}"
        )
    parts.extend(candidate_selection_scope_parts(summary))
    return tuple(parts)


def cluster_context_scope_parts(summary: dict[str, Any]) -> list[str]:
    if not summary.get("collect_cm_events"):
        return ["Cluster event context: not requested"]
    context = summary.get("cluster_context")
    if not isinstance(context, dict):
        return ["Cluster event context: unavailable"]
    status = safe_display_text(context.get("status") or "inconclusive")
    signal_counts = context.get("signal_counts")
    signal_total = 0
    if isinstance(signal_counts, dict):
        for value in signal_counts.values():
            count = numeric_count(value)
            if count:
                signal_total += count
    if signal_total:
        return [f"Cluster event context: {status}, signals {safe_display_text(signal_total)}"]
    return [f"Cluster event context: {status}"]


def candidate_selection_scope_parts(summary: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    if "selected_count" in summary:
        selected = numeric_count(summary.get("selected_count"))
        parts.append(f"Analyzed queries: {safe_display_text(selected)}")
    if "candidate_exclusion_count" in summary:
        excluded = numeric_count(summary.get("candidate_exclusion_count"))
        parts.append(f"Excluded before analysis: {safe_display_text(excluded)}")

    reason_counts = summary.get("candidate_reason_counts")
    if not isinstance(reason_counts, dict):
        return parts
    exclusion_reasons: list[tuple[str, int, str]] = []
    for reason, count in reason_counts.items():
        reason_text = str(reason)
        if reason_text.startswith("selected:"):
            continue
        parsed_count = numeric_count(count)
        if parsed_count is None or parsed_count <= 0:
            continue
        exclusion_reasons.append((reason_text, parsed_count, candidate_reason_label(reason_text)))
    if exclusion_reasons:
        top = "; ".join(
            f"{label}: {count}{candidate_reason_sql_verb_detail(summary, reason)}"
            for reason, count, label in exclusion_reasons[:3]
        )
        parts.append(f"Top exclusions: {safe_display_text(top)}")
    return parts


def candidate_reason_sql_verb_detail(summary: dict[str, Any], reason: str) -> str:
    breakdowns = summary.get("candidate_reason_sql_verb_counts")
    if not isinstance(breakdowns, dict):
        return ""
    counts = breakdowns.get(reason)
    if not isinstance(counts, dict):
        return ""
    items: list[tuple[str, int]] = []
    for verb, count in counts.items():
        parsed = numeric_count(count)
        if parsed is None or parsed <= 0:
            continue
        label = "unknown/no SQL verb" if str(verb).lower() == "unknown" else str(verb)
        items.append((label, parsed))
    items.sort(key=lambda item: (-item[1], item[0]))
    if not items:
        return ""
    visible = items[:3]
    hidden_total = sum(count for _label, count in items[3:])
    parts = [f"{label} {count}" for label, count in visible]
    if hidden_total:
        parts.append(f"other {hidden_total}")
    top = ", ".join(parts)
    return f" ({top})"


def query_type_filter_label(value: Any) -> str:
    text = safe_display_text(value).strip()
    if not text or text.lower() == "all":
        return "all supported"
    return text


def candidate_reason_label(reason: str) -> str:
    return CANDIDATE_REASON_LABELS.get(reason, safe_display_text(reason))


def case_has_spill(case: dict[str, Any]) -> bool:
    reasons = case.get("score_reasons")
    if not isinstance(reasons, list):
        return False
    return any(
        "spill/scratch evidence: non-zero metrics" in str(reason).lower() for reason in reasons
    )


def recent_scan_empty_message(summary: dict[str, Any], *, case_count: int) -> str | None:
    if summary.get("scan_too_broad"):
        return (
            "The CM query summary scan cap was reached before discovery completed. Results are "
            "a partial bounded analysis of the summaries collected so far; use a smaller Search "
            "depth or add user, pool, or query type filters for complete coverage."
        )
    if summary.get("discovery_failed"):
        return "Recent scan discovery failed before case selection. Check CM connectivity and access settings, then run again."
    selected = numeric_count(summary.get("selected_count"))
    if selected or case_count:
        return None
    summaries = summary.get("summaries_inspected")
    if str(summary.get("mode") or "").strip().lower() == "recent-history-online":
        if str(summary.get("history_view") or "").strip().lower() == "details_ready":
            return (
                "No Details-ready analyses yet. Check All recent for queued, running, "
                "failed, or unselected summaries."
            )
        return (
            "No retained query summaries yet. Run the Recent summary collector or use New scan "
            "to populate Online History."
        )
    if summaries is not None and numeric_count(summaries) == 0:
        source_failure = recent_scan_source_failure_message(summary)
        if source_failure:
            return source_failure
        return (
            "No matching queries found for this hour bucket. Try another hour or changing filters."
        )
    if summaries is not None:
        return "No query candidates matched the current scan criteria. Try another hour or changing filters."
    return None


def recent_scan_limit_text(summary: dict[str, Any]) -> str:
    if summary.get("cm_summary_raw_scan_cap_hit"):
        raw_cap = safe_display_text(summary.get("cm_summary_raw_scan_cap"))
        if raw_cap and raw_cap.lower() not in {"none", "unknown"}:
            return raw_cap
    for key in ("cm_summary_safety_cap", "cm_inspect_limit", "summaries_inspected"):
        value = safe_display_text(summary.get(key))
        if value and value.lower() not in {"none", "unknown"}:
            return value
    return ""


def recent_scan_source_failure_message(summary: dict[str, Any]) -> str:
    warnings = summary.get("warnings")
    if not isinstance(warnings, list):
        return ""
    for warning in warnings:
        text = str(warning or "").strip().lower()
        if "cm request failed" in text or "timed out" in text or "timeout" in text:
            return (
                "Recent scan could not read CM query summaries from the selected source. "
                "This does not prove that no queries exist; check CM connectivity/access or "
                "reduce Search depth and run again."
            )
    return ""


def recent_scan_warning_messages(summary: dict[str, Any]) -> tuple[str, ...]:
    warnings = summary.get("warnings")
    if not isinstance(warnings, list):
        return ()
    return tuple(safe_display_text(warning) for warning in warnings[:3] if warning is not None)


def recent_scan_status_summary(
    collection_status: Any,
    analysis_status: Any,
    metadata_status: Any,
    report_status: str,
) -> str:
    return (
        f"collection {safe_display_text(collection_status)}; "
        f"analysis {safe_display_text(analysis_status)}; "
        f"metadata {safe_display_text(metadata_status)}; "
        f"report {safe_display_text(report_status)}"
    )


def recent_scan_signal_summary(case: dict[str, Any]) -> str:
    signals: list[str] = []
    cardinality = numeric_count(case.get("cardinality_anomaly_count"))
    memory = numeric_count(case.get("memory_anomaly_count"))
    tail = numeric_count(case.get("host_tail_candidate_count"))
    if cardinality > 0:
        signals.append(f"cardinality {safe_display_text(cardinality)}")
    if memory > 0:
        signals.append(f"memory {safe_display_text(memory)}")
    if safe_truthy(case.get("backend_data_skew")):
        signals.append("skew observed")
    if tail > 0:
        signals.append(f"host-tail {safe_display_text(tail)}")
    if signals:
        return "; ".join(signals)
    profile_failure_category = safe_display_text(case.get("failure_category"))
    if (
        profile_failure_category
        and case.get("collection_status") == "summary_history"
        and case.get("analysis_status") in {"failed", "profile_retry_pending"}
    ):
        return f"profile processing failure {profile_failure_category}"
    if numeric_value(case.get("score")) <= 0:
        return "no positive analyzer signals"
    return "positive score from detailed analyzer reasons"
