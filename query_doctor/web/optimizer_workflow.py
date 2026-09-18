"""Read-only Query Optimizer workflow helpers for the web UI."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from query_doctor.cli import collect_impala_context as impala_context_collector
from query_doctor.impala import table_metadata_facts
from query_doctor.impala import hms_metadata, hs2_runner
from query_doctor.impala.hms_metadata import METADATA_SOURCE_HMS_POSTGRES
from query_doctor.impala.hs2_runner import Hs2MetadataSession
from query_doctor.impala import metadata_workflow
from query_doctor.optimizer.analysis import OptimizerAnalysis, analyze_query_optimizer
from query_doctor.web.config import metadata_configured
from query_doctor.web.models import WebError, WebSettings
from query_doctor.optimizer.sql import ExtractedTable, OptimizerSqlError, extract_referenced_tables


def run_optimizer_analysis(
    sql: str,
    settings: WebSettings,
    *,
    session: Hs2MetadataSession | None = None,
) -> OptimizerAnalysis:
    try:
        tables = extract_referenced_tables(sql)
    except OptimizerSqlError as exc:
        raise WebError(
            str(exc),
            title="Optimizer SQL was rejected",
            reason_code="web.optimizer_sql_rejected",
            stage="Checking Query Optimizer input",
            next_step="Submit one read-only SELECT or WITH query without additional statements.",
        ) from exc
    metadata_context, metadata_status, metadata_message = collect_optimizer_metadata(
        tables, settings, session=session
    )
    return analyze_query_optimizer(
        sql,
        tables=tables,
        metadata_context=metadata_context,
        metadata_status=metadata_status,
        metadata_message=metadata_message,
    )


def collect_optimizer_metadata(
    tables: list[ExtractedTable],
    settings: WebSettings,
    *,
    session: Hs2MetadataSession | None = None,
) -> tuple[dict[str, Any] | None, str, str]:
    if not tables:
        return (
            None,
            "unavailable",
            "Metadata collection was not attempted because no physical tables were detected.",
        )
    if not metadata_configured(settings):
        return (
            None,
            "unavailable",
            "Metadata is unavailable. Configure local metadata settings to enable table facts.",
        )

    max_tables = settings.metadata_max_tables or metadata_workflow.DEFAULT_METADATA_MAX_TABLES
    plan = metadata_workflow.build_metadata_plan([table.name for table in tables], max_tables)
    if not plan.selected_tables:
        return (
            None,
            "unavailable",
            "No fully qualified db.table identifiers were available for metadata collection.",
        )

    from_metastore = settings.metadata_source == METADATA_SOURCE_HMS_POSTGRES
    if from_metastore and not hms_metadata.driver_available():
        return (
            None,
            "unavailable",
            "Metadata is unavailable because the metastore database driver is not installed.",
        )
    if not from_metastore and not hs2_runner.driver_available():
        return (
            None,
            "unavailable",
            "Metadata is unavailable because the impyla metadata driver is not installed.",
        )

    with tempfile.TemporaryDirectory(prefix="query-doctor-optimizer-") as tmp:
        args = argparse.Namespace(
            table=plan.selected_tables,
            out=tmp,
            source=settings.metadata_source,
            hms_postgres_dsn_env=settings.metadata_hms_postgres_dsn_env,
            coordinator=settings.metadata_coordinator,
            auth=settings.metadata_auth,
            protocol=settings.metadata_protocol,
            kerberos_service_name=settings.metadata_kerberos_service_name,
            kerberos_host_fqdn=settings.metadata_kerberos_host_fqdn,
            ssl=settings.metadata_ssl,
            ca_cert=settings.metadata_ca_cert,
            timeout_sec=settings.metadata_timeout_sec,
            max_output_bytes=settings.metadata_max_output_bytes
            or impala_context_collector.DEFAULT_MAX_OUTPUT_BYTES,
            redact=True,
            dry_run=False,
            config=None,
            krb5ccname=settings.krb5ccname,
        )
        try:
            exit_code = impala_context_collector.collect_impala_context(args, session=session)
        except Exception:
            return (
                None,
                "failed",
                "Metadata collection failed. Extracted tables are still shown with safe limitations.",
            )
        context = read_optimizer_metadata_context(Path(tmp))
    if context is None:
        return None, "failed", "Metadata collection did not produce safe metadata facts."
    if exit_code != 0:
        return (
            context,
            "failed",
            "Metadata collection was incomplete. Only available safe facts are used.",
        )
    skipped = (
        f" Skipped {len(plan.skipped_tables)} table(s) due to the configured metadata table limit."
        if plan.skipped_tables
        else ""
    )
    return (
        context,
        "collected",
        f"Safe metadata facts were collected for {len(plan.selected_tables)} table(s).{skipped}",
    )


def read_optimizer_metadata_context(out_dir: Path) -> dict[str, Any] | None:
    context_path = out_dir / "impala_context.json"
    try:
        payload = json.loads(context_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return table_metadata_facts.context_from_payload(payload, context_path, out_dir)
