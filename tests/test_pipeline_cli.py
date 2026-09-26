import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from command_test_support import command_args, command_uses_role


REPO_DIR = Path(__file__).resolve().parents[1]

from query_doctor.impala import hs2_runner


@pytest.fixture(autouse=True)
def metadata_driver_present(monkeypatch):
    """These tests exercise metadata wiring, not whether impyla is installed."""
    monkeypatch.setattr(hs2_runner, "driver_available", lambda: True)


METADATA_ENV_VARS = [
    "QD_METADATA_COORDINATOR",
    "QD_METADATA_PROTOCOL",
    "QD_METADATA_AUTH",
    "QD_METADATA_MAX_TABLES",
    "QD_METADATA_MAX_OUTPUT_BYTES",
    "QD_METADATA_REDACT",
    "QD_METADATA_DEFAULT_DB",
    "QD_METADATA_SOURCE_TABLES_JSON",
]


@pytest.fixture(autouse=True)
def clear_metadata_env(monkeypatch):
    for name in METADATA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load_pipeline_module():
    from query_doctor.cli import pipeline

    return pipeline


def make_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "profile_digest.md").write_text("# digest\n", encoding="utf-8")
    return case_dir


def point_pipeline_at_fake_package_repo(module, tmp_path: Path, monkeypatch) -> Path:
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    fake_package_file = fake_repo / "query_doctor" / "cli" / "pipeline.py"
    fake_package_file.parent.mkdir(parents=True)
    fake_package_file.write_text("# fake pipeline\n", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(fake_package_file))
    return fake_repo


def write_facts(
    case_dir: Path,
    tables: Optional[list[str]] = None,
    *,
    default_database: Optional[str] = None,
) -> None:
    lines = ["# Query Doctor Analysis Facts", "", "## SQL Context", ""]
    if default_database:
        lines.append(f"- default_database: `{default_database}`")
    else:
        lines.append("- default_database: not_observed")
    lines.extend(["", "## Referenced Tables", ""])
    if tables:
        lines.extend(f"- `{table}`" for table in tables)
    else:
        lines.append(
            "- not_observed: no referenced table names were parsed from SQL inputs or profile digest."
        )
    lines.append("")
    (case_dir / "analysis_facts.md").write_text("\n".join(lines), encoding="utf-8")


def test_pipeline_default_mode_is_admin():
    module = load_pipeline_module()

    args = module.parse_args(["case-dir"])

    assert args.mode == "admin"


def test_pipeline_default_metadata_mode_is_auto():
    module = load_pipeline_module()

    args = module.parse_args(["case-dir"])

    assert args.metadata_mode == "auto"


def test_pipeline_language_arg_is_normalized_before_validation():
    module = load_pipeline_module()

    args = module.parse_args(["case-dir", "--language", " RU "])

    assert args.language == "ru"


def test_pipeline_language_arg_rejects_unknown_language():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir", "--language", "de"])


