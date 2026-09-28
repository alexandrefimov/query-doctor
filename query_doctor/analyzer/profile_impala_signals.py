"""Raw-free counts of what Impala itself reports about skew and table statistics.

Impala 4.x marks the plan nodes whose instances diverge ("skew(s) found at"),
with the coefficient of variation per node ("Skew details"), and lists the
tables the planner found without statistics or with corrupt ones. These are
context facts: they keep only counts and node kinds, never table names, and
no finding or score reads them.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

SKEW_SUMMARY_RE = re.compile(r"^\s*skew\(s\) found at\s*:\s*(?P<value>.+?)\s*$", re.M)
SKEW_DETAILS_RE = re.compile(r"^\s*Skew details\s*:\s*(?P<value>.+?)\s*$", re.M)
COV_RE = re.compile(r"\bCoV\s*=\s*(?P<value>\d+(?:\.\d+)?)")
NODE_KIND_RE = re.compile(r"^(?P<kind>[A-Za-z_]+)")
MISSING_STATS_RE = re.compile(r"^\s*Tables Missing Stats\s*:\s*(?P<value>.+?)\s*$", re.M)
CORRUPT_STATS_RE = re.compile(r"^\s*Tables With Corrupt Table Stats\s*:\s*(?P<value>.+?)\s*$", re.M)


def build_profile_impala_signal_facts(text: str) -> dict[str, Any]:
    kinds: Counter[str] = Counter()
    for match in SKEW_SUMMARY_RE.finditer(text):
        for entry in match.group("value").split(","):
            kind = NODE_KIND_RE.match(entry.strip())
            if kind:
                kinds[kind.group("kind")] += 1

    covs = [
        float(cov.group("value"))
        for details in SKEW_DETAILS_RE.finditer(text)
        for cov in COV_RE.finditer(details.group("value"))
    ]
    missing = _list_count(MISSING_STATS_RE, text)
    corrupt = _list_count(CORRUPT_STATS_RE, text)

    observed = bool(kinds or covs or missing or corrupt)
    return {
        "status": "observed" if observed else ("not_observed" if text.strip() else "unknown"),
        "skew_node_count": sum(kinds.values()),
        "skew_node_kinds": dict(sorted(kinds.items())),
        "max_skew_cov": max(covs) if covs else None,
        "tables_missing_stats_count": missing,
        "tables_with_corrupt_stats_count": corrupt,
    }


def _list_count(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    if not match:
        return 0
    return len([item for item in match.group("value").split(",") if item.strip()])
