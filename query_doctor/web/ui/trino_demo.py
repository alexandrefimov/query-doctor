"""Read-only synthetic Trino Beta demo rendering."""

from __future__ import annotations

import html
import json
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Mapping

from query_doctor.demo.trino_cases import TRINO_DEMO_SCHEMA_VERSION
from query_doctor.web.ui.trino import (
    badge_for_state,
    label_from_id,
    render_trino_attention_areas,
    render_trino_boundary,
    render_trino_compact_status,
    render_trino_diagnostic_lane,
    render_trino_limitations,
    safe_int,
    safe_status_label,
    text_value,
)


TRINO_DEMO_NAME = "trino_demo.json"


def render_trino_demo_sections(settings: Any) -> str:
    payload = load_trino_demo_payload(settings)
    if payload is None:
        return ""
    cases = tuple(case for case in payload.get("cases", ()) if isinstance(case, Mapping))
    if not cases:
        return ""
    return render_trino_demo_recent_section(payload, cases) + "".join(
        render_trino_demo_case_section(case, index=index)
        for index, case in enumerate(cases, start=1)
    )


def load_trino_demo_payload(settings: Any) -> dict[str, Any] | None:
    summary_path = getattr(settings, "batch_summary", None)
    if summary_path is None:
        return None
    try:
        summary_file = Path(summary_path)
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(summary, dict) or summary.get("demo_mode") is not True:
        return None
    try:
        payload = json.loads((summary_file.parent / TRINO_DEMO_NAME).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != TRINO_DEMO_SCHEMA_VERSION:
        return None
    return payload


def render_trino_demo_recent_section(
    payload: Mapping[str, Any],
    cases: tuple[Mapping[str, Any], ...],
) -> str:
    recent = payload.get("recent") if isinstance(payload.get("recent"), Mapping) else {}
    records_seen = safe_int(recent.get("records_seen"))
    records_selected = safe_int(recent.get("records_selected"))
    records_diagnosed = safe_int(recent.get("records_diagnosed"))
    query_bound = safe_int(recent.get("query_bound"))
    rows = "".join(render_trino_demo_row(case, index=index) for index, case in enumerate(cases, 1))
    return (
        '<section id="trino-demo-cases" class="panel batch-panel trino-demo-panel" '
        'aria-label="Trino Beta demo cases">'
        '<div class="batch-head"><div><h2>Trino Beta demo cases</h2>'
        "<p>Read-only synthetic compact diagnosis from raw-free demo facts. No coordinator, "
        "network, metadata collection, materialized Details, Python Report, optimizer "
        "guidance, generated SQL, or SQL execution is used.</p></div></div>"
        '<div class="status-strip" aria-label="Trino Beta demo status">'
        '<span class="status-item"><span class="dot"></span>Engine: '
        '<span class="badge gray">Trino Beta demo</span></span>'
        '<span class="status-item"><span class="dot gray"></span>Retained records: '
        f"<strong>{records_seen}</strong></span>"
        '<span class="status-item"><span class="dot gray"></span>Selected: '
        f"<strong>{records_selected}</strong></span>"
        '<span class="status-item"><span class="dot gray"></span>Diagnosed: '
        f"<strong>{records_diagnosed}</strong></span>"
        '<span class="status-item"><span class="dot gray"></span>Demo bound: '
        f"<strong>{query_bound}</strong></span>"
        "</div>"
        '<div class="batch-table-wrap">'
        '<table class="batch-table trino-demo-table">'
        "<thead><tr>"
        "<th>Demo Query ID</th><th>Status</th><th>Lifecycle</th><th>Coverage</th>"
        "<th>Attention</th><th>Safe note</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</div>"
        '<div class="source-locator-block trino-beta-boundary-note">'
        '<span class="source-locator-heading">Demo boundary</span>'
        "<p>These Trino Beta demo cases are static raw-free compact diagnosis examples. "
        "They are not live Trino collection, Running scans, query-history crawling, "
        "production support, Details pages, trusted or LLM reports, optimizer guidance "
        "or jobs, generated SQL drafts, or SQL execution.</p>"
        "</div>"
        "</section>"
    )


def render_trino_demo_row(case: Mapping[str, Any], *, index: int) -> str:
    query_id = text_value(case.get("query_id"))
    label = text_value(case.get("label")) or "Trino demo case"
    status = safe_status_label(case.get("status"))
    lifecycle = safe_status_label(case.get("lifecycle"))
    coverage = safe_status_label(case.get("parser_coverage"))
    raw_attention_values = case.get("attention_areas")
    attention_values = (
        raw_attention_values
        if isinstance(raw_attention_values, Sequence)
        and not isinstance(raw_attention_values, (str, bytes, bytearray))
        else ()
    )
    attention = ", ".join(
        label_from_id(area, fallback="Attention")
        for area in attention_values
        if isinstance(area, str) and area
    )
    if not attention:
        attention = "No supported attention areas"
    safe_note = text_value(case.get("safe_note")) or label
    anchor = demo_case_anchor(case, index=index)
    return (
        "<tr>"
        f'<td><a class="trino-demo-query-link" href="#{html.escape(anchor, quote=True)}">'
        f"<code>{html.escape(query_id)}</code></a></td>"
        f'<td><span class="badge {badge_for_state(status)}">{html.escape(status)}</span></td>'
        f'<td><span class="badge gray">{html.escape(lifecycle)}</span></td>'
        f'<td><span class="badge {badge_for_state(coverage)}">{html.escape(coverage)}</span></td>'
        f"<td>{html.escape(attention)}</td>"
        f"<td>{html.escape(safe_note)}</td>"
        "</tr>"
    )


def render_trino_demo_case_section(case: Mapping[str, Any], *, index: int) -> str:
    diagnosis = case.get("diagnosis")
    if not isinstance(diagnosis, Mapping):
        return ""
    label = text_value(case.get("label")) or f"Trino demo case {index}"
    query_id = text_value(case.get("query_id"))
    anchor = demo_case_anchor(case, index=index)
    return (
        f'<section id="{html.escape(anchor, quote=True)}" '
        'class="panel optimizer-result trino-compact-result trino-demo-case" '
        'aria-label="Trino Beta demo diagnosis result">'
        '<div class="section-heading"><div>'
        f'<h2 class="section-title">Trino Beta demo: {html.escape(label)}</h2>'
        '<div class="section-kicker">Deterministic compact-fact checks only. '
        "Root cause is not claimed.</div>"
        f'<div class="query-line"><span>Demo Query:</span><code>{html.escape(query_id)}</code></div>'
        "</div></div>"
        f"{render_trino_compact_status(diagnosis)}"
        f"{render_trino_diagnostic_lane(diagnosis.get('diagnostic_lane'))}"
        f"{render_trino_attention_areas(diagnosis.get('attention_areas'))}"
        f"{render_trino_limitations(diagnosis.get('limitations'))}"
        f"{render_trino_boundary(diagnosis.get('diagnosis_boundary'))}"
        "</section>"
    )


def demo_case_anchor(case: Mapping[str, Any], *, index: int) -> str:
    raw = text_value(case.get("case_id")) or f"trino-demo-{index:03d}"
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in raw.lower())
    return safe or f"trino-demo-{index:03d}"
