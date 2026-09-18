"""SQLite adapter for the raw-free Recent summary history store."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from query_doctor.recent.history_store import (
    RecentHistoryStoreError,
    RecentHistoryRetentionPolicy,
    RecentHistoryRetentionResult,
    RecentSummaryHistoryRecord,
    safe_retention_policy,
)
from query_doctor.recent.profile_budget import (
    ANALYSIS_CACHE_DEFAULT_CONTRACT,
    ANALYSIS_CACHE_STATUS_READY,
    ANALYSIS_CACHE_STORAGE_COLUMNS,
    PROFILE_ARTIFACT_DEFAULT_CONTRACT,
    PROFILE_ARTIFACT_STORAGE_COLUMNS,
    PROFILE_ARTIFACT_STATUS_AVAILABLE,
    PROFILE_JOB_AGED_OUT_ERROR_CODE,
    PROFILE_JOB_NOT_FOUND_ERROR_CODE,
    PROFILE_JOB_STATUS_AGED_OUT,
    PROFILE_JOB_STATUS_COMPLETED,
    PROFILE_JOB_STATUS_FAILED,
    PROFILE_JOB_STATUS_LEASED,
    PROFILE_JOB_STATUS_PENDING,
    PROFILE_JOB_STORAGE_COLUMNS,
    PROFILE_STATUS_ANALYZED,
    PROFILE_STATUS_FAILED,
    PROFILE_STATUS_NOT_COLLECTED,
    PROFILE_STATUS_PENDING,
    PROFILE_STATUS_PROCESSING,
    PROFILE_STATUS_RETRY_PENDING,
    RecentAnalysisCacheRecord,
    RecentProfileBacklogHealth,
    RecentProfileArtifactRecord,
    RecentProfileJobRequeueResult,
    RecentProfileJobRecord,
    analysis_cache_record_from_storage_values,
    analysis_cache_record_to_storage_row,
    normalize_analysis_cache_contract,
    normalize_analysis_cache_fingerprint,
    normalize_optional_profile_job_filters,
    normalize_profile_claim_limit,
    normalize_profile_error_code,
    normalize_profile_job_key,
    normalize_profile_lease_owner,
    normalize_profile_lease_timestamp,
    next_profile_job_status,
    profile_backlog_health_from_counts,
    profile_artifact_record_from_storage_values,
    profile_artifact_record_to_storage_row,
    profile_job_record_from_storage_values,
    safe_analysis_cache_payload,
    safe_optional_profile_error_code,
)


class SqliteRecentHistoryStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(SQLITE_RECENT_QUERY_SUMMARY_DDL)
        except (OSError, sqlite3.Error) as exc:
            raise RecentHistoryStoreError("sqlite_recent_history_initialize_failed") from exc

    def upsert_summaries(self, records: Iterable[RecentSummaryHistoryRecord]) -> int:
        rows = [record_to_sqlite_row(record) for record in records]
        if not rows:
            return 0
        self.initialize()
        try:
            with self._connect() as connection:
                connection.executemany(SQLITE_RECENT_QUERY_SUMMARY_UPSERT, rows)
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_history_upsert_failed") from exc
        return len(rows)

    def enqueue_profile_jobs(self, records: Iterable[RecentProfileJobRecord]) -> int:
        rows = [profile_job_to_sqlite_row(record) for record in records]
        if not rows:
            return 0
        self.initialize()
        try:
            with self._connect() as connection:
                connection.executemany(SQLITE_RECENT_PROFILE_JOB_INSERT, rows)
                connection.executemany(
                    SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                    [
                        (
                            PROFILE_STATUS_PENDING,
                            row["engine"],
                            row["source_kind"],
                            row["source_key"],
                            row["query_id"],
                            PROFILE_STATUS_PENDING,
                            PROFILE_STATUS_PENDING,
                        )
                        for row in rows
                    ],
                )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_enqueue_failed") from exc
        return len(rows)

    def claim_profile_jobs(
        self,
        *,
        max_jobs: int,
        lease_owner: str,
        lease_until_iso: str,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
    ) -> list[RecentProfileJobRecord]:
        limit = normalize_profile_claim_limit(max_jobs)
        if limit <= 0:
            return []
        owner = normalize_profile_lease_owner(lease_owner)
        lease_until = normalize_profile_lease_timestamp(lease_until_iso)
        now = normalize_profile_lease_timestamp(now_iso)
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    SQLITE_RECENT_PROFILE_JOB_CLAIM_SELECT,
                    (
                        engine_filter,
                        engine_filter,
                        source_kind_filter,
                        source_kind_filter,
                        source_key_filter,
                        source_key_filter,
                        PROFILE_JOB_STATUS_PENDING,
                        PROFILE_JOB_STATUS_LEASED,
                        now,
                        limit,
                    ),
                ).fetchall()
                claimed: list[RecentProfileJobRecord] = []
                for row in rows:
                    record = profile_job_record_from_storage_values(row)
                    updated = connection.execute(
                        SQLITE_RECENT_PROFILE_JOB_CLAIM_UPDATE,
                        (
                            PROFILE_JOB_STATUS_LEASED,
                            owner,
                            lease_until,
                            now,
                            record.engine,
                            record.source_kind,
                            record.source_key,
                            record.query_id,
                            PROFILE_JOB_STATUS_PENDING,
                            PROFILE_JOB_STATUS_LEASED,
                            now,
                        ),
                    )
                    if updated.rowcount == 1:
                        connection.execute(
                            SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                            (
                                PROFILE_STATUS_PROCESSING,
                                record.engine,
                                record.source_kind,
                                record.source_key,
                                record.query_id,
                                PROFILE_STATUS_PROCESSING,
                                PROFILE_STATUS_PROCESSING,
                            ),
                        )
                        claimed.append(
                            replace(
                                record,
                                status=PROFILE_JOB_STATUS_LEASED,
                                updated_at_iso=now,
                                attempts=record.attempts + 1,
                                lease_owner=owner,
                                lease_until_iso=lease_until,
                            )
                        )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_claim_failed") from exc
        return claimed

    def renew_profile_job_lease(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        lease_owner: str,
        lease_until_iso: str,
        now_iso: str,
    ) -> bool:
        key = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        owner = normalize_profile_lease_owner(lease_owner)
        lease_until = normalize_profile_lease_timestamp(lease_until_iso)
        now = normalize_profile_lease_timestamp(now_iso)
        self.initialize()
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    SQLITE_RECENT_PROFILE_JOB_RENEW_LEASE,
                    (
                        lease_until,
                        now,
                        *key,
                        PROFILE_JOB_STATUS_LEASED,
                        owner,
                    ),
                )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_renew_failed") from exc
        return updated.rowcount == 1

    def complete_profile_job(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        lease_owner: str,
        completed_at_iso: str,
    ) -> bool:
        key = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        owner = normalize_profile_lease_owner(lease_owner)
        completed_at = normalize_profile_lease_timestamp(completed_at_iso)
        self.initialize()
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    SQLITE_RECENT_PROFILE_JOB_COMPLETE,
                    (
                        PROFILE_JOB_STATUS_COMPLETED,
                        completed_at,
                        *key,
                        PROFILE_JOB_STATUS_LEASED,
                        owner,
                    ),
                )
                if updated.rowcount == 1:
                    connection.execute(
                        SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                        (
                            PROFILE_STATUS_ANALYZED,
                            *key,
                            PROFILE_STATUS_ANALYZED,
                            PROFILE_STATUS_ANALYZED,
                        ),
                    )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_complete_failed") from exc
        return updated.rowcount == 1

    def fail_profile_job(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        lease_owner: str,
        failed_at_iso: str,
        error_code: str,
        retry: bool,
        aged_out: bool = False,
    ) -> bool:
        key = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        owner = normalize_profile_lease_owner(lease_owner)
        failed_at = normalize_profile_lease_timestamp(failed_at_iso)
        safe_error_code = normalize_profile_error_code(error_code)
        next_status = next_profile_job_status(retry=retry, aged_out=aged_out)
        next_profile_status = PROFILE_STATUS_RETRY_PENDING if retry else PROFILE_STATUS_FAILED
        self.initialize()
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    SQLITE_RECENT_PROFILE_JOB_FAIL,
                    (
                        next_status,
                        failed_at,
                        safe_error_code,
                        failed_at,
                        *key,
                        PROFILE_JOB_STATUS_LEASED,
                        owner,
                    ),
                )
                if updated.rowcount == 1:
                    connection.execute(
                        SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                        (
                            next_profile_status,
                            *key,
                            next_profile_status,
                            next_profile_status,
                        ),
                    )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_fail_failed") from exc
        return updated.rowcount == 1

    def requeue_failed_profile_jobs(
        self,
        *,
        max_jobs: int,
        requeued_at_iso: str,
        dry_run: bool,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
    ) -> RecentProfileJobRequeueResult:
        limit = normalize_profile_claim_limit(max_jobs)
        requeued_at = normalize_profile_lease_timestamp(requeued_at_iso)
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                count_row = connection.execute(
                    SQLITE_RECENT_PROFILE_JOB_REQUEUE_COUNT,
                    (
                        engine_filter,
                        engine_filter,
                        source_kind_filter,
                        source_kind_filter,
                        source_key_filter,
                        source_key_filter,
                        PROFILE_JOB_STATUS_FAILED,
                    ),
                ).fetchone()
                matched = int(count_row[0]) if count_row else 0
                selected_rows = []
                if limit > 0:
                    selected_rows = connection.execute(
                        SQLITE_RECENT_PROFILE_JOB_REQUEUE_SELECT,
                        (
                            engine_filter,
                            engine_filter,
                            source_kind_filter,
                            source_kind_filter,
                            source_key_filter,
                            source_key_filter,
                            PROFILE_JOB_STATUS_FAILED,
                            limit,
                        ),
                    ).fetchall()
                requeued = 0
                if not dry_run:
                    for row in selected_rows:
                        updated = connection.execute(
                            SQLITE_RECENT_PROFILE_JOB_REQUEUE_UPDATE,
                            (
                                PROFILE_JOB_STATUS_PENDING,
                                requeued_at,
                                str(row[0]),
                                str(row[1]),
                                str(row[2]),
                                str(row[3]),
                                PROFILE_JOB_STATUS_FAILED,
                            ),
                        )
                        if updated.rowcount == 1:
                            requeued += 1
                            connection.execute(
                                SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_REQUEUE,
                                (
                                    PROFILE_STATUS_PENDING,
                                    str(row[0]),
                                    str(row[1]),
                                    str(row[2]),
                                    str(row[3]),
                                    PROFILE_STATUS_FAILED,
                                ),
                            )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_requeue_failed") from exc
        return RecentProfileJobRequeueResult(
            matched_failed_jobs=matched,
            selected_failed_jobs=len(selected_rows),
            requeued_jobs=requeued,
            dry_run=dry_run,
        )

    def age_out_profile_jobs(
        self,
        *,
        cutoff_iso: str,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
    ) -> int:
        now = normalize_profile_lease_timestamp(now_iso)
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                keys = connection.execute(
                    SQLITE_RECENT_PROFILE_JOB_AGE_OUT_SELECT,
                    (
                        engine_filter,
                        engine_filter,
                        source_kind_filter,
                        source_kind_filter,
                        source_key_filter,
                        source_key_filter,
                        str(cutoff_iso),
                        PROFILE_JOB_STATUS_PENDING,
                        PROFILE_JOB_STATUS_LEASED,
                        now,
                    ),
                ).fetchall()
                for key in keys:
                    connection.execute(
                        SQLITE_RECENT_PROFILE_JOB_AGE_OUT_UPDATE,
                        (
                            PROFILE_JOB_STATUS_AGED_OUT,
                            now,
                            PROFILE_JOB_AGED_OUT_ERROR_CODE,
                            now,
                            *key,
                        ),
                    )
                    connection.execute(
                        SQLITE_RECENT_QUERY_SUMMARY_AGE_OUT_UPDATE,
                        (
                            PROFILE_STATUS_FAILED,
                            *key,
                            PROFILE_STATUS_PENDING,
                            PROFILE_STATUS_PROCESSING,
                            PROFILE_STATUS_RETRY_PENDING,
                        ),
                    )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_age_out_failed") from exc
        return len(keys)

    def summarize_profile_backlog_health(
        self,
        *,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
        window_start_iso: str | None = None,
        window_hours: int | None = None,
    ) -> RecentProfileBacklogHealth:
        now = normalize_profile_lease_timestamp(now_iso)
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    SQLITE_RECENT_PROFILE_BACKLOG_HEALTH,
                    (
                        PROFILE_JOB_STATUS_PENDING,
                        PROFILE_STATUS_RETRY_PENDING,
                        PROFILE_JOB_STATUS_PENDING,
                        PROFILE_STATUS_RETRY_PENDING,
                        PROFILE_JOB_STATUS_LEASED,
                        PROFILE_JOB_STATUS_LEASED,
                        now,
                        PROFILE_JOB_STATUS_FAILED,
                        engine_filter,
                        engine_filter,
                        source_kind_filter,
                        source_kind_filter,
                        source_key_filter,
                        source_key_filter,
                    ),
                ).fetchone()
                window_row = None
                if window_start_iso is not None:
                    window_row = connection.execute(
                        SQLITE_RECENT_PROFILE_WINDOW_OUTCOMES,
                        (
                            PROFILE_JOB_STATUS_COMPLETED,
                            PROFILE_JOB_STATUS_FAILED,
                            PROFILE_JOB_STATUS_AGED_OUT,
                            PROFILE_JOB_STATUS_COMPLETED,
                            PROFILE_JOB_STATUS_FAILED,
                            PROFILE_JOB_STATUS_AGED_OUT,
                            PROFILE_JOB_NOT_FOUND_ERROR_CODE,
                            normalize_profile_lease_timestamp(window_start_iso),
                            engine_filter,
                            engine_filter,
                            source_kind_filter,
                            source_kind_filter,
                            source_key_filter,
                            source_key_filter,
                        ),
                    ).fetchone()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_backlog_health_failed") from exc
        if row is None:
            return RecentProfileBacklogHealth()
        return profile_backlog_health_from_counts(
            pending_jobs=row[0],
            retry_pending_jobs=row[1],
            leased_jobs=row[2],
            stale_leased_jobs=row[3],
            failed_jobs=row[4],
            window_hours=window_hours if window_start_iso is not None else None,
            window_counts=window_row,
        )

    def store_analysis_cache_records(
        self,
        records: Iterable[RecentAnalysisCacheRecord],
    ) -> int:
        rows: list[dict[str, object]] = []
        for record in records:
            row = analysis_cache_record_to_storage_row(record)
            if row is not None:
                rows.append(row)
        if not rows:
            return 0
        self.initialize()
        try:
            with self._connect() as connection:
                connection.executemany(SQLITE_RECENT_ANALYSIS_CACHE_UPSERT, rows)
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_analysis_cache_upsert_failed") from exc
        return len(rows)

    def load_analysis_cache_record(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        profile_fingerprint: str,
        analyzer_contract: str,
    ) -> RecentAnalysisCacheRecord | None:
        key = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        fingerprint = normalize_analysis_cache_fingerprint(profile_fingerprint)
        contract = normalize_analysis_cache_contract(analyzer_contract)
        if not key[3] or not fingerprint or not contract:
            return None
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    SQLITE_RECENT_ANALYSIS_CACHE_SELECT,
                    (*key, fingerprint, contract),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_analysis_cache_load_failed") from exc
        return analysis_cache_record_from_storage_values(row) if row else None

    def store_profile_artifact_records(
        self,
        records: Iterable[RecentProfileArtifactRecord],
    ) -> int:
        rows: list[dict[str, object]] = []
        for record in records:
            row = profile_artifact_record_to_storage_row(record)
            if row is not None:
                rows.append(row)
        if not rows:
            return 0
        self.initialize()
        try:
            with self._connect() as connection:
                connection.executemany(SQLITE_RECENT_PROFILE_ARTIFACT_UPSERT, rows)
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_artifact_upsert_failed") from exc
        return len(rows)

    def load_profile_artifact_record(
        self,
        *,
        engine: str,
        source_kind: str,
        source_key: str,
        query_id: str,
        profile_fingerprint: str,
        artifact_contract: str,
    ) -> RecentProfileArtifactRecord | None:
        key = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        fingerprint = normalize_analysis_cache_fingerprint(profile_fingerprint)
        contract = normalize_analysis_cache_contract(artifact_contract)
        if not key[3] or not fingerprint or not contract:
            return None
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    SQLITE_RECENT_PROFILE_ARTIFACT_SELECT,
                    (*key, fingerprint, contract),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_artifact_load_failed") from exc
        return profile_artifact_record_from_storage_values(row) if row else None

    def prune_history(
        self,
        *,
        policy: RecentHistoryRetentionPolicy,
    ) -> RecentHistoryRetentionResult:
        safe_policy = safe_retention_policy(policy)
        if not (
            safe_policy.summary_cutoff_iso
            or safe_policy.profile_job_cutoff_iso
            or safe_policy.analysis_cache_cutoff_iso
            or safe_policy.profile_artifact_cutoff_iso
        ):
            return RecentHistoryRetentionResult()
        self.initialize()
        try:
            with self._connect() as connection:
                summaries_deleted = execute_prune(
                    connection,
                    SQLITE_RECENT_QUERY_SUMMARY_PRUNE,
                    (safe_policy.summary_cutoff_iso,),
                    enabled=bool(safe_policy.summary_cutoff_iso),
                )
                profile_jobs_deleted = execute_prune(
                    connection,
                    SQLITE_RECENT_PROFILE_JOB_PRUNE,
                    (
                        safe_policy.profile_job_cutoff_iso,
                        PROFILE_JOB_STATUS_COMPLETED,
                        PROFILE_JOB_STATUS_FAILED,
                        PROFILE_JOB_STATUS_AGED_OUT,
                    ),
                    enabled=bool(safe_policy.profile_job_cutoff_iso),
                )
                analysis_cache_deleted = execute_prune(
                    connection,
                    SQLITE_RECENT_ANALYSIS_CACHE_PRUNE,
                    (safe_policy.analysis_cache_cutoff_iso,),
                    enabled=bool(safe_policy.analysis_cache_cutoff_iso),
                )
                profile_artifacts_deleted = execute_prune(
                    connection,
                    SQLITE_RECENT_PROFILE_ARTIFACT_PRUNE,
                    (safe_policy.profile_artifact_cutoff_iso,),
                    enabled=bool(safe_policy.profile_artifact_cutoff_iso),
                )
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_history_prune_failed") from exc
        return RecentHistoryRetentionResult(
            summaries_deleted=summaries_deleted,
            profile_jobs_deleted=profile_jobs_deleted,
            analysis_cache_deleted=analysis_cache_deleted,
            profile_artifacts_deleted=profile_artifacts_deleted,
        )

    def count_summaries(self) -> int:
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) FROM recent_query_summary").fetchone()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_history_count_failed") from exc
        return int(row[0]) if row else 0

    def load_payloads(self) -> list[dict[str, object]]:
        self.initialize()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        summary.payload_json,
                        summary.profile_status,
                        NULL AS analysis_payload_json,
                        job.last_error_code
                    FROM recent_query_summary AS summary
                    LEFT JOIN recent_profile_job AS job
                        ON job.engine = summary.engine
                        AND job.source_kind = summary.source_kind
                        AND job.source_key = summary.source_key
                        AND job.query_id = summary.query_id
                    ORDER BY
                        COALESCE(end_time, start_time, recorded_at_iso) DESC,
                        summary.query_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_history_load_failed") from exc
        return recent_summary_payloads_from_rows(rows)

    def load_materialized_payloads(
        self,
        *,
        limit: int | None = None,
        details_ready_only: bool = False,
    ) -> list[dict[str, object]]:
        self.initialize()
        safe_limit = -1 if limit is None else max(0, int(limit))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    SQLITE_RECENT_MATERIALIZED_PAYLOADS_SELECT,
                    {
                        "limit": safe_limit,
                        "details_ready_only": int(bool(details_ready_only)),
                        "analyzed_profile_status": PROFILE_STATUS_ANALYZED,
                        "artifact_contract": PROFILE_ARTIFACT_DEFAULT_CONTRACT,
                        "artifact_status": PROFILE_ARTIFACT_STATUS_AVAILABLE,
                        "analyzer_contract": ANALYSIS_CACHE_DEFAULT_CONTRACT,
                        "analysis_status": ANALYSIS_CACHE_STATUS_READY,
                    },
                ).fetchall()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_history_materialized_load_failed") from exc
        return recent_summary_payloads_from_rows(rows)

    def load_profile_jobs(self) -> list[dict[str, object]]:
        self.initialize()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        query_id,
                        status,
                        priority_score,
                        priority_level,
                        priority_reasons_json,
                        attempts,
                        lease_owner,
                        lease_until_iso,
                        last_error_code,
                        last_error_at_iso
                    FROM recent_profile_job
                    ORDER BY priority_score DESC, query_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RecentHistoryStoreError("sqlite_recent_profile_job_load_failed") from exc
        return [
            {
                "query_id": str(row[0]),
                "status": str(row[1]),
                "priority_score": int(row[2]),
                "priority_level": str(row[3]),
                "priority_reasons": json.loads(str(row[4])),
                "attempts": int(row[5]),
                "lease_owner": row[6],
                "lease_until_iso": row[7],
                "last_error_code": row[8],
                "last_error_at_iso": row[9],
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection


SQLITE_RECENT_QUERY_SUMMARY_DDL = """
CREATE TABLE IF NOT EXISTS recent_query_summary (
    schema_version INTEGER NOT NULL,
    engine TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    query_id TEXT NOT NULL,
    recorded_at_iso TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    duration_ms INTEGER,
    status TEXT,
    query_state TEXT,
    user_label TEXT,
    pool_label TEXT,
    query_type TEXT,
    sql_verb TEXT,
    statement_present INTEGER NOT NULL,
    admission_result TEXT,
    admission_wait_ms INTEGER,
    rows_produced INTEGER,
    bytes_read INTEGER,
    bytes_sent INTEGER,
    memory_aggregate_peak INTEGER,
    memory_per_node_peak INTEGER,
    suspicion_score INTEGER NOT NULL,
    suspicion_level TEXT NOT NULL,
    suspicion_reasons_json TEXT NOT NULL,
    selected INTEGER NOT NULL,
    selected_reason TEXT,
    profile_status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (engine, source_kind, source_key, query_id)
);
CREATE INDEX IF NOT EXISTS recent_query_summary_time_idx
    ON recent_query_summary(engine, source_kind, source_key, end_time, start_time);
