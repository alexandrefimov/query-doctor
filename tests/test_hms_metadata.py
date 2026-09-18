import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from query_doctor.config.contract import ConfigError, normalize_config_value, validate_config_field
from query_doctor.impala import hms_metadata, metadata_workflow
from query_doctor.impala.hms_metadata import (
    HmsColumn,
    HmsMetadataConnectionError,
    HmsMetadataError,
    HmsMetadataTimeoutError,
    HmsMetadataUnavailableError,
    HmsPostgresMetadataReader,
    HmsTableSnapshot,
    column_stats_facts,
    format_bytes,
    show_create_facts,
    table_stats_facts,
)
from query_doctor.impala.table_metadata_facts import collect_table_metadata_context


REPO_DIR = Path(__file__).resolve().parents[1]
DSN_ENV = "QD_TEST_HMS_DSN"
PARTITIONED = {
    "table": (
        7,
        "EXTERNAL_TABLE",
        "hdfs://nn.example.invalid:8020/warehouse/events",
        "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        70,
    ),
    "params": [("numRows", "1200"), ("impala.lastComputeStatsTime", "1767225600")],
    "keys": [("dt",)],
    "summary": (3, 2, 1, 3, 4096),
    "columns": [
        ("event_id", "bigint", True, 1100, 0, None, None, None, None),
        ("payload", "string", True, 900, 5, 12.5, 64, None, None),
        ("is_test", "boolean", True, None, 0, None, None, 3, 1197),
    ],
}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.connection.queries.append((sql, params))
        self.rows = self.connection.respond(sql)

    def fetchall(self):
        return self.rows


class FakeConnection:
    """Answers the reader's queries by the metastore table each one reads."""

    def __init__(self, tables=None, error=None):
        self.tables = tables or {}
        self.error = error
        self.queries = []
        self.current = None
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True

    def respond(self, sql):
        if self.error is not None:
            raise self.error
        if 'FROM "TBLS"' in sql:
            name = ".".join(self.queries[-1][1])
            self.current = self.tables.get(name)
            return [self.current["table"]] if self.current else []
        if 'FROM "TABLE_PARAMS"' in sql:
            return self.current["params"]
        if 'FROM "PARTITION_KEYS"' in sql:
            return self.current["keys"]
        if 'FROM "PARTITIONS"' in sql:
            return [self.current["summary"]]
        if 'FROM "COLUMNS_V2"' in sql:
            return self.current["columns"]
        raise AssertionError(f"unexpected query: {sql}")


class QueryCanceled(Exception):
    sqlstate = "57014"


def reader_for(connection, calls=None):
    def connect(dsn, **options):
        if calls is not None:
            calls.append((dsn, options))
        return connection

    return HmsPostgresMetadataReader(
        "host=db.example.invalid password=hunter2", timeout_sec=7, connect=connect
    )


def test_reader_opens_one_read_only_connection_with_the_statement_timeout():
    calls = []
    connection = FakeConnection({"analytics.events": PARTITIONED})
    reader = reader_for(connection, calls)

    reader.read_table("analytics.events")
    reader.read_table("analytics.missing")
    reader.close()

    assert len(calls) == 1
    options = calls[0][1]
    assert "default_transaction_read_only=on" in options["options"]
    assert "statement_timeout=7000" in options["options"]
    assert options["application_name"] == "query-doctor-metadata"
    assert connection.closed
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in connection.queries)


def test_reader_matches_metastore_names_case_insensitively():
    connection = FakeConnection({"analytics.events": PARTITIONED})

    snapshot = reader_for(connection).read_table("`Analytics`.`Events`")

    assert snapshot is not None
    assert connection.queries[0][1] == ("analytics", "events")


def test_reader_returns_none_for_a_table_the_metastore_does_not_have():
    assert reader_for(FakeConnection()).read_table("analytics.missing") is None


def test_reader_skips_the_partition_query_for_an_unpartitioned_table():
    unpartitioned = dict(PARTITIONED, keys=[])
    connection = FakeConnection({"analytics.events": unpartitioned})

    snapshot = reader_for(connection).read_table("analytics.events")

    assert snapshot.partition_keys == ()
    assert not any('FROM "PARTITIONS"' in sql for sql, _ in connection.queries)


