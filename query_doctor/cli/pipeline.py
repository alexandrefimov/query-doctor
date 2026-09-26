#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from query_doctor.cli.commands import (
    command_prefix,
    resolve_command_backend,
)
from query_doctor.impala.hms_metadata import METADATA_SOURCE_IMPALA
from query_doctor.impala.kerberos_preflight import check_kerberos_ticket_cache
from query_doctor.impala.metadata_workflow import (
    METADATA_SOURCE_TABLES_ENV,
    add_metadata_arguments,
    build_metadata_collector_cmd,
    build_metadata_plan,
    metadata_config_status,
    print_metadata_plan,
    read_default_database_from_facts,
    read_referenced_tables_from_facts,
    resolve_metadata_mode,
    validate_metadata_args,
)
from query_doctor.report.language_contract import (
    SUPPORTED_REPORT_LANGUAGES,
    normalize_report_language,
)
from query_doctor.report.llm_client import DEFAULT_LLM_PROVIDER, LLM_PROVIDER_CHOICES


DEFAULT_MODEL = "qwen3-coder:30b-a3b-q8_0"
ANALYZER_TIMEOUT_SEC = 900
METADATA_STAGE_TIMEOUT_SEC = 1800
REPORT_TIMEOUT_SEC = 2400
SUBPROCESS_TIMEOUT_EXIT_CODE = 124


def report_language_arg(value: str) -> str:
    try:
        return normalize_report_language(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def read_metadata_source_tables_from_env(env: dict[str, str]) -> list[str]:
    raw_value = env.get(METADATA_SOURCE_TABLES_ENV)
    if not raw_value:
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [value for value in payload if isinstance(value, str) and value.strip()]


def run_cmd(cmd: list[str], cwd: Path, *, timeout_sec: int | None = None) -> None:
    print()
    print("[pipeline] running:")
    print(" ".join(cmd))
    effective_timeout = ANALYZER_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    try:
        result = subprocess.run(cmd, cwd=str(cwd), timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[pipeline] ERROR: subprocess timed out after {effective_timeout}s",
            file=sys.stderr,
        )
        raise SystemExit(SUBPROCESS_TIMEOUT_EXIT_CODE) from None
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def run_metadata_cmd(cmd: list[str], cwd: Path, *, timeout_sec: int | None = None) -> None:
    print()
    print("[pipeline] running explicit read-only Impala metadata collector")
    effective_timeout = METADATA_STAGE_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    try:
        result = subprocess.run(cmd, cwd=str(cwd), timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[pipeline] ERROR: metadata collection timed out after {effective_timeout}s",
            file=sys.stderr,
        )
        raise SystemExit(SUBPROCESS_TIMEOUT_EXIT_CODE) from None
    if result.returncode != 0:
        raise SystemExit(result.returncode)


DEFAULT_RUN_CMD = run_cmd
DEFAULT_RUN_METADATA_CMD = run_metadata_cmd


def call_runner_with_timeout(
    runner: Callable[[list[str], Path], None],
    cmd: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
) -> None:
    if runner is DEFAULT_RUN_CMD or runner is DEFAULT_RUN_METADATA_CMD:
        runner(cmd, cwd, timeout_sec=timeout_sec)
        return
    runner(cmd, cwd)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Query Doctor pipeline: deterministic analyzer -> LLM report writer."
    )
    parser.add_argument(
        "case_dir",
        help="Path to case directory containing profile_digest.md",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model for report writer. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--llm-provider",
        choices=LLM_PROVIDER_CHOICES,
        default=os.getenv("QD_REPORT_LLM_PROVIDER", DEFAULT_LLM_PROVIDER),
        help="LLM provider for report writer. Default: %(default)s",
    )
    parser.add_argument(
        "--llm-base-url",
        default=(
            os.getenv("QD_REPORT_LLM_API_BASE_URL")
            or os.getenv("QD_REPORT_LLM_BASE_URL")
            or os.getenv("QD_LLM_API_BASE_URL")
            or os.getenv("QD_LLM_BASE_URL")
        ),
        help="Base URL for the configured LLM provider.",
    )
    parser.add_argument(
        "--llm-chat-path",
        default=os.getenv("QD_REPORT_LLM_CHAT_PATH") or os.getenv("QD_LLM_CHAT_PATH"),
        help="OpenAI-compatible report chat path override.",
    )
    parser.add_argument(
        "--llm-api-key-env",
        default=os.getenv("QD_REPORT_LLM_API_KEY_ENV", "QD_REPORT_LLM_API_KEY"),
        help="Environment variable name containing the external report LLM API token.",
    )
    parser.add_argument(
        "--mode",
        choices=("admin", "user"),
        default="admin",
        help="Report audience mode passed to query-doctor-report. Default: %(default)s",
    )
    parser.add_argument(
        "--language",
        type=report_language_arg,
        choices=SUPPORTED_REPORT_LANGUAGES,
        default="en",
        help="Report language passed to query-doctor-report. Default: %(default)s",
    )
    parser.add_argument(
        "--out",
        default="diagnosis.md",
        help="Report output path. Relative paths are resolved by query-doctor-report relative to CASE_DIR.",
    )
    parser.add_argument(
        "--keep-alive",
        default="0",
        help="Ollama keep_alive value. Default: 0.",
    )
    parser.add_argument(
        "--report-validation-mode",
        choices=("strict", "relaxed", "off"),
        default=os.getenv("QD_REPORT_VALIDATION_MODE", "strict"),
        help=(
            "Validation mode passed to query-doctor-report. strict enforces the full report contract; "
            "relaxed keeps safety/fact checks but ignores shape; off skips validation. Default: %(default)s"
        ),
    )
    parser.add_argument(
        "--stop-other-models",
        action="store_true",
        help="Unload other Ollama models before generation.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Generate the report with deterministic Python code and do not call Ollama.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Run only deterministic analyzer.",
    )
    parser.add_argument(
        "--stop-after-analysis",
        action="store_true",
        help=(
            "Run analyzer and optional metadata collection, then stop before "
            "report generation; no LLM/Ollama call."
        ),
    )
    parser.add_argument(
        "--metadata-failure-policy",
        choices=("fail", "continue"),
        default="fail",
        help=(
            "What to do if metadata collection fails. Default: fail. "
            "continue is only allowed with --stop-after-analysis for analyzer-only profile analysis."
        ),
    )
    add_metadata_arguments(parser)

    args = parser.parse_args(argv)
    validate_metadata_args(parser, args)
    if args.metadata_failure_policy == "continue" and not args.stop_after_analysis:
        parser.error("--metadata-failure-policy continue requires --stop-after-analysis")
    return args