CREATE INDEX IF NOT EXISTS recent_query_summary_suspicion_idx
    ON recent_query_summary(engine, source_kind, source_key, suspicion_score DESC);
CREATE INDEX IF NOT EXISTS recent_query_summary_status_idx
    ON recent_query_summary(engine, source_kind, source_key, status);
CREATE INDEX IF NOT EXISTS recent_query_summary_recorded_idx
    ON recent_query_summary(recorded_at_iso);
CREATE INDEX IF NOT EXISTS recent_query_summary_latest_idx
    ON recent_query_summary(
        COALESCE(end_time, start_time, recorded_at_iso) DESC,
        query_id
    );
CREATE TABLE IF NOT EXISTS recent_profile_job (
    schema_version INTEGER NOT NULL,
    engine TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    query_id TEXT NOT NULL,
    created_at_iso TEXT NOT NULL,
    updated_at_iso TEXT NOT NULL,
    summary_recorded_at_iso TEXT NOT NULL,
    summary_end_time TEXT,
    priority_score INTEGER NOT NULL,
    priority_level TEXT NOT NULL,
    priority_reasons_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until_iso TEXT,
    last_error_code TEXT,
    last_error_at_iso TEXT,
    PRIMARY KEY (engine, source_kind, source_key, query_id)
);
CREATE INDEX IF NOT EXISTS recent_profile_job_status_priority_idx
    ON recent_profile_job(status, priority_score DESC, summary_end_time);