def test_reader_errors_never_carry_the_dsn():
    boom = RuntimeError("connection to host=db.example.invalid password=hunter2 failed")

    with pytest.raises(HmsMetadataError) as query_error:
        reader_for(FakeConnection(error=boom)).read_table("analytics.events")

    def refuse(dsn, **_):
        raise boom

    refusing = HmsPostgresMetadataReader("host=db.example.invalid", timeout_sec=5, connect=refuse)
    with pytest.raises(HmsMetadataConnectionError) as connect_error:
        refusing.read_table("analytics.events")

    for error in (query_error.value, connect_error.value):
        assert "hunter2" not in str(error)
        assert "db.example.invalid" not in str(error)
        assert error.__cause__ is None


def test_reader_reports_a_canceled_statement_as_a_timeout():
    with pytest.raises(HmsMetadataTimeoutError, match="timed out after 7s"):
        reader_for(FakeConnection(error=QueryCanceled())).read_table("analytics.events")


def test_reader_needs_a_dsn_in_the_environment():
    with pytest.raises(HmsMetadataUnavailableError, match=DSN_ENV):
        HmsPostgresMetadataReader.from_env(DSN_ENV, env={}, timeout_sec=5)


def test_partitioned_table_facts_match_the_text_parser_keys():
    snapshot = reader_for(FakeConnection({"analytics.events": PARTITIONED})).read_table(
        "analytics.events"
    )

    assert show_create_facts(snapshot) == {
        "object_type": "table",
        "file_format": "PARQUET",
        "storage_scheme": "hdfs",
        "storage_family": "hdfs",
        "partition_columns": ["dt"],
    }
    assert table_stats_facts(snapshot) == {
        "table_rows": 1200,
        "table_stats_row_count_completeness": "available",
        "partition_count": 3,
        "partitions_with_known_row_count": 2,
        "partitions_with_unknown_row_count": 1,
        "partitions_with_zero_row_count": 1,
        "table_size": "4.00KB",
        "table_stats_last_computed": "2026-01-01T00:00:00Z",
    }
    columns = column_stats_facts(snapshot)
    assert columns["column_stats_completeness"] == "complete"
    assert columns["column_stats_columns"] == ["event_id", "payload", "is_test", "dt"]
    assert columns["column_stats_complete_columns"] == 4
    assert columns["column_stats_missing_markers"] == 0


def test_table_size_is_unknown_when_any_partition_lacks_it():
    snapshot = HmsTableSnapshot(
        table_type="MANAGED_TABLE",
        partition_keys=("dt",),
        partition_count=3,
        partitions_with_known_size=2,
        partition_total_size=10,
    )

    assert "table_size" not in table_stats_facts(snapshot)


def test_missing_row_count_is_unknown_not_zero():
    snapshot = HmsTableSnapshot(table_type="MANAGED_TABLE", table_params={"numRows": "-1"})

    facts = table_stats_facts(snapshot)

    assert facts["table_rows"] == "unknown"
    assert facts["table_stats_row_count_completeness"] == "missing/unknown"
    assert "table_stats_last_computed" not in facts


@pytest.mark.parametrize(
    "column, status",
    [
        (HmsColumn("a", "string"), "all_missing"),
        (HmsColumn("a", "int"), "all_missing"),
        (HmsColumn("a", "int", True, num_distincts=None, num_nulls=0), "ndv_missing"),
        (HmsColumn("a", "string", True, num_distincts=4, num_nulls=0), "size_missing"),
        (HmsColumn("a", "decimal(10,2)", True, num_distincts=4, num_nulls=0), "complete"),
        (HmsColumn("a", "boolean", True, num_nulls=0, num_trues=1, num_falses=2), "complete"),
        (HmsColumn("a", "varchar(8)", True, 4, 0, 3.0, 8), "complete"),
    ],
)
def test_column_status_follows_what_impala_would_show(column, status):
    facts = column_stats_facts(HmsTableSnapshot(table_type="MANAGED_TABLE", columns=(column,)))

    assert facts["column_stats_per_column"] == {"a": status}


def test_column_completeness_ignores_nested_types_and_counts_partition_keys():
    snapshot = HmsTableSnapshot(
        table_type="MANAGED_TABLE",
        partition_keys=("dt",),
        columns=(
            HmsColumn("id", "bigint", True, 5, 0),
            HmsColumn("tags", "array<string>"),
        ),
    )

    facts = column_stats_facts(snapshot)

    assert facts["column_stats_columns"] == ["id", "dt"]
    assert facts["column_stats_completeness"] == "complete"


def test_truncated_column_list_is_never_complete():
    snapshot = HmsTableSnapshot(
        table_type="MANAGED_TABLE",
        columns=(HmsColumn("id", "bigint", True, 5, 0),),
        columns_truncated=True,
    )

    assert column_stats_facts(snapshot)["column_stats_completeness"] == "incomplete/unknown"


