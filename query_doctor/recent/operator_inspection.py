"""Bounded raw-free exchange inspection locations, scoped to one analysis."""

from __future__ import annotations

from collections import Counter
import hashlib
import math
import re

MAX_EXCHANGES = 3


def _identity(op: object) -> str | None:
    value = op.get("operator_id") if isinstance(op, dict) else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        return str(int(value))
    return None


def _number(value: object) -> int | float | None:
    if type(value) not in (int, float) or not 0 <= value <= 2**63 - 1:
        return None
    return value if math.isfinite(value) else None


def exchange_inspection_summary(analysis: object) -> dict[str, object]:
    """Retain observed locations, never inferred edges, row flow or semantics."""
    source = analysis.get("operators") if isinstance(analysis, dict) else None
    result: dict[str, object] = {
        "schema_version": 1,
        "status": "unavailable",
        "coverage": "analyzed_operators_only",
        "operators": [],
        "operators_truncated": False,
        "identity_incomplete": False,
        "query_bytes_sent": None,
    }
    if not isinstance(source, list):
        return result
    identities = Counter(_identity(op) for op in source)
    entries = []
    for op in source:
        if not isinstance(op, dict) or op.get("operator_name") != "EXCHANGE":
            continue
        identity = _identity(op)
        if identity is None or identities[identity] != 1:
            result["identity_incomplete"] = True
            continue
        row_supported = op.get("row_conclusion_state") == "supported"
        rows = _number(op.get("actual_rows")) if row_supported else None
        entries.append(
            {
                "operator_ref": "operator-" + hashlib.sha256(identity.encode()).hexdigest(),
                "kind": "exchange",
                "time_ms": _number(op.get("time_ms")),
                "observed_rows": rows,
                "row_state": "available" if rows is not None else "unavailable",
            }
        )
    entries.sort(key=lambda op: (-(op["time_ms"] or 0), op["operator_ref"]))
    result["operators"] = entries[:MAX_EXCHANGES]
    result["operators_truncated"] = len(entries) > MAX_EXCHANGES
    result["status"] = (
        "available"
        if entries
        else "unavailable"
        if result["identity_incomplete"]
        else "not_observed"
    )
    totals = analysis.get("totals")
    sent = totals.get("TotalBytesSent") if isinstance(totals, dict) else None
    if isinstance(sent, dict):
        result["query_bytes_sent"] = _number(sent.get("bytes"))
    return result
