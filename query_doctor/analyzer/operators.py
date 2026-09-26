"""Operator parsing and serialization helpers for Impala profile analysis."""

from __future__ import annotations

import re
from typing import Iterable

from query_doctor.analyzer.context_files import compact_line
from query_doctor.analyzer.models import OperatorFact
from query_doctor.analyzer.runtime_counters import line_indent
from query_doctor.analyzer.scalars import (
    NUMBER_PATTERN,
    SIZE_PATTERN,
    SIZE_RE,
    extract_first_duration_ms,
    parse_scaled_number,
    parse_size_bytes,
    table_duration_to_ms,
)


KNOWN_OPERATOR_NAMES = [
    "NESTED LOOP JOIN",
    "STREAMING AGGREGATE",
    "HASH AGGREGATE",
    "MERGING EXCHANGE",
    "DATASTREAM SINK",
    "PLAN ROOT SINK",
    "HDFS SCAN",
    "HBASE SCAN",
    "KUDU SCAN",
    "HASH JOIN",
    "CROSS JOIN",
    "AGGREGATE",
    "ANALYTIC",
    "EXCHANGE",
    "SORT",
    "TOP-N",
    "UNION",
    "SELECT",
    "SCAN",
]

OP_RE = re.compile(
    r"\b(?P<id>\d{1,3})\s*:\s*(?P<name>"
    + "|".join(re.escape(x) for x in KNOWN_OPERATOR_NAMES)
    + r")\b",
    flags=re.IGNORECASE,
)

ROW_NUMBER = rf"{NUMBER_PATTERN}\s*[KMBT]?"

TABLE_SEPARATOR_RE = re.compile(r"\s{2,}")
EXEC_SUMMARY_HEADER_RE = re.compile(r"^\s*Operator\s+#Hosts\s")
EXEC_SUMMARY_TREE_PREFIX_RE = re.compile(r"^[|\s-]+(?=\d{1,3}\s*:)")
LEGACY_EXEC_SUMMARY_COLUMNS = [
    "Operator",
    "#Hosts",
    "Avg Time",
    "Max Time",
    "#Rows",
    "Est. #Rows",
    "Peak Mem",
    "Est. Peak Mem",
    "Detail",
]
MODERN_EXEC_SUMMARY_COLUMNS = (
    LEGACY_EXEC_SUMMARY_COLUMNS[:2] + ["#Inst"] + LEGACY_EXEC_SUMMARY_COLUMNS[2:]
)
EXEC_SUMMARY_REQUIRED_COLUMNS = frozenset(
    {"Operator", "#Hosts", "Max Time", "#Rows", "Est. #Rows", "Peak Mem", "Est. Peak Mem"}
)
ROWS_PATTERNS = [
    re.compile(
        rf"(?P<actual>{ROW_NUMBER})\s+actual\s+rows?\s+(?:vs|/)\s+"
        rf"(?P<estimated>{ROW_NUMBER})\s+estimated\s+rows?",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"actual\s+rows?\s*[:=]\s*(?P<actual>{ROW_NUMBER}).{{0,100}}?"
        rf"estimated\s+rows?\s*[:=]\s*(?P<estimated>{ROW_NUMBER})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"estimated\s+rows?\s*[:=]\s*(?P<estimated>{ROW_NUMBER}).{{0,100}}?"
        rf"actual\s+rows?\s*[:=]\s*(?P<actual>{ROW_NUMBER})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"rows\s*[:=]\s*(?P<actual>{ROW_NUMBER}).{{0,100}}?"
        rf"est(?:imated)?\.?\s+rows\s*[:=]\s*(?P<estimated>{ROW_NUMBER})",
        flags=re.IGNORECASE,
    ),
]

