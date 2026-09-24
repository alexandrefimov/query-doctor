"""Raw-free statement fingerprint and error class for Recent summaries."""

from __future__ import annotations

import hashlib
import re


STATEMENT_FINGERPRINT_VERSION = 1
STATEMENT_FINGERPRINT_HEX_LENGTH = 24
FULL_STATEMENT_FINGERPRINT_PREFIX = "sf_"
PREFIX_STATEMENT_FINGERPRINT_PREFIX = "sfp_"
# The Impala daemon listing cuts statements at query_stmt_size and appends
# this marker, so a fingerprint over such text covers only the statement start.
TRUNCATED_STATEMENT_MARKER = "..."

# One pass so that comment markers inside strings and quotes inside comments
# are consumed by whichever token starts first. Unterminated strings and block
# comments run to the end, which is what a truncated statement looks like.
_STATEMENT_TOKEN_RE = re.compile(
    r"""
    (?P<string>'(?:[^'\\]|\\.)*(?:'|\Z)|"(?:[^"\\]|\\.)*(?:"|\Z))
    |(?P<comment>--[^\n]*|/\*.*?(?:\*/|\Z))
    |(?P<identifier>`(?:[^`]|``)*(?:`|\Z)|[A-Za-z_][A-Za-z0-9_$]*)
    |(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
    """,
    re.DOTALL | re.VERBOSE,
)
_WHITESPACE_RE = re.compile(r"\s+")

_EXCEPTION_CLASS_RE = re.compile(
    r"^(?:[A-Za-z_$][A-Za-z0-9_$]*\.)*"
    r"(?P<name>[A-Z][A-Za-z0-9]{0,62}(?:Exception|Error))\s*(?::|$)"
)
ERROR_CLASS_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,62}(?:Exception|Error)$")
ERROR_CLASS_MESSAGE_PREFIXES = (
    ("memory limit exceeded", "memory_limit_exceeded"),
    ("cancelled", "cancelled"),
    ("rejected query from pool", "admission_rejected"),
    ("admission for query exceeded timeout", "admission_timeout"),
)
ERROR_CLASS_MESSAGE_FRAGMENTS = (
    ("expired due to execution time limit", "execution_time_limit"),
    ("expired due to client inactivity", "client_inactivity_timeout"),
)
ERROR_CLASS_OTHER = "other"
ERROR_CLASS_LABELS = frozenset(
    [label for _, label in ERROR_CLASS_MESSAGE_PREFIXES]
    + [label for _, label in ERROR_CLASS_MESSAGE_FRAGMENTS]
    + [ERROR_CLASS_OTHER]
)
# A bare lifecycle word says a query failed or finished, not why; the direct
# Impala listing carries only these, and the profile worker fills the class.
_STATE_WORDS = frozenset(
    {
        "ok",
        "finished",
        "exception",
        "error",
        "failed",
        "failure",
        "running",
        "created",
        "initialized",
        "pending",
        "compiled",
        "unknown",
    }
)


def statement_fingerprint(statement: str | None) -> str | None:
    """Return a raw-free digest of the normalized statement, or None.

    Normalization drops comments, replaces string and number literals with
    ``?``, case-folds and collapses whitespace, so one scheduled statement
    with different parameters shares a fingerprint. A statement the source
    truncated gets the ``sfp_`` prefix because only its start was hashed.
    """

    if not statement:
        return None
    text = str(statement).strip()
    truncated = text.endswith(TRUNCATED_STATEMENT_MARKER)
    if truncated:
        text = text[: -len(TRUNCATED_STATEMENT_MARKER)]
    normalized = normalize_statement_for_fingerprint(text)
    if not normalized:
        return None
    canonical = f"v{STATEMENT_FINGERPRINT_VERSION}\n{normalized}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    prefix = PREFIX_STATEMENT_FINGERPRINT_PREFIX if truncated else FULL_STATEMENT_FINGERPRINT_PREFIX
    return f"{prefix}{digest[:STATEMENT_FINGERPRINT_HEX_LENGTH]}"


def normalize_statement_for_fingerprint(statement: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group("string") is not None or match.group("number") is not None:
            return "?"
        if match.group("comment") is not None:
            return " "
        return match.group(0)

    text = _STATEMENT_TOKEN_RE.sub(replace, statement)
    text = _WHITESPACE_RE.sub(" ", text.casefold()).strip()
    return text.rstrip("; ").strip()


def error_class_from_status(status: str | None) -> str | None:
    """Return a raw-free failure label from a query status text, or None.

    Only the leading exception class name or a fixed label is kept; message
    text, identifiers, paths and SQL never pass through.
    """

    text = str(status or "").strip()
    if not text:
        return None
    match = _EXCEPTION_CLASS_RE.match(text)
    if match:
        return match.group("name")
    lowered = text.lower()
    for prefix, label in ERROR_CLASS_MESSAGE_PREFIXES:
        if lowered.startswith(prefix):
            return label
    for fragment, label in ERROR_CLASS_MESSAGE_FRAGMENTS:
        if fragment in lowered:
            return label
    if lowered.replace("-", "_") in _STATE_WORDS:
        return None
    return ERROR_CLASS_OTHER


def safe_error_class(value: object) -> str | None:
    text = str(value or "")
    if ERROR_CLASS_NAME_RE.fullmatch(text) or text in ERROR_CLASS_LABELS:
        return text
    return None