def test_pipeline_stop_after_analysis_help_mentions_no_llm(capsys):
    module = load_pipeline_module()

    with pytest.raises(SystemExit) as exc_info:
        module.parse_args(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--stop-after-analysis" in output
    assert "no LLM/Ollama call" in output


def test_pipeline_metadata_options_read_environment_defaults(monkeypatch):
    module = load_pipeline_module()
    monkeypatch.setenv("QD_METADATA_COORDINATOR", "coordinator.example.invalid:21000")
    monkeypatch.setenv("QD_METADATA_PROTOCOL", "hs2")
    monkeypatch.setenv("QD_METADATA_MAX_TABLES", "3")
    monkeypatch.setenv("QD_METADATA_MAX_OUTPUT_BYTES", "65536")

    args = module.parse_args(["case-dir"])

    assert args.metadata_coordinator == "coordinator.example.invalid:21000"
    assert args.metadata_protocol == "hs2"
    assert args.metadata_max_tables == 3
    assert args.metadata_max_output_bytes == 65536


def test_pipeline_rejects_invalid_metadata_max_tables_env(monkeypatch, capsys):
    module = load_pipeline_module()
    monkeypatch.setenv("QD_METADATA_MAX_TABLES", "not-int")

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir"])

    assert "QD_METADATA_MAX_TABLES must be a positive integer" in capsys.readouterr().err


def test_pipeline_rejects_invalid_metadata_max_output_bytes_env(monkeypatch, capsys):
    module = load_pipeline_module()
    monkeypatch.setenv("QD_METADATA_MAX_OUTPUT_BYTES", "not-int")

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir"])

    assert "QD_METADATA_MAX_OUTPUT_BYTES must be a positive integer" in capsys.readouterr().err


def test_pipeline_rejects_nonpositive_metadata_integer_env(monkeypatch, capsys):
    module = load_pipeline_module()
    monkeypatch.setenv("QD_METADATA_MAX_TABLES", "0")

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir"])

    assert "QD_METADATA_MAX_TABLES must be a positive integer" in capsys.readouterr().err


def test_pipeline_cli_metadata_integer_values_override_invalid_env(monkeypatch):
    module = load_pipeline_module()
    monkeypatch.setenv("QD_METADATA_MAX_TABLES", "not-int")
    monkeypatch.setenv("QD_METADATA_MAX_OUTPUT_BYTES", "not-int")

    args = module.parse_args(
        [
            "case-dir",
            "--metadata-max-tables",
            "2",
            "--metadata-max-output-bytes",
            "65536",
        ]
    )

    assert args.metadata_max_tables == 2
    assert args.metadata_max_output_bytes == 65536


def test_pipeline_metadata_mode_off_ignores_invalid_metadata_integer_env(monkeypatch):
    module = load_pipeline_module()
    monkeypatch.setenv("QD_METADATA_MAX_TABLES", "not-int")
    monkeypatch.setenv("QD_METADATA_MAX_OUTPUT_BYTES", "not-int")

    args = module.parse_args(["case-dir", "--metadata-mode", "off"])

    assert args.metadata_max_tables == 5
    assert args.metadata_max_output_bytes == 262_144


def test_pipeline_accepts_admin_mode():
    module = load_pipeline_module()

    args = module.parse_args(["case-dir", "--mode", "admin"])

    assert args.mode == "admin"


def test_pipeline_accepts_user_mode():
    module = load_pipeline_module()

    args = module.parse_args(["case-dir", "--mode", "user"])

    assert args.mode == "user"


def test_pipeline_run_cmd_uses_timeout_and_safe_timeout_exit(monkeypatch, tmp_path, capsys):
    module = load_pipeline_module()
    calls = []
    cmd = [sys.executable, "-m", "query_doctor.cli.analyze_profile", str(tmp_path)]

    def fake_subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

    with pytest.raises(SystemExit) as exc:
        module.run_cmd(cmd, tmp_path, timeout_sec=7)

    assert exc.value.code == module.SUBPROCESS_TIMEOUT_EXIT_CODE
    assert calls[0][1]["timeout"] == 7
    stderr = capsys.readouterr().err
    assert "timed out after 7s" in stderr
    assert "query_doctor.cli.analyze_profile" not in stderr


def test_pipeline_report_command_carries_api_key_env_name_not_the_token(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    token = "sk-pipeline-must-not-log-this"
    monkeypatch.setenv("QD_REPORT_LLM_API_KEY", token)
    commands: list[list[str]] = []

    class CompletedProcess:
        returncode = 0

    def fake_subprocess_run(command, **kwargs):
        parts = [str(part) for part in command]
        commands.append(parts)
        if command_uses_role(parts, "analyze"):
            write_facts(case_dir, ["db.table_a"])
        return CompletedProcess()

    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

    assert module.main([str(case_dir), "--metadata-mode", "off"]) == 0

    report_cmd = next(cmd for cmd in commands if command_uses_role(cmd, "report"))
    report_args = command_args(report_cmd, "report")
    assert report_args[report_args.index("--llm-api-key-env") + 1] == "QD_REPORT_LLM_API_KEY"
    assert all(token not in part for cmd in commands for part in cmd)
    assert token not in capsys.readouterr().out


def test_pipeline_rejects_invalid_mode():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir", "--mode", "invalid"])


def test_pipeline_passes_selected_mode_and_language_to_reporter(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            (case_dir / "analysis_facts.md").write_text("# facts\n", encoding="utf-8")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)

    result = module.main([str(case_dir), "--mode", "user", "--language", "RU"])

    assert result == 0
    report_cmds = [cmd for cmd in commands if command_uses_role(cmd, "report")]
    assert len(report_cmds) == 1
    report_cmd = report_cmds[0]
    mode_index = report_cmd.index("--mode")
    assert report_cmd[mode_index + 1] == "user"
    language_index = report_cmd.index("--language")
    assert report_cmd[language_index + 1] == "ru"
    validation_index = report_cmd.index("--validation-mode")
    assert report_cmd[validation_index + 1] == "strict"


def test_pipeline_skip_report_does_not_invoke_reporter(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            (case_dir / "analysis_facts.md").write_text("# facts\n", encoding="utf-8")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)

    result = module.main([str(case_dir), "--mode", "user", "--skip-report"])

    assert result == 0
    assert any(command_uses_role(cmd, "analyze") for cmd in commands)
    assert not any(command_uses_role(cmd, "report") for cmd in commands)


def test_pipeline_stop_after_analysis_with_metadata_off_skips_report(tmp_path, monkeypatch, capsys):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata mode off must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main([str(case_dir), "--metadata-mode", "off", "--stop-after-analysis"])

    assert result == 0
    output = capsys.readouterr().out
    assert "stop-after-analysis requested; report generation skipped" in output
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert not any(command_uses_role(cmd, "report") for cmd in commands)
    assert (case_dir / "analysis_facts.md").exists()
    assert not (case_dir / "diagnosis.md").exists()
    assert not (case_dir / "diagnosis.partial.md").exists()


def test_pipeline_stop_after_analysis_auto_without_metadata_config_skips_report(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("auto without metadata config must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main([str(case_dir), "--stop-after-analysis"])

    assert result == 0
    output = capsys.readouterr().out
    assert "metadata collection: not configured; continuing without metadata" in output
    assert "stop-after-analysis requested; report generation skipped" in output
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert not any(command_uses_role(cmd, "report") for cmd in commands)
    assert (case_dir / "analysis_facts.md").exists()
    assert not (case_dir / "diagnosis.md").exists()
    assert not (case_dir / "diagnosis.partial.md").exists()


def test_pipeline_stop_after_analysis_auto_with_metadata_config_collects_and_skips_report(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    events: list[str] = []
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            events.append("analyzer")
            write_facts(case_dir, ["db.table_a"])
            return
        if command_uses_role(cmd, "report"):
            events.append("report")
            return
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_metadata(cmd, cwd):
        events.append("metadata")
        collector_commands.append(cmd)
        (case_dir / "impala_context.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--stop-after-analysis",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "facts refreshed with Impala metadata" in output
    assert "stop-after-analysis requested; report generation skipped" in output
    assert events == ["analyzer", "metadata", "analyzer"]
    assert collector_commands[0].count("--table") == 1
    assert "db.table_a" in collector_commands[0]
    assert not (case_dir / "diagnosis.md").exists()
    assert not (case_dir / "diagnosis.partial.md").exists()


def test_pipeline_metadata_failure_policy_fail_is_default(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    events: list[str] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            events.append("analyzer")
            write_facts(case_dir, ["db.table_a"])
            return
        if command_uses_role(cmd, "report"):
            events.append("report")
            return
        raise AssertionError(f"unexpected command: {cmd}")

    def fail_metadata(cmd, cwd):
        events.append("metadata")
        (case_dir / "impala_context.json").write_text(
            json.dumps({"tables": ["db.table_a"], "results": [{"status": "error"}]}),
            encoding="utf-8",
        )
        raise SystemExit(1)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    with pytest.raises(SystemExit) as exc_info:
        module.main(
            [
                str(case_dir),
                "--metadata-coordinator",
                "coordinator.example.invalid:21000",
                "--stop-after-analysis",
            ]
        )

    assert exc_info.value.code == 1
    assert events == ["analyzer", "metadata"]
    assert not (case_dir / "diagnosis.md").exists()
    assert not (case_dir / "diagnosis.partial.md").exists()


def test_pipeline_metadata_failure_policy_continue_stops_after_analysis_without_report(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    events: list[str] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            events.append("analyzer")
            write_facts(case_dir, ["db.table_a"])
            return
        if command_uses_role(cmd, "report"):
            events.append("report")
            return
        raise AssertionError(f"unexpected command: {cmd}")

    def fail_metadata(cmd, cwd):
        events.append("metadata")
        (case_dir / "impala_context.json").write_text(
            json.dumps({"tables": ["db.table_a"], "results": [{"status": "error"}]}),
            encoding="utf-8",
        )
        raise SystemExit(1)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main(
        [
            str(case_dir),
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--stop-after-analysis",
            "--metadata-failure-policy",
            "continue",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "metadata collection failed; continuing analyzer-only" in output
    assert (
        "partial metadata outputs are left on disk but not promoted into analyzer facts" in output
    )
    assert events == ["analyzer", "metadata"]
    assert (case_dir / "impala_context.json").exists()
    assert not (case_dir / "diagnosis.md").exists()
    assert not (case_dir / "diagnosis.partial.md").exists()


def test_pipeline_metadata_failure_policy_continue_requires_stop_after_analysis(capsys):
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir", "--metadata-failure-policy", "continue"])

    assert (
        "--metadata-failure-policy continue requires --stop-after-analysis"
        in capsys.readouterr().err
    )


def test_pipeline_metadata_auth_preflight_blocks_default_collector(tmp_path, monkeypatch, capsys):
    module = load_pipeline_module()
    from query_doctor.impala.kerberos_preflight import KerberosTicketCheck

    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["example_warehouse.orders"])

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(
        module,
        "check_kerberos_ticket_cache",
        lambda env: KerberosTicketCheck(
            False,
            "Kerberos ticket cache is missing or expired; refresh it before metadata collection.",
        ),
    )

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
        ]
    )

    assert result == 2
    assert any(command_uses_role(cmd, "analyze") for cmd in commands)
    error_output = capsys.readouterr().err
    assert "Kerberos ticket cache is missing or expired" in error_output
    assert str(tmp_path) not in error_output


def test_pipeline_view_metadata_not_applicable_does_not_fail_stop_after_analysis(
    tmp_path, monkeypatch
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    events: list[str] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            events.append("analyzer")
            write_facts(case_dir, ["db.view_a"])
            return
        if command_uses_role(cmd, "report"):
            events.append("report")
            return
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_metadata(cmd, cwd):
        events.append("metadata")
        (case_dir / "impala_context.json").write_text(
            json.dumps(
                {
                    "tables": ["db.view_a"],
                    "read_only_statements_only": True,
                    "results": [
                        {
                            "table": "db.view_a",
                            "statement": "SHOW CREATE TABLE",
                            "status": "ok",
                            "stdout": "CREATE VIEW db.view_a AS SELECT id FROM db.table_a\n",
                        },
                        {
                            "table": "db.view_a",
                            "statement": "SHOW TABLE STATS",
                            "status": "not_applicable",
                            "error": "object is a view",
                        },
                        {
                            "table": "db.view_a",
                            "statement": "SHOW COLUMN STATS",
                            "status": "not_applicable",
                            "error": "object is a view",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--stop-after-analysis",
        ]
    )

    assert result == 0
    assert events == ["analyzer", "metadata", "analyzer"]
    assert not (case_dir / "diagnosis.md").exists()


def test_pipeline_stop_after_analysis_metadata_mode_on_missing_config_skips_report(capsys):
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir", "--metadata-mode", "on", "--stop-after-analysis"])

    assert "--metadata-coordinator is required with --metadata-mode on" in capsys.readouterr().err


def test_pipeline_stop_after_analysis_does_not_require_report_script(tmp_path, monkeypatch):
    module = load_pipeline_module()
    point_pipeline_at_fake_package_repo(module, tmp_path, monkeypatch)
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata mode off must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main([str(case_dir), "--metadata-mode", "off", "--stop-after-analysis"])

    assert result == 0
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert not any(command_uses_role(cmd, "report") for cmd in commands)


def test_pipeline_package_backend_does_not_require_root_report_script(tmp_path, monkeypatch):
    module = load_pipeline_module()
    fake_repo = tmp_path / "fake-repo"
    fake_package_file = fake_repo / "query_doctor" / "cli" / "pipeline.py"
    fake_package_file.parent.mkdir(parents=True)
    fake_package_file.write_text("# fake pipeline\n", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(fake_package_file))
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)

    result = module.main([str(case_dir), "--metadata-mode", "off"])

    assert result == 0
    analyze_cmds = [cmd for cmd in commands if command_uses_role(cmd, "analyze")]
    report_cmds = [cmd for cmd in commands if command_uses_role(cmd, "report")]
    assert len(analyze_cmds) == 1
    assert len(report_cmds) == 1
    assert analyze_cmds[0][1:3] == ["-m", "query_doctor.cli.analyze_profile"]
    assert report_cmds[0][1:3] == ["-m", "query_doctor.cli.report"]
    assert command_args(report_cmds[0], "report")[0] == str(case_dir)


def test_pipeline_auto_without_metadata_config_does_not_invoke_collector(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata collector must be explicit opt-in")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main([str(case_dir), "--mode", "user"])

    assert result == 0
    output = capsys.readouterr().out
    assert "metadata collection: not configured; continuing without metadata" in output
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert sum(command_uses_role(cmd, "report") for cmd in commands) == 1


def test_pipeline_auto_with_metadata_config_collects_referenced_tables(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    events: list[str] = []
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            events.append("analyzer")
            write_facts(case_dir, ["db.table_a"])
            return
        if command_uses_role(cmd, "report"):
            events.append("report")
            return
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_metadata(cmd, cwd):
        events.append("metadata")
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--metadata-kerberos-service-name",
            "hive",
        ]
    )

    assert result == 0
    assert events == ["analyzer", "metadata", "analyzer", "report"]
    assert collector_commands[0].count("--table") == 1
    assert "db.table_a" in collector_commands[0]


def test_pipeline_metadata_mode_off_does_not_collect_even_when_configured(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata mode off must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main(
        [
            str(case_dir),
            "--metadata-mode",
            "off",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--metadata-kerberos-service-name",
            "hive",
        ]
    )

    assert result == 0
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert sum(command_uses_role(cmd, "report") for cmd in commands) == 1


def test_pipeline_auto_fails_on_invalid_metadata_configuration(tmp_path, monkeypatch, capsys):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("invalid metadata config must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main(
        [
            str(case_dir),
            "--metadata-coordinator",
            "host:21000; rm -rf /",
        ]
    )

    assert result == 2
    error_output = capsys.readouterr().err
    assert "metadata configuration is invalid" in error_output


def test_pipeline_collects_metadata_for_referenced_tables_and_reruns_analyzer(
    tmp_path, monkeypatch
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    events: list[str] = []
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            events.append("analyzer")
            write_facts(case_dir, ["db.table_a", "db.table_b"])
            return
        if command_uses_role(cmd, "report"):
            events.append("report")
            return
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_metadata(cmd, cwd):
        events.append("metadata")
        collector_commands.append(cmd)
        (case_dir / "impala_context.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--mode",
            "user",
        ]
    )

    assert result == 0
    assert events == ["analyzer", "metadata", "analyzer", "report"]
    assert len(collector_commands) == 1
    collector_cmd = collector_commands[0]
    assert isinstance(collector_cmd, list)
    assert collector_cmd.count("--table") == 2
    assert collector_cmd[collector_cmd.index("--table") + 1] == "db.table_a"
    assert (
        collector_cmd[collector_cmd.index("--table", collector_cmd.index("--table") + 1) + 1]
        == "db.table_b"
    )
    assert "--out" in collector_cmd
    assert collector_cmd[collector_cmd.index("--out") + 1] == str(case_dir)
    assert "--protocol" in collector_cmd
    assert collector_cmd[collector_cmd.index("--protocol") + 1] == "hs2"


def test_pipeline_metadata_collection_respects_max_tables(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a", "db.table_b", "db.table_c"])

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--metadata-max-tables",
            "2",
        ]
    )

    assert result == 0
    collector_cmd = collector_commands[0]
    assert collector_cmd.count("--table") == 2
    assert "db.table_a" in collector_cmd
    assert "db.table_b" in collector_cmd
    assert "db.table_c" not in collector_cmd


def test_pipeline_metadata_dry_run_plans_without_collector_or_report(tmp_path, monkeypatch, capsys):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a", "db.table_b"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata dry-run must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main(
        [
            str(case_dir),
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--metadata-dry-run",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "metadata dry-run requested" in output
    assert "collect: db.table_a" in output
    assert "collect: db.table_b" in output
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert not any(command_uses_role(cmd, "report") for cmd in commands)


def test_pipeline_metadata_mode_dry_run_plans_without_coordinator(tmp_path, monkeypatch, capsys):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata dry-run must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main([str(case_dir), "--metadata-mode", "dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert "metadata dry-run requested" in output
    assert "collect: db.table_a" in output
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert not any(command_uses_role(cmd, "report") for cmd in commands)


def test_pipeline_metadata_dry_run_with_stop_after_analysis_keeps_dry_run_behavior(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        commands.append(cmd)
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table_a"])

    def fail_metadata(*args, **kwargs):
        raise AssertionError("metadata dry-run must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main([str(case_dir), "--metadata-mode", "dry-run", "--stop-after-analysis"])

    assert result == 0
    output = capsys.readouterr().out
    assert "metadata dry-run requested" in output
    assert (
        "metadata dry-run complete; analyzer/report were not rerun after metadata collection"
        in output
    )
    assert "stop-after-analysis requested" not in output
    assert sum(command_uses_role(cmd, "analyze") for cmd in commands) == 1
    assert not any(command_uses_role(cmd, "report") for cmd in commands)
    assert not (case_dir / "impala_context.json").exists()
    assert not (case_dir / "diagnosis.md").exists()


def test_pipeline_metadata_mode_on_requires_coordinator():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir", "--metadata-mode", "on"])


def test_pipeline_collect_impala_metadata_legacy_alias_requires_coordinator():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(["case-dir", "--collect-impala-metadata"])


def test_pipeline_metadata_rejects_unsupported_auth():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "case-dir",
                "--collect-impala-metadata",
                "--metadata-coordinator",
                "coordinator.example.invalid:21000",
                "--metadata-auth",
                "ldap",
            ]
        )


def test_pipeline_metadata_rejects_unsafe_coordinator():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "case-dir",
                "--collect-impala-metadata",
                "--metadata-coordinator",
                "host:21000; rm -rf /",
            ]
        )


def test_pipeline_metadata_rejects_unsafe_default_database():
    module = load_pipeline_module()

    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "case-dir",
                "--metadata-mode",
                "dry-run",
                "--metadata-default-db",
                "db;DROP",
            ]
        )


def test_pipeline_metadata_skips_malformed_and_duplicate_referenced_tables(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(
                case_dir,
                [
                    "db.good_table",
                    "db.good_table",
                    "unqualified",
                    "catalog.db.table",
                    "db.bad;DROP",
                ],
            )

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--metadata-kerberos-service-name",
            "hive",
        ]
    )

    assert result == 0
    collector_cmd = collector_commands[0]
    assert collector_cmd.count("--table") == 1
    assert collector_cmd[collector_cmd.index("--table") + 1] == "db.good_table"
    assert collector_cmd[collector_cmd.index("--kerberos-service-name") + 1] == "hive"


def test_pipeline_metadata_skips_generic_placeholder_referenced_tables(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(
                case_dir,
                [
                    "db.table",
                    "table",
                    "<db>.<table>",
                    "other_db.table",
                    "db.good_table",
                ],
                default_database="default_db",
            )

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
        ]
    )

    assert result == 0
    collector_cmd = collector_commands[0]
    assert collector_cmd.count("--table") == 1
    assert collector_cmd[collector_cmd.index("--table") + 1] == "db.good_table"
    assert "default_db.table" not in collector_cmd
    assert "db.table" not in collector_cmd
    assert "other_db.table" not in collector_cmd


def test_pipeline_metadata_skips_generic_placeholders_without_running_collector(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table", "table"], default_database="default_db")

    def fail_metadata(*args, **kwargs):
        raise AssertionError("placeholder-only metadata plan must not execute collector")

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fail_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
        ]
    )

    assert result == 0
    assert "no valid referenced tables found for metadata collection" in capsys.readouterr().out


def test_pipeline_metadata_uses_internal_source_tables_without_echoing_them(
    tmp_path, monkeypatch, capsys
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []
    monkeypatch.setenv("QD_METADATA_SOURCE_TABLES_JSON", json.dumps(["private_db.private_table"]))

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["db.table"], default_database="default_db")

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
        ]
    )

    assert result == 0
    collector_cmd = collector_commands[0]
    assert collector_cmd[collector_cmd.index("--table") + 1] == "private_db.private_table"
    output = capsys.readouterr().out
    assert "private_db.private_table" not in output
    assert "collect: <db>.<table>" in output


def test_pipeline_metadata_numbers_source_tables_like_the_redacted_sql(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []
    monkeypatch.setenv(
        "QD_METADATA_SOURCE_TABLES_JSON",
        json.dumps(["b_db.orders", "a_db.orders", "b_db.orders", "lookup"]),
    )

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            # Facts parsed from redacted SQL see the labels without brackets.
            write_facts(case_dir, ["db.table_2", "db.table"], default_database="default_db")

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
        ]
    )

    assert result == 0
    cmd = collector_commands[0]
    tables = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--table"]
    numbers = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--table-number"]
    assert tables == ["b_db.orders", "a_db.orders", "default_db.lookup"]
    assert numbers == ["1", "2", "4"]


def test_pipeline_metadata_qualifies_unqualified_tables_with_default_database_from_facts(
    tmp_path, monkeypatch
):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["click_event"], default_database="te_ruby_agg")

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
        ]
    )

    assert result == 0
    collector_cmd = collector_commands[0]
    assert collector_cmd.count("--table") == 1
    assert collector_cmd[collector_cmd.index("--table") + 1] == "te_ruby_agg.click_event"