MEM_PATTERNS = [
    re.compile(
        rf"(?:Peak\s+Mem(?:ory)?|PeakMem)\s*[:=]?\s*(?P<actual>{SIZE_PATTERN}).{{0,80}}?"
        rf"(?:Est\.?\s*Peak\s+Mem(?:ory)?|Estimated\s+Peak\s+Mem(?:ory)?|EstPeakMem)\s*[:=]?\s*(?P<estimated>{SIZE_PATTERN})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"(?:Est\.?\s*Peak\s+Mem(?:ory)?|Estimated\s+Peak\s+Mem(?:ory)?|EstPeakMem)\s*[:=]?\s*(?P<estimated>{SIZE_PATTERN}).{{0,80}}?"
        rf"(?:Peak\s+Mem(?:ory)?|PeakMem)\s*[:=]?\s*(?P<actual>{SIZE_PATTERN})",
        flags=re.IGNORECASE,
    ),
]

SINGLE_PEAK_MEM_RE = re.compile(
    rf"(?:Peak\s+Mem(?:ory)?|PeakMem)\s*[:=]?\s*(?P<actual>{SIZE_PATTERN})",
    flags=re.IGNORECASE,
)

JOIN_KIND_RE = re.compile(
    r"\b(?P<kind>LEFT\s+ANTI|LEFT\s+SEMI|RIGHT\s+ANTI|RIGHT\s+SEMI|LEFT\s+OUTER|RIGHT\s+OUTER|FULL\s+OUTER|INNER|LEFT|RIGHT|FULL|CROSS)\s+JOIN\b",
    flags=re.IGNORECASE,
)

ESTIMATED_ROWS_ONLY_RE = re.compile(
    rf"\bcardinality\s*[:=]\s*(?P<estimated>{ROW_NUMBER})\b",
    flags=re.IGNORECASE,
)

RAW_NODE_HEADER_RE = re.compile(
    r"^\s*(?P<node>[A-Z][A-Z0-9_]+_NODE)\s+\(id=(?P<id>\d{1,3})\)",
)

RAW_NODE_NAME_MAP = {
    "ANALYTIC_EVAL_NODE": "ANALYTIC",
    "AGGREGATE_NODE": "AGGREGATE",
    "AGGREGATION_NODE": "AGGREGATE",
    "EXCHANGE_NODE": "EXCHANGE",
    "HASH_JOIN_NODE": "HASH JOIN",
    "HBASE_SCAN_NODE": "HBASE SCAN",
    "HDFS_SCAN_NODE": "HDFS SCAN",
    "KUDU_SCAN_NODE": "KUDU SCAN",
    "NESTED_LOOP_JOIN_NODE": "NESTED LOOP JOIN",
    "SCAN_NODE": "SCAN",
    "SELECT_NODE": "SELECT",
    "SORT_NODE": "SORT",
    "TOPN_NODE": "TOP-N",
    "UNION_NODE": "UNION",
}

RAW_ROW_COUNTER_RE = re.compile(
    r"-\s*(?:RowsProduced|RowsReturned|RowsRead|RowsSent)\s*:\s*(?P<value>[^\n\r]+)",
    flags=re.IGNORECASE,
)
RAW_PEAK_MEMORY_RE = re.compile(
    rf"-\s*(?:PeakMemoryUsage|PerHostPeakMemUsage|PeakMemUsage)\s*:\s*(?P<value>{SIZE_PATTERN})",
    flags=re.IGNORECASE,
)
RAW_TIME_COUNTER_RE = re.compile(
    r"^\s*-\s*(?P<name>TotalTime|ExecTime)\s*:\s*(?P<value>[^\n\r]+)",
    flags=re.IGNORECASE,
)
RAW_NODE_DIRECT_COUNTER_RE = re.compile(
    r"^\s*-\s*(?:RowsProduced|RowsReturned|RowsRead|RowsSent|"
    r"PeakMemoryUsage|PerHostPeakMemUsage|PeakMemUsage|TotalTime|ExecTime)\s*:",
    flags=re.IGNORECASE,
)
RAW_COUNTER_NUMBER_RE = re.compile(
    rf"(?P<display>{NUMBER_PATTERN}\s*[KMBT]?)(?:\s*\((?P<exact>{NUMBER_PATTERN})\))?",
    flags=re.IGNORECASE,
)


