import json
from datetime import datetime, timezone

import pytest

from query_doctor.cli import recent_history_operator_readiness as cli
from query_doctor.recent.operator_readiness import (
    SUMMARY_KIND,
    audit_recent_history_operator_readiness,
    format_recent_history_operator_readiness,
)


def postgres_summary() -> dict[str, object]:
    return {
        "summary_kind": "query_doctor_recent_history_postgres_readiness_v1",
        "status": "ready",
        "backend": "postgres",
        "schema_initialized": True,
        "checks": [
            {"id": "dsn_env", "status": "ready", "summary": "Postgres DSN env is configured"}
        ],
        "issue_codes": [],
        "raw_output": False,
        "sensitive_value_echo": False,
    }


def worker_summary() -> dict[str, object]:
    return {
        "summary_kind": "query_doctor_recent_profile_worker_v1",
        "status": "done",
        "observed_at_iso": "2026-07-09T10:00:00+00:00",
        "jobs_claimed": 1,
        "jobs_completed": 1,
        "jobs_retried": 0,
        "jobs_failed": 0,
        "jobs_lease_lost": 0,
        "analysis_cache_records": 1,
        "profile_artifact_records": 1,
        "profile_backlog_health": {
            "pending_jobs": 2,
            "retry_pending_jobs": 1,
            "leased_jobs": 1,
            "stale_leased_jobs": 0,
            "failed_jobs": 0,
        },
        "next_step": "untrusted retained text query-123",
        "profile_backlog_next_step": "untrusted retained backlog text query-123",
        "issue_codes": [],
    }


def collector_summary(status: str = "recorded") -> dict[str, object]:
    return {
        "summary_kind": "query_doctor_recent_history_collector_v1",
        "status": status,
        "observed_at_iso": "2026-07-09T10:00:00+00:00",
        "discover_only": True,
        "history_backend": "postgres",
        "summaries_inspected": 2,
        "candidates_discovered": 2,
        "selected_count": 1,
        "summaries_recorded": 1,
        "profile_jobs_planned": 1,
        "query_log_at_capacity": True,
        "query_log_continuity_status": "confirmed",
        "next_step": "untrusted retained text query-123",
        "issue_codes": ["recent_history_warning"] if status == "warning" else [],
        "raw_output": False,
        "sensitive_value_echo": False,
    }


def retention_summary() -> dict[str, object]:
    return {
        "summary_kind": "query_doctor_recent_history_retention_v1",
        "status": "pruned",
        "backend": "recent_history",
        "policy": {
            "summary_cutoff_configured": True,
            "profile_job_cutoff_configured": True,
            "analysis_cache_cutoff_configured": True,
            "profile_artifact_cutoff_configured": True,
        },
        "retention": {
            "summaries_deleted": 1,
            "profile_jobs_deleted": 1,
            "analysis_cache_deleted": 1,
            "profile_artifacts_deleted": 1,
            "total_deleted": 4,
        },
        "issue_codes": [],
        "raw_output": False,
        "sensitive_value_echo": False,
    }


def remediation_summary() -> dict[str, object]:
    return {
        "summary_kind": "query_doctor_recent_profile_remediation_v1",
        "status": "dry_run",
        "backend": "recent_history",
        "mode": "dry_run",
        "filters": {
            "engine_configured": True,
            "source_kind_configured": True,
            "source_key_configured": True,
            "max_jobs": 10,
        },
        "remediation": {
            "matched_failed_jobs": 4,
            "selected_failed_jobs": 3,
            "requeued_jobs": 0,
            "skipped_due_to_limit": 1,
            "dry_run": True,
        },
        "next_step": "untrusted retained text query-123",
        "issue_codes": [],
        "raw_output": False,
        "sensitive_value_echo": False,
    }