CREATE INDEX IF NOT EXISTS recent_profile_job_source_status_idx
    ON recent_profile_job(engine, source_kind, source_key, status);
CREATE INDEX IF NOT EXISTS recent_profile_job_status_updated_idx
    ON recent_profile_job(status, updated_at_iso);
CREATE TABLE IF NOT EXISTS recent_analysis_cache (
    schema_version INTEGER NOT NULL,
    engine TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    query_id TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    analyzer_contract TEXT NOT NULL,
    recorded_at_iso TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (
        engine,
        source_kind,
        source_key,
        query_id,
        profile_fingerprint,
        analyzer_contract
    )
);
CREATE INDEX IF NOT EXISTS recent_analysis_cache_recorded_idx
    ON recent_analysis_cache(recorded_at_iso);
CREATE TABLE IF NOT EXISTS recent_profile_artifact (
    schema_version INTEGER NOT NULL,
    engine TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    query_id TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    artifact_contract TEXT NOT NULL,
    recorded_at_iso TEXT NOT NULL,
    status TEXT NOT NULL,
    storage_kind TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    size_bytes INTEGER,
    PRIMARY KEY (
        engine,
        source_kind,
        source_key,
        query_id,
        profile_fingerprint,
        artifact_contract
    )
);
CREATE INDEX IF NOT EXISTS recent_profile_artifact_recorded_idx
    ON recent_profile_artifact(recorded_at_iso);
