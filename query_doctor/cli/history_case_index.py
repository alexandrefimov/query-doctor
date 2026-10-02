"""Explicit owner-only, bounded backfill of retained case link identities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from query_doctor.recent.history_store import RecentHistoryStoreError
from query_doctor.web.config import load_web_local_config
from query_doctor.web.recent_history_inbox import _history_store_from_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Allow the owner backfill to write identities."
    )
    parser.add_argument(
        "--prepare-schema", action="store_true", help="Allow schema/index preparation first."
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-rows", type=int, default=10000)
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("This owner operation requires --apply.")
    if not 1 <= args.batch_size <= 10000 or not 1 <= args.max_rows <= 1000000:
        parser.error("Batch size must be 1..10000 and max rows 1..1000000.")
    processed = 0
    complete = False
    try:
        store, _backend = _history_store_from_config(
            load_web_local_config(args.config, cwd=Path.cwd())
        )
        if store is None:
            raise RecentHistoryStoreError("history_store_unavailable")
        if args.prepare_schema:
            store.prepare_case_ref_index()
        while processed < args.max_rows:
            limit = min(args.batch_size, args.max_rows - processed)
            count = store.backfill_case_refs(limit=limit)
            processed += count
            if count < limit:
                complete = True
                break
    except (OSError, ValueError, RecentHistoryStoreError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "history_case_index_unavailable",
                    "processed": processed,
                }
            )
        )
        return 1
    print(json.dumps({"status": "complete" if complete else "bounded", "processed": processed}))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
