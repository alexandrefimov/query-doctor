"""Redaction helpers for safe logs, collected profiles, and metadata."""

from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol


class SupportsSecretValues(Protocol):
    def secret_values(self) -> tuple[str, ...]:
        """Return secret values that must be redacted from diagnostics."""


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
BRACKETED_IPV6_RE = re.compile(r"\[(?P<ip>[0-9A-Fa-f:]+)\]")
URL_CREDENTIAL_RE = re.compile(r"\b(https?://)([^/\s:@]+):([^@\s/]+)@", re.IGNORECASE)
URL_HOST_RE = re.compile(
    r"\b(https?://)(<redacted>@)?([^/\s:?#\\[]+)(:\d+)?",
    re.IGNORECASE,
)
AUTH_HEADER_RE = re.compile(
    r"(?im)^([ \t]*(?:Authorization|Proxy-Authorization)[ \t]*:[ \t]*(?:Bearer|Basic)[ \t]+)\S+"
)
COOKIE_HEADER_RE = re.compile(r"(?im)^([ \t]*(?:Cookie|Set-Cookie)[ \t]*:[ \t]*).+$")
BEARER_BASIC_RE = re.compile(r"\b(Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]{8,}\b")
SECRET_VALUE_RE = re.compile(
    r"\b("
    r"password|passwd|pwd|token|secret|cookie|"
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?(?:token|key|secret|credential)?|credentials?|passphrase|private[ _-]?key"
    r")\b"
    r"([ \t]*[:=][ \t]*)([\"']?)([^\"'\s,;]+)([\"']?)",
    re.IGNORECASE,
)
USER_FIELD_RE = re.compile(
    r"(?im)^([ \t]*(?:User|Username|Effective User|Connected User|Delegated User)"
    r"[ \t]*[:=][ \t]*)([^ \t\r\n]+)"
)
POOL_FIELD_RE = re.compile(
    r"(?im)^([ \t]*(?:Pool|Request Pool|Resource Pool|Admission Pool)"
    r"[ \t]*[:=][ \t]*)([^ \t\r\n]+)"
)
USER_KV_RE = re.compile(
    r"\b(user|username)([ \t]*=[ \t]*)([A-Za-z][A-Za-z0-9_.-]*)\b", re.IGNORECASE
)
LOCAL_PATH_RE = re.compile(
    r"(?<![\w/])(?:/private)?/tmp/[^\s<>'\"]+|"
    r"(?<![\w/])/Users/[^\s<>'\"]+|"
    r"(?<![\w/])/var/folders/[^\s<>'\"]+|"
    r"(?<![\w/])[A-Za-z]:\\[^\s<>'\"]+"
)
HOST_FIELD_RE = re.compile(
    r"(?im)^([ \t]*(?:Host|Hostname|Coordinator|Coordinator Host|Daemon|Impala Daemon|"
    r"Impalad|Server)[ \t]*[:=][ \t]*)([^ \t\r\n]+)"
)
HOST_ASSIGNMENT_RE = re.compile(
    r"\b(?P<key>host|hostname|executor|backend)(?P<sep>[ \t]*=[ \t]*)(?P<value>[^ \t\r\n,)]+)",
    re.IGNORECASE,
)
HOST_ALIAS_RE = re.compile(r"^host_\d{2,}$", re.IGNORECASE)
PUBLIC_NAMESPACE_HOSTS = frozenset({"www.w3.org"})
PUBLIC_OR_INFRA_DOMAIN_SUFFIXES = frozenset(
    {
        "biz",
        "cloud",
        "cluster",
        "com",
        "corp",
        "dev",
        "example",
        "invalid",
        "io",
        "local",
        "localhost",
        "net",
        "org",
        "prod",
        "test",
    }
)
SANITIZED_TEXT_CACHE_SIZE = 8192
SAFE_FILE_EXTENSION_SUFFIXES = frozenset(
    {
        "crt",
        "csv",
        "json",
        "key",
        "log",
        "md",
        "out",
        "pem",
        "py",
        "sql",
        "txt",
        "yaml",
        "yml",
    }
)
SQL_DB_TABLE_RE = re.compile(
    r"\b(FROM|JOIN|TABLE|DESCRIBE)\s+`?[A-Za-z_][A-Za-z0-9_$]*`?"
    r"\s*\.\s*`?[A-Za-z_][A-Za-z0-9_$]*`?",
    re.IGNORECASE,
)
SQL_TABLE_RE = re.compile(
    r"\b(FROM|JOIN)\s+`?[A-Za-z_][A-Za-z0-9_$]*`?",
    re.IGNORECASE,
)
IPV6_CANDIDATE_CHARS = frozenset("0123456789abcdefABCDEF:.")
IPV6_BOUNDARY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
HOST_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")
HOST_TOKEN_BOUNDARY_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
)
HOSTLIKE_BARE_KEYWORDS = (
    "host",
    "node",
    "worker",
    "server",
    "impala",
    "impalad",
    "coordinator",
    "backend",
    "executor",
    "daemon",
)
SHORT_ROLE_PREFIXES = ("dn", "nn", "cm", "db")

