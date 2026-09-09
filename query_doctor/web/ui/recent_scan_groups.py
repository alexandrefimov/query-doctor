"""Recent scan result grouping, sorting, and filter controls."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from query_doctor.web.presenters.recent_scan import RecentScanCaseRowView, numeric_value
from query_doctor.web.presenters.recent_scan_models import (
    RecentScanWorkloadActionQueueEntryView,
    RecentScanWorkloadHistoryView,
)
from query_doctor.web.query_inbox_time_range import normalize_query_inbox_time_range
from query_doctor.web.ui.html_helpers import escape_value
from query_doctor.web.trusted_artifacts import OPTIMIZER_STATUS_ORDER
from query_doctor.web.ui.recent_scan_result_filters import (
    RESULT_FILTER_PARAMS,
    RecentScanResultFilters,
    ResultFilterToggle,
    active_recent_scan_result_filter_count,
    active_recent_scan_result_filter_labels,
    filter_rows_by_result_filters,
    recent_scan_result_filter_query,
    recent_scan_result_filter_toggles,
    recent_scan_result_filters_from_mapping,
    result_filter_is_active,
    result_filters_with_toggle,
)


REWRITEABILITY_ORDER = {
    "safe_material_draft": 5,
    "recipe_detected_no_draft": 4,
    "recipe_adjacent_shape": 3,
    "stats_likely": 2,
    "human_review_only": 1,
    "not_rewriteable": 0,
    "unknown": 0,
}
QUERY_GROUPS = {
    "all": ("All analyzed", set()),
    "bad": ("Needs attention", {"failed", "high"}),
    "suspicious": ("Worth reviewing", {"suspicious"}),
    "workloads": ("Repeated workloads", set()),
    "frequent_short": ("Frequent short", set()),
    "regressions": ("Regressed workloads", set()),
    "optimization": ("Rewrite opportunities", set()),
    "stats": ("Stats to check", set()),
}
DEFAULT_QUERY_GROUP = "bad"
PRIMARY_QUERY_GROUPS = ("bad", "suspicious")
SECONDARY_QUERY_GROUPS = tuple(key for key in QUERY_GROUPS if key not in PRIMARY_QUERY_GROUPS)
FREQUENT_SHORT_WORKLOAD_P95_MAX_SEC = 60.0
RESULT_SORT_PARAM = "result_sort"
DEFAULT_RESULT_SORT = "default"
RESULT_SORT_OPTIONS = (
    ("default", "Default", "Default ranking for this result group"),
    ("priority", "Priority", "Highest diagnostic priority first"),
    ("duration", "Duration", "Longest duration first"),
    ("start", "Start time", "Newest safe start time first"),
    ("impact", "Impact", "Highest workload or follow-up impact first"),
)
RESULT_SORT_LABELS = {key: label for key, label, _description in RESULT_SORT_OPTIONS}
_SAFE_EXTRA_QUERY_VALUES = {
    "inbox_source": {"cm", "impala", "trino", "demo", "recent"},
    "inbox_workflow": {"finished", "running", "mixed"},
    "inbox_window": {"current", "live", "synthetic"},
}
_INBOX_SOURCE_STATE_LABELS = {
    "cm": "Cloudera Manager",
    "impala": "Direct Impala",
    "trino": "Trino materialized",
    "demo": "Demo",
    "recent": "Recent scan",
}
_INBOX_WORKFLOW_STATE_LABELS = {
    "finished": "Finished queries",
    "running": "Running queries",
    "mixed": "Mixed workflows",
}
_INBOX_WINDOW_STATE_LABELS = {
    "current": "Current scope",
    "live": "Live scope",
    "synthetic": "Synthetic demo",
}


def batch_table_columns(query_group: str, *, language: str = "en") -> tuple[str, ...]:
    del language
    normalized = normalize_query_group(query_group)
    if normalized == "optimization":
        return (
            "Rank",
            "Finding",
            "Query ID",
            "User",
            "Candidate",
            "Duration",
            "Impact",
            "Confidence",
            "Rewrite support",
        )
    elif normalized == "stats":
        return (
            "Rank",
            "Finding",
            "Query ID",
            "User",
            "Candidate",
            "Duration",
            "Need",
            "Speed benefit",
            "Confidence",
        )
    elif normalized == "workloads":
        return (
            "Rank",
            "Workload",
            "Priority",
            "p95",
            "Total impact",
            "Top owner",
        )
    elif normalized in {"regressions", "frequent_short"}:
        return (
            "Rank",
            "Finding",
            "Query ID",
            "User",
            "Runs",
            "Duration",
            "Workload p95",
            "Workload impact" if normalized == "frequent_short" else "Regression",
            "Primary",
        )
    return (
        "Rank",
        "Finding",
        "Query ID",
        "User",
        "Priority",
        "Duration",
    )


def batch_table_column_count(query_group: str) -> int:
    return len(batch_table_columns(query_group))


def batch_table_head(query_group: str, *, language: str = "en") -> str:
    headers = "".join(
        f"<th>{html.escape(label)}</th>"
        for label in batch_table_columns(query_group, language=language)
    )
    return f"<thead><tr>{headers}</tr></thead>"


def normalize_query_group(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in QUERY_GROUPS else DEFAULT_QUERY_GROUP


def normalize_result_sort(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in RESULT_SORT_LABELS else DEFAULT_RESULT_SORT


def filter_rows_by_query_group(
    rows: tuple[RecentScanCaseRowView, ...],
    query_group: str,
) -> tuple[RecentScanCaseRowView, ...]:
    normalized = normalize_query_group(query_group)
    if normalized == "optimization":
        return tuple(row for row in rows if is_optimization_row(row))
    if normalized == "stats":
        return tuple(row for row in rows if row.stats_tier in {"high", "medium"})
    if normalized == "workloads":
        return tuple(row for row in rows if is_repeated_workload_row(row))
    if normalized == "frequent_short":
        return frequent_short_workload_representatives(rows)
    if normalized == "regressions":
        return tuple(row for row in rows if is_regressed_workload_row(row))
    if normalized == "all":
        return rows
    _label, severities = QUERY_GROUPS[normalized]
    return tuple(row for row in rows if row.score_severity in severities)


def sort_rows_for_query_group(
    rows: tuple[RecentScanCaseRowView, ...],
    query_group: str,
    *,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> tuple[RecentScanCaseRowView, ...]:
    normalized = normalize_query_group(query_group)
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort != DEFAULT_RESULT_SORT:
        return sort_rows_by_result_sort(rows, normalized, normalized_sort)
    if normalized not in {"optimization", "stats", "workloads", "regressions", "frequent_short"}:
        return rows
    if normalized == "frequent_short":
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    -workload_group_impact(row),
                    -row.workload_group_member_count,
                    numeric_value(workload_short_duration(row)),
                    row.rank,
                ),
            )
        )
    if normalized in {"workloads", "regressions"}:
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    -workload_regression_order(row.workload_regression)
                    if normalized == "regressions"
                    else 0,
                    -workload_group_impact(row),
                    -row.workload_group_member_count,
                    -numeric_value(row.workload_group_duration_sec_p95),
                    -numeric_value(row.duration_sec),
                    row.rank,
                ),
            )
        )
    if normalized == "stats":
        stats_tier_order = {"high": 4, "medium": 3, "low": 2, "unknown": 1, "not_likely": 0}
        confidence_order = {"high": 2, "medium": 1, "low": 0}
        impact_order = {"high": 2, "medium": 1, "low": 0}
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    -stats_tier_order.get(row.stats_tier, 0),
                    -row.stats_score,
                    -confidence_order.get(row.stats_confidence, 0),
                    -impact_order.get(row.stats_impact, 0),
                    -numeric_value(row.duration_sec),
                    row.rank,
                ),
            )
        )
    tier_order = {"high": 3, "medium": 2, "low": 1, "not_likely": 0}
    impact_order = {"high": 2, "medium": 1, "low": 0}
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                -tier_order.get(row.optimization_tier, 0),
                -REWRITEABILITY_ORDER.get(row.optimizer_rewriteability_bucket, 0),
                -row.optimization_score,
                -impact_order.get(row.optimization_impact, 0),
                -OPTIMIZER_STATUS_ORDER.get(row.optimization_artifact_status, 0),
                -numeric_value(row.duration_sec),
                row.rank,
            ),
        )
    )


def sort_rows_by_result_sort(
    rows: tuple[RecentScanCaseRowView, ...],
    query_group: str,
    result_sort: str,
) -> tuple[RecentScanCaseRowView, ...]:
    normalized = normalize_query_group(query_group)
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort == "priority":
        return tuple(sorted(rows, key=result_sort_priority_key))
    if normalized_sort == "duration":
        return tuple(sorted(rows, key=result_sort_duration_key))
    if normalized_sort == "start":
        return tuple(sorted(rows, key=result_sort_start_key))
    if normalized_sort == "impact":
        if normalized in {"optimization", "stats", "workloads", "regressions", "frequent_short"}:
            return sort_rows_for_query_group(rows, normalized)
        return tuple(sorted(rows, key=result_sort_impact_key))
    return rows


def result_sort_priority_key(row: RecentScanCaseRowView) -> tuple[float, ...]:
    severity_order = {"failed": 4, "high": 3, "suspicious": 2, "clean": 1}
    return (
        -severity_order.get(str(row.score_severity or "").strip().lower(), 0),
        -row.score_value,
        -numeric_value(row.duration_sec),
        float(row.rank),
    )


def result_sort_duration_key(row: RecentScanCaseRowView) -> tuple[float, ...]:
    return (-numeric_value(row.duration_sec), -row.score_value, float(row.rank))


def result_sort_start_key(row: RecentScanCaseRowView) -> tuple[float, ...]:
    timestamp = result_sort_timestamp(row.start_time)
    missing = timestamp is None
    return (1.0 if missing else 0.0, -(timestamp or 0.0), float(row.rank))


def result_sort_impact_key(row: RecentScanCaseRowView) -> tuple[float, ...]:
    impact_order = {"high": 3, "medium": 2, "low": 1}
    return (
        -workload_group_impact(row),
        -impact_order.get(str(row.optimization_impact or "").strip().lower(), 0),
        -float(row.optimization_score),
        -impact_order.get(str(row.stats_impact or "").strip().lower(), 0),
        -float(row.stats_score),
        -numeric_value(row.duration_sec),
        -row.score_value,
        float(row.rank),
    )


def result_sort_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def is_optimization_row(row: RecentScanCaseRowView) -> bool:
    if row.optimization_tier in {"high", "medium"}:
        return True
    return row.optimizer_rewrite_support not in {"", "unknown", "not_candidate"}


def is_repeated_workload_row(row: RecentScanCaseRowView) -> bool:
    return row.workload_group_member_count > 1


def is_regressed_workload_row(row: RecentScanCaseRowView) -> bool:
    return is_repeated_workload_row(row) and row.workload_regression in {"strong", "mild"}


def is_frequent_short_workload_row(row: RecentScanCaseRowView) -> bool:
    duration = numeric_value(workload_short_duration(row))
    return (
        bool(row.workload_fingerprint)
        and is_repeated_workload_row(row)
        and duration > 0
        and duration <= FREQUENT_SHORT_WORKLOAD_P95_MAX_SEC
    )


def frequent_short_workload_representatives(
    rows: tuple[RecentScanCaseRowView, ...],
) -> tuple[RecentScanCaseRowView, ...]:
    grouped: dict[str, list[RecentScanCaseRowView]] = {}
    for row in rows:
        if is_frequent_short_workload_row(row):
            grouped.setdefault(row.workload_fingerprint, []).append(row)
    return tuple(frequent_short_representative(group_rows) for group_rows in grouped.values())


def frequent_short_representative(
    rows: list[RecentScanCaseRowView],
) -> RecentScanCaseRowView:
    return max(
        rows,
        key=lambda row: (
            numeric_value(row.duration_sec),
            row.score_value,
            -row.rank,
        ),
    )


def workload_regression_order(value: object) -> int:
    return {"strong": 2, "mild": 1}.get(str(value or "").strip().lower(), 0)


def workload_short_duration(row: RecentScanCaseRowView) -> object:
    return row.workload_group_duration_sec_p95 or row.duration_sec


def workload_group_impact(row: RecentScanCaseRowView) -> float:
    duration = numeric_value(row.workload_group_duration_sec_p95) or numeric_value(row.duration_sec)
    return row.workload_group_member_count * duration


def filter_rows_by_spills(
    rows: tuple[RecentScanCaseRowView, ...],
    *,
    only_with_spills: bool,
) -> tuple[RecentScanCaseRowView, ...]:
    if not only_with_spills:
        return rows
    return tuple(row for row in rows if row.has_spill)


def render_query_group_switcher(
    rows: tuple[RecentScanCaseRowView, ...],
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    extra_query: dict[str, str] | None = None,
    language: str = "en",
) -> str:
    counts = query_group_counts_for_rows(
        rows,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
    )
    links = query_group_links(
        PRIMARY_QUERY_GROUPS + SECONDARY_QUERY_GROUPS,
        active_group,
        counts,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
        language=language,
    )
    return (
        '<div class="batch-query-groups">'
        f'<nav class="batch-filter-tabs" aria-label="Query result filters">{"".join(links)}</nav>'
        "</div>"
    )


def query_group_links(
    keys: tuple[str, ...],
    active_group: str,
    counts: dict[str, int],
    *,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
    language: str = "en",
) -> list[str]:
    del language
    links = []
    for key in keys:
        label, _severities = QUERY_GROUPS[key]
        count = counts.get(key, 0)
        if key in SECONDARY_QUERY_GROUPS and count == 0 and key != active_group:
            continue
        classes = ["batch-filter-link"]
        if key == active_group:
            classes.append("batch-filter-link--active")
        elif count == 0:
            classes.append("batch-filter-link--zero")
        query: dict[str, str] = {"query_group": key}
        if extra_query:
            query.update(safe_extra_result_query(extra_query))
        if only_with_spills:
            query["only_with_spills"] = "on"
        href = f"/?{urlencode(query)}#recent-results"
        links.append(
            f'<a class="{" ".join(classes)}" href="{href}">'
            f"{html.escape(label)} <span>{count}</span></a>"
        )
    return links


def query_group_counts_for_rows(
    rows: tuple[RecentScanCaseRowView, ...],
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
) -> dict[str, int]:
    rows_for_counts = filter_rows_by_spills(rows, only_with_spills=only_with_spills)
    rows_for_counts = filter_rows_by_result_filters(rows_for_counts, result_filters)
    return {
        key: query_group_count(rows_for_counts, key, severities)
        for key, (_label, severities) in QUERY_GROUPS.items()
    }


def query_group_count(
    rows: tuple[RecentScanCaseRowView, ...],
    key: str,
    severities: set[str],
) -> int:
    if key == "optimization":
        return sum(1 for row in rows if is_optimization_row(row))
    if key == "stats":
        return sum(1 for row in rows if row.stats_tier in {"high", "medium"})
    if key == "workloads":
        return repeated_workload_group_count(rows)
    if key == "frequent_short":
        return len(frequent_short_workload_representatives(rows))
    if key == "regressions":
        return sum(1 for row in rows if is_regressed_workload_row(row))
    if key == "all":
        return len(rows)
    return sum(1 for row in rows if row.score_severity in severities)


def repeated_workload_group_count(rows: tuple[RecentScanCaseRowView, ...]) -> int:
    return len(
        {
            row.workload_fingerprint
            for row in rows
            if is_repeated_workload_row(row) and row.workload_fingerprint
        }
    )


def render_result_filters(
    rows: tuple[RecentScanCaseRowView, ...],
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    result_sort: str = DEFAULT_RESULT_SORT,
    extra_query: dict[str, str] | None = None,
    summary_text: str = "",
    filtered_count_text: str = "",
    language: str = "en",
) -> str:
    del language
    filter_toggles = recent_scan_result_filter_toggles(rows)
    switcher = render_query_group_switcher(
        rows,
        active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        extra_query=extra_query,
    )
    spill_toggle = render_spill_filter_toggle(
        active_group,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
    )
    result_filter_toggles = render_result_filter_toggles(
        active_group,
        rows=rows,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        extra_query=extra_query,
        result_filter_toggles=filter_toggles,
    )
    clear_result_filters = render_clear_result_filters_link(
        active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        extra_query=extra_query,
    )
    active_filter_summary = render_active_result_filter_summary(
        result_filters,
        only_with_spills=only_with_spills,
        toggles=filter_toggles,
    )
    sort_controls = render_result_sort_controls(
        active_group,
        result_sort=result_sort,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
    )
    view_state = render_result_view_state(
        active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=result_sort,
        extra_query=extra_query,
        toggles=filter_toggles,
    )
    summary_html = (
        f'<span class="batch-result-summary">{html.escape(summary_text)}</span>'
        if summary_text
        else ""
    )
    filtered_count_html = (
        f'<span class="batch-filtered-result-summary">{html.escape(filtered_count_text)}</span>'
        if filtered_count_text
        else ""
    )
    active_filter_count = active_recent_scan_result_filter_count(result_filters) + (
        1 if only_with_spills else 0
    )
    drawer_open = " open" if active_filter_count else ""
    drawer_hint = (
        f'<span class="batch-result-filter-count">{active_filter_count}</span>'
        if active_filter_count
        else ""
    )
    return (
        '<div class="batch-result-filters batch-result-filters--query-toolbar">'
        '<div class="batch-result-filter-row">'
        f"{switcher}"
        f"{summary_html}"
        f"{filtered_count_html}"
        "</div>"
        '<div class="batch-result-filter-row batch-result-filter-row--sort">'
        f"{sort_controls}"
        "</div>"
        f'<details class="batch-result-filter-drawer"{drawer_open}>'
        '<summary class="batch-result-filter-drawer-summary">'
        '<span class="batch-result-filter-label">Filters</span>'
        f"{drawer_hint}"
        f"{active_filter_summary}"
        "</summary>"
        '<div class="batch-result-filter-row batch-result-filter-row--secondary">'
        f"{spill_toggle}"
        f"{result_filter_toggles}"
        f"{clear_result_filters}"
        "</div>"
        '<div class="batch-result-filter-row batch-result-filter-row--state">'
        '<span class="batch-result-filter-label">State</span>'
        f"{view_state}"
        "</div>"
        "</details>"
        "</div>"
    )


def render_result_view_state(
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    result_sort: str = DEFAULT_RESULT_SORT,
    extra_query: dict[str, str] | None = None,
    toggles: tuple[ResultFilterToggle, ...] = (),
    language: str = "en",
) -> str:
    del language
    items = result_view_state_items(
        active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=result_sort,
        extra_query=extra_query,
        toggles=toggles,
    )
    chips = "".join(
        '<span class="batch-view-state-chip">'
        f"<strong>{html.escape(label)}</strong>"
        f'<span class="batch-view-state-value">{html.escape(value)}</span></span>'
        for label, value in items
    )
    href = result_view_state_href(
        active_group,
        only_with_spills=only_with_spills,
        result_sort=result_sort,
        extra_query=extra_query,
    )
    return (
        '<span class="batch-view-state-summary" aria-label="Current result view state">'
        f"{chips}</span>"
        f'<a class="batch-view-state-link" href="{html.escape(href, quote=True)}">View link</a>'
        f'<code class="batch-view-state-url">{html.escape(href)}</code>'
    )


def result_view_state_items(
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    result_sort: str = DEFAULT_RESULT_SORT,
    extra_query: dict[str, str] | None = None,
    toggles: tuple[ResultFilterToggle, ...] = (),
) -> tuple[tuple[str, str], ...]:
    normalized_group = normalize_query_group(active_group)
    items: list[tuple[str, str]] = [("Group", QUERY_GROUPS[normalized_group][0])]
    safe_query = safe_extra_result_query(extra_query)
    items.extend(scope_view_state_items(safe_query))
    if only_with_spills:
        items.append(("Result", "Spill evidence"))
    filter_labels = active_recent_scan_result_filter_labels(result_filters, toggles=toggles)
    if filter_labels:
        items.append(("Filters", "; ".join(filter_labels)))
    items.append(("Sort", RESULT_SORT_LABELS[normalize_result_sort(result_sort)]))
    return tuple(items)


def scope_view_state_items(safe_query: dict[str, str]) -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = []
    source = safe_query.get("inbox_source", "")
    if source:
        items.append(("Source", _INBOX_SOURCE_STATE_LABELS.get(source, source.upper())))
    workflow = safe_query.get("inbox_workflow", "")
    if workflow:
        items.append(("Target", _INBOX_WORKFLOW_STATE_LABELS.get(workflow, workflow.title())))
    from_time = safe_query.get("inbox_from", "")
    to_time = safe_query.get("inbox_to", "")
    if from_time and to_time:
        items.append(("Window", "Custom UTC range"))
    else:
        window = safe_query.get("inbox_window", "")
        if window:
            items.append(("Window", scope_window_state_label(window)))
    query_type = _safe_inbox_query_type(safe_query.get("inbox_query_type", ""))
    if query_type:
        items.append(("Query type", query_type))
    return tuple(items)


def scope_window_state_label(value: str) -> str:
    if value.isdigit():
        return f"Last {int(value)} min"
    return _INBOX_WINDOW_STATE_LABELS.get(value, value)


def result_view_state_href(
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_sort: str = DEFAULT_RESULT_SORT,
    extra_query: dict[str, str] | None = None,
) -> str:
    query = result_sort_query(
        active_group,
        result_sort,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
    )
    return f"/?{urlencode(query)}#recent-results"


def render_result_sort_controls(
    active_group: str,
    *,
    result_sort: str = DEFAULT_RESULT_SORT,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
    language: str = "en",
) -> str:
    del language
    active_sort = normalize_result_sort(result_sort)
    links = []
    for value, label, description in RESULT_SORT_OPTIONS:
        query = result_sort_query(
            active_group,
            value,
            only_with_spills=only_with_spills,
            extra_query=extra_query,
        )
        active = value == active_sort
        active_class = " batch-spill-toggle--active" if active else ""
        href = f"/?{urlencode(query)}#recent-results"
        links.append(
            f'<a class="batch-spill-toggle batch-sort-toggle{active_class}" '
            f'href="{html.escape(href, quote=True)}" '
            f'aria-pressed="{str(active).lower()}" '
            f'title="{html.escape(description, quote=True)}">'
            f"<span>{html.escape(label)}</span></a>"
        )
    return f'<span class="batch-sort-controls" aria-label="Sort results">{"".join(links)}</span>'


def result_sort_query(
    active_group: str,
    result_sort: str,
    *,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
) -> dict[str, str]:
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    if extra_query:
        safe_query = safe_extra_result_query(extra_query)
        safe_query.pop("query_group", None)
        query.update(safe_query)
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort == DEFAULT_RESULT_SORT:
        query.pop(RESULT_SORT_PARAM, None)
    else:
        query[RESULT_SORT_PARAM] = normalized_sort
    if only_with_spills:
        query["only_with_spills"] = "on"
    return query


def render_spill_filter_toggle(
    active_group: str,
    *,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
    language: str = "en",
) -> str:
    del language
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    if extra_query:
        query.update(safe_extra_result_query(extra_query))
    active_class = " batch-spill-toggle--active" if only_with_spills else ""
    if not only_with_spills:
        query["only_with_spills"] = "on"
    href = f"/?{urlencode(query)}#recent-results"
    return (
        f'<a class="batch-spill-toggle{active_class}" href="{href}" '
        f'aria-label="Only queries with spills" aria-pressed="{str(only_with_spills).lower()}">'
        f'<span class="batch-spill-check" aria-hidden="true">{"✓" if only_with_spills else ""}</span>'
        "<span>Only queries with spills</span></a>"
    )


def render_result_filter_toggles(
    active_group: str,
    *,
    rows: tuple[RecentScanCaseRowView, ...] = (),
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    extra_query: dict[str, str] | None = None,
    result_filter_toggles: tuple[ResultFilterToggle, ...] | None = None,
    language: str = "en",
) -> str:
    del language
    toggles: list[str] = []
    for toggle in (
        result_filter_toggles
        if result_filter_toggles is not None
        else recent_scan_result_filter_toggles(rows)
    ):
        next_filters = result_filters_with_toggle(result_filters, toggle.param, toggle.value)
        query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
        if extra_query:
            query.update(safe_extra_result_query(extra_query))
        for result_param in RESULT_FILTER_PARAMS:
            query.pop(result_param, None)
        query.update(recent_scan_result_filter_query(next_filters))
        if only_with_spills:
            query["only_with_spills"] = "on"
        active = result_filter_is_active(result_filters, toggle.param, toggle.value)
        active_class = " batch-spill-toggle--active" if active else ""
        href = f"/?{urlencode(query)}#recent-results"
        count_badge = (
            f'<span class="batch-filter-count">{toggle.count}</span>'
            if toggle.count is not None
            else ""
        )
        zero_class = " batch-readiness-toggle--zero" if toggle.count == 0 and not active else ""
        toggles.append(
            f'<a class="batch-spill-toggle batch-readiness-toggle{zero_class}{active_class}" '
            f'href="{href}" aria-label="{html.escape(toggle.aria_label, quote=True)}" '
            f'aria-pressed="{str(active).lower()}">'
            f'<span class="batch-spill-check" aria-hidden="true">{"✓" if active else ""}</span>'
            f"<span>{html.escape(toggle.label)}</span>{count_badge}</a>"
        )
    return "".join(toggles)


def render_active_result_filter_summary(
    result_filters: RecentScanResultFilters | None,
    *,
    only_with_spills: bool = False,
    toggles: tuple[ResultFilterToggle, ...] = (),
    language: str = "en",
) -> str:
    del language
    labels = list(active_recent_scan_result_filter_labels(result_filters, toggles=toggles))
    if only_with_spills:
        labels.insert(0, "Spill evidence")
    if not labels:
        return ""
    return (
        '<span class="batch-active-filter-summary" aria-label="Active result filters">'
        "<strong>Active filters</strong>"
        f"<span>{html.escape('; '.join(labels))}</span></span>"
    )


def render_clear_result_filters_link(
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    extra_query: dict[str, str] | None = None,
    language: str = "en",
) -> str:
    del language
    if not active_recent_scan_result_filter_count(result_filters):
        return ""
    href = clear_result_filters_href(
        active_group,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
    )
    return (
        '<a class="batch-spill-toggle batch-clear-result-filters" '
        f'href="{html.escape(href, quote=True)}" '
        'aria-label="Clear active result filters">'
        "<span>Clear filters</span></a>"
    )


def clear_result_filters_href(
    active_group: str,
    *,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
) -> str:
    query = clear_result_filters_query(
        active_group,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
    )
    return f"/?{urlencode(query)}#recent-results"


def clear_result_filters_query(
    active_group: str,
    *,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
) -> dict[str, str]:
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    if extra_query:
        query.update(safe_extra_result_query(extra_query))
    for result_param in RESULT_FILTER_PARAMS:
        query.pop(result_param, None)
    if only_with_spills:
        query["only_with_spills"] = "on"
    return query


def safe_extra_result_query(extra_query: dict[str, str] | None) -> dict[str, str]:
    if not extra_query:
        return {}
    safe: dict[str, str] = {}
    from_time, to_time = normalize_query_inbox_time_range(
        extra_query.get("inbox_from"),
        extra_query.get("inbox_to"),
    )
    for key, value in extra_query.items():
        if key in {"inbox_from", "inbox_to"}:
            continue
        normalized = str(value or "").strip().lower()
        if not normalized:
            continue
        if key == "inbox_window" and normalized.isdigit():
            minutes = int(normalized)
            if 0 < minutes <= 525600:
                safe[key] = normalized
            continue
        if key == "inbox_query_type":
            query_type = _safe_inbox_query_type(value)
            if query_type:
                safe[key] = query_type
            continue
        if key == RESULT_SORT_PARAM:
            result_sort = normalize_result_sort(value)
            if result_sort != DEFAULT_RESULT_SORT:
                safe[key] = result_sort
            continue
        if normalized in _SAFE_EXTRA_QUERY_VALUES.get(key, set()):
            safe[key] = normalized
    if from_time and to_time:
        safe.pop("inbox_window", None)
        safe["inbox_from"] = from_time
        safe["inbox_to"] = to_time
    safe.update(
        recent_scan_result_filter_query(recent_scan_result_filters_from_mapping(extra_query))
    )
    return safe


def _safe_inbox_query_type(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text or len(text) > 32 or not ("A" <= text[0] <= "Z"):
        return ""
    if any(not ("A" <= char <= "Z" or "0" <= char <= "9" or char == "_") for char in text):
        return ""
    return text


def render_workload_followup_shortlist(
    entries: tuple[RecentScanWorkloadActionQueueEntryView, ...],
    *,
    workload_base_path: str = "/batch/workload",
    limit: int = 3,
    language: str = "en",
) -> str:
    del language
    selected = entries[: max(0, limit)]
    if not selected:
        return ""
    rows = "".join(
        render_workload_followup_shortlist_item(
            entry,
            workload_base_path=workload_base_path,
        )
        for entry in selected
    )
    return (
        '<div class="batch-context-block workload-followup" aria-label="Workload follow-up">'
        '<div class="batch-context-title">Workload follow-up</div>'
        '<div class="workload-followup-list">'
        "<p>Open repeated patterns when one query row is not enough.</p>"
        f"<ul>{rows}</ul>"
        '<nav class="workload-followup-links" aria-label="Workload result views">'
        '<a href="/?query_group=workloads#recent-results">Repeated workloads</a>'
        '<a href="/?query_group=regressions#recent-results">Regressed workloads</a>'
        "</nav>"
        "</div>"
        "</div>"
    )


def render_workload_followup_shortlist_item(
    entry: RecentScanWorkloadActionQueueEntryView,
    *,
    workload_base_path: str,
    language: str = "en",
) -> str:
    del language
    href = workload_href(entry.fingerprint, workload_base_path=workload_base_path)
    return (
        "<li>"
        f'<a href="{href}">{escape_value(entry.signal)}</a>'
        f"<span>{escape_value(entry.priority)} priority; impact {escape_value(entry.group_impact)}; "
        f"{escape_value(entry.evidence)}</span>"
        f"<small>Open Workload Details; {escape_value(entry.outcome_summary)}</small>"
        "</li>"
    )


def workload_href(fingerprint: str, *, workload_base_path: str) -> str:
    return (
        f"{html.escape(workload_base_path.rstrip('/'), quote=True)}/"
        f"{html.escape(fingerprint, quote=True)}"
    )


def workload_history_context_text(view: RecentScanWorkloadHistoryView | None) -> str:
    if view is None:
        return ""
    parts = ["enabled" if view.enabled else "disabled"]
    if view.loaded_record_count:
        parts.append(f"loaded {view.loaded_record_count}")
    if view.appended_record_count:
        parts.append(f"appended {view.appended_record_count}")
    regression_text = workload_history_regression_counts_text(view)
    if regression_text != "none":
        parts.append(f"regressions {regression_text}")
    return f"Workload history: {'; '.join(parts)}"


def workload_history_regression_counts_text(view: RecentScanWorkloadHistoryView) -> str:
    if not view.regression_counts:
        return "none"
    return ", ".join(f"{label}={count}" for label, count in view.regression_counts)
