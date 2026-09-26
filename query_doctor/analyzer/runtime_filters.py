"""Runtime filter context facts parsed from Impala profile text."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from query_doctor.analyzer.scalars import extract_first_duration_ms, fmt_duration, parse_size_bytes


RUNTIME_FILTER_LINE_RE = re.compile(
    r"^\s*(?:\|\s*)*(?:-\s*)?runtime\s+filters?\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
RUNTIME_FILTER_ID_RE = re.compile(r"\bRF\d+\b", re.IGNORECASE)
RUNTIME_FILTER_TYPE_RE = re.compile(r"\bRF\d+\s*\[\s*(?P<kind>[A-Za-z0-9_ -]+?)\s*\]")
MISSING_FILTER_ARRIVAL_RE = re.compile(r"\bnot\s+all\s+filters\s+arrived\b", re.IGNORECASE)
ALL_FILTERS_ARRIVED_RE = re.compile(r"\ball\s+filters\s+arrived\b", re.IGNORECASE)
WAITED_FOR_RE = re.compile(r"\bwaited\s+for\s+(?P<duration>[^\n\r,;]+)", re.IGNORECASE)
BLOOM_FILTER_BYTES_RE = re.compile(
    r"^\s*-\s*BloomFilterBytes\s*:\s*(?P<value>[^\n\r]+)",
    re.IGNORECASE | re.MULTILINE,
)
PLAN_OPERATOR_LINE_RE = re.compile(
    r"^\s*(?:\|\s*)*(?:\|?--\s*)?(?P<id>\d{1,3})\s*:\s*(?P<name>[A-Z][A-Z0-9_ ]+)",
    re.IGNORECASE,
)
RAW_SCAN_NODE_HEADER_RE = re.compile(
    r"^\s*(?P<name>(?:HDFS|KUDU|HBASE|SCAN)_SCAN_NODE|SCAN_NODE)\s+\(id=\d{1,3}\)",
    re.IGNORECASE,
)
FILTER_TABLE_MARKER_RE = re.compile(
    r"^\s*(?P<kind>Filter routing table|Final filter table)\s*:\s*$",
    re.IGNORECASE,
)
FILTER_TABLE_ROW_RE = re.compile(
    r"^\s*(?P<id>\d+)\s+"
    r"(?P<src>\d+|N/A)\s+"
    r"(?P<tgt>[\d,]+|N/A)\s+"
    r"(?P<target_type>[A-Z_]+)\s+"
    r"(?P<partition_filter>true|false)\s+"
    r"(?P<pending>\d+|N/A)\s*"
    r"(?:\([^)]+\))?\s+"
    r"(?P<first_arrived>\S+)\s+"
    r"(?P<completed>\S+)\s+"
    r"(?P<enabled>true|false)(?:\s.*)?$",
    re.IGNORECASE,
)
FILTER_TABLE_HEADER_RE = re.compile(r"^\s*ID\s{2,}Src\. Node\b")
FILTER_TABLE_CELL_SEPARATOR_RE = re.compile(r"\s{2,}")
# Header name -> row field. Columns after Enabled (Bloom Size, Est fpp, Min value,
# Max value, In-list size) may hold values with spaces or be empty, so rows are only
# split up to Enabled.
FILTER_TABLE_FIELD_BY_COLUMN = {
    "Target type": "target_type",
    "Partition filter": "partition_filter",
    "Pending (Expected)": "pending",
    "First arrived": "first_arrived",
    "Completed": "completed",
    "Enabled": "enabled",
}


@dataclass(frozen=True)
class RuntimeFilterTableRow:
    table_kind: str
    target_type: str
    partition_filter: bool
    pending_count: int | None
    first_arrived_observed: bool
    completed_observed: bool
    enabled: bool


@dataclass(frozen=True)
class RuntimeFilterLine:
    value: str
    target_operator_family: str
    target_scan_family: str


@dataclass(frozen=True)
class RuntimeFilterFacts:
    status: str
    evidence_tier: str
    finding_supported: bool
    primary_supported: bool
    profile_dialect: str
    runtime_filter_lines: int
    plan_filter_lines: int
    runtime_filter_id_count: int
    plan_producer_lines: int
    plan_consumer_lines: int
    plan_filter_id_count: int
    producer_filter_id_count: int
    consumer_filter_id_count: int
    paired_filter_id_count: int
    producer_only_filter_id_count: int
    consumer_only_filter_id_count: int
    producer_consumer_mapping_status: str
    target_scan_consumer_lines: int
    non_scan_consumer_lines: int
    unknown_target_consumer_lines: int
    target_scan_filter_id_count: int
    paired_target_scan_filter_id_count: int
    target_scan_mapping_status: str
    target_scan_family_counts: dict[str, int]
    routing_table_status: str
    routing_filter_count: int
    final_filter_count: int
    enabled_filter_count: int
    partition_filter_count: int
    pending_nonzero_count: int
    arrival_observed_count: int
    completed_observed_count: int
    target_type_counts: dict[str, int]
    filter_kind_counts: dict[str, int]
    arrival_status: str
    arrival_status_lines: int
    missing_arrival_lines: int
    all_arrived_lines: int
    max_arrival_wait_ms: float | None
    max_arrival_wait_human: str
    bloom_filter_counter_lines: int
    bloom_filter_counter_nonzero_lines: int
    exec_node_runtime_filter_effectiveness: str
    guardrail: str
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_runtime_filter_facts(
    text: str,
    profile_format: dict[str, Any] | None = None,
    exec_node_completeness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build raw-free runtime-filter facts.

    The first slice is deliberately context-only. Runtime filter profile text can
    show useful producer/consumer and arrival context, but root-cause wording
    requires stronger target-scan, timing, spill, and node-completion support.
    """

    return runtime_filter_facts(text, profile_format, exec_node_completeness).to_dict()


