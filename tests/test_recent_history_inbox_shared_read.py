from __future__ import annotations

from query_doctor.web import recent_history_inbox


class _Settings:
    public_demo = False
    config = "/etc/query-doctor/query-doctor-config.json"


def _counting_loader(counter: dict[str, int]):
    def loader(settings: object, *, history_view: str) -> dict[str, object]:
        counter["calls"] += 1
        return {"rows": []}

    return loader


def test_a_render_reads_the_retained_history_once(monkeypatch):
    counter = {"calls": 0}
    monkeypatch.setattr(
        recent_history_inbox, "load_recent_history_inbox_summary", _counting_loader(counter)
    )
    settings = _Settings()

    with recent_history_inbox.shared_recent_history_inbox_summary():
        first = recent_history_inbox.recent_history_inbox_summary_from_settings(settings)
        second = recent_history_inbox.recent_history_inbox_summary_from_settings(settings)
        third = recent_history_inbox.recent_history_inbox_summary_from_settings(settings)

    # Three parts of one page ask for this; loading it three times is what made
    # the page slow.
    assert counter["calls"] == 1
    assert first is second is third


def test_the_memo_does_not_outlive_one_render(monkeypatch):
    counter = {"calls": 0}
    monkeypatch.setattr(
        recent_history_inbox, "load_recent_history_inbox_summary", _counting_loader(counter)
    )
    settings = _Settings()

    with recent_history_inbox.shared_recent_history_inbox_summary():
        recent_history_inbox.recent_history_inbox_summary_from_settings(settings)
    with recent_history_inbox.shared_recent_history_inbox_summary():
        recent_history_inbox.recent_history_inbox_summary_from_settings(settings)

    assert counter["calls"] == 2


def test_reading_outside_a_render_still_loads_every_time(monkeypatch):
    counter = {"calls": 0}
    monkeypatch.setattr(
        recent_history_inbox, "load_recent_history_inbox_summary", _counting_loader(counter)
    )
    settings = _Settings()

    recent_history_inbox.recent_history_inbox_summary_from_settings(settings)
    recent_history_inbox.recent_history_inbox_summary_from_settings(settings)

    assert counter["calls"] == 2


def test_online_history_loads_only_the_rows_the_page_can_display(monkeypatch):
    class Store:
        def load_materialized_payloads(self, *, limit: int, details_ready_only: bool):
            assert limit == recent_history_inbox.MAX_HISTORY_INBOX_ROWS
            assert details_ready_only is True
            return []

        def count_summaries(self):
            return 10_000

        def summarize_profile_backlog_health(
            self,
            *,
            now_iso: str,
            prepare_schema: bool,
        ):
            assert prepare_schema is False
            raise recent_history_inbox.RecentHistoryStoreError("not_configured")

    monkeypatch.setattr(recent_history_inbox, "load_web_local_config", lambda *_a, **_k: {})
    monkeypatch.setattr(
        recent_history_inbox,
        "_history_store_from_config",
        lambda _config: (Store(), "postgres"),
    )
    monkeypatch.setattr(
        recent_history_inbox,
        "operator_readiness_summary_from_config",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        recent_history_inbox,
        "collector_summary_from_config",
        lambda *_a, **_k: None,
    )

    summary = recent_history_inbox.load_recent_history_inbox_summary(_Settings())

    assert summary is not None
    assert summary["summaries_inspected"] == 10_000
    assert summary["selected_count"] == 0


