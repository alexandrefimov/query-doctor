"""CM query summary and profile response parsing helpers."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from query_doctor.cm.client import format_cm_timestamp, normalize_optional_string
from query_doctor.cm.models import CMAdapterError, CMQueryPage, CMQuerySummary
from query_doctor.safety.redaction import sanitize_adapter_error_message


PROFILE_SQL_STATEMENT_RE = re.compile(
    r"(?ims)^\s*Sql\s+Statement\s*:\s*(?P<statement>.+?)(?:^\s*[A-Z][A-Za-z0-9_ /().-]{2,}\s*:|\Z)"
)
PROFILE_SUMMARY_FIELD_RE = re.compile(
    r"(?im)^\s*(?P<name>Start Time|End Time|Query Type|Query State|Query Status|User|Pool|Request Pool|Resource Pool|Admission Pool)\s*:\s*(?P<value>.+?)\s*$"
)
PROFILE_QUERY_ID_RE = re.compile(
    r"(?im)^\s*(?:Query\s+ID\s*:\s*|Query\s*\(\s*id\s*=\s*)"
    r"(?P<query_id>[A-Za-z0-9]+:[A-Za-z0-9]+)\s*\)?\s*:?\s*$"
)


def extract_statement_from_profile_text(profile_text: str) -> str | None:
    text = profile_details_text(profile_text)
    match = PROFILE_SQL_STATEMENT_RE.search(text)
    if not match:
        return None
    statement = match.group("statement").strip()
    return statement or None


def extract_query_id_from_profile_text(profile_text: str) -> str | None:
    text = profile_details_text(profile_text)
    match = PROFILE_QUERY_ID_RE.search(text)
    if not match:
        return None
    query_id = match.group("query_id").strip()
    return query_id or None


def profile_details_text(profile_text: str) -> str:
    try:
        payload = json.loads(profile_text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        details = payload.get("details")
        if isinstance(details, str) and details.strip():
            return details
    return profile_text


def profile_summary_timestamp_to_iso(value: str) -> str | None:
    raw = value.strip()
    match = re.match(
        r"^(?P<prefix>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d+))?$", raw
    )
    if not match:
        return None
    fraction = match.group("fraction")
    normalized = match.group("prefix").replace(" ", "T")
    if fraction:
        normalized = f"{normalized}.{fraction[:6].ljust(6, '0')}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return format_cm_timestamp(parsed)


def extract_summary_metadata_from_profile_text(profile_text: str) -> dict[str, str | int]:
    text = profile_details_text(profile_text)
    fields: dict[str, str | int] = {}
    for match in PROFILE_SUMMARY_FIELD_RE.finditer(text):
        name = match.group("name").strip().lower()
        value = match.group("value").strip()
        if name == "start time":
            normalized = profile_summary_timestamp_to_iso(value)
            if normalized:
                fields["start_time"] = normalized
        elif name == "end time":
            normalized = profile_summary_timestamp_to_iso(value)
            if normalized:
                fields["end_time"] = normalized
        elif name == "query type" and value:
            fields["query_type"] = value
        elif name == "query state" and value:
            fields["query_state"] = value
        elif name == "query status" and value:
            fields["status"] = value
        elif name == "user" and value:
            fields["user"] = value
        elif name in {"pool", "request pool", "resource pool", "admission pool"} and value:
            fields["pool"] = value
    start_time = fields.get("start_time")
    end_time = fields.get("end_time")
    if isinstance(start_time, str) and isinstance(end_time, str):
        try:
            start = parse_cm_timestamp(start_time)
            end = parse_cm_timestamp(end_time)
        except ValueError:
            start = end = None
        if start is not None and end is not None and end >= start:
            fields["duration_ms"] = int((end - start).total_seconds() * 1000)
    return fields


def merge_profile_summary_metadata(
    summary: CMQuerySummary, profile_text: str
) -> tuple[CMQuerySummary, list[str]]:
    fields = extract_summary_metadata_from_profile_text(profile_text)
    if not fields:
        return summary, []
    updates: dict[str, object] = {}
    for field in ("start_time", "end_time", "duration_ms", "status", "query_state", "query_type"):
        if getattr(summary, field) is None and field in fields:
            updates[field] = fields[field]
    for field in ("user", "pool"):
        if field in fields:
            updates[field] = fields[field]
    if not updates:
        return summary, []
    warnings: list[str] = []
    if "start_time" in updates or "end_time" in updates or "duration_ms" in updates:
        warnings.append("CM profile text timing metadata collected")
    if "status" in updates or "query_state" in updates or "query_type" in updates:
        warnings.append("CM profile text status metadata collected")
    if "user" in updates:
        warnings.append("CM profile text user metadata collected")
    if "pool" in updates:
        warnings.append("CM profile text pool metadata collected")
    return replace(summary, **updates), warnings


def parse_cm_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def padded_cm_timeseries_window(
    summary: CMQuerySummary, *, padding_sec: int
) -> tuple[str, str] | None:
    if not summary.start_time or not summary.end_time:
        return None
    start = parse_cm_timestamp(summary.start_time) - timedelta(seconds=padding_sec)
    end = parse_cm_timestamp(summary.end_time) + timedelta(seconds=padding_sec)
    if end <= start:
        return None
    return format_cm_timestamp(start), format_cm_timestamp(end)


# TODO: Bind these adapters to exact CM API endpoints only after validating the
# response shapes against Cloudera Manager documentation or sanitized samples.
def parse_cm_query_summary(raw: dict[str, object]) -> CMQuerySummary:
    if not isinstance(raw, dict):
        raise CMAdapterError("CM query summary item must be an object.")

    source = raw_with_attributes(raw, query_summary_attributes(raw))
    query_id = normalize_optional_string(first_present(source, ("queryId", "query_id", "id")))
    if not query_id:
        raise CMAdapterError("CM query summary is missing required query id.")

    return CMQuerySummary(
        query_id=query_id,
        start_time=normalize_optional_string(first_present(raw, ("startTime", "start_time"))),
        end_time=normalize_optional_string(first_present(raw, ("endTime", "end_time"))),
        duration_ms=parse_duration_ms(raw),
        status=normalize_optional_string(first_present(source, ("status", "query_status"))),
        user=normalize_optional_string(first_present(source, ("user", "username", "queryUser"))),
        pool=normalize_optional_string(
            first_present(source, ("pool", "poolName", "admissionPool"))
        ),
        query_type=normalize_optional_string(
            first_present(source, ("queryType", "query_type", "statementType", "statement_type"))
        ),
        statement=normalize_optional_string(
            first_present(
                raw,
                (
                    "statement",
                    "statementText",
                    "statement_text",
                    "query",
                    "queryText",
                    "query_text",
                    "sql",
                ),
            )
        ),
        query_state=normalize_optional_string(
            first_present(source, ("queryState", "query_state", "state"))
        ),
        admission_result=normalize_optional_string(
            first_present(
                source,
                ("admissionResult", "admission_result", "admissionStatus", "admission_status"),
            )
        ),
        admission_wait_ms=parse_optional_int_field(
            source,
            (
                "admissionWaitMillis",
                "admissionWaitMs",
                "admissionWait",
                "admission_wait_ms",
                "queuedTimeMillis",
                "queuedTimeMs",
            ),
            "admission_wait_ms",
        ),
        rows_produced=parse_optional_int_field(
            source,
            ("rowsProduced", "rows_produced", "numRowsProduced", "num_rows_produced"),
            "rows_produced",
        ),
        bytes_read=parse_optional_int_field(
            source,
            ("bytesRead", "bytes_read", "hdfsBytesRead", "hdfs_bytes_read"),
            "bytes_read",
        ),
        bytes_sent=parse_optional_int_field(
            source,
            ("bytesSent", "bytes_sent", "totalBytesSent", "total_bytes_sent"),
            "bytes_sent",
        ),
        memory_aggregate_peak=parse_optional_int_field(
            source,
            (
                "memoryAggregatePeak",
                "memory_aggregate_peak",
                "peakMemoryBytes",
                "peak_memory_bytes",
            ),
            "memory_aggregate_peak",
        ),
        memory_per_node_peak=parse_optional_int_field(
            source,
            (
                "memoryPerNodePeak",
                "memory_per_node_peak",
                "perNodePeakMemoryBytes",
                "per_node_peak_memory_bytes",
            ),
            "memory_per_node_peak",
        ),
    )


def parse_cm_query_details_summary(raw: dict[str, object], query_id: str) -> CMQuerySummary:
    if not isinstance(raw, dict):
        raise CMAdapterError("CM query details item must be an object.")

    details_query_id = (
        normalize_optional_string(first_present(raw, ("queryId", "query_id", "id"))) or query_id
    )
    if details_query_id != query_id:
        raise CMAdapterError("CM query details query id did not match the requested query id.")

    summary = parse_cm_query_summary({**raw, "queryId": details_query_id})
    if summary.query_id != query_id:
        raise CMAdapterError("CM query details query id did not match the requested query id.")
    return summary


def parse_cm_query_summary_page(raw: dict[str, object]) -> CMQueryPage:
    if not isinstance(raw, dict):
        raise CMAdapterError("CM query summary page must be an object.")

    items_raw = first_present(raw, ("items", "queries", "querySummaries", "impalaQueries"))
    if items_raw is None:
        items_raw = []
    if not isinstance(items_raw, list):
        raise CMAdapterError("CM query summary page items must be a list.")

    token_raw = first_present(
        raw,
        ("nextPageToken", "next_page_token", "nextToken", "next", "nextOffset", "next_offset"),
    )
    paging = raw.get("paging")
    if token_raw is None and isinstance(paging, dict):
        token_raw = first_present(
            paging,
            ("nextPageToken", "next_page_token", "nextToken", "nextOffset", "next_offset"),
        )

    warnings_raw = raw.get("warnings")
    warnings: list[str] = []
    if isinstance(warnings_raw, list):
        warnings = [sanitize_adapter_error_message(warning) for warning in warnings_raw]

    return CMQueryPage(
        items=[parse_cm_query_summary(item) for item in items_raw],
        next_page_token=normalize_optional_string(token_raw),
        warnings=warnings,
    )


def extract_profile_text(raw: dict[str, object]) -> str:
    if not isinstance(raw, dict):
        raise CMAdapterError("CM profile response must be an object.")

    for field in ("profile", "profileText", "text"):
        if field not in raw:
            continue
        value = raw[field]
        if not isinstance(value, str):
            raise CMAdapterError(f"CM profile field {field} must be a string.")
        return value

    raise CMAdapterError("CM profile response is missing profile text field.")


def first_present(raw: dict[str, object], names: tuple[str, ...]) -> object | None:
    for name in names:
        value = raw.get(name)
        if value is not None:
            return value
    return None


def query_summary_attributes(raw: dict[str, object]) -> dict[str, object]:
    attributes = raw.get("attributes")
    if isinstance(attributes, dict):
        return dict(attributes)
    if not isinstance(attributes, list):
        return {}
    parsed: dict[str, object] = {}
    for item in attributes:
        if not isinstance(item, dict):
            continue
        name = normalize_optional_string(first_present(item, ("name", "key")))
        if not name:
            continue
        value = first_present(item, ("value", "values"))
        if isinstance(value, list):
            if value:
                parsed[name] = value[0]
            continue
        if value is not None:
            parsed[name] = value
    return parsed


def raw_with_attributes(
    raw: dict[str, object],
    attributes: dict[str, object],
) -> dict[str, object]:
    combined = dict(attributes)
    combined.update({key: value for key, value in raw.items() if value is not None})
    return combined


def parse_duration_ms(raw: dict[str, object]) -> int | None:
    attributes = query_summary_attributes(raw)
    source = raw_with_attributes(raw, attributes)
    duration_ms = first_present(
        source,
        ("durationMillis", "durationMs", "duration_ms", "queryDuration", "query_duration"),
    )
    if duration_ms is not None:
        return parse_int_field(duration_ms, "duration_ms")

    duration_sec = first_present(source, ("durationSec", "duration_sec", "durationSeconds"))
    if duration_sec is not None:
        return int(parse_float_field(duration_sec, "duration_sec") * 1000)

    return None


def parse_int_field(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise CMAdapterError(f"CM query summary field {field_name} must be numeric.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError as exc:
            raise CMAdapterError(f"CM query summary field {field_name} must be numeric.") from exc
    raise CMAdapterError(f"CM query summary field {field_name} must be numeric.")


def parse_optional_int_field(
    raw: dict[str, object],
    names: tuple[str, ...],
    field_name: str,
) -> int | None:
    value = first_present(raw, names)
    if value is None:
        return None
    return parse_int_field(value, field_name)


def parse_float_field(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise CMAdapterError(f"CM query summary field {field_name} must be numeric.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise CMAdapterError(f"CM query summary field {field_name} must be numeric.") from exc
    raise CMAdapterError(f"CM query summary field {field_name} must be numeric.")
