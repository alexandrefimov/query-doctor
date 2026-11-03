"""Collect one profile from direct Impala daemon web endpoints."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from query_doctor.analyzer.profile_counter_registry import write_profile_counter_registry_context
from query_doctor.cm.models import CMClientError, CMQuerySummary, OutputError
from query_doctor.cm.profile_collection import write_collected_case
from query_doctor.recent.profile_statement_identity import profile_statement_fingerprint
from query_doctor.cm.profile_parsing import (
    extract_statement_from_profile_text,
    merge_profile_summary_metadata,
)
from query_doctor.impala.daemon_identity import fetch_impala_daemon_identity, identity_metadata
from query_doctor.impala.admission_context import (
    DEFAULT_MAX_ADMISSION_CONTEXT_BYTES,
    fetch_impala_admission_context,
    write_admission_context,
)
from query_doctor.impala.profile_docs import (
    DEFAULT_MAX_PROFILE_DOCS_BYTES,
    fetch_impala_profile_docs_context,
)
from query_doctor.impala.profile_source import (
    DEFAULT_IMPALA_PROFILE_PORT,
    DEFAULT_IMPALA_PROFILE_SCHEME,
    DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
    fetch_impala_profile_text,
)
from query_doctor.metadata_source_tables import write_metadata_source_tables
from query_doctor.prometheus.timeseries import (
    DEFAULT_MAX_PROMETHEUS_POINTS,
    DEFAULT_PROMETHEUS_METRICS_PROFILE,
    DEFAULT_PROMETHEUS_STEP_SEC,
    DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
    DEFAULT_PROMETHEUS_TIMEOUT_SEC,
    PROMETHEUS_METRICS_PROFILE_CHOICES,
    collect_prometheus_timeseries_context,
)
from query_doctor.safety.redaction import sanitize_adapter_error_message


DEFAULT_MAX_PROFILE_BYTES = 50 * 1024 * 1024


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect one Apache Impala runtime profile from configured impalad "
            "debug web endpoints. This does not execute SQL."
        )
    )
    parser.add_argument("--query-id", required=True, help="Explicit Impala query id.")
    parser.add_argument(
        "--host", action="append", default=[], help="impalad web host or host:port."
    )
    parser.add_argument(
        "--port",
        type=positive_int,
        default=DEFAULT_IMPALA_PROFILE_PORT,
        help=f"impalad web port for hosts without an explicit port. Default: {DEFAULT_IMPALA_PROFILE_PORT}.",
    )
    parser.add_argument(
        "--scheme",
        choices=("http", "https"),
        default=DEFAULT_IMPALA_PROFILE_SCHEME,
        help=f"impalad web scheme. Default: {DEFAULT_IMPALA_PROFILE_SCHEME}.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=positive_int,
        default=DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC,
        help=f"Timeout per impalad profile endpoint. Default: {DEFAULT_IMPALA_PROFILE_TIMEOUT_SEC}.",
    )
    parser.add_argument(
        "--prefer-json-profile",
        action="store_true",
        help=(
            "Try the impalad JSON profile endpoint before text. Text endpoints remain "
            "the fallback for older Impala versions."
        ),
    )
    parser.add_argument(
        "--max-profile-bytes",
        type=positive_int,
        default=DEFAULT_MAX_PROFILE_BYTES,
        help=f"Maximum profile response bytes. Default: {DEFAULT_MAX_PROFILE_BYTES}.",
    )
    parser.add_argument(
        "--collect-profile-docs",
        action="store_true",
        help=(
            "Collect safe profile counter stability labels from impalad /profile_docs/?json, "
            "falling back to /profile_docs. Unavailable or old endpoints are treated as "
            "unknown and do not fail collection."
        ),
    )
    parser.add_argument(
        "--max-profile-docs-bytes",
        type=positive_int,
        default=DEFAULT_MAX_PROFILE_DOCS_BYTES,
        help=f"Maximum /profile_docs response bytes. Default: {DEFAULT_MAX_PROFILE_DOCS_BYTES}.",
    )
    parser.add_argument(
        "--collect-admission-context",
        action="store_true",
        help=(
            "Collect bounded aggregate pool context from impalad /admission?json. "
            "Unavailable or old endpoints are treated as unknown and do not fail collection."
        ),
    )
    parser.add_argument(
        "--max-admission-context-bytes",
        type=positive_int,
        default=DEFAULT_MAX_ADMISSION_CONTEXT_BYTES,
        help=(
            "Maximum /admission?json response bytes. "
            f"Default: {DEFAULT_MAX_ADMISSION_CONTEXT_BYTES}."
        ),
    )
    parser.add_argument("--out", type=Path, required=True, help="Output corpus directory.")
    parser.add_argument(
        "--redact", action="store_true", help="Required for real profile collection."
    )
    parser.add_argument(
        "--redact-identifiers", action="store_true", help="Redact usernames and pools."
    )
    parser.add_argument(
        "--no-redact-hosts",
        action="store_false",
        default=True,
        dest="redact_hosts",
        help="Preserve hostnames in local artifacts. Do not use for shared outputs.",
    )
    parser.add_argument(
        "--metadata-source-tables-out",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--prometheus-url",
        help="Prometheus base URL for bounded runtime metric summaries. No credentials in the URL.",
    )
    parser.add_argument(
        "--collect-prometheus-timeseries",
        action="store_true",
        default=None,
        help=(
            "Collect bounded allowlisted Prometheus runtime metric summaries. "
            "Providing --prometheus-url also enables this unless --no-collect-prometheus-timeseries is set."
        ),
    )
    parser.add_argument(
        "--no-collect-prometheus-timeseries",
        action="store_false",
        dest="collect_prometheus_timeseries",
        help="Disable Prometheus runtime metric summaries.",
    )
    parser.add_argument(
        "--prometheus-metrics-profile",
        choices=PROMETHEUS_METRICS_PROFILE_CHOICES,
        default=DEFAULT_PROMETHEUS_METRICS_PROFILE,
        help=f"Prometheus metric-name compatibility profile. Default: {DEFAULT_PROMETHEUS_METRICS_PROFILE}.",
    )
    parser.add_argument(
        "--prometheus-step-sec",
        type=positive_int,
        default=DEFAULT_PROMETHEUS_STEP_SEC,
        help=f"Prometheus query_range step in seconds. Default: {DEFAULT_PROMETHEUS_STEP_SEC}.",
    )
    parser.add_argument(
        "--prometheus-timeseries-padding-sec",
        type=non_negative_int,
        default=DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC,
        help=(
            "Seconds to pad before query start and after query end for Prometheus metrics. "
            f"Default: {DEFAULT_PROMETHEUS_TIMESERIES_PADDING_SEC}."
        ),
    )
    parser.add_argument(
        "--max-timeseries-bytes",
        type=positive_int,
        default=2 * 1024 * 1024,
        help="Maximum bytes per Prometheus query_range response. Default: 2097152.",
    )
    parser.add_argument(
        "--max-timeseries-points",
        type=positive_int,
        default=DEFAULT_MAX_PROMETHEUS_POINTS,
        help=f"Maximum numeric data points to summarize per Prometheus query. Default: {DEFAULT_MAX_PROMETHEUS_POINTS}.",
    )
    parser.add_argument(
        "--prometheus-timeout-sec",
        type=positive_int,
        default=DEFAULT_PROMETHEUS_TIMEOUT_SEC,
        help=f"Timeout per Prometheus request. Default: {DEFAULT_PROMETHEUS_TIMEOUT_SEC}.",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    opener=None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not args.redact:
        print(
            "[Impala profile collector] ERROR: real Impala collection requires --redact.",
            file=sys.stderr,
        )
        return 3
    collect_prometheus = (
        bool(args.prometheus_url)
        if args.collect_prometheus_timeseries is None
        else bool(args.collect_prometheus_timeseries)
    )
    if collect_prometheus and not args.prometheus_url:
        print(
            "[Impala profile collector] ERROR: --collect-prometheus-timeseries requires --prometheus-url.",
            file=sys.stderr,
        )
        return 3
    metadata_source_tables_out = resolve_metadata_source_tables_out(args)
    if metadata_source_tables_out is False:
        return 3
    try:
        fetch_kwargs = {
            "query_id": args.query_id,
            "hosts": args.host,
            "port": args.port,
            "scheme": args.scheme,
            "timeout_sec": args.timeout_sec,
            "max_profile_bytes": args.max_profile_bytes,
            "prefer_json": args.prefer_json_profile,
        }
        if opener is not None:
            fetch_kwargs["opener"] = opener
        result = fetch_impala_profile_text(**fetch_kwargs)
        summary = CMQuerySummary(query_id=result.query_id)
        summary, profile_metadata_warnings = merge_profile_summary_metadata(
            summary, result.profile_text
        )
        profile_metadata_warnings = [
            warning.replace("CM profile text", "Impala profile text")
            for warning in profile_metadata_warnings
        ]
        if not summary.statement:
            profile_statement = extract_statement_from_profile_text(result.profile_text)
            if profile_statement:
                summary = replace(summary, statement=profile_statement)
                profile_metadata_warnings.append("Impala profile text statement metadata collected")
        identity_kwargs = {
            "hosts": args.host,
            "port": args.port,
            "scheme": args.scheme,
            "timeout_sec": args.timeout_sec,
        }
        if opener is not None:
            identity_kwargs["opener"] = opener
        try:
            identity = fetch_impala_daemon_identity(**identity_kwargs)
        except CMClientError:
            identity = None
        warnings = [
            "collected by Query Doctor direct Impala profile collector",
            "source query id preserved",
            "redaction enabled",
            "Impala daemon profile endpoint",
            f"attempted impalad profile endpoint count: {result.attempted_endpoints}",
            f"selected impalad profile endpoint format: {result.profile_endpoint_format}",
            "analyzer/report were not run automatically",
        ]
        warnings.extend(profile_metadata_warnings)
        if identity is None:
            warnings.append("Impala daemon identity unavailable")
        profile_counter_registry_context = None
        profile_docs_attempted = 0
        if args.collect_profile_docs:
            profile_docs_kwargs = {
                "hosts": args.host,
                "port": args.port,
                "scheme": args.scheme,
                "timeout_sec": args.timeout_sec,
                "max_profile_docs_bytes": args.max_profile_docs_bytes,
                "impala_version": identity.version if identity is not None else None,
            }
            if opener is not None:
                profile_docs_kwargs["opener"] = opener
            profile_docs_result = fetch_impala_profile_docs_context(**profile_docs_kwargs)
            profile_counter_registry_context = profile_docs_result.context
            profile_docs_attempted = profile_docs_result.attempted_endpoints
            if profile_counter_registry_context.get("status") == "available":
                warnings.append("Impala profile counter stability docs collected")
            else:
                warnings.append("Impala profile counter stability docs unavailable")
        admission_context = None
        admission_context_attempted = 0
        if args.collect_admission_context:
            admission_kwargs = {
                "hosts": args.host,
                "port": args.port,
                "scheme": args.scheme,
                "timeout_sec": args.timeout_sec,
                "max_admission_context_bytes": args.max_admission_context_bytes,
                "target_pool": summary.pool,
            }
            if opener is not None:
                admission_kwargs["opener"] = opener
            admission_result = fetch_impala_admission_context(**admission_kwargs)
            admission_context = admission_result.context
            admission_context_attempted = admission_result.attempted_endpoints
            if admission_context.get("status") == "available":
                warnings.append("Impala admission aggregate context collected")
            else:
                warnings.append("Impala admission aggregate context unavailable")
        runtime_metrics_context = None
        if collect_prometheus:
            runtime_metrics_context = collect_prometheus_timeseries_context(
                summary,
                prometheus_url=args.prometheus_url,
                metrics_profile=args.prometheus_metrics_profile,
                padding_sec=args.prometheus_timeseries_padding_sec,
                step_sec=args.prometheus_step_sec,
                max_response_bytes=args.max_timeseries_bytes,
                max_points=args.max_timeseries_points,
                timeout_sec=args.prometheus_timeout_sec,
                opener=opener,
            )
            if runtime_metrics_context.get("available"):
                warnings.append("Prometheus runtime metrics context collected")
            else:
                warnings.append("Prometheus runtime metrics context unavailable")
        case_dir = write_collected_case(
            args.out,
            summary,
            profile_digest_text=result.profile_text,
            runtime_metrics_context=runtime_metrics_context,
            extra_metadata={
                "profile_statement_fingerprint": profile_statement_fingerprint(
                    result.profile_text, query_id=summary.query_id
                ),
                "profile_source": "impala_daemon",
                "profile_source_label": "Impala daemon profile endpoint",
                "profile_response_format": result.profile_endpoint_format,
                "profile_fetch_attempt_count": result.attempted_endpoints,
                "profile_json_probe_enabled": bool(args.prefer_json_profile),
                "profile_docs_probe_enabled": bool(args.collect_profile_docs),
                "profile_docs_fetch_attempt_count": profile_docs_attempted,
                "admission_context_probe_enabled": bool(args.collect_admission_context),
                "admission_context_fetch_attempt_count": admission_context_attempted,
                **identity_metadata(identity),
            },
            warnings=warnings,
            redact=True,
            redact_identifiers=args.redact_identifiers,
            redact_hosts=args.redact_hosts,
        )
        if isinstance(metadata_source_tables_out, Path):
            write_metadata_source_tables(metadata_source_tables_out, summary.statement)
        if profile_counter_registry_context is not None:
            write_profile_counter_registry_context(case_dir, profile_counter_registry_context)
        if admission_context is not None:
            write_admission_context(case_dir, admission_context)
    except (CMClientError, OutputError, OSError) as exc:
        print("[Impala profile collector] Collection result: FAILED", file=sys.stderr)
        print(
            f"Single-query Impala profile collection failed: {sanitize_adapter_error_message(exc)}",
            file=sys.stderr,
        )
        return 4

    print("[Impala profile collector] Collection result: OK")
    print("Collected count: 1")
    print(f"Output case directory: {case_dir}")
    print(f"Profile text length: {len(result.profile_text)}")
    print(f"Selected profile endpoint format: {result.profile_endpoint_format}")
    print("Redaction: enabled")
    print(f"Host redaction: {'enabled' if args.redact_hosts else 'disabled'}")
    print(f"Max profile bytes: {args.max_profile_bytes}")
    if collect_prometheus:
        print("Prometheus runtime metrics context: enabled")
        print(f"Prometheus metrics profile: {args.prometheus_metrics_profile}")
    if args.collect_profile_docs:
        print("Profile counter docs context: enabled")
        print(f"Attempted profile docs endpoint count: {profile_docs_attempted}")
    if args.collect_admission_context:
        print("Admission aggregate context: enabled")
        print(f"Attempted admission endpoint count: {admission_context_attempted}")
    print("Raw provider output was not printed to stdout.")
    return 0


def resolve_metadata_source_tables_out(args: argparse.Namespace) -> Path | bool | None:
    if args.metadata_source_tables_out is None:
        return None
    source_tables_path = args.metadata_source_tables_out.resolve(strict=False)
    output_root = args.out.resolve(strict=False)
    try:
        source_tables_path.relative_to(output_root)
    except ValueError:
        print(
            "[Impala profile collector] ERROR: --metadata-source-tables-out must be inside --out.",
            file=sys.stderr,
        )
        return False
    return source_tables_path


if __name__ == "__main__":
    raise SystemExit(main())
