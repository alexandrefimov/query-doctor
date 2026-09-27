"""Deterministic analyzer service orchestration."""

from __future__ import annotations

import argparse
import re
from typing import Any

from query_doctor.analyzer.backend_tail import (
    backend_tail_finding_severity,
    build_backend_tail_analysis,
    parse_backend_host_facts,
)
from query_doctor.analyzer.client_fetch import (
    apply_client_fetch_profile_policy,
    build_client_fetch_facts,
)
from query_doctor.analyzer.context_collection import build_query_wall_clock
from query_doctor.analyzer.operators import (
    op_label,
    op_to_json,
    operator_with_best_memory_ratio,
    operator_with_best_rows_ratio,
    operator_with_zero_memory_estimate_gap,
    operator_with_zero_row_estimate_gap,
    parse_operators,
)
from query_doctor.analyzer.profile_signals import (
    CODEGEN_RE,
    CODEGEN_TIMING_RE,
    SCAN_STORAGE_RE,
    SPILL_RE,
    STATS_PATTERNS,
    find_codegen_bottleneck_lines,
    find_matching_lines,
    find_nonzero_spill_metric_lines,
)
from query_doctor.analyzer.profile_format import (
    build_profile_format_facts,
    profile_section_mapping_state,
)
from query_doctor.analyzer.profile_counter_registry import (
    DEFAULT_PROFILE_COUNTER_REGISTRY,
    ProfileCounterRegistry,
)
from query_doctor.analyzer.node_lifecycle import (
    build_exec_node_completeness_facts,
    operator_row_conclusions_supported,
)
from query_doctor.analyzer.profile_resources import build_profile_resource_facts
from query_doctor.analyzer.profile_text import normalize_profile_text
from query_doctor.analyzer.profile_timings import build_profile_timing_facts
from query_doctor.analyzer.resource_trace import build_resource_trace_facts
from query_doctor.analyzer.runtime_counters import (
    build_runtime_counter_context,
    extract_query_timeline_duration_ms,
    extract_total_counter,
)
from query_doctor.analyzer.runtime_filters import build_runtime_filter_facts
from query_doctor.analyzer.scalars import fmt_bytes, fmt_duration, fmt_ratio, fmt_rows
from query_doctor.analyzer.thresholds import (
    MEDIUM_DATA_MOVEMENT_BYTES,
    MIN_MEMORY_ANOMALY_PEAK_BYTES,
)


def make_finding(
    finding_id: str,
    severity: str,
    title: str,
    summary: str,
    operators: list[dict[str, Any]] | None = None,
    evidence_lines: list[str] | None = None,
    admin_actions: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "title": title,
        "summary": summary,
        "operators": operators or [],
        "evidence_lines": evidence_lines or [],
        "admin_actions": admin_actions or [],
        "missing_evidence": missing_evidence or [],
    }


def op_to_json_with_row_guardrail(
    op,
    exec_node_completeness: dict[str, Any],
) -> dict[str, Any]:
    payload = op_to_json(op)
    if operator_row_conclusions_supported(op, exec_node_completeness):
        payload["row_conclusion_state"] = "supported"
        return payload
    payload.update(
        {
            "actual_rows": None,
            "actual_rows_human": "n/a",
            "estimated_rows": None,
            "estimated_rows_human": "n/a",
            "rows_actual_to_estimated_ratio": None,
            "rows_ratio_human": "n/a",
            "row_conclusion_state": "limited_by_exec_node_completeness",
        }
    )
    return payload


def profile_text_analysis_supported(profile_format: dict[str, Any]) -> bool:
    dialect = str(profile_format.get("profile_dialect") or "").strip().lower()
    return dialect == "classic_text_profile"


def profile_counter_analysis_available(profile_format: dict[str, Any]) -> bool:
    return profile_section_mapping_state(profile_format, "profile_counters") in {
        "supported",
        "limited",
    }


