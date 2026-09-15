"""Action-candidate rendering helpers for recent scan details."""

from __future__ import annotations

import html

from query_doctor.web.action_outcomes import (
    ActionOutcomeRecord,
    RecommendationOutcomeMetric,
    action_outcome_metrics_by_recommendation,
    latest_case_action_outcomes,
    recommendation_id_allowed,
    safe_recommendation_label,
)
from query_doctor.web.presenters.recent_scan import (
    RecentScanActionCandidateCardView,
    RecentScanActionCandidatesView,
    RecentScanCaseDetailView,
    RecentScanSourceLocatorView,
    present_recent_scan_action_candidates,
)
from query_doctor.web.presenters.recent_scan_models import RecentScanDiagnosticFactView
from query_doctor.web.ui.diagnostic_i18n import localize_diagnostic_text
from query_doctor.web.ui.html_helpers import escape_value
from query_doctor.web.ui.i18n import text as ui_text
from query_doctor.web.ui.source_locations import (
    render_redacted_sql_source_map,
    render_source_location_chips,
)


def render_action_candidate_findings(
    view: RecentScanCaseDetailView,
    *,
    detail_base_path: str = "/batch/case",
    language: str = "en",
) -> str:
    return render_action_candidate_findings_view(
        present_recent_scan_action_candidates(view),
        case_id=view.case_id,
        workload_fingerprint=view.workload_fingerprint,
        detail_base_path=detail_base_path,
        recorded_outcomes=latest_case_action_outcomes(view.workload_fingerprint, view.query_id)
        if view.case_id
        else {},
        language=language,
    )


def render_action_candidate_decision_findings(
    view: RecentScanCaseDetailView,
    *,
    detail_base_path: str = "/batch/case",
    language: str = "en",
) -> str:
    return render_action_candidate_decision_findings_view(
        present_recent_scan_action_candidates(view),
        case_id=view.case_id,
        workload_fingerprint=view.workload_fingerprint,
        detail_base_path=detail_base_path,
        recorded_outcomes=latest_case_action_outcomes(view.workload_fingerprint, view.query_id)
        if view.case_id
        else {},
        language=language,
    )


def render_action_candidate_findings_view(
    view: RecentScanActionCandidatesView,
    *,
    case_id: str = "",
    workload_fingerprint: str = "",
    detail_base_path: str = "/batch/case",
    recorded_outcomes: dict[str, ActionOutcomeRecord] | None = None,
    language: str = "en",
) -> str:
    if not view.cards:
        return ""
    outcome_metrics = (
        action_outcome_metrics_by_recommendation() if case_id and workload_fingerprint else {}
    )
    cards = "".join(
        render_action_candidate_card_view(
            card,
            case_id=case_id,
            workload_fingerprint=workload_fingerprint,
            detail_base_path=detail_base_path,
            outcome_metric=outcome_metrics.get(card.recommendation_id),
            recorded_outcome=(recorded_outcomes or {}).get(card.recommendation_id),
            language=language,
        )
        for card in view.cards
    )
    return f'<ul class="reason-list action-candidate-list">{cards}</ul>'


def render_action_candidate_decision_findings_view(
    view: RecentScanActionCandidatesView,
    *,
    case_id: str = "",
    workload_fingerprint: str = "",
    detail_base_path: str = "/batch/case",
    recorded_outcomes: dict[str, ActionOutcomeRecord] | None = None,
    language: str = "en",
) -> str:
    if not view.cards:
        return ""
    outcome_metrics = (
        action_outcome_metrics_by_recommendation() if case_id and workload_fingerprint else {}
    )
    primary = render_action_candidate_card_view(
        view.cards[0],
        case_id=case_id,
        workload_fingerprint=workload_fingerprint,
        detail_base_path=detail_base_path,
        outcome_metric=outcome_metrics.get(view.cards[0].recommendation_id),
        recorded_outcome=(recorded_outcomes or {}).get(view.cards[0].recommendation_id),
        language=language,
        primary=True,
    )
    additional = render_additional_action_candidates(
        view.cards[1:],
        case_id=case_id,
        workload_fingerprint=workload_fingerprint,
        detail_base_path=detail_base_path,
        outcome_metrics=outcome_metrics,
        recorded_outcomes=recorded_outcomes or {},
        language=language,
    )
    return f'<ul class="reason-list action-candidate-list action-candidate-list--primary">{primary}</ul>{additional}'


