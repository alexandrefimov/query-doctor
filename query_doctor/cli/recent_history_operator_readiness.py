"""CLI for retained raw-free Recent history operator readiness evidence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from query_doctor.recent.operator_readiness import (
    DEFAULT_MAX_FAILED_SHARE,
    STATUS_READY,
    audit_recent_history_operator_readiness,
    format_recent_history_operator_readiness,
    operator_readiness_payload_json,
)


def share_fraction(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1") from exc
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate retained raw-free Recent history operator evidence. The command "
            "reads only existing readiness/worker/collector/retention/remediation "
            "summary JSON files, does not print input paths, and does not contact "
            "Postgres, Kubernetes, query engines, or profile collectors."
        )
    )
    parser.add_argument(
        "--postgres-readiness-summary-json",
        type=Path,
        required=True,
        help="Raw-free query-doctor-recent-history-postgres-readiness summary JSON.",
    )
    parser.add_argument(
        "--profile-worker-summary-json",
        type=Path,
        required=True,
        help="Raw-free query-doctor-recent-profile-worker summary JSON.",
    )
    parser.add_argument(
        "--collector-summary-json",
        type=Path,
        help="Optional raw-free query-doctor-recent-history-collector summary JSON.",
    )
    parser.add_argument(
        "--retention-summary-json",
        type=Path,
        help="Optional raw-free query-doctor-recent-history-retention summary JSON.",
    )
    parser.add_argument(
        "--profile-remediation-summary-json",
        type=Path,
        help="Optional raw-free query-doctor-recent-profile-remediation summary JSON.",
    )
    parser.add_argument(
        "--max-evidence-age-minutes",
        type=positive_int,
        help=(
            "Block unless retained collector and profile-worker summaries were observed "
            "within this many minutes. Without it, producer freshness is not checked."
        ),
    )
    parser.add_argument(
        "--max-failed-share",
        type=share_fraction,
        default=DEFAULT_MAX_FAILED_SHARE,
        help=(
            "Block when more than this fraction of profile jobs finished in the worker's "
            "profile window failed. Worker summaries without a window block on any failed "
            f"job. Default: {DEFAULT_MAX_FAILED_SHARE}."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print raw-free JSON.")
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Write raw-free operator-readiness summary JSON.",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit non-zero unless retained evidence is ready.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    postgres_summary = read_summary_json(args.postgres_readiness_summary_json)
    worker_summary = read_summary_json(args.profile_worker_summary_json)
    collector_summary = (
        read_summary_json(args.collector_summary_json) if args.collector_summary_json else None
    )
    retention_summary = (
        read_summary_json(args.retention_summary_json) if args.retention_summary_json else None
    )
    remediation_summary = (
        read_summary_json(args.profile_remediation_summary_json)
        if args.profile_remediation_summary_json
        else None
    )
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary,
        profile_worker_summary=worker_summary,
        collector_summary=collector_summary,
        retention_summary=retention_summary,
        remediation_summary=remediation_summary,
        max_evidence_age_minutes=args.max_evidence_age_minutes,
        max_failed_share=args.max_failed_share,
        now=datetime.now(timezone.utc),
    )
    payload = result.payload()
    if args.summary_json:
        try:
            args.summary_json.write_text(operator_readiness_payload_json(payload), encoding="utf-8")
        except OSError:
            print(
                "[Recent history operator readiness] ERROR: could not write summary JSON",
                file=sys.stderr,
            )
            return 2
    if args.json:
        sys.stdout.write(operator_readiness_payload_json(payload))
    else:
        sys.stdout.write(format_recent_history_operator_readiness(payload))
    if args.fail_on_warning and payload.get("status") != STATUS_READY:
        return 1
    return 0 if payload.get("status") == STATUS_READY else 1


def read_summary_json(path: Path) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


if __name__ == "__main__":
    raise SystemExit(main())
