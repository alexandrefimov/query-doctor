import json
from dataclasses import replace

from query_doctor.cm.models import CMQuerySummary
from query_doctor.recent.history_store import history_record_from_summary
from query_doctor.recent.profile_budget import (
    PROFILE_ARTIFACT_SCHEMA_VERSION,
    PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND,
    PROFILE_JOB_STATUS_PENDING,
    ProfileBudgetPolicy,
    RecentProfileArtifactRecord,
    plan_recent_profile_jobs,
    profile_artifact_record_to_storage_row,
    normalize_profile_lease_owner,
)
from query_doctor.recent.profile_artifact_storage import profile_artifact_storage_lifecycle
from query_doctor.recent.summary_suspicion import SummarySuspicionScore
from query_doctor.safety.redaction import sanitize_text_for_log


def test_hostlike_worker_owners_keep_distinct_private_identities():
    owners = ["query-doctor-worker-123-aaaaa", "query-doctor-worker-124-bbbbb"]
    normalized = [normalize_profile_lease_owner(owner) for owner in owners]
    assert len(set(normalized)) == 2
    for original, owner in zip(owners, normalized):
        assert original not in owner
        assert len(owner) <= 64
        assert normalize_profile_lease_owner(owner) == owner


def test_long_worker_owners_do_not_collide_after_truncation():
    prefix = "a" * 80
    first = normalize_profile_lease_owner(prefix + "first")
    second = normalize_profile_lease_owner(prefix + "second")
    assert first != second
    assert len(first) <= 64
    assert normalize_profile_lease_owner(first) == first


def test_opaque_lease_owner_survives_repeated_normalization():
    # A digest beginning like a short host must not become a log alias either.
    owner = "lease_xdb4" + "a" * 45
    assert normalize_profile_lease_owner(owner) == owner
    assert sanitize_text_for_log(owner) == owner


def summary_record(
    query_id: str,
    *,
    score: int,
    level: str = "medium",
    reasons: tuple[str, ...] = ("duration_ge_10m",),
    selected: bool = False,
    duration_ms: int = 600_000,
):
    return history_record_from_summary(
        CMQuerySummary(
            query_id=query_id,
            end_time=f"2026-07-03T10:{query_id[-1]}0:00Z",
            duration_ms=duration_ms,
            statement="SELECT secret_column FROM sensitive_table",
        ),
        suspicion=SummarySuspicionScore(score=score, level=level, reasons=reasons),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:30:00+00:00",
        sql_verb="SELECT",
        selected=selected,
        selected_reason="selected: SELECT-like user query" if selected else None,
    )


def test_profile_budget_planner_ranks_suspicious_jobs_and_skips_analyzed_profiles():
    already_analyzed = replace(
        summary_record(
            "query-3",
            score=220,
            level="critical",
            reasons=("failed_or_error_status",),
        ),
        profile_status="analyzed",
    )
    records = [
        summary_record("query-1", score=0, level="none", reasons=(), selected=True),
        summary_record(
            "query-2",
            score=130,
            level="critical",
            reasons=("failed_or_error_status", "duration_ge_10m"),
        ),
        already_analyzed,
        summary_record("query-4", score=10, level="low", reasons=("duration_ge_2m",)),
    ]

    jobs = plan_recent_profile_jobs(
        records,
        policy=ProfileBudgetPolicy(max_jobs=2, min_suspicion_score=20, include_selected=True),
        planned_at_iso="2026-07-03T10:35:00+00:00",
    )

    assert [job.query_id for job in jobs] == ["query-2", "query-1"]
    assert jobs[0].priority_score == 130
    assert jobs[0].priority_reasons == ("failed_or_error_status", "duration_ge_10m")
    assert jobs[1].priority_reasons == ("selected_recent_candidate",)
    assert jobs[0].status == PROFILE_JOB_STATUS_PENDING
    payload_text = json.dumps([job.safe_payload() for job in jobs], sort_keys=True)
    assert "SELECT secret_column" not in payload_text
    assert "sensitive_table" not in payload_text


def test_profile_budget_planner_can_require_suspicion_threshold_only():
    records = [
        summary_record("query-1", score=0, level="none", reasons=(), selected=True),
        summary_record("query-2", score=35, level="medium", reasons=("running_status",)),
    ]

    jobs = plan_recent_profile_jobs(
        records,
        policy=ProfileBudgetPolicy(max_jobs=5, min_suspicion_score=20, include_selected=False),
        planned_at_iso="2026-07-03T10:35:00+00:00",
    )

    assert [job.query_id for job in jobs] == ["query-2"]


def profile_artifact_record(
    *,
    storage_kind: str = "local",
    storage_key: str = "sha256_deadbeef",
) -> RecentProfileArtifactRecord:
    return RecentProfileArtifactRecord(
        schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        query_id="query-artifact",
        profile_fingerprint="profile_fingerprint_v1",
        artifact_contract="profile_artifact_v1",
        recorded_at_iso="2026-07-03T10:30:00+00:00",
        status="available",
        storage_kind=storage_kind,
        storage_key=storage_key,
        size_bytes=4096,
    )


def test_profile_artifact_metadata_contract_is_fingerprint_only():
    row = profile_artifact_record_to_storage_row(profile_artifact_record())
    lifecycle = profile_artifact_storage_lifecycle("local")

    assert row is not None
    assert row["storage_kind"] == PROFILE_ARTIFACT_DEFAULT_STORAGE_KIND
    assert row["storage_kind"] == "fingerprint_only"
    assert row["storage_key"] == "sha256_deadbeef"
    assert lifecycle is not None
    assert lifecycle.safe_payload() == {
        "storage_kind": "fingerprint_only",
        "stores_profile_bytes": False,
        "deletion_required": False,
        "deletion_supported": True,
        "deletion_action": "metadata_only",
    }


def test_profile_artifact_metadata_rejects_path_or_unknown_storage():
    assert (
        profile_artifact_record_to_storage_row(
            profile_artifact_record(storage_key="/private/tmp/query-doctor-secret/profile.txt")
        )
        is None
    )
    assert (
        profile_artifact_record_to_storage_row(
            profile_artifact_record(storage_kind="object", storage_key="bucket/key")
        )
        is None
    )
    assert (
        profile_artifact_record_to_storage_row(
            profile_artifact_record(storage_kind="unsupported", storage_key="sha256_deadbeef")
        )
        is None
    )