PRESERVED_METADATA_KEYS = {
    "query_id",
    "start_time",
    "end_time",
    "duration_ms",
    "duration_sec",
    "status",
    "query_type",
    "impala_daemon_product",
    "impala_daemon_version",
    "impala_daemon_version_label",
    "impala_daemon_build_type",
    "impala_daemon_server_mode",
    "impala_daemon_local_catalog_mode",
}
USER_METADATA_KEYS = {"user", "username", "effective_user", "connected_user", "delegated_user"}
POOL_METADATA_KEYS = {"pool", "admission_pool", "queue"}
SECRET_METADATA_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "auth",
    "credential",
    "credentials",
    "passphrase",
    "private_key",
    "privatekey",
    "private-key",
    "api_key",
    "apikey",
    "authorization",
)
HOST_METADATA_KEY_PARTS = ("host", "hostname", "coordinator", "impalad", "daemon", "server")
URL_METADATA_KEY_PARTS = ("url", "uri", "endpoint", "link")


class HostlikeFqdnMatcher:
    """Small search-compatible host matcher that avoids regex backtracking."""

    def search(self, text: str) -> str | None:
        for token in iter_host_tokens(text):
            if is_fqdn_like_token(token) and should_redact_bare_fqdn(token):
                return token
        return None


HOSTLIKE_FQDN_RE = HostlikeFqdnMatcher()


def sanitize_text_for_log(text: object, *, secrets: Iterable[str] = ()) -> str:
    safe = str(text)
    for secret in secrets:
        if secret:
            # A caller-supplied secret makes the result depend on more than the
            # text, so this path is never cached.
            return redact_host_identifiers(sanitize_identifier_for_log(safe, secrets=secrets))
    return sanitized_text_for_log(safe)


@lru_cache(maxsize=SANITIZED_TEXT_CACHE_SIZE)
def sanitized_text_for_log(text: str) -> str:
    """Sanitize one string, remembering the answer.

    Sanitizing is a pure function of its input: the host redactor numbers its
    aliases within a single call, so the same string always yields the same
    result. Retained history is overwhelmingly repetition — reason codes,
    severities, field names, statuses — so the same few hundred strings are
    sanitized over and over whenever a page reads a batch of records. One
    production page rendered 93698 strings drawn from 701 distinct values, and
    spent most of its time on the redaction those repeats did not need.

    The cache holds sanitized output, which is raw-free by construction, and is
    bounded so an unusual run of distinct strings cannot grow it without limit.
    """
    return redact_host_identifiers(sanitize_identifier_for_log(text))


def sanitize_identifier_for_log(text: object, *, secrets: Iterable[str] = ()) -> str:
    """Sanitize a value that is an engine-assigned identifier, not free-form text.

    Same secret and credential redaction as sanitize_text_for_log, without the
    host pass. An Impala query id is written ``<hi>:<lo>``, and the host pass
    reads that colon as a host/port separator: when the low half starts with a
    digit it is taken for a port, and a high half that happens to start with a
    short role prefix (``db4a...``) is replaced by a ``host_NN`` alias. The
    surviving id names no query, so the profile behind it can never be fetched.
    """
    safe = str(text)
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "<secret>")
    safe = AUTH_HEADER_RE.sub(r"\1<redacted>", safe)
    safe = COOKIE_HEADER_RE.sub(r"\1<redacted>", safe)
    safe = BEARER_BASIC_RE.sub(r"\1 <redacted>", safe)
    safe = URL_CREDENTIAL_RE.sub(r"\1<redacted>@", safe)
    safe = SECRET_VALUE_RE.sub(redact_secret_value_match_preserving_marker, safe)
    return safe


def redact_secret_value_match_preserving_marker(match: re.Match[str]) -> str:
    marker = "<secret>" if match.group(4) == "<secret>" else "<redacted>"
    return f"{match.group(1)}{match.group(2)}{match.group(3)}{marker}{match.group(5)}"


def redact_local_paths(text: str) -> str:
    return LOCAL_PATH_RE.sub("<local_path>", text)