def runtime_filter_facts(
    text: str,
    profile_format: dict[str, Any] | None = None,
    exec_node_completeness: dict[str, Any] | None = None,
) -> RuntimeFilterFacts:
    profile_format = profile_format if isinstance(profile_format, dict) else {}
    exec_node_completeness = (
        exec_node_completeness if isinstance(exec_node_completeness, dict) else {}
    )
    dialect = str(
        profile_format.get("profile_dialect") or profile_format.get("dialect") or "unknown"
    )
    runtime_filter_effectiveness = str(
        exec_node_completeness.get("runtime_filter_effectiveness") or "unknown"
    )

    guardrail = (
        "Runtime filter evidence is context-only until the analyzer maps filter "
        "producers, consumers, target scans, timing, spill context, and node "
        "completion for the selected query."
    )
    unsupported_dialect = dialect not in {"classic_text_profile", ""}
    if unsupported_dialect:
        return RuntimeFilterFacts(
            status="unknown",
            evidence_tier="unsupported",
            finding_supported=False,
            primary_supported=False,
            profile_dialect=dialect,
            runtime_filter_lines=0,
            plan_filter_lines=0,
            runtime_filter_id_count=0,
            plan_producer_lines=0,
            plan_consumer_lines=0,
            plan_filter_id_count=0,
            producer_filter_id_count=0,
            consumer_filter_id_count=0,
            paired_filter_id_count=0,
            producer_only_filter_id_count=0,
            consumer_only_filter_id_count=0,
            producer_consumer_mapping_status="unknown",
            target_scan_consumer_lines=0,
            non_scan_consumer_lines=0,
            unknown_target_consumer_lines=0,
            target_scan_filter_id_count=0,
            paired_target_scan_filter_id_count=0,
            target_scan_mapping_status="unknown",
            target_scan_family_counts={},
            routing_table_status="unknown",
            routing_filter_count=0,
            final_filter_count=0,
            enabled_filter_count=0,
            partition_filter_count=0,
            pending_nonzero_count=0,
            arrival_observed_count=0,
            completed_observed_count=0,
            target_type_counts={},
            filter_kind_counts={},
            arrival_status="unknown",
            arrival_status_lines=0,
            missing_arrival_lines=0,
            all_arrived_lines=0,
            max_arrival_wait_ms=None,
            max_arrival_wait_human="n/a",
            bloom_filter_counter_lines=0,
            bloom_filter_counter_nonzero_lines=0,
            exec_node_runtime_filter_effectiveness=runtime_filter_effectiveness,
            guardrail=guardrail,
            limitations=(
                "Runtime filter profile text is not interpreted for this profile dialect.",
                "Do not claim missing, late, ineffective, or successful runtime filters from unsupported profile representations.",
            ),
        )

    line_facts = runtime_filter_lines_with_context(text)
    filter_lines = [line.value for line in line_facts]
    filter_ids = {
        match.group(0).upper()
        for line in filter_lines
        for match in RUNTIME_FILTER_ID_RE.finditer(line)
    }
    plan_filter_lines = sum(
        1
        for line in filter_lines
        if RUNTIME_FILTER_ID_RE.search(line) or "<-" in line or "->" in line
    )
    producer_lines = sum(1 for line in filter_lines if "<-" in line)
    consumer_lines = sum(1 for line in filter_lines if "->" in line)
    plan_mapping = runtime_filter_plan_mapping(line_facts)
    routing_summary = runtime_filter_routing_summary(text)
    filter_kind_counts = safe_filter_kind_counts(filter_lines)

    missing_arrival_lines = sum(
        1 for line in filter_lines if MISSING_FILTER_ARRIVAL_RE.search(line)
    )
    all_arrived_lines = sum(
        1
        for line in filter_lines
        if ALL_FILTERS_ARRIVED_RE.search(line) and not MISSING_FILTER_ARRIVAL_RE.search(line)
    )
    arrival_status_lines = missing_arrival_lines + all_arrived_lines
    arrival_waits = [
        wait_ms for line in filter_lines if (wait_ms := runtime_filter_wait_ms(line)) is not None
    ]
    max_wait_ms = max(arrival_waits) if arrival_waits else None
    bloom_counter_values = [
        parse_size_bytes(match.group("value")) for match in BLOOM_FILTER_BYTES_RE.finditer(text)
    ]
    bloom_counter_lines = len(bloom_counter_values)
    bloom_counter_nonzero_lines = sum(
        1 for value in bloom_counter_values if value is not None and value > 0
    )
    observed = bool(
        filter_lines
        or bloom_counter_lines
        or routing_summary["routing_filter_count"]
        or routing_summary["final_filter_count"]
    )

    limitations = runtime_filter_limitations(
        observed=observed,
        missing_arrival_lines=missing_arrival_lines,
        plan_mapping=plan_mapping,
        routing_table_status=str(routing_summary["routing_table_status"]),
        runtime_filter_effectiveness=runtime_filter_effectiveness,
    )

    return RuntimeFilterFacts(
        status="context_only" if observed else "not_observed",
        evidence_tier="context_only" if observed else "unsupported",
        finding_supported=False,
        primary_supported=False,
        profile_dialect=dialect or "classic_text_profile",
        runtime_filter_lines=len(filter_lines),
        plan_filter_lines=plan_filter_lines,
        runtime_filter_id_count=len(filter_ids),
        plan_producer_lines=producer_lines,
        plan_consumer_lines=consumer_lines,
        plan_filter_id_count=plan_mapping["plan_filter_id_count"],
        producer_filter_id_count=plan_mapping["producer_filter_id_count"],
        consumer_filter_id_count=plan_mapping["consumer_filter_id_count"],
        paired_filter_id_count=plan_mapping["paired_filter_id_count"],
        producer_only_filter_id_count=plan_mapping["producer_only_filter_id_count"],
        consumer_only_filter_id_count=plan_mapping["consumer_only_filter_id_count"],
        producer_consumer_mapping_status=plan_mapping["producer_consumer_mapping_status"],
        target_scan_consumer_lines=plan_mapping["target_scan_consumer_lines"],
        non_scan_consumer_lines=plan_mapping["non_scan_consumer_lines"],
        unknown_target_consumer_lines=plan_mapping["unknown_target_consumer_lines"],
        target_scan_filter_id_count=plan_mapping["target_scan_filter_id_count"],
        paired_target_scan_filter_id_count=plan_mapping["paired_target_scan_filter_id_count"],
        target_scan_mapping_status=plan_mapping["target_scan_mapping_status"],
        target_scan_family_counts=plan_mapping["target_scan_family_counts"],
        routing_table_status=routing_summary["routing_table_status"],
        routing_filter_count=routing_summary["routing_filter_count"],
        final_filter_count=routing_summary["final_filter_count"],
        enabled_filter_count=routing_summary["enabled_filter_count"],
        partition_filter_count=routing_summary["partition_filter_count"],
        pending_nonzero_count=routing_summary["pending_nonzero_count"],
        arrival_observed_count=routing_summary["arrival_observed_count"],
        completed_observed_count=routing_summary["completed_observed_count"],
        target_type_counts=routing_summary["target_type_counts"],
        filter_kind_counts=filter_kind_counts,
        arrival_status=arrival_status(missing_arrival_lines, all_arrived_lines, observed),
        arrival_status_lines=arrival_status_lines,
        missing_arrival_lines=missing_arrival_lines,
        all_arrived_lines=all_arrived_lines,
        max_arrival_wait_ms=max_wait_ms,
        max_arrival_wait_human=fmt_duration(max_wait_ms) if max_wait_ms is not None else "n/a",
        bloom_filter_counter_lines=bloom_counter_lines,
        bloom_filter_counter_nonzero_lines=bloom_counter_nonzero_lines,
        exec_node_runtime_filter_effectiveness=runtime_filter_effectiveness,
        guardrail=guardrail,
        limitations=tuple(limitations),
    )


