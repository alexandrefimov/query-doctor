"""Backend host tail-skew analysis for parsed Impala profile facts."""

from __future__ import annotations

import re
from typing import Any

from query_doctor.analyzer.models import BackendHostFact
from query_doctor.analyzer.scalars import fmt_bytes, fmt_duration, fmt_rate, fmt_ratio, fmt_rows
from query_doctor.safety.redaction import HostAliasRedactor, redact_host_identifiers
from query_doctor.safety.host_display import host_names_visible
from query_doctor.safety.redaction import redact_profile_text


BACKEND_MIN_HOSTS_FOR_SKEW = 3
BACKEND_DATA_SKEW_RATIO = 3.0
BACKEND_WORK_COMPARABLE_RATIO = 1.5
BACKEND_TAIL_RATIO = 3.0
BACKEND_EXECUTION_TAIL_RATIO = 1.6
BACKEND_EXECUTION_TAIL_MIN_MS = 10 * 60 * 1000
BACKEND_EXECUTION_TAIL_MIN_GAP_MS = 10 * 60 * 1000
BACKEND_EXECUTION_TAIL_HIGH_MS = 30 * 60 * 1000
BACKEND_EXECUTION_TAIL_HIGH_GAP_MS = 15 * 60 * 1000


def backend_host_alias(value: str, host_redactor: HostAliasRedactor) -> str:
    if host_names_visible() and re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}", value):
        return value
    alias = host_redactor.redact_host_value(value)
    match = re.match(r"^(host_\d{2})(?::\d+)?$", alias)
    if match:
        return match.group(1)
    return host_redactor.alias_for(alias)


def safe_backend_evidence_lines(lines: list[str], host_redactor: HostAliasRedactor) -> list[str]:
    if host_names_visible():
        return [redact_profile_text(line, redact_hosts=False) for line in lines]
    return [
        re.sub(r"\b(host_\d{2}):\d+\b", r"\1", redact_host_identifiers(line, host_redactor))
        for line in lines
    ]


def host_to_json(
    fact: BackendHostFact, host_redactor: HostAliasRedactor | None = None
) -> dict[str, Any]:
    host_redactor = host_redactor or HostAliasRedactor()
    return {
        "host": backend_host_alias(fact.host, host_redactor),
        "fragment_instance": fact.fragment_instance,
        "fragment_group": fact.fragment_group,
        "scan_bytes_assigned": fact.scan_bytes_assigned,
        "scan_bytes_assigned_human": fmt_bytes(fact.scan_bytes_assigned),
        "bytes_read": fact.bytes_read,
        "bytes_read_human": fmt_bytes(fact.bytes_read),
        "bytes_written": fact.bytes_written,
        "bytes_written_human": fmt_bytes(fact.bytes_written),
        "rows_produced": fact.rows_produced,
        "rows_produced_human": fmt_rows(fact.rows_produced),
        "read_rate_bps": fact.read_rate_bps,
        "read_rate_human": fmt_rate(fact.read_rate_bps),
        "write_rate_bps": fact.write_rate_bps,
        "write_rate_human": fmt_rate(fact.write_rate_bps),
        "hdfs_write_time_ms": fact.hdfs_write_time_ms,
        "hdfs_write_time_human": fmt_duration(fact.hdfs_write_time_ms),
        "hdfs_write_sec_per_gib": fact.hdfs_write_sec_per_gib,
        "hdfs_write_sec_per_gib_human": (
            f"{fact.hdfs_write_sec_per_gib:.2f}s/GiB"
            if fact.hdfs_write_sec_per_gib is not None
            else "n/a"
        ),
        "scanner_wait_time_ms": fact.scanner_wait_time_ms,
        "scanner_wait_time_human": fmt_duration(fact.scanner_wait_time_ms),
        "materialize_time_ms": fact.materialize_time_ms,
        "materialize_time_human": fmt_duration(fact.materialize_time_ms),
        "parse_time_ms": fact.parse_time_ms,
        "parse_time_human": fmt_duration(fact.parse_time_ms),
        "peak_scanner_concurrency": fact.peak_scanner_concurrency,
        "execution_time_ms": fact.execution_time_ms,
        "execution_time_human": fmt_duration(fact.execution_time_ms),
        "evidence_lines": safe_backend_evidence_lines(fact.evidence_lines, host_redactor),
    }


def metric_values(hosts: list[dict[str, Any]], key: str) -> list[tuple[dict[str, Any], float]]:
    values: list[tuple[dict[str, Any], float]] = []
    for host in hosts:
        value = host.get(key)
        if isinstance(value, (int, float)) and value > 0:
            values.append((host, float(value)))
    return values