def render_additional_action_candidates(
    cards: tuple[RecentScanActionCandidateCardView, ...],
    *,
    case_id: str,
    workload_fingerprint: str,
    detail_base_path: str,
    outcome_metrics: dict[str, RecommendationOutcomeMetric],
    recorded_outcomes: dict[str, ActionOutcomeRecord],
    language: str,
) -> str:
    if not cards:
        return ""
    rendered = "".join(
        render_action_candidate_card_view(
            card,
            case_id=case_id,
            workload_fingerprint=workload_fingerprint,
            detail_base_path=detail_base_path,
            outcome_metric=outcome_metrics.get(card.recommendation_id),
            recorded_outcome=recorded_outcomes.get(card.recommendation_id),
            language=language,
        )
        for card in cards
    )
    count = len(cards)
    note = ui_text(
        language,
        "Use these only after reviewing the primary recommendation above.",
        "Используйте это только после проверки основной рекомендации выше.",
    )
    return (
        '<details class="analysis-subdetails additional-action-candidates">'
        f"<summary>{html.escape(f'Additional supported actions ({count})')}</summary>"
        f'<p class="helper">{html.escape(note)}</p>'
        f'<ul class="reason-list action-candidate-list">{rendered}</ul>'
        "</details>"
    )


def render_action_candidate_card_view(
    card: RecentScanActionCandidateCardView,
    *,
    case_id: str = "",
    workload_fingerprint: str = "",
    detail_base_path: str = "/batch/case",
    outcome_metric: RecommendationOutcomeMetric | None = None,
    recorded_outcome: ActionOutcomeRecord | None = None,
    language: str = "en",
    primary: bool = False,
) -> str:
    css_class = "reason-card action-candidate-card"
    if primary:
        css_class += " action-candidate-card--primary"
    return (
        f'<li class="{css_class}">'
        f"<strong>{html.escape(card.title)}</strong>"
        f"{render_action_candidate_sections(card, language=language, primary=primary)}"
        f"{render_recorded_action_outcome(recorded_outcome, language=language)}"
        f"{render_action_outcome_controls(card, case_id=case_id, workload_fingerprint=workload_fingerprint, detail_base_path=detail_base_path, outcome_metric=outcome_metric, language=language)}"
        f"{render_supporting_facts(card.supporting_facts, language=language)}"
        f"{render_action_candidate_guardrails(card.guardrails, language=language)}"
        f"{render_action_candidate_meta(card.body, language=language)}"
        "</li>"
    )


def render_supporting_facts(
    facts: tuple[RecentScanDiagnosticFactView, ...], *, language: str = "en"
) -> str:
    if not facts:
        return ""
    items = "".join(render_supporting_fact(fact, language=language) for fact in facts[:4])
    return (
        '<div class="action-supporting-facts" aria-label="Evidence behind recommendation">'
        '<span class="source-locator-heading">Evidence behind this recommendation</span>'
        f'<ul class="action-supporting-fact-list">{items}</ul>'
        "</div>"
    )


def render_supporting_fact(fact: RecentScanDiagnosticFactView, *, language: str = "en") -> str:
    del language
    label = html.escape(fact.label)
    if fact.source_anchor:
        anchor = html.escape(f"#{fact.source_anchor}", quote=True)
        label = f'<a href="{anchor}">{label}</a>'
    question = f' title="{html.escape(fact.question, quote=True)}"' if fact.question else ""
    return (
        f'<li class="action-supporting-fact action-supporting-fact--{html.escape(fact.severity, quote=True)}">'
        f"<span{question}>{label}</span>"
        f"<strong>{escape_value(fact.value)}</strong>"
        "</li>"
    )


def render_source_locators(
    locators: tuple[RecentScanSourceLocatorView, ...], *, language: str = "en"
) -> str:
    if not locators:
        return ""
    items = "".join(render_source_locator(locator, language=language) for locator in locators[:5])
    source_map = render_redacted_sql_source_map(locators)
    return (
        '<div class="source-locator-block" aria-label="Safe review locations">'
        f'<ul class="source-locator-list">{items}</ul>'
        f"{source_map}"
        "</div>"
    )


def render_source_locator(locator: RecentScanSourceLocatorView, *, language: str = "en") -> str:
    del language
    coordinate = f" ({escape_value(locator.coordinate)})" if locator.coordinate else ""
    detail = f": {escape_value(locator.detail)}" if locator.detail else ""
    chips = render_source_location_chips((locator,), limit=1)
    return (
        f'<li class="source-locator source-locator--{html.escape(locator.kind, quote=True)}">'
        f"{chips}<span>{escape_value(locator.label)}{coordinate}{detail}</span>"
        "</li>"
    )


