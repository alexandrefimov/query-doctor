"""Scalar parsing and formatting helpers for analyzer facts."""

from __future__ import annotations

import math
import re
from typing import Any


NUMBER_PATTERN = r"\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?"
SIZE_PATTERN = rf"{NUMBER_PATTERN}\s*(?:KiB|MiB|GiB|TiB|KB|MB|GB|TB|B)"
SIZE_RE = re.compile(
    rf"(?P<value>{NUMBER_PATTERN})\s*(?P<unit>KiB|MiB|GiB|TiB|KB|MB|GB|TB|B)\b",
    flags=re.IGNORECASE,
)

# Deliberately case-sensitive for single-letter units.
# This avoids treating row suffix "6.37M actual rows" as "6.37 minutes".
DURATION_TOKEN_RE = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>ms|msec|milliseconds?|s|sec|seconds?|m|min|minutes?|h|hr|hours?)(?![A-Za-z])"
)

TABLE_DURATION_TOKEN_RE = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>ns|us|µs|ms|msec|milliseconds?|s|sec|seconds?|m|min|minutes?|h|hr|hours?)(?![A-Za-z])"
)


def parse_scaled_number(value: str) -> float | None:
    s = value.strip().replace(" ", "")
    m = re.fullmatch(rf"(?P<num>{NUMBER_PATTERN})(?P<suffix>[KMBT])?", s, flags=re.IGNORECASE)
    if not m:
        return None
    num = float(m.group("num").replace(",", ""))
    suffix = (m.group("suffix") or "").upper()
    scale = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[suffix]
    return num * scale


def parse_size_bytes(value: str) -> float | None:
    """Parse a size such as '11.43 GB' into bytes.

    Impala prints binary sizes under decimal names, so KB, MB, GB and TB are
    powers of 1024 here, as in the coordinator listing parser and in fmt_bytes.
    """
    m = SIZE_RE.search(value.strip())
    if not m:
        return None
    num = float(m.group("value").replace(",", ""))
    unit = m.group("unit").lower()
    scale = {
        "b": 1,
        "kb": 1024,
        "mb": 1024**2,
        "gb": 1024**3,
        "tb": 1024**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }[unit]
    return num * scale


def parse_rate_bytes_per_sec(value: str) -> float | None:
    size_match = SIZE_RE.search(value)
    if not size_match:
        return None
    bytes_value = parse_size_bytes(size_match.group(0))
    if bytes_value is None:
        return None
    tail = value[size_match.end() :]
    unit_match = re.search(r"/\s*(?P<unit>ms|msec|s|sec|second|seconds)\b", tail, re.IGNORECASE)
    if not unit_match:
        return None
    unit = unit_match.group("unit").lower()
    if unit in {"ms", "msec"}:
        return bytes_value * 1000
    return bytes_value


def parse_seconds_per_gib(value: str) -> float | None:
    duration_ms = extract_first_duration_ms(value)
    if duration_ms is not None:
        return duration_ms / 1000
    number = parse_scaled_number(value)
    return number


def numeric_context_value(context: dict[str, Any], field: str) -> float | None:
    value = context.get(field)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def duration_group_to_ms(matches: list[re.Match[str]]) -> float:
    total = 0.0
    for m in matches:
        value = float(m.group("value").replace(",", ""))
        unit = m.group("unit")
        if unit in {"ms", "msec", "millisecond", "milliseconds"}:
            total += value
        elif unit in {"s", "sec", "second", "seconds"}:
            total += value * 1000
        elif unit in {"m", "min", "minute", "minutes"}:
            total += value * 60 * 1000
        elif unit in {"h", "hr", "hour", "hours"}:
            total += value * 60 * 60 * 1000
    return total


def table_duration_to_ms(value: str) -> float | None:
    matches = list(TABLE_DURATION_TOKEN_RE.finditer(value.strip()))
    if not matches:
        return None

    total = 0.0
    for m in matches:
        num = float(m.group("value").replace(",", ""))
        unit = m.group("unit")
        if unit == "ns":
            total += num / 1_000_000
        elif unit in {"us", "µs"}:
            total += num / 1000
        elif unit in {"ms", "msec", "millisecond", "milliseconds"}:
            total += num
        elif unit in {"s", "sec", "second", "seconds"}:
            total += num * 1000
        elif unit in {"m", "min", "minute", "minutes"}:
            total += num * 60 * 1000
        elif unit in {"h", "hr", "hour", "hours"}:
            total += num * 60 * 60 * 1000
    return total


def extract_first_duration_ms(text: str) -> float | None:
    matches = list(DURATION_TOKEN_RE.finditer(text))
    if not matches:
        return None

    # Build the first contiguous duration group: "1m2s", "1m 2s", "52s385ms".
    group = [matches[0]]
    prev = matches[0]
    for m in matches[1:]:
        gap = text[prev.end() : m.start()]
        if gap.strip() == "":
            group.append(m)
            prev = m
            continue
        break
    return duration_group_to_ms(group)


def fmt_duration(ms: float | None) -> str:
    if ms is None:
        return "n/a"
    if ms < 1000:
        return f"{ms:.0f}ms"
    seconds = ms / 1000
    if seconds < 60:
        s = f"{seconds:.3f}".rstrip("0").rstrip(".")
        return f"{s}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.2f}m"
    hours = minutes / 60
    return f"{hours:.2f}h"


def fmt_bytes(value: float | None) -> str:
    if value is None:
        return "n/a"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    val = float(value)
    for unit in units:
        if abs(val) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{val:.0f} {unit}"
            return f"{val:.2f} {unit}"
        val /= 1024
    return f"{value:.0f} B"


def fmt_rows(value: float | None) -> str:
    if value is None:
        return "n/a"
    abs_v = abs(value)
    if abs_v >= 1e12:
        return f"{value / 1e12:.2f}T"
    if abs_v >= 1e9:
        return f"{value / 1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{value / 1e6:.2f}M"
    if abs_v >= 1e3:
        return f"{value / 1e3:.2f}K"
    return f"{value:.0f}"


def fmt_ratio(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    if value >= 100:
        return f"{value:.0f}x"
    if value >= 10:
        return f"{value:.1f}x"
    return f"{value:.2f}x"


def fmt_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{fmt_bytes(value)}/s"
