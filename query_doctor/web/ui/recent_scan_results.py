"""Recent query scan summary and progress rendering helpers."""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from query_doctor.web.action_outcomes import (
    WorkloadOutcomeMetric,
    action_outcome_count,
    workload_outcome_metrics_by_fingerprint,
)
from query_doctor.web.ui.html_helpers import (
    SafeHtml,
    compact_cell,
    display_score,
    escape_value,
)
from query_doctor.web.ui.diagnostic_i18n import localize_diagnostic_text
from query_doctor.web.trusted_artifacts import decorate_cases_with_optimizer_artifact_status
from query_doctor.recent.materialized_case_index import materialized_case_entries
from query_doctor.web.presenters.recent_scan_summary import (
    recent_scan_empty_message,
    recent_scan_scope_parts,
    recent_scan_warning_messages,
)
from query_doctor.web.presenters.recent_scan import (
    RecentScanCaseRowView,
    is_online_history_summary,
    numeric_count,
    numeric_value,
    online_history_profile_status_count,
    present_recent_scan_case_row,
    present_recent_scan_summary,
    recent_scan_rows_with_action_outcomes,
)
from query_doctor.web.presenters.recent_scan_models import (
    RecentScanSummaryView,
    RecentScanWorkloadGroupView,
)
from query_doctor.web.presenters.recent_scan_values import safe_truthy
from query_doctor.web.presenters.workload_action_contract import (
    top_owner_summary,
    workload_group_impact as workload_total_impact,
)
from query_doctor.web.recent_history_inbox import (
    DEFAULT_HISTORY_VIEW,
    recent_history_inbox_summary_from_settings,
)
from query_doctor.web.ui.recent_scan_view_cache import (
    cached_recent_scan_summary_view,
    recent_scan_summary_view_for_render,
)
from query_doctor.web.ui.recent_scan_groups import (
    DEFAULT_RESULT_SORT,
    DEFAULT_QUERY_GROUP,
    QUERY_GROUPS,
    batch_table_column_count,
    batch_table_head,
    filter_rows_by_query_group,
    filter_rows_by_spills,
    normalize_result_sort,
    normalize_query_group,
    render_result_filters,
    render_workload_followup_shortlist,
    safe_extra_result_query,
    sort_rows_for_query_group,
    workload_group_impact,
    workload_href,
    workload_history_context_text,
)
from query_doctor.web.ui.recent_scan_result_filters import (
    RESULT_FILTER_PARAMS,
    ResultFilterToggle,
    RecentScanResultFilters,
    active_recent_scan_result_filter_count,
    active_recent_scan_result_filter_labels,
    filter_rows_by_result_filters,
    normalize_recent_scan_result_filters,
    recent_scan_result_filter_toggles,
    row_matches_result_filters,
)
from query_doctor.web.ui.recent_scan_progress import (
    batch_progress_percent,
    case_detail,
    discovery_detail,
    metadata_detail,
    progress_step,
    read_batch_progress_events,
    render_batch_progress_panel,
    summarize_batch_progress,
)
from query_doctor.web.ui.source_locations import render_source_location_chips


RESULTS_PAGE_PARAM = "results_page"
RESULTS_PAGE_SIZE = 250
RESULTS_MAX_PAGE = 10000


def render_batch_card(
    settings: Any,
    query_group: str = DEFAULT_QUERY_GROUP,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    result_sort: str = DEFAULT_RESULT_SORT,
    results_page: Any = 1,
    workload_admin_scope: str = "all",
    workload_admin_signal: str = "all",
    workload_group_scope: str = "",
    workload_group_name: str = "",
    workload_group_signal: str = "all",
    extra_query: dict[str, str] | None = None,
    title: str = "Finished Queries",
    details_base_path: str = "/batch/case",
    history_view: str = DEFAULT_HISTORY_VIEW,
) -> str:
    summary_path = getattr(settings, "batch_summary", None)
    corpus_summary = getattr(settings, "corpus_summary", None)
    if summary_path is None and not isinstance(corpus_summary, dict):
        history_summary = recent_history_inbox_summary_from_settings(
            settings,
            history_view=history_view,
        )
        if history_summary is None:
            return ""
        payload = history_summary
        summary_view = None
    if isinstance(corpus_summary, dict):
        payload = corpus_summary
        summary_view = None
    elif summary_path is not None:
        escaped_title = html.escape(title)
        aria_label = html.escape(title.lower())
        try:
            payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return (
                f'<section class="panel batch-panel" aria-label="{aria_label}">'
                f'<div class="batch-head"><div><h1>{escaped_title}</h1>'
                "<p>Configured batch summary could not be read.</p></div></div>"
                f'<div class="batch-note">{html.escape(type(exc).__name__)}</div>'
                "</section>"
            )
        summary_view = None
    if not isinstance(payload, dict):
        escaped_title = html.escape(title)
        aria_label = html.escape(title.lower())
        return (
            f'<section class="panel batch-panel" aria-label="{aria_label}">'
            f'<div class="batch-head"><div><h1>{escaped_title}</h1>'
            "<p>Configured batch summary is not a JSON object.</p></div></div>"
            "</section>"
        )
    language = getattr(settings, "language", "en")
    if summary_view is None and not isinstance(corpus_summary, dict) and summary_path is not None:
        summary_view = cached_recent_scan_summary_view(
            payload,
            summary_path=Path(summary_path),
            language=language,
        )
    workload_outcome_metrics = workload_outcome_metrics_by_fingerprint()
    render_payload = payload
    if summary_view is None:
        render_payload = decorate_cases_with_optimizer_artifact_status(payload)
        summary_view = recent_scan_summary_view_for_render(
            render_payload,
            cache_source=payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )
    effective_title = corpus_summary_title(payload) or title
    return render_batch_summary(
        render_payload,
        query_group=query_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=result_sort,
        results_page=results_page,
        workload_admin_scope=workload_admin_scope,
        workload_admin_signal=workload_admin_signal,
        workload_group_scope=workload_group_scope,
        workload_group_name=workload_group_name,
        workload_group_signal=workload_group_signal,
        extra_query=extra_query,
        title=effective_title,
        details_base_path=details_base_path,
        action_outcomes_recorded=action_outcome_count(),
        workload_outcome_metrics=workload_outcome_metrics,
        summary_view=summary_view,
        language=language,
    )


def corpus_summary_title(summary: dict[str, Any]) -> str | None:
    if str(summary.get("mode") or "").strip().lower() == "manual-profile-corpus":
        return "Exported Profiles"
    if str(summary.get("mode") or "").strip().lower() == "recent-history-online":
        return "Online History"
    return None