def test_views_report_only_their_object_type():
    snapshot = HmsTableSnapshot(table_type="VIRTUAL_VIEW", location="hdfs://nn/x")

    assert show_create_facts(snapshot) == {"object_type": "view"}


@pytest.mark.parametrize(
    "value, rendered",
    [(0, "0B"), (512, "512B"), (1536, "1.50KB"), (11_958_140_000, "11.14GB")],
)
def test_sizes_render_like_impala(value, rendered):
    assert format_bytes(value) == rendered


def run_collector(tmp_path, tables, fake, *extra):
    from query_doctor.cli import collect_impala_context

    argv = []
    for table in tables:
        argv += ["--table", table]
    argv += ["--out", str(tmp_path / "ctx"), "--source", "hms-postgres", *extra]
    rc = collect_impala_context.main(argv, hms_reader=reader_for(fake))
    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))
    return rc, payload


@pytest.fixture(autouse=True)
def isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_collector_reads_the_metastore_without_a_coordinator(tmp_path):
    view = {"table": (8, "VIRTUAL_VIEW", None, None, None), "params": [], "keys": []}
    fake = FakeConnection({"analytics.events": PARTITIONED, "analytics.events_view": view})

    rc, payload = run_collector(
        tmp_path, ["analytics.events", "analytics.events_view"], fake, "--no-redact"
    )

    assert rc == 0
    assert payload["metadata_source"] == "hms-postgres"
    statuses = [(r["table"], r["statement"], r["status"]) for r in payload["results"]]
    assert statuses == [
        ("analytics.events", "SHOW CREATE TABLE", "ok"),
        ("analytics.events", "SHOW TABLE STATS", "ok"),
        ("analytics.events", "SHOW COLUMN STATS", "ok"),
        ("analytics.events_view", "SHOW CREATE TABLE", "ok"),
        ("analytics.events_view", "SHOW TABLE STATS", "not_applicable"),
        ("analytics.events_view", "SHOW COLUMN STATS", "not_applicable"),
    ]

    context = collect_table_metadata_context(tmp_path / "ctx")
    tables = {table["table"]: table for table in context["tables"]}
    events = tables["analytics.events"]
    assert context["metadata_source"] == "hms-postgres"
    assert events["table_rows"] == 1200
    assert events["partition_count"] == 3
    assert events["partitions_with_unknown_row_count"] == 1
    assert events["column_stats_completeness"] == "complete"
    assert events["table_stats_last_computed"] == "2026-01-01T00:00:00Z"
    assert events["storage_family"] == "hdfs"
    assert tables["analytics.events_view"]["object_type"] == "view"


def test_collector_marks_a_missing_table_as_not_found(tmp_path):
    rc, payload = run_collector(tmp_path, ["analytics.missing"], FakeConnection())

    assert rc == 1
    assert {r["status"] for r in payload["results"]} == {"error"}
    context = collect_table_metadata_context(tmp_path / "ctx")
    assert context["statement_issue_counts"] == {"object_not_found": 3}


def test_collector_stops_connecting_after_the_first_refusal(tmp_path):
    from query_doctor.cli import collect_impala_context

    attempts = []

    def refuse(dsn, **_):
        attempts.append(dsn)
        raise OSError("refused")

    reader = HmsPostgresMetadataReader("host=db.example.invalid", timeout_sec=5, connect=refuse)
    rc = collect_impala_context.main(
        [
            "--table",
            "a.b",
            "--table",
            "a.c",
            "--out",
            str(tmp_path / "ctx"),
            "--source",
            "hms-postgres",
        ],
        hms_reader=reader,
    )

    assert rc == 1
    assert len(attempts) == 1
    context = collect_table_metadata_context(tmp_path / "ctx")
    assert context["statement_issue_counts"] == {"connection": 6}


def test_collector_without_the_dsn_env_fails_cleanly(tmp_path, monkeypatch, capsys):
    from query_doctor.cli import collect_impala_context

    monkeypatch.delenv(DSN_ENV, raising=False)
    rc = collect_impala_context.main(
        [
            "--table",
            "a.b",
            "--out",
            str(tmp_path / "ctx"),
            "--source",
            "hms-postgres",
            "--hms-postgres-dsn-env",
            DSN_ENV,
        ]
    )

    assert rc == 2
    assert DSN_ENV in capsys.readouterr().err


