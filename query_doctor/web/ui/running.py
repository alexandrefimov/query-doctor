"""Running query analysis page for the local Query Doctor web UI."""

from __future__ import annotations

import html
from typing import Any

from query_doctor.web.ui.recent_scan_form import (
    configured_web_advanced_filters,
    form_or_config_value,
    owner_missing_reason,
    read_local_config_values,
    render_batch_number_field,
    render_batch_text_field,
    render_batch_user_field,
    render_cluster_select,
    render_hidden_query_inbox_scope_inputs,
    render_running_scan_framing_note,
    user_filter_options,
)
from query_doctor.web.cluster_selection import default_cluster_key, settings_for_cluster_key
from query_doctor.web.models import WebError, metadata_collection_configured
from query_doctor.web.ui.pages import render_page
from query_doctor.web.ui.progress import render_job_panel
from query_doctor.web.ui.query_inbox import (
    QueryInboxScopeFilters,
    query_inbox_scope_filter_query,
    query_inbox_scope_filters_match_settings,
)
from query_doctor.web.ui.recent_scan_results import render_batch_card
from query_doctor.web.ui.recent_scan_result_filters import (
    RecentScanResultFilters,
    normalize_recent_scan_result_filters,
    recent_scan_result_filter_query,
)
from query_doctor.web.ui.recent_scan_groups import (
    DEFAULT_RESULT_SORT,
    RESULT_SORT_PARAM,
    normalize_result_sort,
)


def render_running_queries_page(
    settings: Any,
    *,
    job: Any | None = None,
    error: object | None = None,
    form_values: dict[str, Any] | None = None,
    query_group: str = "bad",
    only_with_spills: bool = False,
    result_sort: str = DEFAULT_RESULT_SORT,
    results_page: Any = 1,
    workload_admin_scope: str = "all",
    workload_admin_signal: str = "all",
    workload_group_scope: str = "",
    workload_group_name: str = "",
    workload_group_signal: str = "all",
    inbox_scope_filters: QueryInboxScopeFilters | None = None,
    result_filters: RecentScanResultFilters | None = None,
) -> str:
    effective_form_values = form_values
    if effective_form_values is None and job is not None:
        effective_form_values = getattr(job, "batch_form_values", None)
    scope_filters = inbox_scope_filters or QueryInboxScopeFilters()
    scope_query = query_inbox_scope_filter_query(scope_filters)
    normalized_result_filters = normalize_recent_scan_result_filters(result_filters)
    normalized_result_sort = normalize_result_sort(result_sort)
    result_query = recent_scan_result_filter_query(normalized_result_filters)
    result_extra_query = {**scope_query, **result_query}
    if normalized_result_sort != DEFAULT_RESULT_SORT:
        result_extra_query[RESULT_SORT_PARAM] = normalized_result_sort
    if scope_query:
        effective_form_values = dict(effective_form_values or {})
        effective_form_values.update(scope_query)
    scope_matches = query_inbox_scope_filters_match_settings(settings, scope_filters)
    sections = [
        render_running_queries_run_panel(
            settings,
            effective_form_values,
            run_disabled=job is not None and job.status == "running",
        )
    ]
    if job is not None:
        result_html = None
        if scope_matches and job.status == "ok" and getattr(job, "kind", "") == "running":
            result_html = render_batch_card(
                settings,
                query_group=query_group,
                only_with_spills=only_with_spills,
                result_filters=normalized_result_filters,
                result_sort=normalized_result_sort,
                results_page=results_page,
                workload_admin_scope=workload_admin_scope,
                workload_admin_signal=workload_admin_signal,
                workload_group_scope=workload_group_scope,
                workload_group_name=workload_group_name,
                workload_group_signal=workload_group_signal,
                title="Running Queries",
                details_base_path="/running/case",
                extra_query=result_extra_query,
            )
        sections.append(render_job_panel(job, result_html_override=result_html))
    if scope_matches and (job is None or job.status != "ok"):
        batch_card = render_batch_card(
            settings,
            query_group=query_group,
            only_with_spills=only_with_spills,
            result_filters=normalized_result_filters,
            result_sort=normalized_result_sort,
            results_page=results_page,
            workload_admin_scope=workload_admin_scope,
            workload_admin_signal=workload_admin_signal,
            workload_group_scope=workload_group_scope,
            workload_group_name=workload_group_name,
            workload_group_signal=workload_group_signal,
            title="Running Queries",
            details_base_path="/running/case",
            extra_query=result_extra_query,
        )
        if batch_card:
            sections.append(batch_card)
    return render_page(
        settings,
        active_nav="running",
        show_run_panel=False,
        error=error,
        extra_sections=sections,
    )