def main(
    argv: list[str] | None = None,
    *,
    repo_dir: Path | None = None,
    run_cmd_func: Callable[[list[str], Path], None] | None = None,
    run_metadata_cmd_func: Callable[[list[str], Path], None] | None = None,
) -> int:
    args = parse_args(argv)

    repo_dir = Path(__file__).resolve().parents[2] if repo_dir is None else repo_dir
    command_runner = run_cmd if run_cmd_func is None else run_cmd_func
    metadata_runner = run_metadata_cmd if run_metadata_cmd_func is None else run_metadata_cmd_func
    command_backend = resolve_command_backend()
    case_dir = Path(args.case_dir).expanduser()

    if not case_dir.is_absolute():
        case_dir = (Path.cwd() / case_dir).resolve()

    profile_digest = case_dir / "profile_digest.md"
    if not profile_digest.exists():
        print(f"[pipeline] ERROR: missing {profile_digest}", file=sys.stderr)
        return 2

    metadata_mode = resolve_metadata_mode(args)

    call_runner_with_timeout(
        command_runner,
        command_prefix(repo_dir, "analyze", backend=command_backend)
        + [str(case_dir), "--json", str(case_dir / "analysis.json")],
        cwd=repo_dir,
        timeout_sec=ANALYZER_TIMEOUT_SEC,
    )

    facts = case_dir / "analysis_facts.md"
    if not facts.exists():
        print(f"[pipeline] ERROR: analyzer did not create {facts}", file=sys.stderr)
        return 3

    print()
    print(f"[pipeline] facts: {facts}")

    if metadata_mode == "auto":
        config_status = metadata_config_status(args)
        if config_status.configured:
            print("[pipeline] metadata collection: auto mode configured")
            metadata_mode = "on"
        elif config_status.fatal:
            print(
                f"[pipeline] ERROR: metadata configuration is invalid: {config_status.reason}",
                file=sys.stderr,
            )
            return 2
        else:
            print(
                "[pipeline] metadata collection: not configured; "
                f"continuing without metadata ({config_status.reason})"
            )

    if metadata_mode in {"on", "dry-run"}:
        if metadata_mode == "on":
            config_status = metadata_config_status(args)
            if not config_status.configured:
                print(
                    f"[pipeline] ERROR: metadata collection is not configured: {config_status.reason}",
                    file=sys.stderr,
                )
                return 2
        source_tables = read_metadata_source_tables_from_env(os.environ)
        raw_tables = [*source_tables, *read_referenced_tables_from_facts(facts)]
        default_database = args.metadata_default_db or read_default_database_from_facts(facts)
        metadata_plan = build_metadata_plan(
            raw_tables,
            args.metadata_max_tables,
            default_database=default_database,
        )
        print_metadata_plan(
            metadata_plan,
            dry_run=metadata_mode == "dry-run",
            redact_identifiers=bool(source_tables),
        )
        if metadata_mode == "dry-run":
            print(
                "[pipeline] metadata dry-run complete; analyzer/report were not rerun after metadata collection"
            )
            return 0
        if metadata_plan.selected_tables:
            if (
                args.metadata_source == METADATA_SOURCE_IMPALA
                and args.metadata_auth == "kerberos"
                and metadata_runner is DEFAULT_RUN_METADATA_CMD
            ):
                ticket_status = check_kerberos_ticket_cache(os.environ)
                if not ticket_status.ok:
                    print(
                        f"[pipeline] ERROR: {ticket_status.reason}",
                        file=sys.stderr,
                    )
                    return 2
            metadata_cmd = build_metadata_collector_cmd(
                args,
                collector_prefix=command_prefix(
                    repo_dir, "collect_impala_context", backend=command_backend
                ),
                case_dir=case_dir,
                tables=metadata_plan.selected_tables,
                # Source tables lead the raw list, so their positions are the
                # numbers the profile's redacted SQL names them by.
                table_numbers=(
                    [metadata_plan.raw_positions[table] for table in metadata_plan.selected_tables]
                    if source_tables
                    else None
                ),
            )
            try:
                call_runner_with_timeout(
                    metadata_runner,
                    metadata_cmd,
                    cwd=repo_dir,
                    timeout_sec=METADATA_STAGE_TIMEOUT_SEC,
                )
            except SystemExit as exc:
                if args.metadata_failure_policy != "continue" or not args.stop_after_analysis:
                    raise
                code = exc.code if isinstance(exc.code, int) else 1
                print(
                    "[pipeline] metadata collection failed; continuing analyzer-only "
                    f"because --metadata-failure-policy continue is set (exit {code})"
                )
                print(
                    "[pipeline] partial metadata outputs are left on disk but not promoted into analyzer facts"
                )
                print("[pipeline] stop-after-analysis requested; report generation skipped")
                return 0
            call_runner_with_timeout(
                command_runner,
                command_prefix(repo_dir, "analyze", backend=command_backend)
                + [str(case_dir), "--json", str(case_dir / "analysis.json")],
                cwd=repo_dir,
                timeout_sec=ANALYZER_TIMEOUT_SEC,
            )
            if not facts.exists():
                print(f"[pipeline] ERROR: analyzer did not create {facts}", file=sys.stderr)
                return 3
            print()
            print(f"[pipeline] facts refreshed with Impala metadata: {facts}")
        else:
            print("[pipeline] no valid referenced tables found for metadata collection")

    if args.skip_report:
        print("[pipeline] skip report requested")
        return 0

    if args.stop_after_analysis:
        print("[pipeline] stop-after-analysis requested; report generation skipped")
        return 0

    report_cmd = command_prefix(repo_dir, "report", backend=command_backend) + [
        str(case_dir),
        "--model",
        args.model,
        "--llm-provider",
        args.llm_provider,
        "--mode",
        args.mode,
        "--language",
        args.language,
        "--out",
        args.out,
        "--keep-alive",
        args.keep_alive,
        "--validation-mode",
        args.report_validation_mode,
    ]
    if args.llm_base_url:
        report_cmd.extend(["--llm-base-url", args.llm_base_url])
    if args.llm_chat_path:
        report_cmd.extend(["--llm-chat-path", args.llm_chat_path])
    if args.llm_api_key_env:
        report_cmd.extend(["--llm-api-key-env", args.llm_api_key_env])

    if args.stop_other_models:
        report_cmd.append("--stop-other-models")
    if args.no_llm:
        report_cmd.append("--no-llm")

    call_runner_with_timeout(
        command_runner, report_cmd, cwd=repo_dir, timeout_sec=REPORT_TIMEOUT_SEC
    )

    print()
    print(f"[pipeline] done: {case_dir / args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
