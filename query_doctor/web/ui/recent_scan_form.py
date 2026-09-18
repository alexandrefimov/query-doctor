"""Recent query scan form rendering helpers."""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from query_doctor.source_visibility import collectable_owner_users
from query_doctor.trino.support_mode import (
    TRINO_SUPPORT_MODE_OFF,
    trino_support_mode_is_beta,
    trino_support_mode_enabled,
    trino_support_mode_is_production,
)
from query_doctor.web.cluster_selection import (
    cluster_select_options,
    cluster_trino_beta_query_ready,
    cluster_trino_beta_recent_ready,
    default_cluster_key,
    settings_for_cluster_key,
)
from query_doctor.web.models import (
    DEFAULT_RECENT_SCAN_TIMEZONE,
    WebError,
    metadata_collection_configured,
)
from query_doctor.web.recent_scan_timezone import (
    configured_recent_scan_timezone,
    utc_offset_label,
)
from query_doctor.web.ui.query_inbox import query_inbox_scope_filter_query_from_mapping


WEB_RECENT_SCAN_DEFAULTS = {
    "parallelism": "50",
    "metadata_jobs": "5",
}
LARGE_RECENT_WINDOW_WARNING_MINUTES = 1440
WEB_ADVANCED_FILTER_CHOICES = ("user", "pool", "query_type")
WEB_ADVANCED_FILTER_DEFAULTS = ("user", "pool")
RUNNING_SCAN_FRAMING_TEXT = (
    "Running scan is a live snapshot: no date/hour window is used, profiles can be incomplete while queries execute, "
    "and Query Doctor may show fewer deterministic findings than after completion. No LLM report or optimizer draft runs automatically."
)
RECENT_SCAN_TIMEZONE = ZoneInfo(DEFAULT_RECENT_SCAN_TIMEZONE)
RECENT_SCAN_LOOKBACK_DAYS = 2