def parse_estimated_rows_only(window: str) -> float | None:
    m = ESTIMATED_ROWS_ONLY_RE.search(window)
    if not m:
        return None
    return parse_scaled_number(m.group("estimated"))


def parse_rows(window: str) -> tuple[float | None, float | None]:
    for rx in ROWS_PATTERNS:
        m = rx.search(window)
        if m:
            return parse_scaled_number(m.group("actual")), parse_scaled_number(m.group("estimated"))
    return None, None


def parse_memory(window: str) -> tuple[float | None, float | None]:
    for rx in MEM_PATTERNS:
        m = rx.search(window)
        if m:
            return parse_size_bytes(m.group("actual")), parse_size_bytes(m.group("estimated"))
    m = SINGLE_PEAK_MEM_RE.search(window)
    if m:
        return parse_size_bytes(m.group("actual")), None
    return None, None


def exec_summary_columns(line: str) -> list[str] | None:
    """Return the ExecSummary column names when ``line`` is its header row."""
    if not EXEC_SUMMARY_HEADER_RE.match(line):
        return None
    columns = TABLE_SEPARATOR_RE.split(line.strip())
    if not EXEC_SUMMARY_REQUIRED_COLUMNS.issubset(columns):
        return None
    return columns


def _is_plain_int(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value))


def parse_operator_table_line(line: str, columns: list[str] | None = None) -> OperatorFact | None:
    """Parse fixed-width Impala operator summary rows.

    Columns come from the ExecSummary header when known. Without a header the
    layout is inferred: Impala 3.x has operator, #Hosts, Avg Time, Max Time,
    #Rows, Est. #Rows, Peak Mem, Est. Peak Mem, Detail; Impala 4.x inserts
    #Inst after #Hosts. Avg Time always carries a unit, #Inst never does.
    """
    stripped = EXEC_SUMMARY_TREE_PREFIX_RE.sub("", line.strip())
    if not re.match(r"^\d{1,3}\s*:", stripped):
        return None

    if columns is None:
        head = TABLE_SEPARATOR_RE.split(stripped, maxsplit=3)
        modern = len(head) > 2 and _is_plain_int(head[2])
        columns = MODERN_EXEC_SUMMARY_COLUMNS if modern else LEGACY_EXEC_SUMMARY_COLUMNS

    parts = TABLE_SEPARATOR_RE.split(stripped, maxsplit=len(columns) - 1)
    index = {name: i for i, name in enumerate(columns)}
    if len(parts) <= index["Est. Peak Mem"]:
        return None

    op_match = OP_RE.search(parts[0])
    if not op_match:
        return None

    if not _is_plain_int(parts[index["#Hosts"]]):
        return None
    if "#Inst" in index and not _is_plain_int(parts[index["#Inst"]]):
        return None

    actual_rows = parse_scaled_number(parts[index["#Rows"]])
    estimated_rows = parse_scaled_number(parts[index["Est. #Rows"]])
    peak_mem = parse_size_bytes(parts[index["Peak Mem"]])
    estimated_peak_mem = parse_size_bytes(parts[index["Est. Peak Mem"]])
    detail_index = index.get("Detail")
    detail = parts[detail_index] if detail_index is not None and len(parts) > detail_index else ""
    evidence = compact_line(line)
    join_match = JOIN_KIND_RE.search(detail)

    return OperatorFact(
        operator_id=op_match.group("id").zfill(2),
        operator_name=op_match.group("name").upper(),
        time_ms=table_duration_to_ms(parts[index["Max Time"]]),
        actual_rows=actual_rows,
        estimated_rows=estimated_rows,
        peak_mem_bytes=peak_mem,
        estimated_peak_mem_bytes=estimated_peak_mem,
        join_kind=(join_match.group("kind").upper() + " JOIN") if join_match else None,
        is_partitioned=bool(re.search(r"\bPARTITIONED\b", detail, re.IGNORECASE)),
        evidence_lines=[evidence],
    )