CREATE INDEX IF NOT EXISTS recent_profile_artifact_storage_idx
    ON recent_profile_artifact(storage_kind, storage_key);
"""

SQLITE_RECENT_QUERY_SUMMARY_UPSERT = """
INSERT INTO recent_query_summary (
    schema_version,
    engine,
    source_kind,
    source_key,
    query_id,
    recorded_at_iso,
    start_time,
    end_time,
    duration_ms,
    status,
    query_state,
    user_label,
    pool_label,
    query_type,
    sql_verb,
    statement_present,
    admission_result,
    admission_wait_ms,
    rows_produced,
    bytes_read,
    bytes_sent,
    memory_aggregate_peak,
    memory_per_node_peak,
    suspicion_score,
    suspicion_level,
    suspicion_reasons_json,
    selected,
    selected_reason,
    profile_status,
    payload_json
)
VALUES (
    :schema_version,
    :engine,
    :source_kind,
    :source_key,
    :query_id,
    :recorded_at_iso,
    :start_time,
    :end_time,
    :duration_ms,
    :status,
    :query_state,
    :user_label,
    :pool_label,
    :query_type,
    :sql_verb,
    :statement_present,
    :admission_result,
    :admission_wait_ms,
    :rows_produced,
    :bytes_read,
    :bytes_sent,
    :memory_aggregate_peak,
    :memory_per_node_peak,
    :suspicion_score,
    :suspicion_level,
    :suspicion_reasons_json,
    :selected,
    :selected_reason,
    :profile_status,
    :payload_json
)
ON CONFLICT(engine, source_kind, source_key, query_id) DO UPDATE SET
    schema_version = excluded.schema_version,
    recorded_at_iso = excluded.recorded_at_iso,
    start_time = excluded.start_time,
    end_time = excluded.end_time,
    duration_ms = excluded.duration_ms,
    status = excluded.status,
    query_state = excluded.query_state,
    user_label = excluded.user_label,
    pool_label = excluded.pool_label,
    query_type = excluded.query_type,
    sql_verb = excluded.sql_verb,
    statement_present = excluded.statement_present,
    admission_result = excluded.admission_result,
    admission_wait_ms = excluded.admission_wait_ms,
    rows_produced = excluded.rows_produced,
    bytes_read = excluded.bytes_read,
    bytes_sent = excluded.bytes_sent,
    memory_aggregate_peak = excluded.memory_aggregate_peak,
    memory_per_node_peak = excluded.memory_per_node_peak,
    suspicion_score = excluded.suspicion_score,
    suspicion_level = excluded.suspicion_level,
    suspicion_reasons_json = excluded.suspicion_reasons_json,
    selected = excluded.selected,
    selected_reason = excluded.selected_reason,
    profile_status = CASE
        WHEN excluded.profile_status = 'not_collected'
            AND recent_query_summary.profile_status <> 'not_collected'
        THEN recent_query_summary.profile_status
        ELSE excluded.profile_status
    END,
    payload_json = excluded.payload_json