def ratio_for_values(values: list[tuple[dict[str, Any], float]]) -> float | None:
    if len(values) < BACKEND_MIN_HOSTS_FOR_SKEW:
        return None
    raw_values = [value for _host, value in values if value > 0]
    if len(raw_values) < BACKEND_MIN_HOSTS_FOR_SKEW:
        return None
    min_value = min(raw_values)
    if min_value <= 0:
        return None
    return max(raw_values) / min_value


def backend_data_skew_status(hosts: list[dict[str, Any]]) -> tuple[str, str, bool]:
    metric_labels = [
        ("scan_bytes_assigned", "assigned scan bytes"),
        ("bytes_read", "bytes read"),
        ("rows_produced", "rows produced"),
    ]
    comparable_seen = False
    best_comparable = ""
    for key, label in metric_labels:
        values = metric_values(hosts, key)
        ratio = ratio_for_values(values)
        if ratio is None:
            continue
        if ratio >= BACKEND_DATA_SKEW_RATIO:
            return "yes", f"{label} max/min ratio is {fmt_ratio(ratio)}", False
        if ratio <= BACKEND_WORK_COMPARABLE_RATIO:
            comparable_seen = True
            best_comparable = f"{label} max/min ratio is {fmt_ratio(ratio)}"
    if comparable_seen:
        return "no", f"assigned/read work appears comparable ({best_comparable})", True
    return "unknown", "insufficient comparable per-host assigned/read/row metrics", False


def tail_candidate_from_metric(
    hosts: list[dict[str, Any]],
    *,
    key: str,
    label: str,
    human_key: str,
    higher_is_worse: bool,
    min_ratio: float = BACKEND_TAIL_RATIO,
    min_worst_value: float | None = None,
    min_gap: float | None = None,
) -> dict[str, Any] | None:
    values = metric_values(hosts, key)
    ratio = ratio_for_values(values)
    if ratio is None or ratio < min_ratio:
        return None
    worst_host, worst_value = (max if higher_is_worse else min)(values, key=lambda item: item[1])
    peer_value = (min if higher_is_worse else max)(value for _host, value in values)
    if min_worst_value is not None and worst_value < min_worst_value:
        return None
    if min_gap is not None and abs(worst_value - peer_value) < min_gap:
        return None
    evidence = (
        f"{label}: {worst_host.get(human_key, 'n/a')} vs peer "
        f"{'min' if higher_is_worse else 'max'} "
        f"{fmt_duration(peer_value) if key.endswith('_ms') else fmt_rate(peer_value) if key.endswith('_bps') else f'{peer_value:.2f}s/GiB'}"
    )
    return {
        "host": worst_host["host"],
        "fragment_instance": worst_host.get("fragment_instance"),
        "evidence": evidence,
        "metric_key": key,
        "metric": label,
        "ratio": ratio,
        "ratio_human": fmt_ratio(ratio),
        "worst_value": worst_value,
        "peer_value": peer_value,
        "gap": abs(worst_value - peer_value),
    }


