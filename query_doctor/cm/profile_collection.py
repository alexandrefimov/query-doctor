"""CM profile collection orchestration over injected fetchers."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from query_doctor.case_metadata import legacy_cm_metadata_path, query_metadata_path
from query_doctor.cm.client import (
    build_cm_query_filter_expression,
    effective_query_summary_page_size,
)
from query_doctor.cm.models import (
    CMClientError,
    CMCollectionResult,
    CMProfileTextFetcher,
    CMQueryFilters,
    CMQueryPageFetcher,
    CMQuerySummary,
    OutputError,
)
from query_doctor.metadata_source_tables import extract_metadata_source_tables
from query_doctor.safety.redaction import (
    redact_metadata,
    redact_profile_text,
    redacted_table_labels,
    sanitize_text_for_log,
)


REPO_DIR = Path(__file__).resolve().parents[2]


def cm_query_summary_metadata(summary: CMQuerySummary) -> dict[str, object]:
    metadata: dict[str, object] = {
        "admission_result": summary.admission_result,
        "admission_wait_ms": summary.admission_wait_ms,
        "bytes_read": summary.bytes_read,
        "bytes_sent": summary.bytes_sent,
        "duration_ms": summary.duration_ms,
        "duration_sec": summary.duration_sec,
        "end_time": summary.end_time,
        "memory_aggregate_peak": summary.memory_aggregate_peak,
        "memory_per_node_peak": summary.memory_per_node_peak,
        "pool": summary.pool,
        "query_id": summary.query_id,
        "query_state": summary.query_state,
        "query_type": summary.query_type,
        "rows_produced": summary.rows_produced,
        "start_time": summary.start_time,
        "status": summary.status,
        "user": summary.user,
    }
    if summary.statement:
        metadata["statement"] = summary.statement
    return {key: value for key, value in metadata.items() if value is not None}


def safe_case_slug(query_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", query_id).strip("._-")
    if not slug:
        raise OutputError("Refusing to create a case directory from an empty query id.")
    return slug


def ensure_child_path(root: Path, child: Path) -> Path:
    root_resolved = root.resolve(strict=False)
    child_resolved = child.resolve(strict=False)
    try:
        child_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise OutputError(f"Refusing to write outside output root: {child}") from exc
    return child_resolved


def case_dir_for_query(out_dir: Path, summary: CMQuerySummary) -> Path:
    root = out_dir.resolve(strict=False)
    if root == Path(root.anchor):
        raise OutputError("Refusing to use filesystem root as corpus output root.")
    if root == REPO_DIR:
        raise OutputError("Refusing to use the current repository root as corpus output root.")
    return ensure_child_path(root, root / safe_case_slug(summary.query_id))


def write_collected_case(
    out_dir: Path,
    summary: CMQuerySummary,
    *,
    profile_digest_text: str,
    cm_timeseries_context: dict[str, object] | None = None,
    runtime_metrics_context: dict[str, object] | None = None,
    extra_metadata: Mapping[str, object] | None = None,
    warnings: Iterable[str] = (),
    secrets: Iterable[str] = (),
    redact: bool = False,
    redact_identifiers: bool = False,
    redact_hosts: bool = True,
) -> Path:
    """Write one already-collected synthetic CM case under out_dir.

    This helper performs filesystem layout only. It does not collect profiles,
    call Cloudera Manager, or enable CLI collection.
    """
    case_dir = case_dir_for_query(out_dir, summary)
    if case_dir.exists():
        raise OutputError(f"Refusing to overwrite existing case directory: {case_dir}")

    metadata = cm_query_summary_metadata(summary)
    if extra_metadata:
        metadata.update({key: value for key, value in extra_metadata.items() if value is not None})
    digest_text = profile_digest_text
    if redact:
        metadata = redact_metadata(
            metadata,
            redact_identifiers=redact_identifiers,
            redact_hosts=redact_hosts,
        )
        # Tables are numbered by their position in the list metadata collection
        # receives, so its per-table facts stay matched to the redacted SQL.
        digest_text = redact_profile_text(
            profile_digest_text,
            redact_identifiers=redact_identifiers,
            redact_hosts=redact_hosts,
            table_labels=redacted_table_labels(extract_metadata_source_tables(summary.statement)),
        )

    metadata_text = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    sanitized_warnings = [sanitize_text_for_log(warning, secrets=secrets) for warning in warnings]
    warnings_text = "\n".join(sanitized_warnings).strip()
    if warnings_text:
        warnings_text += "\n"

    case_dir.mkdir(parents=True, exist_ok=False)
    (case_dir / "profile_digest.md").write_text(digest_text, encoding="utf-8")
    query_metadata_path(case_dir).write_text(metadata_text, encoding="utf-8")
    legacy_cm_metadata_path(case_dir).write_text(metadata_text, encoding="utf-8")
    if cm_timeseries_context is not None:
        timeseries_text = json.dumps(cm_timeseries_context, indent=2, sort_keys=True) + "\n"
        (case_dir / "cm_timeseries_context.json").write_text(timeseries_text, encoding="utf-8")
    canonical_runtime_context = (
        runtime_metrics_context if runtime_metrics_context is not None else cm_timeseries_context
    )
    if canonical_runtime_context is not None:
        runtime_metrics_text = (
            json.dumps(canonical_runtime_context, indent=2, sort_keys=True) + "\n"
        )
        (case_dir / "runtime_metrics_context.json").write_text(
            runtime_metrics_text, encoding="utf-8"
        )
    (case_dir / "collection_warnings.txt").write_text(warnings_text, encoding="utf-8")
    return case_dir


def collect_and_write_cm_profiles(
    *,
    filters: CMQueryFilters,
    out_dir: Path,
    fetch_summary_page: CMQueryPageFetcher,
    fetch_profile_text: CMProfileTextFetcher,
    redact: bool = False,
    redact_identifiers: bool = False,
    secrets: Iterable[str] = (),
) -> CMCollectionResult:
    """Collect already-selected CM profiles through injected helpers.

    This orchestrates mockable helpers only. It does not create HTTP clients,
    call Cloudera Manager directly, run analyzers, or generate reports.
    """
    case_dirs: list[Path] = []
    failures: list[str] = []
    summaries, warnings = collect_query_summaries(
        filters,
        fetch_summary_page,
        secrets=secrets,
    )
    warnings = list(warnings)

    if not summaries and warnings:
        failures.extend(f"query summary collection: {warning}" for warning in warnings)

    for summary in summaries:
        try:
            profile_text = fetch_profile_text(summary)
            case_dir = write_collected_case(
                out_dir,
                summary,
                profile_digest_text=profile_text,
                secrets=secrets,
                redact=redact,
                redact_identifiers=redact_identifiers,
            )
        except (CMClientError, OutputError, OSError) as exc:
            message = sanitize_text_for_log(exc, secrets=secrets)
            failures.append(f"{summary.query_id}: {message}")
            continue
        case_dirs.append(case_dir)

    return CMCollectionResult(
        collected_count=len(case_dirs),
        failed_count=len(failures),
        skipped_count=0,
        case_dirs=case_dirs,
        warnings=warnings,
        failures=failures,
    )


def collect_query_summaries(
    filters: CMQueryFilters,
    fetch_page: CMQueryPageFetcher,
    *,
    secrets: Iterable[str] = (),
) -> tuple[list[CMQuerySummary], list[str]]:
    """Iterate CM query summary pages through an injected transport."""
    collected: list[CMQuerySummary] = []
    warnings: list[str] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    inspected = 0

    while inspected < filters.limit and len(collected) < filters.limit:
        remaining = filters.limit - inspected
        page_limit = effective_query_summary_page_size(filters, remaining)
        page_filters = (
            filters
            if filters.page_size is None and page_limit == filters.limit
            else replace(filters, page_size=page_limit)
        )
        try:
            page = fetch_page(page_filters, page_token)
        except CMClientError as exc:
            warnings.append(sanitize_text_for_log(exc, secrets=secrets))
            break

        warnings.extend(
            sanitize_text_for_log(warning, secrets=secrets) for warning in page.warnings
        )
        inspected += len(page.items)

        for item in page.items:
            if filters.query_id and item.query_id != filters.query_id:
                continue
            collected.append(item)
            if len(collected) >= filters.limit:
                break

        if len(collected) >= filters.limit:
            break
        if len(page.items) < page_limit and not page.next_page_token:
            break
        next_page_token = page.next_page_token or next_numeric_offset(page_token, page_limit)
        if not next_page_token:
            break
        if next_page_token in seen_tokens:
            warnings.append("Stopped pagination because a repeated page token was returned.")
            break
        seen_tokens.add(next_page_token)
        page_token = next_page_token

    return collected, warnings


def next_numeric_offset(page_token: str | None, page_limit: int) -> str:
    if page_token is None:
        return str(page_limit)
    try:
        current = int(page_token)
    except ValueError:
        return ""
    return str(current + page_limit)


def collect_query_summaries_with_duration_fallback(
    filters: CMQueryFilters,
    fetch_page: CMQueryPageFetcher,
    *,
    secrets: Iterable[str] = (),
) -> tuple[list[CMQuerySummary], list[str], bool]:
    summaries, warnings = collect_query_summaries(filters, fetch_page, secrets=secrets)
    if summaries or not build_cm_query_filter_expression(filters):
        return summaries, warnings, False
    if filters.min_duration_sec is None and filters.max_duration_sec is None:
        return summaries, warnings, False

    fallback_filters = replace(
        filters,
        min_duration_sec=None,
        max_duration_sec=None,
        server_duration_filter=False,
    )
    fallback_summaries, fallback_warnings = collect_query_summaries(
        fallback_filters,
        fetch_page,
        secrets=secrets,
    )
    warnings.extend(fallback_warnings)
    return fallback_summaries, warnings, True