"""

SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE = """
UPDATE recent_query_summary
SET profile_status = ?
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND NOT (
        ? = 'pending'
        AND profile_status <> 'not_collected'
    )
    AND NOT (
        ? = 'processing'
        AND profile_status IN ('analyzed', 'failed')
    )
"""

SQLITE_RECENT_QUERY_SUMMARY_PROFILE_STATUS_REQUEUE = """
UPDATE recent_query_summary
SET profile_status = ?
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND profile_status = ?
"""

SQLITE_RECENT_MATERIALIZED_PAYLOADS_SELECT = """
WITH newest_summary_keys AS (
    SELECT
        summary.engine,
        summary.source_kind,
        summary.source_key,
        summary.query_id,
        COALESCE(summary.end_time, summary.start_time, summary.recorded_at_iso) AS sort_time
    FROM recent_query_summary AS summary
    WHERE
        :details_ready_only = 0
        OR (
            summary.profile_status = :analyzed_profile_status
            AND EXISTS (
                SELECT 1
                FROM recent_profile_artifact AS ready_artifact
                JOIN recent_analysis_cache AS ready_cache
                    ON ready_cache.engine = ready_artifact.engine
                    AND ready_cache.source_kind = ready_artifact.source_kind
                    AND ready_cache.source_key = ready_artifact.source_key
                    AND ready_cache.query_id = ready_artifact.query_id
                    AND ready_cache.profile_fingerprint = ready_artifact.profile_fingerprint
                    AND ready_cache.analyzer_contract = :analyzer_contract
                    AND ready_cache.status = :analysis_status
                WHERE
                    ready_artifact.engine = summary.engine
                    AND ready_artifact.source_kind = summary.source_kind
                    AND ready_artifact.source_key = summary.source_key
                    AND ready_artifact.query_id = summary.query_id
                    AND ready_artifact.artifact_contract = :artifact_contract
                    AND ready_artifact.status = :artifact_status
                    AND ready_artifact.profile_fingerprint = (
                        SELECT candidate.profile_fingerprint
                        FROM recent_profile_artifact AS candidate
                        WHERE
                            candidate.engine = summary.engine
                            AND candidate.source_kind = summary.source_kind
                            AND candidate.source_key = summary.source_key
                            AND candidate.query_id = summary.query_id
                            AND candidate.artifact_contract = :artifact_contract
                            AND candidate.status = :artifact_status
                        ORDER BY
                            candidate.recorded_at_iso DESC,
                            candidate.profile_fingerprint DESC
                        LIMIT 1
                    )
            )
        )
    ORDER BY sort_time DESC, query_id
    LIMIT :limit
)
SELECT
    summary.payload_json,
    summary.profile_status,
    analysis_cache.payload_json,
    job.last_error_code