def runtime_filter_wait_ms(line: str) -> float | None:
    match = WAITED_FOR_RE.search(line)
    if not match:
        return None
    return extract_first_duration_ms(match.group("duration"))


def runtime_filter_lines_with_context(text: str) -> list[RuntimeFilterLine]:
    lines = text.splitlines()
    records: list[RuntimeFilterLine] = []
    for index, line in enumerate(lines):
        match = RUNTIME_FILTER_LINE_RE.match(line)
        if not match:
            continue
        operator_family, scan_family = runtime_filter_line_target(lines, index)
        records.append(
            RuntimeFilterLine(
                value=match.group("value").strip(),
                target_operator_family=operator_family,
                target_scan_family=scan_family,
            )
        )
    return records


def runtime_filter_line_target(lines: list[str], index: int) -> tuple[str, str]:
    for offset in range(index - 1, max(-1, index - 10), -1):
        line = lines[offset]
        plan_match = PLAN_OPERATOR_LINE_RE.match(line)
        if plan_match:
            return operator_target_family(plan_match.group("name"))
        raw_match = RAW_SCAN_NODE_HEADER_RE.match(line)
        if raw_match:
            return operator_target_family(raw_match.group("name"))
    return "unknown", "unknown"


def operator_target_family(operator_name: object) -> tuple[str, str]:
    normalized = re.sub(r"[^A-Z0-9_ ]+", " ", str(operator_name or "").upper())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return "unknown", "unknown"
    if "SCAN" not in normalized:
        return "non_scan", "not_applicable"
    if "HDFS" in normalized:
        return "scan", "hdfs"
    if "KUDU" in normalized:
        return "scan", "kudu"
    if "HBASE" in normalized:
        return "scan", "hbase"
    return "scan", "generic"