def render_running_queries_run_panel(
    settings: Any,
    form_values: dict[str, Any] | None = None,
    *,
    run_disabled: bool = False,
) -> str:
    local_config = read_local_config_values(settings)
    values = {
        "cluster_key": form_or_config_value(
            form_values,
            "cluster_key",
            config_values=local_config,
            fallback=default_cluster_key(settings),
        ),
        "min_duration_sec": form_or_config_value(
            form_values,
            "min_duration_sec",
            config_values={},
            fallback="",
        ),
        "user": form_or_config_value(
            form_values, "user", config_values=local_config, config_key="recent_user"
        ),
        "pool": form_or_config_value(
            form_values, "pool", config_values=local_config, config_key="recent_pool"
        ),
        "query_type": form_or_config_value(
            form_values, "query_type", config_values=local_config, config_key="query_type"
        ),
    }
    if form_values:
        values.update(form_values)
    try:
        selected_settings = settings_for_cluster_key(settings, str(values.get("cluster_key") or ""))
    except WebError:
        selected_settings = settings
    owner_required = getattr(selected_settings, "source_visibility", "") == "owner_raw"
    user_options = user_filter_options(selected_settings)
    owner_missing = owner_required and not user_options
    metadata_configured = metadata_collection_configured(selected_settings)

    def value(name: str) -> str:
        return html.escape(str(values.get(name, "")), quote=True)

    metadata_note = (
        "" if metadata_configured else "Metadata collection is not configured for this web session."
    )
    metadata_note_html = (
        f'<div class="batch-note">{html.escape(metadata_note)}</div>' if metadata_note else ""
    )
    button_disabled = " disabled" if run_disabled or owner_missing else ""
    button_label = "Running" if run_disabled else "Owner required" if owner_missing else "Run scan"
    owner_field = render_batch_user_field(
        "user",
        "Username",
        value("user"),
        user_options=user_options,
        owner_required=owner_required,
        disabled_reason=owner_missing_reason() if owner_missing else "",
        help_text=(
            "Optional owner filter for this source visibility. Empty means all configured owners."
            if owner_required
            else "Optional exact query user filter. Empty means all users."
        ),
    )
    primary_owner_field = owner_field if owner_required else ""
    advanced_filter_names = configured_web_advanced_filters(local_config)
    advanced_fields = ""
    if "user" in advanced_filter_names and not owner_required:
        advanced_fields += owner_field
    if "pool" in advanced_filter_names:
        advanced_fields += render_batch_text_field(
            "pool",
            "Resource pool",
            value("pool"),
            help_text="Optional resource pool filter. Empty means all pools.",
        )
    if "query_type" in advanced_filter_names:
        advanced_fields += render_batch_text_field(
            "query_type",
            "Query type",
            value("query_type"),
            help_text="Optional query type filter such as QUERY. Empty means all query types.",
        )
    advanced_panel_html = render_running_advanced_settings(
        metadata_note_html=metadata_note_html,
        advanced_fields=advanced_fields,
    )
    running_grid_class = (
        "batch-form-grid running-form-grid running-form-grid--owner"
        if owner_required
        else "batch-form-grid running-form-grid"
    )
    return (
        '<section class="panel batch-run-panel" aria-label="Run running query scan">'
        '<div class="section-heading"><div>'
        '<h1 class="section-title">Running Queries</h1>'
        "</div></div>"
        '<form id="running-form" class="batch-form" method="post" action="/running/run">'
        f"{render_hidden_query_inbox_scope_inputs(values)}"
        '<div class="batch-source-settings">'
        f"{render_cluster_select(settings, value('cluster_key'), field_id='running_cluster_key', field_class='field diagnosis-cluster-field', label_text='Source cluster')}"
        "</div>"
        '<div class="batch-form-sections">'
        '<fieldset class="batch-form-section batch-form-section--primary"><legend>Live scan</legend>'
        f'<div class="{running_grid_class}" aria-label="Running scan filters">'
        f"{render_batch_number_field('min_duration_sec', 'Minimum duration (sec)', value('min_duration_sec'), step='0.001', required=False, help_text='Only include running queries at least this long. Empty means no duration filter. Running scans use the current running-query snapshot. No date or hour window is used. Profiles can be incomplete while queries execute, and Query Doctor may show fewer deterministic findings than after completion. No LLM report or optimizer draft runs automatically.')}"
        f"{primary_owner_field}"
        '<div class="running-run-action">'
        f'<button class="run-button" type="submit"{button_disabled}>{button_label}</button>'
        "</div>"
        "</div>"
        "</fieldset>"
        "</div>"
        f"{advanced_panel_html}"
        "</form></section>"
    )


def render_running_advanced_settings(*, metadata_note_html: str, advanced_fields: str) -> str:
    if not advanced_fields:
        return ""
    return (
        '<details class="batch-advanced"><summary>Advanced settings</summary>'
        '<div class="batch-advanced-body">'
        f"{metadata_note_html}"
        f"{render_running_scan_framing_note()}"
        '<fieldset class="batch-form-section"><legend>Secondary filters</legend>'
        '<div class="batch-filter-rows">'
        '<div class="batch-filter-row">'
        '<div class="batch-filter-row-label">Optional</div>'
        '<div class="batch-form-grid batch-form-grid--query-limits" aria-label="Secondary running query filters">'
        f"{advanced_fields}"
        "</div>"
        "</div>"
        "</div>"
        "</fieldset>"
        "</div>"
        "</details>"
    )
