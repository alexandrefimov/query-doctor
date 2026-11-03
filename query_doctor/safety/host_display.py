"""Explicit deployment opt-in for host names on diagnostic display surfaces.

This policy does not change storage keys, leases, log sanitization, or credentials.
Collection must separately retain hosts with ``redact_hosts: false``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


HOST_DISPLAY_ENV = "QUERY_DOCTOR_HOST_DISPLAY"


def host_names_visible(env: Mapping[str, str] | None = None) -> bool:
    """Unknown or missing values retain the default host aliases."""
    return (os.environ if env is None else env).get(HOST_DISPLAY_ENV) == "names"