def render_batch_summary(
    summary: dict[str, Any],
    query_group: str = DEFAULT_QUERY_GROUP,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    result_sort: str = DEFAULT_RESULT_SORT,
    results_page: Any = 1,
    workload_admin_scope: str = "all",
    workload_admin_signal: str = "all",
    workload_group_scope: str = "",
    workload_group_name: str = "",
    workload_group_signal: str = "all",
    extra_query: dict[str, str] | None = None,
    title: str = "Finished Queries",
    details_base_path: str = "/batch/case",
    action_outcomes_recorded: int | None = None,
    workload_outcome_metrics: dict[str, WorkloadOutcomeMetric] | None = None,
    summary_view: RecentScanSummaryView | None = None,
    language: str = "en",
) -> str:
    view = summary_view
    if view is None:
        view = present_recent_scan_summary(
            summary,
            workload_outcome_metrics=workload_outcome_metrics,
        )
    elif workload_outcome_metrics:
        view = replace(
            view,
            rows=recent_scan_rows_with_action_outcomes(view.rows, workload_outcome_metrics),
        )
    active_group = normalize_query_group(query_group)
    active_result_sort = normalize_result_sort(result_sort)
    normalized_result_filters = normalize_recent_scan_result_filters(result_filters)
    result_filter_toggles = recent_scan_result_filter_toggles(view.rows)
    requested_page = normalize_results_page(results_page)
    results_base_path = result_list_base_path_for_details_base_path(details_base_path)
    workload_base_path = workload_base_path_for_details_base_path(details_base_path)
    if active_group == "workloads":
        rows_by_workload = rows_by_workload_fingerprint(view.rows)
        base_workload_groups = workload_groups_for_table(
            view.workload_groups.groups,
            rows_by_workload,
        )
        workload_groups = workload_groups_for_table(
            view.workload_groups.groups,
            rows_by_workload,
            only_with_spills=only_with_spills,
            result_filters=normalized_result_filters,
        )
        workload_groups = sort_workload_groups_for_result_sort(
            workload_groups,
            active_result_sort,
        )
        total_unfiltered_result_rows = len(base_workload_groups)
        current_page, page_start, page_end, total_pages = results_page_bounds(
            len(workload_groups),
            requested_page,
        )
        visible_workload_groups = workload_groups[page_start:page_end]
        rows = "\n".join(
            render_workload_group_table_row(
                display_rank,
                group,
                group_rows=rows_by_workload.get(group.fingerprint, ()),
                workload_base_path=workload_base_path,
                language=language,
            )
            for display_rank, group in enumerate(visible_workload_groups, start=page_start + 1)
        )
        total_result_rows = len(workload_groups)
        result_count_unit = "workload group"
    else:
        rows_for_group = filter_rows_by_query_group(view.rows, active_group)
        total_unfiltered_result_rows = len(rows_for_group)
        rows_for_group = filter_rows_by_result_filters(rows_for_group, normalized_result_filters)
        rows_for_group = filter_rows_by_spills(rows_for_group, only_with_spills=only_with_spills)
        rows_for_group = sort_rows_for_query_group(
            rows_for_group,
            active_group,
            result_sort=active_result_sort,
        )
        current_page, page_start, page_end, total_pages = results_page_bounds(
            len(rows_for_group),
            requested_page,
        )
        visible_rows = rows_for_group[page_start:page_end]
        rows = "\n".join(
            render_batch_case_row(
                display_rank,
                row,
                details_base_path=details_base_path,
                workload_base_path=workload_base_path,
                query_group=active_group,
                language=language,
            )
            for display_rank, row in enumerate(visible_rows, start=page_start + 1)
        )
        total_result_rows = len(rows_for_group)
        result_count_unit = "row"
    pagination = render_results_pagination(
        active_group,
        current_page=current_page,
        total_pages=total_pages,
        total_count=total_result_rows,
        page_start=page_start,
        page_end=page_end,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
        base_path=results_base_path,
        language=language,
    )
    broad_scan_message = recent_scan_too_broad_message(summary)
    if not rows:
        empty_text = broad_scan_message or batch_result_empty_message(
            view.rows,
            active_group,
            only_with_spills=only_with_spills,
            result_filters=normalized_result_filters,
            result_filter_toggles=result_filter_toggles,
        )
        clear_href = ""
        if (
            not broad_scan_message
            and view.rows
            and active_recent_scan_result_filter_count(normalized_result_filters)
        ):
            clear_href = empty_result_href(
                active_group,
                only_with_spills=only_with_spills,
                extra_query=extra_query,
                clear_result_filters=True,
                base_path=results_base_path,
            )
        empty_html = batch_result_empty_cell_html(
            empty_text,
            clear_result_filters_href=clear_href,
            actions=empty_result_action_links(
                active_group,
                only_with_spills=only_with_spills,
                result_filters=normalized_result_filters,
                extra_query=extra_query,
                base_path=results_base_path,
                include_clear_filters=bool(clear_href),
            )
            if not broad_scan_message and view.rows
            else (),
        )
        rows = f'<tr><td colspan="{batch_table_column_count(active_group)}" class="empty-cell">{empty_html}</td></tr>'
    empty_notice_parts = batch_empty_notice_parts_from_message(summary, view.empty_message)
    warning_message = scan_warning_message_from_warnings(view.warning_messages)
    results_notices_open = empty_notice_parts is not None or bool(warning_message)
    critical_results_notices = (
        render_results_notices(
            summary,
            (),
            empty_notice_parts=empty_notice_parts,
            warning_message=warning_message,
            notice_inputs_precomputed=True,
            open_by_default=results_notices_open,
            include_guidance=False,
            include_action_outcomes=False,
            language=language,
        )
        if results_notices_open
        else ""
    )
    secondary_results_notices = render_results_notices(
        summary,
        view.header_items,
        action_outcomes_recorded=action_outcomes_recorded,
        compact=True,
        empty_notice_parts=empty_notice_parts,
        warning_message=warning_message,
        notice_inputs_precomputed=True,
        include_guidance=False,
        include_empty=False,
        include_warnings=False,
        language=language,
    )
    scan_details = render_batch_scan_details(
        summary,
        view.header_items,
        compact=True,
        workload_history=view.workload_history,
        language=language,
    )
    frequent_short_limitations = render_frequent_short_limitations_note(
        summary,
        active_group,
        language=language,
    )
    switcher = render_result_filters(
        view.rows,
        active_group,
        only_with_spills=only_with_spills,
        result_filters=normalized_result_filters,
        result_sort=active_result_sort,
        extra_query=extra_query,
        summary_text=scan_volume_summary(view.header_items, language=language),
        filtered_count_text=filtered_result_count_summary(
            total_result_rows,
            total_unfiltered_result_rows,
            unit=result_count_unit,
            active=only_with_spills
            or bool(active_recent_scan_result_filter_count(normalized_result_filters)),
            language=language,
        ),
        language=language,
    )
    workload_followup = render_workload_followup_shortlist(
        view.workload_digest.action_queue,
        workload_base_path=workload_base_path,
        language=language,
    )
    table_legend = render_results_table_legend(active_group, language=language)
    result_context = render_results_context_details(
        scan_details,
        secondary_results_notices,
        frequent_short_limitations,
        workload_followup,
        table_legend,
        language=language,
    )
    escaped_title = html.escape(title)
    aria_label = html.escape(title.lower())
    safe_active_group = html.escape(active_group, quote=True)
    table_class = f"batch-table batch-results-table batch-results-table--{safe_active_group}"
    return (
        f'<details id="recent-results" class="panel batch-panel batch-results-disclosure" aria-label="{aria_label}" open data-results-disclosure>'
        '<summary class="batch-head">'
        f"<div><h1>{escaped_title}</h1></div>"
        "</summary>"
        '<div class="batch-results-body">'
        f"{switcher}"
        f"{critical_results_notices}"
        f"{pagination}"
        f'<div class="batch-table-wrap"><table class="{table_class}">'
        f"{batch_table_head(active_group, language=language)}"
        f"<tbody>{rows}</tbody>"
        "</table></div>"
        f"{pagination}"
        f"{result_context}"
        "</div>"
        "</details>"
    )


def render_action_outcomes_note(count: int | None) -> str:
    if count is None:
        return ""
    return (
        '<div class="batch-note"><strong>Action outcomes recorded:</strong> '
        f'<a href="/outcomes">{html.escape(str(max(0, count)))}</a></div>'
    )


def filtered_result_count_summary(
    visible_count: int,
    unfiltered_count: int,
    *,
    unit: str = "row",
    active: bool = False,
    language: str = "en",
) -> str:
    del language
    if not active:
        return ""
    safe_unfiltered = max(0, int(unfiltered_count))
    if safe_unfiltered <= 0:
        return ""
    safe_visible = max(0, int(visible_count))
    noun = unit if safe_unfiltered == 1 else f"{unit}s"
    return f"Showing {safe_visible} of {safe_unfiltered} {noun}"


def render_frequent_short_limitations_note(
    summary: dict[str, Any],
    active_group: str,
    *,
    language: str = "en",
) -> str:
    if normalize_query_group(active_group) != "frequent_short":
        return ""
    limitations = frequent_short_limitation_messages(summary)
    if not limitations:
        return ""
    del language
    items = "".join(f"<li>{html.escape(item)}</li>" for item in limitations)
    return (
        '<div class="batch-note batch-note--frequent-short-limitations">'
        "<strong>Frequent short limitations:</strong>"
        f"<ul>{items}</ul>"
        "</div>"
    )


def frequent_short_limitation_messages(summary: dict[str, Any]) -> tuple[str, ...]:
    messages = [frequent_short_selection_scope_message(summary)]
    if frequent_short_has_incomplete_fingerprint_coverage(summary):
        messages.append(
            "Some analyzed cases have no complete workload fingerprint, so this view can undercount repeated short shapes."
        )
    if not frequent_short_runtime_metrics_available(summary):
        messages.append(
            "Runtime/admission metrics are not available in this summary; duration and repetition do not prove admission pressure."
        )
    return tuple(message for message in messages if message)