def analyze(
    text: str,
    args: argparse.Namespace,
    cm_query_context: dict[str, Any] | None = None,
    counter_registry: ProfileCounterRegistry = DEFAULT_PROFILE_COUNTER_REGISTRY,
) -> dict[str, Any]:
    raw_text = text
    text = normalize_profile_text(text)
    profile_format = build_profile_format_facts(text, cm_query_context, raw_text=raw_text)
    profile_text = text if profile_text_analysis_supported(profile_format) else ""
    profile_counter_text = text if profile_counter_analysis_available(profile_format) else ""
    profile_timing_text = (
        text
        if profile_section_mapping_state(profile_format, "profile_timings") == "supported"
        else ""
    )

    operators = parse_operators(profile_text)
    exec_node_completeness = build_exec_node_completeness_facts(
        profile_text, operators, cm_query_context
    )
    row_conclusion_operators = [
        op for op in operators if operator_row_conclusions_supported(op, exec_node_completeness)
    ]
    profile_resources = build_profile_resource_facts(text, profile_format)
    profile_timings = build_profile_timing_facts(text, profile_format)
    resource_trace = build_resource_trace_facts(text, profile_format)
    backend_tail = build_backend_tail_analysis(parse_backend_host_facts(profile_text))
    runtime_counter_context = build_runtime_counter_context(profile_counter_text)
    totals = {
        name: extract_total_counter(profile_counter_text, name)
        for name in ["TotalBytesRead", "TotalBytesSent", "TotalTime"]
    }
    query_wall_clock = build_query_wall_clock(
        totals, cm_query_context, extract_query_timeline_duration_ms(profile_timing_text)
    )
    client_fetch = apply_client_fetch_profile_policy(
        build_client_fetch_facts(
            profile_counter_text, profile_timings, query_wall_clock, counter_registry
        ),
        profile_format,
    )
    runtime_filters = build_runtime_filter_facts(
        profile_text, profile_format, exec_node_completeness
    )

    top_by_time = sorted(
        [op for op in operators if op.time_ms is not None],
        key=lambda x: x.time_ms or 0,
        reverse=True,
    )[: args.top_n]
    top_by_memory = sorted(
        [op for op in operators if op.peak_mem_bytes is not None],
        key=lambda x: x.peak_mem_bytes or 0,
        reverse=True,
    )[: args.top_n]

    cardinality_anomalies = sorted(
        [
            anomaly
            for op in row_conclusion_operators
            if (anomaly := operator_with_best_rows_ratio(op, args.rows_ratio_threshold)) is not None
        ],
        key=lambda x: x.rows_ratio or 0,
        reverse=True,
    )
    memory_anomalies = sorted(
        [
            anomaly
            for op in operators
            if (anomaly := operator_with_best_memory_ratio(op, args.mem_ratio_threshold))
            is not None
        ],
        key=lambda x: x.mem_ratio or 0,
        reverse=True,
    )
    zero_row_estimate_gaps = sorted(
        [
            gap
            for op in row_conclusion_operators
            if (gap := operator_with_zero_row_estimate_gap(op)) is not None
        ],
        key=lambda x: x.actual_rows or 0,
        reverse=True,
    )
    zero_memory_estimate_gaps = sorted(
        [
            gap
            for op in operators
            if (gap := operator_with_zero_memory_estimate_gap(op)) is not None
        ],
        key=lambda x: x.peak_mem_bytes or 0,
        reverse=True,
    )

    join_bottlenecks = [
        op
        for op in operators
        if op.is_join
        and (
            (op.time_ms is not None and op.time_ms >= args.slow_operator_ms)
            or op.is_partitioned
            or (
                operator_row_conclusions_supported(op, exec_node_completeness)
                and op.actual_rows is not None
                and op.actual_rows >= args.large_rows_threshold
            )
            or (
                operator_row_conclusions_supported(op, exec_node_completeness)
                and op.rows_ratio is not None
                and op.rows_ratio >= args.rows_ratio_threshold
            )
        )
    ]
    sort_bottlenecks = [
        op
        for op in operators
        if op.is_sort
        and (
            (op.time_ms is not None and op.time_ms >= args.slow_operator_ms)
            or (op.mem_ratio is not None and op.mem_ratio >= args.mem_ratio_threshold)
            or (
                operator_row_conclusions_supported(op, exec_node_completeness)
                and op.actual_rows is not None
                and op.actual_rows >= args.large_rows_threshold
            )
        )
    ]
    analytic_bottlenecks = [
        op
        for op in operators
        if op.is_analytic
        and (
            (op.time_ms is not None and op.time_ms >= args.slow_operator_ms)
            or (
                operator_row_conclusions_supported(op, exec_node_completeness)
                and op.actual_rows is not None
                and op.actual_rows >= args.large_rows_threshold
            )
        )
    ]

    spill_lines = find_matching_lines(profile_counter_text, SPILL_RE)
    spill_nonzero_lines = find_nonzero_spill_metric_lines(profile_counter_text, counter_registry)
    stats_lines = find_matching_lines(
        profile_text, re.compile("|".join(p.pattern for p in STATS_PATTERNS), re.IGNORECASE)
    )
    storage_lines = find_matching_lines(profile_text, SCAN_STORAGE_RE)
    codegen_lines = find_matching_lines(profile_text, CODEGEN_RE)

    scan_top_ops = [op for op in top_by_time[:3] if op.is_scan]
    query_wall_clock_ms = query_wall_clock.get("duration_ms")
    codegen_bottleneck_lines = find_codegen_bottleneck_lines(profile_text, query_wall_clock_ms)
    total_read = totals.get("TotalBytesRead")
    total_read_bytes = (
        total_read.get("bytes")
        if total_read and isinstance(total_read.get("bytes"), (int, float))
        else None
    )
    total_read_large = (
        total_read_bytes is not None and total_read_bytes >= MEDIUM_DATA_MOVEMENT_BYTES
    )
    storage_bottleneck_evidence: list[str] = []
    for op in scan_top_ops:
        if op.time_ms is not None and op.time_ms >= args.slow_operator_ms:
            if not total_read_large:
                continue
            if isinstance(query_wall_clock_ms, (int, float)) and query_wall_clock_ms > 0:
                share = op.time_ms / query_wall_clock_ms
                if share < 0.10:
                    continue
                storage_bottleneck_evidence.append(
                    f"TotalBytesRead is large: {total_read['raw']} ({fmt_bytes(total_read_bytes)})"
                )
                storage_bottleneck_evidence.append(
                    f"{op_label(op)} is among top time operators: {fmt_duration(op.time_ms)}"
                )

    findings: list[dict[str, Any]] = []
    not_supported_causes: list[str] = []
    for limitation in profile_format.get("limitations") or []:
        if isinstance(limitation, dict) and limitation.get("summary"):
            not_supported_causes.append(str(limitation["summary"]))
    for limitation in exec_node_completeness.get("limitations") or []:
        if isinstance(limitation, dict) and limitation.get("summary"):
            not_supported_causes.append(str(limitation["summary"]))
    for limitation in client_fetch.get("limitations") or []:
        if limitation:
            not_supported_causes.append(str(limitation))
    for limitation in runtime_filters.get("limitations") or []:
        if limitation:
            not_supported_causes.append(str(limitation))

    network_exchange_evidence: list[str] = []
    total_sent = totals.get("TotalBytesSent")
    network_exchange_severity = "medium"
    if (
        total_sent
        and total_sent.get("bytes") is not None
        and total_sent["bytes"] >= MEDIUM_DATA_MOVEMENT_BYTES
    ):
        if total_sent["bytes"] >= args.large_bytes_threshold:
            network_exchange_severity = "high"
        network_exchange_evidence.append(
            f"TotalBytesSent is large: {total_sent['raw']} ({fmt_bytes(total_sent['bytes'])})"
        )
    elif (
        total_sent
        and total_sent.get("bytes") is not None
        and total_sent["bytes"] < MEDIUM_DATA_MOVEMENT_BYTES
    ):
        not_supported_causes.append(
            "TotalBytesSent was parsed below the large data-movement threshold: "
            f"{total_sent['raw']} ({fmt_bytes(total_sent['bytes'])}); do not treat it as large exchange traffic."
        )

    if network_exchange_evidence:
        for op in top_by_time[: args.top_n]:
            if "EXCHANGE" in op.operator_name.upper() and op.time_ms is not None:
                network_exchange_evidence.append(
                    f"{op_label(op)} has notable time: {fmt_duration(op.time_ms)}"
                )

    if (
        total_read
        and total_read.get("bytes") is not None
        and total_read["bytes"] < MEDIUM_DATA_MOVEMENT_BYTES
    ):
        not_supported_causes.append(
            "TotalBytesRead was parsed below the large I/O footprint threshold: "
            f"{total_read['raw']} ({fmt_bytes(total_read['bytes'])}); do not treat it as large scan I/O."
        )

    if (
        total_sent
        and total_sent.get("bytes") is not None
        and total_sent["bytes"] >= args.large_bytes_threshold
    ):
        network_exchange_evidence.append(
            "TotalBytesSent meets the high data-movement threshold "
            f"({fmt_bytes(args.large_bytes_threshold)})."
        )

    if cardinality_anomalies:
        worst = cardinality_anomalies[0]
        findings.append(
            make_finding(
                "cardinality_estimate_errors",
                "high" if (worst.rows_ratio or 0) >= 100 else "medium",
                "Cardinality estimate errors",
                (
                    f"Detected actual-vs-estimated row count mismatches. Worst parsed ratio: "
                    f"{op_label(worst)} = {fmt_ratio(worst.rows_ratio)} "
                    f"({fmt_rows(worst.actual_rows)} actual vs {fmt_rows(worst.estimated_rows)} estimated)."
                ),
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in cardinality_anomalies
                ],
            )
        )
    elif not zero_row_estimate_gaps:
        not_supported_causes.append(
            "No parsed actual-vs-estimated row count anomaly above threshold; do not claim cardinality estimate errors unless another evidence line supports it."
        )

    if zero_row_estimate_gaps:
        worst = zero_row_estimate_gaps[0]
        findings.append(
            make_finding(
                "zero_row_estimate_gaps",
                "medium",
                "Zero/unknown row estimate gaps",
                (
                    "Detected positive actual rows with an explicit zero/non-positive row estimate. "
                    f"Worst parsed gap: {op_label(worst)} = {fmt_rows(worst.actual_rows)} "
                    f"actual rows vs {fmt_rows(worst.estimated_rows)} estimated rows."
                ),
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in zero_row_estimate_gaps
                ],
            )
        )

    if memory_anomalies:
        worst = memory_anomalies[0]
        findings.append(
            make_finding(
                "memory_estimate_errors",
                "high" if (worst.mem_ratio or 0) >= 10 else "medium",
                "Memory estimate errors",
                (
                    f"Detected peak memory above estimated peak memory. Worst parsed ratio: "
                    f"{op_label(worst)} = {fmt_ratio(worst.mem_ratio)} "
                    f"({fmt_bytes(worst.peak_mem_bytes)} peak vs {fmt_bytes(worst.estimated_peak_mem_bytes)} estimated)."
                ),
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in memory_anomalies
                ],
            )
        )

    if zero_memory_estimate_gaps:
        worst = zero_memory_estimate_gaps[0]
        findings.append(
            make_finding(
                "zero_memory_estimate_gaps",
                "medium",
                "Zero/unknown memory estimate gaps",
                (
                    "Detected positive peak memory with an explicit zero/non-positive estimated peak memory. "
                    f"Worst parsed gap: {op_label(worst)} = {fmt_bytes(worst.peak_mem_bytes)} "
                    f"peak memory vs {fmt_bytes(worst.estimated_peak_mem_bytes)} estimated peak memory."
                ),
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in zero_memory_estimate_gaps
                ],
            )
        )

    if join_bottlenecks:
        findings.append(
            make_finding(
                "join_bottleneck",
                "high",
                "Join bottleneck",
                "Detected heavy join operators by time, row volume, partitioned join mode, or bad estimates.",
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in sorted(join_bottlenecks, key=lambda x: x.time_ms or 0, reverse=True)
                ],
            )
        )

    if sort_bottlenecks:
        findings.append(
            make_finding(
                "sort_bottleneck",
                "medium",
                "Sort bottleneck",
                "Detected expensive SORT/TOP-N operators by time, row volume, or memory estimate mismatch.",
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in sorted(sort_bottlenecks, key=lambda x: x.time_ms or 0, reverse=True)
                ],
            )
        )

    if analytic_bottlenecks:
        findings.append(
            make_finding(
                "analytic_bottleneck",
                "medium",
                "Analytic bottleneck",
                "Detected ANALYTIC operators with notable time or row volume.",
                operators=[
                    op_to_json_with_row_guardrail(op, exec_node_completeness)
                    for op in sorted(
                        analytic_bottlenecks, key=lambda x: x.time_ms or 0, reverse=True
                    )
                ],
            )
        )
    elif re.search(r"\bANALYTIC\b", profile_text, re.IGNORECASE):
        findings.append(
            make_finding(
                "analytic_present",
                "info",
                "Analytic present",
                "ANALYTIC operators are mentioned in the digest, but this parser did not extract enough per-operator metrics to rank them.",
            )
        )

    if (
        spill_nonzero_lines
        and profile_section_mapping_state(profile_format, "memory_pressure") == "supported"
    ):
        findings.append(
            make_finding(
                "spill_or_scratch_io",
                "medium",
                "Spill or scratch I/O",
                "Detected non-zero spill/scratch metric evidence in digest lines.",
                evidence_lines=spill_nonzero_lines,
            )
        )
    else:
        not_supported_causes.append("No non-zero spill/scratch I/O evidence was parsed.")

    if storage_bottleneck_evidence:
        findings.append(
            make_finding(
                "hdfs_or_storage_bottleneck",
                "medium",
                "Storage/HDFS candidate signal",
                "Detected scan/storage operator evidence among top time operators. Treat this as a candidate signal, not a root-cause claim.",
                evidence_lines=storage_bottleneck_evidence,
            )
        )
    else:
        if scan_top_ops and not isinstance(query_wall_clock_ms, (int, float)):
            not_supported_causes.append(
                "Storage/HDFS share was not evaluated because Query Wall Clock duration is unknown."
            )
        elif scan_top_ops and not total_read_large:
            not_supported_causes.append(
                "Storage/HDFS candidate signal requires both slow scan/storage operator context and parsed TotalBytesRead above the large I/O footprint threshold."
            )
        not_supported_causes.append(
            "No direct HDFS/storage candidate signal was parsed. Large TotalBytesRead is an I/O footprint, not proof that HDFS/block size/replication is the root cause."
        )

    if network_exchange_evidence:
        findings.append(
            make_finding(
                "large_intermediate_or_exchange_traffic",
                network_exchange_severity,
                "Large intermediate or exchange traffic",
                "Detected large bytes sent / exchange footprint. Treat as intermediate data movement evidence, not automatically as a network fault.",
                evidence_lines=network_exchange_evidence,
            )
        )
    else:
        not_supported_causes.append(
            "No large exchange/network traffic evidence was parsed from TotalBytesSent or EXCHANGE operators."
        )

    if codegen_bottleneck_lines:
        findings.append(
            make_finding(
                "codegen_bottleneck",
                "medium",
                "Codegen candidate signal",
                "Detected notable codegen/LLVM timing evidence. Treat this as a candidate signal, not a root-cause claim.",
                evidence_lines=codegen_bottleneck_lines[: args.max_evidence_lines],
            )
        )
    else:
        if CODEGEN_TIMING_RE.search(text) and not isinstance(query_wall_clock_ms, (int, float)):
            not_supported_causes.append(
                "Codegen/LLVM share was not evaluated because Query Wall Clock duration is unknown."
            )
        not_supported_causes.append("No codegen/LLVM candidate signal was parsed.")

    if client_fetch.get("finding_supported"):
        counter = client_fetch.get("dominant_wait_counter")
        counter_name = (
            counter.get("counter")
            if isinstance(counter, dict) and counter.get("counter")
            else "ClientFetchWait"
        )
        findings.append(
            make_finding(
                "client_fetch_tail",
                "medium",
                "Client fetch tail",
                (
                    "Detected query-specific client fetch wait as a large share of query duration. "
                    "Treat this as fetch-tail evidence, not proof of an external client, Hue, "
                    "BI tool, or network root cause."
                ),
                evidence_lines=[
                    f"{counter_name}: wait={client_fetch.get('client_fetch_wait_human') or 'n/a'}",
                    f"wait_share={client_fetch.get('wait_share_human') or 'n/a'}",
                    f"query_duration={client_fetch.get('query_duration_human') or 'n/a'}",
                ],
                missing_evidence=[
                    "External client, Hue, or BI-tool timing.",
                    "End-user network path metrics.",
                    "Comparable rerun showing the same fetch-tail pattern.",
                ],
            )
        )

    if backend_tail["execution_tail_candidates"]:
        findings.append(
            make_finding(
                "host_execution_tail_suspected",
                backend_tail_finding_severity(backend_tail["execution_tail_candidates"]),
                "Host-specific execution tail suspected",
                (
                    "Execution skew is suspected from parsed backend execution-time counters. "
                    "Host-specific HDFS/RPC/write path issue is suspected, not proven."
                ),
                evidence_lines=[
                    f"{candidate['host']}: {candidate['evidence']} ({candidate['ratio_human']})"
                    for candidate in backend_tail["execution_tail_candidates"][
                        : args.max_evidence_lines
                    ]
                ],
                admin_actions=[
                    "Compare per-host RowsProduced / BytesRead / BytesWritten rates.",
                    "Check HDFS write latency and DataNode pipeline details for the tail host.",
                    "Check NIC errors, retransmits, drops, MTU, LACP/bond, and switch output discards.",
                    "Check whether the tail persists after scanner thread cap changes if that evidence is available.",
                ],
                missing_evidence=[
                    "NIC counters.",
                    "Switch drops.",
                    "DataNode pipeline details.",
                    "Per-host OS/network metrics.",
                ],
            )
        )

    if backend_tail["write_path_candidates"]:
        findings.append(
            make_finding(
                "backend_write_path_anomaly",
                backend_tail_finding_severity(backend_tail["write_path_candidates"]),
                "Backend write-path anomaly",
                (
                    "Backend write-rate or HDFS-write counters show a host-specific write-path tail. "
                    "Treat this as write-path evidence, not execution skew and not scan-storage proof."
                ),
                evidence_lines=[
                    f"{candidate['host']}: {candidate['evidence']} ({candidate['ratio_human']})"
                    for candidate in backend_tail["write_path_candidates"][
                        : args.max_evidence_lines
                    ]
                ],
                admin_actions=[
                    "Check HDFS write latency and DataNode pipeline details for the write-path tail host.",
                    "Compare per-host BytesWritten, write rate and HDFS write time.",
                    "Check impalad/DataNode RPC latency, NIC errors and disk write latency around the query window.",
                ],
                missing_evidence=[
                    "DataNode pipeline details.",
                    "Per-host disk write latency.",
                    "Per-host network/RPC metrics.",
                ],
            )
        )

    not_supported_causes.append(
        "No evidence in the profile digest supports HDFS block-size or replication-factor changes as a query-level fix unless scan/storage counters prove it."
    )
    not_supported_causes.append(
        "No evidence in the profile digest supports blaming external network instability; large TotalBytesSent only shows data movement volume."
    )

    return {
        "thresholds": {
            "rows_ratio_threshold": args.rows_ratio_threshold,
            "mem_ratio_threshold": args.mem_ratio_threshold,
            "min_memory_anomaly_peak_bytes": MIN_MEMORY_ANOMALY_PEAK_BYTES,
            "slow_operator_ms": args.slow_operator_ms,
            "large_rows_threshold": args.large_rows_threshold,
            "large_bytes_threshold": args.large_bytes_threshold,
            "medium_data_movement_bytes": MEDIUM_DATA_MOVEMENT_BYTES,
            "report_top_n": args.top_n,
        },
        "totals": totals,
        "query_wall_clock": query_wall_clock,
        "profile_format": profile_format,
        "exec_node_completeness": exec_node_completeness,
        "profile_resources": profile_resources,
        "profile_timings": profile_timings,
        "resource_trace": resource_trace,
        "client_fetch": client_fetch,
        "runtime_filters": runtime_filters,
        "backend_tail": backend_tail,
        "runtime_counter_context": runtime_counter_context,
        "operators": [
            op_to_json_with_row_guardrail(op, exec_node_completeness) for op in operators
        ],
        "top_operators_by_time": [
            op_to_json_with_row_guardrail(op, exec_node_completeness) for op in top_by_time
        ],
        "top_operators_by_peak_memory": [
            op_to_json_with_row_guardrail(op, exec_node_completeness) for op in top_by_memory
        ],
        "cardinality_anomalies": [
            op_to_json_with_row_guardrail(op, exec_node_completeness)
            for op in cardinality_anomalies
        ],
        "memory_anomalies": [
            op_to_json_with_row_guardrail(op, exec_node_completeness) for op in memory_anomalies
        ],
        "zero_row_estimate_gaps": [
            op_to_json_with_row_guardrail(op, exec_node_completeness)
            for op in zero_row_estimate_gaps
        ],
        "zero_memory_estimate_gaps": [
            op_to_json_with_row_guardrail(op, exec_node_completeness)
            for op in zero_memory_estimate_gaps
        ],
        "stats_evidence_lines": stats_lines[: args.max_evidence_lines],
        "spill_evidence_lines": spill_lines[: args.max_evidence_lines],
        "spill_nonzero_evidence_lines": spill_nonzero_lines[: args.max_evidence_lines],
        "storage_evidence_lines": storage_lines[: args.max_evidence_lines],
        "codegen_evidence_lines": codegen_lines[: args.max_evidence_lines],
        "findings": findings,
        "not_supported_causes": not_supported_causes,
    }
