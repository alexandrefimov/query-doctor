"""Raw-free statement identity from a complete, query-bound native profile section."""

from __future__ import annotations

import re

from query_doctor.cm.profile_parsing import (
    PROFILE_QUERY_ID_RE,
    profile_details_text,
)
from query_doctor.recent.statement_identity import statement_fingerprint


PROFILE_STATEMENT_SOURCE = "impala_profile_sql_statement_v1"
_FULL_FINGERPRINT = re.compile(r"sf_[0-9a-f]{24}")
_SQL_START = re.compile(r"(?m)^(?P<indent>[ \t]*)Sql Statement:[ \t]*(?P<sql>[^\n]*)")


def valid_full_statement_fingerprint(value: object) -> str | None:
    return value if isinstance(value, str) and _FULL_FINGERPRINT.fullmatch(value) else None


def profile_statement_fingerprint(profile_text: str, *, query_id: str) -> str | None:
    """Hash before identifier redaction; incomplete or ambiguous input stays unknown.

    Native Impala writes the full Sql Statement info string immediately before
    Coordinator. Require that same-indent boundary rather than treating EOF or
    any SQL-looking field label as proof of a complete statement.
    """
    text = profile_details_text(profile_text)
    identities = list(PROFILE_QUERY_ID_RE.finditer(text))
    if len(identities) != 1 or identities[0]["query_id"] != query_id:
        return None
    starts = list(_SQL_START.finditer(text))
    if len(starts) != 1:
        return None
    start = starts[0]
    ends = list(
        re.finditer(
            r"(?m)^" + re.escape(start["indent"]) + r"Coordinator:[ \t]*[^\n]*$",
            text[start.end() :],
        )
    )
    if len(ends) != 1:
        return None
    sql = (start["sql"] + text[start.end() : start.end() + ends[0].start()]).strip()
    return valid_full_statement_fingerprint(statement_fingerprint(sql))
