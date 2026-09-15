"""Local action-outcome tracking for Recent scan recommendations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_doctor.web.display_safety import sanitize_browser_error_text
from query_doctor.web.form_helpers import first_form_value
from query_doctor.web.models import WebError


LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOAD_LIMIT = 200
DEFAULT_METRIC_LOAD_LIMIT = 1_000_000
DEFAULT_METRIC_MIN_APPLIED = 5
OUTCOME_PATH_ENV = "QUERY_DOCTOR_ACTION_OUTCOMES_PATH"

RECOMMENDATION_LABELS = {
    "query_optimization_review.v1": "Query optimization review",
    "stats_refresh_review.v1": "Stats refresh review",
    "runtime_admission_check.v1": "Admission/runtime check",
}
ALLOWED_APPLIED = {"yes", "no", "skip"}
ALLOWED_OUTCOMES = {"improved", "no_change", "worsened", "unsure", "not_applicable"}
ALLOWED_VERIFICATION_STATUSES = {
    "comparable_rerun",
    "legacy_unverified",
    "not_applicable",
}
WORKLOAD_FINGERPRINT_RE = re.compile(r"^wf_[0-9a-f]{24}$")
CASE_ID_RE = re.compile(r"^case-[0-9]{3,}$")


@dataclass(frozen=True)
class ActionOutcomeRecord:
    schema_version: int
    recorded_at_iso: str
    workload_fingerprint: str
    case_fingerprint: str
    case_id_local: str
    recommendation_id: str
    applied: str
    outcome: str
    verification_status: str = "legacy_unverified"
    note_redacted: str = ""


@dataclass(frozen=True)
class RecommendationOutcomeMetric:
    recommendation_id: str
    total_records: int
    applied_count: int
    comparable_rerun_count: int
    unverified_applied_count: int
    not_applied_count: int
    skipped_count: int
    improved_count: int
    no_change_count: int
    worsened_count: int
    unsure_count: int
    improvement_rate: float | None
    min_sample_met: bool
    min_applied: int


@dataclass(frozen=True)
class WorkloadOutcomeMetric:
    workload_fingerprint: str
    total_records: int
    applied_count: int
    comparable_rerun_count: int
    unverified_applied_count: int
    not_applied_count: int
    skipped_count: int
    improved_count: int
    no_change_count: int
    worsened_count: int
    unsure_count: int
    last_recommendation_id: str
    last_applied: str
    last_outcome: str
    last_applied_recommendation_id: str
    last_applied_outcome: str
    family_signal: "WorkloadOutcomeFamilySignal"
    family_signals: tuple["WorkloadOutcomeFamilySignal", ...] = ()


@dataclass(frozen=True)
class WorkloadOutcomeFamilySignal:
    recommendation_id: str
    total_records: int
    applied_count: int
    comparable_rerun_count: int
    unverified_applied_count: int
    improved_count: int
    no_change_count: int
    worsened_count: int
    unsure_count: int
    min_sample_met: bool
    min_applied: int


def action_outcomes_path() -> Path:
    configured = os.environ.get(OUTCOME_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".query-doctor" / "action_outcomes.jsonl"


def safe_recommendation_label(recommendation_id: str) -> str:
    return RECOMMENDATION_LABELS.get(recommendation_id, "Unknown recommendation")


def recommendation_id_allowed(value: Any) -> bool:
    return str(value or "") in RECOMMENDATION_LABELS


def safe_workload_fingerprint(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if WORKLOAD_FINGERPRINT_RE.fullmatch(text) else ""


def safe_case_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if CASE_ID_RE.fullmatch(text) else ""


def case_fingerprint(workload_fingerprint: str, query_id: Any) -> str:
    query_hash = hashlib.sha256(str(query_id or "").encode("utf-8")).hexdigest()[:16]
    digest = hashlib.sha256(f"{workload_fingerprint}:{query_hash}".encode("utf-8")).hexdigest()
    return f"cf_{digest[:24]}"


def action_outcome_record_from_case(
    *,
    case_id: str,
    case: dict[str, Any],
    recommendation_id: str,
    form: dict[str, list[str]],
) -> ActionOutcomeRecord:
    if not recommendation_id_allowed(recommendation_id):
        raise WebError(
            "Unknown recommendation outcome target.",
            title="Outcome target was rejected",
            reason_code="web.action_outcome_target_unknown",
            stage="Saving recommendation outcome",
            next_step="Refresh the case page and retry from an available recommendation.",
        )
    safe_local_case_id = safe_case_id(case_id)
    if not safe_local_case_id:
        raise WebError(
            "Invalid case outcome target.",
            title="Outcome case target was rejected",
            reason_code="web.action_outcome_case_invalid",
            stage="Saving recommendation outcome",
            next_step="Refresh the Recent results and retry from a listed case.",
        )
    workload_fingerprint = safe_workload_fingerprint(
        case.get("group_fingerprint") or case.get("workload_fingerprint")
    )
    if not workload_fingerprint:
        raise WebError(
            "Outcome tracking is unavailable for this case.",
            title="Outcome tracking is unavailable",
            reason_code="web.action_outcome_unavailable",
            stage="Saving recommendation outcome",
            next_step="Use outcome tracking from a grouped Recent case that includes a workload fingerprint.",
        )

    applied = normalize_applied(first_form_value(form, "applied"))
    outcome = normalize_outcome(first_form_value(form, "outcome"), applied=applied)
    verification_status = normalize_verification_status(
        first_form_value(form, "verification_status"),
        applied=applied,
    )
    note = sanitize_browser_error_text(first_form_value(form, "note") or "", max_chars=256)
    return ActionOutcomeRecord(
        schema_version=SCHEMA_VERSION,
        recorded_at_iso=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        workload_fingerprint=workload_fingerprint,
        case_fingerprint=case_fingerprint(workload_fingerprint, case.get("query_id")),
        case_id_local=safe_local_case_id,
        recommendation_id=recommendation_id,
        applied=applied,
        outcome=outcome,
        verification_status=verification_status,
        note_redacted=note,
    )


def normalize_applied(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in ALLOWED_APPLIED:
        raise WebError(
            "Invalid action outcome value.",
            title="Outcome value was rejected",
            reason_code="web.action_outcome_value_invalid",
            stage="Saving recommendation outcome",
            next_step="Choose one of the available outcome options and retry.",
        )
    return text


def normalize_outcome(value: Any, *, applied: str) -> str:
    if applied != "yes":
        return "not_applicable"
    text = str(value or "unsure").strip().lower()
    if text not in ALLOWED_OUTCOMES or text == "not_applicable":
        raise WebError(
            "Invalid action result value.",
            title="Outcome result was rejected",
            reason_code="web.action_result_value_invalid",
            stage="Saving recommendation outcome",
            next_step="Choose one of the available result options and retry.",
        )
    return text


def normalize_verification_status(value: Any, *, applied: str) -> str:
    if applied != "yes":
        return "not_applicable"
    text = str(value or "").strip().lower()
    if text == "comparable_rerun":
        return text
    raise WebError(
        "Action outcome requires comparable rerun verification.",
        title="Outcome verification is required",
        reason_code="web.action_outcome_verification_required",
        stage="Saving recommendation outcome",
        next_step="Confirm that the outcome is based on a comparable rerun before saving.",
    )


def append_action_outcome(
    record: ActionOutcomeRecord,
    *,
    path: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    target = path or action_outcomes_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    rotate_action_outcomes_if_needed(target, max_bytes=max_bytes)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    return target


def rotate_action_outcomes_if_needed(path: Path, *, max_bytes: int) -> None:
    try:
        current_size = path.stat().st_size
    except OSError:
        return
    if current_size <= max_bytes:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    path.replace(path.with_name(f"{path.stem}-{stamp}{path.suffix}"))


def load_action_outcomes(
    *,
    path: Path | None = None,
    limit: int = DEFAULT_LOAD_LIMIT,
) -> list[ActionOutcomeRecord]:
    target = path or action_outcomes_path()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[ActionOutcomeRecord] = []
    for line in lines:
        record = parse_action_outcome_line(line)
        if record is not None:
            records.append(record)
    return records[-max(0, limit) :]


def load_action_outcome_metrics(
    *,
    path: Path | None = None,
    limit: int = DEFAULT_METRIC_LOAD_LIMIT,
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> tuple[RecommendationOutcomeMetric, ...]:
    return summarize_action_outcomes(
        load_action_outcomes(path=path, limit=limit),
        min_applied=min_applied,
    )


def latest_case_action_outcomes(
    workload_fingerprint: str,
    query_id: Any,
    *,
    path: Path | None = None,
) -> dict[str, ActionOutcomeRecord]:
    """Return saved case feedback without treating a history row index as identity."""
    workload = safe_workload_fingerprint(workload_fingerprint)
    if not workload or not str(query_id or "").strip():
        return {}
    fingerprint = case_fingerprint(workload, query_id)
    return {
        record.recommendation_id: record
        for record in load_action_outcomes(path=path, limit=DEFAULT_METRIC_LOAD_LIMIT)
        if record.workload_fingerprint == workload and record.case_fingerprint == fingerprint
    }


def action_outcome_metrics_by_recommendation(
    *,
    path: Path | None = None,
    limit: int = DEFAULT_METRIC_LOAD_LIMIT,
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> dict[str, RecommendationOutcomeMetric]:
    return {
        metric.recommendation_id: metric
        for metric in load_action_outcome_metrics(
            path=path,
            limit=limit,
            min_applied=min_applied,
        )
    }


def workload_outcome_metrics_by_fingerprint(
    *,
    path: Path | None = None,
    limit: int = DEFAULT_METRIC_LOAD_LIMIT,
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> dict[str, WorkloadOutcomeMetric]:
    return summarize_workload_action_outcomes(
        load_action_outcomes(path=path, limit=limit),
        min_applied=min_applied,
    )


def summarize_action_outcomes(
    records: list[ActionOutcomeRecord],
    *,
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> tuple[RecommendationOutcomeMetric, ...]:
    grouped: dict[str, list[ActionOutcomeRecord]] = {}
    for record in records:
        if recommendation_id_allowed(record.recommendation_id):
            grouped.setdefault(record.recommendation_id, []).append(record)
    metrics = [
        recommendation_outcome_metric(
            recommendation_id=recommendation_id,
            records=group_records,
            min_applied=min_applied,
        )
        for recommendation_id, group_records in grouped.items()
    ]
    return tuple(
        sorted(
            metrics,
            key=lambda metric: (
                -metric.applied_count,
                -metric.improved_count,
                safe_recommendation_label(metric.recommendation_id),
            ),
        )
    )


def summarize_workload_action_outcomes(
    records: list[ActionOutcomeRecord],
    *,
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> dict[str, WorkloadOutcomeMetric]:
    grouped: dict[str, list[ActionOutcomeRecord]] = {}
    for record in records:
        workload_fingerprint = safe_workload_fingerprint(record.workload_fingerprint)
        if (
            workload_fingerprint
            and recommendation_id_allowed(record.recommendation_id)
            and record.applied in ALLOWED_APPLIED
            and record.outcome in ALLOWED_OUTCOMES
        ):
            grouped.setdefault(workload_fingerprint, []).append(record)
    return {
        workload_fingerprint: workload_outcome_metric(
            workload_fingerprint=workload_fingerprint,
            records=group_records,
            min_applied=min_applied,
        )
        for workload_fingerprint, group_records in grouped.items()
    }


def recommendation_outcome_metric(
    *,
    recommendation_id: str,
    records: list[ActionOutcomeRecord],
    min_applied: int,
) -> RecommendationOutcomeMetric:
    applied_counts = Counter(record.applied for record in records)
    applied_records = [record for record in records if record.applied == "yes"]
    comparable_records = comparable_rerun_records(applied_records)
    outcome_counts = Counter(record.outcome for record in comparable_records)
    applied_count = len(applied_records)
    comparable_rerun_count = len(comparable_records)
    unverified_applied_count = applied_count - comparable_rerun_count
    improved_count = outcome_counts.get("improved", 0)
    min_applied = max(1, int(min_applied))
    return RecommendationOutcomeMetric(
        recommendation_id=recommendation_id,
        total_records=len(records),
        applied_count=applied_count,
        comparable_rerun_count=comparable_rerun_count,
        unverified_applied_count=unverified_applied_count,
        not_applied_count=applied_counts.get("no", 0),
        skipped_count=applied_counts.get("skip", 0),
        improved_count=improved_count,
        no_change_count=outcome_counts.get("no_change", 0),
        worsened_count=outcome_counts.get("worsened", 0),
        unsure_count=outcome_counts.get("unsure", 0),
        improvement_rate=(
            round(improved_count / comparable_rerun_count, 4) if comparable_rerun_count else None
        ),
        min_sample_met=comparable_rerun_count >= min_applied,
        min_applied=min_applied,
    )


def workload_outcome_metric(
    *,
    workload_fingerprint: str,
    records: list[ActionOutcomeRecord],
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> WorkloadOutcomeMetric:
    applied_counts = Counter(record.applied for record in records)
    applied_records = [record for record in records if record.applied == "yes"]
    comparable_records = comparable_rerun_records(applied_records)
    outcome_counts = Counter(record.outcome for record in comparable_records)
    applied_count = len(applied_records)
    comparable_rerun_count = len(comparable_records)
    last_record = records[-1]
    last_applied_record = next(
        (record for record in reversed(records) if record.applied == "yes"),
        None,
    )
    family_recommendation_id = (
        last_applied_record.recommendation_id
        if last_applied_record is not None
        else last_record.recommendation_id
    )
    family_records = [
        record for record in records if record.recommendation_id == family_recommendation_id
    ]
    family_signals = tuple(
        workload_outcome_family_signal(
            recommendation_id=recommendation_id,
            records=recommendation_records,
            min_applied=min_applied,
        )
        for recommendation_id, recommendation_records in sorted(
            group_records_by_recommendation(records).items()
        )
    )
    return WorkloadOutcomeMetric(
        workload_fingerprint=workload_fingerprint,
        total_records=len(records),
        applied_count=applied_count,
        comparable_rerun_count=comparable_rerun_count,
        unverified_applied_count=applied_count - comparable_rerun_count,
        not_applied_count=applied_counts.get("no", 0),
        skipped_count=applied_counts.get("skip", 0),
        improved_count=outcome_counts.get("improved", 0),
        no_change_count=outcome_counts.get("no_change", 0),
        worsened_count=outcome_counts.get("worsened", 0),
        unsure_count=outcome_counts.get("unsure", 0),
        last_recommendation_id=last_record.recommendation_id,
        last_applied=last_record.applied,
        last_outcome=last_record.outcome,
        last_applied_recommendation_id=(
            last_applied_record.recommendation_id if last_applied_record is not None else ""
        ),
        last_applied_outcome=last_applied_record.outcome if last_applied_record is not None else "",
        family_signal=workload_outcome_family_signal(
            recommendation_id=family_recommendation_id,
            records=family_records,
            min_applied=min_applied,
        ),
        family_signals=family_signals,
    )


def group_records_by_recommendation(
    records: list[ActionOutcomeRecord],
) -> dict[str, list[ActionOutcomeRecord]]:
    grouped: dict[str, list[ActionOutcomeRecord]] = {}
    for record in records:
        if recommendation_id_allowed(record.recommendation_id):
            grouped.setdefault(record.recommendation_id, []).append(record)
    return grouped


def workload_outcome_family_signal(
    *,
    recommendation_id: str,
    records: list[ActionOutcomeRecord],
    min_applied: int = DEFAULT_METRIC_MIN_APPLIED,
) -> WorkloadOutcomeFamilySignal:
    applied_records = [record for record in records if record.applied == "yes"]
    comparable_records = comparable_rerun_records(applied_records)
    outcome_counts = Counter(record.outcome for record in comparable_records)
    applied_count = len(applied_records)
    comparable_rerun_count = len(comparable_records)
    min_applied = max(1, int(min_applied))
    return WorkloadOutcomeFamilySignal(
        recommendation_id=recommendation_id,
        total_records=len(records),
        applied_count=applied_count,
        comparable_rerun_count=comparable_rerun_count,
        unverified_applied_count=applied_count - comparable_rerun_count,
        improved_count=outcome_counts.get("improved", 0),
        no_change_count=outcome_counts.get("no_change", 0),
        worsened_count=outcome_counts.get("worsened", 0),
        unsure_count=outcome_counts.get("unsure", 0),
        min_sample_met=comparable_rerun_count >= min_applied,
        min_applied=min_applied,
    )


def comparable_rerun_records(records: list[ActionOutcomeRecord]) -> list[ActionOutcomeRecord]:
    return [record for record in records if record.verification_status == "comparable_rerun"]


def workload_outcome_summary_text(
    metric: WorkloadOutcomeMetric | None,
    *,
    recommendation_id: str = "",
) -> str:
    if metric is None or metric.total_records <= 0:
        return "none"
    signal = workload_outcome_signal_for_recommendation(metric, recommendation_id)
    outcome_parts = []
    for label, count in (
        ("improved", metric.improved_count),
        ("no change", metric.no_change_count),
        ("worsened", metric.worsened_count),
        ("unsure", metric.unsure_count),
    ):
        if count > 0:
            outcome_parts.append(f"{label} {count}")
    outcome_summary = ", ".join(outcome_parts) if outcome_parts else "no verified rerun outcomes"
    return (
        f"{metric.total_records} recorded; {metric.applied_count} applied; "
        f"{metric.comparable_rerun_count} comparable reruns; "
        f"{outcome_summary}; last applied action {workload_last_applied_action_label(metric)}; "
        f"family signal {workload_outcome_family_signal_text(signal)}"
    )


def workload_outcome_signal_for_recommendation(
    metric: WorkloadOutcomeMetric,
    recommendation_id: str,
) -> WorkloadOutcomeFamilySignal:
    if recommendation_id_allowed(recommendation_id):
        for signal in metric.family_signals:
            if signal.recommendation_id == recommendation_id:
                return signal
        return workload_outcome_family_signal(
            recommendation_id=recommendation_id,
            records=[],
            min_applied=metric.family_signal.min_applied,
        )
    return metric.family_signal


def workload_last_applied_action_label(metric: WorkloadOutcomeMetric) -> str:
    if not metric.last_applied_recommendation_id:
        return "none yet"
    label = safe_recommendation_label(metric.last_applied_recommendation_id)
    outcome = workload_outcome_label("yes", metric.last_applied_outcome)
    return f"{label}: {outcome}"


def workload_last_outcome_label(metric: WorkloadOutcomeMetric) -> str:
    return workload_outcome_label(metric.last_applied, metric.last_outcome)


def workload_outcome_label(applied: str, outcome: str) -> str:
    if applied == "no":
        return "not applied"
    if applied == "skip":
        return "skipped"
    return {
        "improved": "improved",
        "no_change": "no change",
        "worsened": "worsened",
        "unsure": "unsure",
        "not_applicable": "not applicable",
    }.get(outcome, "unknown")


def workload_outcome_family_signal_text(signal: WorkloadOutcomeFamilySignal) -> str:
    label = safe_recommendation_label(signal.recommendation_id)
    next_check = workload_outcome_family_next_check(signal.recommendation_id)
    sample_text = workload_outcome_family_sample_text(signal)
    if signal.comparable_rerun_count <= 0:
        return f"{label}: no verified rerun records yet; {sample_text}; next check {next_check}"
    parts = [f"improved {signal.improved_count}/{signal.comparable_rerun_count} comparable reruns"]
    for label_text, count in (
        ("no change", signal.no_change_count),
        ("worsened", signal.worsened_count),
        ("unsure", signal.unsure_count),
    ):
        if count > 0:
            parts.append(f"{label_text} {count}")
    return f"{label}: {', '.join(parts)}; {sample_text}; next check {next_check}"


def workload_outcome_family_sample_text(signal: WorkloadOutcomeFamilySignal) -> str:
    if signal.min_sample_met:
        return (
            "feedback sample threshold met "
            f"({signal.comparable_rerun_count}/{signal.min_applied} comparable reruns)"
        )
    return (
        "feedback sample below threshold "
        f"({signal.comparable_rerun_count}/{signal.min_applied} comparable reruns)"
    )


def workload_outcome_family_next_check(recommendation_id: str) -> str:
    return {
        "query_optimization_review.v1": "query-shape signal count and workload p95",
        "stats_refresh_review.v1": "stats signal count and workload p95",
        "runtime_admission_check.v1": "admission/runtime signal count and workload p95",
    }.get(recommendation_id, "signal count and workload p95")


def parse_action_outcome_line(line: str) -> ActionOutcomeRecord | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool):
        return None
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    recommendation_id = str(payload.get("recommendation_id") or "")
    applied = str(payload.get("applied") or "")
    outcome = str(payload.get("outcome") or "")
    workload_fingerprint = safe_workload_fingerprint(payload.get("workload_fingerprint"))
    case_id_local = safe_case_id(payload.get("case_id_local"))
    case_fingerprint_value = safe_case_fingerprint(payload.get("case_fingerprint"))
    verification_status = parse_verification_status(
        payload.get("verification_status"),
        applied=applied,
        schema_version=schema_version,
    )
    if not (
        recommendation_id_allowed(recommendation_id)
        and applied in ALLOWED_APPLIED
        and outcome in ALLOWED_OUTCOMES
        and verification_status
        and workload_fingerprint
        and case_id_local
        and case_fingerprint_value
    ):
        return None
    return ActionOutcomeRecord(
        schema_version=int(schema_version),
        recorded_at_iso=sanitize_browser_error_text(payload.get("recorded_at_iso") or ""),
        workload_fingerprint=workload_fingerprint,
        case_fingerprint=case_fingerprint_value,
        case_id_local=case_id_local,
        recommendation_id=recommendation_id,
        applied=applied,
        outcome=outcome,
        verification_status=verification_status,
        note_redacted=sanitize_browser_error_text(
            payload.get("note_redacted") or "", max_chars=256
        ),
    )


def safe_case_fingerprint(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"cf_[0-9a-f]{24}", text) else ""


def parse_verification_status(
    value: Any,
    *,
    applied: str,
    schema_version: object,
) -> str:
    if applied != "yes":
        return "not_applicable"
    if schema_version == LEGACY_SCHEMA_VERSION:
        return "legacy_unverified"
    text = str(value or "").strip().lower()
    return text if text in {"comparable_rerun", "legacy_unverified"} else ""


def action_outcome_count(*, path: Path | None = None) -> int:
    return len(load_action_outcomes(path=path, limit=1_000_000))