def runtime_filter_plan_mapping(
    lines: list[RuntimeFilterLine],
) -> dict[str, int | str | dict[str, int]]:
    producer_ids: set[str] = set()
    consumer_ids: set[str] = set()
    plan_ids: set[str] = set()
    target_scan_ids: set[str] = set()
    target_scan_consumer_lines = 0
    non_scan_consumer_lines = 0
    unknown_target_consumer_lines = 0
    target_scan_family_counts: dict[str, int] = {}
    for record in lines:
        ids = {match.group(0).upper() for match in RUNTIME_FILTER_ID_RE.finditer(record.value)}
        if not ids:
            continue
        if "<-" in record.value or "->" in record.value:
            plan_ids.update(ids)
        if "<-" in record.value:
            producer_ids.update(ids)
        if "->" in record.value:
            consumer_ids.update(ids)
            if record.target_operator_family == "scan":
                target_scan_ids.update(ids)
                target_scan_consumer_lines += 1
                family = safe_kind(record.target_scan_family) or "generic"
                target_scan_family_counts[family] = target_scan_family_counts.get(family, 0) + 1
            elif record.target_operator_family == "non_scan":
                non_scan_consumer_lines += 1
            else:
                unknown_target_consumer_lines += 1

    paired_ids = producer_ids & consumer_ids
    producer_only_ids = producer_ids - consumer_ids
    consumer_only_ids = consumer_ids - producer_ids
    paired_target_scan_ids = target_scan_ids & producer_ids
    return {
        "plan_filter_id_count": len(plan_ids),
        "producer_filter_id_count": len(producer_ids),
        "consumer_filter_id_count": len(consumer_ids),
        "paired_filter_id_count": len(paired_ids),
        "producer_only_filter_id_count": len(producer_only_ids),
        "consumer_only_filter_id_count": len(consumer_only_ids),
        "producer_consumer_mapping_status": producer_consumer_mapping_status(
            plan_ids, paired_ids, producer_only_ids, consumer_only_ids
        ),
        "target_scan_consumer_lines": target_scan_consumer_lines,
        "non_scan_consumer_lines": non_scan_consumer_lines,
        "unknown_target_consumer_lines": unknown_target_consumer_lines,
        "target_scan_filter_id_count": len(target_scan_ids),
        "paired_target_scan_filter_id_count": len(paired_target_scan_ids),
        "target_scan_mapping_status": target_scan_mapping_status(
            consumer_ids=consumer_ids,
            target_scan_ids=target_scan_ids,
            paired_target_scan_ids=paired_target_scan_ids,
            non_scan_consumer_lines=non_scan_consumer_lines,
            unknown_target_consumer_lines=unknown_target_consumer_lines,
        ),
        "target_scan_family_counts": dict(sorted(target_scan_family_counts.items())),
    }