def test_parser_keeps_only_known_well_typed_facts(tmp_path):
    (tmp_path / "impala_context.json").write_text(
        json.dumps(
            {
                "tables": ["a.b"],
                "results": [
                    {
                        "table": "a.b",
                        "statement": "SHOW TABLE STATS",
                        "status": "ok",
                        "stdout": "| #Rows |\n| 999 |",
                        "facts": {
                            "table_rows": 5,
                            "partition_count": -3,
                            "raw_sql": "SELECT secret",
                            "object_type": "view",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    table = collect_table_metadata_context(tmp_path)["tables"][0]

    assert table["table_rows"] == 5
    assert table["partition_count"] == 0
    assert table["object_type"] == "unknown"
    assert "raw_sql" not in table


def workflow_args(**overrides):
    values = dict(
        metadata_source="hms-postgres",
        metadata_hms_postgres_dsn_env=DSN_ENV,
        metadata_coordinator=None,
        metadata_auth="kerberos",
        metadata_protocol="hs2",
        metadata_timeout_sec=30,
        metadata_max_output_bytes=1024,
        metadata_redact=True,
        metadata_ssl=False,
        metadata_ca_cert=None,
        metadata_dry_run=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pipeline_collector_cmd_for_the_metastore_carries_no_connection_details(tmp_path):
    cmd = metadata_workflow.build_metadata_collector_cmd(
        workflow_args(), collector_prefix=["qd-collect"], case_dir=tmp_path, tables=["a.b"]
    )

    assert cmd[cmd.index("--source") + 1] == "hms-postgres"
    assert cmd[cmd.index("--hms-postgres-dsn-env") + 1] == DSN_ENV
    assert "--coordinator" not in cmd
    assert "--auth" not in cmd
    assert "--redact" in cmd


def test_pipeline_metastore_config_status(monkeypatch):
    monkeypatch.delenv(DSN_ENV, raising=False)
    assert not metadata_workflow.metadata_config_status(workflow_args()).configured

    monkeypatch.setenv(DSN_ENV, "host=db.example.invalid")
    monkeypatch.setattr(hms_metadata, "driver_available", lambda: False)
    status = metadata_workflow.metadata_config_status(workflow_args())
    assert not status.configured
    assert status.reason == hms_metadata.POSTGRES_DRIVER_MISSING_REASON

    monkeypatch.setattr(hms_metadata, "driver_available", lambda: True)
    assert metadata_workflow.metadata_config_status(workflow_args()).configured


def test_pipeline_metadata_mode_on_needs_no_coordinator_for_the_metastore():
    from query_doctor.cli import pipeline

    args = pipeline.parse_args(
        ["case", "--metadata-mode", "on", "--metadata-source", "hms-postgres"]
    )

    assert args.metadata_source == "hms-postgres"
    assert args.metadata_hms_postgres_dsn_env == "QUERY_DOCTOR_HMS_POSTGRES_DSN"


def test_batch_config_passes_the_metastore_source_to_the_pipeline(tmp_path):
    from query_doctor.cli import batch_recent
    from query_doctor.recent import batch_config, command_args

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata_source": "hms-postgres",
                "metadata_hms_postgres_dsn_env": DSN_ENV,
            }
        ),
        encoding="utf-8",
    )
    args = batch_recent.parse_args(
        [
            "--config",
            str(config_path),
            "--out",
            str(tmp_path / "query-doctor-batch"),
            "--cm-url",
            "https://cm.example.invalid:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-top-limit",
            "3",
        ]
    )
    config = batch_config.build_batch_config(
        args, env={}, cwd=tmp_path, repo_root=REPO_DIR, validate_scan_selection_limits=False
    )
    cmd = []
    command_args.append_metadata_args(cmd, config)

    assert config.metadata_source == "hms-postgres"
    assert cmd[cmd.index("--metadata-source") + 1] == "hms-postgres"
    assert cmd[cmd.index("--metadata-hms-postgres-dsn-env") + 1] == DSN_ENV
    assert "--metadata-coordinator" not in cmd
    assert batch_config.metadata_configuration_preflight_required(config)
    assert not batch_config.metadata_kerberos_preflight_required(config)


def test_config_contract_accepts_the_metastore_keys():
    validate_config_field("metadata_source")
    validate_config_field("metadata_hms_postgres_dsn_env")
    assert normalize_config_value("metadata_source", " hms-postgres ") == "hms-postgres"
    with pytest.raises(ConfigError):
        normalize_config_value("metadata_source", "thrift")
    with pytest.raises(ConfigError):
        normalize_config_value("metadata_hms_postgres_dsn_env", "lowercase")