FROM newest_summary_keys AS newest
JOIN recent_query_summary AS summary
    ON summary.engine = newest.engine
    AND summary.source_kind = newest.source_kind
    AND summary.source_key = newest.source_key
    AND summary.query_id = newest.query_id
LEFT JOIN recent_profile_job AS job
    ON job.engine = summary.engine
    AND job.source_kind = summary.source_kind
    AND job.source_key = summary.source_key
    AND job.query_id = summary.query_id
LEFT JOIN recent_profile_artifact AS artifact
    ON artifact.engine = summary.engine
    AND artifact.source_kind = summary.source_kind
    AND artifact.source_key = summary.source_key
    AND artifact.query_id = summary.query_id
    AND artifact.artifact_contract = :artifact_contract
    AND artifact.status = :artifact_status
    AND artifact.profile_fingerprint = (
        SELECT candidate.profile_fingerprint
        FROM recent_profile_artifact AS candidate
        WHERE
            candidate.engine = summary.engine
            AND candidate.source_kind = summary.source_kind
            AND candidate.source_key = summary.source_key
            AND candidate.query_id = summary.query_id
            AND candidate.artifact_contract = :artifact_contract
            AND candidate.status = :artifact_status
        ORDER BY candidate.recorded_at_iso DESC, candidate.profile_fingerprint DESC
        LIMIT 1
    )
LEFT JOIN recent_analysis_cache AS analysis_cache
    ON analysis_cache.engine = summary.engine
    AND analysis_cache.source_kind = summary.source_kind
    AND analysis_cache.source_key = summary.source_key
    AND analysis_cache.query_id = summary.query_id
    AND analysis_cache.profile_fingerprint = artifact.profile_fingerprint
    AND analysis_cache.analyzer_contract = :analyzer_contract
    AND analysis_cache.status = :analysis_status
ORDER BY
    newest.sort_time DESC,
    summary.query_id
"""

SQLITE_RECENT_PROFILE_JOB_INSERT = """
INSERT INTO recent_profile_job (
    schema_version,
    engine,
    source_kind,
    source_key,
    query_id,
    created_at_iso,
    updated_at_iso,
    summary_recorded_at_iso,
    summary_end_time,
    priority_score,
    priority_level,
    priority_reasons_json,
    status,
    attempts,
    lease_owner,
    lease_until_iso,
    last_error_code,
    last_error_at_iso
)
VALUES (
    :schema_version,
    :engine,
    :source_kind,
    :source_key,
    :query_id,
    :created_at_iso,
    :updated_at_iso,
    :summary_recorded_at_iso,
    :summary_end_time,
    :priority_score,
    :priority_level,
    :priority_reasons_json,
    :status,
    :attempts,
    :lease_owner,
    :lease_until_iso,
    :last_error_code,
    :last_error_at_iso
)
ON CONFLICT(engine, source_kind, source_key, query_id) DO NOTHING
"""

SQLITE_PROFILE_JOB_STORAGE_SELECT = ", ".join(PROFILE_JOB_STORAGE_COLUMNS)

SQLITE_RECENT_PROFILE_JOB_CLAIM_SELECT = f"""
SELECT {SQLITE_PROFILE_JOB_STORAGE_SELECT}
FROM recent_profile_job
WHERE
    (? IS NULL OR engine = ?)
    AND (? IS NULL OR source_kind = ?)
    AND (? IS NULL OR source_key = ?)
    AND (
        status = ?
        OR (status = ? AND lease_until_iso IS NOT NULL AND lease_until_iso <= ?)
    )
ORDER BY priority_score DESC, summary_end_time DESC, query_id
LIMIT ?
"""

SQLITE_RECENT_PROFILE_JOB_CLAIM_UPDATE = """
UPDATE recent_profile_job
SET
    status = ?,
    lease_owner = ?,
    lease_until_iso = ?,
    updated_at_iso = ?,
    attempts = attempts + 1
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND (
        status = ?
        OR (status = ? AND lease_until_iso IS NOT NULL AND lease_until_iso <= ?)
    )
