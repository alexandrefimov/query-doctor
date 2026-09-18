"""Postgres adapter for the raw-free Recent summary history store."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

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


ConnectFactory = Callable[[str], Any]


class PostgresRecentHistoryStore:
    def __init__(self, dsn: str, *, connect: ConnectFactory | None = None):
        normalized = dsn.strip()
        if not normalized:
            raise RecentHistoryStoreError("postgres_recent_history_dsn_missing")
        self._dsn = normalized
        self._connect_factory = connect
        self._initialized = False

    @classmethod
    def from_env(
        cls,
        dsn_env: str,
        *,
        env: dict[str, str],
        connect: ConnectFactory | None = None,
    ) -> "PostgresRecentHistoryStore":
        dsn = env.get(dsn_env)
        if not dsn:
            raise RecentHistoryStoreError("postgres_recent_history_dsn_missing")
        return cls(dsn, connect=connect)

    def initialize(self) -> None:
        if self._initialized:
            return
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    for statement in POSTGRES_RECENT_QUERY_SUMMARY_DDL:
                        cursor.execute(statement)
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_history_initialize_failed") from exc
        self._initialized = True

    def upsert_summaries(self, records: Iterable[RecentSummaryHistoryRecord]) -> int:
        rows = [record_to_postgres_row(record) for record in records]
        if not rows:
            return 0
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(POSTGRES_RECENT_QUERY_SUMMARY_UPSERT, rows)
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_history_upsert_failed") from exc
        return len(rows)

    def enqueue_profile_jobs(self, records: Iterable[RecentProfileJobRecord]) -> int:
        rows = [profile_job_to_postgres_row(record) for record in records]
        if not rows:
            return 0
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(POSTGRES_RECENT_PROFILE_JOB_INSERT, rows)
                    cursor.executemany(
                        POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                        [
                            profile_status_update_params(
                                row,
                                profile_status=PROFILE_STATUS_PENDING,
                            )
                            for row in rows
                        ],
                    )
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_job_enqueue_failed") from exc
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
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        params = {
            "limit": limit,
            "lease_owner": normalize_profile_lease_owner(lease_owner),
            "lease_until_iso": normalize_profile_lease_timestamp(lease_until_iso),
            "now_iso": normalize_profile_lease_timestamp(now_iso),
            "pending_status": PROFILE_JOB_STATUS_PENDING,
            "leased_status": PROFILE_JOB_STATUS_LEASED,
            "engine_filter": engine_filter,
            "source_kind_filter": source_kind_filter,
            "source_key_filter": source_key_filter,
        }
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_RECENT_PROFILE_JOB_CLAIM, params)
                    rows = cursor.fetchall()
                    if rows:
                        records_for_status = [
                            profile_job_record_from_storage_values(row) for row in rows
                        ]
                        cursor.executemany(
                            POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                            [
                                profile_status_update_params(
                                    profile_job_to_postgres_row(record),
                                    profile_status=PROFILE_STATUS_PROCESSING,
                                )
                                for record in records_for_status
                            ],
                        )
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_job_claim_failed") from exc
        records = [profile_job_record_from_storage_values(row) for row in rows]
        return sorted(
            records,
            key=lambda record: (
                -record.priority_score,
                record.summary_end_time or "",
                record.query_id,
            ),
        )

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
        params = profile_job_transition_params(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
            lease_owner=lease_owner,
        )
        params.update(
            {
                "lease_until_iso": normalize_profile_lease_timestamp(lease_until_iso),
                "now_iso": normalize_profile_lease_timestamp(now_iso),
                "leased_status": PROFILE_JOB_STATUS_LEASED,
            }
        )
        return self._execute_profile_job_transition(
            POSTGRES_RECENT_PROFILE_JOB_RENEW_LEASE,
            params,
            "postgres_recent_profile_job_renew_failed",
        )

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
        params = profile_job_transition_params(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
            lease_owner=lease_owner,
        )
        params.update(
            {
                "completed_status": PROFILE_JOB_STATUS_COMPLETED,
                "completed_at_iso": normalize_profile_lease_timestamp(completed_at_iso),
                "leased_status": PROFILE_JOB_STATUS_LEASED,
            }
        )
        return self._execute_profile_job_transition(
            POSTGRES_RECENT_PROFILE_JOB_COMPLETE,
            params,
            "postgres_recent_profile_job_complete_failed",
            profile_status=PROFILE_STATUS_ANALYZED,
        )

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
        params = profile_job_transition_params(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
            lease_owner=lease_owner,
        )
        params.update(
            {
                "next_status": next_profile_job_status(retry=retry, aged_out=aged_out),
                "failed_at_iso": normalize_profile_lease_timestamp(failed_at_iso),
                "last_error_code": normalize_profile_error_code(error_code),
                "leased_status": PROFILE_JOB_STATUS_LEASED,
            }
        )
        return self._execute_profile_job_transition(
            POSTGRES_RECENT_PROFILE_JOB_FAIL,
            params,
            "postgres_recent_profile_job_fail_failed",
            profile_status=PROFILE_STATUS_RETRY_PENDING if retry else PROFILE_STATUS_FAILED,
        )

    def requeue_failed_profile_jobs(
        self,
        *,
        max_jobs: int,
        requeued_at_iso: str,
        dry_run: bool,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
        prepare_schema: bool = True,
    ) -> RecentProfileJobRequeueResult:
        limit = normalize_profile_claim_limit(max_jobs)
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        params = {
            "limit": limit,
            "requeued_at_iso": normalize_profile_lease_timestamp(requeued_at_iso),
            "pending_status": PROFILE_JOB_STATUS_PENDING,
            "failed_status": PROFILE_JOB_STATUS_FAILED,
            "engine_filter": engine_filter,
            "source_kind_filter": source_kind_filter,
            "source_key_filter": source_key_filter,
        }
        if prepare_schema:
            self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_RECENT_PROFILE_JOB_REQUEUE_COUNT, params)
                    count_row = cursor.fetchone()
                    matched = int(count_row[0]) if count_row else 0
                    if limit <= 0:
                        rows = []
                    elif dry_run:
                        cursor.execute(POSTGRES_RECENT_PROFILE_JOB_REQUEUE_SELECT, params)
                        rows = cursor.fetchall()
                    else:
                        cursor.execute(POSTGRES_RECENT_PROFILE_JOB_REQUEUE_UPDATE, params)
                        rows = cursor.fetchall()
                        if rows:
                            cursor.executemany(
                                POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_REQUEUE,
                                [requeue_profile_status_update_params(row) for row in rows],
                            )
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_job_requeue_failed") from exc
        return RecentProfileJobRequeueResult(
            matched_failed_jobs=matched,
            selected_failed_jobs=len(rows),
            requeued_jobs=0 if dry_run else len(rows),
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
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        params = {
            "aged_out_status": PROFILE_JOB_STATUS_AGED_OUT,
            "error_code": PROFILE_JOB_AGED_OUT_ERROR_CODE,
            "pending_status": PROFILE_JOB_STATUS_PENDING,
            "leased_status": PROFILE_JOB_STATUS_LEASED,
            "cutoff_iso": str(cutoff_iso),
            "now_iso": normalize_profile_lease_timestamp(now_iso),
            "failed_profile_status": PROFILE_STATUS_FAILED,
            "pending_profile_status": PROFILE_STATUS_PENDING,
            "processing_profile_status": PROFILE_STATUS_PROCESSING,
            "retry_profile_status": PROFILE_STATUS_RETRY_PENDING,
            "engine_filter": engine_filter,
            "source_kind_filter": source_kind_filter,
            "source_key_filter": source_key_filter,
        }
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_RECENT_PROFILE_JOB_AGE_OUT, params)
                    row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_job_age_out_failed") from exc
        return int(row[0] or 0) if row else 0

    def summarize_profile_backlog_health(
        self,
        *,
        now_iso: str,
        engine: str | None = None,
        source_kind: str | None = None,
        source_key: str | None = None,
        prepare_schema: bool = True,
        window_start_iso: str | None = None,
        window_hours: int | None = None,
    ) -> RecentProfileBacklogHealth:
        engine_filter, source_kind_filter, source_key_filter = (
            normalize_optional_profile_job_filters(
                engine=engine,
                source_kind=source_kind,
                source_key=source_key,
            )
        )
        params = {
            "pending_status": PROFILE_JOB_STATUS_PENDING,
            "retry_profile_status": PROFILE_STATUS_RETRY_PENDING,
            "leased_status": PROFILE_JOB_STATUS_LEASED,
            "failed_status": PROFILE_JOB_STATUS_FAILED,
            "now_iso": normalize_profile_lease_timestamp(now_iso),
            "engine_filter": engine_filter,
            "source_kind_filter": source_kind_filter,
            "source_key_filter": source_key_filter,
        }
        window_row = None
        if prepare_schema:
            self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH, params)
                    row = cursor.fetchone()
                    if window_start_iso is not None:
                        cursor.execute(
                            POSTGRES_RECENT_PROFILE_WINDOW_OUTCOMES,
                            {
                                "completed_status": PROFILE_JOB_STATUS_COMPLETED,
                                "failed_status": PROFILE_JOB_STATUS_FAILED,
                                "aged_out_status": PROFILE_JOB_STATUS_AGED_OUT,
                                "not_found_error_code": PROFILE_JOB_NOT_FOUND_ERROR_CODE,
                                "window_start_iso": normalize_profile_lease_timestamp(
                                    window_start_iso
                                ),
                                "engine_filter": engine_filter,
                                "source_kind_filter": source_kind_filter,
                                "source_key_filter": source_key_filter,
                            },
                        )
                        window_row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_backlog_health_failed") from exc
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
                with connection.cursor() as cursor:
                    cursor.executemany(POSTGRES_RECENT_ANALYSIS_CACHE_UPSERT, rows)
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_analysis_cache_upsert_failed") from exc
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
        safe_engine, safe_source_kind, safe_source_key, safe_query_id = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        fingerprint = normalize_analysis_cache_fingerprint(profile_fingerprint)
        contract = normalize_analysis_cache_contract(analyzer_contract)
        if not safe_query_id or not fingerprint or not contract:
            return None
        params = {
            "engine": safe_engine,
            "source_kind": safe_source_kind,
            "source_key": safe_source_key,
            "query_id": safe_query_id,
            "profile_fingerprint": fingerprint,
            "analyzer_contract": contract,
        }
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_RECENT_ANALYSIS_CACHE_SELECT, params)
                    row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_analysis_cache_load_failed") from exc
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
                with connection.cursor() as cursor:
                    cursor.executemany(POSTGRES_RECENT_PROFILE_ARTIFACT_UPSERT, rows)
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_artifact_upsert_failed") from exc
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
        safe_engine, safe_source_kind, safe_source_key, safe_query_id = normalize_profile_job_key(
            engine=engine,
            source_kind=source_kind,
            source_key=source_key,
            query_id=query_id,
        )
        fingerprint = normalize_analysis_cache_fingerprint(profile_fingerprint)
        contract = normalize_analysis_cache_contract(artifact_contract)
        if not safe_query_id or not fingerprint or not contract:
            return None
        params = {
            "engine": safe_engine,
            "source_kind": safe_source_kind,
            "source_key": safe_source_key,
            "query_id": safe_query_id,
            "profile_fingerprint": fingerprint,
            "artifact_contract": contract,
        }
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_RECENT_PROFILE_ARTIFACT_SELECT, params)
                    row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_profile_artifact_load_failed") from exc
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
                with connection.cursor() as cursor:
                    summaries_deleted = execute_prune(
                        cursor,
                        POSTGRES_RECENT_QUERY_SUMMARY_PRUNE,
                        {"cutoff_iso": safe_policy.summary_cutoff_iso},
                        enabled=bool(safe_policy.summary_cutoff_iso),
                    )
                    profile_jobs_deleted = execute_prune(
                        cursor,
                        POSTGRES_RECENT_PROFILE_JOB_PRUNE,
                        {
                            "cutoff_iso": safe_policy.profile_job_cutoff_iso,
                            "completed_status": PROFILE_JOB_STATUS_COMPLETED,
                            "failed_status": PROFILE_JOB_STATUS_FAILED,
                            "aged_out_status": PROFILE_JOB_STATUS_AGED_OUT,
                        },
                        enabled=bool(safe_policy.profile_job_cutoff_iso),
                    )
                    analysis_cache_deleted = execute_prune(
                        cursor,
                        POSTGRES_RECENT_ANALYSIS_CACHE_PRUNE,
                        {"cutoff_iso": safe_policy.analysis_cache_cutoff_iso},
                        enabled=bool(safe_policy.analysis_cache_cutoff_iso),
                    )
                    profile_artifacts_deleted = execute_prune(
                        cursor,
                        POSTGRES_RECENT_PROFILE_ARTIFACT_PRUNE,
                        {"cutoff_iso": safe_policy.profile_artifact_cutoff_iso},
                        enabled=bool(safe_policy.profile_artifact_cutoff_iso),
                    )
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_history_prune_failed") from exc
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
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM recent_query_summary")
                    row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_history_count_failed") from exc
        return int(row[0]) if row else 0

    def load_payloads(self) -> list[dict[str, object]]:
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
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
                    )
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError("postgres_recent_history_load_failed") from exc
        return recent_summary_payloads_from_rows(rows)

    def load_materialized_payloads(
        self,
        *,
        limit: int | None = None,
        details_ready_only: bool = False,
    ) -> list[dict[str, object]]:
        self.initialize()
        params = {
            "artifact_contract": PROFILE_ARTIFACT_DEFAULT_CONTRACT,
            "artifact_status": PROFILE_ARTIFACT_STATUS_AVAILABLE,
            "analyzer_contract": ANALYSIS_CACHE_DEFAULT_CONTRACT,
            "analysis_status": ANALYSIS_CACHE_STATUS_READY,
            "analyzed_profile_status": PROFILE_STATUS_ANALYZED,
            "details_ready_only": bool(details_ready_only),
            "limit": None if limit is None else max(0, int(limit)),
        }
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        postgres_materialized_payloads_select(details_ready_only),
                        params,
                    )
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError(
                "postgres_recent_history_materialized_load_failed"
            ) from exc
        return recent_summary_payloads_from_rows(rows)

    def load_materialized_payloads_with_count(
        self,
        *,
        limit: int | None = None,
        prepare_schema: bool = True,
        details_ready_only: bool = False,
    ) -> tuple[list[dict[str, object]], int]:
        params = {
            "artifact_contract": PROFILE_ARTIFACT_DEFAULT_CONTRACT,
            "artifact_status": PROFILE_ARTIFACT_STATUS_AVAILABLE,
            "analyzer_contract": ANALYSIS_CACHE_DEFAULT_CONTRACT,
            "analysis_status": ANALYSIS_CACHE_STATUS_READY,
            "analyzed_profile_status": PROFILE_STATUS_ANALYZED,
            "details_ready_only": bool(details_ready_only),
            "limit": None if limit is None else max(0, int(limit)),
        }
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    if prepare_schema and not self._initialized:
                        for statement in POSTGRES_RECENT_QUERY_SUMMARY_DDL:
                            cursor.execute(statement)
                    cursor.execute(
                        postgres_materialized_payloads_select(details_ready_only),
                        params,
                    )
                    rows = cursor.fetchall()
                    cursor.execute("SELECT COUNT(*) FROM recent_query_summary")
                    count_row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError(
                "postgres_recent_history_materialized_load_failed"
            ) from exc
        if prepare_schema:
            self._initialized = True
        return recent_summary_payloads_from_rows(rows), int(count_row[0]) if count_row else 0

    def _execute_profile_job_transition(
        self,
        statement: str,
        params: dict[str, object],
        failure_code: str,
        profile_status: str | None = None,
    ) -> bool:
        self.initialize()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement, params)
                    updated = getattr(cursor, "rowcount", 0) == 1
                    if updated and profile_status is not None:
                        cursor.execute(
                            POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE,
                            profile_status_update_params(params, profile_status=profile_status),
                        )
                    return updated
        except Exception as exc:  # noqa: BLE001 - driver errors must stay path-free upstream.
            raise RecentHistoryStoreError(failure_code) from exc

    def _connect(self) -> Any:
        if self._connect_factory is not None:
            return self._connect_factory(self._dsn)
        try:
            import psycopg
        except ImportError as exc:
            raise RecentHistoryStoreError("postgres_recent_history_driver_missing") from exc
        return psycopg.connect(self._dsn)


POSTGRES_RECENT_QUERY_SUMMARY_DDL = (
    """
    CREATE TABLE IF NOT EXISTS recent_query_summary (
        schema_version integer NOT NULL,
        engine text NOT NULL,
        source_kind text NOT NULL,
        source_key text NOT NULL,
        query_id text NOT NULL,
        recorded_at_iso text NOT NULL,
        start_time text,
        end_time text,
        duration_ms bigint,
        status text,
        query_state text,
        user_label text,
        pool_label text,
        query_type text,
        sql_verb text,
        statement_present boolean NOT NULL,
        admission_result text,
        admission_wait_ms bigint,
        rows_produced bigint,
        bytes_read bigint,
        bytes_sent bigint,
        memory_aggregate_peak bigint,
        memory_per_node_peak bigint,
        suspicion_score integer NOT NULL,
        suspicion_level text NOT NULL,
        suspicion_reasons_json jsonb NOT NULL,
        selected boolean NOT NULL,
        selected_reason text,
        profile_status text NOT NULL,
        payload_json jsonb NOT NULL,
        PRIMARY KEY (engine, source_kind, source_key, query_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_query_summary_time_idx
        ON recent_query_summary(engine, source_kind, source_key, end_time, start_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_query_summary_suspicion_idx
        ON recent_query_summary(engine, source_kind, source_key, suspicion_score DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_query_summary_status_idx
        ON recent_query_summary(engine, source_kind, source_key, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_query_summary_recorded_idx
        ON recent_query_summary(recorded_at_iso)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_query_summary_latest_idx
        ON recent_query_summary(
            (COALESCE(end_time, start_time, recorded_at_iso)) DESC,
            query_id
        )
    """,
    """
    CREATE TABLE IF NOT EXISTS recent_profile_job (
        schema_version integer NOT NULL,
        engine text NOT NULL,
        source_kind text NOT NULL,
        source_key text NOT NULL,
        query_id text NOT NULL,
        created_at_iso text NOT NULL,
        updated_at_iso text NOT NULL,
        summary_recorded_at_iso text NOT NULL,
        summary_end_time text,
        priority_score integer NOT NULL,
        priority_level text NOT NULL,
        priority_reasons_json jsonb NOT NULL,
        status text NOT NULL,
        attempts integer NOT NULL DEFAULT 0,
        lease_owner text,
        lease_until_iso text,
        last_error_code text,
        last_error_at_iso text,
        PRIMARY KEY (engine, source_kind, source_key, query_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_profile_job_status_priority_idx
        ON recent_profile_job(status, priority_score DESC, summary_end_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_profile_job_source_status_idx
        ON recent_profile_job(engine, source_kind, source_key, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_profile_job_status_updated_idx
        ON recent_profile_job(status, updated_at_iso)
    """,
    """
    CREATE TABLE IF NOT EXISTS recent_analysis_cache (
        schema_version integer NOT NULL,
        engine text NOT NULL,
        source_kind text NOT NULL,
        source_key text NOT NULL,
        query_id text NOT NULL,
        profile_fingerprint text NOT NULL,
        analyzer_contract text NOT NULL,
        recorded_at_iso text NOT NULL,
        status text NOT NULL,
        payload_json jsonb NOT NULL,
        PRIMARY KEY (
            engine,
            source_kind,
            source_key,
            query_id,
            profile_fingerprint,
            analyzer_contract
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_analysis_cache_recorded_idx
        ON recent_analysis_cache(recorded_at_iso)
    """,
    """
    CREATE TABLE IF NOT EXISTS recent_profile_artifact (
        schema_version integer NOT NULL,
        engine text NOT NULL,
        source_kind text NOT NULL,
        source_key text NOT NULL,
        query_id text NOT NULL,
        profile_fingerprint text NOT NULL,
        artifact_contract text NOT NULL,
        recorded_at_iso text NOT NULL,
        status text NOT NULL,
        storage_kind text NOT NULL,
        storage_key text NOT NULL,
        size_bytes bigint,
        PRIMARY KEY (
            engine,
            source_kind,
            source_key,
            query_id,
            profile_fingerprint,
            artifact_contract
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_profile_artifact_recorded_idx
        ON recent_profile_artifact(recorded_at_iso)
    """,
    """
    CREATE INDEX IF NOT EXISTS recent_profile_artifact_storage_idx
        ON recent_profile_artifact(storage_kind, storage_key)
    """,
)

POSTGRES_RECENT_QUERY_SUMMARY_UPSERT = """
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
    %(schema_version)s,
    %(engine)s,
    %(source_kind)s,
    %(source_key)s,
    %(query_id)s,
    %(recorded_at_iso)s,
    %(start_time)s,
    %(end_time)s,
    %(duration_ms)s,
    %(status)s,
    %(query_state)s,
    %(user_label)s,
    %(pool_label)s,
    %(query_type)s,
    %(sql_verb)s,
    %(statement_present)s,
    %(admission_result)s,
    %(admission_wait_ms)s,
    %(rows_produced)s,
    %(bytes_read)s,
    %(bytes_sent)s,
    %(memory_aggregate_peak)s,
    %(memory_per_node_peak)s,
    %(suspicion_score)s,
    %(suspicion_level)s,
    %(suspicion_reasons_json)s::jsonb,
    %(selected)s,
    %(selected_reason)s,
    %(profile_status)s,
    %(payload_json)s::jsonb
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

POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_UPDATE = """
UPDATE recent_query_summary
SET profile_status = %(profile_status)s
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND NOT (
        %(profile_status)s = 'pending'
        AND profile_status <> 'not_collected'
    )
    AND NOT (
        %(profile_status)s = 'processing'
        AND profile_status IN ('analyzed', 'failed')
    )
"""

POSTGRES_RECENT_QUERY_SUMMARY_PROFILE_STATUS_REQUEUE = """
UPDATE recent_query_summary
SET profile_status = %(pending_profile_status)s
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND profile_status = %(failed_profile_status)s
"""

POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT = """
WITH newest_summary_keys AS (
    SELECT
        summary.engine,
        summary.source_kind,
        summary.source_key,
        summary.query_id,
        COALESCE(summary.end_time, summary.start_time, summary.recorded_at_iso) AS sort_time
    FROM recent_query_summary AS summary
    ORDER BY sort_time DESC, query_id
    LIMIT %(limit)s
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
    AND artifact.artifact_contract = %(artifact_contract)s
    AND artifact.status = %(artifact_status)s
    AND artifact.profile_fingerprint = (
        SELECT candidate.profile_fingerprint
        FROM recent_profile_artifact AS candidate
        WHERE
            candidate.engine = summary.engine
            AND candidate.source_kind = summary.source_kind
            AND candidate.source_key = summary.source_key
            AND candidate.query_id = summary.query_id
            AND candidate.artifact_contract = %(artifact_contract)s
            AND candidate.status = %(artifact_status)s
        ORDER BY candidate.recorded_at_iso DESC, candidate.profile_fingerprint DESC
        LIMIT 1
    )
LEFT JOIN recent_analysis_cache AS analysis_cache
    ON analysis_cache.engine = summary.engine
    AND analysis_cache.source_kind = summary.source_kind
    AND analysis_cache.source_key = summary.source_key
    AND analysis_cache.query_id = summary.query_id
    AND analysis_cache.profile_fingerprint = artifact.profile_fingerprint
    AND analysis_cache.analyzer_contract = %(analyzer_contract)s
    AND analysis_cache.status = %(analysis_status)s
ORDER BY
    newest.sort_time DESC,
    summary.query_id
"""

POSTGRES_RECENT_DETAILS_READY_PAYLOADS_SELECT = """
WITH latest_available_artifacts AS (
    SELECT DISTINCT ON (
        artifact.engine,
        artifact.source_kind,
        artifact.source_key,
        artifact.query_id
    )
        artifact.engine,
        artifact.source_kind,
        artifact.source_key,
        artifact.query_id,
        artifact.profile_fingerprint
    FROM recent_profile_artifact AS artifact
    WHERE
        artifact.artifact_contract = %(artifact_contract)s
        AND artifact.status = %(artifact_status)s
    ORDER BY
        artifact.engine,
        artifact.source_kind,
        artifact.source_key,
        artifact.query_id,
        artifact.recorded_at_iso DESC,
        artifact.profile_fingerprint DESC
),
newest_summary_keys AS (
    SELECT
        summary.engine,
        summary.source_kind,
        summary.source_key,
        summary.query_id,
        artifact.profile_fingerprint,
        COALESCE(summary.end_time, summary.start_time, summary.recorded_at_iso) AS sort_time
    FROM latest_available_artifacts AS artifact
    JOIN recent_analysis_cache AS analysis_cache
        ON analysis_cache.engine = artifact.engine
        AND analysis_cache.source_kind = artifact.source_kind
        AND analysis_cache.source_key = artifact.source_key
        AND analysis_cache.query_id = artifact.query_id
        AND analysis_cache.profile_fingerprint = artifact.profile_fingerprint
        AND analysis_cache.analyzer_contract = %(analyzer_contract)s
        AND analysis_cache.status = %(analysis_status)s
    JOIN recent_query_summary AS summary
        ON summary.engine = artifact.engine
        AND summary.source_kind = artifact.source_kind
        AND summary.source_key = artifact.source_key
        AND summary.query_id = artifact.query_id
        AND summary.profile_status = %(analyzed_profile_status)s
    ORDER BY sort_time DESC, summary.query_id
    LIMIT %(limit)s
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
JOIN recent_analysis_cache AS analysis_cache
    ON analysis_cache.engine = newest.engine
    AND analysis_cache.source_kind = newest.source_kind
    AND analysis_cache.source_key = newest.source_key
    AND analysis_cache.query_id = newest.query_id
    AND analysis_cache.profile_fingerprint = newest.profile_fingerprint
    AND analysis_cache.analyzer_contract = %(analyzer_contract)s
    AND analysis_cache.status = %(analysis_status)s
ORDER BY
    newest.sort_time DESC,
    summary.query_id
"""


def postgres_materialized_payloads_select(details_ready_only: bool) -> str:
    if details_ready_only:
        return POSTGRES_RECENT_DETAILS_READY_PAYLOADS_SELECT
    return POSTGRES_RECENT_MATERIALIZED_PAYLOADS_SELECT


POSTGRES_RECENT_PROFILE_JOB_INSERT = """
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
    %(schema_version)s,
    %(engine)s,
    %(source_kind)s,
    %(source_key)s,
    %(query_id)s,
    %(created_at_iso)s,
    %(updated_at_iso)s,
    %(summary_recorded_at_iso)s,
    %(summary_end_time)s,
    %(priority_score)s,
    %(priority_level)s,
    %(priority_reasons_json)s::jsonb,
    %(status)s,
    %(attempts)s,
    %(lease_owner)s,
    %(lease_until_iso)s,
    %(last_error_code)s,
    %(last_error_at_iso)s
)
ON CONFLICT(engine, source_kind, source_key, query_id) DO NOTHING
"""

POSTGRES_PROFILE_JOB_RETURNING_SELECT = ", ".join(
    f"job.{column}" for column in PROFILE_JOB_STORAGE_COLUMNS
)

POSTGRES_RECENT_PROFILE_JOB_CLAIM = f"""
WITH claimable AS (
    SELECT engine, source_kind, source_key, query_id
    FROM recent_profile_job
    WHERE
        (%(engine_filter)s::text IS NULL OR engine = %(engine_filter)s)
        AND (%(source_kind_filter)s::text IS NULL OR source_kind = %(source_kind_filter)s)
        AND (%(source_key_filter)s::text IS NULL OR source_key = %(source_key_filter)s)
        AND (
            status = %(pending_status)s
            OR (
                status = %(leased_status)s
                AND lease_until_iso IS NOT NULL
                AND lease_until_iso <= %(now_iso)s
            )
        )
    ORDER BY priority_score DESC, summary_end_time DESC NULLS LAST, query_id
    FOR UPDATE SKIP LOCKED
    LIMIT %(limit)s
)
UPDATE recent_profile_job AS job
SET
    status = %(leased_status)s,
    lease_owner = %(lease_owner)s,
    lease_until_iso = %(lease_until_iso)s,
    updated_at_iso = %(now_iso)s,
    attempts = job.attempts + 1
FROM claimable
WHERE
    job.engine = claimable.engine
    AND job.source_kind = claimable.source_kind
    AND job.source_key = claimable.source_key
    AND job.query_id = claimable.query_id
RETURNING {POSTGRES_PROFILE_JOB_RETURNING_SELECT}
"""

POSTGRES_RECENT_PROFILE_JOB_RENEW_LEASE = """
UPDATE recent_profile_job
SET
    lease_until_iso = %(lease_until_iso)s,
    updated_at_iso = %(now_iso)s
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND status = %(leased_status)s
    AND lease_owner = %(lease_owner)s
"""

POSTGRES_RECENT_PROFILE_JOB_COMPLETE = """
UPDATE recent_profile_job
SET
    status = %(completed_status)s,
    updated_at_iso = %(completed_at_iso)s,
    lease_owner = NULL,
    lease_until_iso = NULL,
    last_error_code = NULL,
    last_error_at_iso = NULL
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND status = %(leased_status)s
    AND lease_owner = %(lease_owner)s
"""

POSTGRES_RECENT_PROFILE_JOB_FAIL = """
UPDATE recent_profile_job
SET
    status = %(next_status)s,
    updated_at_iso = %(failed_at_iso)s,
    lease_owner = NULL,
    lease_until_iso = NULL,
    last_error_code = %(last_error_code)s,
    last_error_at_iso = %(failed_at_iso)s
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND status = %(leased_status)s
    AND lease_owner = %(lease_owner)s
"""

POSTGRES_PROFILE_JOB_REQUEUE_KEY_SELECT = (
    "job.engine, job.source_kind, job.source_key, job.query_id"
)

POSTGRES_RECENT_PROFILE_JOB_REQUEUE_COUNT = """
SELECT COUNT(*)
FROM recent_profile_job
WHERE
    (%(engine_filter)s::text IS NULL OR engine = %(engine_filter)s)
    AND (%(source_kind_filter)s::text IS NULL OR source_kind = %(source_kind_filter)s)
    AND (%(source_key_filter)s::text IS NULL OR source_key = %(source_key_filter)s)
    AND status = %(failed_status)s
"""

POSTGRES_RECENT_PROFILE_JOB_REQUEUE_SELECT = f"""
SELECT {POSTGRES_PROFILE_JOB_REQUEUE_KEY_SELECT}
FROM recent_profile_job AS job
WHERE
    (%(engine_filter)s::text IS NULL OR job.engine = %(engine_filter)s)
    AND (%(source_kind_filter)s::text IS NULL OR job.source_kind = %(source_kind_filter)s)
    AND (%(source_key_filter)s::text IS NULL OR job.source_key = %(source_key_filter)s)
    AND job.status = %(failed_status)s
ORDER BY job.priority_score DESC, job.updated_at_iso, job.query_id
LIMIT %(limit)s
"""

POSTGRES_RECENT_PROFILE_JOB_REQUEUE_UPDATE = f"""
WITH selected AS (
    SELECT engine, source_kind, source_key, query_id
    FROM recent_profile_job
    WHERE
        (%(engine_filter)s::text IS NULL OR engine = %(engine_filter)s)
        AND (%(source_kind_filter)s::text IS NULL OR source_kind = %(source_kind_filter)s)
        AND (%(source_key_filter)s::text IS NULL OR source_key = %(source_key_filter)s)
        AND status = %(failed_status)s
    ORDER BY priority_score DESC, updated_at_iso, query_id
    FOR UPDATE SKIP LOCKED
    LIMIT %(limit)s
)
UPDATE recent_profile_job AS job
SET
    status = %(pending_status)s,
    updated_at_iso = %(requeued_at_iso)s,
    attempts = 0,
    lease_owner = NULL,
    lease_until_iso = NULL,
    last_error_code = NULL,
    last_error_at_iso = NULL
FROM selected
WHERE
    job.engine = selected.engine
    AND job.source_kind = selected.source_kind
    AND job.source_key = selected.source_key
    AND job.query_id = selected.query_id
    AND job.status = %(failed_status)s
RETURNING {POSTGRES_PROFILE_JOB_REQUEUE_KEY_SELECT}
"""

POSTGRES_RECENT_PROFILE_BACKLOG_HEALTH = """
SELECT
    COALESCE(SUM(CASE
        WHEN job.status = %(pending_status)s
            AND COALESCE(summary.profile_status, '') <> %(retry_profile_status)s
            AND job.last_error_code IS NULL
        THEN 1 ELSE 0 END), 0) AS pending_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = %(pending_status)s
            AND (
                summary.profile_status = %(retry_profile_status)s
                OR job.last_error_code IS NOT NULL
            )
        THEN 1 ELSE 0 END), 0) AS retry_pending_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = %(leased_status)s
        THEN 1 ELSE 0 END), 0) AS leased_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = %(leased_status)s
            AND job.lease_until_iso IS NOT NULL
            AND job.lease_until_iso <= %(now_iso)s
        THEN 1 ELSE 0 END), 0) AS stale_leased_jobs,
    COALESCE(SUM(CASE
        WHEN job.status = %(failed_status)s
        THEN 1 ELSE 0 END), 0) AS failed_jobs
FROM recent_profile_job AS job
LEFT JOIN recent_query_summary AS summary
    ON summary.engine = job.engine
    AND summary.source_kind = job.source_kind
    AND summary.source_key = job.source_key
    AND summary.query_id = job.query_id
WHERE
    (%(engine_filter)s::text IS NULL OR job.engine = %(engine_filter)s)
    AND (%(source_kind_filter)s::text IS NULL OR job.source_kind = %(source_kind_filter)s)
    AND (%(source_key_filter)s::text IS NULL OR job.source_key = %(source_key_filter)s)
"""

POSTGRES_RECENT_PROFILE_WINDOW_OUTCOMES = """
SELECT
    COALESCE(SUM(CASE WHEN status = %(completed_status)s THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = %(failed_status)s THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = %(aged_out_status)s THEN 1 ELSE 0 END), 0)
FROM recent_profile_job
WHERE
    (
        status IN (%(completed_status)s, %(failed_status)s)
        OR (status = %(aged_out_status)s AND last_error_code = %(not_found_error_code)s)
    )
    AND updated_at_iso >= %(window_start_iso)s
    AND (%(engine_filter)s::text IS NULL OR engine = %(engine_filter)s)
    AND (%(source_kind_filter)s::text IS NULL OR source_kind = %(source_kind_filter)s)
    AND (%(source_key_filter)s::text IS NULL OR source_key = %(source_key_filter)s)
"""

POSTGRES_RECENT_PROFILE_JOB_AGE_OUT = """
WITH aged AS (
    UPDATE recent_profile_job
    SET
        status = %(aged_out_status)s,
        lease_owner = NULL,
        lease_until_iso = NULL,
        updated_at_iso = %(now_iso)s,
        last_error_code = %(error_code)s,
        last_error_at_iso = %(now_iso)s
    WHERE
        (%(engine_filter)s::text IS NULL OR engine = %(engine_filter)s)
        AND (%(source_kind_filter)s::text IS NULL OR source_kind = %(source_kind_filter)s)
        AND (%(source_key_filter)s::text IS NULL OR source_key = %(source_key_filter)s)
        AND summary_end_time IS NOT NULL
        AND summary_end_time < %(cutoff_iso)s
        AND (
            status = %(pending_status)s
            OR (
                status = %(leased_status)s
                AND lease_until_iso IS NOT NULL
                AND lease_until_iso <= %(now_iso)s
            )
        )
    RETURNING engine, source_kind, source_key, query_id
),
summary_update AS (
    UPDATE recent_query_summary AS summary
    SET profile_status = %(failed_profile_status)s
    FROM aged
    WHERE
        summary.engine = aged.engine
        AND summary.source_kind = aged.source_kind
        AND summary.source_key = aged.source_key
        AND summary.query_id = aged.query_id
        AND summary.profile_status IN (
            %(pending_profile_status)s,
            %(processing_profile_status)s,
            %(retry_profile_status)s
        )
    RETURNING 1
)
SELECT COUNT(*) FROM aged
"""

POSTGRES_RECENT_ANALYSIS_CACHE_UPSERT = """
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
    %(schema_version)s,
    %(engine)s,
    %(source_kind)s,
    %(source_key)s,
    %(query_id)s,
    %(profile_fingerprint)s,
    %(analyzer_contract)s,
    %(recorded_at_iso)s,
    %(status)s,
    %(payload_json)s::jsonb
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

POSTGRES_ANALYSIS_CACHE_STORAGE_SELECT = ", ".join(ANALYSIS_CACHE_STORAGE_COLUMNS)

POSTGRES_RECENT_ANALYSIS_CACHE_SELECT = f"""
SELECT {POSTGRES_ANALYSIS_CACHE_STORAGE_SELECT}
FROM recent_analysis_cache
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND profile_fingerprint = %(profile_fingerprint)s
    AND analyzer_contract = %(analyzer_contract)s
"""

POSTGRES_RECENT_PROFILE_ARTIFACT_UPSERT = """
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
    %(schema_version)s,
    %(engine)s,
    %(source_kind)s,
    %(source_key)s,
    %(query_id)s,
    %(profile_fingerprint)s,
    %(artifact_contract)s,
    %(recorded_at_iso)s,
    %(status)s,
    %(storage_kind)s,
    %(storage_key)s,
    %(size_bytes)s
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

POSTGRES_PROFILE_ARTIFACT_STORAGE_SELECT = ", ".join(PROFILE_ARTIFACT_STORAGE_COLUMNS)

POSTGRES_RECENT_PROFILE_ARTIFACT_SELECT = f"""
SELECT {POSTGRES_PROFILE_ARTIFACT_STORAGE_SELECT}
FROM recent_profile_artifact
WHERE
    engine = %(engine)s
    AND source_kind = %(source_kind)s
    AND source_key = %(source_key)s
    AND query_id = %(query_id)s
    AND profile_fingerprint = %(profile_fingerprint)s
    AND artifact_contract = %(artifact_contract)s
"""

POSTGRES_RECENT_QUERY_SUMMARY_PRUNE = """
DELETE FROM recent_query_summary
WHERE recorded_at_iso < %(cutoff_iso)s
"""

POSTGRES_RECENT_PROFILE_JOB_PRUNE = """
DELETE FROM recent_profile_job
WHERE
    updated_at_iso < %(cutoff_iso)s
    AND status IN (%(completed_status)s, %(failed_status)s, %(aged_out_status)s)
"""

POSTGRES_RECENT_ANALYSIS_CACHE_PRUNE = """
DELETE FROM recent_analysis_cache
WHERE recorded_at_iso < %(cutoff_iso)s
"""

POSTGRES_RECENT_PROFILE_ARTIFACT_PRUNE = """
DELETE FROM recent_profile_artifact
WHERE recorded_at_iso < %(cutoff_iso)s
"""


def profile_job_transition_params(
    *,
    engine: object,
    source_kind: object,
    source_key: object,
    query_id: object,
    lease_owner: object,
) -> dict[str, object]:
    safe_engine, safe_source_kind, safe_source_key, safe_query_id = normalize_profile_job_key(
        engine=engine,
        source_kind=source_kind,
        source_key=source_key,
        query_id=query_id,
    )
    return {
        "engine": safe_engine,
        "source_kind": safe_source_kind,
        "source_key": safe_source_key,
        "query_id": safe_query_id,
        "lease_owner": normalize_profile_lease_owner(lease_owner),
    }


def profile_status_update_params(
    values: dict[str, object],
    *,
    profile_status: str,
) -> dict[str, object]:
    safe_engine, safe_source_kind, safe_source_key, safe_query_id = normalize_profile_job_key(
        engine=values.get("engine"),
        source_kind=values.get("source_kind"),
        source_key=values.get("source_key"),
        query_id=values.get("query_id"),
    )
    return {
        "engine": safe_engine,
        "source_kind": safe_source_kind,
        "source_key": safe_source_key,
        "query_id": safe_query_id,
        "profile_status": profile_status,
    }


def requeue_profile_status_update_params(values: object) -> dict[str, object]:
    safe_engine, safe_source_kind, safe_source_key, safe_query_id = normalize_profile_job_key(
        engine=values[0],
        source_kind=values[1],
        source_key=values[2],
        query_id=values[3],
    )
    return {
        "engine": safe_engine,
        "source_kind": safe_source_kind,
        "source_key": safe_source_key,
        "query_id": safe_query_id,
        "pending_profile_status": PROFILE_STATUS_PENDING,
        "failed_profile_status": PROFILE_STATUS_FAILED,
    }


def execute_prune(
    cursor: Any,
    statement: str,
    params: dict[str, object],
    *,
    enabled: bool,
) -> int:
    if not enabled:
        return 0
    cursor.execute(statement, params)
    return max(0, int(getattr(cursor, "rowcount", 0)))


def recent_summary_payloads_from_rows(rows: Iterable[object]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = row[0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        payload["profile_status"] = str(row[1] or PROFILE_STATUS_NOT_COLLECTED)
        if len(row) > 2 and row[2] is not None:
            analysis_payload = row[2]
            if isinstance(analysis_payload, str):
                try:
                    analysis_payload = json.loads(analysis_payload)
                except json.JSONDecodeError:
                    analysis_payload = None
            if isinstance(analysis_payload, dict):
                payload["analysis_cache_payload"] = safe_analysis_cache_payload(analysis_payload)
        if len(row) > 3:
            error_code = safe_optional_profile_error_code(row[3])
            if error_code is not None:
                payload["profile_last_error_code"] = error_code
        payloads.append(payload)
    return payloads


def record_to_postgres_row(record: RecentSummaryHistoryRecord) -> dict[str, object]:
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
        "statement_present": record.statement_present,
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
        "selected": record.selected,
        "selected_reason": record.selected_reason,
        "profile_status": record.profile_status,
        "payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }


def profile_job_to_postgres_row(record: RecentProfileJobRecord) -> dict[str, object]:
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
