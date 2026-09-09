"""Raw-free Query Inbox state rendering helpers."""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from query_doctor.recent.materialized_case_index import (
    SCHEMA_VERSION,
    build_materialized_case_index,
)
from query_doctor.safety.browser_display import redact_browser_display_text
from query_doctor.web.cluster_selection import (
    cluster_trino_beta_query_ready,
    cluster_trino_beta_recent_ready,
)
from query_doctor.web.operator_readiness_status import (
    MAX_OPERATOR_READINESS_ISSUE_CODES,
    project_operator_readiness_issue_code,
)
from query_doctor.web.presenters.recent_scan_models import (
    RecentScanCaseRowView,
    RecentScanSummaryView,
)
from query_doctor.web.query_inbox_time_range import normalize_query_inbox_time_range
from query_doctor.web.recent_history_inbox import (
    DEFAULT_HISTORY_VIEW,
    HISTORY_VIEW_ALL_RECENT,
    HISTORY_VIEW_DETAILS_READY,
    normalize_history_view,
    recent_history_inbox_summary_from_settings,
)
from query_doctor.web.ui.recent_scan_groups import (
    DEFAULT_RESULT_SORT,
    RESULT_SORT_LABELS,
    RESULT_SORT_PARAM,
    normalize_query_group,
    normalize_result_sort,
    query_group_counts_for_rows,
)
from query_doctor.web.ui.recent_scan_result_filters import (
    OWNER_FILTER_TAGGED,
    POOL_FILTER_TAGGED,
    RESULT_FILTER_PARAMS,
    ResultFilterToggle,
    RecentScanResultFilters,
    active_recent_scan_result_filter_labels,
    normalize_recent_scan_result_filters,
    recent_scan_result_filter_query,
    recent_scan_result_filter_toggles,
    recent_scan_result_filters_from_mapping,
)
from query_doctor.web.ui.recent_scan_view_cache import (
    cached_recent_scan_summary_view,
    recent_scan_summary_view_for_render,
)


_MATERIALIZED_INBOX_STATES = {"ready", "partial", "stale"}
INBOX_SOURCE_PARAM = "inbox_source"
INBOX_WORKFLOW_PARAM = "inbox_workflow"
INBOX_WINDOW_PARAM = "inbox_window"
INBOX_QUERY_TYPE_PARAM = "inbox_query_type"
INBOX_FROM_PARAM = "inbox_from"
INBOX_TO_PARAM = "inbox_to"
_INBOX_SOURCE_FILTER_VALUES = {"all", "cm", "impala", "trino", "demo", "recent", "history"}
_INBOX_WORKFLOW_FILTER_VALUES = {"all", "finished", "running", "mixed"}
_INBOX_WINDOW_TEXT_VALUES = {"all", "current", "live", "synthetic"}
_INBOX_FILTER_ALL = "all"


@dataclass(frozen=True)
class QueryInboxViewPreset:
    label: str
    query_group: str
    result_sort: str = DEFAULT_RESULT_SORT
    only_with_spills: bool = False
    result_filters: RecentScanResultFilters = field(default_factory=RecentScanResultFilters)


_INBOX_VIEW_PRESETS = (
    QueryInboxViewPreset(
        label="Needs attention + duration",
        query_group="bad",
        result_sort="duration",
    ),
    QueryInboxViewPreset(
        label="Spill + impact",
        query_group="all",
        result_sort="impact",
        only_with_spills=True,
    ),
    QueryInboxViewPreset(
        label="Rewrite + priority",
        query_group="optimization",
        result_sort="priority",
    ),
    QueryInboxViewPreset(
        label="Owner/pool + priority",
        query_group="all",
        result_sort="priority",
        result_filters=RecentScanResultFilters(
            owner=OWNER_FILTER_TAGGED,
            pool=POOL_FILTER_TAGGED,
        ),
    ),
)


@dataclass(frozen=True)
class QueryInboxStatus:
    state: str
    badge_class: str
    dot_class: str
    title: str
    message: str
    metrics: tuple[tuple[str, str], ...]
    scope_items: tuple[tuple[str, str], ...] = ()
    scope_filter_groups: tuple["QueryInboxScopeFilterGroup", ...] = ()
    result_filter_toggles: tuple[ResultFilterToggle, ...] = ()
    result_rows: tuple[RecentScanCaseRowView, ...] = ()
    history_view: str = ""


@dataclass(frozen=True)
class QueryInboxFreshness:
    state: str
    age_minutes: int | None = None
    window_minutes: int | None = None

    @property
    def known(self) -> bool:
        return self.state in {"fresh", "stale"}


@dataclass(frozen=True)
class QueryInboxScopeFilters:
    source: str = _INBOX_FILTER_ALL
    workflow: str = _INBOX_FILTER_ALL
    window: str = _INBOX_FILTER_ALL
    query_type: str = _INBOX_FILTER_ALL
    from_time: str = ""
    to_time: str = ""


@dataclass(frozen=True)
class QueryInboxScopeFilterGroup:
    key: str
    label: str
    param: str
    current_value: str
    current_label: str
    current_from_time: str = ""
    current_to_time: str = ""


@dataclass(frozen=True)
class _QueryInboxCurrentScope:
    source_value: str = ""
    source_label: str = ""
    workflow_value: str = ""
    workflow_label: str = ""
    window_value: str = ""
    window_label: str = ""
    query_type_value: str = ""
    query_type_label: str = ""
    from_time_value: str = ""
    to_time_value: str = ""


def query_inbox_status_from_settings(
    settings: Any,
    *,
    job: Any | None = None,
    scope_filters: QueryInboxScopeFilters | None = None,
    history_view: str = DEFAULT_HISTORY_VIEW,
) -> QueryInboxStatus:
    if _job_is_running(job):
        return query_inbox_status_from_view(None, job=job)
    history_summary = _history_summary_if_requested(settings, scope_filters, history_view)
    if history_summary is not None:
        return query_inbox_status_from_summary(history_summary, scope_filters=scope_filters)
    summary_path = getattr(settings, "batch_summary", None)
    corpus_summary = getattr(settings, "corpus_summary", None)
    if summary_path is None and not isinstance(corpus_summary, dict):
        history_summary = recent_history_inbox_summary_from_settings(
            settings,
            history_view=history_view,
        )
        if history_summary is not None:
            return query_inbox_status_from_summary(history_summary, scope_filters=scope_filters)
        return query_inbox_status_from_view(None)
    language = getattr(settings, "language", "en")
    if isinstance(corpus_summary, dict):
        return query_inbox_status_from_summary(corpus_summary, scope_filters=scope_filters)
    try:
        payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return QueryInboxStatus(
            state="partial",
            badge_class="amber",
            dot_class="amber",
            title="Inbox unavailable",
            message="Configured materialized summary could not be read.",
            metrics=(("status", "unavailable"),),
        )
    if not isinstance(payload, dict):
        return QueryInboxStatus(
            state="partial",
            badge_class="amber",
            dot_class="amber",
            title="Inbox unavailable",
            message="Configured materialized summary is not a JSON object.",
            metrics=(("status", "unavailable"),),
        )
    summary_view = cached_recent_scan_summary_view(
        payload, summary_path=Path(summary_path), language=language
    )
    return query_inbox_status_from_summary(
        payload,
        summary_view=summary_view,
        scope_filters=scope_filters,
    )


def query_inbox_refresh_form_values_from_settings(
    settings: Any,
    *,
    scope_filters: QueryInboxScopeFilters | None = None,
    history_view: str = DEFAULT_HISTORY_VIEW,
) -> dict[str, object] | None:
    summary = _history_summary_if_requested(settings, scope_filters, history_view)
    if summary is None:
        summary = _summary_payload_from_settings(settings)
    if summary is None:
        history_summary = recent_history_inbox_summary_from_settings(
            settings,
            history_view=history_view,
        )
        if history_summary is not None:
            summary = history_summary
    if summary is None:
        return None
    filters = _normalized_scope_filters(scope_filters)
    values = query_inbox_refresh_form_values_from_summary(summary)
    values.update(_query_inbox_refresh_form_values_from_scope_filters(filters))
    cluster_key = _refresh_cluster_key_for_profile_source(
        settings,
        _refresh_profile_source_for_scope(summary, filters),
    )
    if cluster_key:
        values["cluster_key"] = cluster_key
    values.update(query_inbox_scope_filter_query(filters))
    return values or None


def query_inbox_refresh_form_values_from_summary(
    summary: Mapping[str, Any],
) -> dict[str, object]:
    index = _safe_materialized_index(summary)
    source = _mapping(index.get("source"))
    scope = _mapping(index.get("scope"))
    freshness = _mapping(index.get("freshness"))

    values: dict[str, object] = {"diagnosis_target": "recent"}
    if _safe_bool(scope.get("only_running")):
        values["scan_target"] = "running"
    elif "only_running" in scope or "include_running" in scope:
        values["scan_target"] = "finished"

    window_minutes = _first_positive_int_form_value(
        scope.get("recent_window_minutes"),
        freshness.get("recent_window_minutes"),
        summary.get("recent_window_minutes"),
    )
    if window_minutes:
        values["recent_window_minutes"] = window_minutes

    min_duration_sec = _first_non_negative_number_form_value(
        scope.get("min_duration_sec"),
        summary.get("min_duration_sec"),
    )
    if min_duration_sec is not None:
        values["min_duration_sec"] = min_duration_sec

    query_type = _safe_query_type_form_value(
        scope.get("query_type_filter") or summary.get("query_type_filter")
    )
    if query_type:
        values["query_type"] = query_type

    engine = _refresh_engine_from_profile_source(
        source.get("query_profile_source") or summary.get("query_profile_source")
    )
    if engine:
        values["engine"] = engine

    return values if len(values) > 1 else {}


