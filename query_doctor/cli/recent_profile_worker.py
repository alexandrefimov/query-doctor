"""CLI for the shared Recent profile worker."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from query_doctor.cli import batch_recent
from query_doctor.recent.history_store import RecentHistoryStoreError
from query_doctor.recent.history_store import recent_history_store_from_config
from query_doctor.recent.profile_worker import (
    DEFAULT_PROFILE_WORKER_LEASE_SECONDS,
    DEFAULT_PROFILE_WORKER_MAX_ATTEMPTS,
    DEFAULT_PROFILE_WORKER_MAX_JOBS,
    RecentProfileWorkerOptions,
    format_recent_profile_worker_result,
    run_recent_profile_worker,
    worker_result_json,
)
from query_doctor.recent.progress import ProgressWriter


def _write_summary_json_atomically(path: Path, payload_json: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload_json)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Claim raw-free Recent profile jobs from the configured history backend, "
            "collect bounded profiles through the existing Impala Recent collectors, "
            "run deterministic analysis only, and write raw-free cache/artifact metadata. "
            "Batch Recent connection/config flags are accepted after the worker flags."
        )
    )
    parser.add_argument(
        "--profile-worker-max-jobs",
        type=batch_recent.positive_int,
        default=DEFAULT_PROFILE_WORKER_MAX_JOBS,
        help="Maximum leased profile jobs to process in this worker run.",
    )
    parser.add_argument(
        "--profile-worker-lease-owner",
        default="recent-profile-worker",
        help="Safe lease owner label stored in the raw-free profile job queue.",
    )
    parser.add_argument(
        "--profile-worker-lease-sec",
        type=batch_recent.positive_int,
        default=DEFAULT_PROFILE_WORKER_LEASE_SECONDS,
        help="Lease duration in seconds for claimed jobs.",
    )
    parser.add_argument(
        "--profile-worker-max-attempts",
        type=batch_recent.positive_int,
        default=DEFAULT_PROFILE_WORKER_MAX_ATTEMPTS,
        help="Retry budget before a retryable job is marked terminally failed.",
    )
    parser.add_argument("--json", action="store_true", help="Print raw-free JSON.")
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Write raw-free worker summary JSON.",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit non-zero when the worker summary status is not done.",
    )
    args, batch_argv = parser.parse_known_args(list(argv) if argv is not None else None)
    args.batch_args = batch_recent.parse_args(batch_argv)
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    allow_default_cm_env = env is None
    effective_env = dict(os.environ if env is None else env)
    try:
        args = parse_args(argv)
        effective_env = batch_recent.load_local_cm_env(
            effective_env,
            allow_default=allow_default_cm_env,
        )
        repo_root = batch_recent.REPO_DIR
        config = batch_recent.build_batch_config(
            args.batch_args,
            env=effective_env,
            cwd=Path.cwd(),
            repo_root=repo_root,
            validate_scan_selection_limits=False,
        )
        if config.overwrite:
            raise ValueError("Recent profile worker does not support --overwrite.")
        if config.recent_history_backend == "disabled":
            raise ValueError("Recent profile worker requires recent_history_backend.")
        effective_env = batch_recent.effective_subprocess_env(
            effective_env,
            config.krb5ccname,
        )
        batch_recent.preflight(config, env=effective_env)
        config.out.mkdir(parents=True, exist_ok=True)
        store = recent_history_store_from_config(config, env=effective_env)
        if store is None:
            raise ValueError("Recent profile worker requires recent_history_backend.")
    except ValueError as exc:
        print(f"[recent-profile-worker] ERROR: {exc}", file=sys.stderr)
        return 2
    except (OSError, RecentHistoryStoreError):
        print("[recent-profile-worker] ERROR: recent history store is unavailable", file=sys.stderr)
        return 2

    try:
        progress = ProgressWriter(config.progress_jsonl)
    except OSError:
        print("[recent-profile-worker] ERROR: cannot write --progress-jsonl", file=sys.stderr)
        return 2

    try:
        result = run_recent_profile_worker(
            store=store,
            config=config,
            env=effective_env,
            repo_root=repo_root,
            options=RecentProfileWorkerOptions(
                max_jobs=args.profile_worker_max_jobs,
                lease_owner=args.profile_worker_lease_owner,
                lease_seconds=args.profile_worker_lease_sec,
                max_attempts=args.profile_worker_max_attempts,
            ),
            progress=progress,
        )
        payload = result.safe_payload()
        if args.summary_json:
            try:
                _write_summary_json_atomically(
                    args.summary_json,
                    worker_result_json(payload),
                )
            except OSError:
                print(
                    "[recent-profile-worker] ERROR: could not write summary JSON", file=sys.stderr
                )
                return 2
        if args.json:
            sys.stdout.write(worker_result_json(payload))
        else:
            sys.stdout.write(format_recent_profile_worker_result(payload))
        if args.fail_on_warning and payload.get("status") != "done":
            return 1
        return 0
    finally:
        progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