"""

SQLITE_RECENT_PROFILE_JOB_RENEW_LEASE = """
UPDATE recent_profile_job
SET
    lease_until_iso = ?,
    updated_at_iso = ?
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND status = ?
    AND lease_owner = ?
"""

SQLITE_RECENT_PROFILE_JOB_COMPLETE = """
UPDATE recent_profile_job
SET
    status = ?,
    updated_at_iso = ?,
    lease_owner = NULL,
    lease_until_iso = NULL,
    last_error_code = NULL,
    last_error_at_iso = NULL
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND status = ?
    AND lease_owner = ?
"""

SQLITE_RECENT_PROFILE_JOB_FAIL = """
UPDATE recent_profile_job
SET
    status = ?,
    updated_at_iso = ?,
    lease_owner = NULL,
    lease_until_iso = NULL,
    last_error_code = ?,
    last_error_at_iso = ?
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND status = ?
    AND lease_owner = ?
"""

SQLITE_RECENT_PROFILE_JOB_REQUEUE_COUNT = """
SELECT COUNT(*)
FROM recent_profile_job
WHERE
    (? IS NULL OR engine = ?)
    AND (? IS NULL OR source_kind = ?)
    AND (? IS NULL OR source_key = ?)
    AND status = ?
"""

SQLITE_RECENT_PROFILE_JOB_REQUEUE_SELECT = """
SELECT engine, source_kind, source_key, query_id
FROM recent_profile_job
WHERE
    (? IS NULL OR engine = ?)
    AND (? IS NULL OR source_kind = ?)
    AND (? IS NULL OR source_key = ?)
    AND status = ?
ORDER BY priority_score DESC, updated_at_iso, query_id
LIMIT ?
"""

SQLITE_RECENT_PROFILE_JOB_REQUEUE_UPDATE = """
UPDATE recent_profile_job
SET
    status = ?,
    updated_at_iso = ?,
    attempts = 0,
    lease_owner = NULL,
    lease_until_iso = NULL,
    last_error_code = NULL,
    last_error_at_iso = NULL
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND status = ?
"""

SQLITE_RECENT_PROFILE_BACKLOG_HEALTH = """
SELECT
    COALESCE(SUM(CASE
        WHEN job.status = ?
            AND COALESCE(summary.profile_status, '') <> ?
            AND job.last_error_code IS NULL
        THEN 1 ELSE 0 END), 0) AS pending_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = ?
            AND (
                summary.profile_status = ?
                OR job.last_error_code IS NOT NULL
            )
        THEN 1 ELSE 0 END), 0) AS retry_pending_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = ?
        THEN 1 ELSE 0 END), 0) AS leased_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = ?
            AND job.lease_until_iso IS NOT NULL
            AND job.lease_until_iso <= ?
        THEN 1 ELSE 0 END), 0) AS stale_leased_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = ?
        THEN 1 ELSE 0 END), 0) AS failed_jobs
FROM recent_profile_job AS job
LEFT JOIN recent_query_summary AS summary
    ON summary.engine = job.engine
    AND summary.source_kind = job.source_kind
    AND summary.source_key = job.source_key
    AND summary.query_id = job.query_id
WHERE
    (? IS NULL OR job.engine = ?)
    AND (? IS NULL OR job.source_kind = ?)
    AND (? IS NULL OR job.source_key = ?)
"""

SQLITE_RECENT_PROFILE_WINDOW_OUTCOMES = """
SELECT
    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0)
FROM recent_profile_job
WHERE
    (status IN (?, ?) OR (status = ? AND last_error_code = ?))
    AND updated_at_iso >= ?
    AND (? IS NULL OR engine = ?)
    AND (? IS NULL OR source_kind = ?)
    AND (? IS NULL OR source_key = ?)
"""

SQLITE_RECENT_PROFILE_JOB_AGE_OUT_SELECT = """
SELECT engine, source_kind, source_key, query_id
FROM recent_profile_job
WHERE
    (? IS NULL OR engine = ?)
    AND (? IS NULL OR source_kind = ?)
    AND (? IS NULL OR source_key = ?)
    AND summary_end_time IS NOT NULL
    AND summary_end_time < ?
    AND (
        status = ?
        OR (status = ? AND lease_until_iso IS NOT NULL AND lease_until_iso <= ?)
    )
"""

SQLITE_RECENT_PROFILE_JOB_AGE_OUT_UPDATE = """
UPDATE recent_profile_job
SET
    status = ?,
    lease_owner = NULL,
    lease_until_iso = NULL,
    updated_at_iso = ?,
    last_error_code = ?,
    last_error_at_iso = ?
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
"""

SQLITE_RECENT_QUERY_SUMMARY_AGE_OUT_UPDATE = """
UPDATE recent_query_summary
SET profile_status = ?
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND profile_status IN (?, ?, ?)
"""

SQLITE_ANALYSIS_CACHE_STORAGE_SELECT = ", ".join(ANALYSIS_CACHE_STORAGE_COLUMNS)

SQLITE_RECENT_ANALYSIS_CACHE_UPSERT = """
INSERT INTO recent_analysis_cache (
    schema_version,
    engine,
    source_kind,
    source_key,
    query_id,
    profile_fingerprint,
    analyzer_contract,
    recorded_at_iso,
    status,
    payload_json
)
VALUES (
    :schema_version,
    :engine,
    :source_kind,
    :source_key,
    :query_id,
    :profile_fingerprint,
    :analyzer_contract,
    :recorded_at_iso,
    :status,
    :payload_json
)
ON CONFLICT(
    engine,
    source_kind,
    source_key,
    query_id,
    profile_fingerprint,
    analyzer_contract
) DO UPDATE SET
    schema_version = excluded.schema_version,
    recorded_at_iso = excluded.recorded_at_iso,
    status = excluded.status,
    payload_json = excluded.payload_json
