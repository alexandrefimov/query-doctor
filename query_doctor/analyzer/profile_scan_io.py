"""Raw-free scan I/O and tuple cache facts summed over Impala fragment instances.

Impala 4.x reports per scan how much data its data cache served, how much the
Parquet reader pruned by statistics, page index and runtime filters, and per
tuple cache node whether it hit. These facts are context: they explain a large
read or a fast cached report, and no finding or score reads them.

Only per-instance profiles are summed. Averaged Fragment sections repeat the
same scans as averages, and Per Node Profiles hold host-level sections.
"""

from __future__ import annotations

import re
from typing import Any

from query_doctor.analyzer.runtime_counters import line_indent
from query_doctor.analyzer.scalars import parse_scaled_number, parse_size_bytes

# A section header ends with a colon and at most a "(Total: ...)" summary; an info
# line such as "Table Name: x" or "Node Lifecycle Event Timeline: 1s" is not one.
SECTION_HEADER_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z].*?)(?:\s*\[[^\]]*\])?\s*:\s*(?:\(Total:[^)]*\))?\s*$"
)
COUNTER_RE = re.compile(r"^\s*-\s*(?P<name>[A-Za-z][A-Za-z ]*?)\s*:\s*(?P<value>[^\n\r]+)$")
EXACT_VALUE_RE = re.compile(r"\((?P<exact>\d+)\)\s*$")

SCAN_NODE_RE = re.compile(r"^(?:HDFS|KUDU|HBASE)?_?SCAN_NODE \(id=\d{1,3}\)$")
TUPLE_CACHE_NODE_RE = re.compile(r"^TUPLE_CACHE_NODE \(id=\d{1,3}\)$")
FILTER_SECTION_RE = re.compile(r"^Filter \d+ \(")

SCAN_COUNTERS = {
    "DataCacheHitBytes": "data_cache_hit_bytes",
    "DataCacheMissBytes": "data_cache_miss_bytes",
    "DataCachePartialHitCount": "data_cache_partial_hit_count",
    "NumRowGroups": "row_groups",
    "NumStatsFilteredRowGroups": "stats_filtered_row_groups",
    "NumRuntimeFilteredRowGroups": "runtime_filtered_row_groups",
    "NumBloomFilteredRowGroups": "bloom_filtered_row_groups",
    "NumRowGroupsWithPageIndex": "row_groups_with_page_index",
    "NumPages": "pages",
    "NumStatsFilteredPages": "stats_filtered_pages",
    "NumRuntimeFilteredPages": "runtime_filtered_pages",
    "NumPagesSkippedByLateMaterialization": "pages_skipped_by_late_materialization",
    "NumFileMetadataRead": "file_metadata_reads",
}
# "RowGroups" filter counters are left out: the Parquet scanner also adds
# page-level min/max checks to them, so their unit is ambiguous. Scan-level
# NumRuntimeFilteredRowGroups and NumRuntimeFilteredPages carry that instead.
FILTER_COUNTERS = {
    "Rows rejected": "filter_rows_rejected",
    "Files rejected": "filter_files_rejected",
}
TUPLE_CACHE_COUNTERS = {
    "NumTupleCacheHits": "hits",
    "NumTupleCacheSkipped": "skipped",
    "NumTupleCacheHalted": "halted",
    "NumTupleCacheBackpressureHalted": "backpressure_halted",
    "TupleCacheBytesRead": "bytes_read",
    "TupleCacheBytesWritten": "bytes_written",
}


def build_profile_scan_io_facts(text: str) -> dict[str, Any]:
    scan: dict[str, Any] = {key: 0 for key in SCAN_COUNTERS.values()}
    scan.update({key: 0 for key in FILTER_COUNTERS.values()})
    scan.update(scan_instance_count=0, filter_sections=0, filter_sections_checked_partially=0)
    cache: dict[str, Any] = {key: 0 for key in TUPLE_CACHE_COUNTERS.values()}
    cache["node_instance_count"] = 0

    # Stack of (indent, section name) for the headers enclosing the current line.
    stack: list[tuple[int, str]] = []
    filter_rows: dict[str, float] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = line_indent(line)
        counter = COUNTER_RE.match(line)
        if counter is None:
            header = SECTION_HEADER_RE.match(line)
            if header is None:
                continue
            _close_filter_section(stack, indent, filter_rows, scan)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            name = header.group("name").strip()
            stack.append((indent, name))
            if not _inside_instance(stack):
                continue
            if SCAN_NODE_RE.match(name):
                scan["scan_instance_count"] += 1
            elif TUPLE_CACHE_NODE_RE.match(name):
                cache["node_instance_count"] += 1
            elif FILTER_SECTION_RE.match(name) and _parent_is(stack, SCAN_NODE_RE):
                scan["filter_sections"] += 1
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack or not _inside_instance(stack):
            continue
        name, value = counter.group("name").strip(), counter.group("value")
        owner = stack[-1][1]
        if SCAN_NODE_RE.match(owner) and name in SCAN_COUNTERS:
            scan[SCAN_COUNTERS[name]] += _counter_value(value)
        elif TUPLE_CACHE_NODE_RE.match(owner) and name in TUPLE_CACHE_COUNTERS:
            cache[TUPLE_CACHE_COUNTERS[name]] += _counter_value(value)
        elif FILTER_SECTION_RE.match(owner) and _parent_is(stack, SCAN_NODE_RE, depth=2):
            if name in FILTER_COUNTERS:
                scan[FILTER_COUNTERS[name]] += _counter_value(value)
            elif name in ("Rows processed", "Rows total"):
                filter_rows[name] = _counter_value(value)
    _close_filter_section(stack, -1, filter_rows, scan)

    looked_up = scan["data_cache_hit_bytes"] + scan["data_cache_miss_bytes"]
    scan["data_cache_hit_ratio"] = (
        round(scan["data_cache_hit_bytes"] / looked_up, 4) if looked_up else None
    )
    # The analyzer passes empty text when it cannot read this profile dialect.
    absent = "not_observed" if text.strip() else "unknown"
    scan["status"] = "observed" if scan["scan_instance_count"] else absent
    cache["status"] = "observed" if cache["node_instance_count"] else absent
    return {"scan_io": _ints(scan), "tuple_cache": _ints(cache)}


def _inside_instance(stack: list[tuple[int, str]]) -> bool:
    names = [name for _, name in stack]
    return any(name.startswith("Instance ") for name in names) and not any(
        name.startswith("Averaged Fragment") for name in names
    )


def _parent_is(stack: list[tuple[int, str]], pattern: re.Pattern[str], depth: int = 2) -> bool:
    return len(stack) >= depth and bool(pattern.match(stack[-depth][1]))


def _close_filter_section(
    stack: list[tuple[int, str]], indent: int, filter_rows: dict[str, float], scan: dict[str, Any]
) -> None:
    """Count a filter that stopped checking rows before it saw all of them."""
    if not stack or not FILTER_SECTION_RE.match(stack[-1][1]) or stack[-1][0] < indent:
        return
    processed, total = filter_rows.get("Rows processed"), filter_rows.get("Rows total")
    if processed is not None and total is not None and processed < total:
        scan["filter_sections_checked_partially"] += 1
    filter_rows.clear()


def _counter_value(value: str) -> float:
    exact = EXACT_VALUE_RE.search(value.strip())
    if exact:
        return float(exact.group("exact"))
    parsed = parse_size_bytes(value) or parse_scaled_number(value.split("(")[0])
    return float(parsed or 0)


def _ints(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: int(value) if isinstance(value, float) and value.is_integer() else value
        for key, value in values.items()
    }