def sanitize_http_error_message(text: object, config: SupportsSecretValues) -> str:
    safe = str(text)
    safe = AUTH_HEADER_RE.sub(r"\1<redacted>", safe)
    safe = COOKIE_HEADER_RE.sub(r"\1<redacted>", safe)
    safe = BEARER_BASIC_RE.sub(r"\1 <redacted>", safe)
    safe = URL_CREDENTIAL_RE.sub(r"\1<redacted>@", safe)
    safe = SECRET_VALUE_RE.sub(r"\1\2\3<redacted>\5", safe)
    safe = sanitize_text_for_log(safe, secrets=config.secret_values())
    return safe


def sanitize_adapter_error_message(text: object, *, secrets: Iterable[str] = ()) -> str:
    safe = str(text)
    safe = AUTH_HEADER_RE.sub(r"\1<redacted>", safe)
    safe = COOKIE_HEADER_RE.sub(r"\1<redacted>", safe)
    safe = BEARER_BASIC_RE.sub(r"\1 <redacted>", safe)
    safe = URL_CREDENTIAL_RE.sub(r"\1<redacted>@", safe)
    safe = SECRET_VALUE_RE.sub(r"\1\2\3<redacted>\5", safe)
    return sanitize_text_for_log(safe, secrets=secrets)