def render_action_candidate_sections(
    card: RecentScanActionCandidateCardView, *, language: str = "en", primary: bool = False
) -> str:
    if primary:
        sections = (
            render_action_candidate_section(
                "What to try next",
                card.change_direction,
                modifier_class="action-candidate-section--change",
                language=language,
            ),
            render_action_candidate_section(
                "How to verify",
                card.verification,
                modifier_class="action-candidate-section--verify",
                language=language,
            ),
            render_action_candidate_location_section(
                card.source_locators,
                card.supporting_facts,
                language=language,
            ),
            render_action_candidate_section(
                "Why this query matters",
                card.why,
                modifier_class="action-candidate-section--why",
                language=language,
            ),
        )
    else:
        sections = (
            render_action_candidate_section(
                "What to change",
                card.change_direction,
                modifier_class="action-candidate-section--change",
                language=language,
            ),
            render_action_candidate_section(
                "How to verify",
                card.verification,
                modifier_class="action-candidate-section--verify",
                language=language,
            ),
            render_action_candidate_location_section(
                card.source_locators,
                card.supporting_facts,
                language=language,
            ),
            render_action_candidate_reason_section(card.why, language=language),
        )
    rendered = "".join(section for section in sections if section)
    if not rendered:
        return ""
    return f'<div class="action-candidate-sections">{rendered}</div>'


def render_action_candidate_guardrails(text: str, *, language: str = "en") -> str:
    if not text:
        return ""
    return (
        '<details class="analysis-subdetails action-guardrails" '
        'aria-label="Recommendation guardrails">'
        "<summary>Technical guardrails</summary>"
        f'<p class="helper">{escape_value(localize_diagnostic_text(text, language))}</p>'
        "</details>"
    )


def render_action_candidate_section(
    label: str, text: str, *, modifier_class: str = "", language: str = "en"
) -> str:
    if not text:
        return ""
    class_name = "action-candidate-section"
    if modifier_class:
        class_name = f"{class_name} {modifier_class}"
    return (
        f'<section class="{class_name}">'
        f"<span>{html.escape(label)}</span>"
        f"<p>{escape_value(localize_diagnostic_text(text, language))}</p>"
        "</section>"
    )


def render_action_candidate_reason_section(text: str, *, language: str = "en") -> str:
    if not text:
        return ""
    return (
        '<details class="analysis-subdetails action-candidate-reason" '
        'aria-label="Why this deserves attention">'
        "<summary>Why this deserves attention</summary>"
        f'<p class="helper">{escape_value(localize_diagnostic_text(text, language))}</p>'
        "</details>"
    )


def render_action_candidate_location_section(
    locators: tuple[RecentScanSourceLocatorView, ...],
    fallback_facts: tuple[RecentScanDiagnosticFactView, ...] = (),
    *,
    language: str = "en",
) -> str:
    locator_html = render_source_locators(locators, language=language)
    if not locator_html:
        locator_html = render_fact_review_anchors(fallback_facts, language=language)
    if not locator_html:
        return ""
    return (
        '<section class="action-candidate-section action-candidate-section--locations">'
        "<span>Where to inspect</span>"
        f"{locator_html}"
        "</section>"
    )


def render_fact_review_anchors(
    facts: tuple[RecentScanDiagnosticFactView, ...], *, language: str = "en"
) -> str:
    anchor_facts = tuple(
        fact for fact in facts if fact.source_anchor and fact.source_anchor != "action-plan"
    )[:3]
    if not anchor_facts:
        return ""
    items = "".join(render_fact_review_anchor(fact, language=language) for fact in anchor_facts)
    return (
        '<div class="source-locator-block" aria-label="Safe review locations">'
        f'<ul class="source-locator-list">{items}</ul>'
        "</div>"
    )


def render_fact_review_anchor(fact: RecentScanDiagnosticFactView, *, language: str = "en") -> str:
    del language
    anchor = html.escape(f"#{fact.source_anchor}", quote=True)
    label = escape_value(fact.label)
    value = escape_value(fact.value)
    return (
        '<li class="source-locator source-locator--fact">'
        f'<a href="{anchor}">{label}</a>'
        f"<span>: {value}</span>"
        "</li>"
    )


def render_action_candidate_meta(text: str, *, language: str = "en") -> str:
    if not text:
        return ""
    return (
        '<div class="action-candidate-meta" aria-label="Recommendation candidate details">'
        '<span class="source-locator-heading">Candidate details</span>'
        f'<p class="helper">{escape_value(localize_diagnostic_text(text, language))}</p>'
        "</div>"
    )


