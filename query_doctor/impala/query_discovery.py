"""Bounded Impala daemon query discovery from debug web endpoints."""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from query_doctor.cm.models import CMAdapterError, CMClientError, CMQuerySummary
from query_doctor.cm.profile_parsing import format_cm_timestamp
from query_doctor.impala.profile_source import (
    DEFAULT_IMPALA_PROFILE_PORT,
    DEFAULT_IMPALA_PROFILE_SCHEME,
    DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    UrlOpener,
    normalize_impala_profile_hosts,
    normalize_impala_profile_scheme,
)
from query_doctor.safety.http_egress import configured_diagnostic_urlopen


DEFAULT_MAX_QUERY_LIST_BYTES = 5 * 1024 * 1024
IMPALA_QUERY_LIST_PATHS = ("/queries?json", "/queries?json=true")
DURATION_UNIT_MS = {
    "ns": 0.000_001,
    "us": 0.001,
    "µs": 0.001,
    "ms": 1.0,
    "millis": 1.0,
    "milliseconds": 1.0,
    "s": 1_000.0,
    "sec": 1_000.0,
    "secs": 1_000.0,
    "second": 1_000.0,
    "seconds": 1_000.0,
    "m": 60_000.0,
    "min": 60_000.0,
    "mins": 60_000.0,
    "minute": 60_000.0,
    "minutes": 60_000.0,
    "h": 3_600_000.0,
    "hr": 3_600_000.0,
    "hrs": 3_600_000.0,
    "hour": 3_600_000.0,
    "hours": 3_600_000.0,
}
# Longest spellings first, so that `ms` is not read as `m` and `min` is not read
# as `m` either. A missing unit is only valid for a lone number.
DURATION_TERM_RE = re.compile(
    r"\s*([0-9]+(?:\.[0-9]+)?)\s*"
    r"(milliseconds|minutes|seconds|millis|minute|second|hours|hour|mins|secs|"
    r"min|sec|hrs|ms|us|µs|ns|hr|h|m|s)?"
)
QUERY_ID_RE = re.compile(r"\b[0-9a-f]{16}:[0-9a-f]{16}\b", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ImpalaQueryDiscoveryResult:
    summaries: list[CMQuerySummary]
    warnings: list[str]
    attempted_endpoints: int
    query_log_at_capacity: bool = False


def impala_query_list_urls(
    hosts: Iterable[str],
    *,
    port: int = DEFAULT_IMPALA_PROFILE_PORT,
    scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME,
) -> tuple[str, ...]:
    normalized_scheme = normalize_impala_profile_scheme(scheme)
    urls: list[str] = []
    for host in normalize_impala_profile_hosts(tuple(hosts)):
        netloc = host if ":" in host else f"{host}:{port}"
        for path in IMPALA_QUERY_LIST_PATHS:
            urls.append(f"{normalized_scheme}://{netloc}{path}")
    return tuple(urls)


def fetch_impala_query_summaries(
    *,
    hosts: Iterable[str],
    port: int = DEFAULT_IMPALA_PROFILE_PORT,
    scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME,
    timeout_sec: int = DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    max_query_list_bytes: int = DEFAULT_MAX_QUERY_LIST_BYTES,
    opener: UrlOpener = configured_diagnostic_urlopen,
) -> ImpalaQueryDiscoveryResult:
    normalized_hosts = normalize_impala_profile_hosts(tuple(hosts))
    urls = impala_query_list_urls(normalized_hosts, port=port, scheme=scheme)
    if not urls:
        raise CMAdapterError("Impala query discovery requires at least one impalad host.")

    summaries_by_query_id: dict[str, CMQuerySummary] = {}
    warnings: list[str] = []
    attempted = 0
    successful = 0
    query_log_at_capacity = False
    for url in urls:
        attempted += 1
        try:
            payload = fetch_impala_query_list_url(
                url,
                timeout_sec=timeout_sec,
                max_query_list_bytes=max_query_list_bytes,
                opener=opener,
            )
        except CMClientError:
            continue
        successful += 1
        query_log_at_capacity = query_log_at_capacity or query_list_payload_at_capacity(payload)
        for warning in query_list_payload_warnings(
            payload,
            configured_profile_host_count=len(normalized_hosts),
        ):
            if warning not in warnings:
                warnings.append(warning)
        for summary in parse_impala_query_list_payload(payload):
            summaries_by_query_id.setdefault(summary.query_id, summary)
    if successful == 0:
        raise CMAdapterError(
            "Impala query discovery did not find a readable query list on the configured impalad endpoints. "
            f"Attempted endpoints: {attempted}."
        )
    if not summaries_by_query_id:
        warnings.append("Impala daemon query discovery returned no query summaries.")
    return ImpalaQueryDiscoveryResult(
        summaries=list(summaries_by_query_id.values()),
        warnings=warnings,
        attempted_endpoints=attempted,
        query_log_at_capacity=query_log_at_capacity,
    )


def query_list_payload_warnings(
    payload: Any,
    *,
    configured_profile_host_count: int,
) -> list[str]:
    if not isinstance(payload, dict):
        return []
    warnings: list[str] = []
    if query_list_payload_at_capacity(payload):
        warnings.append(
            "Impala daemon completed query list is at its retained log size; direct Recent "
            "scans cannot inspect older daemon entries. Run a fresh table-backed query or "
            "narrow the validation flow to a fresh Known Query ID."
        )
    query_location_count = top_level_query_location_count(payload)
    if configured_profile_host_count == 1 and query_location_count > configured_profile_host_count:
        warnings.append(
            "Impala daemon query list exposes multiple query location hints while one "
            "profile host is configured; load-balanced or ingress profile collection may "
            "miss the daemon that owns a Known Query ID. Configure explicit daemon profile "
            "hosts when available, or validate with a fresh retained Query ID."
        )
    return warnings


def query_list_payload_at_capacity(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    completed_queries = payload.get("completed_queries")
    completed_query_count = len(completed_queries) if isinstance(completed_queries, list) else 0
    completed_log_size = safe_positive_int(payload.get("completed_log_size"))
    return completed_log_size is not None and completed_query_count >= completed_log_size


def top_level_query_location_count(payload: dict[str, Any]) -> int:
    count = 0
    for key, value in payload.items():
        normalized = str(key).lower()
        if "query" in normalized and "location" in normalized and isinstance(value, list):
            count += len(value)
    return count


def safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def fetch_impala_query_list_url(
    url: str,
    *,
    timeout_sec: int,
    max_query_list_bytes: int,
    opener: UrlOpener,
) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json,text/plain"})
    try:
        with opener(request, timeout=timeout_sec) as response:
            raw = response.read(max_query_list_bytes + 1)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        raise CMClientError("Impala query list endpoint request failed safely.") from exc
    if len(raw) > max_query_list_bytes:
        raise CMClientError(
            "Impala query list endpoint response exceeded the configured byte limit."
        )
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CMClientError("Impala query list endpoint did not return JSON.") from exc


def parse_impala_query_list_payload(payload: Any) -> list[CMQuerySummary]:
    entries = list(iter_query_entries(payload))
    summaries: list[CMQuerySummary] = []
    for entry, default_status in entries:
        summary = parse_impala_query_entry(entry, default_status=default_status)
        if summary is not None:
            summaries.append(summary)
    return summaries


def iter_query_entries(payload: Any) -> Iterable[tuple[dict[str, Any], str | None]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item, None
        return
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if isinstance(value, list) and key_is_query_collection(key):
            default_status = default_status_for_collection_key(key)
            for item in value:
                if isinstance(item, dict):
                    yield item, default_status
    for wrapper_key in ("queries", "query_info", "queryInfo"):
        value = payload.get(wrapper_key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item, None
        elif isinstance(value, dict):
            yield from iter_query_entries(value)


def key_is_query_collection(key: str) -> bool:
    normalized = key.lower()
    if "location" in normalized:
        return False
    return "queries" in normalized or "query_info" in normalized or "queryinfo" in normalized


def default_status_for_collection_key(key: str) -> str | None:
    normalized = key.lower()
    if any(marker in normalized for marker in ("in_flight", "inflight", "running", "active")):
        return "running"
    if any(marker in normalized for marker in ("completed", "complete", "finished")):
        return "finished"
    return None


def parse_impala_query_entry(
    raw: dict[str, Any], *, default_status: str | None
) -> CMQuerySummary | None:
    query_id = normalize_string(first_present(raw, ("query_id", "queryId", "id", "query-id")))
    if not query_id:
        query_id = extract_query_id_from_strings(raw)
    if not query_id:
        return None
    start_time = normalize_impala_timestamp(
        first_present(raw, ("start_time", "startTime", "start_time_utc", "startTimeUtc", "start"))
    )
    end_time = normalize_impala_timestamp(
        first_present(raw, ("end_time", "endTime", "end_time_utc", "endTimeUtc", "end"))
    )
    raw_query_state = normalize_string(first_present(raw, ("query_state", "queryState", "state")))
    status = normalize_impala_query_status(raw, default_status=default_status)
    return CMQuerySummary(
        query_id=query_id,
        start_time=start_time,
        end_time=end_time,
        duration_ms=parse_duration_ms(raw),
        status=status,
        user=normalize_string(
            first_present(raw, ("user", "username", "effective_user", "effectiveUser"))
        ),
        pool=normalize_string(
            first_present(
                raw,
                (
                    "pool",
                    "pool_name",
                    "poolName",
                    "request_pool",
                    "requestPool",
                    "resource_pool",
                    "resourcePool",
                ),
            )
        ),
        query_type=normalize_string(
            first_present(
                raw, ("query_type", "queryType", "stmt_type", "stmtType", "statementType")
            )
        )
        or "QUERY",
        statement=normalize_statement(
            first_present(raw, ("stmt", "statement", "query", "sql", "stmt_text", "stmtText"))
        ),
        query_state="running" if status == "running" else raw_query_state or status,
        rows_produced=parse_nonnegative_count(
            first_present(raw, ("rows_produced", "rowsProduced", "rows_fetched", "rowsFetched"))
        ),
        bytes_read=parse_size_bytes(first_present(raw, ("bytes_read", "bytesRead"))),
        bytes_sent=parse_size_bytes(first_present(raw, ("bytes_sent", "bytesSent"))),
        # The coordinator's mem_usage is the sum of the per-node peaks, not the
        # largest of them: on a nine-node query it read 989.45 MB against a
        # per-node maximum of 138.85. So it is the aggregate, and the per-node
        # peak stays unknown because the listing does not carry it.
        memory_aggregate_peak=parse_size_bytes(
            first_present(raw, ("memory_aggregate_peak", "mem_usage", "memUsage"))
        ),
        admission_wait_ms=parse_admission_wait_ms(raw),
    )


def parse_nonnegative_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def parse_size_bytes(value: Any) -> int | None:
    """Parse a coordinator size such as '11.43 GB' into bytes.

    Impala prints binary units under decimal names, so GB here is 1024**3. The
    suspicion score compares against GiB and TiB, and reading these as decimal
    would understate every threshold by seven percent per unit step.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = SIZE_VALUE_RE.fullmatch(str(value).strip())
    if not match:
        return None
    amount = float(match.group("amount").replace(",", ""))
    unit = (match.group("unit") or "B").upper()
    return max(0, int(amount * SIZE_UNIT_MULTIPLIERS[unit]))


def parse_admission_wait_ms(raw: dict[str, Any]) -> int | None:
    for key in ("admission_wait_ms", "admissionWaitMs", "queued_duration", "queuedDuration"):
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
        parsed = parse_duration_text(str(value))
        if parsed is not None:
            return parsed
    return None


def first_present(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def normalize_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_impala_query_status(raw: dict[str, Any], *, default_status: str | None) -> str | None:
    if default_status == "running" or parse_bool_flag(
        first_present(
            raw,
            ("executing", "isExecuting", "in_flight", "inFlight", "running", "active"),
        )
    ):
        return "running"
    return (
        normalize_string(first_present(raw, ("status", "state", "query_state", "queryState")))
        or default_status
    )


def parse_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    text = normalize_string(value)
    if text is None:
        return False
    return text.lower() in {"1", "true", "t", "yes", "y", "on", "executing", "running", "active"}


def normalize_statement(value: Any) -> str | None:
    text = normalize_string(value)
    if text is None:
        return None
    return HTML_TAG_RE.sub(" ", text)


def extract_query_id_from_strings(raw: dict[str, Any]) -> str | None:
    for value in raw.values():
        if not isinstance(value, str):
            continue
        match = QUERY_ID_RE.search(value)
        if match:
            return match.group(0)
    return None


SIZE_UNIT_MULTIPLIERS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
    "PB": 1024**5,
}
SIZE_VALUE_RE = re.compile(
    r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<unit>[KMGTP]?B)?",
    re.IGNORECASE,
)


def parse_duration_ms(raw: dict[str, Any]) -> int | None:
    for key in (
        "duration_ms",
        "durationMillis",
        "duration_millis",
        "duration",
        "duration_sec",
        "durationSeconds",
    ):
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            multiplier = 1000 if "sec" in key.lower() else 1
            return max(0, int(float(value) * multiplier))
        parsed = parse_duration_text(str(value))
        if parsed is not None:
            return parsed
    return None


def parse_duration_text(value: str) -> int | None:
    """Read one Impala duration, which may be a run of terms.

    The daemon prints anything under a second as a single term, ``214.305ms``,
    and anything longer as a run: ``5s830ms``, ``1m23s``, ``1h2m``. Reading only
    the single-term form is worse than failing loudly, because the terms that do
    not parse are exactly the slow queries, and a candidate whose duration is
    unknown is dropped by the duration filter. A bare number keeps its previous
    meaning of milliseconds.
    """
    text = value.strip().lower()
    if not text:
        return None
    total_ms = 0.0
    position = 0
    while position < len(text):
        match = DURATION_TERM_RE.match(text, position)
        if match is None:
            return None
        unit = match.group(2)
        if unit is None:
            # A bare number means milliseconds only on its own. Inside a run it
            # is a term this parser does not understand, not a defaulted one.
            if position != 0 or match.end() != len(text):
                return None
            total_ms += float(match.group(1))
        else:
            total_ms += float(match.group(1)) * DURATION_UNIT_MS[unit]
        position = match.end()
    return max(0, int(total_ms))


def normalize_impala_timestamp(value: Any) -> str | None:
    text = normalize_string(value)
    if text is None:
        return None
    normalized = text.replace("T", " ").replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(normalized, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return format_cm_timestamp(parsed)
    return text