def test_online_history_uses_combined_postgres_payload_and_count_read(monkeypatch):
    calls: list[tuple[str, int | None]] = []

    class Store:
        def load_materialized_payloads_with_count(
            self,
            *,
            limit: int,
            prepare_schema: bool,
            details_ready_only: bool,
        ):
            calls.append(("snapshot", limit, prepare_schema, details_ready_only))
            return [], 233_036

        def load_materialized_payloads(self, *, limit: int):
            raise AssertionError("combined PostgreSQL read should be used")

        def count_summaries(self):
            raise AssertionError("combined PostgreSQL read should include the count")

        def summarize_profile_backlog_health(
            self,
            *,
            now_iso: str,
            prepare_schema: bool,
        ):
            calls.append(("backlog", None, prepare_schema))
            raise recent_history_inbox.RecentHistoryStoreError("not_configured")

    monkeypatch.setattr(recent_history_inbox, "load_web_local_config", lambda *_a, **_k: {})
    monkeypatch.setattr(
        recent_history_inbox,
        "_history_store_from_config",
        lambda _config: (Store(), "postgres"),
    )
    monkeypatch.setattr(
        recent_history_inbox,
        "operator_readiness_summary_from_config",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        recent_history_inbox,
        "collector_summary_from_config",
        lambda *_a, **_k: None,
    )

    summary = recent_history_inbox.load_recent_history_inbox_summary(_Settings())

    assert summary is not None
    assert summary["summaries_inspected"] == 233_036
    assert calls == [
        ("snapshot", recent_history_inbox.MAX_HISTORY_INBOX_ROWS, False, True),
        ("backlog", None, False),
    ]


def test_online_history_missing_postgres_schema_is_safely_unavailable(monkeypatch):
    class Store:
        def load_materialized_payloads_with_count(
            self,
            *,
            limit: int,
            prepare_schema: bool,
            details_ready_only: bool,
        ):
            assert limit == recent_history_inbox.MAX_HISTORY_INBOX_ROWS
            assert prepare_schema is False
            assert details_ready_only is True
            raise recent_history_inbox.RecentHistoryStoreError(
                "postgres_recent_history_materialized_load_failed"
            )

    monkeypatch.setattr(recent_history_inbox, "load_web_local_config", lambda *_a, **_k: {})
    monkeypatch.setattr(
        recent_history_inbox,
        "_history_store_from_config",
        lambda _config: (Store(), "postgres"),
    )

    summary = recent_history_inbox.load_recent_history_inbox_summary(_Settings())

    assert summary == recent_history_inbox.recent_history_unavailable_summary()


def test_online_history_case_carries_runtime_facts_from_the_retained_summary():
    from query_doctor.web.details_facts import case_query_context_facts
    from query_doctor.web.recent_history_inbox import _history_case

    # Online History keeps no case directory, so Details used to lose the
    # runtime facts the listing already stores.
    case = _history_case(
        1,
        {
            "query_id": "1111111111111111:2222222222222222",
            "status": "finished",
            "query_state": "finished",
            "query_type": "query",
            "pool": "root.default",
            "start_time": "2026-09-09T16:56:15Z",
            "end_time": "2026-09-09T16:56:23Z",
            "duration_ms": 7821,
            "admission_wait_ms": 0,
            "rows_produced": 126,
            "bytes_read": 286294241,
            "memory_aggregate_peak": 11640268390,
            "profile_status": "analyzed",
        },
        history_view="details_ready",
    )

    facts = case_query_context_facts(case)

    assert facts is not None
    summary = facts["summary"]
    assert summary["start_time"] == "2026-09-09T16:56:15Z"
    assert summary["end_time"] == "2026-09-09T16:56:23Z"
    assert summary["duration"] == "7.821s"
    assert summary["bytes_read"] == "273.03 MiB"
    assert summary["memory_aggregate_peak"] == "10.84 GiB"
    assert summary["rows_produced"] == "126"
    assert summary["pool"] == "root.default"
    # A zero admission wait is the normal case and would say nothing on a row.
    assert "admission_wait" not in summary


def test_online_history_case_without_runtime_numbers_carries_no_query_context():
    from query_doctor.web.details_facts import case_query_context_facts
    from query_doctor.web.recent_history_inbox import _history_case

    case = _history_case(1, {"query_id": "abc"}, history_view="details_ready")

    assert case_query_context_facts(case) is None