def render_recorded_action_outcome(
    record: ActionOutcomeRecord | None, *, language: str = "en"
) -> str:
    if record is None:
        return ""
    verification = ""
    if record.applied == "no":
        label = ui_text(language, "Not applied", "Не применено")
    elif record.applied == "skip":
        label = ui_text(language, "Not comparable / skip", "Несопоставимый запуск / пропуск")
    elif record.applied == "yes":
        labels = {
            "improved": ui_text(language, "Improved", "Улучшение"),
            "no_change": ui_text(language, "No change", "Без изменений"),
            "worsened": ui_text(language, "Worsened", "Ухудшение"),
            "unsure": ui_text(language, "Unsure", "Не уверен"),
        }
        label = labels.get(record.outcome, "")
        verification = ui_text(language, "unverified feedback", "непроверенный результат")
        if record.verification_status == "comparable_rerun":
            verification = ui_text(
                language, "reported comparable rerun", "заявлен сопоставимый повторный запуск"
            )
    else:
        return ""
    if not label:
        return ""
    prefix = ui_text(language, "Recorded for this case", "Сохранено для этого кейса")
    link = ui_text(language, "View recorded outcomes", "Посмотреть сохранённые результаты")
    qualifier = f" — {html.escape(verification)}" if verification else ""
    return (
        '<p class="helper action-outcome-recorded">'
        f"{html.escape(prefix)}: <b>{html.escape(label)}</b>{qualifier}. "
        f'<a href="/outcomes">{html.escape(link)}</a></p>'
    )


def render_action_outcome_controls(
    card: RecentScanActionCandidateCardView,
    *,
    case_id: str,
    workload_fingerprint: str,
    detail_base_path: str,
    outcome_metric: RecommendationOutcomeMetric | None = None,
    language: str = "en",
) -> str:
    if not (case_id and workload_fingerprint and recommendation_id_allowed(card.recommendation_id)):
        return ""
    action_url = (
        f"{detail_base_path.rstrip('/')}/{html.escape(case_id, quote=True)}"
        f"/outcome/{html.escape(card.recommendation_id, quote=True)}"
    )
    label = html.escape(safe_recommendation_label(card.recommendation_id))
    metric_note = render_action_outcome_metric_note(outcome_metric)
    return (
        '<details class="action-outcome-control" data-action-outcome-card>'
        "<summary>Record rerun outcome</summary>"
        '<div class="action-outcome-body">'
        f'<p class="action-outcome-help">{html.escape(ui_text(language, "Record whether this recommendation was applied and what happened on a comparable rerun. This feeds workload confidence and next checks.", "Запишите, применялась ли рекомендация и что произошло на сопоставимом повторном запуске. Это повышает уверенность по workload и помогает выбрать следующие проверки."))}</p>'
        f'<span class="action-outcome-label">Recommendation: {label}</span>'
        f"{metric_note}"
        f'<form method="post" action="{action_url}" class="action-outcome-form">'
        '<button type="button" class="button" data-action-outcome-show-result>Applied and rerun</button>'
        '<button type="submit" class="button" name="applied" value="no">Not applied</button>'
        '<button type="submit" class="button" name="applied" value="skip">Not comparable / skip</button>'
        "</form>"
        '<div class="action-outcome-result" data-action-outcome-result-panel hidden>'
        '<span class="action-outcome-label">Comparable rerun result</span>'
        f'<form method="post" action="{action_url}" class="action-outcome-form">'
        '<input type="hidden" name="applied" value="yes">'
        '<input type="hidden" name="verification_status" value="comparable_rerun">'
        '<button type="submit" class="button primary" name="outcome" value="improved">Improved</button>'
        '<button type="submit" class="button" name="outcome" value="no_change">No change</button>'
        '<button type="submit" class="button" name="outcome" value="worsened">Worsened</button>'
        '<button type="submit" class="button" name="outcome" value="unsure">Unsure</button>'
        "</form>"
        "</div>"
        "</div>"
        "</details>"
    )


def render_action_outcome_metric_note(metric: RecommendationOutcomeMetric | None) -> str:
    if metric is None or not metric.min_sample_met or metric.improvement_rate is None:
        return ""
    percent = round(metric.improvement_rate * 100)
    text = (
        f"Local feedback so far: improved in {metric.improved_count} of "
        f"{metric.comparable_rerun_count} comparable reruns ({percent}%)"
    )
    return f'<span class="action-outcome-label">{html.escape(text)}</span>'