def runtime_filter_routing_summary(text: str) -> dict[str, int | str | dict[str, int]]:
    table_rows = runtime_filter_table_rows(text)
    routing_rows = [row for row in table_rows if row.table_kind == "routing"]
    final_rows = [row for row in table_rows if row.table_kind == "final"]
    summary_rows = final_rows or routing_rows
    target_type_counts: dict[str, int] = {}
    for row in summary_rows:
        key = safe_kind(row.target_type)
        if key:
            target_type_counts[key] = target_type_counts.get(key, 0) + 1

    if table_rows:
        status = "observed"
    else:
        status = "not_observed"

    return {
        "routing_table_status": status,
        "routing_filter_count": len(routing_rows),
        "final_filter_count": len(final_rows),
        "enabled_filter_count": sum(1 for row in summary_rows if row.enabled),
        "partition_filter_count": sum(1 for row in summary_rows if row.partition_filter),
        "pending_nonzero_count": sum(
            1 for row in summary_rows if row.pending_count is not None and row.pending_count > 0
        ),
        "arrival_observed_count": sum(1 for row in summary_rows if row.first_arrived_observed),
        "completed_observed_count": sum(1 for row in summary_rows if row.completed_observed),
        "target_type_counts": dict(sorted(target_type_counts.items())),
    }


def runtime_filter_table_rows(text: str) -> list[RuntimeFilterTableRow]:
    lines = text.splitlines()
    rows: list[RuntimeFilterTableRow] = []
    for index, line in enumerate(lines):
        marker_match = FILTER_TABLE_MARKER_RE.match(line)
        if not marker_match:
            continue
        table_kind = (
            "final" if marker_match.group("kind").lower().startswith("final") else "routing"
        )
        started_rows = False
        seen_header = False
        columns: list[str] | None = None
        for row_line in lines[index + 1 : index + 80]:
            if FILTER_TABLE_MARKER_RE.match(row_line):
                break
            stripped = row_line.strip()
            if not stripped:
                if started_rows:
                    break
                continue
            if FILTER_TABLE_HEADER_RE.match(row_line):
                columns = FILTER_TABLE_CELL_SEPARATOR_RE.split(stripped)
                seen_header = True
                continue
            if stripped.startswith("ID ") or set(stripped) <= {"-"}:
                seen_header = True
                continue
            fields = (
                filter_table_fields_by_header(stripped, columns)
                if columns is not None and "Enabled" in columns
                else filter_table_fields_by_pattern(row_line)
            )
            if fields is not None:
                rows.append(runtime_filter_table_row(table_kind, fields))
                started_rows = True
                continue
            if started_rows or seen_header:
                break
    return rows


def filter_table_fields_by_pattern(line: str) -> dict[str, str] | None:
    row_match = FILTER_TABLE_ROW_RE.match(line)
    return row_match.groupdict() if row_match else None


def filter_table_fields_by_header(line: str, columns: list[str]) -> dict[str, str] | None:
    enabled_index = columns.index("Enabled")
    cells = FILTER_TABLE_CELL_SEPARATOR_RE.split(line, maxsplit=enabled_index + 1)
    if len(cells) <= enabled_index:
        return None
    by_column = dict(zip(columns[: enabled_index + 1], cells))
    if not by_column.get("ID", "").isdigit():
        return None
    fields = {
        field: by_column[column]
        for column, field in FILTER_TABLE_FIELD_BY_COLUMN.items()
        if column in by_column
    }
    if fields.get("enabled", "").lower() not in {"true", "false"}:
        return None
    if fields.get("partition_filter", "").lower() not in {"true", "false"}:
        return None
    if not re.fullmatch(r"[A-Za-z_]+", fields.get("target_type", "")):
        return None
    fields["pending"] = fields.get("pending", "").split("(", 1)[0].strip()
    return fields