def frequent_short_selection_scope_message(summary: dict[str, Any]) -> str:
    selected = numeric_count(summary.get("selected_count"))
    inspected = numeric_count(summary.get("summaries_inspected"))
    if selected is None:
        return "Ranks only analyzed cases from the current bounded scan."
    case_word = "case" if selected == 1 else "cases"
    if inspected is not None and inspected > selected:
        return (
            f"Ranks only the {selected} analyzed {case_word} selected from "
            f"{inspected} scanned summaries."
        )
    return f"Ranks only {selected} analyzed {case_word} from the current bounded scan."


def frequent_short_has_incomplete_fingerprint_coverage(summary: dict[str, Any]) -> bool:
    cases = summary.get("cases")
    if not isinstance(cases, list):
        return False
    for case in cases:
        if not isinstance(case, dict):
            continue
        if safe_truthy(case.get("workload_fingerprint_incomplete")):
            return True
        fingerprint = case.get("group_fingerprint") or case.get("workload_fingerprint")
        if not str(fingerprint or "").strip():
            return True
    return False


def frequent_short_runtime_metrics_available(summary: dict[str, Any]) -> bool:
    provider = str(summary.get("runtime_metrics_provider") or "").strip().lower()
    if provider and provider != "none":
        return True
    return safe_truthy(summary.get("collect_cm_timeseries")) or safe_truthy(
        summary.get("collect_prometheus_timeseries")
    )


def workload_base_path_for_details_base_path(details_base_path: str) -> str:
    normalized = details_base_path.rstrip("/")
    if normalized.endswith("/case"):
        return f"{normalized[:-5]}/workload"
    return "/batch/workload"


def result_list_base_path_for_details_base_path(details_base_path: str) -> str:
    normalized = details_base_path.rstrip("/")
    if normalized == "/running" or normalized.startswith("/running/"):
        return "/running"
    return "/"


