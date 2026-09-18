"""Collector results for tables whose metadata is read from the metastore database.

Each table still gets one result per allowlisted statement label, so scoring, the
analyzer and the web read these results exactly as they read Impala's. The
difference is that a result carries its parsed `facts` instead of SHOW output for
the parser to read back.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from query_doctor.impala.hms_metadata import (
    HmsMetadataConnectionError,
    HmsMetadataError,
    HmsMetadataTimeoutError,
    HmsMetadataUnavailableError,
    HmsPostgresMetadataReader,
    HmsTableSnapshot,
    column_stats_facts,
    show_create_facts,
    summary_text,
    table_stats_facts,
)
from query_doctor.impala.metadata_output import normalize_output_text
from query_doctor.impala.metadata_policy import StatementPlan, dedupe_preserve_order
from query_doctor.impala.metadata_results import StatementResult, not_applicable_result


TABLE_NOT_FOUND_ERROR = "table does not exist in the metastore"
FACT_BUILDERS: dict[str, Callable[[HmsTableSnapshot], dict[str, Any]]] = {
    "SHOW CREATE TABLE": show_create_facts,
    "SHOW TABLE STATS": table_stats_facts,
    "SHOW COLUMN STATS": column_stats_facts,
}


def collect_hms_results(
    plans: list[StatementPlan],
    *,
    reader: HmsPostgresMetadataReader,
) -> list[StatementResult]:
    results: list[StatementResult] = []
    connection_error: HmsMetadataConnectionError | None = None
    for table in dedupe_preserve_order(plan.table for plan in plans):
        table_plans = [plan for plan in plans if plan.table == table]
        if connection_error is not None:
            table_results = failed_results(table_plans, "error", str(connection_error))
        else:
            try:
                table_results = table_results_from_metastore(table_plans, reader.read_table(table))
            except HmsMetadataUnavailableError:
                raise
            except HmsMetadataConnectionError as exc:
                connection_error = exc
                table_results = failed_results(table_plans, "error", str(exc))
            except HmsMetadataTimeoutError as exc:
                table_results = failed_results(table_plans, "timeout", str(exc))
            except HmsMetadataError as exc:
                table_results = failed_results(table_plans, "error", str(exc))
        for result in table_results:
            print(f"- {result.label} {result.table} (metastore)")
            print(f"  status: {result.status}")
        results.extend(table_results)
    return results


def table_results_from_metastore(
    plans: list[StatementPlan], snapshot: HmsTableSnapshot | None
) -> list[StatementResult]:
    if snapshot is None:
        return failed_results(plans, "error", TABLE_NOT_FOUND_ERROR)
    results: list[StatementResult] = []
    for plan in plans:
        if snapshot.is_view and plan.label != "SHOW CREATE TABLE":
            results.append(not_applicable_result(plan, "object is a view"))
            continue
        facts = FACT_BUILDERS[plan.label](snapshot)
        stdout = normalize_output_text(summary_text(plan.label, facts))
        results.append(
            StatementResult(
                table=plan.table,
                label=plan.label,
                sql=plan.sql,
                status="ok",
                stdout=stdout.text,
                stdout_raw_bytes=stdout.raw_bytes,
                stdout_bytes=stdout.bytes,
                stdout_normalized=stdout.normalized,
                facts=facts,
            )
        )
    return results


def failed_results(plans: list[StatementPlan], status: str, error: str) -> list[StatementResult]:
    return [
        StatementResult(
            table=plan.table,
            label=plan.label,
            sql=plan.sql,
            status=status,
            error=error,
        )
        for plan in plans
    ]
