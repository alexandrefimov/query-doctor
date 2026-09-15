"""Direct Impala daemon profile collection for one explicit query id."""

from __future__ import annotations

import html
import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from query_doctor.cm.client import validate_cm_query_id_path_segment
from query_doctor.cm.models import CMAdapterError, CMClientError
from query_doctor.safety.http_egress import configured_diagnostic_urlopen


DEFAULT_IMPALA_PROFILE_PORT = 25000
DEFAULT_IMPALA_PROFILE_SCHEME = "http"
DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC = 15
PROFILE_MARKER_SCAN_CHARS = 128 * 1024
IMPALA_TEXT_PROFILE_PATHS = (
    "/query_profile?query_id={query_id}&format=text",
    "/query_profile?query_id={query_id}",
)
IMPALA_JSON_PROFILE_PATHS = ("/query_profile?query_id={query_id}&format=json",)
PROFILE_NOT_FOUND_MARKERS = (
    "Could not find query",
    "Query id not found",
    "Invalid query id",
    "No profile available",
)
PROFILE_CONTENT_MARKERS = (
    # The daemon opens a text profile with its own header, at offset zero, so
    # this holds however long the summary and plan that follow turn out to be.
    # The section headings below can sit past the scan window on a large plan,
    # which is exactly the profile worth reading.
    "query (id=",
    "query runtime profile",
    "query timeline",
    "planner timeline",
    "impala query profile",
)
PROFILE_STRUCTURAL_MARKERS = (
    "plan:",
    "fragment ",
    "fragment_instance_id",
    "query state:",
)
JSON_PROFILE_CONTENT_MARKERS = (
    '"runtime_profile"',
    '"runtimeprofile"',
    '"profile_version"',
    '"counters"',
    '"children"',
)
PRE_RE = re.compile(r"<pre[^>]*>(?P<body>.*?)</pre>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


UrlOpener = Callable[..., object]


@dataclass(frozen=True)
class ImpalaProfileFetchResult:
    query_id: str
    profile_text: str
    attempted_endpoints: int
    profile_endpoint_format: str = "unknown"


def normalize_impala_profile_scheme(value: str | None) -> str:
    scheme = (value or DEFAULT_IMPALA_PROFILE_SCHEME).strip().lower()
    if scheme not in {"http", "https"}:
        raise CMAdapterError("Impala profile scheme must be http or https.")
    return scheme


def normalize_impala_profile_hosts(values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raw_values = [item.strip() for item in values.split(",")]
    elif isinstance(values, (list, tuple)):
        raw_values = [str(item).strip() for item in values]
    else:
        raise CMAdapterError("Impala profile hosts must be a list of hostnames.")
    hosts = tuple(host for host in raw_values if host)
    for host in hosts:
        validate_impala_profile_host(host)
    return hosts


def validate_impala_profile_host(host: str) -> None:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in host):
        raise CMAdapterError("Impala profile host must not contain control characters.")
    if any(marker in host for marker in ("/", "\\", "@", "?", "#")):
        raise CMAdapterError("Impala profile host must be a hostname or host:port only.")


def impala_profile_urls(
    hosts: Iterable[str],
    *,
    query_id: str,
    port: int = DEFAULT_IMPALA_PROFILE_PORT,
    scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME,
    prefer_json: bool = False,
) -> tuple[str, ...]:
    return tuple(
        url
        for url, _format in impala_profile_url_candidates(
            hosts,
            query_id=query_id,
            port=port,
            scheme=scheme,
            prefer_json=prefer_json,
        )
    )


def impala_profile_url_candidates(
    hosts: Iterable[str],
    *,
    query_id: str,
    port: int = DEFAULT_IMPALA_PROFILE_PORT,
    scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME,
    prefer_json: bool = False,
) -> tuple[tuple[str, str], ...]:
    normalized_query_id = validate_cm_query_id_path_segment(query_id)
    normalized_scheme = normalize_impala_profile_scheme(scheme)
    encoded_query_id = urllib.parse.quote(normalized_query_id, safe="")
    paths = (
        IMPALA_JSON_PROFILE_PATHS + IMPALA_TEXT_PROFILE_PATHS
        if prefer_json
        else IMPALA_TEXT_PROFILE_PATHS
    )
    candidates: list[tuple[str, str]] = []
    for host in normalize_impala_profile_hosts(tuple(hosts)):
        netloc = host if ":" in host else f"{host}:{port}"
        for path in paths:
            candidate_format = profile_endpoint_format_for_path(path)
            candidates.append(
                (
                    f"{normalized_scheme}://{netloc}{path.format(query_id=encoded_query_id)}",
                    candidate_format,
                )
            )
    return tuple(candidates)


def profile_endpoint_format_for_path(path: str) -> str:
    if "format=json" in path:
        return "json"
    if "format=text" in path:
        return "text"
    return "default"


def fetch_impala_profile_text(
    *,
    query_id: str,
    hosts: Iterable[str],
    port: int = DEFAULT_IMPALA_PROFILE_PORT,
    scheme: str = DEFAULT_IMPALA_PROFILE_SCHEME,
    timeout_sec: int = DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    max_profile_bytes: int,
    prefer_json: bool = False,
    opener: UrlOpener = configured_diagnostic_urlopen,
) -> ImpalaProfileFetchResult:
    candidates = impala_profile_url_candidates(
        hosts,
        query_id=query_id,
        port=port,
        scheme=scheme,
        prefer_json=prefer_json,
    )
    if not candidates:
        raise CMAdapterError("Impala profile collection requires at least one impalad host.")
    attempted = 0
    last_error = "profile endpoint unavailable"
    for url, endpoint_format in candidates:
        attempted += 1
        try:
            text = fetch_profile_url(
                url,
                timeout_sec=timeout_sec,
                max_profile_bytes=max_profile_bytes,
                opener=opener,
            )
        except CMClientError as exc:
            last_error = str(exc)
            continue
        if profile_response_is_not_found(text):
            last_error = "profile was not found on one impalad endpoint"
            continue
        if profile_text_looks_like_runtime_profile(text):
            return ImpalaProfileFetchResult(
                query_id=validate_cm_query_id_path_segment(query_id),
                profile_text=text,
                attempted_endpoints=attempted,
                profile_endpoint_format=endpoint_format,
            )
        if text.strip():
            last_error = "profile endpoint returned non-profile content"
    raise CMAdapterError(
        "Impala profile collection failed on the configured impalad endpoints. "
        f"Attempted endpoints: {attempted}. Last safe error: {last_error.rstrip('.')}."
    )


def fetch_profile_url(
    url: str,
    *,
    timeout_sec: int,
    max_profile_bytes: int,
    opener: UrlOpener,
) -> str:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json,text/plain,text/html"},
    )
    try:
        with opener(request, timeout=timeout_sec) as response:
            raw = response.read(max_profile_bytes + 1)
    except urllib.error.HTTPError as exc:
        if type(exc.code) is int and 100 <= exc.code <= 599:
            raise CMClientError(
                f"Impala profile endpoint request returned HTTP {exc.code}."
            ) from exc
        raise CMClientError("Impala profile endpoint request failed safely.") from exc
    except TimeoutError as exc:
        raise CMClientError("Impala profile endpoint request timed out safely.") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise CMClientError("Impala profile endpoint request timed out safely.") from exc
        raise CMClientError("Impala profile endpoint request failed safely.") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise CMClientError("Impala profile endpoint request failed safely.") from exc
    if len(raw) > max_profile_bytes:
        raise CMClientError("Impala profile endpoint response exceeded the configured byte limit.")
    text = raw.decode("utf-8", errors="replace")
    return extract_profile_text_from_response(text)


def extract_profile_text_from_response(text: str) -> str:
    pre_match = PRE_RE.search(text)
    if pre_match:
        return html.unescape(pre_match.group("body")).strip() + "\n"
    if "<html" in text[:512].lower() or "<body" in text[:512].lower():
        return html.unescape(TAG_RE.sub("\n", text)).strip() + "\n"
    return text


def profile_response_is_not_found(text: str) -> bool:
    normalized = text.lower()
    return any(marker.lower() in normalized for marker in PROFILE_NOT_FOUND_MARKERS)


def profile_text_looks_like_runtime_profile(text: str) -> bool:
    normalized = text[:PROFILE_MARKER_SCAN_CHARS].lower()
    stripped = normalized.lstrip()
    if stripped.startswith(("{", "[")) and any(
        marker in normalized for marker in JSON_PROFILE_CONTENT_MARKERS
    ):
        return True
    if any(marker in normalized for marker in PROFILE_CONTENT_MARKERS):
        return True
    if "query id:" in normalized and any(
        marker in normalized for marker in PROFILE_STRUCTURAL_MARKERS
    ):
        return True
    return False