def test_recent_history_operator_readiness_accepts_retained_raw_free_summaries():
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=collector_summary(),
        retention_summary=retention_summary(),
        remediation_summary=remediation_summary(),
    )
    payload = result.payload()
    text = format_recent_history_operator_readiness(payload)

    assert payload["summary_kind"] == SUMMARY_KIND
    assert payload["status"] == "ready"
    assert payload["accepted_summary_count"] == 5
    assert payload["evidence_summary_count"] == 5
    assert payload["collector_summary_present"] is True
    assert payload["retention_summary_present"] is True
    assert payload["remediation_summary_present"] is True
    operations = payload["operations"]
    assert operations["postgres_readiness"] == {
        "accepted": True,
        "status": "ready",
        "schema_initialized": True,
        "check_count": 1,
        "issue_count": 0,
    }
    assert operations["collector_summary"] == {
        "present": True,
        "accepted": True,
        "status": "recorded",
        "observed_at_iso": "2026-07-09T10:00:00+00:00",
        "discover_only": True,
        "history_backend": "postgres",
        "summaries_inspected": 2,
        "candidates_discovered": 2,
        "selected_count": 1,
        "summaries_recorded": 1,
        "profile_jobs_planned": 1,
        "query_log_at_capacity": True,
        "query_log_continuity_status": "confirmed",
        "issue_count": 0,
        "next_step": "Run the Recent profile worker to process planned profile jobs.",
    }
    assert operations["profile_worker"] == {
        "accepted": True,
        "status": "done",
        "observed_at_iso": "2026-07-09T10:00:00+00:00",
        "jobs_claimed": 1,
        "jobs_completed": 1,
        "jobs_retried": 0,
        "jobs_failed": 0,
        "jobs_lease_lost": 0,
        "analysis_cache_records": 1,
        "profile_artifact_records": 1,
        "profile_backlog_health_present": True,
        "profile_backlog_health": {
            "pending_jobs": 2,
            "retry_pending_jobs": 1,
            "leased_jobs": 1,
            "stale_leased_jobs": 0,
            "failed_jobs": 0,
        },
        "issue_count": 0,
        "next_step": "Refresh Online History to see newly materialized Details.",
        "profile_backlog_next_step": (
            "Let the profile worker retry pending rows; investigate repeated normalized "
            "error codes if retry backlog persists."
        ),
    }
    assert operations["retention"] == {
        "present": True,
        "accepted": True,
        "status": "pruned",
        "summaries_deleted": 1,
        "profile_jobs_deleted": 1,
        "analysis_cache_deleted": 1,
        "profile_artifacts_deleted": 1,
        "total_deleted": 4,
        "issue_count": 0,
    }
    assert operations["profile_remediation"] == {
        "present": True,
        "accepted": True,
        "status": "dry_run",
        "mode": "dry_run",
        "matched_failed_jobs": 4,
        "selected_failed_jobs": 3,
        "requeued_jobs": 0,
        "skipped_due_to_limit": 1,
        "issue_count": 0,
        "next_step": "Review the bounded count, then rerun remediation with --apply.",
    }
    serialized = json.dumps(payload, sort_keys=True)
    assert "query-123" not in serialized
    assert "/private/tmp" not in serialized
    assert "postgresql://" not in serialized
    assert "Recent history operator readiness: ready" in text
    assert "- postgres readiness: schema=ready checks=1 issues=0" in text
    assert (
        "- collector summary: status=recorded inspected=2 discovered=2 selected=1 "
        "recorded=1 planned=1 issues=0"
    ) in text
    assert "- collector observed: 2026-07-09T10:00:00+00:00" in text
    assert "- query log continuity: at_capacity=true status=confirmed" in text
    assert (
        "- collector next step: Run the Recent profile worker to process planned profile jobs."
    ) in text
    assert (
        "- profile worker: claimed=1 completed=1 retried=0 failed=0 "
        "lease_lost=0 cache=1 artifacts=1 issues=0"
    ) in text
    assert "- profile worker observed: 2026-07-09T10:00:00+00:00" in text
    assert (
        "- profile worker next step: Refresh Online History to see newly materialized Details."
        in text
    )
    assert "- profile backlog: pending=2 retry=1 leased=1 stale_leased=0 failed=0" in text
    assert (
        "- profile backlog next step: Let the profile worker retry pending rows; "
        "investigate repeated normalized error codes if retry backlog persists."
    ) in text
    assert "- retention: deleted=4 summaries=1 jobs=1 cache=1 artifacts=1 issues=0" in text
    assert (
        "- profile remediation: mode=dry_run matched=4 selected=3 requeued=0 skipped=1 issues=0"
        in text
    )
    assert (
        "- profile remediation next step: Review the bounded count, then rerun remediation "
        "with --apply."
    ) in text


