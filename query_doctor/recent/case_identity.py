"""Source-bound opaque identities for retained history case links."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping


def history_case_ref(payload: Mapping[str, object]) -> str:
    if not payload.get("query_id"):
        return ""
    identity = json.dumps(
        [
            str(payload.get(field) or "")
            for field in ("engine", "source_kind", "source_key", "query_id")
        ],
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).digest()[:16]
    return f"case-{int.from_bytes(digest, 'big'):03d}"


def valid_history_case_ref(value: str) -> bool:
    return bool(re.fullmatch(r"case-[0-9]{3,39}", value)) and 0 <= int(value[5:]) < 2**128