def query_inbox_scope_filters_from_mapping(
    values: Mapping[str, object] | None,
) -> QueryInboxScopeFilters:
    return _normalized_scope_filters(
        QueryInboxScopeFilters(
            source=_normalize_source_filter(_mapping_first_value(values, INBOX_SOURCE_PARAM)),
            workflow=_normalize_workflow_filter(_mapping_first_value(values, INBOX_WORKFLOW_PARAM)),
            window=_normalize_window_filter(_mapping_first_value(values, INBOX_WINDOW_PARAM)),
            query_type=_normalize_query_type_filter(
                _mapping_first_value(values, INBOX_QUERY_TYPE_PARAM)
            ),
            from_time=_mapping_first_value(values, INBOX_FROM_PARAM),
            to_time=_mapping_first_value(values, INBOX_TO_PARAM),
        )
    )


def _normalized_scope_filters(
    filters: QueryInboxScopeFilters | None,
) -> QueryInboxScopeFilters:
    normalized = filters or QueryInboxScopeFilters()
    from_time, to_time = normalize_query_inbox_time_range(
        normalized.from_time,
        normalized.to_time,
    )
    workflow = _normalize_workflow_filter(normalized.workflow)
    if workflow == "running":
        from_time = to_time = ""
    window = _normalize_window_filter(normalized.window)
    if from_time and to_time:
        window = _INBOX_FILTER_ALL
    return QueryInboxScopeFilters(
        source=_normalize_source_filter(normalized.source),
        workflow=workflow,
        window=window,
        query_type=_normalize_query_type_filter(normalized.query_type),
        from_time=from_time,
        to_time=to_time,
    )


def _query_inbox_refresh_form_values_from_scope_filters(
    filters: QueryInboxScopeFilters,
) -> dict[str, object]:
    values: dict[str, object] = {}
    profile_source = _profile_source_from_scope_source_filter(filters.source)
    engine = _refresh_engine_from_profile_source(profile_source)
    if engine:
        values["engine"] = engine
    if filters.workflow == "running":
        values["scan_target"] = "running"
    elif filters.workflow == "finished":
        values["scan_target"] = "finished"
    if filters.window.isdigit():
        values["recent_window_minutes"] = filters.window
    if filters.query_type != _INBOX_FILTER_ALL:
        values["query_type"] = filters.query_type
    return values


def query_inbox_scope_filter_query(
    filters: QueryInboxScopeFilters | None,
) -> dict[str, str]:
    normalized = _normalized_scope_filters(filters)
    query: dict[str, str] = {}
    if normalized.source != _INBOX_FILTER_ALL:
        query[INBOX_SOURCE_PARAM] = normalized.source
    if normalized.workflow != _INBOX_FILTER_ALL:
        query[INBOX_WORKFLOW_PARAM] = normalized.workflow
    if normalized.window != _INBOX_FILTER_ALL:
        query[INBOX_WINDOW_PARAM] = normalized.window
    if normalized.query_type != _INBOX_FILTER_ALL:
        query[INBOX_QUERY_TYPE_PARAM] = normalized.query_type
    if normalized.from_time and normalized.to_time:
        query[INBOX_FROM_PARAM] = normalized.from_time
        query[INBOX_TO_PARAM] = normalized.to_time
    return query


def query_inbox_scope_filter_query_from_mapping(
    values: Mapping[str, object] | None,
) -> dict[str, str]:
    return query_inbox_scope_filter_query(query_inbox_scope_filters_from_mapping(values))


def query_inbox_scope_filters_active(filters: QueryInboxScopeFilters | None) -> bool:
    return bool(query_inbox_scope_filter_query(filters))


def query_inbox_scope_filters_match_settings(
    settings: Any,
    filters: QueryInboxScopeFilters | None,
) -> bool:
    summary = _summary_payload_from_settings(settings)
    if summary is None:
        return True
    return query_inbox_scope_filters_match_summary(summary, filters)


def query_inbox_scope_filters_match_summary(
    summary: Mapping[str, Any],
    filters: QueryInboxScopeFilters | None,
) -> bool:
    normalized = _normalized_scope_filters(filters)
    if not query_inbox_scope_filters_active(normalized):
        return True
    current = _query_inbox_current_scope_from_summary(summary)
    return (
        _scope_filter_matches(normalized.source, current.source_value)
        and _scope_filter_matches(normalized.workflow, current.workflow_value)
        and _scope_filter_matches(normalized.window, current.window_value)
        and _scope_filter_matches(normalized.query_type, current.query_type_value)
        and _time_range_scope_filter_matches(normalized, current)
    )


def query_inbox_status_from_summary(
    summary: Mapping[str, Any],
    *,
    summary_view: RecentScanSummaryView | None = None,
    now: datetime | None = None,
    scope_filters: QueryInboxScopeFilters | None = None,
) -> QueryInboxStatus:
    # The batch card presents this source first and may add outcome metrics.
    # Reusing that view keeps the inbox counts aligned with the visible rows.
    view = summary_view or recent_scan_summary_view_for_render(
        dict(summary),
        cache_source=summary,
        reuse_existing_for_source=True,
    )
    current_scope = _query_inbox_current_scope_from_summary(summary)
    scope_filter_groups = _scope_filter_groups_from_current_scope(current_scope)
    freshness = query_inbox_freshness_from_summary(summary, now=now)
    if not query_inbox_scope_filters_match_summary(summary, scope_filters):
        return QueryInboxStatus(
            state="empty",
            badge_class="gray",
            dot_class="gray",
            title="No matching inbox scope",
            message=(
                "Current materialized results do not match the selected source, "
                "window, workflow, or query type filter. Use All filters or New scan to "
                "materialize that scope."
            ),
            metrics=(("status", "filtered"),),
            scope_items=query_inbox_scope_from_summary(summary),
            scope_filter_groups=scope_filter_groups,
        )
    status = query_inbox_status_from_view(
        view,
        freshness=freshness,
        scope_items=query_inbox_scope_from_summary(summary),
        scope_filter_groups=scope_filter_groups,
    )
    if _safe_string(summary.get("mode")).lower() == "recent-history-online":
        online_metrics = _online_history_status_metrics(summary, now=now)
        history_view = normalize_history_view(summary.get("history_view"))
        return replace(
            status,
            title=(
                "Details ready"
                if history_view == HISTORY_VIEW_DETAILS_READY
                else "All recent queries"
            )
            if status.state != "empty"
            else (
                "No Details ready"
                if history_view == HISTORY_VIEW_DETAILS_READY
                else "Online history empty"
            ),
            message=(
                "Showing the newest raw-free analyses with compatible Details. Every result row "
                "opens the analyst decision page."
                if history_view == HISTORY_VIEW_DETAILS_READY
                else "Showing the newest retained summaries and their analysis state. Use Details "
                "ready to work only with openable analyses."
            )
            if status.state != "empty"
            else (
                "No compatible analyzed cases are ready yet. Check All recent for queued, running, "
                "failed, or unselected summaries."
                if history_view == HISTORY_VIEW_DETAILS_READY
                else "Recent history storage is configured, but no retained query summaries are available yet."
            ),
            metrics=tuple((*status.metrics, *online_metrics)),
            history_view=history_view,
        )
    return status