def test_recent_history_operator_readiness_blocks_failed_and_stale_profile_backlog():
    worker = worker_summary()
    worker["profile_backlog_health"] = {
        "pending_jobs": 2,
        "retry_pending_jobs": 1,
        "leased_jobs": 1,
        "stale_leased_jobs": 1,
        "failed_jobs": 3,
    }

    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker,
    )

    assert result.payload()["status"] == "blocked"
    assert result.payload()["issue_codes"] == [
        "profile_worker_backlog_stale_leases",
        "profile_worker_backlog_failed_jobs",
    ]


def test_recent_history_operator_readiness_blocks_missing_and_not_ready_summaries():
    worker = worker_summary()
    worker["status"] = "warning"

    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=None,
        profile_worker_summary=worker,
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["accepted_summary_count"] == 0
    assert payload["evidence_summary_count"] == 2
    assert payload["operations"]["postgres_readiness"] == {"accepted": False}
    assert payload["operations"]["profile_worker"] == {"accepted": False}
    assert payload["operations"]["collector_summary"] == {"present": False, "accepted": False}
    assert payload["operations"]["retention"] == {"present": False, "accepted": False}
    assert payload["operations"]["profile_remediation"] == {"present": False, "accepted": False}
    assert payload["issue_codes"] == [
        "postgres_readiness_summary_missing",
        "profile_worker_summary_status_not_ready",
    ]


def test_recent_history_operator_readiness_requires_profile_backlog_health():
    worker = worker_summary()
    worker.pop("profile_backlog_health")

    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker,
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["accepted_summary_count"] == 1
    assert payload["evidence_summary_count"] == 2
    assert payload["operations"]["postgres_readiness"]["accepted"] is True
    assert payload["operations"]["profile_worker"] == {"accepted": False}
    assert payload["issue_codes"] == ["profile_worker_summary_backlog_health_missing"]


def test_recent_history_operator_readiness_rejects_unsafe_retained_payload():
    worker = worker_summary()
    worker["query_id"] = "query-123"
    worker["debug"] = "postgresql://query_doctor:secret@private-host.example.net/db"

    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker,
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["issue_codes"] == ["profile_worker_summary_unsafe"]
    assert payload["operations"]["profile_worker"] == {"accepted": False}
    serialized = json.dumps(payload, sort_keys=True)
    assert "query-123" not in serialized
    assert "private-host" not in serialized
    assert "secret" not in serialized


def test_recent_history_operator_readiness_blocks_not_ready_collector_but_projects_state():
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=collector_summary("warning"),
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["accepted_summary_count"] == 2
    assert payload["evidence_summary_count"] == 3
    assert payload["collector_summary_present"] is True
    assert payload["issue_codes"] == ["collector_summary_status_not_ready"]
    assert payload["operations"]["collector_summary"] == {
        "present": True,
        "accepted": False,
        "status": "warning",
        "observed_at_iso": "2026-07-09T10:00:00+00:00",
        "discover_only": True,
        "history_backend": "postgres",
        "summaries_inspected": 2,
        "candidates_discovered": 2,
        "selected_count": 1,
        "summaries_recorded": 1,
        "profile_jobs_planned": 1,
        "query_log_at_capacity": True,
        "query_log_continuity_status": "confirmed",
        "issue_count": 1,
        "next_step": "Review collector warning reason codes before relying on scheduled intake.",
    }
    serialized = json.dumps(payload, sort_keys=True)
    assert "query-123" not in serialized


