import json

import pytest

from query_doctor.cli import history_case_index as cli
from query_doctor.recent.history_store import RecentHistoryStoreError


class Store:
    def __init__(self, remaining):
        self.remaining = remaining
        self.prepared = 0
        self.limits = []

    def prepare_case_ref_index(self):
        self.prepared += 1

    def backfill_case_refs(self, *, limit):
        self.limits.append(limit)
        count = min(self.remaining, limit)
        self.remaining -= count
        return count


def setup_store(monkeypatch, remaining):
    store = Store(remaining)
    monkeypatch.setattr(cli, "load_web_local_config", lambda *_a, **_k: {})
    monkeypatch.setattr(cli, "_history_store_from_config", lambda _config: (store, "sqlite"))
    return store


def test_backfill_requires_explicit_apply(monkeypatch):
    store = setup_store(monkeypatch, 3)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", "fixture.json"])
    assert exc.value.code == 2
    assert store.prepared == 0 and store.limits == []


def test_backfill_budget_and_schema_preparation_are_separate(monkeypatch, capsys):
    store = setup_store(monkeypatch, 3)
    assert (
        cli.main(["--config", "fixture.json", "--apply", "--batch-size", "2", "--max-rows", "2"])
        == 2
    )
    assert json.loads(capsys.readouterr().out) == {"status": "bounded", "processed": 2}
    assert store.prepared == 0 and store.remaining == 1
    assert cli.main(["--config", "fixture.json", "--apply", "--prepare-schema"]) == 0
    assert store.prepared == 1
    assert json.loads(capsys.readouterr().out) == {"status": "complete", "processed": 1}


def test_backfill_failure_output_is_raw_free(monkeypatch, capsys):
    store = setup_store(monkeypatch, 3)

    def fail(**_kwargs):
        raise RecentHistoryStoreError("private-source-and-query")

    store.backfill_case_refs = fail
    assert cli.main(["--config", "fixture.json", "--apply"]) == 1
    result = capsys.readouterr().out
    assert "private-source-and-query" not in result
    assert json.loads(result)["reason"] == "history_case_index_unavailable"