def tail_value_human(metric_key: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if metric_key.endswith("_ms"):
        return fmt_duration(value)
    if metric_key.endswith("_bps"):
        return fmt_rate(value)
    if metric_key.endswith("_sec_per_gib"):
        return f"{value:.2f}s/GiB"
    return f"{value:.2f}"


def backend_tail_finding_severity(candidates: list[dict[str, Any]]) -> str:
    for candidate in candidates:
        if (candidate.get("ratio") or 0) >= 5:
            return "high"
        if (
            candidate.get("metric_key") == "execution_time_ms"
            and (candidate.get("worst_value") or 0) >= BACKEND_EXECUTION_TAIL_HIGH_MS
            and (candidate.get("gap") or 0) >= BACKEND_EXECUTION_TAIL_HIGH_GAP_MS
        ):
            return "high"
    return "medium"


def build_backend_tail_analysis(host_facts: list[BackendHostFact]) -> dict[str, Any]:
    host_redactor = HostAliasRedactor()
    hosts = [host_to_json(fact, host_redactor) for fact in host_facts]
    grouped_hosts: dict[str, list[dict[str, Any]]] = {}
    for host in hosts:
        group = str(host.get("fragment_group") or "unknown")
        grouped_hosts.setdefault(group, []).append(host)

    group_statuses: list[tuple[str, str, str, bool, list[dict[str, Any]]]] = []
    for group, group_hosts in grouped_hosts.items():
        group_data_skew, group_reason, comparable_work = backend_data_skew_status(group_hosts)
        group_statuses.append((group, group_data_skew, group_reason, comparable_work, group_hosts))

    yes_statuses = [status for status in group_statuses if status[1] == "yes"]
    comparable_statuses = [status for status in group_statuses if status[3]]
    if yes_statuses:
        group, data_skew, reason, _comparable, _group_hosts = yes_statuses[0]
    elif comparable_statuses:
        group, data_skew, reason, _comparable, _group_hosts = comparable_statuses[0]
    elif group_statuses:
        group, data_skew, reason, _comparable, _group_hosts = group_statuses[0]
    else:
        group, data_skew, reason = (
            "unknown",
            "unknown",
            "insufficient comparable per-host assigned/read/row metrics",
        )
    data_skew_reason = f"{group}: {reason}" if group != "unknown" else reason

    candidates: list[dict[str, Any]] = []
    execution_tail_candidates: list[dict[str, Any]] = []
    read_rate_tail_candidates: list[dict[str, Any]] = []
    write_path_candidates: list[dict[str, Any]] = []

    for group, _group_data_skew, _group_reason, comparable_work, group_hosts in group_statuses:
        if not comparable_work:
            continue
        metric_specs = [
            (
                "execution_time_ms",
                "execution time",
                "execution_time_human",
                True,
                "execution",
                False,
                BACKEND_EXECUTION_TAIL_RATIO,
                BACKEND_EXECUTION_TAIL_MIN_MS,
                BACKEND_EXECUTION_TAIL_MIN_GAP_MS,
            ),
            (
                "read_rate_bps",
                "read rate",
                "read_rate_human",
                False,
                "read_rate",
                False,
                BACKEND_TAIL_RATIO,
                None,
                None,
            ),
            (
                "write_rate_bps",
                "write rate",
                "write_rate_human",
                False,
                "write_path",
                True,
                BACKEND_TAIL_RATIO,
                None,
                None,
            ),
            (
                "hdfs_write_time_ms",
                "HDFS write time",
                "hdfs_write_time_human",
                True,
                "write_path",
                True,
                BACKEND_TAIL_RATIO,
                None,
                None,
            ),
            (
                "hdfs_write_sec_per_gib",
                "HDFS write sec/GiB",
                "hdfs_write_sec_per_gib_human",
                True,
                "write_path",
                True,
                BACKEND_TAIL_RATIO,
                None,
                None,
            ),
        ]
        for (
            key,
            label,
            human_key,
            higher_is_worse,
            family,
            is_write_path,
            min_ratio,
            min_worst_value,
            min_gap,
        ) in metric_specs:
            candidate = tail_candidate_from_metric(
                group_hosts,
                key=key,
                label=f"{group} {label}" if group != "unknown" else label,
                human_key=human_key,
                higher_is_worse=higher_is_worse,
                min_ratio=min_ratio,
                min_worst_value=min_worst_value,
                min_gap=min_gap,
            )
            if candidate is None:
                continue
            candidate["metric_family"] = family
            candidate["fragment_group"] = group
            candidate["worst_human"] = tail_value_human(key, candidate.get("worst_value"))
            candidate["peer_human"] = tail_value_human(key, candidate.get("peer_value"))
            candidate["gap_human"] = tail_value_human(key, candidate.get("gap"))
            candidates.append(candidate)
            if family == "execution":
                execution_tail_candidates.append(candidate)
            elif family == "read_rate":
                read_rate_tail_candidates.append(candidate)
            if is_write_path:
                write_path_candidates.append(candidate)

    execution_skew = (
        "yes" if execution_tail_candidates else ("unknown" if not comparable_statuses else "no")
    )
    write_path_anomaly = (
        "yes" if write_path_candidates else ("unknown" if not comparable_statuses else "no")
    )
    tail_candidate_count = len({candidate["host"] for candidate in candidates})
    execution_tail_candidate_count = len(
        {candidate["host"] for candidate in execution_tail_candidates}
    )
    read_rate_tail_candidate_count = len(
        {candidate["host"] for candidate in read_rate_tail_candidates}
    )
    write_path_tail_candidate_count = len(
        {candidate["host"] for candidate in write_path_candidates}
    )
    return {
        "rows_parsed": len(hosts),
        "hosts": hosts,
        "groups": [
            {
                "fragment_group": group,
                "host_count": len(group_hosts),
                "data_skew": group_data_skew,
                "data_skew_reason": group_reason,
                "comparable_work": comparable_work,
            }
            for group, group_data_skew, group_reason, comparable_work, group_hosts in group_statuses
        ],
        "tail_candidate_count": tail_candidate_count,
        "execution_tail_candidate_count": execution_tail_candidate_count,
        "read_rate_tail_candidate_count": read_rate_tail_candidate_count,
        "write_path_tail_candidate_count": write_path_tail_candidate_count,
        "data_skew": data_skew,
        "data_skew_reason": data_skew_reason,
        "execution_skew": execution_skew,
        "write_path_anomaly": write_path_anomaly,
        "candidates": candidates,
        "execution_tail_candidates": execution_tail_candidates,
        "read_rate_tail_candidates": read_rate_tail_candidates,
        "write_path_candidates": write_path_candidates,
    }