def runtime_filter_table_row(table_kind: str, fields: dict[str, str]) -> RuntimeFilterTableRow:
    return RuntimeFilterTableRow(
        table_kind=table_kind,
        target_type=safe_kind(fields.get("target_type")),
        partition_filter=bool_text(fields.get("partition_filter")),
        pending_count=parse_optional_int(fields.get("pending")),
        first_arrived_observed=observed_table_value(fields.get("first_arrived")),
        completed_observed=observed_table_value(fields.get("completed")),
        enabled=bool_text(fields.get("enabled")),
    )


def bool_text(value: object) -> bool:
    return str(value or "").strip().lower() == "true"


def parse_optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return int(text)


def observed_table_value(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in {"n/a", "na", "none", "-"})


def producer_consumer_mapping_status(
    plan_ids: set[str],
    paired_ids: set[str],
    producer_only_ids: set[str],
    consumer_only_ids: set[str],
) -> str:
    if not plan_ids:
        return "not_observed"
    if paired_ids and not producer_only_ids and not consumer_only_ids:
        return "mapped"
    if paired_ids:
        return "partial"
    if producer_only_ids or consumer_only_ids:
        return "unpaired"
    return "unknown"


def target_scan_mapping_status(
    *,
    consumer_ids: set[str],
    target_scan_ids: set[str],
    paired_target_scan_ids: set[str],
    non_scan_consumer_lines: int,
    unknown_target_consumer_lines: int,
) -> str:
    if not consumer_ids:
        return "not_observed"
    if (
        target_scan_ids
        and paired_target_scan_ids == target_scan_ids == consumer_ids
        and not non_scan_consumer_lines
        and not unknown_target_consumer_lines
    ):
        return "mapped"
    if target_scan_ids and paired_target_scan_ids:
        return "partial"
    if target_scan_ids:
        return "unpaired"
    return "missing_target_scan"


def arrival_status(
    missing_arrival_lines: int,
    all_arrived_lines: int,
    observed: bool,
) -> str:
    if missing_arrival_lines and all_arrived_lines:
        return "mixed"
    if missing_arrival_lines:
        return "missing_observed"
    if all_arrived_lines:
        return "all_arrived_observed"
    if observed:
        return "not_reported"
    return "not_observed"


def safe_filter_kind_counts(lines: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        for match in RUNTIME_FILTER_TYPE_RE.finditer(line):
            key = safe_kind(match.group("kind"))
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def safe_kind(value: object) -> str:
    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:40]


def runtime_filter_limitations(
    *,
    observed: bool,
    missing_arrival_lines: int,
    plan_mapping: dict[str, int | str],
    routing_table_status: str,
    runtime_filter_effectiveness: str,
) -> list[str]:
    limitations = [
        "Runtime filter context does not independently support a root-cause or primary-bottleneck finding.",
        "Missing or late runtime filters require deterministic producer, consumer, target scan, timing, and spill-context evidence before diagnostic promotion.",
    ]
    if missing_arrival_lines:
        limitations.append(
            "Runtime filter arrival gaps were observed, but this slice keeps them as context until target scans and producer timing are mapped."
        )
    mapping_status = str(plan_mapping.get("producer_consumer_mapping_status") or "unknown")
    if mapping_status in {"mapped", "partial", "unpaired"}:
        limitations.append(
            "Runtime filter producer/consumer mapping is aggregate context only; it does not expose filter identifiers or columns."
        )
    if mapping_status in {"partial", "unpaired"}:
        limitations.append(
            "Some runtime-filter producers or consumers were not paired in the parsed plan context."
        )
    target_status = str(plan_mapping.get("target_scan_mapping_status") or "unknown")
    if target_status in {"mapped", "partial", "unpaired", "missing_target_scan"}:
        limitations.append(
            "Runtime filter target-scan mapping is aggregate context only; it does not expose table names, filter identifiers, or filter columns."
        )
    if target_status in {"partial", "unpaired", "missing_target_scan"}:
        limitations.append(
            "Some runtime-filter consumers could not be mapped to a paired scan target in the parsed plan context."
        )
    if routing_table_status == "observed":
        limitations.append(
            "Runtime filter routing table context is aggregate only; it does not expose filter identifiers, node IDs, target columns, or target tables."
        )
    if runtime_filter_effectiveness != "supported":
        limitations.append(
            "Exec-node completeness limits runtime-filter-effectiveness conclusions for this profile."
        )
    if not observed:
        limitations.append(
            "No mapped runtime-filter plan or arrival context was observed in the parsed profile text."
        )
    return limitations
