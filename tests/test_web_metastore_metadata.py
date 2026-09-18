import json
from pathlib import Path

import pytest

from query_doctor.cli import collect_impala_context
from query_doctor.impala import hms_metadata, hs2_runner
from query_doctor.impala.hms_metadata import HmsPostgresMetadataReader
from query_doctor.web.cluster_selection import settings_for_cluster_key
from query_doctor.web.models import WebClusterConfig, metadata_collection_configured
from web_server_test_support import load_web_module


DSN_ENV = "QD_TEST_WEB_HMS_DSN"


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.connection.queries.append(sql)
        if 'FROM "TBLS"' in sql:
            self.rows = [(1, "MANAGED_TABLE", "hdfs://nn.example.invalid/w/orders", "Parquet", 9)]
        elif 'FROM "TABLE_PARAMS"' in sql:
            self.rows = [("numRows", "42")]
        elif 'FROM "VERSION"' in sql:
            self.rows = [("4.0.0",)]
        elif 'FROM "COLUMNS_V2"' in sql:
            self.rows = [("id", "int", True, 42, 0, None, None, None, None)]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.queries = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


def web_settings(tmp_path, **config):
    module = load_web_module()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return module, module.build_web_settings(
        module.parse_args(["--config", str(path)]), cwd=tmp_path
    )


def test_metastore_source_needs_the_dsn_in_the_environment(monkeypatch):
    cluster = WebClusterConfig(
        key="k", label="k", metadata_source="hms-postgres", metadata_hms_postgres_dsn_env=DSN_ENV
    )

    monkeypatch.delenv(DSN_ENV, raising=False)
    assert not metadata_collection_configured(cluster)
    monkeypatch.setenv(DSN_ENV, "host=db.example.invalid")
    assert metadata_collection_configured(cluster)
    assert not metadata_collection_configured(WebClusterConfig(key="k", label="k"))


def test_web_batch_command_passes_the_metastore_source(tmp_path, monkeypatch):
    monkeypatch.setenv(DSN_ENV, "host=db.example.invalid")
    module, settings = web_settings(
        tmp_path, metadata_source="hms-postgres", metadata_hms_postgres_dsn_env=DSN_ENV
    )
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["3"], "parallelism": ["2"]}
    )

    cmd, _out_dir = module.build_batch_command("c" * 32, batch_config, settings)

    assert cmd[cmd.index("--metadata-mode") + 1] == "on"
    assert cmd[cmd.index("--metadata-source") + 1] == "hms-postgres"
    assert cmd[cmd.index("--metadata-hms-postgres-dsn-env") + 1] == DSN_ENV
    assert "--metadata-coordinator" not in cmd
    assert "--metadata-auth" not in cmd


def test_selected_cluster_carries_the_metastore_source(tmp_path, monkeypatch):
    monkeypatch.setenv(DSN_ENV, "host=db.example.invalid")
    module, settings = web_settings(
        tmp_path,
        active_cluster_key="k8s",
        clusters=[
            {
                "id": "k8s",
                "cluster_type": "impala",
                "impala_profile_hosts": ["coordinator.example.invalid"],
                "metadata_source": "hms-postgres",
                "metadata_hms_postgres_dsn_env": DSN_ENV,
            }
        ],
    )

    selected = settings_for_cluster_key(settings, "k8s")

    assert selected.metadata_source == "hms-postgres"
    assert selected.metadata_hms_postgres_dsn_env == DSN_ENV
    assert module.metadata_configured(selected)


def test_metadata_preflight_for_the_metastore_skips_impyla_and_kerberos(tmp_path, monkeypatch):
    monkeypatch.setenv(DSN_ENV, "host=db.example.invalid")
    monkeypatch.setattr(hms_metadata, "driver_available", lambda: True)
    monkeypatch.setattr(hs2_runner, "driver_available", lambda: False)
    module, settings = web_settings(
        tmp_path, metadata_source="hms-postgres", metadata_hms_postgres_dsn_env=DSN_ENV
    )

    def no_subprocess(*_args, **_kwargs):
        raise AssertionError("klist must not run for the metastore source")

    module.preflight_web_metadata_batch(settings, runner=no_subprocess)


def test_optimizer_reads_table_metadata_from_the_metastore(tmp_path, monkeypatch):
    monkeypatch.setenv(DSN_ENV, "host=db.example.invalid")
    monkeypatch.setattr(hms_metadata, "driver_available", lambda: True)
    monkeypatch.setattr(hs2_runner, "driver_available", lambda: False)
    connection = FakeConnection()
    monkeypatch.setattr(
        collect_impala_context,
        "open_hms_reader",
        lambda args: HmsPostgresMetadataReader(
            "dsn", timeout_sec=5, connect=lambda _dsn, **_: connection
        ),
    )
    module, settings = web_settings(
        tmp_path, metadata_source="hms-postgres", metadata_hms_postgres_dsn_env=DSN_ENV
    )

    result = module.run_optimizer_analysis("select id from example_sales.orders", settings)

    assert result.metadata_status == "collected"
    assert any('FROM "TBLS"' in sql for sql in connection.queries)


@pytest.fixture(autouse=True)
def isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Path.cwd() == tmp_path