"""

SQLITE_RECENT_ANALYSIS_CACHE_SELECT = f"""
SELECT {SQLITE_ANALYSIS_CACHE_STORAGE_SELECT}
FROM recent_analysis_cache
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND profile_fingerprint = ?
    AND analyzer_contract = ?
"""

SQLITE_PROFILE_ARTIFACT_STORAGE_SELECT = ", ".join(PROFILE_ARTIFACT_STORAGE_COLUMNS)

SQLITE_RECENT_PROFILE_ARTIFACT_UPSERT = """
INSERT INTO recent_profile_artifact (
    schema_version,
    engine,
    source_kind,
    source_key,
    query_id,
    profile_fingerprint,
    artifact_contract,
    recorded_at_iso,
    status,
    storage_kind,
    storage_key,
    size_bytes
)
VALUES (
    :schema_version,
    :engine,
    :source_kind,
    :source_key,
    :query_id,
    :profile_fingerprint,
    :artifact_contract,
    :recorded_at_iso,
    :status,
    :storage_kind,
    :storage_key,
    :size_bytes
)
ON CONFLICT(
    engine,
    source_kind,
    source_key,
    query_id,
    profile_fingerprint,
    artifact_contract
) DO UPDATE SET
    schema_version = excluded.schema_version,
    recorded_at_iso = excluded.recorded_at_iso,
    status = excluded.status,
    storage_kind = excluded.storage_kind,
    storage_key = excluded.storage_key,
    size_bytes = excluded.size_bytes
"""

SQLITE_RECENT_PROFILE_ARTIFACT_SELECT = f"""
SELECT {SQLITE_PROFILE_ARTIFACT_STORAGE_SELECT}
FROM recent_profile_artifact
WHERE
    engine = ?
    AND source_kind = ?
    AND source_key = ?
    AND query_id = ?
    AND profile_fingerprint = ?
    AND artifact_contract = ?
"""

SQLITE_RECENT_QUERY_SUMMARY_PRUNE = """
DELETE FROM recent_query_summary
WHERE recorded_at_iso < ?
"""

SQLITE_RECENT_PROFILE_JOB_PRUNE = """
DELETE FROM recent_profile_job
WHERE
    updated_at_iso < ?
    AND status IN (?, ?, ?)
"""

SQLITE_RECENT_ANALYSIS_CACHE_PRUNE = """
DELETE FROM recent_analysis_cache
WHERE recorded_at_iso < ?
"""

SQLITE_RECENT_PROFILE_ARTIFACT_PRUNE = """
DELETE FROM recent_profile_artifact
WHERE recorded_at_iso < ?
"""


def execute_prune(
    connection: sqlite3.Connection,
    statement: str,
    params: tuple[object, ...],
    *,
    enabled: bool,
) -> int:
    if not enabled:
        return 0
    cursor = connection.execute(statement, params)
    return max(0, int(cursor.rowcount))


def recent_summary_payloads_from_rows(rows: Iterable[object]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            continue
        payload["profile_status"] = str(row[1] or PROFILE_STATUS_NOT_COLLECTED)
        if len(row) > 2 and row[2] is not None:
            analysis_payload = json.loads(str(row[2]))
            if isinstance(analysis_payload, dict):
                payload["analysis_cache_payload"] = safe_analysis_cache_payload(analysis_payload)
        if len(row) > 3:
            error_code = safe_optional_profile_error_code(row[3])
            if error_code is not None:
                payload["profile_last_error_code"] = error_code
        payloads.append(payload)
    return payloads


def record_to_sqlite_row(record: RecentSummaryHistoryRecord) -> dict[str, object]:
    payload = record.safe_payload()
    return {
        "schema_version": record.schema_version,
        "engine": record.engine,
        "source_kind": record.source_kind,
        "source_key": record.source_key,
        "query_id": record.query_id,
        "recorded_at_iso": record.recorded_at_iso,
        "start_time": record.start_time,
        "end_time": record.end_time,
        "duration_ms": record.duration_ms,
        "status": record.status,
        "query_state": record.query_state,
        "user_label": record.user,
        "pool_label": record.pool,
        "query_type": record.query_type,
        "sql_verb": record.sql_verb,
        "statement_present": 1 if record.statement_present else 0,
        "admission_result": record.admission_result,
        "admission_wait_ms": record.admission_wait_ms,
        "rows_produced": record.rows_produced,
        "bytes_read": record.bytes_read,
        "bytes_sent": record.bytes_sent,
        "memory_aggregate_peak": record.memory_aggregate_peak,
        "memory_per_node_peak": record.memory_per_node_peak,
        "suspicion_score": record.suspicion_score,
        "suspicion_level": record.suspicion_level,
        "suspicion_reasons_json": json.dumps(record.suspicion_reasons, sort_keys=True),
        "selected": 1 if record.selected else 0,
        "selected_reason": record.selected_reason,
        "profile_status": record.profile_status,
        "payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }


def profile_job_to_sqlite_row(record: RecentProfileJobRecord) -> dict[str, object]:
    return {
        "schema_version": record.schema_version,
        "engine": record.engine,
        "source_kind": record.source_kind,
        "source_key": record.source_key,
        "query_id": record.query_id,
        "created_at_iso": record.created_at_iso,
        "updated_at_iso": record.updated_at_iso,
        "summary_recorded_at_iso": record.summary_recorded_at_iso,
        "summary_end_time": record.summary_end_time,
        "priority_score": record.priority_score,
        "priority_level": record.priority_level,
        "priority_reasons_json": json.dumps(record.priority_reasons, sort_keys=True),
        "status": record.status,
        "attempts": record.attempts,
        "lease_owner": record.lease_owner,
        "lease_until_iso": record.lease_until_iso,
        "last_error_code": record.last_error_code,
        "last_error_at_iso": record.last_error_at_iso,
    }
