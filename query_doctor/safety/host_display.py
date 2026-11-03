"""Explicit deployment opt-in for host names on diagnostic display surfaces.

This policy does not change storage keys, leases, log sanitization, or credentials.
Collection must separately retain hosts with ``redact_hosts: false``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from query_doctor.safety.redaction import sanitize_identifier_for_log, sanitize_text_for_log


HOST_DISPLAY_ENV = "QUERY_DOCTOR_HOST_DISPLAY"


def host_names_visible(env: Mapping[str, str] | None = None) -> bool:
    """Unknown or missing values retain the default host aliases."""
    return (os.environ if env is None else env).get(HOST_DISPLAY_ENV) == "names"


def sanitize_diagnostic_display_text(value: object) -> str:
    """Keep opted-in host names in bounded diagnostic payload text, never keys.

    Credential masking stays active. Storage/source/lease identities must keep
    using their existing unconditional sanitizers instead of this projection.
    """
    text = str(value or "")
    if host_names_visible():
        return sanitize_identifier_for_log(text)
    return sanitize_text_for_log(text)