def update_operator(existing: OperatorFact, new: OperatorFact) -> None:
    def prefer_max(attr: str) -> None:
        old = getattr(existing, attr)
        val = getattr(new, attr)
        if val is None:
            return
        if old is None or val > old:
            setattr(existing, attr, val)

    for attr in [
        "time_ms",
        "actual_rows",
        "estimated_rows",
        "peak_mem_bytes",
        "estimated_peak_mem_bytes",
    ]:
        prefer_max(attr)
    if not existing.join_kind and new.join_kind:
        existing.join_kind = new.join_kind
    existing.is_partitioned = existing.is_partitioned or new.is_partitioned
    existing.observations.extend(new.observations)
    for line in new.evidence_lines:
        if line not in existing.evidence_lines:
            existing.evidence_lines.append(line)


def parse_raw_counter_number(value: str) -> float | None:
    m = RAW_COUNTER_NUMBER_RE.search(value.strip())
    if not m:
        return None
    raw = m.group("exact") or m.group("display")
    return parse_scaled_number(raw)


def raw_node_section(lines: list[str], start: int) -> str:
    header_indent = line_indent(lines[start])
    section = [lines[start]]
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            section.append(line)
            continue
        if RAW_NODE_HEADER_RE.match(line):
            break
        if line_indent(line) <= header_indent and not stripped.startswith("-"):
            break
        section.append(line)
    return "\n".join(section)


def raw_node_direct_counter_indent(section_lines: list[str], header_indent: int) -> int | None:
    indents: list[int] = []
    child_indent: int | None = None
    for line in section_lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue

        indent = line_indent(line)
        if indent <= header_indent:
            child_indent = None
            continue

        if not stripped.startswith("-"):
            child_indent = indent
            continue

        if child_indent is not None:
            if indent > child_indent:
                continue
            child_indent = None

        if RAW_NODE_DIRECT_COUNTER_RE.match(line):
            indents.append(indent)
    return min(indents) if indents else None


def classify_raw_time_counter(
    line: str, direct_counter_indent: int | None
) -> tuple[str, float | None]:
    m = RAW_TIME_COUNTER_RE.match(line)
    if not m:
        return "not_time", None
    if direct_counter_indent is None or line_indent(line) != direct_counter_indent:
        return "nested_or_cumulative", None
    return "elapsed_operator", extract_first_duration_ms(m.group("value"))


def parse_raw_node_section(lines: list[str], start: int) -> OperatorFact | None:
    header = lines[start]
    m = RAW_NODE_HEADER_RE.match(header)
    if not m:
        return None

    op_name = RAW_NODE_NAME_MAP.get(m.group("node"))
    if not op_name:
        return None

    section = raw_node_section(lines, start)
    section_lines = section.splitlines()
    direct_counter_indent = raw_node_direct_counter_indent(section_lines, line_indent(header))
    evidence = [compact_line(header)]

    actual_rows: float | None = None
    for row_match in RAW_ROW_COUNTER_RE.finditer(section):
        value = parse_raw_counter_number(row_match.group("value"))
        if value is not None and (actual_rows is None or value > actual_rows):
            actual_rows = value
            evidence_line = compact_line(row_match.group(0))
            if evidence_line not in evidence:
                evidence.append(evidence_line)

    peak_mem: float | None = None
    for mem_match in RAW_PEAK_MEMORY_RE.finditer(section):
        value = parse_size_bytes(mem_match.group("value"))
        if value is not None and (peak_mem is None or value > peak_mem):
            peak_mem = value
            evidence_line = compact_line(mem_match.group(0))
            if evidence_line not in evidence:
                evidence.append(evidence_line)

    time_ms: float | None = None
    for line in section_lines[1:]:
        kind, value = classify_raw_time_counter(line, direct_counter_indent)
        if kind != "elapsed_operator":
            continue
        if value is not None and (time_ms is None or value > time_ms):
            time_ms = value
            evidence_line = compact_line(line)
            if evidence_line not in evidence:
                evidence.append(evidence_line)

    return OperatorFact(
        operator_id=m.group("id").zfill(2),
        operator_name=op_name,
        time_ms=time_ms,
        actual_rows=actual_rows,
        peak_mem_bytes=peak_mem,
        evidence_lines=evidence,
    )