def test_pipeline_metadata_explicit_default_database_overrides_facts(tmp_path, monkeypatch):
    module = load_pipeline_module()
    case_dir = make_case(tmp_path)
    collector_commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd):
        if command_uses_role(cmd, "analyze"):
            write_facts(case_dir, ["click_event"], default_database="profile_db")

    def fake_metadata(cmd, cwd):
        collector_commands.append(cmd)

    monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(module, "run_metadata_cmd", fake_metadata)

    result = module.main(
        [
            str(case_dir),
            "--skip-report",
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "coordinator.example.invalid:21000",
            "--metadata-default-db",
            "example_explicit_db",
        ]
    )

    assert result == 0
    collector_cmd = collector_commands[0]
    assert collector_cmd.count("--table") == 1
    assert collector_cmd[collector_cmd.index("--table") + 1] == "example_explicit_db.click_event"


def test_pipeline_metadata_dry_run_uses_profile_digest_embedded_sql(tmp_path, capsys):
    module = load_pipeline_module()
    case_dir = tmp_path / "profile-sql-case"
    case_dir.mkdir()
    details = """
Query (id=abc:def)
  Summary
    Sql Statement: SELECT a.id, b.name FROM smoke_db.table_a a JOIN smoke_db.table_b b ON a.id = b.id
    Coordinator: host_01:22000
"""
    (case_dir / "profile_digest.md").write_text(json.dumps({"details": details}), encoding="utf-8")

    result = module.main(
        [
            str(case_dir),
            "--collect-impala-metadata",
            "--metadata-coordinator",
            "host_01:21000",
            "--metadata-protocol",
            "hs2",
            "--metadata-max-tables",
            "5",
            "--metadata-dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "collect: smoke_db.table_a" in output
    assert "collect: smoke_db.table_b" in output
    assert "b.name" not in output
    assert "metadata dry-run requested" in output
    assert "query_doctor_report.py" not in output
    assert not (case_dir / "impala_context.json").exists()
    assert not (case_dir / "diagnosis.md").exists()
