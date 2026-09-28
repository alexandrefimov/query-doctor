"""Operator selection and serialization helpers for analyzer outputs."""

from __future__ import annotations

from typing import Any

from query_doctor.analyzer.models import (
    OperatorFact,
    OperatorObservation,
    observation_mem_ratio,
    observation_rows_ratio,
)
from query_doctor.analyzer.scalars import fmt_bytes, fmt_duration, fmt_ratio, fmt_rows
from query_doctor.analyzer.thresholds import MIN_MEMORY_ANOMALY_PEAK_BYTES


def op_label(op: OperatorFact) -> str:
    flags: list[str] = []
    if op.join_kind:
        flags.append(op.join_kind)
    if op.is_partitioned:
        flags.append("PARTITIONED")
    suffix = f" ({', '.join(flags)})" if flags else ""
    return f"{op.operator_id}:{op.operator_name}{suffix}"


def operator_with_observation(op: OperatorFact, observation: OperatorObservation) -> OperatorFact:
    return OperatorFact(
        operator_id=op.operator_id,
        operator_name=op.operator_name,
        time_ms=observation.time_ms if observation.time_ms is not None else op.time_ms,
        actual_rows=observation.actual_rows,
        estimated_rows=observation.estimated_rows,
        peak_mem_bytes=observation.peak_mem_bytes,
        estimated_peak_mem_bytes=observation.estimated_peak_mem_bytes,
        join_kind=op.join_kind,
        is_partitioned=op.is_partitioned,
        evidence_lines=list(observation.evidence_lines or op.evidence_lines),
        observations=[observation],
    )


def operator_with_best_rows_ratio(op: OperatorFact, threshold: float) -> OperatorFact | None:
    observation = op.best_rows_observation()
    if observation is None:
        return None
    ratio = observation_rows_ratio(observation)
    if ratio is None or ratio < threshold:
        return None
    return operator_with_observation(op, observation)


def operator_with_best_memory_ratio(
    op: OperatorFact,
    threshold: float,
    min_peak_bytes: float = MIN_MEMORY_ANOMALY_PEAK_BYTES,
) -> OperatorFact | None:
    # A scan's memory estimate is a fixed guess (about 80 MB for Parquet) and its
    # real peak follows I/O buffers and scanner threads. No plan choice depends
    # on it, so a scan miss is not an estimate problem worth a finding.
    if op.is_scan:
        return None
    observation = op.best_memory_observation()
    if observation is None:
        return None
    ratio = observation_mem_ratio(observation)
    if ratio is None or ratio < threshold:
        return None
    if (observation.peak_mem_bytes or 0) < min_peak_bytes:
        return None
    return operator_with_observation(op, observation)


def operator_with_zero_row_estimate_gap(op: OperatorFact) -> OperatorFact | None:
    observation = op.best_zero_row_estimate_gap_observation()
    if observation is None:
        return None
    return operator_with_observation(op, observation)


def operator_with_zero_memory_estimate_gap(op: OperatorFact) -> OperatorFact | None:
    observation = op.best_zero_memory_estimate_gap_observation()
    if observation is None:
        return None
    return operator_with_observation(op, observation)


def op_to_json(op: OperatorFact) -> dict[str, Any]:
    row_observation = op.best_rows_observation() or op.best_zero_row_estimate_gap_observation()
    memory_observation = (
        op.best_memory_observation() or op.best_zero_memory_estimate_gap_observation()
    )
    actual_rows = row_observation.actual_rows if row_observation else op.actual_rows
    estimated_rows = row_observation.estimated_rows if row_observation else op.estimated_rows
    rows_ratio = observation_rows_ratio(row_observation) if row_observation else None
    peak_mem_bytes = memory_observation.peak_mem_bytes if memory_observation else op.peak_mem_bytes
    estimated_peak_mem_bytes = (
        memory_observation.estimated_peak_mem_bytes
        if memory_observation
        else op.estimated_peak_mem_bytes
    )
    mem_ratio = observation_mem_ratio(memory_observation) if memory_observation else None
    return {
        "operator_id": op.operator_id,
        "operator_name": op.operator_name,
        "label": op_label(op),
        "time_ms": op.time_ms,
        "time": fmt_duration(op.time_ms),
        "actual_rows": actual_rows,
        "actual_rows_human": fmt_rows(actual_rows),
        "estimated_rows": estimated_rows,
        "estimated_rows_human": fmt_rows(estimated_rows),
        "rows_actual_to_estimated_ratio": rows_ratio,
        "rows_ratio_human": fmt_ratio(rows_ratio),
        "peak_mem_bytes": peak_mem_bytes,
        "peak_mem_human": fmt_bytes(peak_mem_bytes),
        "estimated_peak_mem_bytes": estimated_peak_mem_bytes,
        "estimated_peak_mem_human": fmt_bytes(estimated_peak_mem_bytes),
        "mem_actual_to_estimated_ratio": mem_ratio,
        "mem_ratio_human": fmt_ratio(mem_ratio),
        "join_kind": op.join_kind,
        "is_partitioned": op.is_partitioned,
        "evidence_lines": op.evidence_lines,
    }


def operator_key(op: dict[str, Any]) -> tuple[str, str]:
    return str(op.get("operator_id", "")), str(op.get("operator_name", ""))