class HostAliasRedactor:
    """Assign stable safe host aliases within one redaction operation."""

    def __init__(self) -> None:
        self._aliases: dict[str, str] = {}

    def alias_for(self, host: str) -> str:
        normalized = host.strip().strip("[]").rstrip(".").lower()
        if not normalized:
            return "host_00"
        if HOST_ALIAS_RE.fullmatch(normalized):
            return normalized
        alias = self._aliases.get(normalized)
        if alias is None:
            alias = f"host_{len(self._aliases) + 1:02d}"
            self._aliases[normalized] = alias
        return alias

    def redact_host_value(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return stripped
        bracketed_ipv6 = re.match(r"^\[(?P<ip>[0-9A-Fa-f:]+)\](?P<port>:\d+)?$", stripped)
        if bracketed_ipv6:
            try:
                ipaddress.ip_address(bracketed_ipv6.group("ip"))
            except ValueError:
                pass
            else:
                return f"{self.alias_for(bracketed_ipv6.group('ip'))}{bracketed_ipv6.group('port') or ''}"
        try:
            ipaddress.ip_address(stripped)
        except ValueError:
            pass
        else:
            return self.alias_for(stripped)
        match = re.match(r"^(?P<host>.+?)(?P<port>:\d+)?$", stripped)
        if not match:
            return self.alias_for(stripped)
        return f"{self.alias_for(match.group('host'))}{match.group('port') or ''}"


def redact_ipv6_candidates(text: str, host_redactor: HostAliasRedactor) -> str:
    redacted_parts: list[str] = []
    index = 0
    text_length = len(text)

    while index < text_length:
        char = text[index]
        if char not in IPV6_CANDIDATE_CHARS:
            redacted_parts.append(char)
            index += 1
            continue

        start = index
        while index < text_length and text[index] in IPV6_CANDIDATE_CHARS:
            index += 1

        candidate = text[start:index]
        if ":" not in candidate:
            redacted_parts.append(candidate)
            continue
        if start > 0 and text[start - 1] in IPV6_BOUNDARY_CHARS:
            redacted_parts.append(candidate)
            continue
        if index < text_length and text[index] in IPV6_BOUNDARY_CHARS:
            redacted_parts.append(candidate)
            continue

        try:
            ip_address = ipaddress.ip_address(candidate)
        except ValueError:
            redacted_parts.append(candidate)
            continue

        if ip_address.version == 6:
            redacted_parts.append(host_redactor.alias_for(candidate))
        else:
            redacted_parts.append(candidate)

    return "".join(redacted_parts)


def redact_hostlike_tokens(
    text: str,
    host_redactor: HostAliasRedactor,
    host_alias_or_original: HostAliasOrOriginal,
) -> str:
    redacted_parts: list[str] = []
    index = 0
    text_length = len(text)

    while index < text_length:
        char = text[index]
        if char not in HOST_TOKEN_CHARS:
            redacted_parts.append(char)
            index += 1
            continue

        start = index
        while index < text_length and text[index] in HOST_TOKEN_CHARS:
            index += 1

        token = text[start:index]
        token_end = index
        port = ""
        port_end = token_end
        if token_end < text_length and text[token_end] == ":":
            port_index = token_end + 1
            while port_index < text_length and text[port_index].isdigit():
                port_index += 1
            if port_index > token_end + 1:
                port = text[token_end:port_index]
                port_end = port_index

        if start > 0 and text[start - 1] in HOST_TOKEN_BOUNDARY_CHARS:
            redacted_parts.append(token)
            continue
        if not port and token_end < text_length and text[token_end] in HOST_TOKEN_BOUNDARY_CHARS:
            redacted_parts.append(token)
            continue

        if is_fqdn_like_token(token) and should_redact_bare_fqdn(token):
            redacted_parts.append(f"{host_alias_or_original(token)}{port}")
            index = port_end
        elif should_redact_hostlike_bare_name(token) or should_redact_short_role_bare_name(token):
            redacted_parts.append(f"{host_redactor.alias_for(token)}{port}")
            index = port_end
        else:
            redacted_parts.append(token)

    return "".join(redacted_parts)


HostAliasOrOriginal = Callable[[str], str]


def iter_host_tokens(text: str) -> Iterable[str]:
    index = 0
    text_length = len(text)

    while index < text_length:
        char = text[index]
        if char not in HOST_TOKEN_CHARS:
            index += 1
            continue

        start = index
        while index < text_length and text[index] in HOST_TOKEN_CHARS:
            index += 1

        token = text[start:index]
        if start > 0 and text[start - 1] in HOST_TOKEN_BOUNDARY_CHARS:
            continue
        if index < text_length and text[index] in HOST_TOKEN_BOUNDARY_CHARS:
            continue
        yield token


def redact_host_identifiers(text: str, redactor: HostAliasRedactor | None = None) -> str:
    host_redactor = redactor or HostAliasRedactor()

    def host_alias_or_original(value: str) -> str:
        if value.lower() in PUBLIC_NAMESPACE_HOSTS:
            return value
        return host_redactor.alias_for(value)

    def replace_bracketed_ipv6(match: re.Match[str]) -> str:
        value = match.group("ip")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return match.group(0)
        return host_redactor.alias_for(value)

    def replace_host_field(match: re.Match[str]) -> str:
        return f"{match.group(1)}{host_redactor.redact_host_value(match.group(2))}"

    def replace_host_assignment(match: re.Match[str]) -> str:
        alias = host_redactor.redact_host_value(match.group("value"))
        return f"{match.group('key')}{match.group('sep')}{alias}"

    def replace_url_host(match: re.Match[str]) -> str:
        alias = host_alias_or_original(match.group(3))
        return f"{match.group(1)}{match.group(2) or ''}{alias}{match.group(4) or ''}"

    redacted = HOST_FIELD_RE.sub(replace_host_field, text)
    redacted = HOST_ASSIGNMENT_RE.sub(replace_host_assignment, redacted)
    redacted = URL_HOST_RE.sub(replace_url_host, redacted)
    redacted = redact_hostlike_tokens(redacted, host_redactor, host_alias_or_original)
    redacted = IPV4_RE.sub(lambda match: host_redactor.alias_for(match.group(0)), redacted)
    redacted = BRACKETED_IPV6_RE.sub(replace_bracketed_ipv6, redacted)
    redacted = redact_ipv6_candidates(redacted, host_redactor)
    return redacted


def is_fqdn_like_token(value: str) -> bool:
    labels = value.rstrip(".").split(".")
    if len(labels) < 2:
        return False
    if any(not label for label in labels):
        return False
    if not labels[-1][0].isalpha():
        return False
    return all(all(ch.isalnum() or ch == "-" for ch in label) for label in labels)


def should_redact_hostlike_bare_name(value: str) -> bool:
    normalized = value.lower()
    if len(normalized) < 3 or not normalized[0].isalpha():
        return False
    if not any(ch.isdigit() for ch in normalized):
        return False
    return any(keyword in normalized for keyword in HOSTLIKE_BARE_KEYWORDS)


def should_redact_short_role_bare_name(value: str) -> bool:
    normalized = value.lower()
    for prefix in SHORT_ROLE_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        suffix = normalized[len(prefix) :]
        if suffix.startswith("-"):
            suffix = suffix[1:]
        return bool(suffix) and suffix[0].isdigit()
    return False


def should_redact_bare_fqdn(value: str) -> bool:
    normalized = value.rstrip(".").lower()
    if normalized in PUBLIC_NAMESPACE_HOSTS:
        return False
    labels = normalized.split(".")
    if len(labels) >= 3:
        return True
    if len(labels) != 2:
        return False
    left, right = labels
    if right in SAFE_FILE_EXTENSION_SUFFIXES:
        return False
    if right in PUBLIC_OR_INFRA_DOMAIN_SUFFIXES:
        return True
    return any(any(ch.isdigit() for ch in label) or "-" in label for label in (left, right))


def redacted_table_label(number: int) -> str:
    """Name the table at a 1-based position of the statement's table list.

    The SQL redaction and the metadata collector both use this name, so a
    column the SQL joins or filters on can still be matched to its table's
    metadata without either side keeping the real name.
    """
    return f"<db>.<table_{number}>"


def table_label_key(table: str) -> str:
    return re.sub(r"[`\s]", "", table).lower()


def redacted_table_labels(tables: Iterable[str]) -> dict[str, str]:
    """Label tables by their position in the statement's table list.

    A name that repeats keeps the label of its first position, the same
    position the metadata plan keeps when it dedupes the list.
    """
    labels: dict[str, str] = {}
    for number, table in enumerate(tables, start=1):
        labels.setdefault(table_label_key(table), redacted_table_label(number))
    return labels


def redact_profile_text(
    text: str,
    *,
    redact_identifiers: bool = False,
    redact_hosts: bool = True,
    table_labels: Mapping[str, str] | None = None,
) -> str:
    host_redactor = HostAliasRedactor()
    redacted = text
    redacted = EMAIL_RE.sub("<email>", redacted)
    redacted = URL_CREDENTIAL_RE.sub(r"\1<redacted>@", redacted)
    redacted = AUTH_HEADER_RE.sub(r"\1<redacted>", redacted)
    redacted = COOKIE_HEADER_RE.sub(r"\1<redacted>", redacted)
    redacted = BEARER_BASIC_RE.sub(r"\1 <redacted>", redacted)
    redacted = SECRET_VALUE_RE.sub(r"\1\2\3<redacted>\5", redacted)
    redacted = USER_FIELD_RE.sub(r"\1<user>", redacted)
    redacted = POOL_FIELD_RE.sub(r"\1<pool>", redacted)
    redacted = USER_KV_RE.sub(r"\1\2<user>", redacted)
    redacted = redact_local_paths(redacted)
    if redact_hosts:
        redacted = redact_host_identifiers(redacted, host_redactor)

    if redact_identifiers:
        labels = table_labels or {}

        def label(match: re.Match[str], fallback: str) -> str:
            table = match.group(0)[len(match.group(1)) :]
            return f"{match.group(1)} {labels.get(table_label_key(table), fallback)}"

        redacted = SQL_DB_TABLE_RE.sub(lambda match: label(match, "<db>.<table>"), redacted)
        redacted = SQL_TABLE_RE.sub(lambda match: label(match, "<table>"), redacted)

    return redacted


def redact_metadata(
    metadata: dict[str, object],
    *,
    redact_identifiers: bool = False,
    redact_hosts: bool = True,
) -> dict[str, object]:
    host_redactor = HostAliasRedactor()
    return {
        key: redact_metadata_value(
            key,
            value,
            redact_identifiers=redact_identifiers,
            redact_hosts=redact_hosts,
            host_redactor=host_redactor,
        )
        for key, value in metadata.items()
    }


def redact_metadata_value(
    key: str,
    value: object,
    *,
    redact_identifiers: bool,
    redact_hosts: bool,
    host_redactor: HostAliasRedactor,
) -> object:
    normalized_key = key.lower()

    if normalized_key in PRESERVED_METADATA_KEYS:
        return value
    if normalized_key in USER_METADATA_KEYS:
        return "<user>" if value is not None else None
    if normalized_key in POOL_METADATA_KEYS:
        return "<pool>" if value is not None else None
    if any(part in normalized_key for part in SECRET_METADATA_KEY_PARTS):
        return "<redacted>" if value is not None else None
    if redact_hosts and any(part in normalized_key for part in HOST_METADATA_KEY_PARTS):
        return host_redactor.redact_host_value(str(value)) if value is not None else None
    if any(part in normalized_key for part in URL_METADATA_KEY_PARTS):
        return "<url>" if value is not None else None
    if isinstance(value, str):
        redacted = redact_profile_text(
            value,
            redact_identifiers=redact_identifiers,
            redact_hosts=redact_hosts,
        )
        return redact_host_identifiers(redacted, host_redactor) if redact_hosts else redacted
    if isinstance(value, dict):
        return {
            child_key: redact_metadata_value(
                child_key,
                child_value,
                redact_identifiers=redact_identifiers,
                redact_hosts=redact_hosts,
                host_redactor=host_redactor,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            redact_metadata_value(
                key,
                item,
                redact_identifiers=redact_identifiers,
                redact_hosts=redact_hosts,
                host_redactor=host_redactor,
            )
            for item in value
        ]
    return value
