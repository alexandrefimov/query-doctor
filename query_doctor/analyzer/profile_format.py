"""Safe Impala runtime profile format and identity facts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ProfileDialect(str, Enum):
    CLASSIC_TEXT = "classic_text_profile"
    CLASSIC_JSON = "classic_json_profile"
    CLASSIC_THRIFT = "classic_thrift_profile"
    EXPERIMENTAL_V2 = "experimental_profile_v2"
    UNKNOWN = "unknown"


class ProfileAnalysisSupport(str, Enum):
    SUPPORTED = "supported"
    LIMITED = "limited"
    UNSUPPORTED = "unsupported"


class PrimaryBottleneckPolicy(str, Enum):
    SUPPORTED = "supported"
    NON_PROFILE_ONLY = "non_profile_only"
    UNSUPPORTED = "unsupported"


class ProfileSectionMapping(str, Enum):
    SUPPORTED = "supported"
    LIMITED = "limited"
    NOT_OBSERVED = "not_observed"
    UNSUPPORTED = "unsupported"


SAFE_PROFILE_ENDPOINT_FORMATS = {"json", "text", "default", "unknown"}
PROFILE_SECTION_ORDER = (
    "profile_resources",
    "profile_timings",
    "resource_trace",
    "profile_counters",
    "client_fetch_tail",
    "memory_pressure",
)


@dataclass(frozen=True)
class ProfileDialectDetection:
    dialect: ProfileDialect
    confidence: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dialect"] = self.dialect.value
        return payload


IMPALA_VERSION_LINE_RE = re.compile(
    r"^\s*Impala\s+Version\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
IMPALA_VERSION_TOKEN_RE = re.compile(
    r"\b(?:impalad|catalogd|statestored)?\s*version\s+(?P<version>[0-9][A-Za-z0-9_.-]*)"
    r"(?:\s+(?P<build_type>[A-Z][A-Z0-9_-]*))?",
    re.IGNORECASE,
)
RAW_RUNTIME_NODE_RE = re.compile(r"^\s*[A-Z][A-Z0-9_]+_NODE\s+\(id=\d{1,3}\)", re.MULTILINE)
FRAGMENT_SECTION_RE = re.compile(r"^\s*F\d{2,}\s*:", re.MULTILINE)
AVERAGED_FRAGMENT_RE = re.compile(r"^\s*Averaged\s+Fragment\s+F\d+\b", re.IGNORECASE | re.MULTILINE)
INSTANCE_HOST_RE = re.compile(r"^\s*Instance\s+\S+\s+\(host=", re.IGNORECASE | re.MULTILINE)
CLASSIC_TEXT_MARKER_RE = re.compile(
    r"^\s*(?:Summary|ExecSummary|Query\s+Timeline|Plan)\s*:"
    r"|^\s*(?:Query\s+)?Runtime\s+Profile\b"
    r"|^\s*#{1,6}\s*(?:ExecSummary|Backend counters|Metric lines)\b",
    re.IGNORECASE | re.MULTILINE,
)
CLASSIC_TEXT_SECTION_MARKERS = (
    "Admission result:",
    "Backend startup latencies",
    "Per Node Peak Memory Usage",
    "Per Node Bytes Read",
    "Per Node User Time",
    "Per Node System Time",
    "Per Host Number of Fragment Instances",
    "Fragment Instance Lifecycle",
)
THRIFT_PROFILE_RE = re.compile(
    r"\b(?:TQueryProfile|TRuntimeProfileTree|TRuntimeProfileNode|TExecSummary)\b",
    re.IGNORECASE,
)
PROFILE_V2_TEXT_RE = re.compile(
    r"\b(?:experimental[_\s-]*profile(?:[_\s-]*v?2)?|profile[_\s-]*v2|"
    r"aggregated[_\s-]*profile|gen_experimental_profile)\b",
    re.IGNORECASE,
)
# AGGREGATED_PROFILE and GEN_EXPERIMENTAL_PROFILE are also query option names, so
# a classic profile lists them in its Query Options lines. The value stops at a
# newline or at the backslash of an escaped one inside a CM JSON string.
QUERY_OPTIONS_VALUE_RE = re.compile(r"Query Options \([^)\n\r]*\):[^\n\r\\]*", re.IGNORECASE)
CLASSIC_JSON_KEYS = {
    "profile",
    "queryprofile",
    "runtimeprofile",
    "runtime_profile",
    "profiletree",
    "profile_tree",
    "counters",
    "children",
    "nodes",
}
PROFILE_V2_KEYS = {
    "aggregatedprofile",
    "aggregated_profile",
    "experimentalprofile",
    "experimental_profile",
    "profilev2",
    "profile_v2",
    "genexperimentalprofile",
    "gen_experimental_profile",
}
TEXT_PROFILE_FIELDS = ("details", "profile", "profiletext", "profile_text", "text")


def parse_impala_version_label(value: object) -> dict[str, str | None]:
    text = str(value or "").strip()
    match = IMPALA_VERSION_TOKEN_RE.search(text)
    if not match:
        return {"version": None, "build_type": None, "version_label": None}
    version = match.group("version")
    build_type = (match.group("build_type") or "").upper() or None
    label = f"impalad version {version}"
    if build_type:
        label = f"{label} {build_type}"
    return {
        "version": version,
        "build_type": build_type,
        "version_label": label,
    }


def profile_impala_version(text: str) -> dict[str, str | None]:
    match = IMPALA_VERSION_LINE_RE.search(text)
    if not match:
        return {"version": None, "build_type": None, "version_label": None}
    return parse_impala_version_label(match.group("value"))


def version_major(value: object) -> int | None:
    text = str(value or "")
    match = re.match(r"(?P<major>\d+)(?:\.|$)", text)
    if not match:
        return None
    try:
        return int(match.group("major"))
    except ValueError:
        return None


def infer_impala_distribution(version_label: object, metadata_product: object = None) -> str:
    product = str(metadata_product or "").strip().lower()
    if product in {"apache_impala", "cloudera_impala"}:
        return product
    text = str(version_label or "").lower()
    if "cloudera" in text or "cdh" in text or "cdp" in text:
        return "cloudera_impala"
    if "impalad version" in text or "apache impala" in text:
        return "apache_impala"
    return "unknown"


def detect_profile_dialect(
    raw_text: str,
    *,
    normalized_text: str | None = None,
) -> ProfileDialectDetection:
    """Detect the profile representation before profile-derived analysis.

    The reasons are stable reason IDs, not raw profile excerpts, so the result
    can be threaded into browser-visible facts and trusted reports.
    """

    raw = raw_text or ""
    effective = normalized_text if normalized_text is not None else raw
    stripped = raw.lstrip()
    if not stripped:
        return ProfileDialectDetection(ProfileDialect.UNKNOWN, "low", ("empty_profile_input",))

    json_payload = parse_json_object(stripped)
    if json_payload is not None:
        if json_payload_has_profile_v2_marker(json_payload):
            return ProfileDialectDetection(
                ProfileDialect.EXPERIMENTAL_V2,
                "medium",
                ("json_profile_v2_marker",),
            )
        if json_payload_wraps_classic_text_profile(json_payload):
            return ProfileDialectDetection(
                ProfileDialect.CLASSIC_TEXT,
                "medium",
                ("json_wrapped_classic_text_profile",),
            )
        if json_payload_has_classic_profile_marker(json_payload):
            return ProfileDialectDetection(
                ProfileDialect.CLASSIC_JSON,
                "medium",
                ("classic_json_profile_marker",),
            )
        return ProfileDialectDetection(ProfileDialect.UNKNOWN, "low", ("json_profile_unmapped",))

    if PROFILE_V2_TEXT_RE.search(QUERY_OPTIONS_VALUE_RE.sub("", raw)):
        return ProfileDialectDetection(
            ProfileDialect.EXPERIMENTAL_V2,
            "low",
            ("text_profile_v2_marker",),
        )
    if THRIFT_PROFILE_RE.search(raw):
        return ProfileDialectDetection(
            ProfileDialect.CLASSIC_THRIFT,
            "medium",
            ("classic_thrift_profile_marker",),
        )
    if classic_text_profile_markers_observed(effective):
        return ProfileDialectDetection(
            ProfileDialect.CLASSIC_TEXT,
            "medium",
            ("classic_text_profile_marker",),
        )
    if classic_text_section_marker_count(effective) >= 2:
        return ProfileDialectDetection(
            ProfileDialect.CLASSIC_TEXT,
            "medium",
            ("classic_text_section_markers",),
        )
    return ProfileDialectDetection(ProfileDialect.UNKNOWN, "low", ("profile_markers_not_found",))


def parse_json_object(text: str) -> Any | None:
    if not text.startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def classic_text_profile_markers_observed(text: str) -> bool:
    return bool(
        CLASSIC_TEXT_MARKER_RE.search(text)
        or RAW_RUNTIME_NODE_RE.search(text)
        or FRAGMENT_SECTION_RE.search(text)
        or AVERAGED_FRAGMENT_RE.search(text)
    )


def classic_text_section_marker_count(text: str) -> int:
    return sum(1 for marker in CLASSIC_TEXT_SECTION_MARKERS if marker in text)


def normalize_json_key(value: object) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value or "").strip().lower())


def json_payload_has_profile_v2_marker(value: Any) -> bool:
    for key, item in iter_json_items(value):
        normalized_key = normalize_json_key(key)
        if normalized_key in PROFILE_V2_KEYS:
            return True
        if normalized_key in {"profileversion", "profile_version", "version"}:
            if str(item).strip().lower() in {"2", "v2", "profile_v2", "experimental_profile_v2"}:
                return True
        if isinstance(item, str) and PROFILE_V2_TEXT_RE.search(
            QUERY_OPTIONS_VALUE_RE.sub("", item)
        ):
            return True
    return False


def json_payload_wraps_classic_text_profile(value: Any) -> bool:
    for key, item in iter_json_items(value):
        if normalize_json_key(key) not in TEXT_PROFILE_FIELDS or not isinstance(item, str):
            continue
        if (
            classic_text_profile_markers_observed(item)
            or classic_text_section_marker_count(item) >= 2
        ):
            return True
    return False


def json_payload_has_classic_profile_marker(value: Any) -> bool:
    for key, item in iter_json_items(value):
        normalized_key = normalize_json_key(key)
        if normalized_key in CLASSIC_JSON_KEYS:
            if isinstance(item, (dict, list)):
                return True
            if normalized_key in {"counters", "children", "nodes"}:
                return True
        if normalized_key in {"profileversion", "profile_version"}:
            if str(item).strip().lower() in {"1", "classic", "classic_json"}:
                return True
    return False


def iter_json_items(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from iter_json_items(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_items(item)


def profile_layout_name(features: dict[str, bool | int]) -> str:
    if features.get("json_mapped_counter_count"):
        return "json_mapped_counters"
    if features.get("raw_runtime_nodes") and features.get("fragment_instance_lifecycle"):
        return "raw_runtime_nodes_with_lifecycle"
    if features.get("raw_runtime_nodes"):
        return "raw_runtime_nodes"
    if features.get("exec_summary_table"):
        return "exec_summary_table"
    if features.get("summary"):
        return "summary_only"
    if features.get("plan") and features.get("fragment_section_count"):
        return "plan_fragment_sections"
    if classic_text_resource_or_timing_features_observed(features):
        return "resource_or_timing_sections"
    return "unknown"


def classic_text_resource_or_timing_features_observed(features: dict[str, bool | int]) -> bool:
    return bool(
        features.get("query_timeline")
        or features.get("admission")
        or features.get("backend_startup_latencies")
        or features.get("per_node_peak_memory")
        or features.get("per_node_bytes_read")
        or features.get("per_node_user_time")
        or features.get("per_node_system_time")
        or features.get("per_host_fragment_instances")
        or features.get("fragment_instance_lifecycle")
        or features.get("resource_trace")
    )


def build_profile_format_facts(
    text: str,
    query_context: dict[str, Any] | None = None,
    raw_text: str | None = None,
) -> dict[str, Any]:
    context = query_context or {}
    detection = detect_profile_dialect(
        raw_text if raw_text is not None else text, normalized_text=text
    )
    version_facts = {
        "version": context.get("impala_daemon_version"),
        "build_type": context.get("impala_daemon_build_type"),
        "version_label": context.get("impala_daemon_version_label"),
    }
    if not version_facts["version"]:
        version_facts = profile_impala_version(text)

    features: dict[str, bool | int] = {
        "summary": bool(re.search(r"^\s*Summary\s*:", text, re.IGNORECASE | re.MULTILINE)),
        "query_timeline": bool(
            re.search(r"^\s*Query\s+Timeline\s*:", text, re.IGNORECASE | re.MULTILINE)
        ),
        "plan": bool(re.search(r"^\s*Plan\s*:", text, re.IGNORECASE | re.MULTILINE)),
        "exec_summary_table": bool(
            re.search(
                r"^\s*(?:ExecSummary\s*:|#{1,6}\s*ExecSummary\b)",
                text,
                re.IGNORECASE | re.MULTILINE,
            )
        ),
        "admission": "Admission result:" in text,
        "backend_startup_latencies": "Backend startup latencies" in text,
        "per_node_peak_memory": "Per Node Peak Memory Usage" in text,
        "per_node_bytes_read": "Per Node Bytes Read" in text,
        "per_node_user_time": "Per Node User Time" in text,
        "per_node_system_time": "Per Node System Time" in text,
        "per_host_fragment_instances": "Per Host Number of Fragment Instances" in text,
        "fragment_instance_lifecycle": "Fragment Instance Lifecycle" in text,
        "resource_trace": bool(
            re.search(
                r"\b(?:Per\s+Node\s+Profiles|HostCpu|HostDisk|HostNetwork|"
                r"CpuIoWaitPercentage|DiskReadThroughput|NetworkRx)\b",
                text,
                re.IGNORECASE,
            )
        ),
        "json_mapped_counter_count": len(
            re.findall(r"^\s*-\s+[A-Za-z][A-Za-z0-9_]*\s*:", text, re.MULTILINE)
        )
        if "# JSON mapped profile counters" in text
        else 0,
        "raw_runtime_nodes": bool(RAW_RUNTIME_NODE_RE.search(text)),
        "runtime_node_count": len(RAW_RUNTIME_NODE_RE.findall(text)),
        "fragment_section_count": len(FRAGMENT_SECTION_RE.findall(text))
        + len(AVERAGED_FRAGMENT_RE.findall(text)),
        "fragment_instance_count": len(INSTANCE_HOST_RE.findall(text)),
    }
    layout = profile_layout_name(features)
    version = version_facts.get("version")
    compatibility = profile_compatibility_status(layout, detection.dialect)
    analysis_support = profile_analysis_support(compatibility, detection.dialect)
    primary_policy = primary_bottleneck_policy(detection.dialect, analysis_support)
    per_instance_evidence = per_instance_evidence_status(detection.dialect, features)
    source_capabilities = profile_source_capabilities(
        detection,
        features,
        context,
        primary_policy,
    )
    section_mappings = profile_section_mappings(detection.dialect, features)
    return {
        "profile_family": "impala_runtime_profile"
        if detection.dialect != ProfileDialect.UNKNOWN
        or features["summary"]
        or features["raw_runtime_nodes"]
        else "unknown",
        "profile_dialect": detection.dialect.value,
        "dialect_confidence": detection.confidence,
        "dialect_reasons": list(detection.reasons),
        "profile_source": context.get("profile_source") or "unknown",
        "source_label": context.get("source_label")
        or context.get("profile_source_label")
        or "unknown",
        "impala_distribution": infer_impala_distribution(
            version_facts.get("version_label"),
            context.get("impala_daemon_product"),
        ),
        "impala_version": version,
        "impala_major_version": version_major(version),
        "impala_build_type": version_facts.get("build_type"),
        "daemon_server_mode": context.get("impala_daemon_server_mode"),
        "daemon_local_catalog_mode": context.get("impala_daemon_local_catalog_mode"),
        "profile_response_format": source_capabilities["profile_response_format"],
        "layout": layout,
        "features": features,
        "compatibility": compatibility,
        "analysis_support": analysis_support.value,
        "primary_bottleneck_policy": primary_policy.value,
        "per_instance_evidence": per_instance_evidence,
        "source_capabilities": source_capabilities,
        "section_mappings": section_mappings,
        "limitations": profile_format_limitations(
            detection.dialect,
            analysis_support,
            primary_policy,
            per_instance_evidence,
        ),
    }


def profile_compatibility_status(
    layout: str, dialect: ProfileDialect = ProfileDialect.CLASSIC_TEXT
) -> str:
    if dialect == ProfileDialect.UNKNOWN:
        return "unknown"
    if dialect == ProfileDialect.EXPERIMENTAL_V2:
        return "partial"
    if dialect in {ProfileDialect.CLASSIC_JSON, ProfileDialect.CLASSIC_THRIFT}:
        return "partial" if layout != "unknown" else "unknown"
    if layout in {"raw_runtime_nodes_with_lifecycle", "raw_runtime_nodes", "exec_summary_table"}:
        return "supported"
    if layout in {"summary_only", "plan_fragment_sections", "resource_or_timing_sections"}:
        return "partial"
    return "unknown"


def profile_analysis_support(
    compatibility: str,
    dialect: ProfileDialect,
) -> ProfileAnalysisSupport:
    if dialect == ProfileDialect.UNKNOWN:
        return ProfileAnalysisSupport.UNSUPPORTED
    if dialect == ProfileDialect.EXPERIMENTAL_V2:
        return ProfileAnalysisSupport.LIMITED
    if dialect in {ProfileDialect.CLASSIC_JSON, ProfileDialect.CLASSIC_THRIFT}:
        return ProfileAnalysisSupport.LIMITED
    if compatibility == "supported":
        return ProfileAnalysisSupport.SUPPORTED
    if compatibility == "partial":
        return ProfileAnalysisSupport.LIMITED
    return ProfileAnalysisSupport.UNSUPPORTED


def primary_bottleneck_policy(
    dialect: ProfileDialect,
    support: ProfileAnalysisSupport,
) -> PrimaryBottleneckPolicy:
    if dialect == ProfileDialect.CLASSIC_TEXT and support in {
        ProfileAnalysisSupport.SUPPORTED,
        ProfileAnalysisSupport.LIMITED,
    }:
        return PrimaryBottleneckPolicy.SUPPORTED
    if support == ProfileAnalysisSupport.SUPPORTED:
        return PrimaryBottleneckPolicy.SUPPORTED
    if dialect == ProfileDialect.EXPERIMENTAL_V2:
        return PrimaryBottleneckPolicy.NON_PROFILE_ONLY
    return PrimaryBottleneckPolicy.UNSUPPORTED


def per_instance_evidence_status(
    dialect: ProfileDialect,
    features: dict[str, bool | int],
) -> str:
    if dialect == ProfileDialect.EXPERIMENTAL_V2:
        return "unknown"
    if dialect in {
        ProfileDialect.UNKNOWN,
        ProfileDialect.CLASSIC_JSON,
        ProfileDialect.CLASSIC_THRIFT,
    }:
        return "unknown"
    if (
        features.get("fragment_instance_count")
        or features.get("per_host_fragment_instances")
        or features.get("fragment_instance_lifecycle")
    ):
        return "supported"
    return "not_observed"


def profile_format_limitations(
    dialect: ProfileDialect,
    support: ProfileAnalysisSupport,
    primary_policy: PrimaryBottleneckPolicy,
    per_instance_evidence: str,
) -> list[dict[str, str]]:
    limitations: list[dict[str, str]] = []
    if dialect == ProfileDialect.UNKNOWN:
        limitations.append(
            {
                "id": "profile_dialect_unknown",
                "state": "unknown",
                "summary": (
                    "Profile dialect is unknown; profile-derived primary bottleneck "
                    "classification is disabled."
                ),
            }
        )
    elif dialect == ProfileDialect.EXPERIMENTAL_V2:
        limitations.append(
            {
                "id": "profile_v2_limited",
                "state": "unknown",
                "summary": (
                    "Experimental profile-v2 was detected; only explicitly mapped "
                    "query-specific sections may support findings."
                ),
            }
        )
    elif dialect in {ProfileDialect.CLASSIC_JSON, ProfileDialect.CLASSIC_THRIFT}:
        limitations.append(
            {
                "id": "profile_dialect_partially_mapped",
                "state": "unknown",
                "summary": (
                    f"{dialect.value} was detected, but this analyzer slice only has "
                    "limited mapped-section coverage."
                ),
            }
        )
    if primary_policy != PrimaryBottleneckPolicy.SUPPORTED:
        limitations.append(
            {
                "id": "primary_bottleneck_policy_limited",
                "state": "unknown",
                "summary": (
                    "Primary bottleneck routing is limited until the profile dialect "
                    "has mapped evidence for the relevant claim family."
                ),
            }
        )
    if per_instance_evidence != "supported":
        limitations.append(
            {
                "id": "per_instance_evidence_limited",
                "state": "unknown",
                "summary": (
                    "Per-instance or equivalent aggregate evidence is not mapped; "
                    "scan-skew and backend-tail claims must not be promoted."
                ),
            }
        )
    if support == ProfileAnalysisSupport.UNSUPPORTED and dialect != ProfileDialect.UNKNOWN:
        limitations.append(
            {
                "id": "profile_analysis_unsupported",
                "state": "unknown",
                "summary": "Profile-derived deterministic analysis is unsupported for this layout.",
            }
        )
    return limitations


def profile_section_mappings(
    dialect: ProfileDialect,
    features: dict[str, bool | int],
) -> dict[str, dict[str, str]]:
    """Describe which profile-derived sections may be interpreted.

    Section summaries are raw-free by construction so they can be rendered in
    analyzer facts, Details diagnostics, and trusted report prompts.
    """

    if dialect == ProfileDialect.CLASSIC_TEXT:
        return {
            "profile_resources": classic_text_section_mapping(
                bool(
                    features.get("admission")
                    or features.get("backend_startup_latencies")
                    or features.get("per_node_peak_memory")
                    or features.get("per_node_bytes_read")
                    or features.get("per_node_user_time")
                    or features.get("per_node_system_time")
                    or features.get("per_host_fragment_instances")
                ),
                section_label="Profile resource sections",
            ),
            "profile_timings": classic_text_section_mapping(
                bool(features.get("query_timeline") or features.get("fragment_instance_lifecycle")),
                section_label="Profile timing sections",
            ),
            "resource_trace": classic_text_section_mapping(
                bool(features.get("resource_trace")),
                section_label="Resource trace sections",
            ),
            "profile_counters": section_mapping(
                ProfileSectionMapping.SUPPORTED,
                "classic_text_profile_mapped",
                "Classic text profile counters are mapped for the current analyzer slices.",
            ),
            "client_fetch_tail": section_mapping(
                ProfileSectionMapping.SUPPORTED,
                "classic_text_profile_mapped",
                "Client-fetch counters are mapped for classic text profiles when stable counters are present.",
            ),
            "memory_pressure": section_mapping(
                ProfileSectionMapping.SUPPORTED,
                "classic_text_profile_mapped",
                "Spill and scratch counters are mapped for classic text profiles when stable counters are present.",
            ),
        }

    if dialect == ProfileDialect.CLASSIC_JSON and features.get("json_mapped_counter_count"):
        limited = section_mapping(
            ProfileSectionMapping.LIMITED,
            "classic_json_allowlisted_counter_mapping",
            (
                "Classic JSON profile counters are allowlisted and mapped only as limited "
                "context; profile-derived primary or root-cause claims stay disabled."
            ),
        )
        unsupported = unsupported_section_mapping(
            "classic_json_profile_partially_mapped",
            "This classic JSON profile section is not mapped by the current analyzer slice.",
        )
        return {
            "profile_resources": unsupported,
            "profile_timings": unsupported,
            "resource_trace": unsupported,
            "profile_counters": limited,
            "client_fetch_tail": limited,
            "memory_pressure": limited,
        }

    if dialect == ProfileDialect.CLASSIC_JSON:
        return unsupported_profile_section_mappings(
            "classic_json_profile_unmapped",
            "Classic JSON profile sections are not mapped by the current analyzer slice.",
        )

    if dialect == ProfileDialect.EXPERIMENTAL_V2:
        return unsupported_profile_section_mappings(
            "experimental_profile_v2_unmapped",
            (
                "Experimental profile-v2 sections are not interpreted by this analyzer "
                "slice unless a section is explicitly mapped."
            ),
        )

    if dialect == ProfileDialect.CLASSIC_THRIFT:
        return unsupported_profile_section_mappings(
            "classic_thrift_profile_unmapped",
            "Classic Thrift profile sections are not mapped by the current analyzer slice.",
        )

    return unsupported_profile_section_mappings(
        "profile_dialect_unknown",
        "Profile-derived sections are not interpreted because the profile dialect is unknown.",
    )


def classic_text_section_mapping(observed: bool, *, section_label: str) -> dict[str, str]:
    if observed:
        return section_mapping(
            ProfileSectionMapping.SUPPORTED,
            "classic_text_section_observed",
            f"{section_label} are mapped for classic text profiles.",
        )
    return section_mapping(
        ProfileSectionMapping.NOT_OBSERVED,
        "classic_text_section_not_observed",
        f"{section_label} were not observed in this profile.",
    )


def unsupported_profile_section_mappings(reason: str, summary: str) -> dict[str, dict[str, str]]:
    return {
        section_id: unsupported_section_mapping(reason, summary)
        for section_id in PROFILE_SECTION_ORDER
    }


def unsupported_section_mapping(reason: str, summary: str) -> dict[str, str]:
    return section_mapping(ProfileSectionMapping.UNSUPPORTED, reason, summary)


def section_mapping(
    state: ProfileSectionMapping,
    reason: str,
    summary: str,
) -> dict[str, str]:
    return {"state": state.value, "reason": reason, "summary": summary}


def profile_section_mapping(
    profile_format: dict[str, Any] | None,
    section_id: str,
) -> dict[str, str]:
    profile = profile_format if isinstance(profile_format, dict) else {}
    mappings = profile.get("section_mappings")
    mappings = mappings if isinstance(mappings, dict) else {}
    mapping = mappings.get(section_id)
    if isinstance(mapping, dict):
        return {
            "state": str(mapping.get("state") or "unsupported"),
            "reason": str(mapping.get("reason") or "profile_section_unmapped"),
            "summary": str(
                mapping.get("summary")
                or "Profile section is not mapped by the current analyzer slice."
            ),
        }
    return unsupported_section_mapping(
        "profile_section_mapping_missing",
        "Profile section mapping was unavailable; profile-derived interpretation is disabled.",
    )


def profile_section_mapping_state(
    profile_format: dict[str, Any] | None,
    section_id: str,
) -> str:
    return profile_section_mapping(profile_format, section_id)["state"]


def profile_source_capabilities(
    detection: ProfileDialectDetection,
    features: dict[str, bool | int],
    context: dict[str, Any],
    primary_policy: PrimaryBottleneckPolicy,
) -> dict[str, str | int]:
    """Return safe observed capability facts for profile ingestion.

    These are feature-detected observations from the collected artifact and
    collector metadata. They are not promises that a given Impala version or
    Web UI endpoint supports the same capability in general.
    """

    response_format = safe_profile_response_format(context.get("profile_response_format"))
    json_payload = json_profile_payload_status(detection, features, response_format)
    text_payload = text_profile_payload_status(detection, response_format)
    return {
        "profile_response_format": response_format,
        "profile_fetch_attempt_count": nonnegative_int(context.get("profile_fetch_attempt_count")),
        "json_profile_probe": enabled_status(context.get("profile_json_probe_enabled")),
        "profile_docs_probe": enabled_status(context.get("profile_docs_probe_enabled")),
        "profile_docs_fetch_attempt_count": nonnegative_int(
            context.get("profile_docs_fetch_attempt_count")
        ),
        "json_profile_payload": json_payload,
        "text_profile_payload": text_payload,
        "primary_profile_routing": primary_policy.value,
    }


def safe_profile_response_format(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in SAFE_PROFILE_ENDPOINT_FORMATS else "unknown"


def enabled_status(value: object) -> str:
    if isinstance(value, bool):
        return "enabled" if value else "not_configured"
    return "unknown"


def nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def json_profile_payload_status(
    detection: ProfileDialectDetection,
    features: dict[str, bool | int],
    response_format: str,
) -> str:
    if response_format == "json" or detection.dialect == ProfileDialect.CLASSIC_JSON:
        return "mapped_limited" if features.get("json_mapped_counter_count") else "observed"
    if detection.reasons == ("json_wrapped_classic_text_profile",):
        return "wrapped_text_observed"
    if response_format in {"text", "default"}:
        return "not_selected"
    return "unknown"


def text_profile_payload_status(
    detection: ProfileDialectDetection,
    response_format: str,
) -> str:
    if detection.dialect == ProfileDialect.CLASSIC_TEXT:
        if detection.reasons == ("json_wrapped_classic_text_profile",):
            return "wrapped_text_observed"
        return "observed"
    if response_format in {"text", "default"}:
        return "selected_but_unmapped"
    if response_format == "json":
        return "not_selected"
    return "unknown"
