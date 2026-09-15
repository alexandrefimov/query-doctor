"""CLI for raw-free Recent profile job remediation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from query_doctor.recent.batch_config import DEFAULT_RECENT_HISTORY_POSTGRES_DSN_ENV
from query_doctor.recent.batch_config import validate_env_var_name
from query_doctor.recent.history_store import RecentHistoryStoreError
from query_doctor.recent.history_store import safe_label
from query_doctor.recent.profile_budget import (
    DEFAULT_PROFILE_REQUEUE_LIMIT,
    RecentProfileJobRequeueResult,
)


SUMMARY_KIND = "query_doctor_recent_profile_remediation_v1"
STATUS_DRY_RUN = "dry_run"
STATUS_APPLIED = "applied"
STATUS_BLOCKED = "blocked"


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely requeue terminal failed Recent profile jobs in the history backend. "
            "The command does not contact query engines, discover queries, collect profiles, "
            "run metadata SQL, run LLM reports, or run optimizer jobs."
        )
    )
    parser.add_argument(
        "--backend",
        choices=("sqlite", "postgres"),
        required=True,
        help="Recent history backend to inspect or remediate.",
    )
    parser.add_argument(
        "--sqlite-db",
        type=Path,
        help="SQLite recent history database path. Required for --backend sqlite.",
    )
    parser.add_argument(
        "--postgres-dsn-env",
        default=DEFAULT_RECENT_HISTORY_POSTGRES_DSN_ENV,
        help="Environment variable name containing the Postgres DSN.",
    )
    parser.add_argument(
        "--engine",
        help="Optional engine filter. The value is used for matching only and is not echoed.",
    )
    parser.add_argument(
        "--source-kind",
        help="Optional source-kind filter. The value is used for matching only and is not echoed.",
    )
    parser.add_argument(
        "--source-key",
        help="Optional source-key filter. The value is used for matching only and is not echoed.",
    )
    parser.add_argument(
        "--max-jobs",
        type=positive_int,
        default=DEFAULT_PROFILE_REQUEUE_LIMIT,
        help="Maximum failed profile jobs to select for one remediation run.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Count/select matching failed jobs without changing the store. This is the default.",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Requeue the selected failed jobs by setting them back to pending.",
    )
    parser.add_argument("--json", action="store_true", help="Print raw-free JSON.")
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Write raw-free remediation summary JSON.",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit non-zero unless remediation completed or dry-run succeeded.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    args = parse_args(argv)
    effective_env = dict(os.environ if env is None else env)
    try:
        result = run_remediation(args=args, env=effective_env)
        status = STATUS_DRY_RUN if result.dry_run else STATUS_APPLIED
        payload = remediation_payload(status=status, args=args, result=result)
    except ValueError as exc:
        payload = remediation_payload(
            status=STATUS_BLOCKED,
            args=args,
            result=RecentProfileJobRequeueResult(dry_run=bool(args.dry_run)),
            issue_codes=(
                safe_label(
                    exc.args[0] if exc.args else "",
                    default="profile_remediation_invalid",
                ),
            ),
        )
    except (OSError, RecentHistoryStoreError):
        payload = remediation_payload(
            status=STATUS_BLOCKED,
            args=args,
            result=RecentProfileJobRequeueResult(dry_run=bool(args.dry_run)),
            issue_codes=("recent_profile_remediation_failed",),
        )

    if args.summary_json:
        try:
            args.summary_json.write_text(remediation_payload_json(payload), encoding="utf-8")
        except OSError:
            print(
                "[Recent profile remediation] ERROR: could not write summary JSON",
                file=sys.stderr,
            )
            return 2
    if args.json:
        sys.stdout.write(remediation_payload_json(payload))
    else:
        sys.stdout.write(format_remediation_payload(payload))
    if args.fail_on_warning and payload.get("status") == STATUS_BLOCKED:
        return 1
    return 0 if payload.get("status") != STATUS_BLOCKED else 1


def run_remediation(
    *,
    args: argparse.Namespace,
    env: Mapping[str, str],
) -> RecentProfileJobRequeueResult:
    if args.backend == "sqlite":
        if args.sqlite_db is None:
            raise ValueError("recent_history_sqlite_db_missing")
        from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

        store = SqliteRecentHistoryStore(args.sqlite_db)
    elif args.backend == "postgres":
        safe_dsn_env = validate_env_var_name(
            args.postgres_dsn_env,
            name="recent_history_postgres_dsn_env",
        )
        if not env.get(safe_dsn_env):
            raise ValueError("postgres_dsn_env_missing")
        from query_doctor.recent.postgres_history_store import PostgresRecentHistoryStore

        store = PostgresRecentHistoryStore.from_env(safe_dsn_env, env=dict(env))
        return store.requeue_failed_profile_jobs(
            max_jobs=args.max_jobs,
            requeued_at_iso=remediation_timestamp(),
            dry_run=bool(args.dry_run),
            engine=args.engine,
            source_kind=args.source_kind,
            source_key=args.source_key,
            prepare_schema=False,
        )
    else:
        raise ValueError("recent_history_backend_invalid")

    return store.requeue_failed_profile_jobs(
        max_jobs=args.max_jobs,
        requeued_at_iso=remediation_timestamp(),
        dry_run=bool(args.dry_run),
        engine=args.engine,
        source_kind=args.source_kind,
        source_key=args.source_key,
    )


def remediation_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def remediation_payload(
    *,
    status: str,
    args: argparse.Namespace,
    result: RecentProfileJobRequeueResult,
    issue_codes: Sequence[str] = (),
) -> dict[str, object]:
    remediation = result.safe_payload()
    return {
        "summary_kind": SUMMARY_KIND,
        "status": status,
        "backend": "recent_history",
        "mode": "dry_run" if result.dry_run else "apply",
        "filters": {
            "engine_configured": bool(args.engine),
            "source_kind_configured": bool(args.source_kind),
            "source_key_configured": bool(args.source_key),
            "max_jobs": max(1, int(args.max_jobs)),
        },
        "remediation": remediation,
        "next_step": remediation_next_step(status=status, result=result),
        "issue_codes": list(dict.fromkeys(issue_codes)),
        "raw_output": False,
        "sensitive_value_echo": False,
    }


def remediation_next_step(*, status: str, result: RecentProfileJobRequeueResult) -> str:
    if status == STATUS_BLOCKED:
        return "Fix the remediation configuration or history backend, then rerun dry-run."
    if result.selected_failed_jobs <= 0:
        return "No terminal failed profile jobs matched the remediation filters."
    if result.dry_run:
        return "Review the bounded count, then rerun with --apply to requeue selected jobs."
    if result.requeued_jobs > 0:
        return "Run the Recent profile worker to process the requeued jobs."
    return "No profile jobs were changed; rerun dry-run to inspect the current backlog."


def remediation_payload_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True) + "\n"


def format_remediation_payload(payload: Mapping[str, Any]) -> str:
    remediation = payload.get("remediation")
    counts = remediation if isinstance(remediation, dict) else {}
    lines = [f"Recent profile remediation: {payload.get('status', 'unknown')}"]
    lines.append(f"- mode: {payload.get('mode', 'unknown')}")
    lines.append(f"- matching failed jobs: {counts.get('matched_failed_jobs', 0)}")
    lines.append(f"- selected by limit: {counts.get('selected_failed_jobs', 0)}")
    lines.append(f"- requeued jobs: {counts.get('requeued_jobs', 0)}")
    lines.append(f"- skipped by limit: {counts.get('skipped_due_to_limit', 0)}")
    next_step = payload.get("next_step")
    if next_step:
        lines.append(f"- next step: {next_step}")
    issues = payload.get("issue_codes")
    if isinstance(issues, list) and issues:
        lines.append("issues: " + ",".join(str(issue) for issue in issues))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