def query_inbox_freshness_from_summary(
    summary: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> QueryInboxFreshness:
    freshness_payload = summary.get("materialized_case_index")
    if isinstance(freshness_payload, Mapping):
        freshness_payload = freshness_payload.get("freshness")
    source = freshness_payload if isinstance(freshness_payload, Mapping) else summary
    to_time = _parse_summary_time(source.get("to_time"))
    if to_time is None:
        return QueryInboxFreshness(state="unknown")
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    else:
        effective_now = effective_now.astimezone(timezone.utc)
    age_minutes = max(0, int((effective_now - to_time).total_seconds() // 60))
    window_minutes = _positive_int(source.get("recent_window_minutes"))
    state = "stale" if age_minutes > _stale_after_minutes(window_minutes) else "fresh"
    return QueryInboxFreshness(
        state=state,
        age_minutes=age_minutes,
        window_minutes=window_minutes,
    )


def query_inbox_status_from_view(
    view: RecentScanSummaryView | None,
    *,
    job: Any | None = None,
    freshness: QueryInboxFreshness | None = None,
    scope_items: tuple[tuple[str, str], ...] = (),
    scope_filter_groups: tuple[QueryInboxScopeFilterGroup, ...] = (),
) -> QueryInboxStatus:
    if _job_is_running(job):
        return QueryInboxStatus(
            state="running",
            badge_class="blue",
            dot_class="",
            title="Scan running",
            message="New bounded analysis is in progress. Existing materialized results stay visible when available.",
            metrics=_running_metrics(job),
        )
    if view is None:
        return QueryInboxStatus(
            state="empty",
            badge_class="gray",
            dot_class="gray",
            title="No materialized cases yet",
            message="Choose a source and time window to build the first raw-free Recent case set.",
            metrics=(("status", "empty"),),
        )
    total = _header_count(view, "total")
    bad = _header_count(view, "bad")
    suspicious = _header_count(view, "suspicious")
    warnings = len(view.warning_messages)
    freshness = freshness or QueryInboxFreshness(state="unknown")
    result_filter_toggles = recent_scan_result_filter_toggles(view.rows)
    if total <= 0:
        return QueryInboxStatus(
            state="empty",
            badge_class="gray",
            dot_class="gray",
            title="No matching cases",
            message=view.empty_message
            or "The latest bounded scan did not materialize cases for the selected filters.",
            metrics=_summary_metrics(
                total=total,
                bad=bad,
                suspicious=suspicious,
                warnings=warnings,
                freshness=freshness,
            ),
            scope_items=scope_items,
            scope_filter_groups=scope_filter_groups,
            result_filter_toggles=result_filter_toggles,
            result_rows=view.rows,
        )
    if freshness.state == "stale":
        return QueryInboxStatus(
            state="stale",
            badge_class="amber",
            dot_class="amber",
            title="",
            message=(
                "Materialized results are older than the current freshness window. Use New "
                "scan to refresh source, time range, workflow, or query type."
            ),
            metrics=_summary_metrics(
                total=total,
                bad=bad,
                suspicious=suspicious,
                warnings=warnings,
                freshness=freshness,
            ),
            scope_items=scope_items,
            scope_filter_groups=scope_filter_groups,
            result_filter_toggles=result_filter_toggles,
            result_rows=view.rows,
        )
    if warnings:
        return QueryInboxStatus(
            state="partial",
            badge_class="amber",
            dot_class="amber",
            title="",
            message="",
            metrics=_summary_metrics(
                total=total,
                bad=bad,
                suspicious=suspicious,
                warnings=warnings,
                freshness=freshness,
            ),
            scope_items=scope_items,
            scope_filter_groups=scope_filter_groups,
            result_filter_toggles=result_filter_toggles,
            result_rows=view.rows,
        )
    return QueryInboxStatus(
        state="ready",
        badge_class="green",
        dot_class="",
        title="",
        message="",
        metrics=_summary_metrics(
            total=total,
            bad=bad,
            suspicious=suspicious,
            warnings=warnings,
            freshness=freshness,
        ),
        scope_items=scope_items,
        scope_filter_groups=scope_filter_groups,
        result_filter_toggles=result_filter_toggles,
        result_rows=view.rows,
    )


def render_query_inbox_status(
    status: QueryInboxStatus,
    *,
    active_group: str = "bad",
    only_with_spills: bool = False,
    scope_filters: QueryInboxScopeFilters | None = None,
    result_filters: RecentScanResultFilters | None = None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    filters = _normalized_scope_filters(scope_filters)
    scope_query = query_inbox_scope_filter_query(filters)
    result_query = recent_scan_result_filter_query(result_filters)
    preset_query = {**scope_query, **result_query}
    normalized_result_sort = normalize_result_sort(result_sort)
    if normalized_result_sort != DEFAULT_RESULT_SORT:
        preset_query[RESULT_SORT_PARAM] = normalized_result_sort
    history_views = _render_online_history_view_switch(
        status,
        active_group=active_group,
        only_with_spills=only_with_spills,
        extra_query=preset_query,
    )
    metrics = "".join(
        '<span class="query-inbox-metric">'
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(value)}</span>"
        "</span>"
        for label, value in status.metrics
    )
    action = _render_query_inbox_action(status)
    scope = _render_query_inbox_scope(status.scope_items)
    active_filters = _render_query_inbox_active_filters(
        status,
        filters,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=normalized_result_sort,
    )
    scope_filter_controls = _render_query_inbox_scope_filter_controls(
        status.scope_filter_groups,
        filters,
        active_group=active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=normalized_result_sort,
    )
    view_presets = _render_query_inbox_view_presets(
        status,
        active_group=active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=normalized_result_sort,
        extra_query=scope_query,
    )
    controls = _render_query_inbox_controls(
        scope_filter_controls=scope_filter_controls,
        view_presets=view_presets,
    )
    message = (
        f'<p class="query-inbox-status-message">{html.escape(status.message)}</p>'
        if status.message
        else ""
    )
    if status.state in _MATERIALIZED_INBOX_STATES:
        heading = (
            f'<span class="query-inbox-status-title">{html.escape(status.title)}</span>'
            if status.title
            else ""
        )
    else:
        heading = f"<h1>{html.escape(status.title)}</h1>"
    return (
        f'<section id="query-inbox-status" class="panel query-inbox-status query-inbox-status--{html.escape(status.state, quote=True)}" '
        'aria-label="Query Inbox status">'
        '<div class="query-inbox-status-main">'
        f'<span class="dot {html.escape(status.dot_class, quote=True)}"></span>'
        '<div class="query-inbox-status-heading">'
        f"{heading}"
        f'<span class="badge {html.escape(status.badge_class, quote=True)}">{html.escape(status.state)}</span>'
        "</div>"
        f'<div class="query-inbox-metrics" aria-label="Query Inbox summary">{metrics}</div>'
        f"{action}"
        "</div>"
        f"{message}"
        f"{history_views}"
        f"{scope}"
        f"{active_filters}"
        f"{controls}"
        "</section>"
    )


def _render_online_history_view_switch(
    status: QueryInboxStatus,
    *,
    active_group: str,
    only_with_spills: bool,
    extra_query: Mapping[str, str],
) -> str:
    if not status.history_view:
        return ""
    active_view = normalize_history_view(status.history_view)
    links: list[str] = []
    for view, label in (
        (HISTORY_VIEW_DETAILS_READY, "Details ready"),
        (HISTORY_VIEW_ALL_RECENT, "All recent"),
    ):
        query = dict(extra_query)
        query["query_group"] = normalize_query_group(active_group)
        if only_with_spills:
            query["only_with_spills"] = "on"
        if view == DEFAULT_HISTORY_VIEW:
            query.pop("history_view", None)
        else:
            query["history_view"] = view
        active = view == active_view
        classes = "query-inbox-preset"
        attrs = ""
        if active:
            classes += " query-inbox-preset--active"
            attrs = ' aria-current="page"'
        href = f"/?{urlencode(query)}#recent-results"
        links.append(
            f'<a class="{classes}" href="{html.escape(href, quote=True)}"{attrs}>'
            f"{html.escape(label)}</a>"
        )
    return (
        '<nav class="query-inbox-presets query-inbox-history-views" '
        'aria-label="Online history view">'
        f"{''.join(links)}</nav>"
    )


def _render_query_inbox_controls(
    *,
    scope_filter_controls: str,
    view_presets: str,
) -> str:
    body = f"{scope_filter_controls}{view_presets}"
    if not body:
        return ""
    return (
        '<details class="query-inbox-controls" aria-label="Query Inbox scan scope and saved views">'
        '<summary class="query-inbox-controls-summary">'
        "<span>Scan scope and saved views</span>"
        "</summary>"
        f"{body}"
        "</details>"
    )


def query_inbox_scope_from_summary(summary: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    index = _safe_materialized_index(summary)
    source = _mapping(index.get("source"))
    scope = _mapping(index.get("scope"))
    freshness = _mapping(index.get("freshness"))

    items: list[tuple[str, str]] = []
    source_label = _source_scope_label(source, summary)
    if source_label:
        items.append(("Source", source_label))
    target_label = _target_scope_label(scope)
    if target_label:
        items.append(("Target", target_label))
    window_label = _window_scope_label(scope, freshness)
    if window_label:
        items.append(("Window", window_label))
    query_type_label = _query_type_scope_label(
        scope.get("query_type_filter") or summary.get("query_type_filter")
    )
    if query_type_label:
        items.append(("Query type", query_type_label))
    owner_pool_label = _owner_pool_scope_label(scope)
    if owner_pool_label:
        items.append(("Owner/pool", owner_pool_label))
    duration_label = _duration_scope_label(scope)
    if duration_label:
        items.append(("Duration", duration_label))
    return tuple(items)


def _render_query_inbox_scope(scope_items: tuple[tuple[str, str], ...]) -> str:
    if not scope_items:
        return ""
    chips = "".join(
        '<span class="query-inbox-scope-item">'
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(value)}</span>"
        "</span>"
        for label, value in scope_items
    )
    return f'<div class="query-inbox-scope" aria-label="Current Query Inbox scope">{chips}</div>'


def _render_query_inbox_active_filters(
    status: QueryInboxStatus,
    filters: QueryInboxScopeFilters,
    *,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    chips = _active_scope_filter_chips(status.scope_filter_groups, filters)
    if only_with_spills:
        chips.append(("Result", "Spill evidence"))
    chips.extend(
        ("Result", label)
        for label in active_recent_scan_result_filter_labels(
            result_filters,
            toggles=status.result_filter_toggles,
        )
    )
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort != DEFAULT_RESULT_SORT:
        chips.append(("Sort", RESULT_SORT_LABELS.get(normalized_sort, normalized_sort)))
    if not chips:
        return ""
    rendered = "".join(
        '<span class="query-inbox-active-filter-chip">'
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(value)}</span>"
        "</span>"
        for label, value in chips
    )
    return (
        '<div class="query-inbox-active-filters" aria-label="Active Query Inbox filters">'
        '<span class="query-inbox-active-filter-title">Active filters</span>'
        f"{rendered}"
        "</div>"
    )


def _active_scope_filter_chips(
    groups: tuple[QueryInboxScopeFilterGroup, ...],
    filters: QueryInboxScopeFilters,
) -> list[tuple[str, str]]:
    chips: list[tuple[str, str]] = []
    from_time, to_time = normalize_query_inbox_time_range(filters.from_time, filters.to_time)
    if from_time and to_time:
        chips.append(("Window", f"{from_time} to {to_time}"))
    for group in groups:
        if group.key == "window" and from_time and to_time:
            continue
        active_value = _filter_value_for_group(filters, group.key)
        if active_value == _INBOX_FILTER_ALL:
            continue
        chips.append((group.label, _scope_filter_value_label(group.key, active_value)))
    return chips


def _render_query_inbox_view_presets(
    status: QueryInboxStatus,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str,
    extra_query: Mapping[str, str] | None = None,
) -> str:
    if status.state not in _MATERIALIZED_INBOX_STATES:
        return ""
    normalized_group = normalize_query_group(active_group)
    normalized_sort = normalize_result_sort(result_sort)
    normalized_filters = normalize_recent_scan_result_filters(result_filters)
    links = []
    for preset in _INBOX_VIEW_PRESETS:
        active = _query_inbox_view_preset_is_active(
            preset,
            active_group=normalized_group,
            only_with_spills=only_with_spills,
            result_filters=normalized_filters,
            result_sort=normalized_sort,
        )
        classes = "query-inbox-view-preset"
        attrs = ""
        count = _query_inbox_view_preset_count(status, preset)
        if active:
            classes += " query-inbox-view-preset--active"
            attrs = ' aria-current="page"'
        elif count == 0:
            classes += " query-inbox-view-preset--zero"
        count_badge = (
            f'<span class="query-inbox-preset-count">{count}</span>' if count is not None else ""
        )
        href = _query_inbox_view_preset_href(preset, extra_query=extra_query)
        links.append(
            f'<a class="{classes}" href="{html.escape(href, quote=True)}"{attrs}>'
            f"{html.escape(preset.label)}{count_badge}</a>"
        )
    return (
        '<nav class="query-inbox-view-presets" aria-label="Query Inbox view presets">'
        '<span class="query-inbox-view-label">Views</span>'
        f"{''.join(links)}</nav>"
    )


def _query_inbox_view_preset_href(
    preset: QueryInboxViewPreset,
    *,
    extra_query: Mapping[str, str] | None = None,
) -> str:
    query: dict[str, str] = {"query_group": normalize_query_group(preset.query_group)}
    if extra_query:
        query.update(_safe_extra_query(extra_query))
    for result_param in RESULT_FILTER_PARAMS:
        query.pop(result_param, None)
    query.pop(RESULT_SORT_PARAM, None)
    query.pop("only_with_spills", None)
    query.update(recent_scan_result_filter_query(preset.result_filters))
    normalized_sort = normalize_result_sort(preset.result_sort)
    if normalized_sort != DEFAULT_RESULT_SORT:
        query[RESULT_SORT_PARAM] = normalized_sort
    if preset.only_with_spills:
        query["only_with_spills"] = "on"
    return f"/?{urlencode(query)}#recent-results"


def _query_inbox_view_preset_count(
    status: QueryInboxStatus,
    preset: QueryInboxViewPreset,
) -> int | None:
    if not status.result_rows:
        return None
    counts = query_group_counts_for_rows(
        status.result_rows,
        only_with_spills=preset.only_with_spills,
        result_filters=preset.result_filters,
    )
    return counts.get(normalize_query_group(preset.query_group))


def _query_inbox_view_preset_is_active(
    preset: QueryInboxViewPreset,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters,
    result_sort: str,
) -> bool:
    return (
        normalize_query_group(preset.query_group) == active_group
        and preset.only_with_spills == only_with_spills
        and normalize_result_sort(preset.result_sort) == result_sort
        and normalize_recent_scan_result_filters(preset.result_filters) == result_filters
    )


def _render_query_inbox_action(status: QueryInboxStatus) -> str:
    if status.state not in {"empty", "ready", "partial", "stale"}:
        return ""
    return '<a class="query-inbox-action" href="/#new-scan" data-open-new-scan>New scan</a>'


def _render_query_inbox_scope_filter_controls(
    groups: tuple[QueryInboxScopeFilterGroup, ...],
    filters: QueryInboxScopeFilters,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    if not groups:
        return ""
    rendered_groups = "".join(
        _render_scope_filter_group(
            group,
            filters,
            active_group=active_group,
            only_with_spills=only_with_spills,
            result_filters=result_filters,
            result_sort=result_sort,
        )
        for group in groups
    )
    return (
        '<div class="query-inbox-scope-filters" aria-label="Query Inbox scope filters">'
        f"{rendered_groups}"
        "</div>"
    )


def _render_scope_filter_group(
    group: QueryInboxScopeFilterGroup,
    filters: QueryInboxScopeFilters,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    active_value = _filter_value_for_group(filters, group.key)
    links = []
    time_range_active = group.key == "window" and bool(filters.from_time and filters.to_time)
    for value, label in _scope_filter_options(group, active_value):
        active = value == active_value and not time_range_active
        classes = "query-inbox-scope-filter"
        attrs = ""
        if active:
            classes += " query-inbox-scope-filter--active"
            attrs = ' aria-current="page"'
        href = _scope_filter_href(
            filters,
            group.key,
            value,
            active_group=active_group,
            only_with_spills=only_with_spills,
            result_filters=result_filters,
            result_sort=result_sort,
        )
        links.append(
            f'<a class="{classes}" href="{html.escape(href, quote=True)}"{attrs}>'
            f"{html.escape(label)}</a>"
        )
    return (
        '<div class="query-inbox-scope-filter-group">'
        f'<span class="query-inbox-scope-filter-label">{html.escape(group.label)}</span>'
        f"{''.join(links)}"
        f"{_render_window_scope_filter_form(group, filters, active_group=active_group, only_with_spills=only_with_spills, result_filters=result_filters, result_sort=result_sort)}"
        f"{_render_time_range_scope_filter_form(group, filters, active_group=active_group, only_with_spills=only_with_spills, result_filters=result_filters, result_sort=result_sort)}"
        f"{_render_query_type_scope_filter_form(group, filters, active_group=active_group, only_with_spills=only_with_spills, result_filters=result_filters, result_sort=result_sort)}"
        "</div>"
    )


def _render_window_scope_filter_form(
    group: QueryInboxScopeFilterGroup,
    filters: QueryInboxScopeFilters,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    if group.key != "window":
        return ""
    active_value = _filter_value_for_group(filters, group.key)
    value = active_value if active_value.isdigit() else group.current_value
    if not str(value or "").isdigit():
        value = ""
    hidden = _scope_filter_hidden_inputs(
        filters,
        active_group=active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=result_sort,
        omit_key="window",
    )
    return (
        '<form class="query-inbox-window-filter" method="get" action="/#recent-results" '
        'aria-label="Set Query Inbox window">'
        f"{hidden}"
        '<label class="query-inbox-window-label" for="query-inbox-window-minutes">Minutes</label>'
        '<input id="query-inbox-window-minutes" class="query-inbox-window-input" '
        'name="inbox_window" type="number" min="1" max="525600" step="1" '
        f'value="{html.escape(str(value or ""), quote=True)}" inputmode="numeric">'
        '<button class="query-inbox-window-button" type="submit">Apply</button>'
        "</form>"
    )


def _render_time_range_scope_filter_form(
    group: QueryInboxScopeFilterGroup,
    filters: QueryInboxScopeFilters,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    if group.key != "window":
        return ""
    from_value = filters.from_time or group.current_from_time
    to_value = filters.to_time or group.current_to_time
    if not (from_value and to_value):
        return ""
    hidden = _scope_filter_hidden_inputs(
        filters,
        active_group=active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=result_sort,
        omit_key="time_range",
    )
    return (
        '<form class="query-inbox-time-range-filter" method="get" action="/#recent-results" '
        'aria-label="Set Query Inbox UTC time range">'
        f"{hidden}"
        '<label class="query-inbox-time-range-label" for="query-inbox-from">From UTC</label>'
        '<input id="query-inbox-from" class="query-inbox-time-range-input" '
        'name="inbox_from" type="text" pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z" '
        'maxlength="17" autocomplete="off" '
        f'value="{html.escape(str(from_value), quote=True)}" placeholder="YYYY-MM-DDTHH:MMZ">'
        '<label class="query-inbox-time-range-label" for="query-inbox-to">To</label>'
        '<input id="query-inbox-to" class="query-inbox-time-range-input" '
        'name="inbox_to" type="text" pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z" '
        'maxlength="17" autocomplete="off" '
        f'value="{html.escape(str(to_value), quote=True)}" placeholder="YYYY-MM-DDTHH:MMZ">'
        '<button class="query-inbox-time-range-button" type="submit">Apply</button>'
        "</form>"
    )


def _render_query_type_scope_filter_form(
    group: QueryInboxScopeFilterGroup,
    filters: QueryInboxScopeFilters,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    if group.key != "query_type":
        return ""
    active_value = _filter_value_for_group(filters, group.key)
    value = active_value if active_value != _INBOX_FILTER_ALL else group.current_value
    if value == _INBOX_FILTER_ALL:
        value = ""
    hidden = _scope_filter_hidden_inputs(
        filters,
        active_group=active_group,
        only_with_spills=only_with_spills,
        result_filters=result_filters,
        result_sort=result_sort,
        omit_key="query_type",
    )
    return (
        '<form class="query-inbox-query-type-filter" method="get" action="/#recent-results" '
        'aria-label="Set Query Inbox query type">'
        f"{hidden}"
        '<label class="query-inbox-query-type-label" for="query-inbox-query-type">Type</label>'
        '<input id="query-inbox-query-type" class="query-inbox-query-type-input" '
        'name="inbox_query_type" type="text" pattern="[A-Za-z][A-Za-z0-9_]{0,31}" '
        'maxlength="32" autocomplete="off" '
        f'value="{html.escape(str(value or ""), quote=True)}" inputmode="latin">'
        '<button class="query-inbox-query-type-button" type="submit">Apply</button>'
        "</form>"
    )


def _scope_filter_hidden_inputs(
    filters: QueryInboxScopeFilters,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
    omit_key: str = "",
) -> str:
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    query.update(recent_scan_result_filter_query(result_filters))
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort != DEFAULT_RESULT_SORT:
        query[RESULT_SORT_PARAM] = normalized_sort
    scope_query = query_inbox_scope_filter_query(filters)
    for key, param in (
        ("source", INBOX_SOURCE_PARAM),
        ("workflow", INBOX_WORKFLOW_PARAM),
        ("window", INBOX_WINDOW_PARAM),
        ("query_type", INBOX_QUERY_TYPE_PARAM),
        ("from_time", INBOX_FROM_PARAM),
        ("to_time", INBOX_TO_PARAM),
    ):
        if omit_key == "window" and key in {"window", "from_time", "to_time"}:
            continue
        if omit_key == "time_range" and key in {"window", "from_time", "to_time"}:
            continue
        if key == omit_key:
            continue
        value = scope_query.get(param)
        if value:
            query[param] = value
    if only_with_spills:
        query["only_with_spills"] = "on"
    return "".join(
        '<input type="hidden" '
        f'name="{html.escape(name, quote=True)}" '
        f'value="{html.escape(value, quote=True)}">'
        for name, value in query.items()
    )


def _scope_filter_options(
    group: QueryInboxScopeFilterGroup,
    active_value: str,
) -> tuple[tuple[str, str], ...]:
    options: list[tuple[str, str]] = [(_INBOX_FILTER_ALL, "All")]
    if active_value != _INBOX_FILTER_ALL and active_value != group.current_value:
        options.append((active_value, _scope_filter_value_label(group.key, active_value)))
    if group.current_value:
        options.append((group.current_value, group.current_label))
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value, label in options:
        if not value or value in seen:
            continue
        deduped.append((value, label))
        seen.add(value)
    return tuple(deduped)


def _scope_filter_href(
    filters: QueryInboxScopeFilters,
    key: str,
    value: str,
    *,
    active_group: str,
    only_with_spills: bool,
    result_filters: RecentScanResultFilters | None,
    result_sort: str = DEFAULT_RESULT_SORT,
) -> str:
    updated = _updated_scope_filters(filters, key, value)
    query: dict[str, str] = {"query_group": normalize_query_group(active_group)}
    query.update(recent_scan_result_filter_query(result_filters))
    normalized_sort = normalize_result_sort(result_sort)
    if normalized_sort != DEFAULT_RESULT_SORT:
        query[RESULT_SORT_PARAM] = normalized_sort
    query.update(query_inbox_scope_filter_query(updated))
    if only_with_spills:
        query["only_with_spills"] = "on"
    return f"/?{urlencode(query)}#recent-results"


def _updated_scope_filters(
    filters: QueryInboxScopeFilters,
    key: str,
    value: str,
) -> QueryInboxScopeFilters:
    if key == "source":
        return QueryInboxScopeFilters(
            source=value,
            workflow=filters.workflow,
            window=filters.window,
            query_type=filters.query_type,
            from_time=filters.from_time,
            to_time=filters.to_time,
        )
    if key == "workflow":
        return QueryInboxScopeFilters(
            source=filters.source,
            workflow=value,
            window=filters.window,
            query_type=filters.query_type,
            from_time=filters.from_time,
            to_time=filters.to_time,
        )
    if key == "window":
        return QueryInboxScopeFilters(
            source=filters.source,
            workflow=filters.workflow,
            window=value,
            query_type=filters.query_type,
        )
    if key == "query_type":
        return QueryInboxScopeFilters(
            source=filters.source,
            workflow=filters.workflow,
            window=filters.window,
            query_type=value,
            from_time=filters.from_time,
            to_time=filters.to_time,
        )
    return filters


def _filter_value_for_group(filters: QueryInboxScopeFilters, key: str) -> str:
    if key == "source":
        return filters.source
    if key == "workflow":
        return filters.workflow
    if key == "window":
        return filters.window
    if key == "query_type":
        return filters.query_type
    return _INBOX_FILTER_ALL


def _safe_materialized_index(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    existing = summary.get("materialized_case_index")
    if isinstance(existing, Mapping) and existing.get("schema_version") == SCHEMA_VERSION:
        return existing
    return build_materialized_case_index(summary)


def _summary_payload_from_settings(settings: Any) -> Mapping[str, Any] | None:
    corpus_summary = getattr(settings, "corpus_summary", None)
    if isinstance(corpus_summary, Mapping):
        return corpus_summary
    summary_path = getattr(settings, "batch_summary", None)
    if summary_path is None:
        return None
    try:
        payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _history_summary_if_requested(
    settings: Any,
    scope_filters: QueryInboxScopeFilters | None,
    history_view: str = DEFAULT_HISTORY_VIEW,
) -> Mapping[str, Any] | None:
    if _normalized_scope_filters(scope_filters).source != "history":
        return None
    return recent_history_inbox_summary_from_settings(settings, history_view=history_view)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_first_value(values: Mapping[str, object] | None, key: str) -> object:
    if values is None:
        return ""
    value = values.get(key, "")
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value


def _normalize_source_filter(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in {"cloudera-manager", "cloudera"}:
        text = "cm"
    return text if text in _INBOX_SOURCE_FILTER_VALUES else _INBOX_FILTER_ALL


def _normalize_workflow_filter(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return text if text in _INBOX_WORKFLOW_FILTER_VALUES else _INBOX_FILTER_ALL


def _normalize_window_filter(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in _INBOX_WINDOW_TEXT_VALUES:
        return text
    if not text or len(text) > 8:
        return _INBOX_FILTER_ALL
    try:
        minutes = int(text)
    except ValueError:
        return _INBOX_FILTER_ALL
    if minutes <= 0 or minutes > 525600:
        return _INBOX_FILTER_ALL
    return str(minutes)


def _normalize_query_type_filter(value: object) -> str:
    return _safe_query_type_form_value(value) or _INBOX_FILTER_ALL


def _scope_filter_matches(requested: str, current: str) -> bool:
    return requested == _INBOX_FILTER_ALL or bool(current and requested == current)


def _time_range_scope_filter_matches(
    filters: QueryInboxScopeFilters,
    current: _QueryInboxCurrentScope,
) -> bool:
    if not (filters.from_time and filters.to_time):
        return True
    return filters.from_time == current.from_time_value and filters.to_time == current.to_time_value


def _query_inbox_current_scope_from_summary(summary: Mapping[str, Any]) -> _QueryInboxCurrentScope:
    index = _safe_materialized_index(summary)
    source = _mapping(index.get("source"))
    scope = _mapping(index.get("scope"))
    freshness = _mapping(index.get("freshness"))

    source_value, source_label = _source_scope_filter_value(source, summary)
    workflow_value, workflow_label = _workflow_scope_filter_value(scope)
    window_value, window_label = _window_scope_filter_value(scope, freshness)
    query_type_value, query_type_label = _query_type_scope_filter_value(scope, summary)
    from_time_value, to_time_value = _time_range_scope_filter_values(scope, freshness)
    return _QueryInboxCurrentScope(
        source_value=source_value,
        source_label=source_label,
        workflow_value=workflow_value,
        workflow_label=workflow_label,
        window_value=window_value,
        window_label=window_label,
        query_type_value=query_type_value,
        query_type_label=query_type_label,
        from_time_value=from_time_value,
        to_time_value=to_time_value,
    )


def _scope_filter_groups_from_current_scope(
    current: _QueryInboxCurrentScope,
) -> tuple[QueryInboxScopeFilterGroup, ...]:
    groups: list[QueryInboxScopeFilterGroup] = []
    if current.source_value and current.source_label:
        groups.append(
            QueryInboxScopeFilterGroup(
                key="source",
                label="Source",
                param=INBOX_SOURCE_PARAM,
                current_value=current.source_value,
                current_label=current.source_label,
            )
        )
    if current.workflow_value and current.workflow_label:
        groups.append(
            QueryInboxScopeFilterGroup(
                key="workflow",
                label="Workflow",
                param=INBOX_WORKFLOW_PARAM,
                current_value=current.workflow_value,
                current_label=current.workflow_label,
            )
        )
    if current.window_value and current.window_label:
        groups.append(
            QueryInboxScopeFilterGroup(
                key="window",
                label="Window",
                param=INBOX_WINDOW_PARAM,
                current_value=current.window_value,
                current_label=current.window_label,
                current_from_time=current.from_time_value,
                current_to_time=current.to_time_value,
            )
        )
    if current.query_type_value and current.query_type_label:
        groups.append(
            QueryInboxScopeFilterGroup(
                key="query_type",
                label="Query type",
                param=INBOX_QUERY_TYPE_PARAM,
                current_value=current.query_type_value,
                current_label=current.query_type_label,
            )
        )
    return tuple(groups)


def _source_scope_filter_value(
    source: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[str, str]:
    mode = _safe_string(source.get("mode") or summary.get("mode")).lower()
    if mode == "synthetic-demo" or bool(summary.get("demo_mode")):
        return "demo", "Synthetic demo"
    profile_source = _canonical_profile_source(
        source.get("query_profile_source") or summary.get("query_profile_source")
    )
    if profile_source == "cm":
        return "cm", "Cloudera Manager"
    if profile_source == "impala":
        return "impala", "Direct Impala"
    if profile_source == "trino":
        return "trino", "Trino local"
    if profile_source == "history" or mode == "recent-history-online":
        return "history", "Online history"
    if mode in {"recent-query-batch", "batch"}:
        return "recent", "Recent scan"
    return "", ""


def _workflow_scope_filter_value(scope: Mapping[str, Any]) -> tuple[str, str]:
    if _safe_bool(scope.get("only_running")):
        return "running", "Running now"
    if _safe_bool(scope.get("include_running")):
        return "mixed", "Finished + running"
    if "include_running" in scope or "only_running" in scope:
        return "finished", "Finished queries"
    return "", ""


def _window_scope_filter_value(
    scope: Mapping[str, Any],
    freshness: Mapping[str, Any],
) -> tuple[str, str]:
    from_time = scope.get("from_time") or freshness.get("from_time")
    to_time = scope.get("to_time") or freshness.get("to_time")
    if _synthetic_time(from_time) or _synthetic_time(to_time):
        return "synthetic", "Synthetic demo"
    label = _window_scope_label(scope, freshness)
    if not label:
        return "", ""
    if label == "Live snapshot":
        return "live", label
    parsed_from = _parse_summary_time(from_time)
    parsed_to = _parse_summary_time(to_time)
    if parsed_from is not None or parsed_to is not None:
        return "current", label
    window_minutes = _positive_int(scope.get("recent_window_minutes"))
    if window_minutes is None:
        window_minutes = _positive_int(freshness.get("recent_window_minutes"))
    if window_minutes is not None:
        return str(window_minutes), label
    return "", ""


def _time_range_scope_filter_values(
    scope: Mapping[str, Any],
    freshness: Mapping[str, Any],
) -> tuple[str, str]:
    return normalize_query_inbox_time_range(
        scope.get("from_time") or freshness.get("from_time"),
        scope.get("to_time") or freshness.get("to_time"),
    )


def _query_type_scope_filter_value(
    scope: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[str, str]:
    raw_value = scope.get("query_type_filter") or summary.get("query_type_filter")
    value = _safe_query_type_form_value(raw_value)
    if value:
        return value, value
    if _safe_string(raw_value).lower() == "all":
        return _INBOX_FILTER_ALL, "All supported"
    return "", ""


def _scope_filter_value_label(key: str, value: str) -> str:
    if key == "source":
        return {
            "cm": "Cloudera Manager",
            "impala": "Direct Impala",
            "trino": "Trino local",
            "demo": "Synthetic demo",
            "recent": "Recent scan",
            "history": "Online history",
        }.get(value, value)
    if key == "workflow":
        return {
            "finished": "Finished queries",
            "running": "Running now",
            "mixed": "Finished + running",
        }.get(value, value)
    if key == "window":
        if value == "current":
            return "Current window"
        if value == "live":
            return "Live snapshot"
        if value == "synthetic":
            return "Synthetic demo"
        if value.isdigit():
            return f"Last {value} min"
    if key == "query_type":
        if value == _INBOX_FILTER_ALL:
            return "All supported"
        return value
    return value


def _safe_extra_query(extra_query: Mapping[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    from_time, to_time = normalize_query_inbox_time_range(
        extra_query.get(INBOX_FROM_PARAM),
        extra_query.get(INBOX_TO_PARAM),
    )
    for key in (
        INBOX_SOURCE_PARAM,
        INBOX_WORKFLOW_PARAM,
        INBOX_WINDOW_PARAM,
        INBOX_QUERY_TYPE_PARAM,
    ):
        value = extra_query.get(key)
        if isinstance(value, str) and value:
            safe[key] = value
    result_sort = normalize_result_sort(extra_query.get(RESULT_SORT_PARAM))
    if result_sort != DEFAULT_RESULT_SORT:
        safe[RESULT_SORT_PARAM] = result_sort
    if from_time and to_time:
        safe.pop(INBOX_WINDOW_PARAM, None)
        safe[INBOX_FROM_PARAM] = from_time
        safe[INBOX_TO_PARAM] = to_time
    safe.update(
        recent_scan_result_filter_query(recent_scan_result_filters_from_mapping(extra_query))
    )
    return safe


def _refresh_cluster_key_for_summary(settings: Any, summary: Mapping[str, Any]) -> str:
    return _refresh_cluster_key_for_profile_source(settings, _summary_profile_source(summary))


def _refresh_cluster_key_for_profile_source(settings: Any, profile_source: str) -> str:
    if profile_source not in {"cm", "impala", "trino"}:
        return ""
    clusters = tuple(getattr(settings, "clusters", ()) or ())
    if not clusters:
        return ""
    matches = [
        str(getattr(cluster, "key", "") or "")
        for cluster in clusters
        if _cluster_matches_profile_source(cluster, profile_source)
    ]
    matches = [key for key in matches if key]
    active_key = str(getattr(settings, "active_cluster_key", "") or "")
    if active_key in matches:
        return active_key
    default_key = str(getattr(clusters[0], "key", "") or "")
    if not active_key and default_key in matches:
        return default_key
    return matches[0] if len(matches) == 1 else ""


def _summary_profile_source(summary: Mapping[str, Any]) -> str:
    source = _mapping(_safe_materialized_index(summary).get("source"))
    return _canonical_profile_source(
        source.get("query_profile_source") or summary.get("query_profile_source")
    )


def _cluster_profile_source(cluster: Any) -> str:
    return _canonical_profile_source(getattr(cluster, "query_profile_source", "cm"))


def _cluster_matches_profile_source(cluster: Any, profile_source: str) -> bool:
    if profile_source == "trino":
        return bool(
            cluster_trino_beta_query_ready(cluster) or cluster_trino_beta_recent_ready(cluster)
        )
    return _cluster_profile_source(cluster) == profile_source


def _refresh_profile_source_for_scope(
    summary: Mapping[str, Any],
    filters: QueryInboxScopeFilters,
) -> str:
    return _profile_source_from_scope_source_filter(filters.source) or _summary_profile_source(
        summary
    )


def _profile_source_from_scope_source_filter(value: str) -> str:
    return value if value in {"cm", "impala", "trino", "history"} else ""


def _canonical_profile_source(value: Any) -> str:
    text = _safe_string(value).lower().replace("_", "-")
    if text in {"cm", "cloudera-manager"}:
        return "cm"
    if text == "impala":
        return "impala"
    if text == "trino":
        return "trino"
    if text in {"history", "recent-history", "online-history"}:
        return "history"
    return ""


def _refresh_engine_from_profile_source(value: Any) -> str:
    source = _canonical_profile_source(value)
    if source == "trino":
        return "trino"
    if source in {"cm", "impala"}:
        return "impala"
    return ""


def _source_scope_label(source: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    mode = _safe_string(source.get("mode") or summary.get("mode")).lower()
    if mode == "synthetic-demo" or bool(summary.get("demo_mode")):
        return "Synthetic demo"
    profile_source = _safe_string(source.get("query_profile_source")).lower()
    if profile_source in {"cm", "cloudera-manager", "cloudera_manager"}:
        return "Cloudera Manager"
    if profile_source == "impala":
        return "Direct Impala"
    if profile_source == "trino":
        return "Trino local"
    if profile_source == "history" or mode == "recent-history-online":
        return "Online history"
    if mode in {"recent-query-batch", "batch"}:
        return "Recent scan"
    return ""


def _target_scope_label(scope: Mapping[str, Any]) -> str:
    if _safe_bool(scope.get("only_running")):
        return "Running now"
    if _safe_bool(scope.get("include_running")):
        return "Finished + running"
    if "include_running" in scope or "only_running" in scope:
        return "Finished queries"
    return ""


def _window_scope_label(scope: Mapping[str, Any], freshness: Mapping[str, Any]) -> str:
    from_time = scope.get("from_time") or freshness.get("from_time")
    to_time = scope.get("to_time") or freshness.get("to_time")
    if _synthetic_time(from_time) or _synthetic_time(to_time):
        return "Synthetic demo"
    parsed_from = _parse_summary_time(from_time)
    parsed_to = _parse_summary_time(to_time)
    if parsed_from is not None and parsed_to is not None:
        return _time_range_label(parsed_from, parsed_to)
    if parsed_to is not None:
        return f"Until {_time_label(parsed_to)}"
    if parsed_from is not None:
        return f"From {_time_label(parsed_from)}"
    window_minutes = _positive_int(scope.get("recent_window_minutes"))
    if window_minutes is None:
        window_minutes = _positive_int(freshness.get("recent_window_minutes"))
    if window_minutes is not None:
        return f"Last {window_minutes} min"
    if _safe_bool(scope.get("only_running")):
        return "Live snapshot"
    return ""


def _query_type_scope_label(value: Any) -> str:
    text = _safe_query_type_form_value(value)
    if not text:
        raw = _safe_string(value)
        if raw.lower() == "all":
            return "All supported"
        return ""
    return text


def _owner_pool_scope_label(scope: Mapping[str, Any]) -> str:
    has_user = _safe_bool(scope.get("user_filter_present"))
    has_pool = _safe_bool(scope.get("pool_filter_present"))
    if has_user and has_pool:
        return "user + pool set"
    if has_user:
        return "user set"
    if has_pool:
        return "pool set"
    if "user_filter_present" in scope or "pool_filter_present" in scope:
        return "all users/pools"
    return ""


def _duration_scope_label(scope: Mapping[str, Any]) -> str:
    value = scope.get("duration_filter")
    text = _safe_string(value)
    if not text or text.lower() in {"none", "null"}:
        return ""
    return text[:64]


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    return redact_browser_display_text(
        value,
        redact_field_names=True,
        redact_artifact_markers=True,
        redact_model_names=True,
        redact_sql_snippets=True,
        redact_infrastructure=True,
        max_chars=128,
    ).strip()


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_string(value).lower() in {"1", "true", "yes", "y", "on"}


def _first_positive_int_form_value(*values: Any) -> str | None:
    for value in values:
        form_value = _positive_int_form_value(value)
        if form_value is not None:
            return form_value
    return None


def _positive_int_form_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value)) if value > 0 else None
    return None


def _first_non_negative_number_form_value(*values: Any) -> str | None:
    for value in values:
        form_value = _non_negative_number_form_value(value)
        if form_value is not None:
            return form_value
    return None


def _non_negative_number_form_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if not isinstance(value, float) or not math.isfinite(value) or value < 0:
        return None
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


def _safe_query_type_form_value(value: Any) -> str:
    text = _safe_string(value).strip().upper()
    if not text or text == "ALL":
        return ""
    if len(text) > 32 or not ("A" <= text[0] <= "Z"):
        return ""
    if any(not ("A" <= char <= "Z" or "0" <= char <= "9" or char == "_") for char in text):
        return ""
    return text


def _synthetic_time(value: Any) -> bool:
    return _safe_string(value).lower() == "synthetic"


def _time_range_label(from_time: datetime, to_time: datetime) -> str:
    if from_time.date() == to_time.date():
        return f"{from_time:%Y-%m-%d %H:%M}-{to_time:%H:%M} UTC"
    return f"{_time_label(from_time)} -> {_time_label(to_time)}"


def _time_label(value: datetime) -> str:
    return f"{value:%Y-%m-%d %H:%M} UTC"


def _job_is_running(job: Any | None) -> bool:
    return bool(
        job is not None
        and getattr(job, "status", "") == "running"
        and getattr(job, "kind", "") in {"batch", "running", "trino_recent"}
    )


def _running_metrics(job: Any) -> tuple[tuple[str, str], ...]:
    progress = getattr(job, "progress", None)
    stage = str(getattr(job, "stage_label", "") or "").strip()
    metrics = [("status", "running")]
    if isinstance(progress, int):
        metrics.append(("progress", f"{max(0, min(progress, 100))}%"))
    if stage:
        metrics.append(("stage", stage))
    return tuple(metrics)


def _summary_metrics(
    *,
    total: int,
    bad: int,
    suspicious: int,
    warnings: int,
    freshness: QueryInboxFreshness | None = None,
) -> tuple[tuple[str, str], ...]:
    metrics = [
        ("cases", str(max(0, total))),
        ("bad", str(max(0, bad))),
        ("suspicious", str(max(0, suspicious))),
    ]
    if warnings:
        metrics.append(("warnings", str(warnings)))
    if freshness is not None and freshness.known:
        metrics.append(("freshness", freshness.state))
        if freshness.age_minutes is not None:
            metrics.append(("age", _age_label(freshness.age_minutes)))
        if freshness.window_minutes is not None:
            metrics.append(("window", f"{freshness.window_minutes} min"))
    return tuple(metrics)


def _online_history_status_metrics(
    summary: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[tuple[str, str], ...]:
    counts = _mapping(summary.get("history_profile_status_counts"))
    queued = _profile_status_count(counts, "pending") + _profile_status_count(
        counts, "retry_pending"
    )
    active = _profile_status_count(counts, "processing")
    analyzed = _profile_status_count(counts, "analyzed")
    failed = _profile_status_count(counts, "failed")
    loop_parts: list[str] = []
    if queued:
        loop_parts.append(f"{queued} queued")
    if active:
        loop_parts.append(f"{active} active")
    if analyzed:
        loop_parts.append(f"{analyzed} analyzed")
    if failed:
        loop_parts.append(f"{failed} failed")

    metrics: list[tuple[str, str]] = []
    row_summary = _online_history_row_summary(summary)
    if row_summary:
        metrics.append(("history rows", row_summary))
    metrics.extend(_online_history_collector_freshness_metrics(summary))
    metrics.extend(_online_history_collector_run_metrics(summary, now=now))
    shown = _safe_metric_count(summary.get("selected_count"))
    loop_summary = " / ".join(loop_parts)
    # When every shown row is analyzed the loop, the state breakdown and the
    # details-ready ratio all restate the row summary. Keep the first one that
    # says something new.
    settled = bool(analyzed) and not queued and not active and not failed
    if loop_summary and not (settled and analyzed == shown and row_summary):
        metrics.append(("profile loop", loop_summary))
    state_summary = _online_history_profile_state_summary(counts)
    if state_summary and state_summary != loop_summary:
        metrics.append(("profile states", state_summary))
    error_summary = _online_history_profile_error_summary(summary)
    if error_summary:
        metrics.append(("profile errors", error_summary))
    profile_next_step = _online_history_profile_next_step(summary, counts)
    if profile_next_step:
        metrics.append(("profile next step", profile_next_step))
    if analyzed:
        ready = _online_history_details_ready_count(summary)
        details_ready_view = (
            normalize_history_view(summary.get("history_view")) == HISTORY_VIEW_DETAILS_READY
        )
        if not (details_ready_view and ready == analyzed and row_summary):
            metrics.append(("details ready", f"{ready}/{analyzed} analyzed"))
    metrics.extend(_online_history_profile_backlog_metrics(summary))
    metrics.extend(_online_history_operator_readiness_metrics(summary))
    return tuple(metrics)


def _online_history_row_summary(summary: Mapping[str, Any]) -> str:
    retained = _safe_metric_count(summary.get("summaries_inspected"))
    shown = _safe_metric_count(summary.get("selected_count"))
    if normalize_history_view(summary.get("history_view")) == HISTORY_VIEW_DETAILS_READY:
        return f"{shown} details ready shown / {retained} retained"
    if retained <= 0 or shown <= 0 or retained == shown:
        return ""
    return f"{retained} retained / {shown} shown"


def _online_history_collector_freshness_metrics(
    summary: Mapping[str, Any],
) -> list[tuple[str, str]]:
    freshness = _mapping(summary.get("history_collector_freshness"))
    if not freshness:
        return []
    if _safe_string(freshness.get("scope")) == HISTORY_VIEW_DETAILS_READY:
        return []
    status = _safe_string(freshness.get("status")).lower()
    if status not in {"fresh", "stale", "empty", "unknown"}:
        status = "unknown"
    metrics = [("collector freshness", status)]
    age_minutes = _safe_optional_metric_count(freshness.get("age_minutes"))
    if age_minutes is not None:
        metrics.append(("last planning", _age_label(age_minutes)))
    run_status = _safe_string(_mapping(summary.get("history_collector_run")).get("status")).lower()
    next_step = _online_history_collector_next_step(status, run_status=run_status)
    if next_step:
        metrics.append(("collector next step", next_step))
    return metrics


def _online_history_collector_next_step(status: str, *, run_status: str = "") -> str:
    if status == "stale":
        if run_status in {"recorded", "idle"}:
            return ""
        return "Use New scan to refresh retained summaries, or check the scheduled Recent summary collector."
    if status == "empty":
        return "Run the Recent summary collector or use New scan to populate retained summaries."
    if status == "unknown":
        return "Run a discover-only Recent refresh to refresh retained summary freshness evidence."
    return ""


def _online_history_collector_run_metrics(
    summary: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    run = _mapping(summary.get("history_collector_run"))
    if not run:
        return []
    status = _safe_string(run.get("status")).lower()
    if status not in {
        "recorded",
        "idle",
        "warning",
        "failed",
        "disabled",
        "blocked",
        "unavailable",
        "unknown",
    }:
        status = "unknown"
    recorded = _safe_metric_count(run.get("summaries_recorded"))
    planned = _safe_metric_count(run.get("profile_jobs_planned"))
    metrics = [("producer status", f"{status} / {recorded} rows / {planned} jobs")]
    age_label = _collector_run_age_label(run.get("observed_at_iso"), now=now)
    if age_label:
        metrics.append(("last producer run", age_label))
    next_step = _online_history_producer_next_step(status)
    if next_step:
        metrics.append(("producer next step", next_step))
    return metrics


def _collector_run_age_label(value: Any, *, now: datetime | None = None) -> str:
    observed_at = _parse_summary_time(value)
    if observed_at is None:
        return ""
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    else:
        effective_now = effective_now.astimezone(timezone.utc)
    age_minutes = max(0, int((effective_now - observed_at).total_seconds() // 60))
    return _age_label(age_minutes)


def _online_history_producer_next_step(status: str) -> str:
    if status == "failed":
        return "Check the Recent summary collector job and configured history credentials."
    if status == "warning":
        return "Check recent-history store and profile-job planning warnings before relying on producer health."
    if status == "idle":
        return "Collector ran but found no retained summaries; check the scan window and filters if this is unexpected."
    if status == "disabled":
        return "Enable Recent history storage before expecting retained producer updates."
    if status in {"blocked", "unavailable", "unknown"}:
        return "Refresh the retained collector-run summary before relying on producer health."
    return ""


def _online_history_profile_state_summary(counts: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("not_collected", "not collected"),
        ("pending", "pending"),
        ("retry_pending", "retry"),
        ("processing", "processing"),
        ("analyzed", "analyzed"),
        ("failed", "failed"),
    ):
        count = _profile_status_count(counts, key)
        if count:
            parts.append(f"{count} {label}")
    return " / ".join(parts)


def _online_history_profile_error_summary(summary: Mapping[str, Any]) -> str:
    cases = summary.get("cases")
    if not isinstance(cases, list):
        return ""
    counts: dict[str, int] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        code = _safe_string(case.get("failure_category"))
        if not code:
            continue
        if _safe_string(case.get("collection_status")) != "summary_history":
            continue
        if _safe_string(case.get("analysis_status")) not in {"failed", "profile_retry_pending"}:
            continue
        counts[code] = counts.get(code, 0) + 1
    if not counts:
        return ""
    visible = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    parts = [f"{code} x{count}" for code, count in visible]
    remaining = len(counts) - len(visible)
    if remaining:
        parts.append(f"+{remaining} more")
    return " / ".join(parts)


def _online_history_profile_next_step(
    summary: Mapping[str, Any],
    counts: Mapping[str, Any],
) -> str:
    failed = _profile_status_count(counts, "failed")
    retry = _profile_status_count(counts, "retry_pending")
    pending = _profile_status_count(counts, "pending")
    processing = _profile_status_count(counts, "processing")
    analyzed = _profile_status_count(counts, "analyzed")
    if failed:
        return "Review normalized profile errors before requeueing failed rows."
    if retry:
        return "Let the profile worker retry pending rows; investigate repeated errors."
    if pending:
        return "Run or schedule the Recent profile worker to materialize Details."
    if processing:
        return "Wait for active worker leases or check stale processing jobs."
    if analyzed:
        ready = _details_ready_count(summary)
        if ready < analyzed:
            return "Check analysis cache and artifact metadata for analyzed rows without Details."
    return ""


def _online_history_profile_backlog_metrics(
    summary: Mapping[str, Any],
) -> list[tuple[str, str]]:
    backlog_health = _mapping(summary.get("history_profile_backlog_health"))
    if not backlog_health:
        return []
    pending = _safe_metric_count(backlog_health.get("pending_jobs"))
    retry = _safe_metric_count(backlog_health.get("retry_pending_jobs"))
    leased = _safe_metric_count(backlog_health.get("leased_jobs"))
    stale = _safe_metric_count(backlog_health.get("stale_leased_jobs"))
    failed = _safe_metric_count(backlog_health.get("failed_jobs"))
    if not any((pending, retry, leased, stale, failed)):
        return []
    metrics = [
        (
            "profile backlog",
            f"{pending} pending / {retry} retry / {leased} leased / {stale} stale / {failed} failed",
        )
    ]
    next_step = _online_history_backlog_next_step(
        pending=pending,
        retry=retry,
        leased=leased,
        stale=stale,
        failed=failed,
    )
    if next_step:
        metrics.append(("backlog next step", next_step))
    return metrics


def _online_history_backlog_next_step(
    *,
    pending: int,
    retry: int,
    leased: int,
    stale: int,
    failed: int,
) -> str:
    active_leased = max(0, leased - stale)
    if stale:
        return (
            "Run the Recent profile worker to reclaim expired leases; check worker "
            "lease duration if stale leases persist."
        )
    if failed:
        return "Run profile remediation dry-run before requeueing terminal failed profile jobs."
    if retry:
        return (
            "Let the profile worker retry pending rows; investigate repeated normalized "
            "error codes if retry backlog persists."
        )
    if pending:
        return "Run or schedule the Recent profile worker to materialize Details."
    if active_leased:
        return "Wait for active worker leases before starting another worker."
    return ""


def _online_history_details_ready_count(summary: Mapping[str, Any]) -> int:
    configured = _safe_optional_metric_count(summary.get("history_details_ready_count"))
    if configured is not None:
        return configured
    return _details_ready_count(summary)


def _online_history_operator_readiness_metrics(
    summary: Mapping[str, Any],
) -> list[tuple[str, str]]:
    readiness = _mapping(summary.get("operator_readiness"))
    if not readiness:
        return []
    status = _operator_readiness_display_status(readiness.get("status"))
    accepted = _safe_metric_count(readiness.get("accepted_summary_count"))
    evidence = _safe_metric_count(readiness.get("evidence_summary_count"))
    issue_count = _safe_metric_count(readiness.get("issue_count"))
    metrics = [("operator readiness", status)]
    if evidence:
        metrics.append(("readiness evidence", f"{min(accepted, evidence)}/{evidence} summaries"))
    if issue_count:
        metrics.append(("readiness issues", str(issue_count)))
    issue_summary = _online_history_operator_issue_summary(readiness)
    if issue_summary:
        metrics.append(("readiness reasons", issue_summary))

    operations = _mapping(readiness.get("operations"))
    postgres = _mapping(operations.get("postgres_readiness"))
    if postgres.get("accepted") is True:
        schema = "ready" if postgres.get("schema_initialized") is True else "not ready"
        checks = _safe_metric_count(postgres.get("check_count"))
        issues = _safe_metric_count(postgres.get("issue_count"))
        value = f"{schema} / {checks} checks"
        if issues:
            value = f"{value} / {issues} issues"
        metrics.append(("history schema", value))

    collector = _mapping(operations.get("collector_summary"))
    if collector.get("present") is True:
        status = _safe_string(collector.get("status")) or "unknown"
        inspected = _safe_metric_count(collector.get("summaries_inspected"))
        recorded = _safe_metric_count(collector.get("summaries_recorded"))
        planned = _safe_metric_count(collector.get("profile_jobs_planned"))
        issues = _safe_metric_count(collector.get("issue_count"))
        parts = [status, f"{recorded} recorded", f"{planned} jobs"]
        if inspected:
            parts.append(f"{inspected} inspected")
        if issues:
            parts.append(f"{issues} issues")
        metrics.append(("operator collector", " / ".join(parts)))
        observed_at = _safe_string(collector.get("observed_at_iso"))
        if observed_at:
            metrics.append(("collector observed", observed_at))
        next_step = _safe_string(collector.get("next_step"))
        if next_step:
            metrics.append(("collector handoff next step", next_step))

    worker = _mapping(operations.get("profile_worker"))
    if worker.get("accepted") is True:
        parts = [
            f"{_safe_metric_count(worker.get('jobs_claimed'))} claimed",
            f"{_safe_metric_count(worker.get('jobs_completed'))} completed",
            f"{_safe_metric_count(worker.get('jobs_failed'))} failed",
        ]
        retried = _safe_metric_count(worker.get("jobs_retried"))
        lease_lost = _safe_metric_count(worker.get("jobs_lease_lost"))
        if retried:
            parts.append(f"{retried} retried")
        if lease_lost:
            parts.append(f"{lease_lost} lease lost")
        metrics.append(("profile worker", " / ".join(parts)))
        cache_records = _safe_metric_count(worker.get("analysis_cache_records"))
        artifact_records = _safe_metric_count(worker.get("profile_artifact_records"))
        if cache_records or artifact_records:
            metrics.append(
                (
                    "worker materialization",
                    f"{cache_records} cache / {artifact_records} artifacts",
                )
            )
        next_step = _safe_string(worker.get("next_step"))
        if next_step:
            metrics.append(("worker next step", next_step))
        backlog_health = _mapping(worker.get("profile_backlog_health"))
        if worker.get("profile_backlog_health_present") is True and backlog_health:
            pending = _safe_metric_count(backlog_health.get("pending_jobs"))
            retry = _safe_metric_count(backlog_health.get("retry_pending_jobs"))
            leased = _safe_metric_count(backlog_health.get("leased_jobs"))
            stale = _safe_metric_count(backlog_health.get("stale_leased_jobs"))
            failed = _safe_metric_count(backlog_health.get("failed_jobs"))
            metrics.append(
                (
                    "profile backlog",
                    (
                        f"{pending} pending / {retry} retry / {leased} leased / "
                        f"{stale} stale / {failed} failed"
                    ),
                )
            )
            backlog_next_step = _safe_string(worker.get("profile_backlog_next_step"))
            if backlog_next_step:
                metrics.append(("backlog next step", backlog_next_step))

    retention = _mapping(operations.get("retention"))
    if retention.get("accepted") is True:
        deleted = _safe_metric_count(retention.get("total_deleted"))
        summaries = _safe_metric_count(retention.get("summaries_deleted"))
        metrics.append(("history retention", f"{deleted} deleted / {summaries} summaries"))
    remediation = _mapping(operations.get("profile_remediation"))
    if remediation.get("accepted") is True:
        mode = _safe_string(remediation.get("mode")) or "unknown"
        matched = _safe_metric_count(remediation.get("matched_failed_jobs"))
        selected = _safe_metric_count(remediation.get("selected_failed_jobs"))
        requeued = _safe_metric_count(remediation.get("requeued_jobs"))
        metrics.append(
            (
                "profile remediation",
                f"{mode} / {matched} matched / {selected} selected / {requeued} requeued",
            )
        )
        next_step = _safe_string(remediation.get("next_step"))
        if next_step:
            metrics.append(("remediation next step", next_step))
    return metrics


def _online_history_operator_issue_summary(readiness: Mapping[str, Any]) -> str:
    issue_codes_value = readiness.get("issue_codes")
    if not isinstance(issue_codes_value, list):
        return ""
    issue_count = _safe_metric_count(readiness.get("issue_count"))
    reasons: list[str] = []
    for item in issue_codes_value:
        reason = project_operator_readiness_issue_code(item)
        if reason not in reasons:
            reasons.append(reason)
        if len(reasons) >= MAX_OPERATOR_READINESS_ISSUE_CODES:
            break
    if not reasons:
        return ""
    remaining = max(0, issue_count - len(reasons))
    if remaining:
        reasons.append(f"+{remaining} more")
    return " / ".join(reasons)


def _operator_readiness_display_status(value: Any) -> str:
    status = _safe_string(value).lower()
    if status in {"ready", "blocked", "unavailable", "unknown"}:
        return status
    return "unknown"


def _safe_metric_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _safe_optional_metric_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _profile_status_count(counts: Mapping[str, Any], status: str) -> int:
    value = counts.get(status)
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _details_ready_count(summary: Mapping[str, Any]) -> int:
    cases = summary.get("cases")
    if not isinstance(cases, list):
        return 0
    ready = 0
    for case in cases:
        if isinstance(case, Mapping) and _positive_int(case.get("case_index")) is not None:
            ready += 1
    return ready


def _header_count(view: RecentScanSummaryView, key: str) -> int:
    for label, value in view.header_items:
        if label != key:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return 0
    return 0


def _parse_summary_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"synthetic", "unknown", "none", "null"}:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _stale_after_minutes(window_minutes: int | None) -> int:
    if window_minutes is None:
        return 240
    return max(120, window_minutes * 2)


def _age_label(age_minutes: int) -> str:
    safe_minutes = max(0, age_minutes)
    if safe_minutes < 90:
        return f"{safe_minutes} min ago"
    hours = safe_minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    days = hours // 24
    return f"{days} d ago"