def render_batch_run_panel(
    settings: Any,
    form_values: dict[str, Any] | None = None,
    *,
    run_disabled: bool = False,
    query_id: str = "",
    diagnosis_target: str = "recent",
    collapsed: bool = False,
    heading_title: str = "Query Inbox",
) -> str:
    local_config = read_local_config_values(settings)
    if "recent_parallelism" not in local_config and "recent_cm_jobs" in local_config:
        local_config["recent_parallelism"] = local_config["recent_cm_jobs"]
    default_parallelism = WEB_RECENT_SCAN_DEFAULTS["parallelism"]
    values = {
        "cluster_key": form_or_config_value(
            form_values,
            "cluster_key",
            config_values=local_config,
            fallback=default_cluster_key(settings),
        ),
        "scan_target": form_or_config_value(
            form_values,
            "scan_target",
            config_values=local_config,
            fallback="finished",
        ),
        "scan_date": form_or_config_value(
            form_values,
            "scan_date",
            config_values=local_config,
            fallback="",
        ),
        "scan_hour": form_or_config_value(
            form_values,
            "scan_hour",
            config_values=local_config,
            fallback="",
        ),
        "recent_window_minutes": form_or_config_value(
            form_values,
            "recent_window_minutes",
            config_values=local_config,
            fallback="60",
        ),
        "min_duration_sec": form_or_config_value(
            form_values,
            "min_duration_sec",
            config_values={},
            fallback="",
        ),
        "max_duration_sec": "",
        "parallelism": form_or_config_value(
            form_values,
            "parallelism",
            config_values=local_config,
            config_key="recent_parallelism",
            fallback=default_parallelism,
        ),
        "metadata_jobs": form_or_config_value(
            form_values,
            "metadata_jobs",
            config_values=local_config,
            config_key="recent_metadata_jobs",
            fallback=WEB_RECENT_SCAN_DEFAULTS["metadata_jobs"],
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
        "engine": form_or_config_value(
            form_values,
            "engine",
            config_values=local_config,
            config_key="engine",
            fallback="impala",
        ),
    }
    if form_values:
        values.update(form_values)
    selected_engine = normalize_engine_value(values.get("engine"))
    values["cluster_key"] = cluster_key_for_engine(
        settings,
        values.get("cluster_key"),
        selected_engine,
    )
    try:
        selected_settings = settings_for_cluster_key(settings, str(values.get("cluster_key") or ""))
    except WebError:
        selected_settings = settings
    scan_timezone = configured_recent_scan_timezone(selected_settings, local_config)
    default_scan_date, default_scan_hour = default_recent_scan_bucket(scan_timezone=scan_timezone)
    if not values.get("scan_date"):
        values["scan_date"] = default_scan_date
    if not values.get("scan_hour"):
        values["scan_hour"] = str(default_scan_hour)
    user_options = user_filter_options(selected_settings)
    metadata_configured = metadata_collection_configured(selected_settings)
    selected_diagnosis_target = str(values.get("diagnosis_target") or diagnosis_target or "recent")
    if selected_diagnosis_target not in {"recent", "query"}:
        selected_diagnosis_target = "recent"
    selected_source_impala_ready = cluster_impala_ready(selected_settings)
    impala_available = impala_any_source_ready(settings)
    trino_beta_query_is_ready = trino_beta_query_ready(selected_settings)
    trino_beta_recent_is_ready = trino_beta_recent_ready(selected_settings)
    trino_beta_ready = trino_beta_query_is_ready or trino_beta_recent_is_ready
    trino_beta_available = trino_beta_any_source_ready(settings)
    if selected_engine == "impala" and not selected_source_impala_ready:
        selected_engine = "trino" if trino_beta_ready and not impala_available else "impala"
    if selected_engine == "trino" and not trino_beta_ready:
        selected_engine = "impala"
    if selected_engine == "trino":
        if selected_diagnosis_target == "recent" and not trino_beta_recent_is_ready:
            selected_diagnosis_target = "query"
        if selected_diagnosis_target == "query" and not trino_beta_query_is_ready:
            selected_diagnosis_target = "recent"
    scan_target = str(values.get("scan_target") or "finished")
    if scan_target not in {"finished", "running"}:
        scan_target = "finished"
    if selected_engine == "trino":
        scan_target = "finished"
    values["scan_target"] = scan_target
    owner_required = getattr(selected_settings, "source_visibility", "") == "owner_raw"
    owner_missing = owner_required and not user_options

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
    form_action = "/running/run" if scan_target == "running" else "/batch/run"
    finished_scope_class = "" if scan_target == "finished" else " manual-inputs-hidden"
    finished_window_class = "" if scan_target == "finished" else " manual-inputs-hidden"
    min_duration_value = value("min_duration_sec")
    running_scope_class = "" if scan_target == "running" else " manual-inputs-hidden"
    recent_form_class = "" if selected_diagnosis_target == "recent" else " manual-inputs-hidden"
    query_form_class = "" if selected_diagnosis_target == "query" else " manual-inputs-hidden"
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
    advanced_owner_field = "" if owner_required else owner_field
    advanced_filter_names = (
        () if selected_engine == "trino" else configured_web_advanced_filters(local_config)
    )
    if "user" not in advanced_filter_names or owner_required:
        advanced_owner_field = ""
    advanced_pool_field = (
        render_batch_text_field(
            "pool",
            "Resource pool",
            value("pool"),
            help_text="Optional resource pool filter. Empty means all pools.",
        )
        if "pool" in advanced_filter_names
        else ""
    )
    advanced_query_type_field = (
        render_batch_text_field(
            "query_type",
            "Query type",
            value("query_type"),
            help_text="Optional query type filter such as QUERY. Empty means all query types.",
        )
        if "query_type" in advanced_filter_names
        else ""
    )
    advanced_panel_html = render_configured_advanced_settings(
        metadata_note_html=metadata_note_html,
        finished_scope_class=finished_scope_class,
        running_scope_class=running_scope_class,
        advanced_fields=f"{advanced_owner_field}{advanced_pool_field}{advanced_query_type_field}",
    )
    finished_window_fields = (
        f'<div class="batch-target-field{finished_window_class}" data-scan-target-field="finished">'
        f"{render_recent_window_field(value('recent_window_minutes'))}"
        "</div>"
    )
    owner_grid_class = " batch-form-grid--owner" if owner_required else ""
    panel_tag = "details" if collapsed else "section"
    panel_open = "" if collapsed else ""
    panel_summary = (
        '<summary class="batch-run-summary"><span>New scan</span>'
        "<small>Change source, window, or workflow</small></summary>"
        if collapsed
        else ""
    )
    panel_heading = (
        ""
        if collapsed
        else f'<div class="section-heading"><div><h1 class="section-title">{html.escape(heading_title)}</h1></div></div>'
    )
    engine_control_html = render_engine_control(
        selected_engine,
        impala_ready=impala_available,
        trino_beta_ready=trino_beta_available,
        selected_source_trino_beta_ready=trino_beta_ready,
        trino_label=(
            trino_display_label(selected_settings)
            if trino_beta_ready
            else first_ready_trino_display_label(settings)
        ),
    )
    return (
        f'<{panel_tag} id="new-scan" class="panel batch-run-panel{" batch-run-panel--disclosure" if collapsed else ""}" aria-label="Run query diagnosis" data-diagnosis-target-root{panel_open}>'
        f"{panel_summary}"
        f"{panel_heading}"
        f"{engine_control_html}"
        f"{render_source_settings(settings, value('cluster_key'))}"
        f"{render_workflow_control(selected_diagnosis_target, scan_target)}"
        f'<form id="batch-form" class="batch-form{recent_form_class}" method="post" action="{form_action}" data-scan-target-form data-active-scan-target="{html.escape(scan_target, quote=True)}" data-diagnosis-target-field="recent">'
        f"{render_hidden_cluster_input(value('cluster_key'))}"
        f"{render_hidden_engine_input(selected_engine)}"
        f"{render_hidden_scan_target_input(scan_target)}"
        f"{render_hidden_query_inbox_scope_inputs(values)}"
        '<div class="batch-form-sections">'
        '<fieldset class="batch-form-section batch-form-section--primary"><legend>Basic scan</legend>'
        f'<div class="batch-form-grid batch-form-grid--simple{owner_grid_class}" aria-label="Basic scan window">'
        f"{finished_window_fields}"
        f'<div class="batch-target-field{finished_window_class}" data-scan-target-field="finished">{render_batch_number_field("min_duration_sec", "Minimum duration (sec)", min_duration_value, step="0.001", required=False, help_text="Leave empty to include long queries and repeated short workload patterns. Set a value to narrow the scan to longer-running queries only.")}</div>'
        f"{primary_owner_field}"
        '<div class="batch-run-action">'
        f'<button class="run-button" type="submit"{button_disabled}>{button_label}</button>'
        "</div>"
        "</div>"
        "</fieldset>"
        "</div>"
        f"{advanced_panel_html}"
        "</form>"
        f'<div class="{query_form_class}" data-diagnosis-target-field="query">'
        f"{render_known_query_form(settings, cluster_key=value('cluster_key'), engine=selected_engine, query_id=query_id, run_disabled=run_disabled)}"
        "</div>"
        f"</{panel_tag}>"
    )


def normalize_engine_value(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"impala", "trino"} else "impala"


def configured_web_advanced_filters(config_values: dict[str, object]) -> tuple[str, ...]:
    enabled = form_or_config_bool(
        None,
        "web_advanced_settings_enabled",
        config_values=config_values,
        fallback=False,
    )
    if not enabled:
        return ()
    raw_filters = config_values.get("web_advanced_filters")
    if raw_filters is None:
        return WEB_ADVANCED_FILTER_DEFAULTS
    if not isinstance(raw_filters, (list, tuple)):
        return ()
    selected: list[str] = []
    seen: set[str] = set()
    for item in raw_filters:
        if not isinstance(item, str):
            continue
        normalized = item.strip().lower()
        if normalized not in WEB_ADVANCED_FILTER_CHOICES or normalized in seen:
            continue
        selected.append(normalized)
        seen.add(normalized)
    return tuple(selected)


def render_configured_advanced_settings(
    *,
    metadata_note_html: str,
    finished_scope_class: str,
    running_scope_class: str,
    advanced_fields: str,
) -> str:
    if not advanced_fields:
        return ""
    return (
        '<details class="batch-advanced"><summary>Advanced settings</summary>'
        '<div class="batch-advanced-body">'
        f"{metadata_note_html}"
        f'<div class="scope-line{finished_scope_class}" aria-label="Finished query collection scope" data-scan-target-field="finished">'
        "<strong>Finished scope:</strong> selected Search depth → matching summaries → analyzable profiles → ranked cases → bounded automatic metadata · no auto LLM"
        "</div>"
        f'<div class="scope-line{running_scope_class}" aria-label="Running query collection scope" data-scan-target-field="running">'
        "<strong>Running scope:</strong> current running query summaries → analyzable profiles → ranked cases. Runtime evidence may be incomplete until a query finishes; no auto LLM."
        "</div>"
        f"{render_running_scan_framing_note(extra_class=running_scope_class, data_scan_target=True)}"
        '<fieldset class="batch-form-section"><legend>Secondary filters</legend>'
        '<div class="batch-filter-rows">'
        '<div class="batch-filter-row">'
        '<div class="batch-filter-row-label">Optional</div>'
        '<div class="batch-form-grid batch-form-grid--query-limits" aria-label="Secondary query filters">'
        f"{advanced_fields}"
        "</div>"
        "</div>"
        "</div>"
        "</fieldset>"
        "</div>"
        "</details>"
    )


def trino_beta_query_ready(settings: Any) -> bool:
    return bool(
        trino_settings_enabled(settings)
        and getattr(settings, "trino_coordinator_url", None)
        and getattr(settings, "trino_query_info_source_contract", None)
    )


def trino_settings_enabled(settings: Any) -> bool:
    mode = getattr(settings, "trino_support_mode", TRINO_SUPPORT_MODE_OFF)
    return trino_support_mode_enabled(mode) or bool(getattr(settings, "trino_beta_enabled", False))


def trino_display_label(settings: Any) -> str:
    mode = getattr(settings, "trino_support_mode", "")
    if trino_support_mode_is_production(mode):
        return "Trino"
    if trino_support_mode_is_beta(mode) or bool(getattr(settings, "trino_beta_enabled", False)):
        return "Trino Beta"
    return "Trino"


def first_ready_trino_display_label(settings: Any) -> str:
    clusters = tuple(getattr(settings, "clusters", ()) or ())
    for cluster in clusters:
        if cluster_trino_beta_query_ready(cluster) or cluster_trino_beta_recent_ready(cluster):
            return trino_display_label(cluster)
    return trino_display_label(settings)


def trino_beta_recent_ready(settings: Any) -> bool:
    return bool(
        trino_beta_query_ready(settings)
        and getattr(settings, "trino_query_list_source_contract", None)
    )


def trino_beta_any_source_ready(settings: Any) -> bool:
    clusters = tuple(getattr(settings, "clusters", ()) or ())
    if clusters:
        return any(
            cluster_trino_beta_query_ready(cluster) or cluster_trino_beta_recent_ready(cluster)
            for cluster in clusters
        )
    return trino_beta_query_ready(settings) or trino_beta_recent_ready(settings)


def cluster_impala_ready(cluster: Any) -> bool:
    if not getattr(cluster, "clusters", None) and not hasattr(cluster, "key"):
        return True
    if any(
        (
            getattr(cluster, "cm_url", None),
            getattr(cluster, "cm_cluster", None),
            getattr(cluster, "cm_service", None),
            getattr(cluster, "impala_profile_hosts", ()),
            getattr(cluster, "manual_profile_dir", None),
        )
    ):
        return True
    return not trino_beta_query_ready(cluster)


def impala_any_source_ready(settings: Any) -> bool:
    clusters = tuple(getattr(settings, "clusters", ()) or ())
    if clusters:
        return any(cluster_impala_ready(cluster) for cluster in clusters)
    return True


def cluster_key_for_engine(settings: Any, selected_value: object, engine: str) -> str:
    clusters = tuple(getattr(settings, "clusters", ()) or ())
    selected_raw = html.unescape(str(selected_value or default_cluster_key(settings)))
    if not clusters:
        return selected_raw
    for cluster in clusters:
        if getattr(cluster, "key", "") == selected_raw and cluster_matches_engine(cluster, engine):
            return selected_raw
    for cluster in clusters:
        if cluster_matches_engine(cluster, engine):
            return str(getattr(cluster, "key", "") or selected_raw)
    return selected_raw


def cluster_matches_engine(cluster: Any, engine: str) -> bool:
    if engine == "trino":
        return cluster_trino_beta_query_ready(cluster) or cluster_trino_beta_recent_ready(cluster)
    return cluster_impala_ready(cluster)


def render_engine_control(
    selected_value: str,
    *,
    impala_ready: bool = True,
    trino_beta_ready: bool = False,
    selected_source_trino_beta_ready: bool | None = None,
    trino_label: str = "Trino Beta",
) -> str:
    selected = normalize_engine_value(selected_value)

    def option(
        value: str,
        label: str,
        help_text: str,
        *,
        disabled: bool = False,
    ) -> str:
        checked = " checked" if selected == value else ""
        disabled_attr = ' disabled aria-disabled="true"' if disabled else ""
        help_html = f"<small>{html.escape(help_text)}</small>" if help_text else ""
        return (
            "<label>"
            f'<input type="radio" name="engine_choice" value="{html.escape(value, quote=True)}" '
            f"data-engine-choice{checked}{disabled_attr}>"
            f"<span><strong>{html.escape(label)}</strong>{help_html}</span>"
            "</label>"
        )

    selected_source_ready = (
        trino_beta_ready
        if selected_source_trino_beta_ready is None
        else selected_source_trino_beta_ready
    )
    if selected_source_ready:
        readiness_note = f"{trino_label} is configured for this selected source. It is limited to retained-list Recent and/or one explicit Query ID."
    elif trino_beta_ready:
        readiness_note = f"{trino_label} is configured for another local source. Selecting it narrows Source cluster to Trino-ready sources."
    else:
        readiness_note = "Trino requires trino_support_mode, coordinator URL, and source contracts before the UI can select it."
    return (
        '<div class="mode-control engine-control" aria-label="Query engine">'
        '<div class="label-row">'
        '<span class="mode-label" id="engine_label">Engine</span>'
        '<details class="info-popover">'
        '<summary aria-label="Engine help">i</summary>'
        f'<div class="info-body">Impala is production triage. {html.escape(trino_label)} supports bounded retained-list Recent diagnosis when a query-list source contract is configured, one explicit Query ID through a local raw-free coordinator QueryInfo contract, a raw-free Details view after case materialization, deterministic Python Report, and optimizer guidance from that Details view. Running scans, query-history crawling, metadata collection, LLM reports, Query Optimizer jobs, generated SQL, and SQL execution remain unavailable. '
        f"{html.escape(readiness_note)}</div>"
        "</details></div>"
        '<div class="segmented engine-segmented" role="radiogroup" aria-labelledby="engine_label">'
        f"{option('impala', 'Impala', '', disabled=not impala_ready)}"
        f"{option('trino', trino_label, '', disabled=not trino_beta_ready)}"
        "</div>"
        "</div>"
    )


def workflow_value(diagnosis_target: str, scan_target: str) -> str:
    if diagnosis_target == "query":
        return "query"
    return "running" if scan_target == "running" else "finished"


def render_workflow_control(diagnosis_target: str, scan_target: str) -> str:
    selected = workflow_value(diagnosis_target, scan_target)

    def workflow_option(value: str, label: str, help_text: str) -> str:
        checked = " checked" if selected == value else ""
        return (
            "<label>"
            f'<input type="radio" name="diagnosis_workflow" value="{value}" '
            f"data-diagnosis-workflow-choice{checked}>"
            f"<span><strong>{html.escape(label)}</strong><small>{html.escape(help_text)}</small></span>"
            "</label>"
        )

    diagnosis_recent_checked = " checked" if selected != "query" else ""
    diagnosis_query_checked = " checked" if selected == "query" else ""
    scan_finished_checked = " checked" if selected != "running" else ""
    scan_running_checked = " checked" if selected == "running" else ""
    return (
        '<div class="mode-control workflow-control" aria-label="What to analyze">'
        '<div class="label-row">'
        '<span class="mode-label" id="workflow_label">What to analyze</span>'
        '<details class="info-popover">'
        '<summary aria-label="What to analyze help">i</summary>'
        '<div class="info-body">Choose the workflow first: completed queries for normal triage, running queries for a live lower-confidence snapshot, or one known Query ID. No workflow auto-runs LLM actions.</div>'
        "</details></div>"
        '<div class="segmented workflow-segmented" role="radiogroup" aria-labelledby="workflow_label">'
        f"{workflow_option('finished', 'Finished queries', 'Default triage')}"
        f"{workflow_option('running', 'Running now', 'Live snapshot')}"
        f"{workflow_option('query', 'One Query ID', 'Known query')}"
        "</div>"
        '<div class="workflow-state-inputs" aria-hidden="true" hidden>'
        f'<input type="radio" name="diagnosis_target" value="recent" data-diagnosis-target-choice{diagnosis_recent_checked}>'
        f'<input type="radio" name="diagnosis_target" value="query" data-diagnosis-target-choice{diagnosis_query_checked}>'
        f'<input type="radio" name="scan_target" value="finished" data-scan-target-choice{scan_finished_checked}>'
        f'<input type="radio" name="scan_target" value="running" data-scan-target-choice{scan_running_checked}>'
        "</div>"
        "</div>"
    )


def render_diagnosis_target_control(selected_value: str) -> str:
    selected = selected_value if selected_value in {"recent", "query"} else "recent"

    def option(value: str, label: str) -> str:
        checked = " checked" if selected == value else ""
        return (
            "<label>"
            f'<input type="radio" name="diagnosis_target" value="{value}" data-diagnosis-target-choice{checked}>'
            f"<span>{html.escape(label)}</span>"
            "</label>"
        )

    return (
        '<div class="mode-control diagnosis-target-control" aria-label="What to analyze">'
        '<div class="label-row">'
        '<span class="mode-label">What to analyze</span>'
        '<details class="info-popover">'
        '<summary aria-label="What to analyze help">i</summary>'
        '<div class="info-body">Use Recent queries for batch triage from the selected source. '
        "Use One Query ID when you already have one explicit Impala Query ID. "
        "Both workflows run deterministic analysis and do not auto-run LLM actions.</div>"
        "</details></div>"
        '<div class="segmented">'
        f"{option('recent', 'Recent queries')}"
        f"{option('query', 'One Query ID')}"
        "</div>"
        "</div>"
    )


def render_global_cluster_select(settings: Any, selected_value: str) -> str:
    select_html = render_cluster_select(
        settings,
        selected_value,
        field_id="diagnosis_cluster_key",
        field_class="field diagnosis-cluster-field",
        label_text="Source cluster",
    )
    if not select_html:
        return ""
    return (
        f'<div class="diagnosis-cluster-control" data-diagnosis-cluster-control>{select_html}</div>'
    )


def render_source_settings(settings: Any, selected_value: str) -> str:
    cluster_html = render_global_cluster_select(settings, selected_value)
    if not cluster_html:
        return ""
    return f'<div class="batch-source-settings">{cluster_html}</div>'


def render_hidden_cluster_input(value: str) -> str:
    return f'<input type="hidden" name="cluster_key" value="{html.escape(str(value or ""), quote=True)}">'


def render_hidden_engine_input(value: str) -> str:
    return (
        '<input type="hidden" name="engine" '
        f'value="{html.escape(normalize_engine_value(value), quote=True)}" data-engine-hidden>'
    )


def render_hidden_scan_target_input(value: str) -> str:
    normalized = "running" if value == "running" else "finished"
    return (
        '<input type="hidden" name="scan_target" '
        f'value="{html.escape(normalized, quote=True)}" data-scan-target-hidden>'
    )


def render_hidden_query_inbox_scope_inputs(values: dict[str, Any] | None) -> str:
    scope_query = query_inbox_scope_filter_query_from_mapping(values)
    return "".join(
        '<input type="hidden" '
        f'name="{html.escape(name, quote=True)}" '
        f'value="{html.escape(value, quote=True)}" '
        "data-inbox-scope-hidden>"
        for name, value in scope_query.items()
    )


def render_known_query_form(
    settings: Any | None = None,
    *,
    cluster_key: str = "",
    engine: str = "impala",
    query_id: str = "",
    run_disabled: bool = False,
) -> str:
    query_value = html.escape(query_id, quote=True)
    disabled_attr = " disabled" if run_disabled else ""
    selected_engine = normalize_engine_value(engine)
    if selected_engine == "trino":
        trino_label = trino_display_label(settings)
        button_label = "Running" if run_disabled else f"Run {trino_label}"
        label_text = "Trino Query ID"
        placeholder = "20260603_120102_00001_abcde"
        query_help_text = (
            "One explicit Trino Query ID. Query Doctor reads one bounded, pruned "
            "coordinator QueryInfo payload through local Trino config and renders compact "
            "raw-free diagnosis. A raw-free Details view, deterministic Python Report, and optimizer guidance "
            "appear only after case materialization. Running scans, query-history crawling, "
            "metadata collection, LLM reports, Query Optimizer jobs, generated SQL, and SQL "
            "execution remain unavailable."
        )
    else:
        button_label = "Running" if run_disabled else "Run"
        label_text = "Query ID"
        placeholder = "aaaaaaaaaaaaaaaa:0000000000000001"
        query_help_text = (
            "One explicit Query ID. Query Doctor collects or reuses the profile, "
            "runs deterministic analysis, adds metadata when configured, prepares the Python report, "
            "and does not auto-run LLM or optimizer actions. "
            "Recent-query filters stay hidden in this mode."
        )
    return (
        '<form id="analyze-form" class="run-form" method="post" action="/analyze">'
        f"{render_hidden_cluster_input(cluster_key) if settings is not None else ''}"
        f"{render_hidden_engine_input(selected_engine)}"
        '<div class="run-main-row known-query-row">'
        '<div class="field">'
        f"{render_label_with_info('query_id', label_text, query_help_text)}"
        f'<input class="input" id="query_id" name="query_id" type="text" value="{query_value}" '
        f'autocomplete="off" required placeholder="{html.escape(placeholder, quote=True)}">'
        "</div>"
        f'<button class="run-button" type="submit"{disabled_attr}>{button_label}</button>'
        "</div>"
        "</form>"
        + (
            render_profile_upload_form(
                settings,
                cluster_key=cluster_key,
                query_id=query_id,
                disabled_attr=disabled_attr,
            )
            if selected_engine != "trino"
            and settings is not None
            and not getattr(settings, "public_demo", False)
            else ""
        )
    )


def render_profile_upload_form(
    settings: Any | None,
    *,
    cluster_key: str,
    query_id: str,
    disabled_attr: str = "",
) -> str:
    query_value = html.escape(query_id, quote=True)
    return (
        '<form id="profile-upload-form" class="run-form profile-upload-form" method="post" '
        'action="/profile/upload" enctype="multipart/form-data">'
        f"{render_hidden_cluster_input(cluster_key) if settings is not None else ''}"
        f"{render_hidden_engine_input('impala')}"
        '<div class="run-main-row known-query-row profile-upload-row">'
        '<div class="field">'
        f"{render_label_with_info('upload_query_id', 'Profile Query ID', 'One explicit Impala Query ID matching the exported text profile. The uploaded profile is staged locally and is not rendered back in trusted browser surfaces.')}"
        f'<input class="input" id="upload_query_id" name="query_id" type="text" value="{query_value}" '
        'autocomplete="off" required placeholder="aaaaaaaaaaaaaaaa:0000000000000001">'
        "</div>"
        '<div class="field">'
        f"{render_label_with_info('profile_file', 'Exported profile', 'Exported Impala text profile from the Impala Web UI. JSON, Thrift, and profile-v2 payloads are rejected for this intake.')}"
        '<input class="input" id="profile_file" name="profile_file" type="file" '
        'accept=".txt,.profile,.log,.text" required>'
        "</div>"
        f'<button class="run-button" type="submit"{disabled_attr}>Upload</button>'
        "</div>"
        "</form>"
    )


def render_scan_target_control(selected_value: str) -> str:
    selected_raw = html.unescape(str(selected_value or "finished"))
    if selected_raw not in {"finished", "running"}:
        selected_raw = "finished"

    def option(value: str, label: str) -> str:
        checked = " checked" if value == selected_raw else ""
        safe_value = html.escape(value, quote=True)
        safe_label = html.escape(label)
        return (
            "<label>"
            f'<input type="radio" name="scan_target" value="{safe_value}" data-scan-target-choice{checked}>'
            f"<span>{safe_label}</span>"
            "</label>"
        )

    return (
        '<div class="field scan-target-field">'
        '<div class="label-row">'
        '<label id="scan_target_label">Scan target</label>'
        '<details class="info-popover">'
        '<summary aria-label="Scan target help">i</summary>'
        '<div class="info-body">Choose Finished queries or Running now, then run the scan and open rows marked High or Failed. '
        "Details starts with the recommended change path. Finished queries have complete profile evidence. Running queries are useful "
        "for live inspection but may have incomplete analyzer facts until execution finishes.</div>"
        "</details></div>"
        '<div class="segmented scan-target-segmented" role="radiogroup" aria-labelledby="scan_target_label">'
        f"{option('finished', 'Finished queries')}"
        f"{option('running', 'Running now')}"
        "</div>"
        "</div>"
    )


def render_running_scan_framing_note(
    *, extra_class: str = "", data_scan_target: bool = False
) -> str:
    data_attr = ' data-scan-target-field="running"' if data_scan_target else ""
    return (
        f'<div class="batch-note batch-note--running-confidence{extra_class}"{data_attr}>'
        f"<strong>Live snapshot:</strong> {html.escape(RUNNING_SCAN_FRAMING_TEXT)}"
        "</div>"
    )


def default_recent_scan_bucket(
    now: datetime | None = None, *, scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE
) -> tuple[str, int]:
    current = now.astimezone(scan_timezone) if now else datetime.now(scan_timezone)
    bucket = current.replace(minute=0, second=0, microsecond=0)
    return bucket.date().isoformat(), bucket.hour


def recent_scan_date_options(
    now: datetime | None = None, *, scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE
) -> list[tuple[str, str]]:
    current = now.astimezone(scan_timezone).date() if now else datetime.now(scan_timezone).date()
    return [
        (
            (current - timedelta(days=days)).isoformat(),
            (current - timedelta(days=days)).strftime("%d.%m.%Y"),
        )
        for days in range(RECENT_SCAN_LOOKBACK_DAYS + 1)
    ]


def render_scan_date_select(
    selected_value: str, *, scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE
) -> str:
    selected_raw = html.unescape(str(selected_value))
    options = recent_scan_date_options(scan_timezone=scan_timezone)
    option_values = {value for value, _ in options}
    if selected_raw and selected_raw not in option_values:
        options.append((selected_raw, f"{selected_raw} (selected)"))
    hour_options_by_date = scan_hour_options_by_date(options, scan_timezone=scan_timezone)
    hour_options_json = html.escape(json.dumps(hour_options_by_date), quote=True)
    rendered_options = "".join(
        f'<option value="{html.escape(value, quote=True)}"{" selected" if value == selected_raw else ""}>'
        f"{html.escape(label)}</option>"
        for value, label in options
    )
    return (
        '<div class="field">'
        f"{render_label_with_info('scan_date', 'Scan date', 'Calendar day to inspect. Query Doctor keeps this bounded to today and the previous two days.')}"
        f'<select class="input" id="scan_date" name="scan_date" data-scan-hour-options="{hour_options_json}">{rendered_options}</select>'
        "</div>"
    )


def latest_selectable_scan_bucket(
    now: datetime | None = None, *, scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE
) -> tuple[str, int]:
    return default_recent_scan_bucket(now, scan_timezone=scan_timezone)


def scan_hour_options(
    scan_date: str,
    now: datetime | None = None,
    *,
    scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE,
) -> list[tuple[str, str]]:
    latest_date, latest_hour = latest_selectable_scan_bucket(now, scan_timezone=scan_timezone)
    max_hour = latest_hour if scan_date == latest_date else 23
    if scan_date > latest_date:
        max_hour = -1
    return [
        (str(hour), f"{hour:02d}:00 - {(hour + 1) % 24:02d}:00") for hour in range(max_hour + 1)
    ]


def scan_hour_options_by_date(
    date_options: list[tuple[str, str]],
    now: datetime | None = None,
    *,
    scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE,
) -> dict[str, list[tuple[str, str]]]:
    return {
        date_value: scan_hour_options(date_value, now=now, scan_timezone=scan_timezone)
        for date_value, _ in date_options
    }


def render_scan_hour_select(
    selected_value: str,
    *,
    scan_date: str = "",
    scan_timezone: ZoneInfo = RECENT_SCAN_TIMEZONE,
) -> str:
    selected_raw = html.unescape(str(selected_value))
    selected_date = html.unescape(str(scan_date))
    options = scan_hour_options(
        selected_date or default_recent_scan_bucket(scan_timezone=scan_timezone)[0],
        scan_timezone=scan_timezone,
    )
    option_values = {value for value, _label in options}
    if selected_raw and selected_raw not in option_values:
        try:
            selected_hour = int(selected_raw)
        except ValueError:
            selected_hour = -1
        if 0 <= selected_hour <= 23:
            options.append(
                (
                    selected_raw,
                    f"{selected_hour:02d}:00 - {(selected_hour + 1) % 24:02d}:00 (selected)",
                )
            )
    rendered_options = ""
    for value, label in options:
        rendered_options += (
            f'<option value="{value}"{" selected" if value == selected_raw else ""}>'
            f"{html.escape(label)}</option>"
        )
    timezone_label = utc_offset_label(scan_timezone)
    help_text = (
        "One configured local-hour CM window to inspect. Times are shown in the "
        "configured scan timezone and sent to CM as UTC bounds."
    )
    return (
        '<div class="field">'
        f"{render_label_with_info('scan_hour', f'Scan Hour ({timezone_label})', help_text)}"
        f'<select class="input" id="scan_hour" name="scan_hour">{rendered_options}</select>'
        "</div>"
    )


def render_cluster_select(
    settings: Any,
    selected_value: str,
    *,
    field_id: str = "cluster_key",
    field_class: str = "field",
    label_text: str = "Cluster",
) -> str:
    options = cluster_select_options(settings)
    if not options:
        return ""
    selected_raw = html.unescape(str(selected_value or default_cluster_key(settings)))
    option_values = {value for value, _label in options}
    if selected_raw not in option_values:
        selected_raw = default_cluster_key(settings)
    trino_ready_by_key = {
        getattr(cluster, "key", ""): cluster_trino_beta_query_ready(cluster)
        for cluster in getattr(settings, "clusters", ())
    }
    trino_recent_ready_by_key = {
        getattr(cluster, "key", ""): cluster_trino_beta_recent_ready(cluster)
        for cluster in getattr(settings, "clusters", ())
    }
    impala_ready_by_key = {
        getattr(cluster, "key", ""): cluster_impala_ready(cluster)
        for cluster in getattr(settings, "clusters", ())
    }
    cluster_by_key = {
        getattr(cluster, "key", ""): cluster for cluster in getattr(settings, "clusters", ())
    }

    def render_option(value: str, label: str) -> str:
        trino_ready = bool(trino_ready_by_key.get(value) or trino_recent_ready_by_key.get(value))
        trino_label_attr = ""
        if trino_ready:
            trino_label_attr = (
                ' data-trino-display-label="'
                f'{html.escape(trino_display_label(cluster_by_key.get(value)), quote=True)}"'
            )
        return (
            f'<option value="{html.escape(value, quote=True)}"{" selected" if value == selected_raw else ""} '
            f'data-engine-impala-ready="{"true" if impala_ready_by_key.get(value, True) else "false"}" '
            f'data-engine-trino-ready="{"true" if trino_ready else "false"}" '
            f'data-trino-beta-query-ready="{"true" if trino_ready_by_key.get(value) else "false"}" '
            f'data-trino-beta-recent-ready="{"true" if trino_recent_ready_by_key.get(value) else "false"}"'
            f"{trino_label_attr}>"
            f"{html.escape(label)}</option>"
        )

    rendered_options = "".join(render_option(value, label) for value, label in options)
    help_text = (
        "Clusters are loaded from local config. Recent and Running scans use the selected "
        "cluster's Cloudera Manager settings; Known Query ID uses the selected cluster's configured profile source. "
        "Credentials and endpoints stay in local config. Direct Impala clusters can add Prometheus runtime metrics when configured."
    )
    safe_field_id = html.escape(field_id, quote=True)
    safe_field_class = html.escape(field_class, quote=True)
    return (
        f'<div class="{safe_field_class}">'
        f"{render_label_with_info(field_id, label_text, help_text)}"
        f'<select class="input" id="{safe_field_id}" name="cluster_key">{rendered_options}</select>'
        "</div>"
    )


def read_local_config_values(settings: Any) -> dict[str, object]:
    config_path = getattr(settings, "config", None)
    if config_path is None:
        return {}
    try:
        path = Path(config_path).expanduser()
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def form_or_config_value(
    form_values: dict[str, Any] | None,
    form_key: str,
    *,
    config_values: dict[str, object],
    config_key: str | None = None,
    fallback: str = "",
    maximum: int | None = None,
) -> str:
    if form_values is not None and form_key in form_values:
        value = form_values.get(form_key)
        if value is not None:
            return str(value)
    value = config_values.get(config_key or form_key)
    if isinstance(value, bool) or value is None:
        return fallback
    if isinstance(value, (int, float, str)):
        text = str(value)
        if maximum is not None:
            try:
                if int(float(text)) > maximum:
                    return str(maximum)
            except (TypeError, ValueError):
                pass
        return text
    return fallback


def form_or_config_bool(
    form_values: dict[str, Any] | None,
    form_key: str,
    *,
    config_values: dict[str, object],
    config_key: str | None = None,
    fallback: bool = False,
) -> bool:
    if form_values is not None and form_key in form_values:
        return bool(form_values.get(form_key))
    value = config_values.get(config_key or form_key)
    if isinstance(value, bool):
        return value
    return fallback


def render_batch_number_field(
    name: str,
    label: str,
    value: str,
    *,
    step: str = "1",
    required: bool = True,
    help_text: str = "",
) -> str:
    required_attr = " required" if required else ""
    return (
        f'<div class="field">{render_label_with_info(name, label, help_text)}'
        f'<input class="input" id="{html.escape(name, quote=True)}" name="{html.escape(name, quote=True)}" '
        f'type="number" min="0" step="{html.escape(step, quote=True)}" value="{value}"{required_attr}>'
        "</div>"
    )


def render_recent_window_field(value: str) -> str:
    value_text = str(value or "")
    warning_hidden_class = ""
    try:
        minutes = int(value_text)
    except ValueError:
        minutes = 0
    if minutes <= LARGE_RECENT_WINDOW_WARNING_MINUTES:
        warning_hidden_class = " manual-inputs-hidden"
    warning_text = (
        "Large Search depth values can increase load on Cloudera Manager, direct Impala UI endpoints, "
        "and optional Prometheus collection. Narrow the scan with user, pool, query type, or duration filters when possible."
    )
    return (
        '<div class="field" data-recent-window-field '
        f'data-large-window-threshold="{LARGE_RECENT_WINDOW_WARNING_MINUTES}">'
        f"{render_label_with_info('recent_window_minutes', 'Search depth (min)', 'Query summary lookback for the selected source. Profile and metadata collection remain bounded by analysis limits.')}"
        f'<input class="input" id="recent_window_minutes" name="recent_window_minutes" '
        f'type="number" min="1" step="1" value="{html.escape(value_text, quote=True)}" required data-recent-window-input>'
        f'<div class="batch-note batch-note--large-window{warning_hidden_class}" '
        f"data-large-window-warning>{html.escape(warning_text)}</div>"
        "</div>"
    )


def render_batch_text_field(
    name: str,
    label: str,
    value: str,
    *,
    help_text: str = "",
) -> str:
    return (
        f'<div class="field">{render_label_with_info(name, label, help_text)}'
        f'<input class="input" id="{html.escape(name, quote=True)}" name="{html.escape(name, quote=True)}" '
        f'type="text" value="{value}" autocomplete="off" data-server-owned-default></div>'
    )


def render_batch_user_field(
    name: str,
    label: str,
    value: str,
    *,
    user_options: tuple[str, ...],
    owner_required: bool = False,
    disabled_reason: str = "",
    help_text: str = "",
) -> str:
    if not user_options:
        if disabled_reason:
            return render_disabled_owner_select(name, label, help_text=help_text)
        return render_batch_text_field(
            name,
            label,
            value,
            help_text=help_text,
        )
    selected_raw = html.unescape(str(value or ""))
    options = sorted(
        dict.fromkeys(str(option) for option in user_options if option),
        key=lambda option: (option.casefold(), option),
    )
    if selected_raw and selected_raw not in options:
        options.append(selected_raw)
    rendered_options = ""
    if owner_required:
        rendered_options = (
            f'<option value=""{" selected" if not selected_raw else ""}>'
            "All configured owners</option>"
        )
    else:
        rendered_options = (
            f'<option value=""{" selected" if not selected_raw else ""}>All users</option>'
        )
    for option in options:
        safe_option = html.escape(option, quote=True)
        selected = " selected" if option == selected_raw else ""
        rendered_options += (
            f'<option value="{safe_option}"{selected}>{html.escape(option)}</option>'
        )
    return (
        f'<div class="field">{render_label_with_info(name, label, help_text)}'
        f'<select class="input" id="{html.escape(name, quote=True)}" name="{html.escape(name, quote=True)}" '
        f"data-server-owned-default>{rendered_options}</select></div>"
    )


def render_disabled_owner_select(name: str, label: str, *, help_text: str = "") -> str:
    safe_name = html.escape(name, quote=True)
    return (
        f'<div class="field">{render_label_with_info(name, label, help_text)}'
        f'<select class="input" id="{safe_name}" name="{safe_name}" disabled>'
        '<option value="" selected>No configured owner</option>'
        "</select></div>"
    )


def user_filter_options(settings: Any) -> tuple[str, ...]:
    return collectable_owner_users(
        getattr(settings, "source_owner_user", None),
        getattr(settings, "source_owner_user_options", ()),
    )


def owner_missing_reason() -> str:
    return "Owner-gated scans require a configured Username."


def render_batch_checkbox(name: str, label: str, checked: bool, *, help_text: str = "") -> str:
    safe_name = html.escape(name, quote=True)
    checked_attr = " checked" if checked else ""
    return (
        '<label class="batch-checkbox">'
        f'<input type="checkbox" id="{safe_name}" name="{safe_name}" value="on"{checked_attr}>'
        f"{html.escape(label)}"
        "</label>" + (f'<span class="helper">{html.escape(help_text)}</span>' if help_text else "")
    )


def render_label_with_info(field_id: str, label: str, help_text: str = "") -> str:
    safe_id = html.escape(field_id, quote=True)
    safe_label = html.escape(label)
    if not help_text:
        return f'<label for="{safe_id}">{safe_label}</label>'
    return (
        '<div class="label-row">'
        f'<label for="{safe_id}">{safe_label}</label>'
        '<details class="info-popover">'
        f'<summary aria-label="{safe_label} help">i</summary>'
        f'<div class="info-body">{html.escape(help_text)}</div>'
        "</details></div>"
    )