def test_recent_history_operator_readiness_blocks_unsafe_collector_summary():
    collector = collector_summary()
    collector["query_id"] = "query-123"

    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=collector,
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["issue_codes"] == ["collector_summary_unsafe"]
    assert payload["operations"]["collector_summary"] == {"present": True, "accepted": False}
    serialized = json.dumps(payload, sort_keys=True)
    assert "query-123" not in serialized


def test_recent_history_operator_readiness_blocks_unsafe_remediation_summary():
    remediation = remediation_summary()
    remediation["query_id"] = "query-123"

    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        remediation_summary=remediation,
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["issue_codes"] == ["profile_remediation_summary_unsafe"]
    assert payload["operations"]["profile_remediation"] == {"present": True, "accepted": False}
    serialized = json.dumps(payload, sort_keys=True)
    assert "query-123" not in serialized


def test_recent_history_operator_readiness_cli_json_and_summary_are_raw_free(tmp_path, capsys):
    postgres_path = tmp_path / "postgres-summary.json"
    worker_path = tmp_path / "worker-summary.json"
    collector_path = tmp_path / "collector-summary.json"
    retention_path = tmp_path / "retention-summary.json"
    remediation_path = tmp_path / "remediation-summary.json"
    summary_path = tmp_path / "operator-summary.json"
    postgres_path.write_text(json.dumps(postgres_summary()), encoding="utf-8")
    worker_path.write_text(json.dumps(worker_summary()), encoding="utf-8")
    collector_path.write_text(json.dumps(collector_summary()), encoding="utf-8")
    retention_path.write_text(json.dumps(retention_summary()), encoding="utf-8")
    remediation_path.write_text(json.dumps(remediation_summary()), encoding="utf-8")

    rc = cli.main(
        [
            "--json",
            "--summary-json",
            str(summary_path),
            "--postgres-readiness-summary-json",
            str(postgres_path),
            "--profile-worker-summary-json",
            str(worker_path),
            "--collector-summary-json",
            str(collector_path),
            "--retention-summary-json",
            str(retention_path),
            "--profile-remediation-summary-json",
            str(remediation_path),
        ]
    )

    assert rc == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert stdout_payload["summary_kind"] == SUMMARY_KIND
    assert stdout_payload["operations"]["collector_summary"]["summaries_recorded"] == 1
    assert stdout_payload["operations"]["profile_worker"]["jobs_completed"] == 1
    assert stdout_payload["operations"]["retention"]["total_deleted"] == 4
    assert stdout_payload["operations"]["profile_remediation"]["selected_failed_jobs"] == 3
    serialized = json.dumps(stdout_payload, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "postgres-summary" not in serialized
    assert "collector-summary" not in serialized
    assert "remediation-summary" not in serialized
    assert "query-123" not in serialized
    assert "secret" not in serialized


def test_recent_history_operator_readiness_cli_fail_on_warning_without_path_echo(tmp_path, capsys):
    missing_path = tmp_path / "missing-postgres-summary.json"
    worker_path = tmp_path / "worker-summary.json"
    worker_path.write_text(json.dumps(worker_summary()), encoding="utf-8")

    rc = cli.main(
        [
            "--fail-on-warning",
            "--postgres-readiness-summary-json",
            str(missing_path),
            "--profile-worker-summary-json",
            str(worker_path),
        ]
    )

    assert rc == 1
    output = capsys.readouterr().out
    assert "Recent history operator readiness: blocked" in output
    assert str(missing_path) not in output
    assert "missing-postgres-summary" not in output


def test_recent_history_operator_readiness_ignores_evidence_age_without_the_option():
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=collector_summary(),
        now=datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc),
    )
    payload = result.payload()

    assert payload["status"] == "ready"
    assert not any(check["id"] == "collector_summary_freshness" for check in payload["checks"])
    assert not any(check["id"] == "profile_worker_summary_freshness" for check in payload["checks"])


