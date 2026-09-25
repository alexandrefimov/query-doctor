"""Bounded facts about a failed or cancelled Impala query, read from its profile.

The Recent profile worker already fetches the profile of every failed query it
selects, but the optimisation analysis it stores says nothing about why the
query failed. These facts keep that part: the failure category, where the
query was in its lifecycle, admission and memory at the time of failure, and
the node that reported the error.

Everything here is read from the Summary block and the query timeline, which
Impala prints at the start of a profile, so an excerpt with its middle removed
still yields the facts. Status text is kept only when the caller asks for it:
it can carry identifiers and SQL fragments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from query_doctor.analyzer.profile_text import normalize_profile_text
from query_doctor.analyzer.profile_timings import (
    TIMELINE_EVENT_RE,
    duration_pair,
    normalized_label,
    query_event_key,
)
from query_doctor.analyzer.scalars import table_duration_to_ms
from query_doctor.recent.statement_identity import error_class_from_status


IMPALA_FAILURE_FACTS_CONTRACT = "impala_failure_facts_v1"
MAX_ERROR_MESSAGE_CHARS = 2048
MAX_QUEUE_REASON_CHARS = 512
MAX_TIMELINE_EVENTS = 16

# Summary labels that can follow Query Status. The status itself can span
# hundreds of lines (memory tracker dumps, Java stacks), so it ends only at a
# line that is certainly the next Summary field.
_SUMMARY_LABELS = (
    "Session ID",
    "Session Type",
    "HiveServer2 Protocol Version",
    "Start Time",
    "End Time",
    "Duration",
    "Query Type",
    "Query State",
    "Impala Query State",
    "Query Status",
    "Impala Version",
    "User",
    "Connected User",
    "Delegated User",
    "Network Address",
    "Default Db",
    "Sql Statement",
    "Coordinator",
    "Query Options (set by configuration)",
    "Query Options (set by configuration and planner)",
    "Plan",
    "Estimated Per-Host Mem",
    "Request Pool",
    "Per Host Min Memory Reservation",
    "Per Host Number of Fragment Instances",
    "Admission result",
    "Cluster Memory Admitted",
    "Executor Group",
    "Initial admission queue reason",
    "Latest admission queue reason",
    "ExecSummary",
    "Errors",
    "Query Timeline",
    "Query Compilation",
    "Frontend",
    "DDL Type",
)
_SUMMARY_FIELD_RE = re.compile(
    r"^ {4}(?P<label>" + "|".join(re.escape(label) for label in _SUMMARY_LABELS) + r")"
    r":(?: (?P<value>.*))?$"
)
# Statement, plan and memory-tracker lines can also start with two spaces
# (a tracker dump prints "  Fragment <instance id>:"), so only the known
# section headers end the Summary.
_SECTION_RE = re.compile(
    r"^  (?:ImpalaServer:|Execution Profile [0-9a-f]+:[0-9a-f]+|"
    r"(?:Averaged |Coordinator )?Fragment F\d+:)"
)
# Printed in the Execution Profile section, after the Summary.
_PER_NODE_PEAK_MEMORY_RE = re.compile(
    r"^\s*Per Node Peak Memory Usage:(?P<value>.*)$", re.MULTILINE
)
_JAVA_FRAME_RE = re.compile(r"^\s+at [\w$.<>/-]+\(")
_PROFILE_QUERY_ID_RE = re.compile(r"^Query \(id=(?P<id>[0-9a-f]+:[0-9a-f]+)\)", re.MULTILINE)
# A process-limit failure dumps the trackers of every query on the node, so
# only the line naming this query, or the one that says it hit its own limit,
# describes this query.
_QUERY_MEMORY_RE = re.compile(
    r"Query\((?P<id>[0-9a-f]+:[0-9a-f]+)\):(?P<exceeded> memory limit exceeded\.)?"
    r" Limit=(?P<limit>-?[\d.]+ ?[KMGTP]?B)\b"
    r".*?Total=(?P<total>-?[\d.]+ ?[KMGTP]?B)\b"
    r" Peak=(?P<peak>-?[\d.]+ ?[KMGTP]?B)\b"
)
_PROCESS_LEFT_RE = re.compile(r"Memory left in process limit: (?P<value>-?[\d.]+ ?[KMGTP]?B)\b")
_QUERY_LEFT_RE = re.compile(r"Memory left in query limit: (?P<value>-?[\d.]+ ?[KMGTP]?B)\b")
_FAILING_BACKEND_RE = re.compile(
    r"Error occurred on backend (?P<node>[^\s]+?)"
    r"(?: by fragment (?P<fragment>[0-9a-f]+:[0-9a-f]+))?(?:\s|$)"
)
_IO_NODE_RE = re.compile(r"Disk I/O error on (?P<node>[^\s:]+(?::\d+)?)")
_READY_TO_START_RE = re.compile(r"Ready to start on (?P<count>\d+) backends?", re.IGNORECASE)
_NODE_VALUE_RE = re.compile(r"(?P<node>[^\s()]+)\((?P<value>[^)]*)\)")
_SIZE_RE = re.compile(r"^\s*(?P<number>-?[\d.,]+)\s*(?P<unit>[KMGTP]?B)\s*$")
# Impala prints binary magnitudes under decimal names: GB means 1024^3.
_SIZE_SCALE = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
_EXCEPTION_PREFIX_RE = re.compile(r"^[A-Za-z_$][\w$.]*(?:Exception|Error)\b")

_FAILURE_EVENT_KEYS = {
    "executionerror": "execution_error",
    "executioncancelled": "execution_cancelled",
    "cancelled": "cancelled",
    "requestfinished": "request_finished",
    "catalogddlrequestfinished": "catalog_ddl_request_finished",
}
_FAILURE_AT_EVENTS = ("execution_error", "execution_cancelled", "cancelled")

# (lowercase status prefix or fragment, category, cancellation source); first
# match wins, so the specific forms of "Cancelled" and "Session closed" come
# before the plain ones.
_STATUS_RULES: tuple[tuple[str, str, str, str | None], ...] = (
    ("prefix", "parseexception", "syntax", None),
    ("prefix", "authorizationexception", "authorization", None),
    ("prefix", "analysisexception", "analysis", None),
    ("prefix", "tablenotfoundexception", "analysis", None),
    ("prefix", "memory limit exceeded", "memory_limit", None),
    ("prefix", "rejected query from pool", "admission_rejected", None),
    ("prefix", "admission for query exceeded timeout", "admission_timeout", None),
    ("prefix", "cancelled (queued)", "cancelled", "admission_queue"),
    ("prefix", "cancelled from impala's debug web interface", "cancelled", "web_ui"),
    ("prefix", "cancelled", "cancelled", "client"),
    ("prefix", "session closed from impala's debug web interface", "session_closed", "web_ui"),
    ("prefix", "session closed", "session_closed", "session_closed"),
    ("prefix", "session expired due to inactivity", "idle_session_timeout", "idle_session_timeout"),
    ("fragment", "expired due to client inactivity", "idle_query_timeout", "idle_query_timeout"),
    (
        "fragment",
        "expired due to execution time limit",
        "execution_time_limit",
        "execution_time_limit",
    ),
    ("fragment", "terminated due to", "resource_limit", "resource_limit"),
    ("prefix", "disk i/o error", "io", None),
    ("prefix", "file '", "io", None),
)
_CANCELLED_SOURCES = {
    "client",
    "web_ui",
    "admission_queue",
    "idle_query_timeout",
    "idle_session_timeout",
    "session_closed",
}
_FAILED_STATES = {"exception", "error", "failed", "failure", "cancelled", "canceled"}


def build_impala_failure_facts(
    profile_text: str, *, include_error_text: bool = False
) -> dict[str, object] | None:
    """Return failure facts for a failed or cancelled query, or None.

    None means the profile does not show a failure: the status is OK, or the
    Summary block is missing. With include_error_text the bounded status
    text, the admission queue reason, the default database and the failing
    fragment are kept as Impala printed them, after the collector's redaction.
    """

    text = normalize_profile_text(profile_text)
    fields, status_lines = _summary_fields(text)
    status = "\n".join(status_lines).strip()
    query_state = fields.get("Query State", "")
    if not _is_failure(status, query_state):
        return None
    first_line = status_lines[0].strip() if status_lines else ""
    category, source = _classify_status(first_line)
    facts: dict[str, object] = {
        "facts_contract": IMPALA_FAILURE_FACTS_CONTRACT,
        "outcome": "cancelled" if source in _CANCELLED_SOURCES else "failed",
        "error_category": category,
    }
    _put(facts, "cancellation_source", source)
    _put(facts, "error_class", error_class_from_status(first_line))
    _put(facts, "query_state", _word(query_state))
    _put(facts, "impala_query_state", _word(fields.get("Impala Query State")))
    _put(facts, "query_type", _word(fields.get("Query Type")))
    _put(facts, "session_type", _word(fields.get("Session Type")))

    timeline = _timeline_events(text)
    _put(facts, "timings", _timings(fields, timeline))
    if timeline:
        facts["timeline"] = [
            {"event": key, "at_ms": at_ms} for key, at_ms in timeline[:MAX_TIMELINE_EVENTS]
        ]
    ready = _READY_TO_START_RE.search(text)
    if ready:
        facts["backend_count"] = int(ready.group("count"))

    admission: dict[str, object] = {}
    _put(admission, "result", _bounded(fields.get("Admission result"), 128))
    if include_error_text:
        reason = fields.get("Latest admission queue reason") or fields.get(
            "Initial admission queue reason"
        )
        _put(admission, "queue_reason", _bounded(reason, MAX_QUEUE_REASON_CHARS))
    _put(facts, "admission", admission)
    per_node_peak = _PER_NODE_PEAK_MEMORY_RE.search(text)
    profile_query_id = _PROFILE_QUERY_ID_RE.search(text)
    _put(
        facts,
        "memory",
        _memory(
            fields,
            status,
            per_node_peak.group("value") if per_node_peak else "",
            profile_query_id.group("id") if profile_query_id else None,
        ),
    )

    failing = _FAILING_BACKEND_RE.search(status)
    node = failing or _IO_NODE_RE.search(first_line)
    _put(facts, "failing_node", node.group("node") if node else None)
    if include_error_text:
        if failing:
            _put(facts, "failing_fragment", failing.group("fragment"))
        _put(facts, "default_db", _bounded(fields.get("Default Db"), 128))
        message, truncated = _error_message(status_lines)
        if message:
            facts["error_message"] = message
            facts["error_message_truncated"] = truncated
    return facts


def _summary_fields(text: str) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    status_lines: list[str] = []
    in_summary = False
    in_status = False
    for line in text.splitlines():
        if line.startswith("  Summary:"):
            in_summary = True
            continue
        if not in_summary:
            continue
        if _SECTION_RE.match(line):
            # The next two-space section (ImpalaServer, Execution Profile...)
            # ends the Summary. Lines at column zero do not: they continue a
            # multi-line field such as the status, the plan or ExecSummary.
            break
        match = _SUMMARY_FIELD_RE.match(line)
        if match:
            in_status = match.group("label") == "Query Status"
            value = (match.group("value") or "").strip()
            fields.setdefault(match.group("label"), value)
            if in_status:
                status_lines.append(value)
            continue
        if in_status:
            status_lines.append(line.rstrip())
    return fields, status_lines


def _is_failure(status: str, query_state: str) -> bool:
    if query_state.strip().lower() in _FAILED_STATES:
        return True
    return bool(status) and status.strip().upper() != "OK"


def _classify_status(first_line: str) -> tuple[str, str | None]:
    lowered = first_line.lower()
    for kind, pattern, category, source in _STATUS_RULES:
        if (kind == "prefix" and lowered.startswith(pattern)) or (
            kind == "fragment" and pattern in lowered
        ):
            return category, source
    if _EXCEPTION_PREFIX_RE.match(first_line):
        return "runtime", None
    return "other", None


def _error_message(status_lines: list[str]) -> tuple[str, bool]:
    kept = [line for line in status_lines if not _JAVA_FRAME_RE.match(line)]
    while kept and not kept[-1].strip():
        kept.pop()
    message = "\n".join(kept).strip()
    if len(message) <= MAX_ERROR_MESSAGE_CHARS:
        return message, len(kept) != len(status_lines) and bool(message)
    return message[:MAX_ERROR_MESSAGE_CHARS].rstrip(), True


def _timeline_events(text: str) -> list[tuple[str, int]]:
    events: list[tuple[str, int]] = []
    in_timeline = False
    for line in text.splitlines():
        if line.startswith("    Query Timeline:"):
            in_timeline = True
            continue
        if not in_timeline:
            continue
        stripped = line.strip()
        if not stripped or not stripped.startswith("-"):
            break
        match = TIMELINE_EVENT_RE.match(line)
        if not match:
            continue
        label = match.group("label")
        key = query_event_key(label) or _FAILURE_EVENT_KEYS.get(normalized_label(label))
        elapsed_ms, _delta = duration_pair(match.group("value"))
        if key is not None and elapsed_ms is not None:
            events.append((key, int(round(elapsed_ms))))
    return events


def _timings(fields: Mapping[str, str], timeline: list[tuple[str, int]]) -> dict[str, object]:
    at = {}
    for key, at_ms in timeline:
        at.setdefault(key, at_ms)
    timings: dict[str, object] = {}
    duration = table_duration_to_ms(fields.get("Duration", ""))
    _put(timings, "duration_ms", int(round(duration)) if duration is not None else None)
    _put(timings, "planning_ms", at.get("planning_finished"))
    if "submit_for_admission" in at and "completed_admission" in at:
        timings["admission_wait_ms"] = max(
            0, at["completed_admission"] - at["submit_for_admission"]
        )
    failure_at = next((at[key] for key in _FAILURE_AT_EVENTS if key in at), None)
    _put(timings, "failure_at_ms", failure_at)
    return timings


def _memory(
    fields: Mapping[str, str], status: str, per_node_peak: str, query_id: str | None
) -> dict[str, object]:
    memory: dict[str, object] = {}
    query_memory = next(
        (
            match
            for match in _QUERY_MEMORY_RE.finditer(status)
            if match.group("id") == query_id or (query_id is None and match.group("exceeded"))
        ),
        None,
    )
    if query_memory:
        _put(memory, "query_limit_bytes", _size(query_memory.group("limit")))
        _put(memory, "query_total_bytes_at_failure", _size(query_memory.group("total")))
        _put(memory, "query_peak_bytes_at_failure", _size(query_memory.group("peak")))
    for regex, key in (
        (_PROCESS_LEFT_RE, "process_memory_left_bytes"),
        (_QUERY_LEFT_RE, "query_memory_left_bytes"),
    ):
        match = regex.search(status)
        if match:
            _put(memory, key, _size(match.group("value")))
    peaks = [
        (match.group("node"), _size(match.group("value")))
        for match in _NODE_VALUE_RE.finditer(per_node_peak)
    ]
    peaks = [(node, value) for node, value in peaks if value is not None]
    if peaks:
        node, value = max(peaks, key=lambda item: item[1])
        memory["per_node_peak_max_bytes"] = value
        if value > 0:
            memory["per_node_peak_max_node"] = node
        memory["node_count"] = len(peaks)
    _put(memory, "cluster_memory_admitted_bytes", _size(fields.get("Cluster Memory Admitted")))
    _put(memory, "estimated_per_host_bytes", _size(fields.get("Estimated Per-Host Mem")))
    return memory


def _size(value: str | None) -> int | None:
    text = (value or "").strip()
    if text.isdigit():
        return int(text)
    match = _SIZE_RE.match(text)
    if not match:
        return None
    try:
        number = float(match.group("number").replace(",", ""))
    except ValueError:
        return None
    return int(round(number * _SIZE_SCALE[match.group("unit")]))


def _word(value: str | None) -> str | None:
    text = (value or "").strip()
    return text if re.fullmatch(r"[A-Za-z_]{1,32}", text) else None


def _bounded(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    return text[:limit] if text else None


def _put(target: dict[str, object], key: str, value: object) -> None:
    if value is None or value == {} or value == "":
        return
    target[key] = value