def parse_raw_runtime_nodes(lines: list[str]) -> list[OperatorFact]:
    facts: list[OperatorFact] = []
    for i, line in enumerate(lines):
        fact = parse_raw_node_section(lines, i)
        if fact:
            facts.append(fact)
    return facts


def build_operator_windows(lines: list[str], lookahead: int = 3) -> Iterable[tuple[str, str, str]]:
    """Yield (operator_id, operator_name, window_text)."""
    for i, line in enumerate(lines):
        m = OP_RE.search(line)
        if not m:
            continue

        window_parts = [line.strip()]
        for j in range(i + 1, min(len(lines), i + 1 + lookahead)):
            nxt = lines[j]
            if OP_RE.search(nxt):
                break
            stripped = nxt.strip()
            if not stripped:
                break
            if stripped.startswith(("-", "*", "|")) or re.match(
                r"^(Actual|Estimated|Rows|Peak|Exec|Time|Memory|Join|Type|Cardinality)\b",
                stripped,
                flags=re.IGNORECASE,
            ):
                window_parts.append(stripped)
            else:
                break

        yield m.group("id"), m.group("name").upper(), " ".join(window_parts)


def parse_operators(text: str) -> list[OperatorFact]:
    lines = text.splitlines()
    by_key: dict[tuple[str, str], OperatorFact] = {}

    table_line_numbers: set[int] = set()
    columns: list[str] | None = None
    for i, line in enumerate(lines):
        header_columns = exec_summary_columns(line)
        if header_columns is not None:
            columns = header_columns
            continue
        if not line.strip():
            columns = None
            continue
        fact = parse_operator_table_line(line, columns)
        if not fact:
            continue
        table_line_numbers.add(i)
        key = (fact.operator_id, fact.operator_name)
        if key in by_key:
            update_operator(by_key[key], fact)
        else:
            by_key[key] = fact

    for op_id, op_name, window in build_operator_windows(lines):
        first_line = window.split(" ", 1)[0]
        if any(lines[i].strip().startswith(first_line) for i in table_line_numbers):
            continue

        # Parse duration only from the substring after the operator token.
        op_match = OP_RE.search(window)
        after_op = window[op_match.end() :] if op_match else window

        actual_rows, estimated_rows = parse_rows(window)
        if estimated_rows is None:
            estimated_rows = parse_estimated_rows_only(window)
        peak_mem, est_peak_mem = parse_memory(window)
        join_match = JOIN_KIND_RE.search(window)

        fact = OperatorFact(
            operator_id=op_id.zfill(2),
            operator_name=op_name,
            time_ms=extract_first_duration_ms(after_op),
            actual_rows=actual_rows,
            estimated_rows=estimated_rows,
            peak_mem_bytes=peak_mem,
            estimated_peak_mem_bytes=est_peak_mem,
            join_kind=(join_match.group("kind").upper() + " JOIN") if join_match else None,
            is_partitioned=bool(re.search(r"\bPARTITIONED\b", window, re.IGNORECASE)),
            evidence_lines=[compact_line(window)],
        )

        key = (fact.operator_id, fact.operator_name)
        if key in by_key:
            update_operator(by_key[key], fact)
        else:
            by_key[key] = fact

    for fact in parse_raw_runtime_nodes(lines):
        key = (fact.operator_id, fact.operator_name)
        if key in by_key:
            update_operator(by_key[key], fact)
        else:
            by_key[key] = fact

    return sorted(by_key.values(), key=lambda x: (int(x.operator_id), x.operator_name))


from query_doctor.analyzer.operator_views import (  # noqa: E402
    op_label,
    op_to_json,
    operator_key,
    operator_with_best_memory_ratio,
    operator_with_best_rows_ratio,
    operator_with_observation,
    operator_with_zero_memory_estimate_gap,
    operator_with_zero_row_estimate_gap,
)
