from types import SimpleNamespace

import pytest

from query_doctor.recent import history_store
from query_doctor.recent.history_store_diagnostics import log_history_persistence_failure


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("23502", "postgres_not_null_violation"),
        ("23505", "postgres_unique_violation"),
        ("40001", "postgres_serialization_failure"),
        ("40P01", "postgres_deadlock"),
        ("42501", "postgres_permission_denied"),
        ("42P01", "postgres_relation_missing"),
        ("53300", "postgres_too_many_connections"),
        ("57014", "postgres_query_canceled"),
        ("08006", "postgres_connection_failure"),
        ("SELECT secret", "history_store_error"),
        (["23505"], "history_store_error"),
        (None, "history_store_error"),
    ],
)
def test_wrapped_driver_failure_logs_only_fixed_codes(state, reason, caplog):
    driver_error = RuntimeError("SELECT secret FROM private_table password=hidden")
    driver_error.sqlstate = state
    wrapped = history_store.RecentHistoryStoreError("private-host /private/path")
    wrapped.__cause__ = driver_error

    log_history_persistence_failure(wrapped, operation="summary_upsert")

    assert [record.getMessage() for record in caplog.records] == [
        "recent_history_persistence_failed operation=summary_upsert reason_code=" + reason
    ]
    assert caplog.records[0].exc_info is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("/private/path"), "storage_permission_denied"),
        (TimeoutError("private-host"), "storage_timeout"),
        (OSError("/private/path"), "history_store_error"),
    ],
)
def test_store_initialization_failure_retains_warning(monkeypatch, caplog, error, reason):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(history_store, "recent_history_store_from_config", fail)
    count, warning = history_store.persist_recent_history([], config=SimpleNamespace(), env={})

    assert count == 0
    assert warning == history_store.recent_history_store_warning()
    assert [record.getMessage() for record in caplog.records] == [
        "recent_history_persistence_failed operation=store_initialize reason_code=" + reason
    ]


def test_cyclic_exception_cause_is_bounded(caplog):
    error = RuntimeError("private-host")
    error.__cause__ = error
    log_history_persistence_failure(error, operation="summary_upsert")
    assert "reason_code=history_store_error" in caplog.text
    assert "private-host" not in caplog.text
