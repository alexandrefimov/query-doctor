"""Runtime counter parsing helpers for analyzer facts."""

from __future__ import annotations

import re
from typing import Any

from query_doctor.analyzer.scalars import extract_first_duration_ms, fmt_duration, parse_size_bytes


TOTAL_COUNTER_ALIASES = {
    "TotalBytesRead": ["TotalBytesRead", "Total Bytes Read"],
    "TotalBytesSent": ["TotalBytesSent", "Total Bytes Sent", "Bytes Sent"],
    "TotalTime": ["TotalTime", "Total Time"],
}
QUERY_TIMELINE_HEADER_RE = re.compile(
    r"^\s*Query\s+Timeline\s*:?\s*(?P<value>[^\n\r]*)$",
    flags=re.IGNORECASE,
)
QUERY_TIMELINE_EVENT_RE = re.compile(
    r"^\s*-\s*[^:\n\r]{1,160}:\s*(?P<value>[^\n\r]+)$",
    flags=re.IGNORECASE,
)
RUNTIME_TIME_COUNTER_RE = re.compile(
    r"^\s*-\s*(?P<name>[A-Za-z][A-Za-z0-9_./* -]*(?:Time|WallClockTime|CpuTime|CPUTime))"
    r"\s*[:=]\s*(?P<value>[^\n\r]+)",
    flags=re.IGNORECASE,
)


def line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def normalize_metric_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.lower())


def extract_total_counter(text: str, canonical_name: str) -> dict[str, Any] | None:
    aliases = TOTAL_COUNTER_ALIASES[canonical_name]
    alias_re = "|".join(re.escape(x) for x in aliases)
    # The lookbehind keeps TotalTime from matching InactiveTotalTime.
    rx = re.compile(rf"(?<![A-Za-z])(?:{alias_re})\s*[:=]\s*(?P<value>[^\n\r,;|]+)", re.IGNORECASE)
    m = rx.search(text)
    if not m:
        return None
    raw = m.group("value").strip().rstrip(".")
    if "Bytes" in canonical_name:
        b = parse_size_bytes(raw)
        return {"raw": raw, "bytes": b}
    ms = extract_first_duration_ms(raw)
    return {"raw": raw, "ms": ms}


def extract_query_timeline_duration_ms(text: str) -> float | None:
    best_ms: float | None = None
    in_timeline = False
    timeline_indent = 0

    for line in text.splitlines():
        stripped = line.strip()
        header_match = QUERY_TIMELINE_HEADER_RE.match(line)
        if header_match:
            in_timeline = True
            timeline_indent = line_indent(line)
            header_ms = extract_first_duration_ms(header_match.group("value"))
            if header_ms is not None and header_ms > 0:
                best_ms = max(best_ms or 0, header_ms)
            continue

        if not in_timeline:
            continue
        if not stripped:
            in_timeline = False
            continue
        if line_indent(line) <= timeline_indent and not stripped.startswith("-"):
            in_timeline = False
            continue

        event_match = QUERY_TIMELINE_EVENT_RE.match(line)
        if not event_match:
            continue
        event_ms = extract_first_duration_ms(event_match.group("value"))
        if event_ms is not None and event_ms > 0:
            best_ms = max(best_ms or 0, event_ms)

    return best_ms


def runtime_counter_family(name: str) -> str | None:
    normalized = normalize_metric_key(name)
    if "codegen" in normalized:
        return "codegen"
    if "network" in normalized:
        return "network"
    if "thread" in normalized and ("wallclock" in normalized or normalized.endswith("time")):
        return "thread_wall_clock"
    if "wait" in normalized:
        return "wait"
    if "cputime" in normalized or normalized.endswith("usertime") or normalized.endswith("systime"):
        return "cpu"
    return None


def build_runtime_counter_context(text: str) -> dict[str, Any]:
    families: dict[str, dict[str, Any]] = {}
    network_related: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = RUNTIME_TIME_COUNTER_RE.match(line)
        if not match:
            continue
        counter_name = match.group("name").strip()
        family = runtime_counter_family(match.group("name"))
        if family is None:
            continue
        duration_ms = extract_first_duration_ms(match.group("value"))
        if duration_ms is None or duration_ms <= 0:
            continue

        item = families.setdefault(
            family,
            {
                "count": 0,
                "max_ms": None,
                "max_human": "unknown",
                "max_counter": None,
            },
        )
        item["count"] += 1
        if item["max_ms"] is None or duration_ms > item["max_ms"]:
            item["max_ms"] = duration_ms
            item["max_human"] = fmt_duration(duration_ms)
            item["max_counter"] = counter_name
        normalized_name = normalize_metric_key(counter_name)
        if (
            family == "network"
            or "datawait" in normalized_name
            or "firstbatchwait" in normalized_name
            or "rowbatchqueue" in normalized_name
        ):
            network_related.append(
                {
                    "counter": counter_name,
                    "duration_ms": duration_ms,
                    "duration_human": fmt_duration(duration_ms),
                }
            )

    network_related = sorted(
        network_related,
        key=lambda item: float(item.get("duration_ms") or 0),
        reverse=True,
    )[:5]

    return {
        "families": dict(sorted(families.items())),
        "network_related_counters": network_related,
        "guardrail": (
            "Runtime thread/codegen/wait/CPU counters are context only; they are not used "
            "as operator elapsed time unless a separate deterministic finding says so."
        ),
    }
