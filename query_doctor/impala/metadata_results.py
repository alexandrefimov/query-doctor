"""Result models and output rendering for Impala metadata collection."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from query_doctor.impala.metadata_policy import StatementPlan
from query_doctor.impala.metadata_redaction import redact_impala_context_text


@dataclass
class StatementResult:
    table: str
    label: str
    sql: str
    status: str
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    error: str = ""
    stdout_raw_bytes: int = 0
    stdout_bytes: int = 0
    stdout_normalized: bool = False
    stderr_raw_bytes: int = 0
    stderr_bytes: int = 0
    stderr_normalized: bool = False
    # Parsed facts supplied by a source that does not produce SHOW output text.
    facts: dict[str, Any] | None = None


def planned_result(plan: StatementPlan) -> StatementResult:
    return StatementResult(
        table=plan.table,
        label=plan.label,
        sql=plan.sql,
        status="planned",
    )


def not_applicable_result(plan: StatementPlan, reason: str) -> StatementResult:
    return StatementResult(
        table=plan.table,
        label=plan.label,
        sql=plan.sql,
        status="not_applicable",
        error=reason,
    )


def redact_output_value(args: argparse.Namespace, value: object) -> str:
    if not getattr(args, "redact", True):
        return str(value)
    return redact_impala_context_text(
        value,
        redact_identifiers=getattr(args, "redact_identifiers", True),
        redact_hosts=getattr(args, "redact_hosts", True),
    )


def redact_facts_value(args: argparse.Namespace, value: Any) -> Any:
    if isinstance(value, str):
        return redact_output_value(args, value)
    if isinstance(value, list):
        return [redact_facts_value(args, item) for item in value]
    if isinstance(value, dict):
        return {
            redact_output_value(args, key): redact_facts_value(args, item)
            for key, item in value.items()
        }
    return value


def table_labels(tables: list[str], args: argparse.Namespace) -> dict[str, str]:
    """Name each requested table in the written outputs.

    Identifier redaction turns every name into the same `<db>.<table>`, and
    readers key per-table facts by that name, so redacted outputs number the
    tables in request order to keep them apart.
    """
    if getattr(args, "redact", True) and getattr(args, "redact_identifiers", True):
        return {table: f"<db>.<table-{index}>" for index, table in enumerate(tables, start=1)}
    return {table: redact_output_value(args, table) for table in tables}


def result_to_json(
    result: StatementResult,
    *,
    args: argparse.Namespace,
    table_label: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "table": table_label or redact_output_value(args, result.table),
        "statement": result.label,
        "sql": redact_output_value(args, result.sql),
        "status": result.status,
        "returncode": result.returncode,
        "error": redact_output_value(args, result.error) if result.error else "",
        "stdout": redact_output_value(args, result.stdout) if result.stdout else "",
        "stderr": redact_output_value(args, result.stderr) if result.stderr else "",
        "stdout_raw_bytes": result.stdout_raw_bytes,
        "stdout_bytes": result.stdout_bytes,
        "stdout_normalized": result.stdout_normalized,
        "stderr_raw_bytes": result.stderr_raw_bytes,
        "stderr_bytes": result.stderr_bytes,
        "stderr_normalized": result.stderr_normalized,
    }
    if result.facts is not None:
        payload["facts"] = redact_facts_value(args, result.facts)
    return payload


def render_statement_output(result: StatementResult, *, args: argparse.Namespace) -> str:
    output = redact_output_value(args, result.stdout).strip() if result.stdout else ""
    if result.stderr.strip():
        output = (
            output + "\n\nstderr:\n" + redact_output_value(args, result.stderr).strip()
        ).strip()
    if result.error:
        output = (output + "\n\nerror: " + redact_output_value(args, result.error)).strip()
    return output or "(no output captured)"


def render_markdown(
    *,
    timestamp: str,
    tables: list[str],
    results: list[StatementResult],
    args: argparse.Namespace,
) -> str:
    lines = [
        "# Impala Context",
        "",
        "## Collection Summary",
        f"- collection timestamp: {timestamp}",
        f"- metadata source: {metadata_source(args)}",
        f"- tables requested: {len(tables)}",
        "- read-only statements only: yes",
        f"- max output bytes: {args.max_output_bytes}",
        f"- timeout seconds: {args.timeout_sec}",
        f"- redaction: {'enabled' if getattr(args, 'redact', True) else 'disabled'}",
        f"- identifier redaction: {'enabled' if getattr(args, 'redact', True) and getattr(args, 'redact_identifiers', True) else 'disabled'}",
        f"- host redaction: {'enabled' if getattr(args, 'redact', True) and getattr(args, 'redact_hosts', True) else 'disabled'}",
        f"- dry-run: {'yes' if args.dry_run else 'no'}",
        "",
    ]

    labels = table_labels(tables, args)
    for table in tables:
        lines += [f"## Table: {labels[table]}", ""]
        for result in [item for item in results if item.table == table]:
            fence = "sql" if result.label == "SHOW CREATE TABLE" else "text"
            lines += [
                f"### {result.label}",
                f"status: {result.status}",
                "",
                f"```{fence}",
                render_statement_output(result, args=args),
                "```",
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"


def metadata_source(args: argparse.Namespace) -> str:
    return str(getattr(args, "source", None) or "impala")


def write_outputs(
    out_dir: Path,
    *,
    timestamp: str,
    tables: list[str],
    results: list[StatementResult],
    args: argparse.Namespace,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(timestamp=timestamp, tables=tables, results=results, args=args)
    labels = table_labels(tables, args)
    payload = {
        "collection_timestamp": timestamp,
        "metadata_source": metadata_source(args),
        "tables": [labels[table] for table in tables],
        "read_only_statements_only": True,
        "max_output_bytes": args.max_output_bytes,
        "timeout_seconds": args.timeout_sec,
        "redaction": "enabled" if getattr(args, "redact", True) else "disabled",
        "identifier_redaction": (
            "enabled"
            if getattr(args, "redact", True) and getattr(args, "redact_identifiers", True)
            else "disabled"
        ),
        "host_redaction": "enabled"
        if getattr(args, "redact", True) and getattr(args, "redact_hosts", True)
        else "disabled",
        "dry_run": args.dry_run,
        "results": [
            result_to_json(result, args=args, table_label=labels.get(result.table))
            for result in results
        ],
    }
    (out_dir / "impala_context.md").write_text(markdown, encoding="utf-8")
    (out_dir / "impala_context.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
