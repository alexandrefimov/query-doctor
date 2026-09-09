"""Browser display redaction helpers for Query Doctor web UI."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from query_doctor.cli import collect_cm_profiles as cm_collector
from query_doctor.safety.artifact_names import RAW_ARTIFACT_FILENAMES
from query_doctor.safety import redaction as shared_redaction


LOCAL_PATH_REPLACEMENT = "<local path hidden>"
HIDDEN_FIELD_REPLACEMENT = "[hidden field]"
RAW_PROFILE_REPLACEMENT = "[raw profile hidden]"
RAW_METADATA_REPLACEMENT = "[metadata statement hidden]"
RAW_OUTPUT_REPLACEMENT = "[subprocess output hidden]"
RAW_ARTIFACT_REPLACEMENT = "[artifact name hidden]"
MODEL_REPLACEMENT = "[model setting hidden]"
SQL_SNIPPET_REPLACEMENT = "[SQL hidden]"
SVG_NAMESPACE_TOKENS = (
    "http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg",
    "https%3A%2F%2Fwww.w3.org%2F2000%2Fsvg",
)
STATIC_ASSET_PATH_RE = re.compile(r"/static/[A-Za-z0-9_.-]+\.(?:css|js)")

FIELD_NAME_TOKENS = (
    "case_dir",
    "CM_PASSWORD",
    "CM_TOKEN",
    "KRB5CCNAME",
    "metadata_coordinator",
    "metadata_auth",
    "metadata_path",
)

RAW_ARTIFACT_FILENAME_TOKENS = RAW_ARTIFACT_FILENAMES
RAW_METADATA_STATEMENT_TOKENS = (
    "SHOW CREATE TABLE",
    "SHOW TABLE STATS",
    "SHOW COLUMN STATS",
    "DESCRIBE FORMATTED",
    "SHOW PARTITIONS",
)

MODEL_NAME_RE = re.compile(
    r"\b(?:"
    r"qwen[\w:.-]*|"
    r"gpt-oss[\w:.-]*|"
    r"gpt[_-]lst[\w:.-]*|"
    r"gpt[\w:.-]*|"
    r"codestral[\w:.-]*|"
    r"deepseek[\w:.-]*|"
    r"internal[_-]model[\w:.-]*|"
    r"sqlcoder[\w:.-]*|"
    r"mistral[\w:.-]*|"
    r"magistral[\w:.-]*|"
    r"devstral[\w:.-]*|"
    r"codellama[\w:.-]*|"
    r"llama[\w:.-]*|"
    r"ollama"
    r")\b",
    re.IGNORECASE,
)
# A Kerberos principal is not an email address, but it has the same shape, so
# the email rule turned every principal into "<email>" and the analyst lost the
# one thing the User column answers. Keep the primary, drop the realm: an
# uppercase dotted realm is what separates "n_ivanov@LESTA.HADOOP" from
# "someone@example.com".
KERBEROS_PRINCIPAL_RE = re.compile(
    r"\b([A-Za-z0-9._-]+)(/[A-Za-z0-9._-]+)?@((?:[A-Z0-9-]+\.)+[A-Z]{2,})\b"
)


BROWSER_USER_LABEL_RE = re.compile(
    r"\b(User|Username|Effective User|Connected User|Delegated User)([ \t]*[:=][ \t]*)"
    r"([^ \t\r\n,;]+)",
    re.IGNORECASE,
)
BROWSER_HOST_LABEL_RE = re.compile(
    r"\b(Host|Hostname|Coordinator|Coordinator Host|Daemon|Impala Daemon|Impalad|Server)"
    r"([ \t]*[:=][ \t]*)([^ \t\r\n,;]+)",
    re.IGNORECASE,
)
SAFE_FAVICON_LINK_RE = re.compile(
    r'<link rel="icon" type="image/svg\+xml" '
    r'href="data:image/svg\+xml;base64,[A-Za-z0-9+/=]{1,2048}">'
)
SAFE_PUBLIC_PROJECT_URL_RE = re.compile(
    r"https://github\.com/alexandrefimov/Query-Doctor"
    r"(?:/(?:blob/main/)?[A-Za-z0-9_./#%-]+)?"
    r"|https://pypi\.org/project/query-doctor/"
)

MAX_SHARED_BROWSER_DISPLAY_REDACTIONS = 4096
MAX_SHARED_BROWSER_DISPLAY_INPUT_CHARS = 2048
_SHARED_BROWSER_DISPLAY_REDACTIONS: ContextVar[dict[tuple[object, ...], str] | None] = ContextVar(
    "shared_browser_display_redactions", default=None
)


@contextmanager
def shared_browser_display_redactions() -> Iterator[None]:
    """Reuse exact browser projections within one request only."""

    token = _SHARED_BROWSER_DISPLAY_REDACTIONS.set({})
    try:
        yield
    finally:
        _SHARED_BROWSER_DISPLAY_REDACTIONS.reset(token)


def redact_browser_display_text(
    value: Any,
    *,
    env: Mapping[str, str] | None = None,
    redact_field_names: bool = False,
    redact_artifact_markers: bool = False,
    redact_model_names: bool = False,
    redact_sql_snippets: bool = False,
    redact_infrastructure: bool = False,
    max_chars: int | None = None,
) -> str:
    # Central browser-visible boundary: opt into narrower redaction flags per surface.
    text = str(value)
    cache = _SHARED_BROWSER_DISPLAY_REDACTIONS.get()
    cache_key: tuple[object, ...] | None = None
    if cache is not None and env is None and len(text) <= MAX_SHARED_BROWSER_DISPLAY_INPUT_CHARS:
        cache_key = (
            text,
            redact_field_names,
            redact_artifact_markers,
            redact_model_names,
            redact_sql_snippets,
            redact_infrastructure,
            max_chars,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    text = redact_credentials_for_display(text, env=env)
    text = redact_local_paths_for_display(text)
    if redact_infrastructure:
        text = redact_infrastructure_identifiers_for_display(text)
    if redact_field_names:
        text = redact_field_names_for_display(text)
    if redact_sql_snippets:
        text = redact_sql_snippets_for_display(text)
    if redact_artifact_markers:
        text = redact_raw_artifact_markers_for_display(text)
    if redact_model_names:
        text = redact_model_names_for_display(text)
    redacted = text[:max_chars] if max_chars is not None else text
    if (
        cache_key is not None
        and cache is not None
        and len(cache) < MAX_SHARED_BROWSER_DISPLAY_REDACTIONS
    ):
        cache[cache_key] = redacted
    return redacted


def redact_credentials_for_display(text: str, *, env: Mapping[str, str] | None = None) -> str:
    effective_env = os.environ if env is None else env
    for secret in (effective_env.get("CM_PASSWORD"), effective_env.get("CM_TOKEN")):
        if secret:
            text = text.replace(secret, "<secret>")
    text = cm_collector.AUTH_HEADER_RE.sub(r"\1<redacted>", text)
    text = cm_collector.BEARER_BASIC_RE.sub(r"\1 <redacted>", text)
    text = cm_collector.URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = cm_collector.SECRET_VALUE_RE.sub(r"\1\2\3<redacted>\5", text)
    return text


def redact_local_paths_for_display(text: str) -> str:
    text = re.sub(r"(?<![\w/])(?:/private)?/tmp/[^\s<>'\"]+", LOCAL_PATH_REPLACEMENT, text)
    text = re.sub(r"(?<![\w/])/Users/[^\s<>'\"]+", LOCAL_PATH_REPLACEMENT, text)
    text = re.sub(r"(?<![\w/])/var/folders/[^\s<>'\"]+", LOCAL_PATH_REPLACEMENT, text)
    text = re.sub(r"(?<![\w/])[A-Za-z]:\\[^\s<>'\"]+", LOCAL_PATH_REPLACEMENT, text)
    return text


def redact_infrastructure_identifiers_for_display(text: str) -> str:
    host_redactor = shared_redaction.HostAliasRedactor()
    text, protected_fragments = protect_first_party_browser_chrome(text)

    def replace_host_label(match: re.Match[str]) -> str:
        alias = host_redactor.redact_host_value(match.group(3))
        return f"{match.group(1)}{match.group(2)}{alias}"

    text = KERBEROS_PRINCIPAL_RE.sub(r"\1", text)
    text = shared_redaction.EMAIL_RE.sub("<email>", text)
    text = shared_redaction.USER_FIELD_RE.sub(r"\1<user>", text)
    text = BROWSER_USER_LABEL_RE.sub(r"\1\2<user>", text)
    text = BROWSER_HOST_LABEL_RE.sub(replace_host_label, text)
    text = shared_redaction.USER_KV_RE.sub(r"\1\2<user>", text)
    text = shared_redaction.redact_host_identifiers(text, host_redactor)
    return restore_first_party_browser_chrome(text, protected_fragments)


def protect_first_party_browser_chrome(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def protect(fragment: str) -> str:
        token = f"QXSAFEZ{len(protected):04d}Z"
        protected[token] = fragment
        return token

    text = SAFE_FAVICON_LINK_RE.sub(lambda match: protect(match.group(0)), text)
    text = SAFE_PUBLIC_PROJECT_URL_RE.sub(lambda match: protect(match.group(0)), text)
    for token in SVG_NAMESPACE_TOKENS:
        text = text.replace(token, protect(token))
    text = STATIC_ASSET_PATH_RE.sub(lambda match: protect(match.group(0)), text)
    return text, protected


def restore_first_party_browser_chrome(text: str, protected_fragments: Mapping[str, str]) -> str:
    for token, fragment in protected_fragments.items():
        text = text.replace(token, fragment)
    return text


def redact_field_names_for_display(text: str) -> str:
    for token in FIELD_NAME_TOKENS:
        text = text.replace(token, HIDDEN_FIELD_REPLACEMENT)
    return text


def redact_raw_artifact_markers_for_display(text: str) -> str:
    for token in ("BEGIN PROFILE", "Query Timeline"):
        text = text.replace(token, RAW_PROFILE_REPLACEMENT)
    for token in RAW_ARTIFACT_FILENAME_TOKENS:
        text = text.replace(token, RAW_ARTIFACT_REPLACEMENT)
    for token in RAW_METADATA_STATEMENT_TOKENS:
        text = text.replace(token, RAW_METADATA_REPLACEMENT)
    text = text.replace("raw stdout", RAW_OUTPUT_REPLACEMENT)
    text = text.replace("raw stderr", RAW_OUTPUT_REPLACEMENT)
    return text


def redact_model_names_for_display(text: str) -> str:
    return MODEL_NAME_RE.sub(MODEL_REPLACEMENT, text)


def redact_sql_snippets_for_display(text: str) -> str:
    # Intentionally coarse and fail-closed; hiding extra text is safer than echoing a query fragment.
    identifier_part = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$-]*)"
    identifier = rf"{identifier_part}(?:[ \t]*\.[ \t]*{identifier_part})*"
    metadata_statement_pattern = (
        r"\b(?:"
        r"SHOW\s+(?:CREATE\s+TABLE|TABLE\s+STATS|COLUMN\s+STATS|PARTITIONS)\b|"
        r"DESCRIBE\s+FORMATTED\b"
        r")"
        rf"(?:[ \t]+{identifier}){{0,3}}"
    )
    statement_pattern = (
        r"\b(?:"
        r"SELECT\b(?=[^.;\n<]{0,120}\bFROM\b)|"
        r"INSERT\s+INTO\b|"
        r"UPSERT\s+INTO\b|"
        r"DELETE\s+FROM\b|"
        r"CREATE\s+(?:TABLE|VIEW)\b|"
        r"DROP\s+(?:TABLE|VIEW)\b|"
        r"ALTER\s+(?:TABLE|VIEW)\b|"
        r"COMPUTE\s+STATS\b|"
        r"INVALIDATE\s+METADATA\b|"
        r"REFRESH\b(?=\s+[A-Za-z_`\"]|[^.;\n<]{0,40}\.)"
        r")"
        r"[^;\n<]{0,220}"
    )
    cte_pattern = (
        r"\bWITH\s+(?:[A-Za-z_][\w$]*|\"[^\"]+\"|`[^`]+`)\s+AS\b"
        r"[^.;\n<]{0,220}"
    )
    text = re.sub(
        metadata_statement_pattern,
        RAW_METADATA_REPLACEMENT,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(statement_pattern, SQL_SNIPPET_REPLACEMENT, text, flags=re.IGNORECASE)
    return re.sub(cte_pattern, SQL_SNIPPET_REPLACEMENT, text, flags=re.IGNORECASE)