def normalize_results_page(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 1
    try:
        page = int(text)
    except ValueError:
        return 1
    if page < 1:
        return 1
    return min(page, RESULTS_MAX_PAGE)


def results_page_bounds(
    total_count: int,
    requested_page: int,
    *,
    page_size: int = RESULTS_PAGE_SIZE,
) -> tuple[int, int, int, int]:
    safe_total = max(0, int(total_count))
    safe_page_size = max(1, int(page_size))
    total_pages = max(1, (safe_total + safe_page_size - 1) // safe_page_size)
    current_page = min(max(1, requested_page), total_pages)
    if safe_total == 0:
        return current_page, 0, 0, total_pages
    page_start = (current_page - 1) * safe_page_size
    page_end = min(safe_total, page_start + safe_page_size)
    return current_page, page_start, page_end, total_pages


def render_results_pagination(
    active_group: str,
    *,
    current_page: int,
    total_pages: int,
    total_count: int,
    page_start: int,
    page_end: int,
    only_with_spills: bool = False,
    extra_query: dict[str, str] | None = None,
    base_path: str = "/",
    language: str = "en",
) -> str:
    del language
    if total_count <= RESULTS_PAGE_SIZE:
        return ""
    first_row = page_start + 1
    status = f"Rows {first_row}-{page_end} of {total_count}; page {current_page} of {total_pages}"
    previous_link = render_results_page_link(
        "Prev",
        current_page - 1,
        active_group=active_group,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
        base_path=base_path,
        disabled=current_page <= 1,
    )
    next_link = render_results_page_link(
        "Next",
        current_page + 1,
        active_group=active_group,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
        base_path=base_path,
        disabled=current_page >= total_pages,
    )
    return (
        '<nav class="batch-results-pagination" aria-label="Results pages">'
        f'<span class="batch-results-page-status">{html.escape(status)}</span>'
        '<span class="batch-results-page-links">'
        f"{previous_link}{next_link}"
        "</span>"
        "</nav>"
    )


def render_results_page_link(
    label: str,
    page: int,
    *,
    active_group: str,
    only_with_spills: bool,
    extra_query: dict[str, str] | None,
    base_path: str,
    disabled: bool = False,
) -> str:
    safe_label = html.escape(label)
    if disabled:
        return (
            '<span class="batch-results-page-link batch-results-page-link--disabled" '
            f'aria-disabled="true">{safe_label}</span>'
        )
    href = results_page_href(
        active_group,
        page,
        only_with_spills=only_with_spills,
        extra_query=extra_query,
        base_path=base_path,
    )
    safe_href = html.escape(href, quote=True)
    return f'<a class="batch-results-page-link" href="{safe_href}">{safe_label}</a>'


def results_page_href(
    active_group: str,
    page: int,
    *,
    only_with_spills: bool,
    base_path: str,
    extra_query: dict[str, str] | None = None,
) -> str:
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    if extra_query:
        safe_query = safe_extra_result_query(extra_query)
        safe_query.pop("query_group", None)
        query.update(safe_query)
    if only_with_spills:
        query["only_with_spills"] = "on"
    if page > 1:
        query[RESULTS_PAGE_PARAM] = str(normalize_results_page(page))
    normalized_base = base_path if str(base_path or "").startswith("/") else "/"
    return f"{html.escape(normalized_base, quote=True)}?{urlencode(query)}#recent-results"


def render_results_notices(
    summary: dict[str, Any],
    header_items: tuple[tuple[str, Any], ...],
    *,
    action_outcomes_recorded: int | None = None,
    compact: bool = False,
    empty_notice_parts: tuple[str, str] | None = None,
    warning_message: str = "",
    notice_inputs_precomputed: bool = False,
    open_by_default: bool | None = None,
    include_guidance: bool = True,
    include_action_outcomes: bool = True,
    include_empty: bool = True,
    include_warnings: bool = True,
    language: str = "en",
) -> str:
    rows = results_notice_rows(
        summary,
        header_items,
        action_outcomes_recorded=action_outcomes_recorded,
        empty_notice_parts=empty_notice_parts,
        warning_message=warning_message,
        notice_inputs_precomputed=notice_inputs_precomputed,
        include_guidance=include_guidance,
        include_action_outcomes=include_action_outcomes,
        include_empty=include_empty,
        include_warnings=include_warnings,
    )
    if not rows:
        return ""
    del language
    row_labels = {label for label, _body in rows}
    single_warning = row_labels == {"Scan warnings"} and len(rows) == 1
    notice_title = "Scan warnings" if single_warning else "Scan notes"
    if single_warning:
        rendered_rows = (
            '<div class="batch-notice-row batch-notice-row--single">'
            f"<span>{rows[0][1]}</span>"
            "</div>"
        )
    else:
        rendered_rows = "".join(
            '<div class="batch-notice-row">'
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{body}</span>"
            "</div>"
            for label, body in rows
        )
    if compact:
        return (
            f'<div class="batch-context-block batch-context-notes" aria-label="{notice_title}">'
            f'<div class="batch-context-title">{notice_title}</div>'
            f'<div class="batch-notices-body">{rendered_rows}</div>'
            "</div>"
        )
    if single_warning:
        return (
            f'<p class="batch-notice-line" aria-label="{notice_title}">'
            f"<strong>{notice_title}</strong>"
            f"<span>{rows[0][1]}</span>"
            "</p>"
        )
    if open_by_default is None:
        open_by_default = results_notices_open_by_default(
            summary,
            empty_notice_parts=empty_notice_parts,
            warning_message=warning_message,
            notice_inputs_precomputed=notice_inputs_precomputed,
        )
    open_attr = " open" if open_by_default else ""
    return (
        f'<details class="batch-notices" aria-label="{notice_title}"{open_attr}>'
        f"<summary>{notice_title}</summary>"
        f'<div class="batch-notices-body">{rendered_rows}</div>'
        "</details>"
    )


def results_notices_open_by_default(
    summary: dict[str, Any],
    *,
    empty_notice_parts: tuple[str, str] | None = None,
    warning_message: str = "",
    notice_inputs_precomputed: bool = False,
) -> bool:
    if not notice_inputs_precomputed:
        empty_notice_parts = batch_empty_notice_parts(summary)
        warning_message = scan_warning_message(summary)
    return empty_notice_parts is not None or bool(warning_message)


def render_results_context_details(*sections: str, language: str = "en") -> str:
    del language
    content = "".join(section for section in sections if section)
    if not content:
        return ""
    return (
        '<section id="scan-context" class="batch-results-context" aria-label="Scan context">'
        '<div class="batch-results-context-head">'
        "<h2>Scan context</h2>"
        "<p>Coverage, scan notes, and compact follow-up links for this result set.</p>"
        "</div>"
        f'<div class="batch-results-context-body">{content}</div>'
        "</section>"
    )


def results_notice_rows(
    summary: dict[str, Any],
    header_items: tuple[tuple[str, Any], ...],
    *,
    action_outcomes_recorded: int | None = None,
    empty_notice_parts: tuple[str, str] | None = None,
    warning_message: str = "",
    notice_inputs_precomputed: bool = False,
    include_guidance: bool = True,
    include_action_outcomes: bool = True,
    include_empty: bool = True,
    include_warnings: bool = True,
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    rewrite_message = rewrite_guidance_message(header_items)
    if include_guidance and rewrite_message:
        rows.append(("Rewrite guidance", html.escape(rewrite_message)))
    if include_action_outcomes and action_outcomes_recorded:
        count = html.escape(str(max(0, action_outcomes_recorded)))
        rows.append(("Action outcomes", f'<a href="/outcomes">{count}</a> recorded'))
    if include_empty:
        if not notice_inputs_precomputed:
            empty_notice_parts = batch_empty_notice_parts(summary)
        if empty_notice_parts:
            rows.append(empty_notice_parts)
    if include_warnings:
        if not notice_inputs_precomputed:
            warning_message = scan_warning_message(summary)
        if warning_message:
            rows.append(("Scan warnings", warning_message))
    return rows


def scan_volume_summary(header_items: tuple[tuple[str, Any], ...], *, language: str = "en") -> str:
    del language
    metrics = header_metric_map(header_items)
    scanned = metrics.get("CM inspected") or metrics.get("total") or ""
    value = "" if scanned is None else str(scanned).strip()
    return f"Scanned {value}" if value else ""


def header_metric_map(header_items: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {str(label): value for label, value in header_items}


def render_batch_scope_note(summary: dict[str, Any]) -> str:
    return render_batch_scan_details(summary)


def render_batch_scan_details(
    summary: dict[str, Any],
    header_items: tuple[tuple[str, Any], ...] = (),
    *,
    compact: bool = False,
    workload_history: Any = None,
    language: str = "en",
) -> str:
    if compact:
        parts = scan_context_coverage_parts(
            summary,
            header_items,
            workload_history=workload_history,
        )
    else:
        parts = list(recent_scan_scope_parts(summary))
        parts.extend(scan_detail_metric_parts(header_items))
        parts.extend(profile_reuse_scan_parts(summary))
    if not parts:
        return ""
    del language
    items = "".join(f"<span>{html.escape(part)}</span>" for part in parts)
    if compact:
        return (
            '<div class="batch-context-block batch-context-scan-details" aria-label="Coverage">'
            '<div class="batch-context-title">Coverage</div>'
            f'<div class="batch-detail-grid" aria-label="Coverage">{items}</div>'
            "</div>"
        )
    return (
        '<details class="batch-scan-details">'
        "<summary>Coverage</summary>"
        f'<div class="batch-detail-grid" aria-label="Coverage">{items}</div>'
        "</details>"
    )


def scan_detail_metric_parts(header_items: tuple[tuple[str, Any], ...]) -> list[str]:
    metrics = header_metric_map(header_items)
    labels = (
        ("total", "Result rows"),
        ("analyzed", "Analyzed"),
        ("CM inspected", "Scanned summaries"),
        ("metadata", "Metadata contexts"),
    )
    parts: list[str] = []
    for key, label in labels:
        raw_value = metrics.get(key)
        value = "" if raw_value is None else str(raw_value).strip()
        if value:
            parts.append(f"{label}: {value}")
    return parts


def scan_context_coverage_parts(
    summary: dict[str, Any],
    header_items: tuple[tuple[str, Any], ...],
    *,
    workload_history: Any = None,
) -> list[str]:
    if is_online_history_summary(summary):
        return online_history_scan_context_coverage_parts(
            summary,
            header_items,
            workload_history=workload_history,
        )
    metrics = header_metric_map(header_items)
    scanned = coverage_metric_text(
        metrics.get("CM inspected") or summary.get("summaries_inspected")
    )
    analyzed = coverage_metric_text(metrics.get("analyzed") or summary.get("selected_count"))
    rows = coverage_metric_text(metrics.get("total"))
    parts: list[str] = []
    if scanned and analyzed:
        coverage = f"Scanned {scanned} summaries -> Analyzed {analyzed} cases"
        if rows:
            coverage = f"{coverage} ({rows} rows)"
        parts.append(coverage)
    elif scanned:
        parts.append(f"Scanned {scanned} summaries")
    elif analyzed:
        parts.append(f"Analyzed {analyzed} cases")
    elif rows:
        parts.append(f"Result rows: {rows}")
    metadata = coverage_metric_text(metrics.get("metadata"))
    if metadata:
        parts.append(f"Metadata contexts: {metadata}")
    parts.extend(compact_scan_context_scope_parts(summary))
    history = workload_history_context_text(workload_history)
    if history:
        parts.append(history)
    return parts


def online_history_scan_context_coverage_parts(
    summary: dict[str, Any],
    header_items: tuple[tuple[str, Any], ...],
    *,
    workload_history: Any = None,
) -> list[str]:
    metrics = header_metric_map(header_items)
    retained = coverage_metric_text(
        metrics.get("CM inspected") or summary.get("summaries_inspected")
    )
    rows = coverage_metric_text(metrics.get("total") or summary.get("selected_count"))
    parts: list[str] = []
    if retained and rows:
        parts.append(f"Retained {retained} summaries -> Showing {rows} rows")
    elif retained:
        parts.append(f"Retained {retained} summaries")
    elif rows:
        parts.append(f"Showing {rows} rows")
    analyzed = online_history_profile_status_count(summary, "analyzed")
    if analyzed:
        ready = numeric_count(summary.get("history_details_ready_count"))
        parts.append(f"Profile analysis ready: {ready}/{analyzed}")
    parts.extend(compact_scan_context_scope_parts(summary))
    history = workload_history_context_text(workload_history)
    if history:
        parts.append(history)
    return parts


def compact_scan_context_scope_parts(summary: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    summaries = coverage_metric_text(summary.get("summaries_inspected"))
    if summary.get("cm_summary_safety_cap_hit"):
        cap = recent_scan_too_broad_limit_text(summary) or summaries
        if cap:
            parts.append(f"Partial CM scan: cap {cap}")
    if summary.get("only_running"):
        parts.append("Status: running only")
    cluster_context = compact_cluster_event_context(summary)
    if cluster_context:
        parts.append(cluster_context)
    parts.extend(profile_reuse_scan_parts(summary))
    return parts


def profile_reuse_scan_parts(summary: dict[str, Any]) -> list[str]:
    reused = numeric_count(summary.get("profile_reused_case_count"))
    if reused <= 0:
        reuse = summary.get("profile_reuse")
        if isinstance(reuse, dict):
            status_counts = reuse.get("status_counts")
            if isinstance(status_counts, dict):
                reused = numeric_count(status_counts.get("reused"))
    if reused <= 0:
        return []
    return [f"Reused analyzed profiles: {reused}"]


def compact_cluster_event_context(summary: dict[str, Any]) -> str:
    if not summary.get("collect_cm_events"):
        return ""
    context = summary.get("cluster_context")
    if not isinstance(context, dict):
        return "Cluster event context: unavailable"
    status = coverage_metric_text(context.get("status")) or "inconclusive"
    signal_counts = context.get("signal_counts")
    signal_total = 0
    if isinstance(signal_counts, dict):
        for value in signal_counts.values():
            signal_total += numeric_count(value)
    if signal_total:
        return f"Cluster event context: {status}, signals {signal_total}"
    return f"Cluster event context: {status}"


def coverage_metric_text(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"", "none", "unknown"} else text


def render_optimizer_funnel_note(header_items: tuple[tuple[str, Any], ...]) -> str:
    message = rewrite_guidance_message(header_items)
    if not message:
        return ""
    return (
        f'<div class="batch-note"><strong>Rewrite guidance:</strong> {html.escape(message)}</div>'
    )


def rewrite_guidance_message(header_items: tuple[tuple[str, Any], ...]) -> str:
    metrics = {str(label): numeric_count(value) for label, value in header_items}
    if metrics.get("optimization", 0) <= 0:
        return ""
    return "Open Details for the supported next step, verification anchor, and rewrite scope."


def render_batch_empty_note(summary: dict[str, Any]) -> str:
    parts = batch_empty_notice_parts(summary)
    if not parts:
        return ""
    heading, message = parts
    return f'<div class="batch-note"><strong>{html.escape(heading)}:</strong> {message}</div>'


def batch_empty_notice_parts(summary: dict[str, Any]) -> tuple[str, str] | None:
    return batch_empty_notice_parts_from_message(
        summary,
        recent_scan_empty_message(summary, case_count=len(materialized_case_entries(summary))),
    )


def batch_empty_notice_parts_from_message(
    summary: dict[str, Any],
    message: str,
) -> tuple[str, str] | None:
    if not message:
        return None
    heading = "Partial scan" if summary.get("scan_too_broad") else "No cases selected"
    return heading, html.escape(message)


def recent_scan_too_broad_message(summary: dict[str, Any]) -> str | None:
    if not summary.get("scan_too_broad"):
        return None
    cap = recent_scan_too_broad_limit_text(summary)
    cap_text = f" ({cap})" if cap else ""
    return (
        f"The CM query summary scan cap{cap_text} was reached before discovery completed. "
        "This table shows only the bounded partial result; use a smaller Search depth or add "
        "user, pool, or query type filters for complete coverage."
    )


def recent_scan_too_broad_limit_text(summary: dict[str, Any]) -> str:
    if summary.get("cm_summary_raw_scan_cap_hit"):
        raw_cap = coverage_metric_text(summary.get("cm_summary_raw_scan_cap"))
        if raw_cap:
            return raw_cap
    return (
        coverage_metric_text(summary.get("cm_summary_safety_cap"))
        or coverage_metric_text(summary.get("cm_inspect_limit"))
        or coverage_metric_text(summary.get("summaries_inspected"))
    )


def batch_result_empty_message(
    rows: tuple[RecentScanCaseRowView, ...],
    active_group: str,
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
    result_filter_toggles: tuple[ResultFilterToggle, ...] = (),
) -> str:
    group_label = QUERY_GROUPS[active_group][0].lower()
    spill_suffix = " with spill evidence" if only_with_spills else ""
    if not rows:
        return f"No {group_label}{spill_suffix} were found in the configured batch summary."
    filter_labels = active_recent_scan_result_filter_labels(
        result_filters,
        toggles=result_filter_toggles,
    )
    if filter_labels:
        label_text = ", ".join(filter_labels)
        return (
            f"No {group_label}{spill_suffix} matched the active result filters "
            f"({label_text}). Clear those filters to see all rows in this group."
        )
    if only_with_spills:
        if active_group == "bad":
            return "No rows needing attention with spill evidence matched this result filter. Clear the spill filter to see all rows in this group."
        if active_group == "suspicious":
            return "No rows worth reviewing with spill evidence matched this result filter. Clear the spill filter to see all rows in this group."
        if active_group == "optimization":
            return "No rewrite opportunities with spill evidence matched this result filter. Clear the spill filter to see all rows in this group."
        if active_group == "stats":
            return "No stats-to-check rows with spill evidence matched this result filter. Clear the spill filter to see all rows in this group."
        if active_group == "workloads":
            return "No repeated workload groups with spill evidence matched this result filter. Clear the spill filter to see all workload groups."
        if active_group == "regressions":
            return "No regressed workload rows with spill evidence matched this result filter. Clear the spill filter to see all rows in this group."
        return f"No {group_label} with spill evidence matched this result filter. Clear the spill filter to see all rows in this group."
    if active_group == "bad":
        return "No queries requiring attention were found. Check the other result groups for lower-priority follow-up work."
    if active_group == "suspicious":
        return "No medium-priority rows were found. Needs attention, rewrite, or stats groups may still contain follow-up work."
    if active_group == "optimization":
        return "No medium/high rewrite opportunities were found. Query Doctor did not identify a supported query-shape review opportunity in this scan."
    if active_group == "stats":
        return "No medium/high stats-to-check candidates were found. Query Doctor did not find enough stats evidence plus runtime symptoms for this scan."
    if active_group == "workloads":
        return "No repeated workload groups were found. This scan may still contain one-off query findings."
    if active_group == "regressions":
        return "No regressed workload rows were found. Enable workload history or review repeated workloads for current-scan impact."
    return f"No {group_label} were found in the configured batch summary."


def batch_result_empty_cell_html(
    message: str,
    *,
    clear_result_filters_href: str = "",
    actions: tuple[tuple[str, str], ...] = (),
) -> str:
    content = f'<span class="empty-cell-message">{html.escape(message)}</span>'
    links = actions
    if clear_result_filters_href and not links:
        links = (("Clear filters", clear_result_filters_href),)
    if not links:
        return content
    rendered_links = "".join(
        '<a class="empty-cell-action" '
        f'href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
        for label, href in links
    )
    return f'{content}<span class="empty-cell-actions">{rendered_links}</span>'


def empty_result_action_links(
    active_group: str,
    *,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    extra_query: dict[str, str] | None,
    base_path: str,
    include_clear_filters: bool,
) -> tuple[tuple[str, str], ...]:
    normalized_group = normalize_query_group(active_group)
    actions: list[tuple[str, str]] = []
    if include_clear_filters:
        actions.append(
            (
                "Clear filters",
                empty_result_href(
                    normalized_group,
                    only_with_spills=only_with_spills,
                    extra_query=extra_query,
                    clear_result_filters=True,
                    base_path=base_path,
                ),
            )
        )
    if only_with_spills:
        actions.append(
            (
                "Remove spill filter",
                empty_result_href(
                    normalized_group,
                    only_with_spills=False,
                    extra_query=extra_query,
                    base_path=base_path,
                ),
            )
        )
    if normalized_group != "all":
        actions.append(
            (
                "Show All analyzed",
                empty_result_href(
                    "all",
                    only_with_spills=only_with_spills,
                    extra_query=extra_query,
                    base_path=base_path,
                    clear_result_filters=not active_recent_scan_result_filter_count(result_filters),
                ),
            )
        )
    return tuple(actions)


def empty_result_href(
    active_group: str,
    *,
    only_with_spills: bool,
    extra_query: dict[str, str] | None,
    base_path: str,
    clear_result_filters: bool = False,
) -> str:
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    if extra_query:
        safe_query = safe_extra_result_query(extra_query)
        safe_query.pop("query_group", None)
        query.update(safe_query)
    query.pop(RESULTS_PAGE_PARAM, None)
    if clear_result_filters:
        for result_param in RESULT_FILTER_PARAMS:
            query.pop(result_param, None)
    if only_with_spills:
        query["only_with_spills"] = "on"
    else:
        query.pop("only_with_spills", None)
    normalized_base = base_path if str(base_path or "").startswith("/") else "/"
    return f"{normalized_base}?{urlencode(query)}#recent-results"


def render_results_table_legend(active_group: str, *, language: str = "en") -> str:
    del language
    normalized = normalize_query_group(active_group)
    if normalized == "optimization":
        items = (
            ("Finding", "Rewrite signal"),
            ("Candidate", "Ranked opportunity"),
            ("Rewrite support", "Draft or review"),
        )
    elif normalized == "stats":
        items = (
            ("Finding", "Stats signal"),
            ("Candidate", "Follow-up rank"),
            ("Need", "Stats area"),
        )
    elif normalized == "workloads":
        items = (
            ("Workload", "Grouped fingerprint; opens Workload Details"),
            ("Priority", "Highest group severity"),
            ("p95", "Observed group latency"),
            ("Total impact", "Observed runtime sum"),
        )
    elif normalized == "regressions":
        items = (
            ("Finding", "Workload signal"),
            ("Runs", "Similar queries"),
            ("Workload p95", "Current latency"),
            ("Regression", "History signal"),
        )
    else:
        items = (
            ("Finding", "Main signal; opens selected-case Details"),
            ("Priority", "Label + score"),
        )
    rendered_items = "".join(
        f"<li><strong>{html.escape(label)}</strong><span>{html.escape(description)}</span></li>"
        for label, description in items
    )
    return (
        '<details class="batch-scan-details batch-table-key" aria-label="Results table legend">'
        "<summary>Table key</summary>"
        '<div class="batch-table-legend">'
        f'<ul class="batch-table-legend-list">{rendered_items}</ul>'
        "</div></details>"
    )


def render_batch_warning_note(summary: dict[str, Any]) -> str:
    message = scan_warning_message(summary)
    if not message:
        return ""
    return f'<div class="batch-note"><strong>Scan warnings:</strong> {message}</div>'


def scan_warning_message(summary: dict[str, Any]) -> str:
    return scan_warning_message_from_warnings(recent_scan_warning_messages(summary))


def scan_warning_message_from_warnings(warnings: tuple[str, ...]) -> str:
    if not warnings:
        return ""
    return "; ".join(html.escape(warning) for warning in warnings)


def rows_by_workload_fingerprint(
    rows: tuple[RecentScanCaseRowView, ...],
) -> dict[str, tuple[RecentScanCaseRowView, ...]]:
    grouped: dict[str, list[RecentScanCaseRowView]] = {}
    for row in rows:
        if row.workload_fingerprint:
            grouped.setdefault(row.workload_fingerprint, []).append(row)
    return {fingerprint: tuple(group_rows) for fingerprint, group_rows in grouped.items()}


def workload_groups_for_table(
    groups: tuple[RecentScanWorkloadGroupView, ...],
    rows_by_workload: dict[str, tuple[RecentScanCaseRowView, ...]],
    *,
    only_with_spills: bool = False,
    result_filters: RecentScanResultFilters | None = None,
) -> tuple[RecentScanWorkloadGroupView, ...]:
    normalized_filters = normalize_recent_scan_result_filters(result_filters)
    filter_active = bool(active_recent_scan_result_filter_count(normalized_filters))
    selected: list[RecentScanWorkloadGroupView] = []
    for group in groups:
        if group.member_count <= 1:
            continue
        group_rows = rows_by_workload.get(group.fingerprint, ())
        if only_with_spills and not any(row.has_spill for row in group_rows):
            continue
        if filter_active and not any(
            row_matches_result_filters(row, normalized_filters) for row in group_rows
        ):
            continue
        selected.append(group)
    return tuple(selected)


def sort_workload_groups_for_result_sort(
    groups: tuple[RecentScanWorkloadGroupView, ...],
    result_sort: str,
) -> tuple[RecentScanWorkloadGroupView, ...]:
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort == "duration":
        return tuple(
            sorted(
                groups,
                key=lambda group: (
                    -numeric_value(group.duration_sec_p95),
                    -numeric_value(group.duration_sec_total),
                    group.fingerprint,
                ),
            )
        )
    if normalized_sort == "priority":
        severity_order = {"failed": 4, "high": 3, "suspicious": 2, "clean": 1}
        return tuple(
            sorted(
                groups,
                key=lambda group: (
                    -severity_order.get(str(group.score_top or "").strip().lower(), 0),
                    -workload_total_impact(group),
                    group.fingerprint,
                ),
            )
        )
    if normalized_sort == "start":
        return groups
    return tuple(
        sorted(
            groups,
            key=lambda group: (
                -workload_total_impact(group),
                -group.member_count,
                group.fingerprint,
            ),
        )
    )


def render_workload_group_table_row(
    rank: int,
    group: RecentScanWorkloadGroupView,
    *,
    group_rows: tuple[RecentScanCaseRowView, ...],
    workload_base_path: str = "/batch/workload",
    language: str = "en",
) -> str:
    del language
    href = workload_href(group.fingerprint, workload_base_path=workload_base_path)
    row_attrs = f'class="batch-row" data-href="{href}" tabindex="0"'
    cells = [
        compact_cell(rank),
        workload_group_summary_cell(group, href),
        workload_group_score_cell(group.score_top),
        duration_cell(group.duration_sec_p95),
        compact_cell(display_seconds_label(workload_total_impact(group))),
        user_cell(top_owner_summary(group_rows)),
    ]
    return f"<tr {row_attrs}>{''.join(cells)}</tr>"


def workload_group_summary_cell(group: RecentScanWorkloadGroupView, href: str = "") -> str:
    title = f"Repeated workload: {group.member_count} similar queries"
    details = [
        group.fingerprint_short,
        group.shape_summary,
        f"tables {group.table_summary}" if group.table_summary else "",
    ]
    baseline = workload_group_baseline_text(group)
    if baseline:
        details.append(baseline)
    detail_text = "; ".join(str(part).strip() for part in details if str(part or "").strip())
    label = escape_value(title)
    if href:
        label = f'<a class="batch-finding-link" href="{href}">{label}</a>'
    return (
        '<td class="batch-cell--summary">'
        f"<strong>{label}</strong>"
        f"<span>{escape_value(detail_text)}.</span>"
        "</td>"
    )


def workload_group_baseline_text(group: RecentScanWorkloadGroupView) -> str:
    if group.baseline_sample_count <= 0:
        return ""
    baseline = str(group.baseline_duration_sec_p95 or "").strip()
    baseline_text = f"baseline p95 {baseline}s" if baseline else "baseline p95 unknown"
    return f"{baseline_text}; regression {group.regression}; n={group.baseline_sample_count}"


def workload_group_score_cell(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    class_name = {
        "failed": "batch-severity--failed",
        "high": "batch-severity--high",
        "suspicious": "batch-severity--suspicious",
        "clean": "batch-status--neutral",
        "unknown": "batch-status--warning",
    }.get(normalized, "batch-status--neutral")
    if normalized == "failed":
        return badge_cell(
            workload_group_label(normalized), class_name, cell_class="batch-cell--status"
        )
    return (
        '<td class="batch-cell--compact batch-cell--badge batch-cell--priority">'
        f'<span class="batch-priority {class_name}">'
        '<span class="batch-priority-dot" aria-hidden="true"></span>'
        f"{html.escape(workload_group_label(normalized))}</span>"
        "</td>"
    )


def workload_group_label(value: Any) -> str:
    text = str(value or "unknown").strip().replace("_", " ")
    return text.title() if text else "Unknown"


def render_batch_case_row(
    rank: int,
    case: dict[str, Any] | RecentScanCaseRowView,
    *,
    details_base_path: str = "/batch/case",
    workload_base_path: str | None = None,
    query_group: str = DEFAULT_QUERY_GROUP,
    language: str = "en",
) -> str:
    view = (
        case
        if isinstance(case, RecentScanCaseRowView)
        else present_recent_scan_case_row(rank, case)
    )
    row_class = "batch-row batch-row--failed" if row_has_failure(view) else "batch-row"
    row_attrs = f'class="{row_class}"'
    if view.case_id:
        href = f"{details_base_path.rstrip('/')}/{html.escape(view.case_id, quote=True)}"
        row_attrs += f' data-href="{href}" tabindex="0"'
    normalized = normalize_query_group(query_group)
    if normalized == "optimization":
        cells = [
            compact_cell(rank),
            summary_cell(view, query_group=normalized, language=language),
            query_id_cell(view, workload_base_path=workload_base_path),
            user_cell(view.user),
            candidate_cell(view.optimization_tier),
            duration_cell(view.duration_sec),
            compact_cell(view.optimization_impact.title()),
            compact_cell(view.optimization_confidence.title()),
            optimizer_rewrite_support_cell(
                view.optimizer_rewrite_support,
                view.optimizer_rewrite_support_label,
                view.optimizer_rewrite_support_reason,
            ),
        ]
    elif normalized == "stats":
        cells = [
            compact_cell(rank),
            summary_cell(view, query_group=normalized, language=language),
            query_id_cell(view, workload_base_path=workload_base_path),
            user_cell(view.user),
            candidate_cell(view.stats_tier),
            duration_cell(view.duration_sec),
            reason_cell(stats_need_label(view.stats_need_type)),
            compact_cell(view.stats_speed_benefit.title()),
            compact_cell(view.stats_confidence.title()),
        ]
    elif normalized in {"workloads", "regressions", "frequent_short"}:
        primary = (
            view.primary_bottleneck.summary
            if not view.primary_bottleneck.unavailable
            else "Unknown"
        )
        group_metric = (
            workload_group_impact_cell(view)
            if normalized == "frequent_short"
            else workload_regression_cell(view)
        )
        cells = [
            compact_cell(rank),
            summary_cell(view, query_group=normalized, language=language),
            query_id_cell(view, workload_base_path=workload_base_path),
            user_cell(view.user),
            compact_cell(view.workload_group_member_count),
            duration_cell(view.duration_sec),
            duration_cell(view.workload_group_duration_sec_p95),
            group_metric,
            reason_cell(primary),
        ]
    else:
        cells = [
            compact_cell(rank),
            summary_cell(
                view,
                query_group=normalized,
                details_base_path=details_base_path,
                language=language,
            ),
            query_id_cell(view, workload_base_path=workload_base_path),
            user_cell(view.user),
            score_cell(view),
            duration_cell(view.duration_sec),
        ]
    return f"<tr {row_attrs}>{''.join(cells)}</tr>"


def row_has_failure(view: RecentScanCaseRowView) -> bool:
    return any(
        str(value).lower() == "failed" for value in (view.collection_status, view.analysis_status)
    )


def query_id_cell(view: Any, *, workload_base_path: str | None = None) -> str:
    if isinstance(view, RecentScanCaseRowView):
        query_id = view.query_id
        badge = workload_badge(view, workload_base_path=workload_base_path)
    else:
        query_id = view
        badge = ""
    escaped = escape_value(query_id)
    return f'<td class="batch-cell--query-id">{escaped}{badge}</td>'


def workload_badge(
    view: RecentScanCaseRowView,
    *,
    workload_base_path: str | None = None,
) -> str:
    if not view.workload_fingerprint_short:
        return ""
    title_parts = ["Workload group fingerprint"]
    if view.workload_group_member_count > 1:
        title_parts.append(f"{view.workload_group_member_count} similar queries in this scan")
    title = "; ".join(title_parts)
    label = escape_value(view.workload_fingerprint_short)
    title_attr = escape_value(title)
    if workload_base_path and view.workload_fingerprint and view.workload_group_member_count > 1:
        href = (
            f"{html.escape(workload_base_path.rstrip('/'), quote=True)}/"
            f"{html.escape(view.workload_fingerprint, quote=True)}"
        )
        return (
            ' <a class="batch-mini-badge batch-status--neutral" '
            f'href="{href}" title="{title_attr}">{label}</a>'
        )
    return (
        f' <span class="batch-mini-badge batch-status--neutral" title="{title_attr}">{label}</span>'
    )


def user_cell(user: Any) -> str:
    return f'<td class="batch-cell--user">{escape_value(user)}</td>'


def reason_cell(value: Any) -> str:
    return f'<td class="batch-cell--reason">{escape_value(value)}</td>'


def score_cell(view: RecentScanCaseRowView, *, language: str = "en") -> str:
    del language
    score = view.score
    if view.score_severity == "failed":
        class_name = "batch-severity--failed"
        label = "Failed"
    elif view.score_severity == "high":
        class_name = "batch-severity--high"
        label = "High"
    elif view.score_severity == "suspicious":
        class_name = "batch-severity--suspicious"
        label = "Medium"
    else:
        class_name = "batch-severity--clean"
        label = "Clean"
    if view.score_severity == "failed":
        return badge_cell(
            f"{label} · {display_score(score)}",
            class_name,
            cell_class="batch-cell--priority",
            badge_class="batch-priority-badge",
        )
    return (
        '<td class="batch-cell--compact batch-cell--badge batch-cell--priority">'
        f'<span class="batch-priority {class_name}">'
        '<span class="batch-priority-dot" aria-hidden="true"></span>'
        f"{html.escape(label)} · {html.escape(str(display_score(score)))}</span>"
        "</td>"
    )


def candidate_cell(tier: Any, *, language: str = "en") -> str:
    del language
    normalized = str(tier or "not_likely").lower()
    class_name = {
        "high": "batch-severity--high",
        "medium": "batch-severity--suspicious",
        "low": "batch-status--neutral",
        "unknown": "batch-status--warning",
    }.get(normalized, "batch-status--neutral")
    label = candidate_label(tier)
    return badge_cell(label, class_name, cell_class="batch-cell--candidate")


def workload_regression_cell(view: RecentScanCaseRowView, *, language: str = "en") -> str:
    del language
    class_name = {
        "strong": "batch-severity--high",
        "mild": "batch-severity--suspicious",
        "none": "batch-status--neutral",
        "unknown": "batch-status--neutral",
    }.get(view.workload_regression, "batch-status--neutral")
    return badge_cell(view.workload_regression.title(), class_name, cell_class="batch-cell--status")


def workload_group_impact_cell(view: RecentScanCaseRowView) -> str:
    impact = workload_group_impact(view)
    label = display_seconds_label(impact) if impact > 0 else "unknown"
    return f'<td class="batch-cell--compact">{escape_value(label)}</td>'


def duration_cell(value: Any) -> str:
    return f'<td class="batch-cell--compact batch-cell--duration">{escape_value(display_seconds_label(value))}</td>'


def render_case_not_ready_state(view: RecentScanCaseRowView) -> str:
    label, badge_class = case_not_ready_state(view)
    return (
        f'<span class="batch-mini-badge batch-mini-badge--status {badge_class} '
        f'batch-finding-state">{html.escape(label)}</span>'
    )


def case_not_ready_state(view: RecentScanCaseRowView) -> tuple[str, str]:
    status = str(view.analysis_status or "").strip().lower()
    return {
        "profile_not_collected": ("Not selected", "batch-status--neutral"),
        "profile_pending": ("Queued", "batch-status--warning"),
        "profile_processing": ("Analyzing", "batch-status--warning"),
        "profile_retry_pending": ("Retry queued", "batch-status--warning"),
        "details_unavailable": ("Details unavailable", "batch-status--warning"),
        "failed": ("Analysis failed", "batch-severity--failed"),
    }.get(status, ("Not ready", "batch-status--neutral"))


def display_seconds_label(value: Any) -> str:
    seconds = numeric_value(value)
    if seconds <= 0:
        return "unknown"
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:.1f}s"


def split_finding_confidence(title: str) -> tuple[str, str]:
    text = str(title or "").strip()
    if text.endswith(")") and "(" in text:
        head, _, tail = text.rpartition("(")
        if tail[:-1].strip().lower().endswith("confidence"):
            return head.strip(), tail[:-1].strip()
    return text, ""


def render_finding_title(
    view: RecentScanCaseRowView,
    title: Any,
    *,
    details_base_path: str = "/batch/case",
    linked: bool = False,
) -> str:
    head, confidence = split_finding_confidence(str(title or ""))
    label = escape_value(head)
    if linked and view.case_id:
        base_path = html.escape(details_base_path.rstrip("/"), quote=True)
        href = f"{base_path}/{html.escape(view.case_id, quote=True)}"
        label = f'<a class="batch-finding-link" href="{href}">{label}</a>'
    confidence_html = (
        f'<span class="batch-finding-confidence">{escape_value(confidence)}</span>'
        if confidence
        else ""
    )
    return f"<strong>{label}{confidence_html}</strong>"


def summary_cell(
    view: RecentScanCaseRowView,
    *,
    query_group: str = DEFAULT_QUERY_GROUP,
    details_base_path: str = "/batch/case",
    language: str = "en",
) -> str:
    normalized = normalize_query_group(query_group)
    default_table_group = normalized in {"all", "bad", "suspicious"}
    reason_html = (
        f"<span>{escape_value(localize_diagnostic_text(view.reason_text, language))}</span>"
        if view.reason_text and not default_table_group
        else ""
    )
    primary_html = (
        "<span>"
        f"{escape_value(localize_diagnostic_text('Primary:', language))} "
        f"{escape_value(localize_diagnostic_text(view.primary_bottleneck.summary, language))}."
        "</span>"
        if not view.primary_bottleneck.unavailable and not default_table_group
        else ""
    )
    title = localize_diagnostic_text(view.signal_summary, language)
    if (
        default_table_group
        and not view.primary_bottleneck.unavailable
        and view.primary_bottleneck.label != "Unknown"
    ):
        title = localize_diagnostic_text(view.primary_bottleneck.summary, language)
    detail_html = ""
    if normalized == "optimization":
        title = (
            f"{localize_diagnostic_text('Query optimization candidate:', language)} "
            f"{localize_diagnostic_text(candidate_label(view.optimization_tier), language)}"
        )
        why = (
            f"{localize_diagnostic_text('Why:', language)} "
            f"{localize_diagnostic_text(view.optimization_summary, language)}"
            if view.optimization_summary
            else localize_diagnostic_text("Why: query-shape evidence", language)
        )
        review = (
            f" {localize_diagnostic_text('Review:', language)} "
            f"{localize_diagnostic_text(view.optimization_review_areas, language)}."
            if view.optimization_review_areas
            else ""
        )
        facts = (
            f" {localize_diagnostic_text('Facts:', language)} "
            f"{localize_diagnostic_text(view.optimizer_fact_summary, language)}."
            if view.optimizer_fact_summary
            else ""
        )
        guardrails = (
            f" {localize_diagnostic_text('Guardrails:', language)} "
            f"{localize_diagnostic_text(view.optimizer_guardrail_summary, language)}."
            if view.optimizer_guardrail_summary
            else ""
        )
        detail_html = f"<span>{escape_value(why)}.{escape_value(review)}{escape_value(facts)}{escape_value(guardrails)}</span>"
        source_location_html = render_row_source_location_chips(view, "query_optimization")
    elif normalized == "stats":
        title = (
            f"{localize_diagnostic_text('Stats candidate:', language)} "
            f"{localize_diagnostic_text(candidate_label(view.stats_tier), language)}"
        )
        why = (
            f"{localize_diagnostic_text('Why:', language)} "
            f"{localize_diagnostic_text(view.stats_summary, language)}"
            if view.stats_summary
            else localize_diagnostic_text("Why: stats-planning evidence", language)
        )
        review = (
            f" {localize_diagnostic_text('Review:', language)} "
            f"{localize_diagnostic_text(view.stats_review_areas, language)}"
            if view.stats_review_areas
            else ""
        )
        detail_html = f"<span>{escape_value(why)}.{escape_value(review)}</span>"
    elif normalized in {"workloads", "regressions", "frequent_short"}:
        runs = view.workload_group_member_count
        if normalized == "regressions":
            title = (
                f"{localize_diagnostic_text('Regressed workload:', language)} "
                f"{localize_diagnostic_text(view.workload_regression.title(), language)}"
            )
        elif normalized == "frequent_short":
            title = localize_diagnostic_text(
                f"Frequent short workload: {runs} similar queries",
                language,
            )
        else:
            title = localize_diagnostic_text(
                f"Repeated workload: {runs} similar queries",
                language,
            )
        group_p95 = str(view.workload_group_duration_sec_p95 or "").strip()
        details = [
            f"{runs} similar queries",
            f"workload p95 {group_p95}s" if group_p95 else "workload p95 unknown",
        ]
        if normalized == "frequent_short":
            impact = workload_group_impact(view)
            if impact > 0:
                details.append(f"current scan impact about {display_seconds_label(impact)}")
        if view.workload_baseline_sample_count > 0:
            baseline = str(view.workload_baseline_duration_sec_p95 or "").strip()
            baseline_text = f"baseline p95 {baseline}s" if baseline else "baseline p95 unknown"
            details.append(
                f"{baseline_text}; regression {view.workload_regression}; n={view.workload_baseline_sample_count}"
            )
        detail_html = (
            f"<span>{escape_value(localize_diagnostic_text('; '.join(details), language))}.</span>"
        )
    default_table_group = normalize_query_group(query_group) in {"all", "bad", "suspicious"}
    title_html = render_finding_title(
        view,
        title,
        details_base_path=details_base_path,
        linked=default_table_group,
    )
    not_ready_html = (
        render_case_not_ready_state(view) if default_table_group and not view.case_id else ""
    )
    return (
        '<td class="batch-cell--summary">'
        f"{title_html}"
        f"{not_ready_html}"
        f"{primary_html}"
        f"{detail_html}"
        f"{source_location_html if normalized == 'optimization' else ''}"
        f"{reason_html}"
        "</td>"
    )


def render_row_source_location_chips(view: RecentScanCaseRowView, group: str) -> str:
    locators = view.source_locators.get(group, ()) if view.source_locators else ()
    return render_source_location_chips(locators, limit=2)


def candidate_label(value: Any, *, language: str = "en") -> str:
    del language
    return str(value or "not_likely").replace("_", " ").title()


def optimizer_review_scope_text(view: RecentScanCaseRowView) -> str:
    if not view.optimizer_fact_summary and not view.optimizer_guardrail_summary:
        return view.optimization_review_areas or "Review query shape"
    parts = []
    if view.optimization_review_areas:
        parts.append(f"Review: {view.optimization_review_areas}")
    if view.optimizer_fact_summary:
        parts.append(f"Facts: {view.optimizer_fact_summary}")
    if view.optimizer_guardrail_summary:
        parts.append(f"Guardrails: {view.optimizer_guardrail_summary}")
    return ". ".join(parts) or "Review query shape"


def stats_need_label(value: Any, *, language: str = "en") -> str:
    del language
    labels = {
        "table_stats": "table/partition stats",
        "column_stats": "column stats",
        "table_and_column_stats": "table/partition stats first, then column stats",
        "stats_possibly_stale": "stats freshness unknown",
        "insufficient_metadata": "insufficient metadata",
        "not_likely_stats_issue": "not likely a stats issue",
    }
    return labels.get(str(value), str(value))


def optimizer_rewrite_support_cell(
    status: Any, label: Any, reason: Any, *, language: str = "en"
) -> str:
    del language
    display_label, class_name, title = optimizer_rewrite_support_view(status, label, reason)
    return badge_cell(display_label, class_name, title=title, cell_class="batch-cell--status")


def optimizer_rewrite_support_view(status: Any, label: Any, reason: Any) -> tuple[str, str, str]:
    normalized = str(status or "unknown").strip().lower()
    fallback_labels = {
        "sql_draft_supported": ("SQL eligible", "batch-status--ok"),
        "sql_draft_attemptable": ("Recipe found", "batch-status--warning"),
        "recipe_detected": ("Recipe found", "batch-status--warning"),
        "draft_disabled": ("Draft disabled", "batch-status--neutral"),
        "guidance_only": ("Guidance only", "batch-status--neutral"),
        "source_unavailable": ("No source", "batch-status--neutral"),
        "not_candidate": ("Not applicable", "batch-status--neutral"),
        "unknown": ("Unknown", "batch-status--neutral"),
    }
    fallback_label, class_name = fallback_labels.get(normalized, fallback_labels["unknown"])
    title_label = str(label or fallback_label).strip() or fallback_label
    if normalized == "not_candidate":
        title_label = "Optimizer not applicable"
    if title_label.lower() == "human review only":
        fallback_label = "Human review"
    if title_label.lower() == "review guidance only":
        fallback_label = "Review only"
    title_reason = str(reason or "").strip()
    title = f"{title_label}: {title_reason}" if title_reason else title_label
    return fallback_label, class_name, title


def metadata_cell(status: Any, *, language: str = "en") -> str:
    del language
    normalized = str(status).lower() if status is not None else "unknown"
    if normalized in {"ok", "available", "done", "collected"}:
        label = "Collected"
        class_name = "batch-status--ok"
    elif normalized == "failed":
        label = "Failed"
        class_name = "batch-status--failed"
    elif normalized in {"skipped", "not_run", "none"}:
        label = "Skipped"
        class_name = "batch-status--neutral"
    else:
        label = "Unknown"
        class_name = "batch-status--warning"
    return badge_cell(label, class_name, title=status, cell_class="batch-cell--status")


def stats_cell(status: Any, *, language: str = "en") -> str:
    del language
    normalized = str(status).lower() if status is not None else "not_checked"
    if normalized == "available":
        label = "Available"
        class_name = "batch-status--ok"
        title = "table stats available"
    elif normalized in {"missing", "not_available", "unknown"}:
        label = "Missing"
        class_name = "batch-status--failed"
        title = f"table stats {normalized}"
    elif normalized == "not_applicable":
        label = "N/A"
        class_name = "batch-status--neutral"
        title = "table stats not applicable"
    else:
        label = "Not checked"
        class_name = "batch-status--neutral"
        title = "table stats not checked"
    return badge_cell(label, class_name, title=title, cell_class="batch-cell--status")


def badge_cell(
    label: Any,
    class_name: str,
    *,
    title: Any | None = None,
    cell_class: str = "",
    badge_class: str = "",
) -> str:
    cell_classes = "batch-cell--compact batch-cell--badge"
    if cell_class:
        cell_classes += f" {cell_class}"
    badge_classes = f"batch-mini-badge batch-mini-badge--status {class_name}"
    if badge_class:
        badge_classes += f" {badge_class}"
    title_attr = f' title="{escape_value(title)}"' if title is not None else ""
    return (
        f'<td class="{cell_classes}">'
        f'<span class="{badge_classes}"{title_attr}>{escape_value(label)}</span></td>'
    )


def batch_case_details_link(case: dict[str, Any] | RecentScanCaseRowView) -> SafeHtml:
    case_id = case.case_id if isinstance(case, RecentScanCaseRowView) else batch_case_id(case)
    if case_id is None:
        return SafeHtml("")
    escaped = html.escape(case_id, quote=True)
    return SafeHtml(f'<a class="button" href="/batch/case/{escaped}">Details</a>')


def batch_case_id(case: dict[str, Any]) -> str | None:
    case_ref = str(case.get("case_ref") or "").strip().lower()
    if re.fullmatch(r"(?:recent-)?case-[0-9]{3,}", case_ref):
        return case_ref
    value = case.get("case_index")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return f"case-{parsed:03d}"
