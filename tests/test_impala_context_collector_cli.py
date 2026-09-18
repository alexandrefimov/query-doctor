import json
import subprocess
import sys
from pathlib import Path

import pytest

from query_doctor.impala.hs2_runner import (
    ImpalaStatementError,
    ImpalaStatementTimeoutError,
    StatementRows,
)


REPO_DIR = Path(__file__).resolve().parents[1]


class FakeSession:
    """Stand-in for a HiveServer2 session: SQL maps to rows or to a failure."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []
        self.closed = False

    def run(self, sql, *, timeout_sec=None):
        self.calls.append(sql)
        response = self._responses(sql) if callable(self._responses) else self._responses[sql]
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.closed = True


def text_rows(text):
    """The single text column SHOW CREATE TABLE returns."""
    return StatementRows(columns=("result",), rows=((text,),))


def table_rows(columns, *rows):
    return StatementRows(columns=tuple(columns), rows=tuple(tuple(row) for row in rows))


def load_collector_module():
    from query_doctor.cli import collect_impala_context

    return collect_impala_context


def test_package_entrypoint_keeps_repo_root_config_lookup():
    from query_doctor.cli import collect_impala_context

    assert collect_impala_context.REPO_DIR == REPO_DIR


@pytest.fixture(autouse=True)
def isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "raw, normalized",
    [
        ("db.table", "db.table"),
        ("`db`.`table`", "db.table"),
        ("db.`table`", "db.table"),
        # Impala accepts a quoted name that starts with a digit, and the
        # collector quotes everything it emits.
        ("db.2014_2018_facts", "db.2014_2018_facts"),
        ("`db`.`2014_2018_facts`", "db.2014_2018_facts"),
        ("2014_marts.facts", "2014_marts.facts"),
    ],
)
def test_valid_table_identifiers_are_normalized(raw, normalized):
    module = load_collector_module()

    assert module.normalize_table_identifier(raw) == normalized


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("db", "db"),
        ("`db`", "db"),
        ("te_ruby_agg", "te_ruby_agg"),
        ("1db", "1db"),
        ("`2014_marts`", "2014_marts"),
    ],
)
def test_valid_database_identifiers_are_normalized(raw, normalized):
    module = load_collector_module()

    assert module.normalize_database_identifier(raw) == normalized


@pytest.mark.parametrize(
    "raw",
    [
        "db.table; DROP TABLE x",
        "db.table -- comment",
        "db.table/*comment*/",
        "db.table where x=1",
        "catalog.db.table",
        "unqualified_table",
        "db.'table'",
        "db.*",
        "db.table\nSHOW TABLES",
    ],
)
def test_invalid_table_identifiers_are_rejected(raw):
    module = load_collector_module()

    with pytest.raises(module.CollectorError):
        module.normalize_table_identifier(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "catalog.db",
        "db; DROP",
        "db -- comment",
        "db name",
        "'db'",
    ],
)
def test_invalid_database_identifiers_are_rejected(raw):
    module = load_collector_module()

    with pytest.raises(module.CollectorError):
        module.normalize_database_identifier(raw)


def test_generated_sql_is_exactly_allowlisted():
    module = load_collector_module()

    plans = module.build_statement_plan(["db.table"])

    assert [plan.sql for plan in plans] == [
        "SHOW CREATE TABLE `db`.`table`",
        "SHOW TABLE STATS `db`.`table`",
        "SHOW COLUMN STATS `db`.`table`",
    ]
    for plan in plans:
        module.validate_read_only_statement(plan.sql, plan.table)
    module.validate_read_only_statement("SHOW CREATE TABLE db.table", "db.table")


def test_a_table_name_starting_with_a_digit_still_produces_quoted_allowlisted_sql():
    module = load_collector_module()

    plans = module.build_statement_plan(["2014_marts.2014_2018_facts"])

    assert [plan.sql for plan in plans] == [
        "SHOW CREATE TABLE `2014_marts`.`2014_2018_facts`",
        "SHOW TABLE STATS `2014_marts`.`2014_2018_facts`",
        "SHOW COLUMN STATS `2014_marts`.`2014_2018_facts`",
    ]
    for plan in plans:
        module.validate_read_only_statement(plan.sql, plan.table)


@pytest.mark.parametrize(
    "raw",
    [
        "db.tab`le",
        "db.`tab`le`",
        "`db`.`tab``le`",
    ],
)
def test_a_backtick_inside_a_name_is_still_refused(raw):
    # The quoting is what keeps a digit-leading name safe, so nothing may
    # break out of it.
    module = load_collector_module()

    with pytest.raises(module.CollectorError):
        module.normalize_table_identifier(raw)


def test_repeated_tables_are_deduped_deterministically():
    module = load_collector_module()

    tables = module.dedupe_preserve_order(
        module.normalize_table_identifier(table)
        for table in ["db.table", "db.other_table", "`db`.`table`"]
    )
    plans = module.build_statement_plan(tables)

    assert tables == ["db.table", "db.other_table"]
    assert [plan.sql for plan in plans] == [
        "SHOW CREATE TABLE `db`.`table`",
        "SHOW TABLE STATS `db`.`table`",
        "SHOW COLUMN STATS `db`.`table`",
        "SHOW CREATE TABLE `db`.`other_table`",
        "SHOW TABLE STATS `db`.`other_table`",
        "SHOW COLUMN STATS `db`.`other_table`",
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM db.table",
        "COMPUTE STATS db.table",
        "DROP TABLE db.table",
        "CREATE TABLE db.new_table (id int)",
        "ALTER TABLE db.table ADD COLUMNS (x int)",
        "INSERT INTO db.table VALUES (1)",
        "DELETE FROM db.table WHERE id = 1",
        "UPDATE db.table SET id = 1",
        "MERGE INTO db.table t USING db.other o ON t.id = o.id",
        "TRUNCATE TABLE db.table",
        "REFRESH db.table",
        "INVALIDATE METADATA db.table",
        "MSCK REPAIR TABLE db.table",
        "SHOW PARTITIONS db.table",
        "DESCRIBE FORMATTED db.table",
        "EXPLAIN SELECT * FROM db.table",
        "SHOW TABLE STATS db.table; REFRESH db.table",
    ],
)
def test_forbidden_statements_are_rejected(sql):
    module = load_collector_module()

    with pytest.raises(module.CollectorError):
        module.validate_read_only_statement(sql, "db.table")


def test_dry_run_prints_plan_and_does_not_execute(tmp_path, capsys):
    module = load_collector_module()
    session = FakeSession(lambda sql: pytest.fail("dry-run must not run a statement"))

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--dry-run",
        ],
        session=session,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert session.calls == []
    assert "coordinator: <required for execution>" in captured.out
    assert "auth: kerberos" in captured.out
    assert "protocol: hs2" in captured.out
    assert "kerberos service name: impala (driver default)" in captured.out
    assert "SHOW CREATE TABLE <db>.<table>" in captured.out
    assert "SHOW TABLE STATS <db>.<table>" in captured.out
    assert "SHOW COLUMN STATS <db>.<table>" in captured.out
    assert "DESCRIBE" not in captured.out
    assert "EXPLAIN" not in captured.out
    assert (tmp_path / "ctx" / "impala_context.md").exists()
    assert (tmp_path / "ctx" / "impala_context.json").exists()


def test_dry_run_redacts_coordinator_and_ca_cert_path(tmp_path, capsys):
    module = load_collector_module()

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21000",
            "--ssl",
            "--ca-cert",
            "/user/alice/ssl/impala-ca.pem",
            "--dry-run",
        ],
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "coordinator01.example.com" not in captured.out
    assert "alice" not in captured.out
    assert "host_01:21000" in captured.out
    assert "/user/<user>/ssl/impala-ca.pem" in captured.out


def test_missing_required_args_fail():
    module = load_collector_module()

    with pytest.raises(SystemExit):
        module.parse_args(["--out", "ctx"])
    with pytest.raises(SystemExit):
        module.parse_args(["--table", "db.table"])
    with pytest.raises(SystemExit):
        module.parse_args(["--table", "db.table", "--out", "ctx"])


def test_auth_and_protocol_validation():
    module = load_collector_module()

    args = module.parse_args(
        [
            "--table",
            "db.table",
            "--out",
            "ctx",
            "--coordinator",
            "coordinator01.example.com:21000",
            "--auth",
            "kerberos",
            "--protocol",
            "hs2",
        ]
    )

    assert args.auth == "kerberos"
    assert args.protocol == "hs2"

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--table",
                "db.table",
                "--out",
                "ctx",
                "--coordinator",
                "coordinator01.example.com:21000",
                "--auth",
                "ldap",
            ]
        )
    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--table",
                "db.table",
                "--out",
                "ctx",
                "--coordinator",
                "coordinator01.example.com:21000",
                "--protocol",
                "unsafe;value",
            ]
        )
    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--table",
                "db.table",
                "--out",
                "ctx",
                "--coordinator",
                "coordinator01.example.com:21000",
                "--ca-cert",
                "/tmp/ca.pem",
            ]
        )


def test_local_config_applies_metadata_defaults_and_cli_overrides(tmp_path):
    module = load_collector_module()
    config_path = tmp_path / "metadata-config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata_coordinator": "config-coordinator.example.com:21000",
                "metadata_impala_shell": "/opt/config/impala-shell",
                "metadata_auth": "kerberos",
                "metadata_protocol": "hs2",
                "metadata_kerberos_service_name": "hive",
                "metadata_kerberos_host_fqdn": "config-coordinator.example.com",
                "metadata_ssl": True,
                "metadata_ca_cert": "/tmp/config-ca.pem",
                "metadata_timeout_sec": 44,
                "metadata_max_tables": 2,
                "metadata_max_output_bytes": 9999,
                "metadata_redact": False,
                "metadata_krb5ccname": "FILE:/tmp/krb5cc_metadata_config",
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(["--config", str(config_path), "--table", "db.table", "--out", "ctx"])

    assert args.coordinator == "config-coordinator.example.com:21000"
    assert args.protocol == "hs2"
    assert args.kerberos_service_name == "hive"
    assert args.kerberos_host_fqdn == "config-coordinator.example.com"
    assert args.ssl is True
    assert args.ca_cert == "/tmp/config-ca.pem"
    assert args.timeout_sec == 44
    assert args.max_output_bytes == 9999
    assert args.redact is False
    assert args.krb5ccname == "FILE:/tmp/krb5cc_metadata_config"

    args = module.parse_args(
        [
            "--config",
            str(config_path),
            "--table",
            "db.table",
            "--out",
            "ctx",
            "--coordinator",
            "cli-coordinator.example.com:21000",
            "--protocol",
            "hs2",
            "--kerberos-service-name",
            "impala",
            "--kerberos-host-fqdn",
            "cli-coordinator.example.com",
            "--timeout-sec",
            "5",
            "--redact",
        ]
    )

    assert args.coordinator == "cli-coordinator.example.com:21000"
    assert args.protocol == "hs2"
    assert args.kerberos_service_name == "impala"
    assert args.kerberos_host_fqdn == "cli-coordinator.example.com"
    assert args.timeout_sec == 5
    assert args.redact is True


def test_local_config_metadata_max_tables_bounds_requested_tables(tmp_path):
    module = load_collector_module()
    config_path = tmp_path / "metadata-config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata_coordinator": "config-coordinator.example.com:21000",
                "metadata_max_tables": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--config",
                str(config_path),
                "--table",
                "db.table_a",
                "--table",
                "db.table_b",
                "--out",
                "ctx",
            ]
        )


def test_local_config_krb5ccname_passed_via_env_not_argv(tmp_path, monkeypatch):
    module = load_collector_module()
    monkeypatch.delenv("KRB5CCNAME", raising=False)
    config_path = tmp_path / "metadata-config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata_coordinator": "config-coordinator.example.com:21000",
                "metadata_krb5ccname": "FILE:/tmp/krb5cc_metadata_config",
            }
        ),
        encoding="utf-8",
    )
    args = module.parse_args(
        ["--config", str(config_path), "--table", "db.table", "--out", str(tmp_path / "ctx")]
    )

    module.apply_kerberos_cache_env(args)

    # The handshake runs in-process now, so the cache reaches libkrb5 through the
    # environment rather than through a subprocess env argument.
    import os

    assert os.environ["KRB5CCNAME"] == "FILE:/tmp/krb5cc_metadata_config"


def test_env_krb5ccname_overrides_local_config_cache(tmp_path, monkeypatch):
    module = load_collector_module()
    monkeypatch.setenv("KRB5CCNAME", "FILE:/tmp/krb5cc_env")
    config_path = tmp_path / "metadata-config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata_coordinator": "config-coordinator.example.com:21000",
                "metadata_krb5ccname": "FILE:/tmp/krb5cc_metadata_config",
            }
        ),
        encoding="utf-8",
    )
    args = module.parse_args(
        ["--config", str(config_path), "--table", "db.table", "--out", str(tmp_path / "ctx")]
    )

    module.apply_kerberos_cache_env(args)

    import os

    assert os.environ["KRB5CCNAME"] == "FILE:/tmp/krb5cc_env"


@pytest.mark.parametrize(
    "coordinator",
    [
        "host.example.com 21000",
        "host.example.com:21000;rm -rf /",
        "http://user:" + "pass" + "word" + "@host.example.com:21000",
        "user:" + "pass" + "word" + "@host.example.com:21000",
        "host.example.com:21000$(whoami)",
        "host.example.com:21000|cat",
        "host.example.com:*",
    ],
)
def test_invalid_coordinator_is_rejected(coordinator):
    module = load_collector_module()

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--table",
                "db.table",
                "--out",
                "ctx",
                "--coordinator",
                coordinator,
            ]
        )


def test_connection_settings_carry_kerberos_and_tls_options():
    from query_doctor.impala.hs2_runner import connect_kwargs

    module = load_collector_module()
    args = module.parse_args(
        [
            "--table",
            "db.table",
            "--out",
            "ctx",
            "--coordinator",
            "coordinator01.example.com:21050",
            "--ssl",
            "--ca-cert",
            "/etc/ssl/certs/impala-ca.pem",
            "--protocol",
            "hs2-http",
            "--kerberos-service-name",
            "hive",
            "--kerberos-host-fqdn",
            "lb.example.com",
            "--timeout-sec",
            "17",
        ]
    )

    settings = module.build_connection_settings(args)

    assert settings.host == "coordinator01.example.com"
    assert settings.port == 21050
    assert settings.use_http_transport is True
    assert settings.http_path == "cliservice"
    assert connect_kwargs(settings) == {
        "host": "coordinator01.example.com",
        "port": 21050,
        "auth_mechanism": "GSSAPI",
        "timeout": 17,
        "use_ssl": True,
        "ca_cert": "/etc/ssl/certs/impala-ca.pem",
        "kerberos_service_name": "hive",
        "krb_host": "lb.example.com",
        "use_http_transport": True,
        "http_path": "cliservice",
    }


def test_binary_hs2_does_not_ask_for_the_http_transport():
    from query_doctor.impala.hs2_runner import connect_kwargs

    module = load_collector_module()
    args = module.parse_args(
        [
            "--table",
            "db.table",
            "--out",
            "ctx",
            "--coordinator",
            "coordinator01.example.com:21050",
        ]
    )

    kwargs = connect_kwargs(module.build_connection_settings(args))

    assert "use_http_transport" not in kwargs
    assert "kerberos_service_name" not in kwargs


def test_successful_collection_writes_redacted_bounded_outputs(tmp_path, capsys):
    module = load_collector_module()

    session = FakeSession(
        {
            "SHOW CREATE TABLE `db`.`table`": text_rows(
                "CREATE TABLE db.table (id BIGINT, token_column STRING)\n"
                "LOCATION 'hdfs://warehouse01.example.invalid:8020/user/alice/warehouse/db.table'\n"
                "COMMENT 'replica hdfs://[2001:db8::44]:8020/warehouse/db.table'\n"
                "TBLPROPERTIES ('external_location'='s3a://raw-lake-prod/warehouse/db.table')\n"
                "TBLPROPERTIES ('access_token'='secret-token')\n"
                "Authorization: Bearer secret-token\n"
                "Cookie: session=secret-cookie"
            ),
            "SHOW TABLE STATS `db`.`table`": table_rows(
                ("#Rows", "Size", "Location"),
                (10, "128B", "hdfs://10.1.2.3:22000/warehouse/db.table"),
            ),
            "SHOW COLUMN STATS `db`.`table`": table_rows(
                ("Column", "Type", "#Distinct Values", "#Nulls"),
                ("id", "BIGINT", 10, 0),
            ),
        }
    )

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=session,
    )

    captured = capsys.readouterr()
    md_text = (tmp_path / "ctx" / "impala_context.md").read_text(encoding="utf-8")
    json_text = (tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8")
    combined = md_text + json_text

    assert rc == 0
    assert session.calls == [
        "SHOW CREATE TABLE `db`.`table`",
        "SHOW TABLE STATS `db`.`table`",
        "SHOW COLUMN STATS `db`.`table`",
    ]
    assert "LOCATION" not in captured.out
    assert "128B" not in captured.out
    assert "db.table" in md_text
    assert "| id | BIGINT | 10 | 0 |" in md_text
    assert "| 10 | 128B |" in md_text
    assert "warehouse01.example.invalid" not in combined
    assert "2001:db8::44" not in combined
    assert "raw-lake-prod" not in combined
    assert "10.1.2.3" not in combined
    assert "alice" not in combined
    assert "secret-token" not in combined
    assert "secret-cookie" not in combined
    assert "abcdefghijkl" not in combined
    assert "host_" in combined
    assert "/user/<user>" in combined
    assert "token_column STRING" in combined


def test_one_session_serves_every_statement_of_a_run(tmp_path):
    module = load_collector_module()
    session = FakeSession(lambda sql: text_rows("CREATE TABLE db.table (id BIGINT)"))

    rc = module.main(
        [
            "--table",
            "db.table_a",
            "--table",
            "db.table_b",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=session,
    )

    assert rc == 0
    assert len(session.calls) == 6
    # The caller owns an injected session; the collector must not close it.
    assert session.closed is False


def test_view_metadata_skips_stats_as_not_applicable(tmp_path):
    module = load_collector_module()
    session = FakeSession(
        {
            "SHOW CREATE TABLE `db`.`view_a`": text_rows(
                "CREATE VIEW db.view_a AS SELECT id FROM db.table_a"
            )
        }
    )

    rc = module.main(
        [
            "--table",
            "db.view_a",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=session,
    )

    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))
    statuses = {item["statement"]: item["status"] for item in payload["results"]}

    assert rc == 0
    assert session.calls == ["SHOW CREATE TABLE `db`.`view_a`"]
    assert statuses == {
        "SHOW CREATE TABLE": "ok",
        "SHOW TABLE STATS": "not_applicable",
        "SHOW COLUMN STATS": "not_applicable",
    }
    assert all(
        item["error"] == "object is a view"
        for item in payload["results"]
        if item["status"] == "not_applicable"
    )


def test_view_stats_not_applicable_error_is_non_fatal(tmp_path):
    module = load_collector_module()

    def respond(sql):
        if sql == "SHOW CREATE TABLE `db`.`view_a`":
            return text_rows("CREATE TABLE db.view_a (id BIGINT)")
        return ImpalaStatementError(
            "AnalysisException: SHOW TABLE STATS not applicable to a view: db.view_a"
        )

    rc = module.main(
        [
            "--table",
            "db.view_a",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=FakeSession(respond),
    )

    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))

    assert rc == 0
    assert [item["status"] for item in payload["results"]] == [
        "ok",
        "not_applicable",
        "not_applicable",
    ]


def test_authorization_error_still_fails(tmp_path):
    module = load_collector_module()
    session = FakeSession(
        lambda sql: ImpalaStatementError("AuthorizationException: user is not authorized")
    )

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=session,
    )

    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))

    assert rc == 1
    assert payload["results"][0]["status"] == "error"
    # The coordinator message has to survive into stderr: the fact parser reads it
    # to tell an authorization failure from a missing object.
    assert "AuthorizationException" in payload["results"][0]["stderr"]


def test_table_not_found_error_still_fails(tmp_path):
    module = load_collector_module()
    session = FakeSession(
        lambda sql: ImpalaStatementError(
            "AnalysisException: Could not resolve table reference: db.missing_table"
        )
    )

    rc = module.main(
        [
            "--table",
            "db.missing_table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=session,
    )

    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))

    assert rc == 1
    assert payload["results"][0]["status"] == "error"


def test_too_large_output_is_recorded_without_raw_body(tmp_path):
    module = load_collector_module()
    session = FakeSession(lambda sql: text_rows("X" * 50))

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
            "--max-output-bytes",
            "8",
        ],
        session=session,
    )

    md_text = (tmp_path / "ctx" / "impala_context.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))

    assert rc == 1
    assert "status: too_large" in md_text
    assert "captured output exceeded max-output-bytes" in md_text
    assert "XXXXXXXX" not in md_text
    assert {item["status"] for item in payload["results"]} == {"too_large"}


def test_stats_rows_render_as_a_delimited_table(tmp_path):
    module = load_collector_module()

    def respond(sql):
        if sql.startswith("SHOW CREATE TABLE"):
            return text_rows("CREATE TABLE db.table (id BIGINT)")
        return table_rows(("#Rows", "Size"), (-1, "0B"), ("Total", "0B"))

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=FakeSession(respond),
    )

    md_text = (tmp_path / "ctx" / "impala_context.md").read_text(encoding="utf-8")

    assert rc == 0
    assert "+---+" in md_text
    assert "| #Rows | Size |" in md_text
    assert "| -1 | 0B |" in md_text
    # The DDL keeps its own line breaks instead of being boxed.
    assert "```sql\nCREATE TABLE " in md_text
    assert "(id BIGINT)" in md_text


def test_padded_ddl_is_compacted_before_size_check(tmp_path):
    module = load_collector_module()
    padded = (
        "CREATE TABLE db.table (                                                  \n"
        "  note STRING COMMENT 'hello   world'                                    \n"
        ")                                                                        \n"
        "\n"
        "\n" + (" " * 120) + "\n"
    )

    def respond(sql):
        if sql.startswith("SHOW CREATE TABLE"):
            return text_rows(padded)
        return table_rows(("#Rows", "Size"), (1, "0B"))

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
            "--max-output-bytes",
            "120",
        ],
        session=FakeSession(respond),
    )

    md_text = (tmp_path / "ctx" / "impala_context.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))
    ddl = next(item for item in payload["results"] if item["statement"] == "SHOW CREATE TABLE")

    assert rc == 0
    assert {item["status"] for item in payload["results"]} == {"ok"}
    assert "status: too_large" not in md_text
    assert "hello   world" in md_text
    assert "                    " not in md_text
    assert "\n\n\n" not in md_text
    assert ddl["stdout_raw_bytes"] > 120
    assert ddl["stdout_bytes"] <= 120
    assert ddl["stdout_normalized"] is True


def test_large_meaningful_output_still_fails_after_compaction(tmp_path):
    module = load_collector_module()
    meaningful = (
        "CREATE TABLE db.table (\n" + "\n".join(f"  c{i} STRING" for i in range(40)) + "\n)"
    )
    session = FakeSession(lambda sql: text_rows(meaningful))

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
            "--max-output-bytes",
            "96",
        ],
        session=session,
    )

    md_text = (tmp_path / "ctx" / "impala_context.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))

    assert rc == 1
    assert {item["status"] for item in payload["results"]} == {"too_large"}
    assert "c39 STRING" not in md_text
    assert all(item["stdout_bytes"] > 96 for item in payload["results"])


def test_timeout_and_error_status_are_recorded_safely(tmp_path, capsys):
    module = load_collector_module()

    def respond(sql):
        if sql == "SHOW CREATE TABLE `db`.`table`":
            return ImpalaStatementTimeoutError("statement timed out after 30s")
        return ImpalaStatementError(
            "password=topsecret token=secret-token "
            "Authorization: Bearer secret-token host=prod-nn.example.com"
        )

    rc = module.main(
        [
            "--table",
            "db.table",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=FakeSession(respond),
    )

    text = (tmp_path / "ctx" / "impala_context.md").read_text(encoding="utf-8")
    captured = capsys.readouterr()
    terminal_output = captured.out + captured.err

    assert rc == 1
    assert "status: timeout" in text
    assert "status: error" in text
    assert "topsecret" not in terminal_output
    assert "secret-token" not in terminal_output
    assert "abcdefghijkl" not in terminal_output
    assert "prod-nn.example.com" not in terminal_output
    assert "topsecret" not in text
    assert "secret-token" not in text
    assert "abcdefghijkl" not in text
    assert "prod-nn.example.com" not in text
    assert "Authorization: Bearer <redacted>" in text


def test_help_works():
    result = subprocess.run(
        [sys.executable, "-m", "query_doctor.cli.collect_impala_context", "--help"],
        cwd=REPO_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "--table" in result.stdout
    assert "--out" in result.stdout


def test_redacted_outputs_keep_each_table_apart(tmp_path):
    from query_doctor.impala.table_metadata_facts import collect_table_metadata_context

    module = load_collector_module()

    def responses(sql):
        rows = 10 if "`small`" in sql else 20
        if sql.startswith("SHOW CREATE TABLE"):
            return text_rows(
                f"CREATE TABLE db.t (id BIGINT)\nSTORED AS {'PARQUET' if rows == 10 else 'ORC'}"
            )
        if sql.startswith("SHOW TABLE STATS"):
            return table_rows(("#Rows", "Size"), (rows, "1KB"))
        return table_rows(
            ("Column", "Type", "#Distinct Values", "#Nulls"), ("id", "BIGINT", rows, 0)
        )

    rc = module.main(
        [
            "--table",
            "db.small",
            "--table",
            "db.large",
            "--out",
            str(tmp_path / "ctx"),
            "--coordinator",
            "coordinator01.example.com:21050",
        ],
        session=FakeSession(responses),
    )

    payload = json.loads((tmp_path / "ctx" / "impala_context.json").read_text(encoding="utf-8"))
    context = collect_table_metadata_context(tmp_path / "ctx")
    tables = {table["table"]: table for table in context["tables"]}

    assert rc == 0
    assert payload["tables"] == ["<db>.<table-1>", "<db>.<table-2>"]
    assert "small" not in json.dumps(payload) and "large" not in json.dumps(payload)
    assert context["tables_requested"] == 2
    assert tables["<db>.<table-1>"]["table_rows"] == 10
    assert tables["<db>.<table-1>"]["file_format"] == "PARQUET"
    assert tables["<db>.<table-2>"]["table_rows"] == 20
    assert tables["<db>.<table-2>"]["file_format"] == "ORC"