def test_recent_history_operator_readiness_accepts_collector_summary_within_the_age():
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=collector_summary(),
        max_evidence_age_minutes=30,
        now=datetime(2026, 7, 9, 10, 25, tzinfo=timezone.utc),
    )
    payload = result.payload()

    assert payload["status"] == "ready"
    assert payload["issue_codes"] == []
    assert {
        "id": "collector_summary_freshness",
        "status": "ready",
        "summary": "Retained collector summary is within the accepted age",
    } in payload["checks"]
    assert {
        "id": "profile_worker_summary_freshness",
        "status": "ready",
        "summary": "Retained profile worker summary is within the accepted age",
    } in payload["checks"]


def test_recent_history_operator_readiness_blocks_a_collector_summary_that_stopped_moving():
    worker = worker_summary()
    worker["observed_at_iso"] = "2026-07-16T09:50:00+00:00"
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker,
        collector_summary=collector_summary("idle"),
        max_evidence_age_minutes=30,
        now=datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc),
    )
    payload = result.payload()

    assert payload["status"] == "blocked"
    assert payload["issue_codes"] == ["collector_summary_freshness_stale"]


def test_recent_history_operator_readiness_blocks_unreadable_and_absent_observation_time():
    unreadable = collector_summary()
    unreadable["observed_at_iso"] = "not a timestamp"

    unreadable_result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=unreadable,
        max_evidence_age_minutes=30,
        now=datetime(2026, 7, 9, 10, 25, tzinfo=timezone.utc),
    )
    absent_result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        max_evidence_age_minutes=30,
        now=datetime(2026, 7, 9, 10, 25, tzinfo=timezone.utc),
    )

    assert unreadable_result.payload()["issue_codes"] == [
        "collector_summary_freshness_observed_at_unreadable"
    ]
    assert absent_result.payload()["issue_codes"] == ["collector_summary_freshness_absent"]


def test_recent_history_operator_readiness_blocks_stale_profile_worker_summary():
    collector = collector_summary()
    collector["observed_at_iso"] = "2026-07-09T10:50:00+00:00"
    result = audit_recent_history_operator_readiness(
        postgres_readiness_summary=postgres_summary(),
        profile_worker_summary=worker_summary(),
        collector_summary=collector,
        max_evidence_age_minutes=30,
        now=datetime(2026, 7, 9, 11, 0, tzinfo=timezone.utc),
    )

    assert result.payload()["status"] == "blocked"
    assert result.payload()["issue_codes"] == ["profile_worker_summary_freshness_stale"]


def test_recent_history_operator_readiness_cli_blocks_on_stale_evidence(tmp_path, capsys):
    postgres_path = tmp_path / "postgres.json"
    worker_path = tmp_path / "worker.json"
    collector_path = tmp_path / "collector.json"
    postgres_path.write_text(json.dumps(postgres_summary()), encoding="utf-8")
    worker_path.write_text(json.dumps(worker_summary()), encoding="utf-8")
    collector_path.write_text(json.dumps(collector_summary()), encoding="utf-8")

    exit_code = cli.main(
        [
            "--postgres-readiness-summary-json",
            str(postgres_path),
            "--profile-worker-summary-json",
            str(worker_path),
            "--collector-summary-json",
            str(collector_path),
            "--max-evidence-age-minutes",
            "30",
            "--fail-on-warning",
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "collector_summary_freshness" in out
    assert str(collector_path) not in out


def test_recent_history_operator_readiness_cli_takes_a_positive_evidence_age_only():
    required = [
        "--postgres-readiness-summary-json",
        "postgres.json",
        "--profile-worker-summary-json",
        "worker.json",
    ]

    assert cli.parse_args(required).max_evidence_age_minutes is None
    assert (
        cli.parse_args(required + ["--max-evidence-age-minutes", "30"]).max_evidence_age_minutes
        == 30
    )
    with pytest.raises(SystemExit):
        cli.parse_args(required + ["--max-evidence-age-minutes", "0"])
