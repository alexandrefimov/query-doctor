"""Raw-free deployment readiness summary for Query Doctor web deployments."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from query_doctor.source_visibility import SOURCE_VISIBILITY_OWNER_RAW
from query_doctor.trino.support_mode import trino_support_mode_enabled
from query_doctor.web.models import (
    WebClusterConfig,
    WebSettings,
    metadata_collection_configured,
)


READINESS_KIND = "query_doctor_deployment_readiness_v1"
CHECK_READY = "ready"
CHECK_INFO = "info"
CHECK_WARNING = "warning"
CHECK_BLOCKED = "blocked"
READINESS_STATUS_ORDER = {
    CHECK_READY: 0,
    CHECK_INFO: 1,
    CHECK_WARNING: 2,
    CHECK_BLOCKED: 3,
}


def deployment_readiness_payload(settings: WebSettings) -> dict[str, Any]:
    checks = _readiness_checks(settings)
    return {
        "kind": READINESS_KIND,
        "status": _overall_status(checks),
        "mode": _deployment_mode(settings),
        "web": {
            "bind_scope": "nonlocal_explicit" if settings.allow_nonlocal_web_bind else "local_only",
            "public_demo": settings.public_demo,
            "post_actions": "disabled" if settings.public_demo else "enabled",
            "profile_upload": "disabled_public_demo"
            if settings.public_demo
            else "enabled_local_private",
            "llm_actions": "disabled" if settings.no_llm else "explicit_only",
        },
        "storage": {
            "case_storage": "configured_hidden",
            "read_only_results": bool(settings.batch_summary or settings.corpus_summary),
            "public_demo_pack": settings.public_demo,
        },
        "sources": _source_summary(settings),
        "security": {
            "source_visibility": settings.source_visibility,
            "owner_raw_source": "enabled" if settings.owner_raw_source_enabled else "disabled",
            "viewer_identity_header": "configured"
            if settings.viewer_identity_header
            else "not_configured",
            "native_auth": "not_implemented",
            "rbac": "not_implemented",
            "sql_execution": False,
            "raw_output": False,
        },
        "probes": {
            "liveness": "/healthz",
            "readiness": "/readyz",
            "deployment_json": "/deployment/readiness.json",
        },
        "checks": checks,
    }


def deployment_readiness_json(settings: WebSettings) -> str:
    return json.dumps(deployment_readiness_payload(settings), sort_keys=True) + "\n"


def format_deployment_readiness_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Query Doctor deployment readiness: {payload.get('status', 'unknown')}",
        f"mode: {payload.get('mode', 'unknown')}",
    ]
    web = payload.get("web")
    if isinstance(web, dict):
        lines.append(f"bind_scope: {web.get('bind_scope', 'unknown')}")
        lines.append(f"post_actions: {web.get('post_actions', 'unknown')}")
        lines.append(f"profile_upload: {web.get('profile_upload', 'unknown')}")
    sources = payload.get("sources")
    if isinstance(sources, dict):
        counts = sources.get("counts")
        if isinstance(counts, dict):
            formatted_counts = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
            lines.append(f"sources: {formatted_counts or 'none'}")
    checks = payload.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue
            lines.append(
                "[{status}] {check_id}: {summary}".format(
                    status=check.get("status", "unknown"),
                    check_id=check.get("id", "unknown"),
                    summary=check.get("summary", ""),
                )
            )
    return "\n".join(lines) + "\n"


def _deployment_mode(settings: WebSettings) -> str:
    if settings.public_demo:
        return "public_demo"
    if settings.batch_summary or settings.corpus_summary:
        return "read_only_results"
    return "configured_private"


def _source_summary(settings: WebSettings) -> dict[str, Any]:
    if settings.clusters:
        clusters = settings.clusters
    elif settings.public_demo:
        clusters = ()
    else:
        clusters = (_active_settings_as_cluster(settings),)
    counts = Counter()
    for cluster in clusters:
        if _cluster_is_cm(cluster):
            counts["cm"] += 1
        if cluster.query_profile_source == "impala" and cluster.impala_profile_hosts:
            counts["direct_impala"] += 1
        if cluster.manual_profile_dir is not None:
            counts["manual_profile_inbox"] += 1
        if trino_support_mode_enabled(cluster.trino_support_mode) or cluster.trino_beta_enabled:
            counts["trino_local"] += 1
        if metadata_collection_configured(cluster):
            counts["metadata"] += 1
        if cluster.collect_prometheus_timeseries:
            counts["prometheus"] += 1
    if settings.public_demo:
        counts["synthetic_demo"] += 1
    if settings.batch_summary or settings.corpus_summary:
        counts["read_only_results"] += 1
    return {
        "active_engine": settings.selected_engine,
        "configured_source_count": len(clusters),
        "counts": dict(sorted(counts.items())),
    }


def _active_settings_as_cluster(settings: WebSettings) -> WebClusterConfig:
    return WebClusterConfig(
        key="active",
        label="Active source",
        cm_url=settings.cm_url,
        cm_cluster=settings.cm_cluster,
        cm_service=settings.cm_service,
        cm_username=settings.cm_username,
        manual_profile_dir=settings.manual_profile_dir,
        query_profile_source=settings.query_profile_source,
        impala_profile_hosts=settings.impala_profile_hosts,
        collect_prometheus_timeseries=settings.collect_prometheus_timeseries,
        metadata_source=settings.metadata_source,
        metadata_hms_postgres_dsn_env=settings.metadata_hms_postgres_dsn_env,
        metadata_coordinator=settings.metadata_coordinator,
        source_visibility=settings.source_visibility,
        trino_support_mode=settings.trino_support_mode,
        trino_beta_enabled=settings.trino_beta_enabled,
        trino_coordinator_url=settings.trino_coordinator_url,
        trino_query_info_source_contract=settings.trino_query_info_source_contract,
    )


def _cluster_is_cm(cluster: WebClusterConfig) -> bool:
    return cluster.query_profile_source != "impala" and any(
        (cluster.cm_url, cluster.cm_cluster, cluster.cm_service)
    )


def _readiness_checks(settings: WebSettings) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "probe_routes",
            CHECK_READY,
            "raw-free liveness and readiness probes are available",
        )
    )
    if settings.public_demo:
        checks.append(
            _check(
                "public_demo_read_only",
                CHECK_READY,
                "public demo disables POST actions and live source configuration",
            )
        )
    else:
        checks.append(
            _check(
                "private_actions_explicit",
                CHECK_INFO,
                "private web actions require explicit user submission",
            )
        )
    if settings.allow_nonlocal_web_bind and settings.public_demo:
        checks.append(
            _check(
                "nonlocal_bind_public_demo",
                CHECK_READY,
                "non-local bind is limited to read-only public demo mode",
            )
        )
    elif settings.allow_nonlocal_web_bind:
        checks.append(
            _check(
                "nonlocal_bind",
                CHECK_WARNING,
                "non-local bind is explicit; put shared access behind a trusted front door",
            )
        )
    else:
        checks.append(_check("local_bind", CHECK_READY, "web bind is local-only by default"))
    if _settings_include_owner_raw(settings):
        if settings.allow_nonlocal_web_bind and not settings.viewer_identity_header:
            checks.append(
                _check(
                    "owner_raw_front_door",
                    CHECK_BLOCKED,
                    "owner_raw with non-local bind requires a trusted viewer identity header",
                )
            )
        elif settings.allow_nonlocal_web_bind:
            checks.append(
                _check(
                    "owner_raw_front_door",
                    CHECK_WARNING,
                    "owner_raw shared access still requires the D3 trusted front-door review",
                )
            )
        else:
            checks.append(
                _check(
                    "owner_raw_local_scope",
                    CHECK_INFO,
                    "owner_raw source reveal remains isolated and kill-switch gated",
                )
            )
    else:
        checks.append(
            _check("source_visibility", CHECK_READY, "trusted browser surfaces stay raw-free")
        )
    if settings.no_llm:
        checks.append(_check("llm_actions", CHECK_READY, "LLM actions are disabled"))
    else:
        checks.append(
            _check(
                "llm_actions",
                CHECK_INFO,
                "LLM reports and optimizer jobs remain explicit selected-case actions",
            )
        )
    if settings.public_demo:
        checks.append(
            _check(
                "profile_upload",
                CHECK_READY,
                "profile upload is disabled in public demo mode",
            )
        )
    else:
        checks.append(
            _check(
                "profile_upload",
                CHECK_INFO,
                "one-profile upload is available only for local/private web sessions",
            )
        )
    checks.append(
        _check(
            "kubernetes_boundary",
            CHECK_INFO,
            "Kubernetes support is web deployment plus synthetic self-test, not SQL execution",
        )
    )
    return checks


def _settings_include_owner_raw(settings: WebSettings) -> bool:
    if settings.source_visibility == SOURCE_VISIBILITY_OWNER_RAW:
        return True
    return any(
        cluster.source_visibility == SOURCE_VISIBILITY_OWNER_RAW for cluster in settings.clusters
    )


def _check(check_id: str, status: str, summary: str) -> dict[str, str]:
    return {"id": check_id, "status": status, "summary": summary}


def _overall_status(checks: list[dict[str, str]]) -> str:
    highest = max(READINESS_STATUS_ORDER.get(check["status"], 0) for check in checks)
    if highest >= READINESS_STATUS_ORDER[CHECK_BLOCKED]:
        return CHECK_BLOCKED
    if highest >= READINESS_STATUS_ORDER[CHECK_WARNING]:
        return CHECK_WARNING
    return CHECK_READY
