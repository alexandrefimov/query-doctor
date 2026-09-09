import hashlib
import html
import importlib
import io
import json
import inspect
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urldefrag, urlparse
from zoneinfo import ZoneInfo

import pytest

from command_test_support import command_args, command_uses_role
from web_server_test_support import REPO_DIR, load_web_module, write_complete_collected_case
from query_doctor.optimizer.defaults import BUILTIN_OPTIMIZER_MODEL
from query_doctor.web.manual_profile_inbox import manual_profile_file_for_query
from query_doctor.web.routes import route_get_request
from query_doctor.web.ui import layout
from query_doctor.impala import hs2_runner


@pytest.fixture(autouse=True)
def metadata_driver_present(monkeypatch):
    """These tests exercise metadata wiring, not whether impyla is installed."""
    monkeypatch.setattr(hs2_runner, "driver_available", lambda: True)


def compact_css(css: str) -> str:
    return "".join(css.split())


def assert_css_contains(styles: str, snippet: str) -> None:
    assert compact_css(snippet) in compact_css(styles)


def job_id_from_location(location: str) -> str:
    url, _fragment = urldefrag(location)
    path = url.split("?", 1)[0]
    return path.rsplit("/", 1)[1]


def fragment_from_location(location: str) -> str:
    _url, fragment = urldefrag(location)
    return fragment


def html_between(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def complete_python_report_command(cmd: list[object]) -> subprocess.CompletedProcess[str]:
    args = command_args(cmd, "report")
    case_dir = Path(args[0])
    assert "--no-llm" in args
    assert args[args.index("--out") + 1] == "diagnosis_python.md"
    assert args[args.index("--validation-mode") + 1] == "strict"
    (case_dir / "diagnosis_python.md").write_text(
        "# Query Doctor Report\n\nDeterministic Python report.\n",
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def optimizer_recipe_facts():
    return """
# Query Doctor deterministic analysis facts

## Action Cards

### Card 1: Severe cardinality underestimation before high-cost operator

Evidence:
- operator: 03:HASH JOIN
- actual rows: 10.00K
- estimated rows: 1
""".strip()


def post_union_aggregate_source_sql():
    return """
WITH src AS (
    SELECT category, user_id, amount, 1 AS n_transactions
    FROM db.a
    WHERE ds = 1
    UNION ALL
    SELECT category, user_id, amount, 1 AS n_transactions
    FROM db.b
    WHERE ds = 1
), purchases AS (
    SELECT category,
           CAST(SUM(n_transactions) AS BIGINT) AS n_transactions,
           CAST(SUM(amount * n_transactions) AS BIGINT) AS spends
    FROM src
    GROUP BY category
)
SELECT category, n_transactions, spends FROM purchases
""".strip()


def post_union_aggregate_draft_sql(where_value: int = 1):
    return f"""
WITH src AS (
    SELECT category,
           CAST(SUM(1) AS BIGINT) AS n_transactions,
           CAST(SUM(amount * 1) AS BIGINT) AS spends
    FROM db.a
    WHERE ds = {where_value}
    GROUP BY category
    UNION ALL
    SELECT category,
           CAST(SUM(1) AS BIGINT) AS n_transactions,
           CAST(SUM(amount * 1) AS BIGINT) AS spends
    FROM db.b
    WHERE ds = 1
    GROUP BY category
), purchases AS (
    SELECT category,
           CAST(SUM(n_transactions) AS BIGINT) AS n_transactions,
           CAST(SUM(spends) AS BIGINT) AS spends
    FROM src
    GROUP BY category
)
SELECT category, n_transactions, spends FROM purchases;
""".strip()


def owner_raw_source_sql() -> str:
    return """
SELECT id, password = 'supersecret' AS masked_value
FROM qdleak_db_20260611.qdleak_table_20260611
WHERE scratch_path = '/tmp/owner-raw-private'
""".strip()


def write_owner_raw_source_summary(
    tmp_path: Path,
    *,
    user: str = "analyst",
    source_sql=None,
    source_locators=None,
) -> Path:
    case_dir = tmp_path / "case-001"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text(
        "# Query Doctor deterministic analysis facts\n", encoding="utf-8"
    )
    (case_dir / "original_query.sql").write_text(
        source_sql or owner_raw_source_sql(),
        encoding="utf-8",
    )
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "cases": [
                    {
                        "case_index": 1,
                        "case_dir": "case-001",
                        "query_id": "aaaabbbbccccdddd:1111222233334444",
                        "user": user,
                        "score": 80,
                        "duration_sec": 120,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "table_stats_status": "not_checked",
                        "score_reasons": ["cardinality anomaly detected"],
                        "source_locators": source_locators
                        or {
                            "query_optimization": [
                                {
                                    "id": "sql_final_select_filter",
                                    "coordinate": "line 3",
                                    "detail": "predicate near final SELECT",
                                }
                            ]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return summary


def owner_raw_web_settings(module, tmp_path: Path, summary: Path, *, viewer: str = "analyst"):
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    return module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        viewer_identity=local_first_viewer_identity((viewer,)),
    )


class MultiValueHeaders:
    def __init__(self, values):
        self.values = values

    def get(self, name):
        values = self.values.get(name)
        if not values:
            return None
        return values[0]

    def get_all(self, name):
        return self.values.get(name)


def make_handler_get_request(handler, path: str, headers):
    request = handler.__new__(handler)
    captured: dict[str, object] = {"headers": []}
    output = io.BytesIO()
    request.path = path
    request.headers = headers
    request.wfile = output
    request.send_response = lambda status: captured.__setitem__("status", status)
    request.send_header = lambda name, value: captured["headers"].append((name, value))
    request.end_headers = lambda: None
    request.do_GET()
    return captured, output.getvalue().decode("utf-8")


def handler_get_html(handler, path: str) -> tuple[int, str]:
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = path
    request.write_html = write_html
    request.do_GET()
    return int(captured["status"]), str(captured["body"])


def write_large_recent_summary(tmp_path: Path, *, rows: int = 601) -> Path:
    cases = [
        {
            "case_index": index,
            "query_id": f"large-query-{index:04d}",
            "score": 10 + index % 90,
            "score_severity": "suspicious",
            "duration_sec": float(index),
            "collection_status": "ok",
            "analysis_status": "ok",
            "metadata_status": "skipped",
            "report_generated": False,
            "report_validation_status": "not_run",
            "cardinality_anomaly_count": 1,
            "memory_anomaly_count": 0,
            "backend_data_skew": False,
            "host_tail_candidate_count": 0,
            "score_reasons": ["cardinality anomaly detected"],
        }
        for index in range(1, rows + 1)
    ]
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": rows,
                "summaries_inspected": rows,
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    return summary


def owner_raw_running_web_settings(module, tmp_path: Path, summary: Path, **overrides):
    values = {
        "config": tmp_path / "cm-config.json",
        "source_visibility": "owner_raw",
        "viewer_identity_header": "X-QD-Viewer",
    }
    values.update(overrides)
    settings = module.WebSettings(**values)
    store = module.WebJobStore()
    store.set_latest_running_summary(summary)
    return settings, store


def test_web_parse_args_defaults_to_localhost():
    module = load_web_module()

    args = module.parse_args([])

    assert args.config is None
    assert args.host is None
    assert args.port is None
    assert args.allow_nonlocal_web_bind is False
    assert args.viewer_identity_header is None
    assert args.disable_owner_raw_source is False
    assert args.optimizer_model is None
    assert args.batch_summary is None
    assert args.public_demo is False
    assert args.metadata_coordinator is None
    assert args.metadata_protocol is None


def test_web_parse_args_accepts_viewer_identity_header():
    module = load_web_module()

    args = module.parse_args(["--viewer-identity-header", "X-QD-Viewer"])

    assert args.viewer_identity_header == "X-QD-Viewer"


def test_web_parse_args_accepts_owner_raw_source_kill_switch():
    module = load_web_module()

    args = module.parse_args(["--disable-owner-raw-source"])

    assert args.disable_owner_raw_source is True


SERVER_REEXPORTS = [
    (
        "query_doctor.web.models",
        ("WebSettings", "WebError", "BatchRunConfig", "batch_progress_path"),
    ),
    (
        "query_doctor.web.jobs",
        ("WebJobStore", "stages_for_job_kind", "render_job_status_json", "BATCH_STAGES"),
    ),
    (
        "query_doctor.web.config",
        (
            "build_web_settings",
            "validate_bind_host",
            "validate_owner_raw_nonlocal_bind",
            "validate_public_demo_settings",
            "metadata_configured",
        ),
    ),
    ("query_doctor.web.server_args", ("parse_args",)),
    (
        "query_doctor.web.app",
        ("MAX_WEB_POST_BODY_BYTES", "AnalysisFunc", "make_handler"),
    ),
    ("query_doctor.cli.web", ("main",)),
    (
        "query_doctor.web.command_builders",
        (
            "BATCH_REPORT_NAME",
            "OPTIMIZED_QUERY_NAME",
            "build_report_command",
            "build_optimized_query_command",
            "display_float",
            "build_analyzer_command",
        ),
    ),
    (
        "query_doctor.web.case_files",
        (
            "build_query_id_summary_case",
            "read_case_metadata",
            "parse_facts_summary",
            "parse_output_case_dir",
            "expected_case_dir_for_query",
        ),
    ),
    (
        "query_doctor.web.corpus_summary",
        (
            "build_manual_profile_corpus_summary",
            "prepare_corpus_summary_runtime",
        ),
    ),
    (
        "query_doctor.web.subprocesses",
        (
            "WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES",
            "run_subprocess",
            "effective_subprocess_env",
            "preflight_web_metadata_batch",
            "subprocess_failure_message",
            "has_cm_credentials",
        ),
    ),
    (
        "query_doctor.web.form_helpers",
        (
            "first_form_value",
            "form_flag_enabled",
            "parse_positive_form_int",
            "parse_non_negative_form_int",
            "parse_non_negative_form_float",
            "parse_optional_non_negative_form_float",
        ),
    ),
    (
        "query_doctor.web.batch_scan",
        (
            "RECENT_SCAN_TIMEZONE",
            "BATCH_METADATA_TOP_LIMIT_MAX",
            "default_recent_scan_bucket",
            "allowed_recent_scan_dates",
            "parse_recent_scan_window",
            "parse_batch_run_config",
            "parse_running_run_config",
            "form_values_from_form",
            "form_values_from_config",
            "validate_batch_config_for_settings",
            "build_batch_command",
        ),
    ),
    ("query_doctor.web.batch_jobs", ("start_batch_job", "start_running_job", "run_batch_job")),
    (
        "query_doctor.web.optimizer_workflow",
        ("run_optimizer_analysis", "collect_optimizer_metadata", "read_optimizer_metadata_context"),
    ),
    (
        "query_doctor.web.request_handlers",
        (
            "sanitize_for_display",
            "handle_analyze_request",
            "handle_optimizer_request",
            "start_analyze_job",
            "run_analysis_job",
        ),
    ),
    (
        "query_doctor.web.specific_query_actions",
        (
            "start_specific_query_report_job",
            "start_specific_query_optimized_query_job",
            "start_specific_query_llm_actions_job",
            "handle_specific_query_external_rewrite_validation",
        ),
    ),
    (
        "query_doctor.web.optimizer_validation",
        (
            "EXTERNAL_REWRITE_SQL_FIELD",
            "optimizer_manual_guidance",
            "optimizer_manual_rewrite_allowed",
            "validate_external_optimizer_rewrite",
            "safe_optimizer_validation_categories",
        ),
    ),
    (
        "query_doctor.web.trusted_artifacts",
        (
            "resolve_batch_case_dir",
            "resolve_batch_case_report_dir",
            "load_batch_case_report_state",
            "load_specific_query_report_state",
            "load_optimized_query_state",
            "batch_case_validated_report_exists",
            "optimized_query_validated_exists",
            "write_batch_case_report_validation_marker",
        ),
    ),
    (
        "query_doctor.web.details_facts",
        (
            "MAX_METADATA_FACTS_BYTES",
            "CM_METRIC_SIGNAL_LABELS",
            "load_specific_query_metadata_facts",
            "load_specific_query_data_movement_facts",
            "load_specific_query_runtime_metrics_facts",
            "load_batch_case_metadata_facts",
            "load_batch_case_cluster_runtime_context_facts",
            "load_batch_case_data_movement_facts",
            "load_batch_case_runtime_metrics_facts",
            "load_case_analysis_data_movement_facts",
            "load_case_analysis_runtime_metrics_facts",
            "parse_data_movement_facts",
            "parse_table_metadata_context_facts",
            "parse_cm_metrics_facts",
            "parse_runtime_metrics_facts",
            "parse_cluster_runtime_context_facts",
            "parse_runtime_diagnosis_facts",
            "metadata_statement_counts",
        ),
    ),
    (
        "query_doctor.web.case_detail_context",
        (
            "batch_page_settings",
            "running_page_settings",
            "running_detail_kwargs",
            "resolve_case_detail_settings",
            "resolve_running_case_detail_settings",
            "load_batch_summary",
            "case_with_detail_ranks",
            "find_batch_case",
            "case_allows_llm_report",
        ),
    ),
    (
        "query_doctor.web.case_detail_state",
        (
            "build_batch_case_detail_action_context",
            "build_batch_case_detail_render_context",
            "server_owned_case_required_report_state",
        ),
    ),
    (
        "query_doctor.web.specific_query_state",
        (
            "build_specific_query_detail_action_context",
            "build_specific_query_detail_render_context",
        ),
    ),
    (
        "query_doctor.web.specific_query_pages",
        (
            "render_specific_query_detail_for_request",
            "render_specific_query_report_for_request",
            "render_specific_query_report_page",
        ),
    ),
    (
        "query_doctor.web.batch_case_pages",
        ("render_batch_case_detail_for_request",),
    ),
    (
        "query_doctor.web.batch_case_actions",
        (
            "start_batch_case_report_job",
            "start_batch_case_optimized_query_job",
            "handle_batch_case_external_rewrite_validation",
            "start_batch_case_llm_actions_job",
        ),
    ),
    (
        "query_doctor.web.job_workers",
        (
            "REPORT_VALIDATION_EXIT_CODE",
            "run_batch_case_report_job",
            "run_specific_query_report_job",
            "run_optimized_query_job",
            "run_llm_actions_job",
            "generate_validated_report_artifact",
            "generate_validated_optimizer_artifact",
        ),
    ),
    (
        "query_doctor.web.query_analysis",
        (
            "MISSING_CM_CREDENTIALS_MESSAGE",
            "validate_query_id",
            "run_web_analysis",
            "run_query_id_analysis",
            "collect_case",
            "collect_analyze_and_replace_query_case",
        ),
    ),
]


@pytest.mark.parametrize(("module_path", "names"), SERVER_REEXPORTS)
def test_web_server_reexports_package_symbols(module_path, names):
    module = load_web_module()
    source_module = importlib.import_module(module_path)

    for name in names:
        assert getattr(module, name) is getattr(source_module, name), name


def test_web_server_declares_intentional_facade_exports():
    module = load_web_module()
    expected_exports = {name for _module_path, names in SERVER_REEXPORTS for name in names}

    assert len(module.__all__) == len(set(module.__all__))
    assert expected_exports.issubset(set(module.__all__))
    for name in module.__all__:
        assert hasattr(module, name), name

    imported: dict[str, object] = {}
    exec("from query_doctor.web.server import *", imported)
    assert expected_exports.issubset(set(imported))
    assert "Path" not in imported
    assert "subprocess" not in imported
    assert "BaseHTTPRequestHandler" not in imported


def test_web_server_facade_reexport_smoke(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    query_id = "246462725beeed0:506befef00000000"
    store = module.WebJobStore()
    snapshot = store.create_batch({"scan_date": "2026-05-06"})

    assert settings.repo_dir == REPO_DIR
    assert module.batch_progress_path("abc").name == "progress.jsonl"
    assert module.stages_for_job_kind("running") is module.BATCH_STAGES
    assert snapshot.kind == "batch"
    assert snapshot.batch_progress_path is not None
    settings = module.build_web_settings(
        module.parse_args(["--metadata-coordinator", "host:21000"]), cwd=tmp_path
    )
    assert module.metadata_configured(settings) is True
    assert module.parse_output_case_dir("Output case directory: cases/case-001") == Path(
        "cases/case-001"
    )
    query_settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        repo_dir=REPO_DIR,
        corpus_dir=tmp_path,
    )
    assert module.expected_case_dir_for_query(query_id, query_settings) == (
        tmp_path / "246462725beeed0_506befef00000000"
    )
    assert module.first_form_value({"query_id": [" abc "]}, "query_id") == "abc"


def test_batch_case_detail_action_context_centralizes_action_state(tmp_path):
    module = load_web_module()
    from query_doctor.web.case_detail_state import BatchCaseDetailActionContext

    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": "cases/case-001/abc",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()

    context = module.build_batch_case_detail_action_context(settings, "case-001", store)

    assert isinstance(context, BatchCaseDetailActionContext)
    assert context.case is not None
    assert context.case_dir == case_dir
    assert context.report_allowed is True
    assert context.source_sql_available is True
    assert context.optimizer_allowed is True
    assert context.report_running is False
    assert context.optimizer_running is False
    assert context.job_source == "batch"

    store.create_batch_llm_actions("case-001")
    running_context = module.build_batch_case_detail_action_context(settings, "case-001", store)

    assert running_context.report_running is True
    assert running_context.optimizer_running is True
    assert running_context.optimizer_allowed is True

    blocked_state = module.server_owned_case_required_report_state()
    blocked_state["error"] = "mutated"
    assert (
        module.server_owned_case_required_report_state()["error"]
        == "Report generation requires a complete server-owned case. Re-run analysis first."
    )


def test_web_batch_clean_details_hide_optimizer_action(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 0,
                        "score_severity": "clean",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "not_requested",
                        "score_reasons": ["no analyzer-supported suspicious facts"],
                        "case_dir": "cases/case-001/abc",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **_kwargs):
        calls.append(cmd)
        raise AssertionError("runner should not be called")

    context = module.build_batch_case_detail_action_context(settings, "case-001", store)
    render_context = module.build_batch_case_detail_render_context(
        settings, "case-001", context.case, store
    )
    body = module.render_batch_case_detail_for_request(settings, "case-001", context.case, store)
    status, blocked_body = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert context.report_allowed is False
    assert context.source_sql_available is False
    assert context.optimizer_allowed is False
    assert render_context.optimized_query_state["status"] == "hidden"
    assert status == 400
    assert calls == []
    for rendered in (body, blocked_body):
        assert "Run Query LLM optimizer" not in rendered
        assert "Query LLM optimizer" not in rendered
        assert "Generate report + optimizer" not in rendered
        assert "SELECT secret_col" not in rendered
        assert str(case_dir) not in rendered


def test_web_batch_guidance_only_optimizer_action_is_unavailable(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "not_requested",
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": "cases/case-001/abc",
                        "optimizer_rewrite_support": {
                            "status": "guidance_only",
                            "label": "Guidance only",
                            "reason": "SQL shape exceeds current safe draft thresholds",
                            "risk_mode": "recommendations_only",
                            "risk_reasons": [
                                "cte_body_validation_not_proven",
                                "too_many_ctes_for_safe_rewrite",
                            ],
                            "draft_eligibility": "disabled_by_safety_thresholds",
                            "draft_eligibility_label": "Draft disabled by safety thresholds",
                            "rewriteability_bucket": "human_review_only",
                            "rewriteability_label": "Human review only",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **_kwargs):
        calls.append(cmd)
        raise AssertionError("runner should not be called")

    context = module.build_batch_case_detail_action_context(settings, "case-001", store)
    render_context = module.build_batch_case_detail_render_context(
        settings, "case-001", context.case, store
    )
    body = module.render_batch_case_detail_for_request(settings, "case-001", context.case, store)
    optimizer_status, optimizer_body = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )
    combined_status, combined_body = module.start_batch_case_llm_actions_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert context.report_allowed is True
    assert context.source_sql_available is True
    assert context.optimizer_allowed is False
    assert render_context.optimized_query_state["status"] == "unavailable"
    assert (
        render_context.optimized_query_state["unavailable_reason"]
        == "Trusted SQL draft generation is disabled for this query shape by safety and "
        "validation guardrails. Use the query-shape review areas instead."
    )
    assert optimizer_status == 400
    assert combined_status == 400
    assert calls == []
    for rendered in (body, optimizer_body, combined_body):
        assert "Query LLM optimizer" in rendered
        assert "Trusted SQL draft generation is disabled for this query shape" in rendered
        assert 'aria-label="Unavailable actions"' in rendered
        assert "Run Query LLM optimizer" not in rendered
        assert "Generate Python report + optimizer" not in rendered
        assert "SELECT secret_col" not in rendered
        assert "secret_flag" not in rendered
        assert str(case_dir) not in rendered


def test_web_batch_legacy_draft_ready_optimizer_status_allows_action(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": "cases/case-001/abc",
                        "optimizer_rewrite_support": {
                            "status": "recipe_detected",
                            "label": "Rewrite recipe detected",
                            "reason": "Optimizer run and validation required",
                            "rewriteability_bucket": "safe_material_draft",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()

    context = module.build_batch_case_detail_action_context(settings, "case-001", store)
    render_context = module.build_batch_case_detail_render_context(
        settings, "case-001", context.case, store
    )
    body = module.render_batch_case_detail_for_request(settings, "case-001", context.case, store)

    assert context.source_sql_available is True
    assert context.optimizer_allowed is True
    assert render_context.optimized_query_state["status"] == "not_run"
    assert "Run Query LLM optimizer" in body
    assert "not eligible for an optimizer job" not in body
    assert "SELECT secret_col" not in body
    assert str(case_dir) not in body


def test_batch_case_detail_render_context_returns_typed_safe_view(tmp_path):
    module = load_web_module()
    from query_doctor.web.case_detail_state import BatchCaseDetailRenderContext
    from query_doctor.web.presenters.recent_scan_models import RecentScanCaseDetailView

    summary = tmp_path / "batch_summary.json"
    summary.write_text(json.dumps({"cases": []}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    case = {
        "case_index": 1,
        "query_id": "abc /tmp/case_dir CM_PASSWORD",
        "user": "alice /Users/example/case_dir",
        "score": 22,
        "duration_sec": 90.5,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "skipped",
        "score_reasons": ["raw stderr /tmp/case_dir qwen3-coder"],
        "case_dir": "cases/case-001/abc",
    }

    context = module.build_batch_case_detail_render_context(
        settings, "case-001", case, module.WebJobStore()
    )

    assert isinstance(context, BatchCaseDetailRenderContext)
    assert isinstance(context.view, RecentScanCaseDetailView)
    assert "metadata_facts" not in context.__dict__
    assert "runtime_diagnosis_facts" not in context.__dict__
    rendered_view = repr(context.view)
    assert "/tmp/" not in rendered_view
    assert "/Users/" not in rendered_view
    assert "case_dir" not in rendered_view
    assert "CM_PASSWORD" not in rendered_view
    assert "qwen" not in rendered_view


def test_batch_case_detail_uses_summary_profile_source_for_limitations(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "query_profile_source": "impala",
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc",
                        "score": 17,
                        "score_severity": "suspicious",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "case_dir": str(case_dir),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        batch_summary=summary,
        query_profile_source="cm",
    )
    store = module.WebJobStore()
    action_context = module.build_batch_case_detail_action_context(settings, "case-001", store)

    body = module.render_batch_case_detail_for_request(
        action_context.settings,
        "case-001",
        action_context.case,
        store,
    )

    assert action_context.case["_detail_query_profile_source"] == "impala"
    assert "Source coverage and limitations" in body
    assert "Cluster event context is unavailable for Direct Impala scans" in body
    assert "Bounded Impala metadata is unavailable for this case" in body
    assert str(case_dir) not in body


def test_specific_query_detail_action_context_centralizes_action_state(tmp_path):
    module = load_web_module()
    from query_doctor.web.specific_query_state import SpecificQueryDetailActionContext

    case_dir = tmp_path / "case"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text("- Cardinality anomalies: 2\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    store = module.WebJobStore()

    context = module.build_specific_query_detail_action_context("abc:def", case_dir, store)

    assert isinstance(context, SpecificQueryDetailActionContext)
    assert context.case["query_id"] == "abc:def"
    assert context.analyzer_facts_available is True
    assert context.report_allowed is True
    assert context.source_sql_available is True
    assert context.optimizer_allowed is False
    assert context.report_running is False
    assert context.optimizer_running is False

    store.create_query_llm_actions("abc:def")
    running_context = module.build_specific_query_detail_action_context("abc:def", case_dir, store)

    assert running_context.report_running is True
    assert running_context.optimizer_running is True
    assert running_context.optimizer_allowed is False


def test_web_specific_query_clean_details_hide_report_and_optimizer_actions(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("runner should not be called")

    action_context = module.build_specific_query_detail_action_context("abc:def", case_dir, store)
    render_context = module.build_specific_query_detail_render_context(
        settings, "abc:def", case_dir, store
    )
    page_status, page_body = module.render_specific_query_detail_for_request(
        settings, "abc:def", store
    )
    report_status, report_body = module.start_specific_query_report_job(
        "abc:def", settings, store, runner=fake_runner
    )
    optimizer_status, optimizer_body = module.start_specific_query_optimized_query_job(
        "abc:def", settings, store, runner=fake_runner
    )
    combined_status, combined_body = module.start_specific_query_llm_actions_job(
        "abc:def", settings, store, runner=fake_runner
    )

    assert action_context.case["score_severity"] == "clean"
    assert action_context.report_allowed is False
    assert action_context.source_sql_available is False
    assert action_context.optimizer_allowed is False
    assert render_context.optimized_query_state["status"] == "hidden"
    assert page_status == 200
    assert report_status == 400
    assert optimizer_status == 400
    assert combined_status == 200
    assert calls == []
    for rendered in (page_body, report_body, optimizer_body, combined_body):
        assert "Generate Python report</button>" not in rendered
        assert "Generate LLM narrative</button>" not in rendered
        assert "Run Query LLM optimizer" not in rendered
        assert "Query LLM optimizer" not in rendered
        assert "Generate Python report + optimizer" not in rendered
        assert 'action="/query/details/abc%3Adef/optimized-query"' not in rendered
        assert 'action="/query/details/abc%3Adef/llm-actions"' not in rendered
        assert "SELECT secret_col" not in rendered
        assert "secret_flag" not in rendered
        assert str(case_dir) not in rendered


def test_web_specific_query_not_candidate_optimizer_action_is_unavailable(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text("- Cardinality anomalies: 2\n", encoding="utf-8")
    (case_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("runner should not be called")

    action_context = module.build_specific_query_detail_action_context("abc:def", case_dir, store)
    render_context = module.build_specific_query_detail_render_context(
        settings, "abc:def", case_dir, store
    )
    page_status, page_body = module.render_specific_query_detail_for_request(
        settings, "abc:def", store
    )
    optimizer_status, optimizer_body = module.start_specific_query_optimized_query_job(
        "abc:def", settings, store, runner=fake_runner
    )
    combined_status, combined_body = module.start_specific_query_llm_actions_job(
        "abc:def", settings, store, runner=fake_runner
    )

    assert action_context.case["score_severity"] == "suspicious"
    assert action_context.source_sql_available is True
    assert action_context.optimizer_allowed is False
    assert render_context.optimized_query_state["status"] == "unavailable"
    assert (
        render_context.optimized_query_state["unavailable_reason"]
        == "Query Doctor already classified this query shape as not eligible for an "
        "optimizer job. Use the query-shape review areas instead."
    )
    assert page_status == 200
    assert optimizer_status == 400
    assert combined_status == 200
    assert calls == []
    for rendered in (page_body, optimizer_body, combined_body):
        assert "Query LLM optimizer" in rendered
        assert "not eligible for an optimizer job" in rendered
        assert 'aria-label="Unavailable actions"' in rendered
        assert "Run Query LLM optimizer" not in rendered
        assert "Generate Python report + optimizer" not in rendered
        assert "SELECT secret_col" not in rendered
        assert "secret_flag" not in rendered
        assert str(case_dir) not in rendered


def test_specific_query_detail_render_context_returns_typed_safe_view(tmp_path):
    module = load_web_module()
    from query_doctor.web.presenters.recent_scan_models import RecentScanCaseDetailView
    from query_doctor.web.specific_query_state import SpecificQueryDetailRenderContext

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case = {
        "query_id": "abc /tmp/case_dir CM_PASSWORD",
        "user": "alice /Users/example/case_dir",
        "score": 22,
        "duration_sec": 90.5,
        "collection_status": "ok",
        "analysis_status": "ok",
        "metadata_status": "skipped",
        "score_reasons": ["raw stderr /tmp/case_dir qwen3-coder"],
    }

    context = module.build_specific_query_detail_render_context(
        module.WebSettings(config=Path(".query-doctor-cm.local.json")),
        "abc:def",
        case_dir,
        module.WebJobStore(),
        case=case,
    )

    assert isinstance(context, SpecificQueryDetailRenderContext)
    assert isinstance(context.view, RecentScanCaseDetailView)
    assert "metadata_facts" not in context.__dict__
    assert "runtime_diagnosis_facts" not in context.__dict__
    rendered_view = repr(context.view)
    assert "/tmp/" not in rendered_view
    assert "/Users/" not in rendered_view
    assert "case_dir" not in rendered_view
    assert "CM_PASSWORD" not in rendered_view
    assert "qwen" not in rendered_view


def test_web_handler_defaults_specific_query_to_analysis_only():
    module = load_web_module()

    signature = inspect.signature(module.make_handler)

    assert signature.parameters["analysis_func"].default is module.run_query_id_analysis


def test_web_parse_args_accepts_batch_summary_path():
    module = load_web_module()

    args = module.parse_args(
        [
            "--config",
            ".query-doctor-cm.local.json",
            "--corpus-dir",
            "/tmp/query-doctor-corpus",
            "--recent-batch-root",
            "/tmp/query-doctor-web-batches",
            "--batch-summary",
            "/tmp/query-doctor-batch/batch_summary.json",
        ]
    )

    assert args.corpus_dir == "/tmp/query-doctor-corpus"
    assert args.recent_batch_root == "/tmp/query-doctor-web-batches"
    assert args.batch_summary == "/tmp/query-doctor-batch/batch_summary.json"


def test_web_parse_args_accepts_optimizer_model():
    module = load_web_module()

    args = module.parse_args(
        [
            "--optimizer-model",
            "optimizer-only-model",
            "--report-llm-provider",
            "openai_compatible",
            "--report-llm-base-url",
            "https://llm.example.com",
        ]
    )

    assert args.optimizer_model == "optimizer-only-model"
    assert args.report_llm_provider == "openai_compatible"
    assert args.report_llm_base_url == "https://llm.example.com"


def test_web_settings_defaults_to_localhost_without_config(tmp_path):
    module = load_web_module()

    missing_config = tmp_path / "missing-query-doctor-config.json"

    settings = module.build_web_settings(
        module.parse_args(["--config", str(missing_config)]),
        cwd=tmp_path,
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.model == module.DEFAULT_MODEL
    assert settings.optimizer_model == module.DEFAULT_OPTIMIZER_MODEL
    assert settings.corpus_dir == tmp_path / module.DEFAULT_CORPUS_DIR


def test_web_parse_args_accepts_metadata_options():
    module = load_web_module()

    args = module.parse_args(
        [
            "--config",
            ".query-doctor-cm.local.json",
            "--metadata-coordinator",
            "impala.example.com:21000",
            "--metadata-auth",
            "kerberos",
            "--metadata-protocol",
            "hs2",
            "--metadata-kerberos-service-name",
            "hive",
            "--metadata-kerberos-host-fqdn",
            "impala-lb.example.com",
            "--metadata-ssl",
            "--metadata-ca-cert",
            "/tmp/example-ca.pem",
            "--metadata-timeout-sec",
            "45",
            "--metadata-max-tables",
            "5",
            "--metadata-max-output-bytes",
            "2097152",
            "--metadata-redact",
        ]
    )

    assert args.metadata_coordinator == "impala.example.com:21000"
    assert args.metadata_protocol == "hs2"
    assert args.metadata_kerberos_service_name == "hive"
    assert args.metadata_kerberos_host_fqdn == "impala-lb.example.com"
    assert args.metadata_ssl is True
    assert args.metadata_ca_cert == "/tmp/example-ca.pem"
    assert args.metadata_timeout_sec == 45
    assert args.metadata_max_tables == 5
    assert args.metadata_max_output_bytes == 2097152
    assert args.metadata_redact is True


def test_web_parse_args_accepts_web_nonlocal_bind_alias():
    module = load_web_module()

    args = module.parse_args(
        ["--config", ".query-doctor-cm.local.json", "--allow-nonlocal-web-bind"]
    )

    assert args.allow_nonlocal_web_bind is True


def test_web_parse_args_accepts_legacy_demo_nonlocal_bind_alias():
    module = load_web_module()

    args = module.parse_args(
        ["--config", ".query-doctor-cm.local.json", "--allow-nonlocal-demo-bind"]
    )

    assert args.allow_nonlocal_web_bind is True


def test_web_parse_args_accepts_public_demo():
    module = load_web_module()

    args = module.parse_args(["--public-demo"])

    assert args.batch_summary is None
    assert args.public_demo is True
    assert args.no_llm is False


def write_web_startup_config(tmp_path, **overrides):
    config = {
        "cm_url": "https://cm.example.com:7183/",
        "username": "example_cm_user",
        "cluster": "example_cluster",
        "service": "impala",
    }
    config.update(overrides)
    path = tmp_path / "cm-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def write_manual_profile_case(
    corpus_dir: Path,
    query_id: str,
    *,
    score_reason: str = "cardinality estimate anomalies: 2",
    parsed_operators: int = 3,
) -> Path:
    slug = query_id.replace(":", "_")
    case_dir = corpus_dir / slug
    case_dir.mkdir(parents=True)
    metadata = {
        "query_id": query_id,
        "profile_source": "manual_profile_text",
        "profile_response_format": "text",
        "duration_ms": 90000,
        "query_type": "QUERY",
        "user": "<user>",
        "pool": "<pool>",
    }
    (case_dir / "query_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (case_dir / "cm_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (case_dir / "profile_digest.md").write_text(
        f"Query Runtime Profile\nQuery ID: {query_id}\nUser: <user>\n",
        encoding="utf-8",
    )
    (case_dir / "analysis_facts.md").write_text(
        "# Query Doctor deterministic analysis facts\n\n"
        f"- Parsed operators: {parsed_operators}\n"
        "- Cardinality anomalies: 2\n"
        "- Memory anomalies: 0\n"
        f"- {score_reason}\n",
        encoding="utf-8",
    )
    (case_dir / "analysis.json").write_text(
        json.dumps(
            {
                "operators": [{} for _ in range(parsed_operators)],
                "cardinality_anomalies": [{}, {}],
                "memory_anomalies": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (case_dir / "collection_warnings.txt").write_text(
        "Local exported Impala text profile staged without network collection\n",
        encoding="utf-8",
    )
    return case_dir


@pytest.mark.parametrize(
    "missing_key, expected",
    [
        ("cm_url", "cm_url"),
        ("username", "username/cm_user"),
        ("cluster", "cluster"),
        ("service", "service"),
    ],
)
def test_web_startup_validation_requires_cm_config_fields(tmp_path, missing_key, expected):
    module = load_web_module()
    config = {
        "cm_url": "https://cm.example.com:7183/",
        "username": "example_cm_user",
        "cluster": "example_cluster",
        "service": "impala",
    }
    config.pop(missing_key)
    path = tmp_path / "cm-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(module.WebError) as exc:
        module.validate_web_startup_config(
            path, cwd=tmp_path, env={"CM_PASSWORD": "secret-password"}
        )

    assert expected in str(exc.value)
    assert "CM_PASSWORD or CM_TOKEN" in str(exc.value)
    assert "secret-password" not in str(exc.value)


def test_web_startup_validation_accepts_cm_user_alias_and_env_secret(tmp_path):
    module = load_web_module()
    path = write_web_startup_config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cm_user"] = payload.pop("username")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        module.validate_web_startup_config(path, cwd=tmp_path, env={"CM_TOKEN": "secret-token"})
        == []
    )
    assert (
        module.validate_web_startup_config(
            path, cwd=tmp_path, env={"CM_PASSWORD": "secret-password"}
        )
        == []
    )


def test_web_startup_validation_accepts_configured_cluster_list(tmp_path):
    module = load_web_module()
    path = tmp_path / "cm-config.json"
    path.write_text(
        json.dumps(
            {
                "username": "query_doctor_user",
                "clusters": [
                    {
                        "id": "prod",
                        "cm_url": "https://cm-prod.example.com:7183/",
                        "cluster": "prod_cluster",
                        "service": "impala",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        module.validate_web_startup_config(path, cwd=tmp_path, env={"CM_TOKEN": "secret-token"})
        == []
    )


def test_web_startup_validation_rejects_missing_env_secret(tmp_path):
    module = load_web_module()
    path = write_web_startup_config(tmp_path)

    with pytest.raises(module.WebError) as exc:
        module.validate_web_startup_config(path, cwd=tmp_path, env={})

    message = str(exc.value)
    assert "CM_PASSWORD/CM_TOKEN" in message
    assert "manual_profile_dir" in message
    assert "one exported Impala text profile" in message


def test_web_startup_validation_can_skip_cm_for_read_only_batch_summary(tmp_path):
    module = load_web_module()
    path = tmp_path / "missing-cm-config.json"

    assert module.validate_web_startup_config(path, cwd=tmp_path, env={}, require_cm=False) == []


def test_web_builds_quickstart_corpus_summary_from_staged_manual_profiles(tmp_path):
    module = load_web_module()
    corpus_dir = tmp_path / "cm-corpus"
    first_case = write_manual_profile_case(
        corpus_dir,
        "aaaaaaaaaaaaaaaa:0000000000000001",
    )
    write_manual_profile_case(
        corpus_dir,
        "bbbbbbbbbbbbbbbb:0000000000000002",
        score_reason="no analyzer-supported suspicious facts",
        parsed_operators=0,
    )
    non_manual = corpus_dir / "cccccccccccccccc_0000000000000003"
    non_manual.mkdir()
    (non_manual / "query_metadata.json").write_text(
        json.dumps(
            {
                "query_id": "cccccccccccccccc:0000000000000003",
                "profile_source": "cm",
            }
        ),
        encoding="utf-8",
    )

    summary = module.build_manual_profile_corpus_summary(corpus_dir)

    assert summary is not None
    assert summary["mode"] == "manual-profile-corpus"
    assert summary["selected_count"] == 2
    assert summary["summaries_inspected"] == 2
    assert summary["query_profile_source"] == "manual_profile_text"
    cases = summary["cases"]
    assert [case["query_id"] for case in cases] == [
        "aaaaaaaaaaaaaaaa:0000000000000001",
        "bbbbbbbbbbbbbbbb:0000000000000002",
    ]
    assert cases[0]["case_index"] == 1
    assert cases[0]["case_dir"] == first_case.name
    serialized = json.dumps(summary)
    assert str(corpus_dir) not in serialized
    assert "profile_aaaaaaaaaaaaaaaa_0000000000000001" not in serialized


def test_web_corpus_runtime_skips_cm_startup_requirements(tmp_path):
    module = load_web_module()
    corpus_dir = tmp_path / "cm-corpus"
    write_manual_profile_case(corpus_dir, "aaaaaaaaaaaaaaaa:0000000000000001")
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "example_cluster",
                "service": "impala",
                "username": "example_cm_user",
            }
        ),
        encoding="utf-8",
    )
    args = module.parse_args(["--config", str(config), "--corpus-dir", str(corpus_dir)])
    settings = module.build_web_settings(args, cwd=tmp_path)

    runtime = module.prepare_corpus_summary_runtime(settings)

    assert runtime is not None
    assert runtime.case_count == 1
    assert runtime.settings.corpus_summary is not None
    assert (
        module.validate_web_startup_config(
            runtime.settings.config,
            cwd=tmp_path,
            env={},
            require_cm=runtime.settings.batch_summary is None
            and runtime.settings.corpus_summary is None,
        )
        == []
    )


def test_web_cli_main_starts_from_staged_corpus_without_cm_credentials(
    monkeypatch, tmp_path, capsys
):
    from query_doctor.cli import web as web_cli

    monkeypatch.delenv("CM_PASSWORD", raising=False)
    monkeypatch.delenv("CM_TOKEN", raising=False)
    corpus_dir = tmp_path / "cm-corpus"
    write_manual_profile_case(corpus_dir, "aaaaaaaaaaaaaaaa:0000000000000001")
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "example_cluster",
                "service": "impala",
                "username": "example_cm_user",
            }
        ),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address, handler):
            seen["address"] = address
            seen["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(web_cli, "ThreadingHTTPServer", FakeServer)

    result = web_cli.main(["--config", str(config), "--corpus-dir", str(corpus_dir)])

    captured = capsys.readouterr()
    assert result == 0
    assert seen["address"] == ("127.0.0.1", 8765)
    assert seen["closed"] is True
    assert "Missing required CM startup" not in captured.err
    assert "listening on http://127.0.0.1:8765" in captured.out


def test_web_cli_main_quickstart_corpus_ignores_invalid_default_config(
    monkeypatch, tmp_path, capsys
):
    from query_doctor.cli import web as web_cli

    corpus_dir = tmp_path / "cm-corpus"
    write_manual_profile_case(corpus_dir, "aaaaaaaaaaaaaaaa:0000000000000001")
    (tmp_path / web_cli.cm_collector.DEFAULT_LOCAL_CONFIG_NAME).write_text(
        json.dumps({"future_config_field": True}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address, handler):
            seen["address"] = address
            seen["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(web_cli, "ThreadingHTTPServer", FakeServer)

    result = web_cli.main(["--corpus-dir", str(corpus_dir)])

    captured = capsys.readouterr()
    assert result == 0
    assert seen["address"] == ("127.0.0.1", 8765)
    assert seen["closed"] is True
    assert "Unknown config field" not in captured.err
    assert "listening on http://127.0.0.1:8765" in captured.out


def test_web_cli_main_batch_summary_ignores_invalid_default_config(monkeypatch, tmp_path, capsys):
    from query_doctor.cli import web as web_cli

    summary = tmp_path / "batch_summary.json"
    summary.write_text(json.dumps({"selected_count": 0, "cases": []}), encoding="utf-8")
    (tmp_path / web_cli.cm_collector.DEFAULT_LOCAL_CONFIG_NAME).write_text(
        json.dumps({"future_config_field": True}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address, handler):
            seen["address"] = address
            seen["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(web_cli, "ThreadingHTTPServer", FakeServer)

    result = web_cli.main(["--batch-summary", str(summary), "--no-llm"])

    captured = capsys.readouterr()
    assert result == 0
    assert seen["address"] == ("127.0.0.1", 8765)
    assert seen["closed"] is True
    assert "Unknown config field" not in captured.err
    assert "listening on http://127.0.0.1:8765" in captured.out


def test_web_cli_main_quickstart_corpus_keeps_explicit_config_strict(monkeypatch, tmp_path, capsys):
    from query_doctor.cli import web as web_cli

    corpus_dir = tmp_path / "cm-corpus"
    write_manual_profile_case(corpus_dir, "aaaaaaaaaaaaaaaa:0000000000000001")
    config = tmp_path / "future-config.json"
    config.write_text(json.dumps({"future_config_field": True}), encoding="utf-8")

    def fail_server(*_args, **_kwargs):
        raise AssertionError("server should not start when explicit config is invalid")

    monkeypatch.setattr(web_cli, "ThreadingHTTPServer", fail_server)

    result = web_cli.main(["--config", str(config), "--corpus-dir", str(corpus_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "Unknown config field future_config_field." in captured.err


def test_public_demo_validation_requires_batch_summary_and_no_llm(tmp_path):
    module = load_web_module()
    config = tmp_path / "missing-cm-config.json"
    summary = tmp_path / "batch_summary.json"

    with pytest.raises(module.WebError) as exc:
        module.validate_public_demo_settings(
            module.WebSettings(config=config, public_demo=True, no_llm=True)
        )
    assert "--batch-summary" in str(exc.value)
    assert exc.value.reason_code == "web.public_demo_summary_missing"

    with pytest.raises(module.WebError) as exc:
        module.validate_public_demo_settings(
            module.WebSettings(config=config, batch_summary=summary, public_demo=True)
        )
    assert "--no-llm" in str(exc.value)
    assert exc.value.reason_code == "web.public_demo_llm_not_allowed"

    module.validate_public_demo_settings(
        module.WebSettings(config=config, batch_summary=summary, public_demo=True, no_llm=True)
    )


def test_public_demo_validation_rejects_external_source_settings(tmp_path):
    module = load_web_module()
    config = tmp_path / "missing-cm-config.json"
    summary = tmp_path / "batch_summary.json"

    with pytest.raises(module.WebError) as exc:
        module.validate_public_demo_settings(
            module.WebSettings(
                config=config,
                batch_summary=summary,
                public_demo=True,
                no_llm=True,
                metadata_coordinator="impala.example.com:21000",
            )
        )

    assert "must not load" in str(exc.value)
    assert exc.value.reason_code == "web.public_demo_external_source_rejected"

    with pytest.raises(module.WebError) as exc:
        module.validate_public_demo_settings(
            module.WebSettings(
                config=config,
                batch_summary=summary,
                public_demo=True,
                no_llm=True,
                trino_support_mode="production",
            )
        )

    assert "Trino settings" in str(exc.value)
    assert exc.value.reason_code == "web.public_demo_external_source_rejected"


def test_public_demo_settings_ignore_default_local_config_discovery(tmp_path):
    module = load_web_module()
    from query_doctor.web.viewer_identity import VIEWER_IDENTITY_UNAUTHENTICATED

    default_config = tmp_path / module.cm_collector.DEFAULT_LOCAL_CONFIG_NAME
    default_config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "username": "example_cm_user",
                "cluster": "example_cluster",
                "service": "impala",
            }
        ),
        encoding="utf-8",
    )
    args = module.parse_args(["--public-demo"])

    settings = module.build_web_settings(args, cwd=tmp_path)

    assert settings.public_demo is True
    assert settings.no_llm is True
    assert settings.batch_summary == module.default_public_demo_summary_path()
    assert settings.cm_url is None
    assert settings.clusters == ()
    assert settings.viewer_identity.mode == VIEWER_IDENTITY_UNAUTHENTICATED
    assert settings.viewer_identity.viewer_raw_subjects == ()
    module.validate_public_demo_settings(settings)


def test_public_demo_settings_ignore_owner_source_env(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web.viewer_identity import VIEWER_IDENTITY_UNAUTHENTICATED

    monkeypatch.setenv("QD_SOURCE_OWNER_USER", "private_user")

    settings = module.build_web_settings(module.parse_args(["--public-demo"]), cwd=tmp_path)

    assert settings.source_owner_user is None
    assert settings.source_owner_user_options == ()
    assert settings.viewer_identity.mode == VIEWER_IDENTITY_UNAUTHENTICATED
    assert settings.viewer_identity.viewer_raw_subjects == ()


def test_public_demo_runtime_generates_pack_and_action_outcome_env(tmp_path):
    module = load_web_module()
    settings = module.build_web_settings(module.parse_args(["--public-demo"]), cwd=tmp_path)
    env: dict[str, str] = {}
    out_dir = tmp_path / "query-doctor-public-demo-pack"

    runtime = module.prepare_public_demo_runtime(settings, out_dir=out_dir, env=env)

    assert runtime is not None
    assert runtime.generated is True
    assert runtime.settings.no_llm is True
    assert runtime.settings.batch_summary == out_dir / "batch_summary.json"
    assert runtime.summary_path.is_file()
    assert runtime.action_outcomes_path.is_file()
    assert env["QUERY_DOCTOR_ACTION_OUTCOMES_PATH"] == str(runtime.action_outcomes_path)
    module.validate_public_demo_settings(runtime.settings)


def test_public_demo_runtime_generation_failure_hides_local_path(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.cli import demo_data

    private_dir = tmp_path / "private-demo-output"

    def fail_generate_demo_pack(path, *, overwrite):
        raise ValueError(f"cannot write demo pack under {path}")

    monkeypatch.setattr(demo_data, "generate_demo_pack", fail_generate_demo_pack)
    settings = module.build_web_settings(module.parse_args(["--public-demo"]), cwd=tmp_path)

    with pytest.raises(module.WebError) as exc:
        module.prepare_public_demo_runtime(settings, out_dir=private_dir, env={})

    assert str(exc.value) == "Public demo pack could not be generated."
    assert str(private_dir) not in str(exc.value)
    assert exc.value.reason_code == "web.public_demo_generation_failed"
    assert exc.value.stage == "Preparing public demo runtime"


def test_web_form_numeric_errors_include_safe_reason_code():
    module = load_web_module()

    with pytest.raises(module.WebError) as exc:
        module.parse_positive_form_int(
            {"recent_window_minutes": ["0"]}, "recent_window_minutes", default=60
        )

    assert str(exc.value) == "recent_window_minutes must be a positive integer."
    assert exc.value.reason_code == "web.form_positive_integer_required"
    assert exc.value.stage == "Checking form field recent_window_minutes"

    with pytest.raises(module.WebError) as exc:
        module.parse_non_negative_form_float(
            {"min_duration_sec": ["nan"]}, "min_duration_sec", default=0
        )

    assert exc.value.reason_code == "web.form_finite_number_required"
    assert exc.value.stage == "Checking form field min_duration_sec"


def test_web_startup_validation_accepts_direct_impala_profile_source_without_cm(tmp_path):
    module = load_web_module()
    path = write_web_startup_config(
        tmp_path,
        query_profile_source="impala",
        impala_profile_hosts=[
            "impalad-1.example.com",
            "impalad-2.example.com",
            "impalad-3.example.com",
        ],
    )

    assert module.validate_web_startup_config(path, cwd=tmp_path, env={}) == []


def test_web_startup_validation_accepts_manual_profile_dir_without_cm(tmp_path):
    module = load_web_module()
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    path = tmp_path / "manual-profile-config.json"
    path.write_text(json.dumps({"manual_profile_dir": str(profile_dir)}), encoding="utf-8")

    assert module.validate_web_startup_config(path, cwd=tmp_path, env={}) == []


def test_web_startup_validation_resolves_relative_manual_profile_dir_from_config_parent(
    tmp_path,
):
    module = load_web_module()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    profile_dir = config_dir / "profile-inbox"
    profile_dir.mkdir()
    path = config_dir / "manual-profile-config.json"
    path.write_text(json.dumps({"manual_profile_dir": "profile-inbox"}), encoding="utf-8")

    assert module.validate_web_startup_config(path, cwd=tmp_path, env={}) == []


def test_web_settings_loads_manual_profile_dir_from_config(tmp_path):
    module = load_web_module()
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    path = tmp_path / "manual-profile-config.json"
    path.write_text(json.dumps({"manual_profile_dir": str(profile_dir)}), encoding="utf-8")

    settings = module.build_web_settings(module.parse_args(["--config", str(path)]), cwd=tmp_path)

    assert settings.active_cluster_key == "default"
    assert settings.manual_profile_dir == profile_dir


def test_web_settings_loads_corpus_dir_from_config_parent(tmp_path):
    module = load_web_module()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "manual-profile-config.json"
    path.write_text(json.dumps({"corpus_dir": "web-cases"}), encoding="utf-8")

    settings = module.build_web_settings(module.parse_args(["--config", str(path)]), cwd=tmp_path)

    assert settings.corpus_dir == config_dir / "web-cases"


def test_web_settings_loads_recent_batch_root_from_config_parent(tmp_path):
    module = load_web_module()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "web-config.json"
    path.write_text(json.dumps({"recent_batch_root": "recent-batches"}), encoding="utf-8")

    settings = module.build_web_settings(module.parse_args(["--config", str(path)]), cwd=tmp_path)

    assert settings.recent_batch_root == config_dir / "recent-batches"


def test_web_settings_cli_recent_batch_root_overrides_config(tmp_path):
    module = load_web_module()
    path = tmp_path / "web-config.json"
    path.write_text(json.dumps({"recent_batch_root": "config-batches"}), encoding="utf-8")
    cli_batch_root = tmp_path / "cli-batches"

    settings = module.build_web_settings(
        module.parse_args(["--config", str(path), "--recent-batch-root", str(cli_batch_root)]),
        cwd=tmp_path,
    )

    assert settings.recent_batch_root == cli_batch_root


def test_web_settings_rejects_recent_batch_root_outside_temp(tmp_path):
    module = load_web_module()
    path = tmp_path / "web-config.json"
    path.write_text(
        json.dumps({"recent_batch_root": "/var/lib/query-doctor/recent-batches"}),
        encoding="utf-8",
    )

    with pytest.raises(module.WebError) as exc_info:
        module.build_web_settings(module.parse_args(["--config", str(path)]), cwd=tmp_path)

    assert exc_info.value.reason_code == "web.recent_batch_root_invalid"


def test_web_settings_cli_corpus_dir_overrides_config(tmp_path):
    module = load_web_module()
    path = tmp_path / "manual-profile-config.json"
    path.write_text(json.dumps({"corpus_dir": "config-cases"}), encoding="utf-8")
    cli_corpus = tmp_path / "cli-cases"

    settings = module.build_web_settings(
        module.parse_args(["--config", str(path), "--corpus-dir", str(cli_corpus)]),
        cwd=tmp_path,
    )

    assert settings.corpus_dir == cli_corpus


def test_web_startup_validation_rejects_missing_manual_profile_dir(tmp_path):
    module = load_web_module()
    path = tmp_path / "manual-profile-config.json"
    missing_profile_dir = tmp_path / "missing-profile-inbox"
    path.write_text(
        json.dumps({"manual_profile_dir": str(missing_profile_dir)}),
        encoding="utf-8",
    )

    with pytest.raises(module.WebError) as exc:
        module.validate_web_startup_config(path, cwd=tmp_path, env={})

    assert "manual_profile_dir" in str(exc.value)
    assert str(missing_profile_dir) not in str(exc.value)
    assert exc.value.reason_code == "web.manual_profile_dir_unreadable"


def test_web_startup_validation_requires_impala_hosts_for_direct_source(tmp_path):
    module = load_web_module()
    path = write_web_startup_config(tmp_path, query_profile_source="impala")

    with pytest.raises(module.WebError) as exc:
        module.validate_web_startup_config(path, cwd=tmp_path, env={})

    assert "impala_profile_hosts" in str(exc.value)
    assert exc.value.reason_code == "impala.direct_profile_host_missing"
    assert exc.value.stage == "Checking web startup source config"


def test_web_startup_validation_rejects_secret_config_fields(tmp_path):
    module = load_web_module()
    path = write_web_startup_config(tmp_path, CM_PASSWORD="not-allowed")

    with pytest.raises(module.cm_collector.ConfigError):
        module.validate_web_startup_config(path, cwd=tmp_path, env={"CM_TOKEN": "secret-token"})


def test_web_startup_validation_checks_ca_bundle(tmp_path):
    module = load_web_module()
    missing_ca = tmp_path / "missing-ca.pem"
    path = write_web_startup_config(tmp_path, ca_bundle=str(missing_ca))

    with pytest.raises(module.WebError) as exc:
        module.validate_web_startup_config(path, cwd=tmp_path, env={"CM_TOKEN": "secret-token"})

    assert "ca_bundle" in str(exc.value)
    assert str(missing_ca) not in str(exc.value)
    assert exc.value.reason_code == "impala.cm_ca_bundle_unreadable"
    assert exc.value.stage == "Checking web startup TLS config"
    assert "Fix the configured CM ca_bundle" in str(exc.value.next_step)

    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("CERT", encoding="utf-8")
    path = write_web_startup_config(tmp_path, ca_bundle=str(ca_bundle), insecure_skip_verify=True)
    warnings = module.validate_web_startup_config(
        path, cwd=tmp_path, env={"CM_TOKEN": "secret-token"}
    )

    assert warnings
    assert "insecure_skip_verify=true" in warnings[0]


def test_web_rejects_nonlocal_bind_without_explicit_flag():
    module = load_web_module()

    module.validate_bind_host("::1", allow_nonlocal_web_bind=False)

    with pytest.raises(module.WebError):
        module.validate_bind_host("0.0.0.0", allow_nonlocal_web_bind=False)

    module.validate_bind_host("0.0.0.0", allow_nonlocal_web_bind=True)


def test_web_allows_safe_nonlocal_bind_with_explicit_flag(tmp_path):
    module = load_web_module()
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        host="0.0.0.0",
        allow_nonlocal_web_bind=True,
        source_visibility="safe",
        viewer_identity=local_first_viewer_identity(("analyst_one",)),
    )

    module.validate_owner_raw_nonlocal_bind(settings)


def test_web_rejects_owner_raw_nonlocal_bind_without_authenticated_viewer(tmp_path):
    module = load_web_module()
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        host="0.0.0.0",
        allow_nonlocal_web_bind=True,
        source_visibility="owner_raw",
        source_owner_user="analyst_one",
        viewer_identity=local_first_viewer_identity(("analyst_one",)),
    )

    with pytest.raises(module.WebError, match="source_visibility=owner_raw") as exc_info:
        module.validate_owner_raw_nonlocal_bind(settings)
    message = str(exc_info.value)
    assert "D3 trusted auth front door" in message
    assert "sets exactly one normalized viewer_identity_header" in message
    assert "Configure viewer_identity_header behind that front door" in message


def test_web_rejects_owner_raw_cluster_nonlocal_bind_without_authenticated_viewer(tmp_path):
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        host="0.0.0.0",
        allow_nonlocal_web_bind=True,
        source_visibility="safe",
        clusters=(
            WebClusterConfig(key="prod", label="Production", source_visibility="safe"),
            WebClusterConfig(
                key="stage",
                label="Staging",
                source_visibility="owner_raw",
                source_owner_user="stage_user",
            ),
        ),
        viewer_identity=local_first_viewer_identity(("stage_user",)),
    )

    with pytest.raises(module.WebError, match="source_visibility=owner_raw") as exc_info:
        module.validate_owner_raw_nonlocal_bind(settings)
    message = str(exc_info.value)
    assert "D3 trusted auth front door" in message
    assert "sets exactly one normalized viewer_identity_header" in message


def test_web_allows_owner_raw_nonlocal_bind_with_authenticated_viewer(tmp_path):
    module = load_web_module()
    from query_doctor.web.viewer_identity import authenticated_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        host="0.0.0.0",
        allow_nonlocal_web_bind=True,
        source_visibility="owner_raw",
        source_owner_user="analyst_one",
        viewer_identity=authenticated_viewer_identity("analyst_one"),
    )

    module.validate_owner_raw_nonlocal_bind(settings)


def test_web_allows_owner_raw_nonlocal_bind_with_viewer_identity_header(tmp_path):
    module = load_web_module()
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        host="0.0.0.0",
        allow_nonlocal_web_bind=True,
        source_visibility="owner_raw",
        source_owner_user="analyst_one",
        viewer_identity_header="X-QD-Viewer",
        viewer_identity=local_first_viewer_identity(("analyst_one",)),
    )

    module.validate_owner_raw_nonlocal_bind(settings)


def test_web_cli_main_rejects_owner_raw_nonlocal_bind_without_auth(monkeypatch, tmp_path, capsys):
    from query_doctor.cli import web as web_cli

    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "example_cluster",
                "service": "impala",
                "username": "example_cm_user",
                "source_visibility": "owner_raw",
                "source_owner_user": "analyst_one",
            }
        ),
        encoding="utf-8",
    )

    def fail_server(*_args, **_kwargs):
        raise AssertionError("server should not start for owner_raw non-local bind")

    monkeypatch.setattr(web_cli, "ThreadingHTTPServer", fail_server)

    result = web_cli.main(
        ["--config", str(config), "--host", "0.0.0.0", "--allow-nonlocal-web-bind"]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "source_visibility=owner_raw" in captured.err
    assert "D3 trusted auth front door" in captured.err
    assert "sets exactly one normalized viewer_identity_header" in captured.err
    assert "analyst_one" not in captured.err


def test_web_cli_main_allows_owner_raw_nonlocal_bind_with_viewer_identity_header(
    monkeypatch, tmp_path, capsys
):
    from query_doctor.cli import web as web_cli

    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "example_cluster",
                "service": "impala",
                "username": "example_cm_user",
                "source_visibility": "owner_raw",
                "source_owner_user": "analyst_one",
                "viewer_identity_header": "X-QD-Viewer",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CM_PASSWORD", "dummy")
    captured_settings = {}

    class DummyServer:
        def __init__(self, address, handler):
            captured_settings["address"] = address
            captured_settings["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            captured_settings["closed"] = True

    monkeypatch.setattr(web_cli, "ThreadingHTTPServer", DummyServer)

    result = web_cli.main(
        ["--config", str(config), "--host", "0.0.0.0", "--allow-nonlocal-web-bind"]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "source_visibility=owner_raw" not in captured.err
    assert captured_settings["address"] == ("0.0.0.0", 8765)
    assert captured_settings["closed"] is True


@pytest.mark.parametrize(
    "query_id",
    [
        "",
        "missingcolon",
        "abc:def/ghi",
        "../abc:def",
        "abc%3Adef",
        "https://cm.example.com/a:b",
        "abc:def?x=1",
        "abc:def#fragment",
        "abc def:ghi",
    ],
)
def test_web_query_id_validation_rejects_unsafe_ids(query_id):
    module = load_web_module()

    with pytest.raises(module.WebError):
        module.validate_query_id(query_id)


def test_web_job_flow_returns_progress_status_and_escaped_result():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()

    def fake_analysis(query_id, report_mode, redact_identifiers, received_settings):
        assert query_id == "abc:def"
        assert report_mode == "analysis"
        assert redact_identifiers is True
        assert received_settings is settings
        return module.WebQueryAnalysisResult(
            query_id=query_id,
            case={
                "query_id": query_id,
                "score": 6,
                "duration_sec": 12.5,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 3"],
                "memory_anomaly_count": 3,
                "report_generated": False,
                "report_validation_status": "not_run",
            },
        )

    status, location = module.start_analyze_job(
        {"query_id": ["abc:def"], "mode": ["admin"]},
        settings,
        store,
        analysis_func=fake_analysis,
    )

    assert status == 303
    assert location.startswith("/jobs/")
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    payload = json.loads(module.render_job_status_json(snapshot))
    assert payload["status"] == "ok"
    assert payload["progress"] == 100
    assert payload["progress_view"]["percent"] == 100
    assert payload["progress_view"]["steps"][-1]["label"] == "Done"
    assert "Known Query ID analysis" in payload["result_html"]
    assert "No LLM report is generated." in payload["result_html"]
    assert "analysis only" not in payload["result_html"]
    assert "This page does not render raw SQL" not in payload["result_html"]
    assert 'data-href="/query/details/abc%3Adef"' in payload["result_html"]
    assert "onclick=" not in payload["result_html"]
    assert "onkeydown=" not in payload["result_html"]
    assert (
        "<th>Query ID</th><th>Priority</th><th>Duration</th>"
        "<th>Table stats</th><th>Metadata</th><th>Summary</th>" in payload["result_html"]
    )
    assert "<th>Rank</th>" not in payload["result_html"]
    assert "memory 3" in payload["result_html"]
    assert "/tmp/query-doctor-web" not in payload["result_html"]
    assert "case_dir" not in payload["result_html"]
    assert "Case path" not in payload["result_html"]
    assert "qwen3-coder:30b" not in payload["result_html"]
    assert "Model" not in payload["result_html"]


def test_profile_upload_job_flow_uses_raw_free_known_query_output(monkeypatch):
    module = load_web_module()
    from query_doctor.web import request_handlers as request_handlers_module

    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    received: dict[str, object] = {}

    def fake_upload_analysis(
        query_id,
        profile_text,
        received_settings,
        *,
        runner,
        progress,
        cancel_check,
    ):
        received.update(
            {
                "query_id": query_id,
                "profile_text": profile_text,
                "settings": received_settings,
                "runner": runner,
                "cancel_check": cancel_check,
            }
        )
        for stage_index in range(6):
            progress(stage_index)
        return module.WebQueryAnalysisResult(
            query_id=query_id,
            case={
                "query_id": query_id,
                "score": 6,
                "duration_sec": 12.5,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 3"],
                "memory_anomaly_count": 3,
                "report_generated": True,
                "report_validation_status": "passed",
                "case_dir": "/tmp/query-doctor-web/private-case",
            },
        )

    monkeypatch.setattr(
        request_handlers_module,
        "run_uploaded_profile_analysis",
        fake_upload_analysis,
    )

    raw_profile = "Query Runtime Profile\nSql Statement: SELECT secret_upload FROM private_table\n"
    runner = lambda *args, **kwargs: None
    status, location = module.start_profile_upload_job(
        {"query_id": ["abc:def"], "profile_text": [raw_profile]},
        settings,
        store,
        runner=runner,
    )

    assert status == 303
    assert location.startswith("/jobs/")
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    payload = json.loads(module.render_job_status_json(snapshot))
    assert payload["status"] == "ok"
    assert payload["kind"] == "query"
    assert "Known Query ID analysis" in payload["result_html"]
    assert 'data-href="/query/details/abc%3Adef"' in payload["result_html"]
    assert "SELECT secret_upload" not in payload["result_html"]
    assert "Query Runtime Profile" not in payload["result_html"]
    assert "/tmp/query-doctor-web" not in payload["result_html"]
    assert "case_dir" not in payload["result_html"]
    assert received["query_id"] == "abc:def"
    assert received["profile_text"] == raw_profile
    assert received["settings"] is settings
    assert received["runner"] is runner
    assert callable(received["cancel_check"])


def test_web_report_optimizer_progress_uses_shared_stage_definitions():
    from query_doctor.web.job_progress import (
        BATCH_REPORT_STAGES,
        LLM_ACTIONS_PROGRESS_STEPS,
        LLM_ACTIONS_STAGES,
        OPTIMIZED_QUERY_STAGES,
        OPTIMIZED_QUERY_PROGRESS_STEPS,
        REPORT_PROGRESS_STEPS,
        build_progress_view,
        progress_step_index,
    )
    from query_doctor.web.jobs import stages_for_job_kind

    assert stages_for_job_kind("batch_report") == BATCH_REPORT_STAGES
    assert stages_for_job_kind("query_report") == BATCH_REPORT_STAGES
    assert stages_for_job_kind("batch_optimized_query") == OPTIMIZED_QUERY_STAGES
    assert stages_for_job_kind("query_optimized_query") == OPTIMIZED_QUERY_STAGES
    assert stages_for_job_kind("batch_llm_actions") == LLM_ACTIONS_STAGES
    assert (
        progress_step_index(
            REPORT_PROGRESS_STEPS,
            BATCH_REPORT_STAGES[1][1],
            BATCH_REPORT_STAGES[1][2],
        )
        == 1
    )
    assert (
        progress_step_index(
            OPTIMIZED_QUERY_PROGRESS_STEPS,
            OPTIMIZED_QUERY_STAGES[1][1],
            OPTIMIZED_QUERY_STAGES[1][2],
        )
        == 1
    )
    assert (
        progress_step_index(
            LLM_ACTIONS_PROGRESS_STEPS,
            LLM_ACTIONS_STAGES[2][1],
            LLM_ACTIONS_STAGES[2][2],
        )
        == 2
    )
    progress_view = build_progress_view(
        LLM_ACTIONS_PROGRESS_STEPS, LLM_ACTIONS_STAGES[2][1], LLM_ACTIONS_STAGES[2][2]
    )
    assert progress_view.percent == 50
    assert [(step.label, step.state, step.detail) for step in progress_view.steps] == [
        ("Checking case", "done", "Done"),
        ("Generating report", "done", "Done"),
        ("Generating optimizer draft", "running", "Generating optimizer draft"),
        ("Done", "neutral", "Pending"),
    ]


def test_web_job_status_json_includes_safe_progress_view_for_llm_actions():
    module = load_web_module()
    store = module.WebJobStore()
    job = store.create_batch_llm_actions("case-001")
    store.update_stage(job.job_id, 2)

    payload = json.loads(module.render_job_status_json(store.get(job.job_id)))

    assert payload["progress"] == 72
    assert payload["progress_view"]["current_stage"] == "Generating optimizer draft"
    assert payload["progress_view"]["percent"] == 50
    assert payload["progress_view"]["steps"] == [
        {"label": "Checking case", "state": "done", "icon": "✓", "detail": "Done"},
        {"label": "Generating report", "state": "done", "icon": "✓", "detail": "Done"},
        {
            "label": "Generating optimizer draft",
            "state": "running",
            "icon": "…",
            "detail": "Generating optimizer draft",
        },
        {"label": "Done", "state": "neutral", "icon": "−", "detail": "Pending"},
    ]


def test_detail_progress_renderers_prefer_server_owned_progress_view():
    from query_doctor.web.job_progress import (
        BATCH_REPORT_STAGES,
        LLM_ACTIONS_STAGES,
        OPTIMIZED_QUERY_STAGES,
        progress_view_for_job,
    )
    from query_doctor.web.presenters.recent_scan_models import ReportActionView
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_job_progress,
        render_optimized_query_progress,
    )
    from query_doctor.web.ui.report_actions import render_llm_report_progress

    job_id = "0123456789abcdef0123456789abcdef"
    report_view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id=job_id,
        stage_label="Done",
        progress=100,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_report",
        progress_view=progress_view_for_job(
            "batch_report",
            BATCH_REPORT_STAGES[1][1],
            BATCH_REPORT_STAGES[1][2],
        ),
    )
    report_html = render_llm_report_progress(report_view)

    assert "Generating validated report" in report_html
    assert 'style="width:25%"' in report_html
    assert 'style="width:100%"' not in report_html

    optimizer_html = render_optimized_query_progress(
        present_optimized_query_action(
            {
                "status": "running",
                "job_id": job_id,
                "job_kind": "batch_optimized_query",
                "stage_label": "Done",
                "progress": 100,
                "progress_view": progress_view_for_job(
                    "batch_optimized_query",
                    OPTIMIZED_QUERY_STAGES[1][1],
                    OPTIMIZED_QUERY_STAGES[1][2],
                ),
            }
        )
    )

    assert "Generating optimizer draft" in optimizer_html
    assert 'style="width:25%"' in optimizer_html
    assert 'style="width:100%"' not in optimizer_html

    combined_view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id=job_id,
        stage_label="Done",
        progress=100,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_llm_actions",
        progress_view=progress_view_for_job(
            "batch_llm_actions",
            LLM_ACTIONS_STAGES[2][1],
            LLM_ACTIONS_STAGES[2][2],
        ),
    )
    combined_html = render_llm_actions_job_progress(
        combined_view,
        present_optimized_query_action(
            {
                "status": "running",
                "job_id": job_id,
                "job_kind": "batch_llm_actions",
                "stage_label": "Done",
                "progress": 100,
            }
        ),
    )

    assert "Generating optimizer draft" in combined_html
    assert 'style="width:50%"' in combined_html
    assert 'style="width:100%"' not in combined_html


def test_action_presenters_keep_only_route_safe_job_ids():
    from query_doctor.web.presenters.recent_scan import present_report_action
    from query_doctor.web.ui.llm_actions import present_optimized_query_action

    job_id = "0123456789abcdef0123456789abcdef"

    report_view = present_report_action({"status": "running", "job_id": job_id})
    optimizer_view = present_optimized_query_action({"status": "running", "job_id": job_id})

    assert report_view.job_id == job_id
    assert optimizer_view.job_id == job_id
    assert present_report_action({"status": "running", "job_id": "host_01"}).job_id == ""
    assert present_optimized_query_action({"status": "running", "job_id": "bad:e2e"}).job_id == ""


def test_detail_progress_renderers_do_not_poll_display_safe_job_ids():
    from query_doctor.web.job_progress import (
        BATCH_REPORT_STAGES,
        LLM_ACTIONS_STAGES,
        OPTIMIZED_QUERY_STAGES,
        progress_view_for_job,
    )
    from query_doctor.web.presenters.recent_scan_models import ReportActionView
    from query_doctor.web.ui.llm_actions import (
        OptimizedQueryActionView,
        render_llm_actions_job_progress,
        render_optimized_query_progress,
    )
    from query_doctor.web.ui.report_actions import render_llm_report_progress

    unsafe_job_id = "host_01"
    report_view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id=unsafe_job_id,
        stage_label="Generating validated report",
        progress=38,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_report",
        progress_view=progress_view_for_job(
            "batch_report",
            BATCH_REPORT_STAGES[1][1],
            BATCH_REPORT_STAGES[1][2],
        ),
    )
    report_html = render_llm_report_progress(report_view)

    assert "Generating validated report" in report_html
    assert "data-report-job-status-url" not in report_html
    assert "/jobs/host_01" not in report_html

    optimizer_view = OptimizedQueryActionView(
        status="running",
        job_id=unsafe_job_id,
        job_kind="batch_optimized_query",
        stage_label="Generating optimizer draft",
        error="",
        output_kind="sql_draft",
        source_available=True,
        fallback_reason="",
        risk_mode="",
        risk_reasons=(),
        source_scope="",
        progress_view=progress_view_for_job(
            "batch_optimized_query",
            OPTIMIZED_QUERY_STAGES[1][1],
            OPTIMIZED_QUERY_STAGES[1][2],
        ),
    )
    optimizer_html = render_optimized_query_progress(optimizer_view)

    assert "Generating optimizer draft" in optimizer_html
    assert "data-optimizer-job-status-url" not in optimizer_html
    assert "/jobs/host_01" not in optimizer_html

    combined_report_view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id=unsafe_job_id,
        stage_label="Generating optimizer draft",
        progress=72,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_llm_actions",
        progress_view=progress_view_for_job(
            "batch_llm_actions",
            LLM_ACTIONS_STAGES[2][1],
            LLM_ACTIONS_STAGES[2][2],
        ),
    )
    combined_html = render_llm_actions_job_progress(combined_report_view, optimizer_view)

    assert "Generating optimizer draft" in combined_html
    assert "data-report-job-status-url" not in combined_html
    assert "/jobs/host_01" not in combined_html


def test_render_optimized_query_progress_fails_closed_without_progress_view():
    from query_doctor.web.ui.llm_actions import (
        OptimizedQueryActionView,
        render_optimized_query_progress,
    )

    view = OptimizedQueryActionView(
        status="running",
        job_id="0123456789abcdef0123456789abcdef",
        job_kind="batch_optimized_query",
        stage_label="Generating optimizer draft",
        error="",
        output_kind="sql_draft",
        source_available=True,
        fallback_reason="",
        risk_mode="",
        risk_reasons=(),
        source_scope="",
        progress_view=None,
    )

    assert render_optimized_query_progress(view) == ""


def test_render_llm_report_progress_fails_closed_without_progress_view():
    from query_doctor.web.presenters.recent_scan_models import ReportActionView
    from query_doctor.web.ui.report_actions import render_llm_report_progress

    view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id="0123456789abcdef0123456789abcdef",
        stage_label="Generating validated report",
        progress=38,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_report",
        progress_view=None,
    )

    assert render_llm_report_progress(view) == ""


def test_render_llm_report_progress_fails_closed_for_combined_job_kind():
    from query_doctor.web.job_progress import LLM_ACTIONS_STAGES, progress_view_for_job
    from query_doctor.web.presenters.recent_scan_models import ReportActionView
    from query_doctor.web.ui.report_actions import render_llm_report_progress

    view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id="0123456789abcdef0123456789abcdef",
        stage_label="Generating validated report",
        progress=38,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_llm_actions",
        progress_view=progress_view_for_job(
            "batch_llm_actions",
            LLM_ACTIONS_STAGES[1][1],
            LLM_ACTIONS_STAGES[1][2],
        ),
    )

    assert render_llm_report_progress(view) == ""


def test_render_llm_actions_job_progress_fails_closed_without_progress_view():
    from query_doctor.web.presenters.recent_scan_models import ReportActionView
    from query_doctor.web.ui.llm_actions import (
        OptimizedQueryActionView,
        render_llm_actions_job_progress,
    )

    job_id = "0123456789abcdef0123456789abcdef"
    report_view = ReportActionView(
        status="running",
        running=True,
        trusted=False,
        partial_untrusted=False,
        error="",
        job_id=job_id,
        stage_label="Generating validated report",
        progress=38,
        note="",
        button_label="Generating LLM report",
        button_disabled=True,
        show_open_link=False,
        job_kind="batch_llm_actions",
        progress_view=None,
    )
    optimizer_view = OptimizedQueryActionView(
        status="running",
        job_id=job_id,
        job_kind="batch_llm_actions",
        stage_label="Generating optimizer draft",
        error="",
        output_kind="sql_draft",
        source_available=True,
        fallback_reason="",
        risk_mode="",
        risk_reasons=(),
        source_scope="",
        progress_view=None,
    )

    assert render_llm_actions_job_progress(report_view, optimizer_view) == ""


@pytest.mark.parametrize(
    ("factory_name", "case_or_query_id", "expected_kind"),
    [
        ("create_batch_report", "case-001", "batch_report"),
        ("create_batch_optimized_query", "case-001", "batch_optimized_query"),
        ("create_batch_case_actions", "case-001", "batch_case_actions"),
        ("create_query_report", "abc:def", "query_report"),
        ("create_query_optimized_query", "abc:def", "query_optimized_query"),
        ("create_query_case_actions", "abc:def", "query_case_actions"),
    ],
)
def test_detail_job_status_json_uses_safe_progress_view_contract(
    factory_name, case_or_query_id, expected_kind
):
    module = load_web_module()
    store = module.WebJobStore()
    job = getattr(store, factory_name)(case_or_query_id)
    store.update_stage(job.job_id, 1)

    payload = json.loads(module.render_job_status_json(store.get(job.job_id)))

    assert payload["kind"] == expected_kind
    assert payload["status"] == "running"
    assert isinstance(payload["progress_view"]["current_stage"], str)
    assert isinstance(payload["progress_view"]["percent"], int)
    assert payload["progress_view"]["steps"]
    for step in payload["progress_view"]["steps"]:
        assert set(step) == {"label", "state", "icon", "detail"}
        assert step["state"] in {"done", "running", "neutral", "failed", "skipped"}
        assert isinstance(step["label"], str)
        assert isinstance(step["detail"], str)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert case_or_query_id not in serialized
    assert "case_dir" not in serialized
    assert "/tmp/query-doctor" not in serialized
    assert "qwen3-coder" not in serialized


def test_web_job_cancel_marks_safe_terminal_status_and_blocks_late_completion():
    module = load_web_module()
    store = module.WebJobStore()
    job = store.create_batch({"scan_target": "finished"})

    cancelled = store.request_cancel(job.job_id)
    store.complete_html(job.job_id, "late unsafe result")

    snapshot = store.get(job.job_id)
    assert cancelled is not None
    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.cancel_requested is True
    assert snapshot.result_html == ""
    payload = json.loads(module.render_job_status_json(snapshot))
    assert payload["status"] == "cancelled"
    assert payload["error"] == "Job stopped by user."
    assert payload["cancel_requested"] is True
    assert "late unsafe result" not in payload["result_html"]


@pytest.mark.parametrize(
    ("config", "create_job", "expected_reason", "expected_title", "expected_next_step"),
    (
        (
            None,
            "create_batch",
            "batch.unexpected_failure",
            "Recent scan failed unexpectedly",
            "Review the selected source, local config, credentials, and scan bounds, then retry.",
        ),
        (
            {"only_running": True},
            "create_running_batch",
            "running.unexpected_failure",
            "Running scan failed unexpectedly",
            "Review the selected source, local config, credentials, and scan bounds, then retry.",
        ),
    ),
)
def test_web_batch_job_unexpected_exception_uses_structured_raw_free_fallback(
    monkeypatch,
    config,
    create_job,
    expected_reason,
    expected_title,
    expected_next_step,
):
    module = load_web_module()
    from query_doctor.web import batch_jobs

    raw_fragments = [
        "/Users/example/case_dir",
        "SELECT secret_col FROM raw_table",
        "raw stdout from subprocess",
        "optimized_query.sql",
        "qwen3-coder",
    ]

    def raise_unexpected_exception(*_args, **_kwargs):
        raise RuntimeError(" | ".join(raw_fragments))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("runner should not be called after command build failure")

    monkeypatch.setattr(batch_jobs, "build_batch_command", raise_unexpected_exception)

    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = getattr(store, create_job)({"scan_target": "running" if config else "finished"})

    module.run_batch_job(
        job.job_id,
        module.BatchRunConfig(metadata_top_limit=0, **(config or {})),
        settings,
        store,
        fail_if_called,
    )

    snapshot = store.get(job.job_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.stage_label == "Failed"
    assert snapshot.progress == 100
    assert snapshot.result_html == ""
    assert snapshot.error == (
        f"{expected_title}. Internal exception details are hidden because "
        "they may contain sensitive data."
    )

    payload = json.loads(module.render_job_status_json(snapshot))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "failed"
    assert payload["error"] == snapshot.error
    assert payload["error_info"]["title"] == expected_title
    assert payload["error_info"]["reason_code"] == expected_reason
    assert payload["error_info"]["stage"] == "Running recent scan"
    assert payload["error_info"]["next_step"] == expected_next_step
    assert (
        "The failing internal step did not raise a classified web error."
        in payload["error_info"]["details"]
    )
    assert payload["result_html"] == ""
    for fragment in raw_fragments:
        assert fragment not in snapshot.error
        assert fragment not in serialized


def test_web_batch_report_unexpected_exception_fails_job_with_structured_error(
    tmp_path, monkeypatch
):
    module = load_web_module()
    from query_doctor.web import job_workers

    raw_fragments = [
        "SELECT secret_col FROM raw_table",
        "/private/tmp/query-doctor-report-case",
        "diagnosis_python.partial.md",
    ]

    def raise_unexpected_exception(*_args, **_kwargs):
        raise RuntimeError(" | ".join(raw_fragments))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("runner should not be called after command build failure")

    monkeypatch.setattr(
        job_workers,
        "build_selected_case_report_command",
        raise_unexpected_exception,
    )

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_batch_report("case-001")

    module.run_batch_case_report_job(
        job.job_id,
        "case-001",
        case_dir,
        settings,
        store,
        fail_if_called,
    )

    snapshot = store.get(job.job_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.progress == 100
    assert snapshot.result_html == ""

    payload = json.loads(module.render_job_status_json(snapshot))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["error_info"]["title"] == "Finished-query report generation failed unexpectedly"
    assert payload["error_info"]["reason_code"] == "batch_report.unexpected_failure"
    assert payload["error_info"]["stage"] == "Generating validated report"
    assert payload["error_info"]["next_step"] == (
        "Review the selected case artifacts, report settings, and local config, then retry."
    )
    for fragment in raw_fragments:
        assert fragment not in serialized


@pytest.mark.parametrize(
    ("kind", "expected_reason", "expected_title", "expected_stage"),
    (
        (
            "query",
            "query.unexpected_failure",
            "Specific Query analysis failed unexpectedly",
            "Checking Query ID",
        ),
        (
            "trino_query",
            "trino_query.unexpected_failure",
            "Trino Query ID diagnosis failed unexpectedly",
            "Checking Trino Query ID",
        ),
    ),
)
def test_web_analyze_job_unexpected_exception_uses_job_kind_structured_error(
    kind, expected_reason, expected_title, expected_stage
):
    module = load_web_module()
    raw_fragments = [
        "SELECT secret_col FROM raw_table",
        "https://coordinator.example.invalid/v1/query/raw",
        "/private/tmp/query-doctor-query-case",
    ]

    def raise_unexpected_exception(*_args, **_kwargs):
        raise RuntimeError(" | ".join(raw_fragments))

    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create("abc:def", "analysis", kind=kind)

    module.run_analysis_job(
        job.job_id,
        "abc:def",
        "analysis",
        True,
        settings,
        store,
        raise_unexpected_exception,
    )

    payload = json.loads(module.render_job_status_json(store.get(job.job_id)))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "failed"
    assert payload["error_info"]["title"] == expected_title
    assert payload["error_info"]["reason_code"] == expected_reason
    assert payload["error_info"]["stage"] == expected_stage
    assert payload["error_info"]["details"]
    for fragment in raw_fragments:
        assert fragment not in serialized


def test_web_trino_recent_unexpected_exception_uses_structured_error(monkeypatch):
    module = load_web_module()
    from query_doctor.web import batch_jobs

    raw_fragments = [
        "20260603_120102_00001_abcde",
        "https://trino.example.invalid/v1/query",
        "Bearer secret-token",
    ]

    def raise_unexpected_exception(*_args, **_kwargs):
        raise RuntimeError(" | ".join(raw_fragments))

    monkeypatch.setattr(batch_jobs, "run_trino_recent_scan", raise_unexpected_exception)

    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_trino_recent({"engine": "trino"})

    batch_jobs.run_trino_recent_job(
        job.job_id,
        module.BatchRunConfig(engine="trino"),
        settings,
        store,
    )

    payload = json.loads(module.render_job_status_json(store.get(job.job_id)))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "failed"
    assert payload["error_info"]["title"] == "Trino Recent scan failed unexpectedly"
    assert payload["error_info"]["reason_code"] == "trino_recent.unexpected_failure"
    assert payload["error_info"]["stage"] == "Checking Trino Recent contract"
    assert payload["error_info"]["next_step"].startswith("Check the selected Trino local source")
    for fragment in raw_fragments:
        assert fragment not in serialized


def test_web_optimizer_job_unexpected_exception_uses_job_kind_structured_error(
    tmp_path, monkeypatch
):
    module = load_web_module()
    from query_doctor.web import job_workers

    raw_fragments = [
        "SELECT secret_col FROM raw_table",
        "/private/tmp/query-doctor-optimizer-case",
        "optimized_query.partial.txt",
    ]

    def raise_unexpected_exception(*_args, **_kwargs):
        raise RuntimeError(" | ".join(raw_fragments))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("runner should not be called after command build failure")

    monkeypatch.setattr(job_workers, "build_optimized_query_command", raise_unexpected_exception)

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_query_optimized_query("abc:def")

    module.run_optimized_query_job(
        job.job_id,
        "abc:def",
        case_dir,
        settings,
        store,
        fail_if_called,
    )

    payload = json.loads(module.render_job_status_json(store.get(job.job_id)))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "failed"
    assert (
        payload["error_info"]["title"] == "Specific Query optimizer generation failed unexpectedly"
    )
    assert payload["error_info"]["reason_code"] == "query_optimized_query.unexpected_failure"
    assert payload["error_info"]["stage"] == "Generating optimizer draft"
    assert payload["error_info"]["next_step"] == (
        "Review the selected case artifacts, source SQL availability, and local config, then retry."
    )
    for fragment in raw_fragments:
        assert fragment not in serialized


def test_web_case_actions_unexpected_exception_uses_job_kind_structured_error(
    tmp_path, monkeypatch
):
    module = load_web_module()
    from query_doctor.web import job_workers

    raw_fragments = [
        "SELECT secret_col FROM raw_table",
        "/private/tmp/query-doctor-actions-case",
        "diagnosis_python.partial.md",
    ]

    def raise_unexpected_exception(*_args, **_kwargs):
        raise RuntimeError(" | ".join(raw_fragments))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("runner should not be called after report failure")

    monkeypatch.setattr(
        job_workers,
        "generate_validated_report_artifact",
        raise_unexpected_exception,
    )

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_query_case_actions("abc:def")

    module.run_llm_actions_job(
        job.job_id,
        "abc:def",
        case_dir,
        settings,
        store,
        fail_if_called,
    )

    payload = json.loads(module.render_job_status_json(store.get(job.job_id)))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "failed"
    assert payload["error_info"]["title"] == (
        "Specific Query report and optimizer action failed unexpectedly"
    )
    assert payload["error_info"]["reason_code"] == "query_case_actions.unexpected_failure"
    assert payload["error_info"]["stage"] == "Generating validated report"
    assert payload["error_info"]["details"]
    for fragment in raw_fragments:
        assert fragment not in serialized


def test_web_cancel_route_redirects_to_job_page():
    module = load_web_module()
    from query_doctor.web.routes import route_post_request

    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_batch({"scan_target": "finished"})

    response = route_post_request(f"/jobs/{job.job_id}/cancel", {}, settings, store)

    snapshot = store.get(job.job_id)
    assert response is not None
    assert response.status == 303
    assert response.location == f"/jobs/{job.job_id}"
    assert snapshot is not None
    assert snapshot.status == "cancelled"


def test_web_job_progress_renders_cancel_button():
    module = load_web_module()
    from query_doctor.web.ui.progress import render_job_panel

    store = module.WebJobStore()
    job = store.create_batch({"scan_target": "finished"})

    html = render_job_panel(job)

    assert f'action="/jobs/{job.job_id}/cancel"' in html
    assert "Stop job" in html


def test_web_job_panel_uses_shared_progress_view_for_initial_render():
    module = load_web_module()
    from query_doctor.web.ui.progress import render_job_panel

    job = module.WebJobSnapshot(
        job_id="0123456789abcdef0123456789abcdef",
        query_id="abc:def",
        report_mode="analysis",
        status="running",
        stage_label="Analyzing profile",
        progress=62,
    )

    html = render_job_panel(job)

    assert '<span id="job-stage" class="progress-stage">Analyzing profile</span>' in html
    assert 'id="job-progress-fill" class="progress-fill" style="width:33%"' in html
    assert 'id="job-progress-fill" class="progress-fill" style="width:62%"' not in html
    assert 'class="batch-progress-steps job-progress-steps"' in html
    assert "Collecting or reusing profile" in html
    assert "Analyzing profile" in html
    assert "Generating Python report" in html
    assert "Preparing deterministic result" in html
    assert "batch-progress-step--done" in html
    assert "batch-progress-step--running" in html


def test_web_combined_case_actions_renders_single_cancel_button():
    from query_doctor.web.job_progress import LLM_ACTIONS_STAGES, progress_view_for_job
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_block,
    )
    from query_doctor.web.presenters.recent_scan import present_report_action

    job_id = "0123456789abcdef0123456789abcdef"
    progress_view = progress_view_for_job(
        "batch_llm_actions",
        LLM_ACTIONS_STAGES[1][1],
        LLM_ACTIONS_STAGES[1][2],
    )
    report_state = {
        "status": "running",
        "running": True,
        "trusted": False,
        "report_variant": "python",
        "job_id": job_id,
        "job_kind": "batch_case_actions",
        "stage_label": "Generating validated report",
        "progress": 38,
        "progress_view": progress_view,
    }
    optimizer_state = {
        "status": "running",
        "running": True,
        "job_id": job_id,
        "job_kind": "batch_case_actions",
        "stage_label": "Generating validated report",
        "progress": 38,
        "progress_view": progress_view,
    }

    html = render_llm_actions_block(
        "case-001",
        present_report_action(report_state),
        present_optimized_query_action(optimizer_state),
    )

    assert html.count(f'action="/jobs/{job_id}/cancel"') == 1
    assert 'aria-label="Python actions progress"' in html
    assert 'aria-label="Python report progress"' not in html
    assert "Stop Python actions" in html
    assert "Stop job" not in html
    assert "Generating Python report + optimizer" in html


def test_web_combined_case_actions_cancelled_uses_combined_status_label():
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_block,
    )
    from query_doctor.web.presenters.recent_scan import present_report_action

    job_id = "0123456789abcdef0123456789abcdef"
    report_state = {
        "status": "cancelled",
        "running": False,
        "trusted": False,
        "report_variant": "python",
        "error": "Job stopped by user.",
        "job_id": job_id,
        "job_kind": "batch_case_actions",
        "stage_label": "Cancelled",
        "progress": 100,
    }
    optimizer_state = {
        "status": "cancelled",
        "running": False,
        "error": "Job stopped by user.",
        "job_id": job_id,
        "job_kind": "batch_case_actions",
        "stage_label": "Cancelled",
        "progress": 100,
    }

    html = render_llm_actions_block(
        "case-001",
        present_report_action(report_state),
        present_optimized_query_action(optimizer_state),
    )

    assert "Python actions stopped" in html
    assert "Query LLM optimizer stopped" not in html
    assert "Python report stopped" not in html
    assert "Job stopped by user." in html
    assert f'action="/jobs/{job_id}/cancel"' not in html


def test_web_report_action_failure_renders_structured_job_error(tmp_path):
    module = load_web_module()
    from query_doctor.web.presenters.recent_scan import present_report_action
    from query_doctor.web.ui.report_actions import render_llm_report_status

    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_batch_report("case-001", report_variant="python")
    store.fail(
        job.job_id,
        RuntimeError(
            "Report command failed with stdout SELECT secret_col FROM db.source_table "
            f"and path {tmp_path / 'private-case'}"
        ),
    )
    failed_job = store.get(job.job_id)
    assert failed_job is not None

    state = module.load_batch_case_report_state(
        settings,
        "case-001",
        {"case_index": 1, "query_id": "abc...000001", "case_dir": str(tmp_path / "case")},
        store,
        job=failed_job,
    )
    view = present_report_action(state)
    html = render_llm_report_status(view, None, llm_enabled=False)

    assert state["status"] == "failed"
    assert state["error_info"]["reason_code"] == "batch_report.failed"
    assert "Reason" in html
    assert "batch_report.failed" in html
    assert "Stage" in html
    assert "Checking selected batch case" in html
    assert "Next step:" in html
    assert "Review the selected inputs and local configuration" in html
    assert "SELECT" not in html
    assert "secret_col" not in html
    assert "private-case" not in html


def test_web_no_llm_action_block_uses_python_only_labels():
    from query_doctor.web.ui.html_helpers import SafeHtml
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_block,
    )
    from query_doctor.web.presenters.recent_scan import present_report_action

    html = render_llm_actions_block(
        "case-001",
        present_report_action({"status": "generated", "trusted": True, "report_variant": "python"}),
        present_optimized_query_action(
            {
                "status": "generated",
                "output_kind": "recommendations_only",
                "risk_mode": "recommendations_only",
                "source_scope": "read_only_statement",
            }
        ),
        trusted_report_html=SafeHtml("<p>Safe Python report.</p>"),
        trusted_optimizer_recommendations="- Review the deterministic findings.",
        llm_enabled=False,
    )

    assert "Reports and optimizer" in html
    assert '<h2 class="docs-panel-title">Reports and optimizer</h2>' in html
    assert "<h1>Reports and optimizer</h1>" not in html
    assert '<section id="case-actions"' in html
    assert '<section id="llm-actions"' not in html
    assert "Deterministic baseline from Python-owned facts. Recommended first." in html
    assert "Looks for validated rewrite guidance or a trusted draft without executing SQL." in html
    assert "Python Report" in html
    assert "Python report result" in html
    assert (
        '<details class="analysis-subdetails action-result-details report-result-details" '
        'aria-label="Python report body">'
    ) in html
    assert "<summary>Python Report body</summary>" in html
    assert "Open Query optimizer recommendations" in html
    assert 'href="#query-optimizer-result"' in html
    assert 'id="query-optimizer-result"' in html
    assert "Query optimizer recommendations" in html
    assert (
        '<details class="analysis-subdetails action-result-details" '
        'aria-label="Query optimizer recommendations">'
    ) in html
    assert (
        '<details class="analysis-subdetails" open aria-label="Query optimizer recommendations">'
    ) not in html
    assert "Generate LLM narrative" not in html
    assert "Runs one LLM report" not in html
    assert "LLM narrative" not in html
    assert "Query LLM optimizer" not in html


def test_web_available_action_cards_explain_purpose():
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_block,
    )
    from query_doctor.web.presenters.recent_scan import present_report_action

    html = render_llm_actions_block(
        "case-001",
        present_report_action({"status": "not_run", "report_variant": "python"}),
        present_optimized_query_action({"status": "not_run"}),
        llm_report_view=present_report_action({"status": "not_run", "report_variant": "llm"}),
    )
    styles = layout.render_shared_styles()

    assert "Deterministic baseline from Python-owned facts. Recommended first." in html
    assert "LLM narrative" in html
    assert "Optional wording pass over the same validated facts for comparison." in html
    assert "Looks for validated rewrite guidance or a trusted draft without executing SQL." in html
    assert "Runs the deterministic report and optimizer for this selected case only." in html
    assert 'class="llm-action-card-actions"' in html
    assert_css_contains(
        styles,
        ".case-detail-panel{display:grid;gap:0;padding:0;border:0;"
        "border-radius:0;background:transparent;box-shadow:none;overflow:visible}",
    )
    assert_css_contains(
        styles,
        "@media(max-width:760px){",
    )
    assert_css_contains(
        styles,
        ".case-detail-panel{padding:0;border:0;background:transparent;box-shadow:none}",
    )
    assert_css_contains(
        styles,
        ".case-detail-panel>#case-actions,.case-detail-panel>#llm-actions{"
        "margin-top:24px;padding:16px;border:1px solid var(--border);"
        "border-left:3px solid var(--accent);",
    )
    assert_css_contains(
        styles,
        ".case-detail-panel>#case-actions .report-body,"
        ".case-detail-panel>#llm-actions .report-body{padding:12px 0 0}",
    )
    assert_css_contains(
        styles,
        ".case-detail-panel>.docs-panel.action-plan-panel{"
        "margin-top:16px;padding:16px;border:1px solid var(--border);"
        "border-left:3px solid var(--accent);border-radius:var(--radius-lg);"
        "background:var(--panel-muted);box-shadow:none}",
    )
    assert_css_contains(
        styles,
        ".case-detail-panel>.docs-panel.action-plan-panel .section-title{"
        "padding:0 0 12px;border-bottom:1px solid var(--border);font-size:15px}",
    )
    assert_css_contains(
        styles,
        ".action-candidate-card{display:grid;gap:12px;padding:14px 12px;"
        "border-radius:var(--radius-lg);background:var(--panel)}",
    )
    assert_css_contains(
        styles,
        ".action-candidate-card .source-locator-list{display:flex;flex-wrap:wrap;"
        "gap:7px;margin:0;padding:0;list-style:none}",
    )
    assert_css_contains(
        styles,
        ".source-location-chip{display:inline-flex;align-items:center;min-height:20px;",
    )
    assert_css_contains(
        styles,
        ".redacted-source-map{display:grid;gap:4px;margin-top:8px;",
    )
    assert_css_contains(
        styles,
        ".owner-raw-source-code{display:grid;overflow:auto;",
    )
    assert_css_contains(
        styles,
        ".batch-cell--summary .source-location-chips{display:flex;margin-top:6px}",
    )
    assert_css_contains(
        styles,
        ".action-supporting-fact-list{display:grid;"
        "grid-template-columns:repeat(auto-fit,minmax(220px,1fr));"
        "gap:8px;margin:0;padding:0;list-style:none}",
    )
    assert_css_contains(
        styles,
        ".llm-action-card{display:flex;flex-direction:column;gap:6px;padding:10px;",
    )
    assert_css_contains(styles, ".llm-action-card-actions{display:grid;gap:6px;margin-top:auto}")
    assert_css_contains(styles, ".llm-action-card .button{height:auto;min-height:32px;")


def test_web_static_js_opens_new_scan_deep_link():
    script = (REPO_DIR / "query_doctor/web/static/app.js").read_text(encoding="utf-8")

    assert "function openNewScanPanel()" in script
    assert "document.getElementById('new-scan')" in script
    assert "window.location.hash === '#new-scan'" in script
    assert "[data-open-new-scan]" in script


def test_web_static_js_surfaces_lost_job_polling_connection():
    script = (REPO_DIR / "query_doctor/web/static/app.js").read_text(encoding="utf-8")

    assert "var pollFailureCount = 0;" in script
    assert "function renderClientErrorInfoBody(target, info)" in script
    assert "function showPollingUnavailable()" in script
    assert "Scan status unavailable" in script
    assert "web.client_polling_unavailable" in script
    assert "Job status polling" in script
    assert "local Query Doctor web server is not responding" in script
    assert "Unsafe details remain hidden." in script
    assert "pollFailureCount >= 3" in script


def test_web_static_js_legacy_job_error_fallback_is_structured():
    script = (REPO_DIR / "query_doctor/web/static/app.js").read_text(encoding="utf-8")

    assert "renderClientErrorInfoBody(errorSlot, {" in script
    assert "data.error_info && data.error_info.reason_code" in script
    assert "web.job_status_failed" in script
    assert "Review the selected inputs and local configuration, then retry." in script


def test_web_unavailable_case_actions_render_compact_status():
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_block,
    )
    from query_doctor.web.presenters.recent_scan import present_report_action

    html = render_llm_actions_block(
        "case-001",
        present_report_action({"status": "not_run", "report_variant": "python"}),
        present_optimized_query_action({"status": "unavailable"}),
        report_enabled=False,
    )

    assert (
        '<section id="case-actions" '
        'class="panel docs-panel llm-actions-panel llm-actions-panel--unavailable"' in html
    )
    assert '<details class="llm-actions-status-details">' in html
    assert (
        "<summary><span>Reports and optimizer</span>"
        "<small>No action is available for this case</small></summary>" in html
    )
    assert '<h2 class="docs-panel-title">Reports and optimizer</h2>' not in html
    assert "Python Report is available only for suspicious or bad queries." in html
    assert (
        "Source SQL is unavailable or outside the optimizer read-only scope for this case." in html
    )
    assert 'action="/batch/case/case-001/case-actions"' not in html


def test_web_no_llm_combined_action_uses_python_labels():
    from query_doctor.web.job_progress import LLM_ACTIONS_STAGES, progress_view_for_job
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_llm_actions_block,
    )
    from query_doctor.web.presenters.recent_scan import present_report_action

    job_id = "0123456789abcdef0123456789abcdef"
    progress_view = progress_view_for_job(
        "batch_case_actions",
        LLM_ACTIONS_STAGES[1][1],
        LLM_ACTIONS_STAGES[1][2],
    )
    report_state = {
        "status": "running",
        "running": True,
        "report_variant": "python",
        "job_id": job_id,
        "job_kind": "batch_case_actions",
        "progress_view": progress_view,
    }
    optimizer_state = {
        "status": "running",
        "running": True,
        "job_id": job_id,
        "job_kind": "batch_case_actions",
        "progress_view": progress_view,
    }

    html = render_llm_actions_block(
        "case-001",
        present_report_action(report_state),
        present_optimized_query_action(optimizer_state),
        llm_enabled=False,
    )

    assert 'aria-label="Python actions progress"' in html
    assert '<section id="case-actions"' in html
    assert '<section id="llm-actions"' not in html
    assert "Stop Python actions" in html
    assert "Generating Python report + optimizer" in html
    assert "Stop LLM actions" not in html
    assert "Generating LLM report + optimizer" not in html


def test_status_summary_uses_no_llm_report_label():
    from types import SimpleNamespace

    from query_doctor.web.presenters.recent_scan_status import (
        present_recent_scan_status_summary,
    )
    from query_doctor.web.ui.recent_scan_details import render_case_status_summary_view

    view = SimpleNamespace(
        status_fields=(
            ("collection", "ok"),
            ("analysis", "ok"),
            ("metadata", "not_requested"),
            ("report", "validated"),
        )
    )
    status_view = present_recent_scan_status_summary(view, report_label="Python report")
    html = render_case_status_summary_view(status_view)

    assert "Python report" in html
    assert "LLM report" not in html


def test_web_run_subprocess_terminates_when_cancelled(tmp_path):
    module = load_web_module()

    completed = module.run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        cwd=tmp_path,
        timeout_sec=30,
        runner=subprocess.run,
        cancel_check=lambda: True,
    )

    assert completed.returncode == -15


def test_web_run_subprocess_bounds_real_stdout_and_stderr_capture(tmp_path):
    module = load_web_module()
    limit = module.WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES
    script = (
        "import sys\n"
        f"sys.stdout.write('o' * ({limit} + 4096))\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write('e' * ({limit} + 8192))\n"
        "sys.stderr.flush()\n"
    )

    completed = module.run_subprocess(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout_sec=10,
        runner=subprocess.run,
    )

    assert completed.returncode == 0
    assert len(completed.stdout.encode("utf-8")) == limit
    assert len(completed.stderr.encode("utf-8")) == limit
    assert completed.stdout == "o" * limit
    assert completed.stderr == "e" * limit


def test_web_run_subprocess_bounds_custom_runner_output(tmp_path):
    module = load_web_module()
    limit = module.WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES

    def fake_runner(cmd, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="x" * (limit + 512),
            stderr=("é" * limit),
        )

    completed = module.run_subprocess(
        ["query-doctor-test"],
        cwd=tmp_path,
        timeout_sec=10,
        runner=fake_runner,
    )

    assert completed.returncode == 0
    assert len(completed.stdout.encode("utf-8")) == limit
    assert len(completed.stderr.encode("utf-8")) <= limit
    assert completed.stdout == "x" * limit


def test_web_query_results_accumulate_after_multiple_completed_jobs():
    module = load_web_module()
    store = module.WebJobStore()

    first = store.create("abc:def", "analysis")
    store.complete(
        first.job_id,
        module.WebQueryAnalysisResult(
            query_id="abc:def",
            case={
                "query_id": "abc:def",
                "score": 2,
                "duration_sec": 4.0,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 1"],
            },
        ),
    )
    second = store.create("ghi:jkl", "analysis")
    store.complete(
        second.job_id,
        module.WebQueryAnalysisResult(
            query_id="ghi:jkl",
            case={
                "query_id": "ghi:jkl",
                "score": 5,
                "duration_sec": 8.0,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "ok",
                "table_stats_status": "available",
                "score_reasons": ["cardinality estimate anomalies: 2"],
            },
        ),
    )

    payload = json.loads(module.render_job_status_json(store.get(second.job_id)))
    result_html = payload["result_html"]

    assert result_html.count("<tbody>") == 1
    assert result_html.index("abc:def") < result_html.index("ghi:jkl")
    assert 'data-href="/query/details/abc%3Adef"' in result_html
    assert 'data-href="/query/details/ghi%3Ajkl"' in result_html
    assert "<th>Rank</th>" not in result_html


def test_web_running_query_job_keeps_prior_specific_query_table_visible():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()

    first = store.create("abc:def", "analysis")
    store.complete(
        first.job_id,
        module.WebQueryAnalysisResult(
            query_id="abc:def",
            case={
                "query_id": "abc:def",
                "score": 2,
                "duration_sec": 4.0,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 1"],
            },
        ),
    )
    running = store.create("ghi:jkl", "analysis")
    handler = module.make_handler(settings, job_store=store)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{running.job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Analysis running" in captured["body"]
    assert "Known Query ID analysis" in captured["body"]
    assert "abc:def" in captured["body"]
    assert "ghi:jkl" not in captured["body"]


def test_web_query_job_page_clears_query_input_after_launch():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create("abc:def", "analysis")
    handler = module.make_handler(settings, job_store=store)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job.job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert 'id="query_id" name="query_id" type="text" value=""' in captured["body"]
    assert 'value="abc:def"' not in captured["body"]


def test_web_query_job_panel_omits_duration_hint():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    job = module.WebJobSnapshot(
        job_id="job-1",
        query_id="abc:def",
        report_mode="analysis",
        status="running",
        stage_label="Analyzing profile",
        progress=62,
    )

    body = module.render_page(settings, query_id="abc:def", report_mode="analysis", job=job)

    assert "Analysis running" in body
    assert "This usually takes a few seconds to a couple of minutes." not in body


def test_web_specific_query_details_route_renders_safe_deterministic_details(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "- Parsed operators: 4",
                "- Cardinality anomalies: 1",
                "- Memory anomalies: 2",
                "- table stats row-count completeness: available",
                "",
                "## Evidence Quality",
                "",
                "- score: 90/100",
                "- level: high",
                "",
                "### Strengths",
                "",
                "- profile operators parsed: 4",
                "- CM metrics coverage: 4/4 metrics ok, 40 points",
                "",
                "### Limitations",
                "",
                "- table metadata context is unavailable",
                "",
                "## Stats Metadata Quality",
                "",
                "- status: available",
                "- table_stats: available",
                "- column_stats: complete",
                "- row_estimate_evidence: observed",
                "- row_estimate_issue_count: 1",
                "- partition_coverage: available",
                "- stats_context: stats_present_with_row_estimate_evidence",
                "- interpretation: Stats are present, but row-estimate mismatch evidence remains; stats may not be the primary explanation.",
                "- guardrail: Stats quality is follow-up evidence, not a standalone root cause.",
                "",
                "## CM Query Context",
                "",
                "- available: yes",
                "- query_type: QUERY",
                "- pool: root.analytics",
                "- start_time: 2026-05-04T10:00:00Z",
                "- end_time: 2026-05-04T10:05:15Z",
                "- duration: 315s",
                "- admission_result: admitted",
                "- admission_wait: 2.50s",
                "- rows_produced: 12.00M",
                "- bytes_read: 42.00 GiB",
                "- memory_aggregate_peak: 18.00 GiB",
                "",
                "## CM Time-Series Context",
                "",
                "- window: 2026-05-04T09:59:00Z to 2026-05-04T10:06:00Z",
                "- max: 209715200.00",
                "",
                "## CM Metrics Facts",
                "",
                "- status: available",
                "- metrics_profile: cm6",
                "- coverage: 4/4 metrics ok, 40 points",
                "- availability: 4 ok, 0 no_data, 0 unavailable",
                "- unavailable_metrics: none",
                "- no_data_metrics: none",
                "- admission_pool_pressure: observed",
                "- admission_pool_pressure_basis: admission queued max=2.50/s avg=0.40/s; admission rejected max=0.00/s; admission timed_out max=0.00/s",
                "- host_cpu_pressure: not_observed",
                "- host_cpu_pressure_basis: host_cpu_user max=22.00 avg=5.00; host_cpu_system max=3.00",
                "- daemon_memory_growth: observed",
                "- daemon_memory_growth_basis: daemon memory min=10.00 GiB max=23.00 GiB delta=13.00 GiB ratio=2.30x",
                "- daemon_memory_pressure: unknown",
                "- daemon_memory_pressure_basis: daemon memory capacity or limit is not part of the current safe runtime metrics contract",
                "- host_disk_io_pressure: observed",
                "- host_disk_io_pressure_basis: host disk I/O max=500.00 MiB/s avg=100.00 MiB/s ratio=5.00x",
                "- hdfs_datanode_io_pressure: observed",
                "- hdfs_datanode_io_pressure_basis: HDFS DataNode read max=600.00 MiB/s avg=120.00 MiB/s; read ratio=5.00x; local_reads_max=100.00/s remote_reads_max=260.00/s; remote/local reads ratio=2.60x",
                "- network_io_spike: observed",
                "- network_io_spike_basis: host network I/O max=200.00 MiB/s avg=20.00 MiB/s ratio=10.00x",
                "",
                "### CM metrics limitations",
                "",
                "- CM metrics are bounded query-window context signals, not standalone proof of cause.",
                "",
                "## CM Metrics Correlation",
                "",
                "- status: available",
                "- coverage: 4/4 metrics ok, 40 points",
                "- correlated_signals: 2",
                "- context_only_signals: 1",
                "- guardrail: CM metrics can strengthen profile-supported evidence, but they are not standalone root-cause proof.",
                "",
                "- admission_pool_pressure: context_only (metric=observed, strength=weak)",
                "  - basis: admission queued max=2.50/s avg=0.40/s; admission rejected max=0.00/s; admission timed_out max=0.00/s",
                "  - interpretation: Admission/pool pressure was observed, but the query did not expose matching admission wait evidence.",
                "- host_cpu_pressure: not_observed (metric=not_observed, strength=none)",
                "  - basis: host_cpu_user max=22.00 avg=5.00; host_cpu_system max=3.00",
                "  - interpretation: No deterministic optimizer or report action is derived from this metric status.",
                "- daemon_memory_growth: correlated (metric=observed, strength=moderate)",
                "  - basis: daemon memory min=10.00 GiB max=23.00 GiB delta=13.00 GiB ratio=2.30x",
                "  - interpretation: Daemon memory growth is correlated with selected-query non-zero spill/scratch evidence; use it only as runtime context for reducing intermediate memory footprint.",
                "- host_disk_io_pressure: context_only (metric=observed, strength=weak)",
                "  - basis: host disk I/O max=500.00 MiB/s avg=100.00 MiB/s ratio=5.00x",
                "  - interpretation: Host disk I/O pressure was observed, but parsed profile facts did not identify matching scan/storage elapsed-time evidence.",
                "- hdfs_datanode_io_pressure: context_only (metric=observed, strength=weak)",
                "  - basis: HDFS DataNode read max=600.00 MiB/s avg=120.00 MiB/s; read ratio=5.00x; local_reads_max=100.00/s remote_reads_max=260.00/s; remote/local reads ratio=2.60x",
                "  - interpretation: HDFS DataNode I/O pressure was observed, but parsed profile facts did not identify matching scan/storage elapsed-time evidence.",
                "- network_io_spike: correlated (metric=observed, strength=moderate)",
                "  - basis: host network I/O max=200.00 MiB/s avg=20.00 MiB/s ratio=10.00x",
                "  - interpretation: Network I/O spike is correlated with parsed large exchange/data movement evidence; prioritize reducing exchange rows or payload.",
                "",
                "## Cluster Runtime Context",
                "",
                "- status: available",
                "- collection_status: collected",
                "- coverage: 4/4 metrics ok, 40 points",
                "- metrics_profile: cm6",
                "- window_scope: bounded query runtime window with 60s padding",
                "- limit_summary: max_points_per_query=10, max_response_bytes=12345",
                "- scoring_contribution: +4 triage score points from 2 correlated CM metric signal(s), capped at +6; context-only, unknown and not_observed signals do not add score",
                "- guardrail: Cluster runtime context is deterministic follow-up context only. It can strengthen profile-supported hypotheses but must not be phrased as standalone root-cause proof.",
                "",
                "### Signal rollup",
                "",
                "- observed_signals: Admission/pool pressure, Daemon memory growth, Host disk I/O pressure, HDFS DataNode I/O pressure, Network I/O spike",
                "- correlated_signals: Daemon memory growth, Network I/O spike",
                "- context_only_signals: Admission/pool pressure, Host disk I/O pressure, HDFS DataNode I/O pressure",
                "- unknown_signals: Daemon memory pressure",
                "- not_observed_signals: Host CPU pressure",
                "",
                "### Cluster runtime limitations",
                "",
                "- CM metrics are bounded query-window context signals, not standalone proof of cause.",
                "- Raw metric points and per-point times are intentionally excluded from trusted analysis facts.",
                "",
                "## Runtime Diagnosis",
                "",
                "- status: available",
                "- summary: Network/exchange pressure is the strongest plausible follow-up hypothesis from deterministic facts.",
                "- guardrail: Runtime Diagnosis ranks follow-up hypotheses from analyzer facts only. It does not convert correlated metrics into standalone root-cause proof.",
                "",
                "### Network/exchange pressure",
                "",
                "- status: plausible_follow_up",
                "- interpretation: Network/exchange pressure or downstream exchange backpressure is a plausible follow-up hypothesis for this query window. Validate it with comparable reruns and bounded cluster network metrics; this is not standalone proof of external network instability.",
                "- evidence:",
                "  - CM Metrics Correlation: network_io_spike=correlated (metric=observed, strength=moderate).",
                "  - host network I/O max=200.00 MiB/s avg=20.00 MiB/s ratio=10.00x",
            ]
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    handler = module.make_handler(settings)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/query/details/abc%3Adef"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Known Query ID details" in captured["body"]
    assert (
        "Use the verdict to decide priority, then read the recommended change" in captured["body"]
    )
    assert "Read-only" not in captured["body"]
    assert "abc:def" in captured["body"]
    assert "Jump to section" not in captured["body"]
    assert 'class="detail-toc"' not in captured["body"]
    assert '<section id="case-overview" class="case-verdict"' in captured["body"]
    assert '<section id="analysis-summary"' not in captured["body"]
    assert '<section id="action-plan"' in captured["body"]
    assert '<section id="diagnostics"' in captured["body"]
    assert 'href="#pipeline-status"' not in captured["body"]
    assert 'href="#findings"' not in captured["body"]
    assert 'href="#evidence-details"' not in captured["body"]
    assert '<section id="case-actions"' in captured["body"]
    assert "Verdict" in captured["body"]
    assert "Start with the recommendation below" not in captured["body"]
    assert "confidence" in captured["body"]
    assert "High: 90/100 analyzer evidence quality" in captured["body"]
    assert (
        "Stats are present, but row-estimate mismatch evidence remains; stats may not be the primary explanation."
        in captured["body"]
    )
    assert "profile operators parsed: 4" not in captured["body"]
    assert "Diagnostics and evidence" in captured["body"]
    assert "Python Report" in captured["body"]
    assert "LLM narrative" in captured["body"]
    assert captured["body"].index("Diagnostics and evidence") < captured["body"].index(
        "Python Report"
    )
    assert '<details class="panel docs-panel diagnostics-details">' in captured["body"]
    assert '<details class="panel docs-panel diagnostics-details" open>' not in captured["body"]
    diagnostics_html = captured["body"][
        captured["body"].index('<section id="diagnostics"') : captured["body"].index(
            '<section id="case-actions"'
        )
    ]
    assert '<section id="evidence-details"' not in diagnostics_html
    assert '<section id="diagnostic-questions"' in diagnostics_html
    assert '<section id="runtime-evidence"' in diagnostics_html
    assert '<section id="metrics-evidence"' in diagnostics_html
    assert '<section id="metadata-evidence"' in diagnostics_html
    assert '<section id="score-evidence"' in diagnostics_html
    analysis_html = diagnostics_html[diagnostics_html.index('<section id="runtime-evidence"') :]
    assert "Runtime signals" in captured["body"]
    assert "Runtime metrics" in captured["body"]
    assert "<span>CM metrics</span><strong>available</strong>" not in captured["body"]
    assert "metrics_profile" in captured["body"]
    assert "metrics_profile</span><strong>host_01</strong>" in captured["body"]
    assert "cm6" not in captured["body"]
    assert "4/4 metrics ok, 40 points" in captured["body"]
    assert "4 ok, 0 no_data, 0 unavailable" in captured["body"]
    assert "unavailable_metrics" in captured["body"]
    assert "no_data_metrics" in captured["body"]
    assert "Admission/pool pressure" in captured["body"]
    assert "admission queued max=2.50/s avg=0.40/s" in captured["body"]
    assert "Daemon memory growth" in captured["body"]
    assert "Host disk I/O pressure" in captured["body"]
    assert "host disk I/O max=500.00 MiB/s avg=100.00 MiB/s ratio=5.00x" in captured["body"]
    assert "HDFS DataNode I/O pressure" in captured["body"]
    assert "remote/local reads ratio=2.60x" in captured["body"]
    assert "observed" in captured["body"]
    assert "correlated_signals" in captured["body"]
    assert "context_only_signals" in captured["body"]
    assert "Cluster runtime context" in captured["body"]
    assert "Runtime verdict" in captured["body"]
    assert "Correlated runtime context" in captured["body"]
    assert "Cluster/runtime context aligns with profile evidence" in captured["body"]
    assert "correlated signals: Daemon memory growth, Network I/O spike" in captured["body"]
    assert "+4 triage score points from 2 correlated runtime metric signal" in captured["body"]
    assert (
        "Raw metric points and per-point times are intentionally excluded" not in captured["body"]
    )
    assert "Correlation" in captured["body"]
    assert "correlated" in captured["body"]
    assert "moderate" in captured["body"]
    assert "Interpretation" in captured["body"]
    assert "prioritize reducing exchange rows or payload" in captured["body"]
    assert "host network I/O max=200.00 MiB/s avg=20.00 MiB/s ratio=10.00x" in captured["body"]
    assert "Runtime Diagnosis" in captured["body"]
    assert "Network/exchange pressure may be relevant" in captured["body"]
    assert "Runtime diagnosis" in captured["body"]
    assert "Query context" in captured["body"]
    assert "query window" in captured["body"]
    assert "2026-05-04T10:00:00Z to 2026-05-04T10:05:15Z" in captured["body"]
    assert "pool</span><strong>root.analytics" in captured["body"]
    assert "admission wait" in captured["body"]
    assert "2.50s" in captured["body"]
    assert "42.00 GiB" in captured["body"]
    assert "18.00 GiB" in captured["body"]
    assert "plausible follow up" in captured["body"]
    assert "downstream exchange backpressure" in captured["body"]
    assert "2026-05-04T09:59:00Z" not in captured["body"]
    assert "Metadata facts" in captured["body"]
    assert '<details class="analysis-subdetails" aria-label="Runtime signals">' in analysis_html
    assert '<details class="analysis-subdetails" aria-label="Query context">' in analysis_html
    assert '<details class="analysis-subdetails" aria-label="Runtime diagnosis">' in analysis_html
    assert (
        '<details class="analysis-subdetails" aria-label="Cluster runtime context">'
        in analysis_html
    )
    assert '<details class="analysis-subdetails" aria-label="Runtime metrics">' in analysis_html
    cm_metrics_html = analysis_html[
        analysis_html.index(
            '<details class="analysis-subdetails" aria-label="Runtime metrics">'
        ) : analysis_html.index('<details class="analysis-subdetails" aria-label="Metadata facts">')
    ]
    assert cm_metrics_html.count('<div class="batch-table-wrap">') == 2
    assert "<th>Metric status</th><th>Metric basis</th><th>Correlation</th>" in cm_metrics_html
    assert "Correlated runtime metric signals" in cm_metrics_html
    assert "<summary>All collected runtime metrics</summary>" not in cm_metrics_html
    assert "<h3>All collected runtime metrics</h3>" in cm_metrics_html
    primary_cm_metrics_html = cm_metrics_html[
        : cm_metrics_html.index("<h3>All collected runtime metrics</h3>")
    ]
    assert "Daemon memory growth" in primary_cm_metrics_html
    assert "Network I/O spike" in primary_cm_metrics_html
    assert "Admission/pool pressure" not in primary_cm_metrics_html
    assert "Host disk I/O pressure" not in primary_cm_metrics_html
    assert "<h3>Limitations</h3>" not in cm_metrics_html
    cluster_context_html = analysis_html[
        analysis_html.index(
            '<details class="analysis-subdetails" aria-label="Cluster runtime context">'
        ) : analysis_html.index(
            '<details class="analysis-subdetails" aria-label="Runtime signals">'
        )
    ]
    assert "<h3>Limitations</h3>" not in cluster_context_html
    assert '<details class="analysis-subdetails" aria-label="Metadata facts">' in analysis_html
    assert 'action="/query/details/abc%3Adef/python-report"' in captured["body"]
    assert 'action="/query/details/abc%3Adef/llm-report"' in captured["body"]
    assert "Generate Python report</button>" in captured["body"]
    assert "Generate LLM narrative</button>" in captured["body"]
    assert "disabled>Generate Python report</button>" not in captured["body"]
    assert "Generate Python report + optimizer" not in captured["body"]
    assert 'action="/query/details/abc%3Adef/llm-actions"' not in captured["body"]
    assert 'action="/batch/case/specific-query/python-report"' not in captured["body"]
    assert "/tmp/" not in captured["body"]
    assert str(case_dir) not in captured["body"]
    assert "case_dir" not in captured["body"]
    assert "analysis_facts.md" not in captured["body"]


def test_web_specific_query_report_action_builds_validated_pipeline_command(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text("- Cardinality anomalies: 2\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        model="configured-model",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        (case_dir / module.PYTHON_REPORT_NAME).write_text(
            "# Report\n\nSafe body.\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            cmd, 0, stdout="raw stdout hidden", stderr="raw stderr hidden"
        )

    status, location = module.start_specific_query_report_job(
        "abc:def", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "query_report"
    assert snapshot.status == "ok"
    cmd, kwargs = calls[0]
    assert command_uses_role(cmd, "report")
    assert command_args(cmd, "report")[0] == str(case_dir)
    assert cmd[cmd.index("--mode") + 1] == "admin"
    assert cmd[cmd.index("--model") + 1] == "configured-model"
    assert cmd[cmd.index("--out") + 1] == module.PYTHON_REPORT_NAME
    assert cmd[cmd.index("--validation-mode") + 1] == "strict"
    assert "--no-llm" in cmd
    assert kwargs["env"] is not None
    assert (case_dir / module.PYTHON_REPORT_VALIDATION_MARKER).is_file()

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Open full report" in captured["body"]
    assert 'href="/query/details/abc%3Adef/python-report"' in captured["body"]
    assert ">Report</h1>" in captured["body"]
    assert "Safe body." in captured["body"]
    assert "raw stdout hidden" not in captured["body"]


def test_web_job_failure_status_is_sanitized(monkeypatch):
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    monkeypatch.setenv("CM_TOKEN", "secret-token")

    def fake_analysis(*args, **kwargs):
        raise module.WebError("Authorization: Bearer secret-token")

    status, location = module.start_analyze_job(
        {"query_id": ["abc:def"], "mode": ["admin"]},
        settings,
        store,
        analysis_func=fake_analysis,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    payload = json.loads(module.render_job_status_json(snapshot))
    assert payload["status"] == "failed"
    assert "secret-token" not in payload["error"]
    assert "<redacted>" in payload["error"]


def test_web_unknown_job_status_is_safe_json():
    module = load_web_module()

    payload = json.loads(module.render_job_status_json(None))

    assert payload["status"] == "failed"
    assert payload["error"] == "Analysis job was not found."
    assert payload["result_html"] == ""


def test_web_batch_route_renders_empty_state_without_configured_summary():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch?query_group=suspicious"
    request.write_html = write_html

    request.do_GET()

    assert captured["status"] == 200
    assert '<form id="batch-form"' in captured["body"]
    assert '<button class="run-button" type="submit">Run scan</button>' in captured["body"]
    assert "Search depth" in captured["body"]
    assert "Scan date" not in captured["body"]
    assert "Scan Hour" not in captured["body"]
    assert "Advanced scan parameters" not in captured["body"]
    assert "Queries to scan" not in captured["body"]


def test_web_batch_route_ignores_query_param_file_paths(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps({"selected_count": 1, "cases": [{"query_id": "evil<script>"}]}),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/batch?path={summary}"
    request.write_html = write_html

    request.do_GET()

    assert captured["status"] == 200
    assert "evil" not in captured["body"]
    assert '<form id="batch-form"' in captured["body"]


def test_web_batch_route_renders_configured_summary_safely(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 2,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "query_type_filter": "QUERY",
                "include_running": False,
                "only_running": False,
                "user_filter_present": True,
                "pool_filter_present": False,
                "duration_filter": "server-side<script>",
                "jobs": 2,
                "total_seconds": 12.3,
                "warnings": ["scan warning <script>"],
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "duration_sec": 90.5,
                        "cardinality_anomaly_count": 5,
                        "memory_anomaly_count": 4,
                        "zero_row_estimate_gap_count": 2,
                        "zero_memory_estimate_gap_count": 1,
                        "backend_data_skew": True,
                        "host_tail_candidate_count": 0,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "report_generated": True,
                        "report_validation_status": "passed",
                        "score_reasons": ["cardinality <script>alert(1)</script>"],
                        "case_dir": "/tmp/query-doctor-secret-case",
                    },
                    {
                        "case_index": 2,
                        "query_id": "bbbbbbbbbbbbbbbb:0000000000000002",
                        "score": 0,
                        "duration_sec": None,
                        "backend_data_skew": "unknown",
                        "host_tail_candidate_count": 0,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "report_generated": False,
                        "report_validation_status": "failed_partial_untrusted",
                        "score_reasons": ["no analyzer-supported suspicious facts"],
                    },
                    {
                        "case_index": 3,
                        "query_id": "ghi<script>",
                        "score": 7,
                        "duration_sec": 12,
                        "cardinality_anomaly_count": 1,
                        "memory_anomaly_count": 2,
                        "backend_data_skew": False,
                        "host_tail_candidate_count": 1,
                        "collection_status": "ok<script>",
                        "analysis_status": "failed",
                        "metadata_status": "failed",
                        "report_generated": False,
                        "report_validation_status": "not_run",
                        "score_reasons": ["analysis failed <script>"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch?query_group=suspicious"
    request.write_html = write_html

    request.do_GET()
    body = captured["body"]
    styles = layout.render_shared_styles()

    assert captured["status"] == 200
    assert '<section id="query-inbox-status"' in body
    assert "<h1>Partial inbox</h1>" not in body
    assert '<span class="badge amber">partial</span>' in body
    assert '<span class="query-inbox-metric"><strong>cases</strong><span>3</span></span>' in body
    assert (
        '<span class="query-inbox-metric"><strong>suspicious</strong><span>1</span></span>' in body
    )
    assert '<span class="query-inbox-metric"><strong>warnings</strong><span>1</span></span>' in body
    assert '<div class="query-inbox-scope" aria-label="Current Query Inbox scope">' in body
    assert '<div class="query-inbox-active-filters"' not in body
    assert "<strong>Source</strong><span>Cloudera Manager</span>" in body
    assert "<strong>Target</strong><span>Finished queries</span>" in body
    assert "<strong>Window</strong><span>Last 60 min</span>" in body
    assert "<strong>Query type</strong><span>QUERY</span>" in body
    assert "<strong>Owner/pool</strong><span>user set</span>" in body
    assert "<strong>Duration</strong><span>server-side&lt;script&gt;</span>" in body
    assert (
        '<details class="query-inbox-controls" '
        'aria-label="Query Inbox scan scope and saved views">' in body
    )
    assert (
        '<summary class="query-inbox-controls-summary">'
        "<span>Scan scope and saved views</span></summary>" in body
    )
    assert '<nav class="query-inbox-view-presets" aria-label="Query Inbox view presets">' in body
    assert '<span class="query-inbox-view-label">Views</span>' in body
    assert (
        'class="query-inbox-view-preset" '
        'href="/?query_group=bad&amp;result_sort=duration#recent-results">'
        'Needs attention + duration<span class="query-inbox-preset-count">1</span></a>' in body
    )
    assert (
        'class="query-inbox-view-preset query-inbox-view-preset--zero" '
        'href="/?query_group=all&amp;result_sort=impact&amp;only_with_spills=on#recent-results">'
        'Spill + impact<span class="query-inbox-preset-count">0</span></a>' in body
    )
    assert '<a class="query-inbox-action" href="/#new-scan" data-open-new-scan>New scan</a>' in body
    assert '<nav class="query-inbox-presets" aria-label="Query Inbox presets">' not in body
    assert '<nav class="batch-filter-tabs" aria-label="Query result filters">' in body
    assert (
        '<a class="batch-filter-link batch-filter-link--active" '
        'href="/?query_group=suspicious#recent-results">Worth reviewing <span>1</span></a>' in body
    )
    assert (
        '<a class="batch-filter-link" '
        'href="/?query_group=bad#recent-results">Needs attention <span>1</span></a>' in body
    )
    assert "query_group=workloads" not in body
    assert (
        'href="/?query_group=suspicious&only_with_spills=on#recent-results" '
        'aria-label="Only queries with spills" aria-pressed="false">' in body
    )
    assert '<a class="nav-link nav-link--active" href="/">Query Inbox</a>' in body
    assert "Finished Queries" in body
    assert "Batch query triage" not in body
    assert '<form id="batch-form"' in body
    assert (
        '<details id="new-scan" class="panel batch-run-panel batch-run-panel--disclosure"' in body
    )
    assert '<summary class="batch-run-summary"><span>New scan</span>' in body
    assert body.index('id="query-inbox-status"') < body.index('id="recent-results"')
    assert body.index('id="recent-results"') < body.index('id="new-scan"')
    assert body.index('class="batch-table-wrap"') < body.index('<form id="batch-form"')
    assert (
        '<details id="recent-results" class="panel batch-panel batch-results-disclosure" '
        'aria-label="finished queries" open data-results-disclosure>' in body
    )
    assert '<summary class="batch-head"><div><h1>Finished Queries</h1></div></summary>' in body
    assert 'class="batch-table-wrap"' in body
    assert 'class="batch-table batch-results-table batch-results-table--suspicious"' in body
    assert 'class="batch-table-legend"' in body
    assert body.index('class="batch-table-wrap"') < body.index('class="batch-table-legend"')
    assert body.index('class="batch-table-wrap"') < body.index('class="batch-results-context"')
    assert (
        '<section id="scan-context" class="batch-results-context" aria-label="Scan context">'
        in (body)
    )
    assert "<h2>Scan context</h2>" in body
    assert "Coverage, scan notes, and compact follow-up links for this result set." in body
    assert 'class="batch-result-filters batch-result-filters--query-toolbar"' in body
    assert "batch-filtered-result-summary" not in body
    assert_css_contains(styles, ".batch-table-wrap{margin-top:14px;")
    assert_css_contains(styles, ".query-inbox-status-main{display:grid;")
    assert_css_contains(styles, ".query-inbox-metrics{display:flex;")
    assert_css_contains(styles, ".query-inbox-scope{display:flex;")
    assert_css_contains(styles, ".query-inbox-scope-item{display:inline-flex;")
    assert_css_contains(styles, ".query-inbox-active-filters{display:flex;")
    assert_css_contains(styles, ".query-inbox-active-filter-chip{display:inline-flex;")
    assert_css_contains(styles, ".query-inbox-controls{margin-top:10px;")
    assert_css_contains(styles, ".query-inbox-controls-summary{display:flex;")
    assert_css_contains(
        styles,
        ".query-inbox-window-filter,.query-inbox-query-type-filter,.query-inbox-time-range-filter{display:inline-flex;",
    )
    assert_css_contains(
        styles,
        ".query-inbox-window-input,.query-inbox-query-type-input,.query-inbox-time-range-input{width:78px;",
    )
    assert_css_contains(styles, ".query-inbox-query-type-input{width:92px;")
    assert_css_contains(styles, ".query-inbox-time-range-input{width:138px;")
    assert_css_contains(styles, ".query-inbox-view-presets{display:flex;")
    assert_css_contains(styles, ".query-inbox-view-label{color:var(--muted);")
    assert_css_contains(styles, ".query-inbox-preset,.query-inbox-view-preset{display:inline-flex;")
    assert_css_contains(
        styles,
        ".query-inbox-preset--active,.query-inbox-view-preset--active{background:var(--accent-soft);",
    )
    assert_css_contains(styles, ".query-inbox-presets{display:flex;")
    assert_css_contains(
        styles,
        ".query-inbox-preset--zero,.query-inbox-view-preset--zero{border-color:var(--border);",
    )
    assert_css_contains(styles, ".batch-filter-link--zero,.batch-readiness-toggle--zero{")
    assert_css_contains(styles, ".batch-active-filter-summary{display:inline-flex;")
    assert_css_contains(styles, ".batch-filtered-result-summary{display:inline-flex;")
    assert_css_contains(styles, ".empty-cell-message{display:block}")
    assert_css_contains(styles, ".empty-cell-actions{display:inline-flex;")
    assert_css_contains(styles, ".batch-result-filter-row--sort{align-items:center;")
    assert_css_contains(styles, ".batch-sort-controls{display:flex;")
    assert_css_contains(styles, ".batch-sort-toggle{min-height:32px;")
    assert_css_contains(styles, ".batch-result-filter-row--state{align-items:center;")
    assert_css_contains(styles, ".batch-view-state-summary{display:flex;")
    assert_css_contains(styles, ".batch-view-state-chip{display:inline-flex;")
    assert_css_contains(styles, ".batch-view-state-link{display:inline-flex;")
    assert_css_contains(styles, ".batch-view-state-url{flex:1 1 180px;")
    assert_css_contains(styles, ".batch-table-legend{display:flex;flex-wrap:wrap;")
    assert ".batch-table-legend{display:grid;grid-template-columns:auto" not in compact_css(styles)
    assert_css_contains(styles, ".batch-results-context{margin-top:12px;")
    assert_css_contains(styles, ".batch-results-context-head{display:grid;")
    assert_css_contains(styles, ".batch-result-summary{display:inline-flex;")
    assert_css_contains(styles, ".batch-results-context-body{display:grid;gap:8px;")
    assert_css_contains(
        styles,
        ".batch-head-actions{display:flex;align-items:center;justify-content:flex-end;",
    )
    assert_css_contains(
        styles, ".batch-context-block{display:grid;grid-template-columns:minmax(92px,auto)"
    )
    assert_css_contains(styles, ".batch-query-groups{display:flex;flex-wrap:wrap;")
    assert_css_contains(styles, ".batch-result-filters--query-toolbar{align-items:flex-start;")
    assert "batch-filter-more" not in styles
    assert_css_contains(
        styles,
        ".batch-cell--duration{color:var(--strong);font-variant-numeric:tabular-nums;",
    )
    assert_css_contains(styles, ".batch-cell--badge{font-family:var(--sans);")
    assert_css_contains(styles, ".batch-mini-badge--status{justify-content:center;")
    assert_css_contains(styles, ".action-candidate-card--primary{border:1px solid var(--border);")
    assert (
        ".action-candidate-card--primary{border:1px solid var(--border);border-left" not in styles
    )
    assert ".workload-action-signal" not in styles
    assert ".workload-action-plan" not in styles
    assert_css_contains(
        styles,
        ".batch-results-disclosure>.batch-head::after,.batch-notices>summary::after,"
        ".batch-scan-details>summary::after",
    )
    assert_css_contains(styles, "width:30px;height:30px;margin-left:auto;")
    assert_css_contains(
        styles,
        ".compact-details summary::after,.action-outcome-control summary::after",
    )
    assert_css_contains(styles, "border-left:1px solid var(--border);")
    assert_css_contains(styles, ".batch-results-disclosure>.batch-head{cursor:pointer;")
    assert_css_contains(styles, ".batch-head .badge{max-width:min(360px,45vw);overflow:hidden;")
    assert "batch-metrics" not in styles
    assert "batch-metric" not in styles
    assert_css_contains(styles, "@media(max-width:760px){.page{padding:0;")
    assert_css_contains(
        styles,
        "@media(max-width:760px){.page{padding:0;width:100%;max-width:100%;overflow-x:hidden}"
        ".page>.panel,.page>section,.page>details{padding:12px 14px}",
    )
    assert_css_contains(
        styles,
        ".header-actions{display:grid;grid-template-columns:minmax(0,1fr) auto 44px;",
    )
    assert_css_contains(
        styles,
        ".top-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;",
    )
    assert_css_contains(styles, ".brand-subtitle{display:none}")
    assert_css_contains(styles, ".batch-result-filter-label{display:none}")
    assert_css_contains(styles, ".batch-sort-controls{flex:1 1 100%}")
    assert_css_contains(styles, ".batch-sort-toggle{flex:1 1 calc(50% - 6px)}")
    assert_css_contains(styles, ".batch-view-state-summary{flex:1 1 100%}")
    assert_css_contains(styles, ".batch-view-state-chip{flex:1 1 calc(50% - 5px);")
    assert_css_contains(styles, ".batch-view-state-link,.batch-view-state-url{flex:1 1 100%;")
    assert_css_contains(styles, ".query-inbox-view-presets{gap:5px;")
    assert_css_contains(styles, ".query-inbox-view-label{flex:1 1 100%;")
    assert_css_contains(styles, ".query-inbox-preset,.query-inbox-view-preset{flex:1 1 auto;")
    assert_css_contains(styles, ".batch-notices{display:block;")
    assert_css_contains(styles, ".batch-notices>summary{cursor:pointer;")
    assert_css_contains(styles, ".batch-notices-body{display:grid;")
    assert_css_contains(styles, ".batch-notice-row{display:grid;")
    assert_css_contains(styles, ".batch-notice-row--single{grid-template-columns:minmax(0,1fr)}")
    assert_css_contains(styles, ".batch-filter-tabs{flex-wrap:wrap;overflow-x:visible;")
    assert_css_contains(styles, ".batch-scan-details{margin-bottom:12px;")
    assert_css_contains(
        styles,
        ".batch-table th,.batch-table td{border-bottom:1px solid var(--border);padding:5px 8px;",
    )
    assert_css_contains(
        styles,
        ".batch-results-disclosure>.batch-results-body>.batch-table-wrap{border:0;overflow:visible}",
    )
    assert_css_contains(
        styles,
        ".batch-results-disclosure>.batch-results-body>.batch-table-wrap>"
        ".batch-results-table tr{display:grid;grid-template-columns:34px minmax(0,1fr);",
    )
    assert_css_contains(
        styles,
        '.batch-results-table--optimization td:nth-child(9)::before{content:"Rewrite support"}',
    )
    assert_css_contains(styles, ".batch-results-table--workloads{table-layout:fixed}")
    assert_css_contains(
        styles,
        '.batch-results-table--workloads td:nth-child(3)::before{content:"Priority"}',
    )
    assert_css_contains(
        styles,
        '.batch-results-table--workloads td:nth-child(7)::before{content:"Next"}',
    )
    assert_css_contains(
        styles,
        ".batch-results-table--bad td:nth-child(7)::before,"
        ".batch-results-table--suspicious td:nth-child(7)::before,"
        '.batch-results-table--all td:nth-child(7)::before{content:"Next"}',
    )
    assert_css_contains(styles, ".batch-cell--query-id{width:1%;min-width:190px;max-width:264px;")
    assert_css_contains(styles, ".batch-cell--user{width:1%;min-width:76px;max-width:152px;")
    assert_css_contains(styles, ".batch-cell--summary{width:100%;min-width:320px;")
    assert 'class="batch-cell--compact"' in body
    assert 'class="batch-cell--query-id"' in body
    assert '<span class="batch-result-summary">Scanned 3</span>' in body
    assert "<span>Scanned</span>" not in body
    assert "<span>Needs attention</span>" not in body
    assert "<span>Worth reviewing</span>" not in body
    assert "<span>Rewrite</span>" not in body
    assert "<span>Stats</span>" not in body
    assert '<p class="batch-notice-line" aria-label="Scan warnings">' in body
    assert "<summary>Scan warnings</summary>" not in body
    assert "<strong>Scan warnings</strong>" in body
    assert "scan warning &lt;script&gt;" in body
    assert "scan warning <script>" not in body
    assert 'class="batch-context-block batch-context-scan-details"' in body
    assert '<div class="batch-context-title">Coverage</div>' in body
    assert "Analyzed 2 cases" in body
    assert "Analyzed: 2" not in body
    assert "total" not in body
    assert "CM inspected" not in body
    assert "server-side<script>" not in body
    assert "cardinality <script>alert(1)</script>" not in body
    assert "cardinality &lt;script&gt;alert(1)&lt;/script&gt;" not in body
    assert "ghi&lt;script&gt;" not in body
    assert "ghi<script>" not in body
    assert "ok&lt;script&gt;" not in body
    assert "ok<script>" not in body
    assert "analysis failed &lt;script&gt;" not in body
    assert '<td class="batch-cell--query-id">aaaaaaaaaaaaaaaa:0000000000000001</td>' in body
    assert (
        "<th>Rank</th><th>Finding</th><th>Query ID</th><th>User</th><th>Priority</th>"
        "<th>Duration</th></tr>" in body
    )
    assert '<td class="batch-cell--compact batch-cell--duration">90.5s</td>' in body
    assert 'class="batch-cell--compact batch-cell--badge batch-cell--priority"' in body
    assert '<span class="batch-priority batch-severity--suspicious">' in body
    assert '<span class="batch-priority-dot" aria-hidden="true"></span>' in body
    assert "batch-priority-badge" not in body
    assert 'title="table stats not checked">Not checked</span>' not in body
    assert "<strong>Priority</strong><span>Label + score</span>" in body
    assert "<strong>Finding</strong><span>Main signal; opens selected-case Details</span>" in body
    assert "<th>Finding</th>" in body
    assert "<th>Summary</th>" not in body
    assert "<th>At a glance</th>" not in body
    assert "<th>Collection</th>" not in body
    assert "<th>Analysis</th>" not in body
    assert "<th>Report</th>" not in body
    assert "<th>Reasons</th>" not in body
    assert "<th>Details</th>" not in body
    assert "<th>Card</th>" not in body
    assert "<th>Mem</th>" not in body
    assert "<th>Skew</th>" not in body
    assert "<th>Tail</th>" not in body
    assert "cardinality 5; memory 4" in body
    assert "no positive analyzer signals" not in body
    assert "Needs attention <span>1</span>" in body
    assert "Worth reviewing <span>1</span>" in body
    assert "Rewrite opportunities <span>0</span>" not in body
    assert "Stats to check <span>0</span>" not in body
    assert "View" in body
    assert "<summary>More filters</summary>" not in body
    assert '<details class="batch-filter-more">' not in body
    assert "Result group" not in body
    assert "Filters" in body
    assert "Good queries" not in body
    assert (
        'batch-filter-link batch-filter-link--active" href="/?query_group=suspicious#recent-results"'
        in body
    )
    assert "batch-severity--suspicious" in body
    assert ">Medium · 22</span>" in body
    assert "22 high" not in body
    assert "22 suspicious" not in body
    assert 'class="batch-mini-badge batch-severity--clean"' not in body
    assert '<span class="batch-mini-badge batch-severity--clean">0</span>' not in body
    assert "0 clean" not in body
    assert 'class="batch-mini-badge batch-severity--failed"' not in body
    assert ">7</span>" not in body
    assert "7 failed" not in body
    assert 'class="batch-row batch-row--failed"' not in body
    assert 'class="batch-mini-badge batch-status--failed"' not in body
    assert ">validated report<" not in body
    assert "partial untrusted" not in body
    assert 'data-href="/batch/case/case-001"' in body
    assert '<a class="batch-finding-link" href="/batch/case/case-001">' in body
    assert "onclick=" not in body
    assert "onkeydown=" not in body
    assert "window.location.href=this.dataset.href" not in body
    assert 'data-href="/batch/case/case-002"' not in body
    assert 'data-href="/batch/case/case-003"' not in body
    assert "/tmp/query-doctor-secret-case" not in body
    assert 'href="/tmp' not in body
    assert "Run diagnosis" not in body


def test_web_query_inbox_ready_state_uses_safe_summary_counts(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 2,
                "summaries_inspected": 5,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 90,
                        "score_severity": "high",
                        "duration_sec": 44,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                    {
                        "case_index": 2,
                        "query_id": "bbbbbbbbbbbbbbbb:0000000000000002",
                        "score": 0,
                        "score_severity": "clean",
                        "duration_sec": 1,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    body = module.render_batch_page(
        module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    )

    assert "<h1>Inbox ready</h1>" not in body
    assert '<span class="badge green">ready</span>' in body
    assert '<span class="badge green">ready</span>' in body
    assert '<span class="query-inbox-metric"><strong>cases</strong><span>2</span></span>' in body
    assert '<span class="query-inbox-metric"><strong>bad</strong><span>1</span></span>' in body
    assert (
        '<a class="batch-filter-link batch-filter-link--active" '
        'href="/?query_group=bad#recent-results">Needs attention <span>1</span></a>' in body
    )
    assert "query_group=stats" not in body
    assert (
        'href="/?query_group=bad&only_with_spills=on#recent-results" '
        'aria-label="Only queries with spills" aria-pressed="false">' in body
    )
    assert "batch_summary.json" not in body
    assert str(summary) not in body


def test_web_query_inbox_freshness_keeps_current_summary_ready():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = {
        "selected_count": 1,
        "summaries_inspected": 1,
        "to_time": "2026-07-03T09:30:00Z",
        "recent_window_minutes": 60,
        "cases": [
            {
                "case_index": 1,
                "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                "score": 90,
                "score_severity": "high",
                "duration_sec": 44,
                "collection_status": "ok",
                "analysis_status": "ok",
            }
        ],
    }

    status = query_inbox_status_from_summary(
        summary,
        now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    body = render_query_inbox_status(status)

    assert status.state == "ready"
    assert "<h1>Inbox ready</h1>" not in body
    assert '<span class="badge green">ready</span>' in body
    assert '<span class="badge green">ready</span>' in body
    assert (
        '<span class="query-inbox-metric"><strong>freshness</strong><span>fresh</span></span>'
        in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>age</strong><span>30 min ago</span></span>'
        in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>window</strong><span>60 min</span></span>' in body
    )


def test_web_query_inbox_stale_state_uses_safe_summary_window():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = {
        "selected_count": 1,
        "summaries_inspected": 1,
        "to_time": "2026-07-03T05:00:00Z",
        "recent_window_minutes": 60,
        "cases": [
            {
                "case_index": 1,
                "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                "score": 90,
                "score_severity": "high",
                "duration_sec": 44,
                "collection_status": "ok",
                "analysis_status": "ok",
            }
        ],
    }

    status = query_inbox_status_from_summary(
        summary,
        now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    body = render_query_inbox_status(status)

    assert status.state == "stale"
    assert "<h1>Inbox stale</h1>" not in body
    assert '<span class="badge amber">stale</span>' in body
    assert '<span class="badge amber">stale</span>' in body
    assert "Use New scan to refresh source, time range, workflow, or query type." in body
    assert (
        '<span class="query-inbox-metric"><strong>freshness</strong><span>stale</span></span>'
        in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>age</strong><span>5 h ago</span></span>' in body
    )
    assert "batch_summary.json" not in body
    assert "/tmp/" not in body


def test_web_query_inbox_unknown_freshness_does_not_claim_stale():
    from query_doctor.web.ui.query_inbox import query_inbox_status_from_summary

    summary = {
        "selected_count": 1,
        "summaries_inspected": 1,
        "to_time": "synthetic",
        "recent_window_minutes": 60,
        "warnings": ["Synthetic demo data only."],
        "cases": [
            {
                "case_index": 1,
                "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                "score": 90,
                "score_severity": "high",
                "duration_sec": 44,
                "collection_status": "ok",
                "analysis_status": "ok",
            }
        ],
    }

    status = query_inbox_status_from_summary(
        summary,
        now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )

    assert status.state == "partial"
    assert ("freshness", "stale") not in status.metrics
    assert ("freshness", "fresh") not in status.metrics


def test_web_query_inbox_scope_uses_materialized_safe_fields():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = {
        "selected_count": 1,
        "summaries_inspected": 1,
        "query_profile_source": "impala",
        "from_time": "2026-07-03T08:00:00Z",
        "to_time": "2026-07-03T09:30:00Z",
        "query_type_filter": "all",
        "include_running": True,
        "only_running": False,
        "user_filter_present": True,
        "pool_filter_present": True,
        "duration_filter": "/tmp/private<script>",
        "cases": [
            {
                "case_index": 1,
                "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                "score": 90,
                "score_severity": "high",
                "duration_sec": 44,
                "collection_status": "ok",
                "analysis_status": "ok",
            }
        ],
    }

    body = render_query_inbox_status(
        query_inbox_status_from_summary(
            summary,
            now=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        )
    )

    assert "<strong>Source</strong><span>Direct Impala</span>" in body
    assert "<strong>Target</strong><span>Finished + running</span>" in body
    assert "<strong>Window</strong><span>2026-07-03 08:00-09:30 UTC</span>" in body
    assert "<strong>Query type</strong><span>All supported</span>" in body
    assert "<strong>Owner/pool</strong><span>user + pool set</span>" in body
    assert "<strong>Duration</strong><span>&lt;local path hidden&gt;&lt;script&gt;</span>" in body
    assert "/tmp/private" not in body
    assert "<script" not in body
    assert "aaaaaaaaaaaaaaaa:0000000000000001" not in body


def test_web_query_inbox_scope_filters_render_and_preserve_url_state(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "query_type_filter": "QUERY",
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "duration_sec": 90.5,
                        "cardinality_anomaly_count": 2,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "score_reasons": ["cardinality anomaly detected"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = (
        "/batch?query_group=suspicious&inbox_source=cm&inbox_workflow=finished"
        "&inbox_window=60&inbox_query_type=QUERY"
    )
    request.write_html = write_html

    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert '<div class="query-inbox-scope-filters" aria-label="Query Inbox scope filters">' in body
    assert (
        '<div class="query-inbox-active-filters" aria-label="Active Query Inbox filters">' in body
    )
    assert '<span class="query-inbox-active-filter-title">Active filters</span>' in body
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Source</strong>'
        "<span>Cloudera Manager</span></span>" in body
    )
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Workflow</strong>'
        "<span>Finished queries</span></span>" in body
    )
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Window</strong>'
        "<span>Last 60 min</span></span>" in body
    )
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Query type</strong>'
        "<span>QUERY</span></span>" in body
    )
    assert '<span class="query-inbox-scope-filter-label">Source</span>' in body
    assert '<span class="query-inbox-scope-filter-label">Workflow</span>' in body
    assert '<span class="query-inbox-scope-filter-label">Window</span>' in body
    assert '<span class="query-inbox-scope-filter-label">Query type</span>' in body
    assert (
        '<form class="query-inbox-window-filter" method="get" action="/#recent-results" '
        'aria-label="Set Query Inbox window">' in body
    )
    assert '<input type="hidden" name="query_group" value="suspicious">' in body
    assert '<input type="hidden" name="inbox_source" value="cm">' in body
    assert '<input type="hidden" name="inbox_workflow" value="finished">' in body
    assert '<input type="hidden" name="inbox_query_type" value="QUERY">' in body
    assert (
        '<label class="query-inbox-window-label" for="query-inbox-window-minutes">Minutes</label>'
        in body
    )
    assert (
        '<input id="query-inbox-window-minutes" class="query-inbox-window-input" '
        'name="inbox_window" type="number" min="1" max="525600" step="1" '
        'value="60" inputmode="numeric">' in body
    )
    assert '<button class="query-inbox-window-button" type="submit">Apply</button>' in body
    assert (
        '<form class="query-inbox-query-type-filter" method="get" action="/#recent-results" '
        'aria-label="Set Query Inbox query type">' in body
    )
    assert (
        '<label class="query-inbox-query-type-label" for="query-inbox-query-type">Type</label>'
        in body
    )
    assert (
        '<input id="query-inbox-query-type" class="query-inbox-query-type-input" '
        'name="inbox_query_type" type="text" pattern="[A-Za-z][A-Za-z0-9_]{0,31}" '
        'maxlength="32" autocomplete="off" value="QUERY" inputmode="latin">' in body
    )
    assert '<button class="query-inbox-query-type-button" type="submit">Apply</button>' in body
    assert (
        'query-inbox-scope-filter--active" href="/?query_group=suspicious'
        "&amp;inbox_source=cm&amp;inbox_workflow=finished&amp;inbox_window=60"
        '&amp;inbox_query_type=QUERY#recent-results" '
        'aria-current="page">Cloudera Manager</a>' in body
    )
    assert (
        '<a class="batch-filter-link" '
        'href="/?query_group=all&inbox_source=cm&inbox_workflow=finished'
        '&inbox_window=60&inbox_query_type=QUERY#recent-results">All analyzed ' in body
    )
    assert (
        'batch-filter-link batch-filter-link--active" href="/?query_group=suspicious'
        "&inbox_source=cm&inbox_workflow=finished&inbox_window=60"
        '&inbox_query_type=QUERY#recent-results"' in body
    )
    assert (
        'href="/?query_group=suspicious&inbox_source=cm&inbox_workflow=finished'
        '&inbox_window=60&inbox_query_type=QUERY&only_with_spills=on#recent-results" '
        'aria-label="Only queries with spills"' in body
    )
    assert "aaaaaaaaaaaaaaaa:0000000000000001" in body


def test_web_query_inbox_view_presets_apply_shareable_filter_sort_state(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 3,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "query_type_filter": "QUERY",
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "owner-pool-spill-attention",
                        "score": 90,
                        "score_severity": "high",
                        "duration_sec": 120,
                        "user": "owner_tagged",
                        "pool": "root.shared",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                    },
                    {
                        "case_index": 2,
                        "query_id": "rewrite-only",
                        "score": 50,
                        "score_severity": "suspicious",
                        "duration_sec": 45,
                        "pool": "root.shared",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "query_optimization_candidate": {
                            "score": 70,
                            "tier": "high",
                            "confidence": "medium",
                            "impact": "high",
                            "reasons": ["large exchange/intermediate data movement"],
                            "counter_signals": [],
                            "suggested_review_areas": ["exchange payload"],
                        },
                    },
                    {
                        "case_index": 3,
                        "query_id": "clean-row",
                        "score": 0,
                        "score_severity": "clean",
                        "duration_sec": 5,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    status, body = handler_get_html(
        handler,
        "/batch?query_group=all&inbox_source=cm&inbox_workflow=finished"
        "&inbox_window=60&inbox_query_type=QUERY&owner_filter=tagged"
        "&pool_filter=tagged&result_sort=priority",
    )

    assert status == 200
    assert '<nav class="query-inbox-view-presets" aria-label="Query Inbox view presets">' in body
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Sort</strong>'
        "<span>Priority</span></span>" in body
    )
    assert (
        'class="query-inbox-view-preset query-inbox-view-preset--active" '
        'href="/?query_group=all&amp;inbox_source=cm&amp;inbox_workflow=finished'
        "&amp;inbox_window=60&amp;inbox_query_type=QUERY&amp;owner_filter=tagged"
        '&amp;pool_filter=tagged&amp;result_sort=priority#recent-results" '
        'aria-current="page">Owner/pool + priority'
        '<span class="query-inbox-preset-count">1</span></a>' in body
    )
    assert (
        'class="query-inbox-view-preset" '
        'href="/?query_group=bad&amp;inbox_source=cm&amp;inbox_workflow=finished'
        '&amp;inbox_window=60&amp;inbox_query_type=QUERY&amp;result_sort=duration#recent-results">'
        'Needs attention + duration<span class="query-inbox-preset-count">1</span></a>' in body
    )
    assert (
        'class="query-inbox-view-preset" '
        'href="/?query_group=all&amp;inbox_source=cm&amp;inbox_workflow=finished'
        "&amp;inbox_window=60&amp;inbox_query_type=QUERY&amp;result_sort=impact"
        '&amp;only_with_spills=on#recent-results">Spill + impact'
        '<span class="query-inbox-preset-count">1</span></a>' in body
    )
    assert (
        'class="query-inbox-view-preset" '
        'href="/?query_group=optimization&amp;inbox_source=cm&amp;inbox_workflow=finished'
        '&amp;inbox_window=60&amp;inbox_query_type=QUERY&amp;result_sort=priority#recent-results">'
        'Rewrite + priority<span class="query-inbox-preset-count">1</span></a>' in body
    )
    assert (
        'query-inbox-scope-filter--active" href="/?query_group=all'
        "&amp;owner_filter=tagged&amp;pool_filter=tagged&amp;result_sort=priority"
        "&amp;inbox_source=cm&amp;inbox_workflow=finished&amp;inbox_window=60"
        '&amp;inbox_query_type=QUERY#recent-results" '
        'aria-current="page">Cloudera Manager</a>' in body
    )
    assert '<input type="hidden" name="owner_filter" value="tagged">' in body
    assert '<input type="hidden" name="pool_filter" value="tagged">' in body
    assert '<input type="hidden" name="result_sort" value="priority">' in body


def test_web_query_inbox_result_toolbar_summarizes_shareable_view_state(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 3,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "query_type_filter": "QUERY",
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "owner-pool-spill-attention",
                        "score": 90,
                        "score_severity": "high",
                        "duration_sec": 120,
                        "user": "owner_tagged",
                        "pool": "root.shared",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                    },
                    {
                        "case_index": 2,
                        "query_id": "review-only",
                        "score": 50,
                        "score_severity": "suspicious",
                        "duration_sec": 45,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                    {
                        "case_index": 3,
                        "query_id": "clean-row",
                        "score": 0,
                        "score_severity": "clean",
                        "duration_sec": 5,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    status, body = handler_get_html(
        handler,
        "/batch?query_group=all&inbox_source=cm&inbox_workflow=finished"
        "&inbox_window=60&inbox_query_type=QUERY&owner_filter=tagged"
        "&pool_filter=tagged&result_sort=priority&only_with_spills=on"
        "&private_state=secret",
    )

    assert status == 200
    assert '<div class="batch-result-filter-row batch-result-filter-row--state">' in body
    assert (
        '<span class="batch-view-state-summary" aria-label="Current result view state">'
        '<span class="batch-view-state-chip"><strong>Group</strong>'
        '<span class="batch-view-state-value">All analyzed</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Source</strong>'
        '<span class="batch-view-state-value">Cloudera Manager</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Target</strong>'
        '<span class="batch-view-state-value">Finished queries</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Window</strong>'
        '<span class="batch-view-state-value">Last 60 min</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Query type</strong>'
        '<span class="batch-view-state-value">QUERY</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Result</strong>'
        '<span class="batch-view-state-value">Spill evidence</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Filters</strong>'
        '<span class="batch-view-state-value">Owner tagged; Pool tagged</span></span>' in body
    )
    assert (
        '<span class="batch-view-state-chip"><strong>Sort</strong>'
        '<span class="batch-view-state-value">Priority</span></span>' in body
    )
    assert (
        '<a class="batch-view-state-link" href="/?query_group=all&amp;inbox_source=cm'
        "&amp;inbox_workflow=finished&amp;inbox_window=60&amp;inbox_query_type=QUERY"
        "&amp;result_sort=priority&amp;owner_filter=tagged&amp;pool_filter=tagged"
        '&amp;only_with_spills=on#recent-results">View link</a>' in body
    )
    assert (
        '<code class="batch-view-state-url">/?query_group=all&amp;inbox_source=cm'
        "&amp;inbox_workflow=finished&amp;inbox_window=60&amp;inbox_query_type=QUERY"
        "&amp;result_sort=priority&amp;owner_filter=tagged&amp;pool_filter=tagged"
        "&amp;only_with_spills=on#recent-results</code>" in body
    )
    assert "private_state" not in body
    assert "secret" not in body


def test_web_query_inbox_time_range_scope_filters_render_and_preserve_url_state(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "from_time": "2026-07-03T08:00:00Z",
                "to_time": "2026-07-03T09:30:00Z",
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "duration_sec": 90.5,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = (
        "/batch?query_group=suspicious&inbox_source=cm&inbox_workflow=finished"
        "&inbox_from=2026-07-03T08:00Z&inbox_to=2026-07-03T09:30Z"
    )
    request.write_html = write_html

    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Window</strong>'
        "<span>2026-07-03T08:00Z to 2026-07-03T09:30Z</span></span>" in body
    )
    assert (
        '<form class="query-inbox-time-range-filter" method="get" action="/#recent-results" '
        'aria-label="Set Query Inbox UTC time range">' in body
    )
    assert '<input type="hidden" name="query_group" value="suspicious">' in body
    assert '<input type="hidden" name="inbox_source" value="cm">' in body
    assert '<input type="hidden" name="inbox_workflow" value="finished">' in body
    assert '<input type="hidden" name="inbox_window"' not in body
    assert (
        '<label class="query-inbox-time-range-label" for="query-inbox-from">From UTC</label>'
        in body
    )
    assert (
        '<input id="query-inbox-from" class="query-inbox-time-range-input" '
        'name="inbox_from" type="text" '
        'pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z" '
        'maxlength="17" autocomplete="off" value="2026-07-03T08:00Z" '
        'placeholder="YYYY-MM-DDTHH:MMZ">' in body
    )
    assert (
        '<input id="query-inbox-to" class="query-inbox-time-range-input" '
        'name="inbox_to" type="text" '
        'pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z" '
        'maxlength="17" autocomplete="off" value="2026-07-03T09:30Z" '
        'placeholder="YYYY-MM-DDTHH:MMZ">' in body
    )
    assert '<button class="query-inbox-time-range-button" type="submit">Apply</button>' in body
    assert (
        'query-inbox-scope-filter--active" href="/?query_group=suspicious'
        "&amp;inbox_source=cm&amp;inbox_workflow=finished&amp;inbox_from=2026-07-03T08%3A00Z"
        '&amp;inbox_to=2026-07-03T09%3A30Z#recent-results" '
        'aria-current="page">Cloudera Manager</a>' in body
    )
    assert (
        '<a class="batch-filter-link" '
        'href="/?query_group=all&inbox_source=cm&inbox_workflow=finished'
        '&inbox_from=2026-07-03T08%3A00Z&inbox_to=2026-07-03T09%3A30Z#recent-results"' in body
    )
    assert (
        'batch-filter-link batch-filter-link--active" href="/?query_group=suspicious'
        "&inbox_source=cm&inbox_workflow=finished&inbox_from=2026-07-03T08%3A00Z"
        '&inbox_to=2026-07-03T09%3A30Z#recent-results"' in body
    )
    assert (
        'href="/?query_group=suspicious&inbox_source=cm&inbox_workflow=finished'
        "&inbox_from=2026-07-03T08%3A00Z&inbox_to=2026-07-03T09%3A30Z"
        '&only_with_spills=on#recent-results" aria-label="Only queries with spills"' in body
    )
    assert "aaaaaaaaaaaaaaaa:0000000000000001" in body


def test_web_online_history_details_route_uses_materialized_analysis_cache(
    tmp_path,
    monkeypatch,
):
    module = load_web_module()
    from dataclasses import replace

    from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
    from query_doctor.recent.history_store import history_record_from_candidate
    from query_doctor.recent.profile_budget import (
        ANALYSIS_CACHE_SCHEMA_VERSION,
        PROFILE_ARTIFACT_SCHEMA_VERSION,
        PROFILE_STATUS_ANALYZED,
        RecentAnalysisCacheRecord,
        RecentProfileArtifactRecord,
    )
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    history_db = tmp_path / "recent-history.sqlite"
    store = SqliteRecentHistoryStore(history_db)
    record = history_record_from_candidate(
        RecentQueryCandidate(
            summary=CMQuerySummary(
                query_id="query-online-details",
                duration_ms=90_000,
                status="FINISHED",
                query_type="QUERY",
                statement="SELECT secret_column FROM private_table",
            ),
            selected=True,
            reason="selected: long-running summary",
            sql_verb="SELECT",
        ),
        engine="impala",
        source_kind="cm",
        source_key="cm:cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    store.upsert_summaries(
        [
            replace(record, profile_status=PROFILE_STATUS_ANALYZED),
            replace(
                record,
                query_id="query-newer-unprocessed",
                recorded_at_iso="2026-07-03T11:05:00+00:00",
                profile_status="not_collected",
            ),
        ]
    )
    store.store_analysis_cache_records(
        [
            RecentAnalysisCacheRecord(
                schema_version=ANALYSIS_CACHE_SCHEMA_VERSION,
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                query_id="query-online-details",
                profile_fingerprint="profile_fingerprint_v1",
                analyzer_contract="profile_digest_analysis_json_v1",
                recorded_at_iso="2026-07-03T10:10:00+00:00",
                status="ready",
                payload={
                    "score": 88,
                    "score_severity": "high",
                    "score_reasons": ["runtime signal"],
                    "analysis_status": "ok",
                    "collection_status": "ok",
                    "metadata_status": "not_collected",
                    "referenced_table_count": 3,
                    "collectable_metadata_table_count": 2,
                    "collected_metadata_table_count": 0,
                    "too_large_count": 1,
                    "cm_collect_seconds": 1.25,
                    "analysis_seconds": 0.75,
                    "total_seconds": 2.0,
                    "raw_sql": "SELECT secret_column FROM private_table",
                },
            )
        ]
    )
    store.store_profile_artifact_records(
        [
            RecentProfileArtifactRecord(
                schema_version=PROFILE_ARTIFACT_SCHEMA_VERSION,
                engine="impala",
                source_kind="cm",
                source_key="cm:cluster:impala",
                query_id="query-online-details",
                profile_fingerprint="profile_fingerprint_v1",
                artifact_contract="profile_artifact_v1",
                recorded_at_iso="2026-07-03T10:10:00+00:00",
                status="available",
                storage_kind="fingerprint_only",
                storage_key="sha256_deadbeef",
                size_bytes=1024,
            )
        ]
    )
    config = tmp_path / "web-config.json"
    config.write_text(
        '{"recent_history_backend":"sqlite","recent_history_db":"recent-history.sqlite"}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    response = route_get_request(
        "/batch/case/case-001",
        module.WebSettings(config=config, repo_dir=REPO_DIR, no_llm=True),
        module.WebJobStore(),
    )
    all_recent_page = route_get_request(
        "/?history_view=all_recent&query_group=all",
        module.WebSettings(config=config, repo_dir=REPO_DIR, no_llm=True),
        module.WebJobStore(),
    )
    all_recent_details = route_get_request(
        "/batch/case/recent-case-002",
        module.WebSettings(config=config, repo_dir=REPO_DIR, no_llm=True),
        module.WebJobStore(),
    )

    assert response is not None
    assert response.status == 200
    assert all_recent_page is not None
    assert all_recent_page.status == 200
    assert 'aria-label="Online history view"' in all_recent_page.body
    assert "Details ready" in all_recent_page.body
    assert "All recent" in all_recent_page.body
    assert 'data-href="/batch/case/recent-case-002"' in all_recent_page.body
    assert all_recent_details is not None
    assert all_recent_details.status == 200
    body = response.body
    assert "query-online-details" in body
    assert "runtime signal" in body
    assert "collectable metadata tables" in body
    assert "cm collect seconds" in body
    assert "1.25" in body
    assert "secret_column" not in body
    assert "private_table" not in body
    assert "profile_fingerprint_v1" not in body
    assert "sha256_deadbeef" not in body
    assert "batch_summary.json" not in body
    assert str(history_db) not in body
    assert 'href="/batch/case/case-001/source"' not in body


def test_web_online_history_end_to_end_smoke_keeps_worker_and_readiness_raw_free(
    tmp_path,
    monkeypatch,
):
    module = load_web_module()
    from query_doctor.cli import batch_recent
    from query_doctor.cm.models import CMQuerySummary, RecentQueryCandidate
    from query_doctor.recent.history_store import (
        history_record_from_candidate,
        recent_history_source_key,
    )
    from query_doctor.recent.operator_readiness import audit_recent_history_operator_readiness
    from query_doctor.recent.postgres_readiness import SUMMARY_KIND as POSTGRES_READINESS_KIND
    from query_doctor.recent.profile_budget import ProfileBudgetPolicy, plan_recent_profile_jobs
    from query_doctor.recent.profile_worker import (
        RecentProfileWorkerJobOutcome,
        RecentProfileWorkerOptions,
        run_recent_profile_worker,
    )
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    history_db = tmp_path / "recent-history.sqlite"
    worker_out = tmp_path / "query-doctor-worker-out"
    args = batch_recent.parse_args(
        [
            "--out",
            str(worker_out),
            "--cm-url",
            "https://cm.example.net:7183",
            "--cluster",
            "cluster",
            "--service",
            "impala",
            "--metadata-mode",
            "off",
            "--recent-history-db",
            str(history_db),
        ]
    )
    config = batch_recent.build_batch_config(
        args,
        env={"CM_USERNAME": "user", "CM_PASSWORD": "secret"},
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )
    source_key = recent_history_source_key(config)
    candidate = RecentQueryCandidate(
        summary=CMQuerySummary(
            query_id="online-history-smoke-query",
            start_time="2026-07-03T10:00:00Z",
            end_time="2026-07-03T10:10:00Z",
            duration_ms=600_000,
            status="FINISHED",
            user="analyst",
            pool="root.default",
            query_type="QUERY",
            statement="SELECT secret_column FROM private_table",
        ),
        selected=True,
        reason="selected: long-running summary",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate,
        engine="impala",
        source_kind="cm",
        source_key=source_key,
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    store = SqliteRecentHistoryStore(history_db)
    store.upsert_summaries([record])
    store.enqueue_profile_jobs(
        plan_recent_profile_jobs(
            [record],
            policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
            planned_at_iso="2026-07-03T10:06:00+00:00",
        )
    )

    def processor(job, _config, _env, _repo_root):
        assert job.query_id == "online-history-smoke-query"
        return RecentProfileWorkerJobOutcome(
            status="completed",
            profile_fingerprint="sha256_online_history_smoke",
            analysis_payload={
                "score": 91,
                "score_severity": "high",
                "score_reasons": ["runtime signal"],
                "analysis_status": "ok",
                "collection_status": "ok",
                "metadata_status": "not_collected",
                "referenced_table_count": 3,
                "collectable_metadata_table_count": 2,
                "collected_metadata_table_count": 0,
                "too_large_count": 1,
                "cm_collect_seconds": 1.25,
                "analysis_seconds": 0.75,
                "total_seconds": 2.0,
                "raw_sql": "SELECT secret_column FROM private_table",
                "case_dir": "/private/tmp/query-doctor-secret",
                "profile_fingerprint": "profile_fingerprint_v1",
            },
            artifact_storage_key="sha256_online_history_smoke",
            artifact_size_bytes=4096,
        )

    worker_result = run_recent_profile_worker(
        store=store,
        config=config,
        env={"CM_USERNAME": "user", "CM_PASSWORD": "secret"},
        repo_root=REPO_DIR,
        options=RecentProfileWorkerOptions(max_jobs=1, lease_owner="worker-smoke"),
        now=datetime(2026, 7, 3, 10, 10, tzinfo=timezone.utc),
        processor=processor,
    )
    assert worker_result.jobs_completed == 1
    assert worker_result.analysis_cache_records == 1
    assert worker_result.profile_artifact_records == 1
    assert store.load_profile_jobs()[0]["status"] == "completed"

    operator_summary = tmp_path / "operator-readiness-summary.json"
    operator_payload = audit_recent_history_operator_readiness(
        postgres_readiness_summary={
            "summary_kind": POSTGRES_READINESS_KIND,
            "status": "ready",
            "backend": "postgres",
            "schema_initialized": True,
            "checks": [{"id": "schema", "status": "ready", "summary": "Schema ready"}],
            "issue_codes": [],
            "raw_output": False,
            "sensitive_value_echo": False,
        },
        profile_worker_summary=worker_result.safe_payload(),
        collector_summary={
            "summary_kind": "query_doctor_recent_history_collector_v1",
            "status": "recorded",
            "observed_at_iso": "2026-07-03T10:08:00+00:00",
            "discover_only": True,
            "history_backend": "postgres",
            "summaries_inspected": 1,
            "candidates_discovered": 1,
            "selected_count": 1,
            "summaries_recorded": 1,
            "profile_jobs_planned": 1,
            "next_step": "untrusted retained text online-history-smoke-query",
            "issue_codes": [],
            "raw_output": False,
            "sensitive_value_echo": False,
        },
    ).payload()
    operator_summary.write_text(json.dumps(operator_payload), encoding="utf-8")
    config_path = tmp_path / "web-config.json"
    config_path.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": "recent-history.sqlite",
                "recent_history_operator_readiness_summary_json": (
                    "operator-readiness-summary.json"
                ),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = module.WebSettings(config=config_path, repo_dir=REPO_DIR, no_llm=True)

    page_body = module.render_batch_page(settings)
    details_response = route_get_request("/batch/case/case-001", settings, module.WebJobStore())
    action_context = module.build_batch_case_detail_action_context(
        settings,
        "case-001",
        module.WebJobStore(),
    )

    assert details_response is not None
    assert details_response.status == 200
    assert action_context.report_allowed is False
    assert action_context.optimizer_allowed is False
    assert action_context.source_sql_available is False
    assert "Details ready" in page_body
    assert "online-history-smoke-query" in page_body
    assert 'data-href="/batch/case/case-001"' in page_body
    assert (
        '<span class="query-inbox-metric"><strong>operator readiness</strong>'
        "<span>ready</span></span>" in page_body
    )
    assert '<span class="query-inbox-metric"><strong>operator collector</strong>' in page_body
    assert '<span class="query-inbox-metric"><strong>collector observed</strong>' in page_body
    assert '<span class="query-inbox-metric"><strong>collector freshness</strong>' not in page_body
    assert '<span class="query-inbox-metric"><strong>last planning</strong>' not in page_body
    # Every shown row is analyzed, so the loop, the state breakdown and the
    # details-ready ratio would all restate the row summary.
    assert '<span class="query-inbox-metric"><strong>history rows</strong>' in page_body
    assert '<span class="query-inbox-metric"><strong>profile loop</strong>' not in page_body
    assert '<span class="query-inbox-metric"><strong>profile states</strong>' not in page_body
    assert '<span class="query-inbox-metric"><strong>details ready</strong>' not in page_body
    details_body = details_response.body
    assert "online-history-smoke-query" in details_body
    assert "runtime signal" in details_body
    assert "collectable metadata tables" in details_body
    assert "cm collect seconds" in details_body
    assert "Generate Python report</button>" not in details_body
    assert "Generate LLM narrative</button>" not in details_body
    assert "Run Query LLM optimizer" not in details_body
    assert 'href="/batch/case/case-001/source"' not in details_body
    rendered = "\n".join([page_body, details_body, json.dumps(operator_payload, sort_keys=True)])
    for unsafe in (
        "SELECT secret_column",
        "private_table",
        "/private/tmp",
        "sha256_online_history_smoke",
        "profile_fingerprint_v1",
        "operator-readiness-summary.json",
        str(history_db),
        "secret",
        "cm.example.net",
    ):
        assert unsafe not in rendered


def test_web_query_inbox_scope_filter_mismatch_hides_materialized_results(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "duration_sec": 90.5,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch?query_group=suspicious&inbox_source=impala"
    request.write_html = write_html

    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "<h1>No matching inbox scope</h1>" in body
    assert (
        '<span class="query-inbox-metric"><strong>status</strong><span>filtered</span></span>'
        in (body)
    )
    assert "Use All filters or New scan to materialize that scope." in body
    assert 'aria-current="page">Direct Impala</a>' in body
    assert '<section id="new-scan" class="panel batch-run-panel"' in body
    assert '<form id="batch-form"' in body
    assert 'id="recent-results"' not in body
    assert "Finished Queries" not in body
    assert 'data-href="/batch/case/' not in body
    assert '<td class="batch-cell--query-id">aaaaaaaaaaaaaaaa:0000000000000001</td>' not in body
    assert "batch_summary.json" not in body
    assert str(summary) not in body


def test_web_query_inbox_scope_filter_mismatch_prefills_new_scan_scope(tmp_path):
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig

    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "query_profile_source": "cm",
                "recent_window_minutes": 60,
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 22,
                        "score_severity": "suspicious",
                        "duration_sec": 90.5,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        clusters=(
            WebClusterConfig(
                key="cm",
                label="CM source",
                cm_url="https://cm.example.invalid",
                cm_cluster="cluster",
                cm_service="impala",
            ),
            WebClusterConfig(
                key="direct",
                label="Direct source",
                query_profile_source="impala",
                impala_profile_hosts=("impalad.example.invalid",),
            ),
        ),
        active_cluster_key="cm",
    )
    settings.config.write_text(
        json.dumps(
            {
                "web_advanced_settings_enabled": True,
                "web_advanced_filters": ["query_type"],
            }
        ),
        encoding="utf-8",
    )
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = (
        "/batch?query_group=suspicious&inbox_source=impala&inbox_workflow=running"
        "&inbox_window=120&inbox_query_type=DML"
    )
    request.write_html = write_html

    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "<h1>No matching inbox scope</h1>" in body
    assert (
        '<form id="batch-form" class="batch-form" method="post" action="/running/run" '
        'data-scan-target-form data-active-scan-target="running" '
        'data-diagnosis-target-field="recent">' in body
    )
    assert (
        '<option value="direct" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Direct source</option>' in body
    )
    assert '<input type="hidden" name="cluster_key" value="direct">' in body
    assert '<input type="hidden" name="engine" value="impala" data-engine-hidden>' in body
    assert (
        'name="diagnosis_workflow" value="running" data-diagnosis-workflow-choice checked' in body
    )
    assert 'type="radio" name="scan_target" value="running" data-scan-target-choice checked' in body
    assert (
        '<input type="hidden" name="scan_target" value="running" data-scan-target-hidden>' in body
    )
    assert (
        '<input type="hidden" name="inbox_source" value="impala" data-inbox-scope-hidden>' in body
    )
    assert (
        '<input type="hidden" name="inbox_workflow" value="running" data-inbox-scope-hidden>'
        in body
    )
    assert '<input type="hidden" name="inbox_window" value="120" data-inbox-scope-hidden>' in body
    assert (
        '<input type="hidden" name="inbox_query_type" value="DML" data-inbox-scope-hidden>' in body
    )
    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="120"' in body
    assert 'name="query_type" type="text" value="DML"' in body
    assert 'id="recent-results"' not in body
    assert "cm.example.invalid" not in body
    assert "impalad.example.invalid" not in body
    assert "batch_summary.json" not in body
    assert str(summary) not in body


def test_web_query_inbox_scope_filters_ignore_unsafe_url_values():
    from query_doctor.web.ui.query_inbox import (
        QueryInboxScopeFilterGroup,
        QueryInboxScopeFilters,
        QueryInboxStatus,
        query_inbox_scope_filter_query,
        query_inbox_scope_filters_from_mapping,
        render_query_inbox_status,
    )
    from query_doctor.web.ui.recent_scan_result_filters import (
        RecentScanResultFilters,
        recent_scan_result_filter_query,
        recent_scan_result_filters_from_mapping,
    )

    filters = query_inbox_scope_filters_from_mapping(
        {
            "inbox_source": ["/tmp/private"],
            "inbox_workflow": ["<script>"],
            "inbox_window": ["private-window"],
            "inbox_query_type": ["SELECT hidden"],
            "inbox_from": ["2026-07-03T09:30Z"],
            "inbox_to": ["2026-07-03T08:00Z"],
        }
    )

    assert query_inbox_scope_filter_query(filters) == {}

    result_filters = recent_scan_result_filters_from_mapping(
        {
            "report_filter": ["/tmp/private"],
            "optimizer_filter": ["<script>"],
            "outcome_filter": ["recorded;rm"],
            "lifecycle_filter": ["../../private"],
            "owner_filter": ["alice"],
            "pool_filter": ["root.analytics"],
        }
    )

    assert recent_scan_result_filter_query(result_filters) == {}

    filters = query_inbox_scope_filters_from_mapping(
        {
            "inbox_source": ["cloudera-manager"],
            "inbox_workflow": ["mixed"],
            "inbox_window": ["1440"],
            "inbox_query_type": ["query"],
            "inbox_from": ["2026-07-03T08:00:00Z"],
            "inbox_to": ["2026-07-03T09:30:00Z"],
        }
    )

    assert query_inbox_scope_filter_query(filters) == {
        "inbox_source": "cm",
        "inbox_workflow": "mixed",
        "inbox_query_type": "QUERY",
        "inbox_from": "2026-07-03T08:00Z",
        "inbox_to": "2026-07-03T09:30Z",
    }

    status = QueryInboxStatus(
        state="ready",
        badge_class="green",
        dot_class="",
        title="Inbox ready",
        message="Materialized raw-free cases are ranked and ready for filtering.",
        metrics=(("cases", "1"),),
        scope_filter_groups=(
            QueryInboxScopeFilterGroup(
                key="window",
                label="Window",
                param="inbox_window",
                current_value="60",
                current_label="Last 60 min",
                current_from_time="2026-07-03T08:00Z",
                current_to_time="2026-07-03T09:30Z",
            ),
            QueryInboxScopeFilterGroup(
                key="query_type",
                label="Query type",
                param="inbox_query_type",
                current_value="QUERY",
                current_label="QUERY",
            ),
        ),
    )

    body = render_query_inbox_status(
        status,
        active_group="/tmp/private-group",
        only_with_spills=True,
        scope_filters=QueryInboxScopeFilters(
            source="/tmp/private",
            workflow="<script>",
            window="private-window",
            query_type="SELECT hidden",
            from_time="2026-07-03T09:30Z",
            to_time="2026-07-03T08:00Z",
        ),
        result_filters=RecentScanResultFilters(
            report="/tmp/private",
            optimizer="<script>",
            outcome="recorded;rm",
            lifecycle="../../private",
            owner="alice",
            pool="root.analytics",
        ),
    )

    assert "inbox_source" not in body
    assert "inbox_workflow" not in body
    assert '<input type="hidden" name="query_group" value="bad">' in body
    assert '<input type="hidden" name="only_with_spills" value="on">' in body
    assert 'name="inbox_window" type="number" min="1" max="525600" step="1" value="60"' in body
    assert 'name="inbox_from"' in body
    assert 'name="inbox_to"' in body
    assert "2026-07-03T08:00Z" in body
    assert "2026-07-03T09:30Z" in body
    assert (
        'name="inbox_query_type" type="text" pattern="[A-Za-z][A-Za-z0-9_]{0,31}" '
        'maxlength="32" autocomplete="off" value="QUERY" inputmode="latin">' in body
    )
    assert "/tmp/private" not in body
    assert "private-window" not in body
    assert "SELECT hidden" not in body
    assert "<script" not in body


def test_web_query_inbox_result_filters_preserve_safe_url_state():
    from query_doctor.web.ui.query_inbox import (
        QueryInboxScopeFilterGroup,
        QueryInboxScopeFilters,
        QueryInboxStatus,
        render_query_inbox_status,
    )
    from query_doctor.web.ui.recent_scan_groups import render_result_filters
    from query_doctor.web.ui.recent_scan_result_filters import RecentScanResultFilters

    status = QueryInboxStatus(
        state="ready",
        badge_class="green",
        dot_class="",
        title="Inbox ready",
        message="Materialized raw-free cases are ranked and ready for filtering.",
        metrics=(("cases", "1"),),
        scope_filter_groups=(
            QueryInboxScopeFilterGroup(
                key="window",
                label="Window",
                param="inbox_window",
                current_value="60",
                current_label="Last 60 min",
            ),
        ),
    )
    result_filters = RecentScanResultFilters(
        report="validated",
        optimizer="ready",
        outcome="recorded",
        lifecycle="status_followup",
        owner="tagged",
        pool="tagged",
    )

    body = render_query_inbox_status(
        status,
        active_group="optimization",
        only_with_spills=True,
        scope_filters=QueryInboxScopeFilters(source="cm", window="60", query_type="QUERY"),
        result_filters=result_filters,
    )
    toolbar = render_result_filters(
        (),
        "optimization",
        only_with_spills=True,
        result_filters=result_filters,
        extra_query={"inbox_source": "cm", "inbox_window": "60", "inbox_query_type": "QUERY"},
    )

    assert (
        'href="/?query_group=optimization&inbox_source=cm'
        "&inbox_window=60&inbox_query_type=QUERY&optimizer_filter=ready"
        "&outcome_filter=recorded&lifecycle_filter=status_followup"
        "&owner_filter=tagged&pool_filter=tagged"
        '&only_with_spills=on#recent-results" '
        'aria-label="Only rows with validated reports" aria-pressed="true">' in toolbar
    )
    assert (
        'href="/?query_group=optimization&inbox_source=cm'
        "&inbox_window=60&inbox_query_type=QUERY&report_filter=validated"
        "&optimizer_filter=ready&outcome_filter=recorded"
        "&lifecycle_filter=status_followup&pool_filter=tagged"
        '&only_with_spills=on#recent-results" '
        'aria-label="Only rows with a safe owner tag" aria-pressed="true">' in toolbar
    )
    assert (
        'href="/?query_group=optimization&inbox_source=cm'
        "&inbox_window=60&inbox_query_type=QUERY&report_filter=validated"
        "&optimizer_filter=ready&outcome_filter=recorded"
        "&owner_filter=tagged&pool_filter=tagged"
        '&only_with_spills=on#recent-results" '
        'aria-label="Only rows needing status follow-up" aria-pressed="true">' in toolbar
    )
    assert (
        '<a class="batch-spill-toggle batch-clear-result-filters" '
        'href="/?query_group=optimization&amp;inbox_source=cm&amp;inbox_window=60'
        '&amp;inbox_query_type=QUERY&amp;only_with_spills=on#recent-results" '
        'aria-label="Clear active result filters">' in toolbar
    )
    assert '<span class="batch-result-filter-count">7</span>' in toolbar
    assert '<input type="hidden" name="report_filter" value="validated">' in body
    assert '<input type="hidden" name="optimizer_filter" value="ready">' in body
    assert '<input type="hidden" name="outcome_filter" value="recorded">' in body
    assert '<input type="hidden" name="lifecycle_filter" value="status_followup">' in body
    assert '<input type="hidden" name="owner_filter" value="tagged">' in body
    assert '<input type="hidden" name="pool_filter" value="tagged">' in body
    assert '<input type="hidden" name="inbox_source" value="cm">' in body


def test_web_query_inbox_result_filters_apply_to_batch_rows(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web.action_outcomes import (
        SCHEMA_VERSION,
        ActionOutcomeRecord,
        append_action_outcome,
    )
    from query_doctor.web.ui.recent_scan_result_filters import (
        owner_filter_value_token,
        pool_filter_value_token,
    )

    outcome_path = tmp_path / "action_outcomes.jsonl"
    monkeypatch.setenv("QUERY_DOCTOR_ACTION_OUTCOMES_PATH", str(outcome_path))
    outcome_fingerprint = "wf_aaaaaaaaaaaaaaaaaaaaaaaa"
    append_action_outcome(
        ActionOutcomeRecord(
            schema_version=SCHEMA_VERSION,
            recorded_at_iso="2026-07-03T00:00:00+00:00",
            workload_fingerprint=outcome_fingerprint,
            case_fingerprint="cf_aaaaaaaaaaaaaaaaaaaaaaaa",
            case_id_local="case-003",
            recommendation_id="query_optimization_review.v1",
            applied="yes",
            outcome="improved",
            verification_status="comparable_rerun",
        ),
        path=outcome_path,
    )
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 8,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "report-ready-case",
                        "score": 91,
                        "score_severity": "high",
                        "duration_sec": 80,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "report_generated": True,
                        "report_validation_status": "passed",
                    },
                    {
                        "case_index": 2,
                        "query_id": "optimizer-ready-case",
                        "score": 71,
                        "score_severity": "high",
                        "duration_sec": 70,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "optimizer_rewrite_support": {
                            "status": "guidance_only",
                            "rewriteability_bucket": "human_review_only",
                            "rewriteability_label": "Human review only",
                        },
                    },
                    {
                        "case_index": 3,
                        "query_id": "outcome-recorded-case",
                        "score": 61,
                        "score_severity": "high",
                        "duration_sec": 60,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "group_fingerprint": outcome_fingerprint,
                        "workload_group_member_count": 2,
                    },
                    {
                        "case_index": 4,
                        "query_id": "status-followup-case",
                        "score": 52,
                        "score_severity": "failed",
                        "duration_sec": 52,
                        "collection_status": "ok",
                        "analysis_status": "failed",
                    },
                    {
                        "case_index": 5,
                        "query_id": "metadata-available-case",
                        "score": 51,
                        "score_severity": "high",
                        "duration_sec": 51,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "collected",
                    },
                    {
                        "case_index": 6,
                        "query_id": "owner-tagged-case",
                        "score": 50,
                        "score_severity": "high",
                        "duration_sec": 50,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "user": "owner_tagged",
                    },
                    {
                        "case_index": 7,
                        "query_id": "pool-only-tagged-case",
                        "score": 49,
                        "score_severity": "high",
                        "duration_sec": 49,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "pool": "root.filtered",
                    },
                    {
                        "case_index": 8,
                        "query_id": "owner-and-pool-tagged-case",
                        "score": 48,
                        "score_severity": "high",
                        "duration_sec": 48,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "user": "owner_pool_tagged",
                        "pool": "root.filtered",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    status, report_body = handler_get_html(
        handler,
        "/batch?query_group=all&report_filter=validated&optimizer_filter=unsafe",
    )
    assert status == 200
    assert "report-ready-case" in report_body
    assert "optimizer-ready-case" not in report_body
    assert "outcome-recorded-case" not in report_body
    assert "optimizer_filter=unsafe" not in report_body
    assert '<span class="batch-filtered-result-summary">Showing 1 of 8 rows</span>' in report_body
    assert (
        '<span class="batch-active-filter-summary" aria-label="Active result filters">'
        "<strong>Active filters</strong><span>Validated reports</span></span>" in report_body
    )

    status, optimizer_body = handler_get_html(
        handler, "/batch?query_group=all&optimizer_filter=ready"
    )
    assert status == 200
    assert "optimizer-ready-case" in optimizer_body
    assert "report-ready-case" not in optimizer_body
    assert "outcome-recorded-case" not in optimizer_body
    assert "optimizer_filter=ready" in optimizer_body

    status, outcome_body = handler_get_html(
        handler, "/batch?query_group=all&outcome_filter=recorded"
    )
    assert status == 200
    assert "outcome-recorded-case" in outcome_body
    assert "report-ready-case" not in outcome_body
    assert "optimizer-ready-case" not in outcome_body
    assert "outcome_filter=recorded" in outcome_body

    status, status_body = handler_get_html(
        handler, "/batch?query_group=all&lifecycle_filter=status_followup"
    )
    assert status == 200
    assert "status-followup-case" in status_body
    assert "report-ready-case" not in status_body
    assert "optimizer-ready-case" not in status_body
    assert "outcome-recorded-case" not in status_body
    assert "metadata-available-case" not in status_body
    assert "lifecycle_filter=status_followup" in status_body

    status, metadata_body = handler_get_html(
        handler, "/batch?query_group=all&lifecycle_filter=metadata_available"
    )
    assert status == 200
    assert "metadata-available-case" in metadata_body
    assert "report-ready-case" not in metadata_body
    assert "status-followup-case" not in metadata_body
    assert "lifecycle_filter=metadata_available" in metadata_body

    status, clean_body = handler_get_html(handler, "/batch?query_group=all&lifecycle_filter=clean")
    assert status == 200
    assert "report-ready-case" in clean_body
    assert "optimizer-ready-case" in clean_body
    assert "outcome-recorded-case" in clean_body
    assert "metadata-available-case" in clean_body
    assert "status-followup-case" not in clean_body
    assert "lifecycle_filter=clean" in clean_body

    status, owner_body = handler_get_html(handler, "/batch?query_group=all&owner_filter=tagged")
    assert status == 200
    assert "owner-tagged-case" in owner_body
    assert "owner-and-pool-tagged-case" in owner_body
    assert "pool-only-tagged-case" not in owner_body
    assert "report-ready-case" not in owner_body
    assert "owner_filter=tagged" in owner_body

    status, pool_body = handler_get_html(handler, "/batch?query_group=all&pool_filter=tagged")
    assert status == 200
    assert "pool-only-tagged-case" in pool_body
    assert "owner-and-pool-tagged-case" in pool_body
    assert "owner-tagged-case" not in pool_body
    assert "report-ready-case" not in pool_body
    assert "pool_filter=tagged" in pool_body

    status, owner_pool_body = handler_get_html(
        handler, "/batch?query_group=all&owner_filter=tagged&pool_filter=tagged"
    )
    assert status == 200
    assert "owner-and-pool-tagged-case" in owner_pool_body
    assert "owner-tagged-case" not in owner_pool_body
    assert "pool-only-tagged-case" not in owner_pool_body
    assert "owner_filter=tagged" in owner_pool_body
    assert "pool_filter=tagged" in owner_pool_body

    owner_token = owner_filter_value_token("owner_tagged")
    status, owner_token_body = handler_get_html(
        handler, f"/batch?query_group=all&owner_filter={owner_token}"
    )
    assert status == 200
    assert "owner-tagged-case" in owner_token_body
    assert "owner-and-pool-tagged-case" not in owner_token_body
    assert "pool-only-tagged-case" not in owner_token_body
    assert f"owner_filter={owner_token}" in owner_token_body
    assert "owner_filter=owner_tagged" not in owner_token_body
    assert "Owner: owner_tagged" in owner_token_body
    assert (
        '<span class="query-inbox-active-filter-chip"><strong>Result</strong>'
        "<span>Owner: owner_tagged</span></span>" in owner_token_body
    )
    assert (
        '<span class="batch-filtered-result-summary">Showing 1 of 8 rows</span>' in owner_token_body
    )
    assert (
        '<span class="batch-active-filter-summary" aria-label="Active result filters">'
        "<strong>Active filters</strong><span>Owner: owner_tagged</span></span>" in owner_token_body
    )
    assert (
        'class="batch-spill-toggle batch-clear-result-filters" '
        'href="/?query_group=all#recent-results" '
        'aria-label="Clear active result filters"><span>Clear filters</span></a>'
        in owner_token_body
    )
    assert (
        '<span>Owner: owner_tagged</span><span class="batch-filter-count">1</span>'
        in owner_token_body
    )

    pool_token = pool_filter_value_token("root.filtered")
    status, pool_token_body = handler_get_html(
        handler, f"/batch?query_group=all&pool_filter={pool_token}"
    )
    assert status == 200
    assert "pool-only-tagged-case" in pool_token_body
    assert "owner-and-pool-tagged-case" in pool_token_body
    assert "owner-tagged-case" not in pool_token_body
    assert f"pool_filter={pool_token}" in pool_token_body
    assert "pool_filter=root.filtered" not in pool_token_body
    assert "Pool: root.filtered" in pool_token_body
    assert (
        '<span>Pool: root.filtered</span><span class="batch-filter-count">2</span>'
        in pool_token_body
    )

    status, spill_body = handler_get_html(handler, "/batch?query_group=all&only_with_spills=on")
    assert status == 200
    assert (
        '<span class="batch-active-filter-summary" aria-label="Active result filters">'
        "<strong>Active filters</strong><span>Spill evidence</span></span>" in spill_body
    )
    assert '<span class="batch-filtered-result-summary">Showing 0 of 8 rows</span>' in spill_body
    assert '<span class="empty-cell-actions">' in spill_body
    assert (
        'class="empty-cell-action" href="/?query_group=all#recent-results">'
        "Remove spill filter</a>" in spill_body
    )

    owner_pool_token = owner_filter_value_token("owner_pool_tagged")
    status, owner_pool_token_body = handler_get_html(
        handler,
        f"/batch?query_group=all&owner_filter={owner_pool_token}&pool_filter={pool_token}",
    )
    assert status == 200
    assert "owner-and-pool-tagged-case" in owner_pool_token_body
    assert "owner-tagged-case" not in owner_pool_token_body
    assert "pool-only-tagged-case" not in owner_pool_token_body
    assert f"owner_filter={owner_pool_token}" in owner_pool_token_body
    assert f"pool_filter={pool_token}" in owner_pool_token_body

    status, owner_empty_body = handler_get_html(
        handler, f"/batch?query_group=stats&owner_filter={owner_token}"
    )
    assert status == 200
    assert (
        "No stats to check matched the active result filters (Owner: owner_tagged). "
        "Clear those filters to see all rows in this group." in owner_empty_body
    )
    assert (
        'class="empty-cell-action" href="/?query_group=stats#recent-results">'
        "Clear filters</a>" in owner_empty_body
    )
    assert (
        'class="empty-cell-action" href="/?query_group=all&amp;owner_filter='
        f'{owner_token}#recent-results">Show All analyzed</a>' in owner_empty_body
    )
    assert "Owner value" not in owner_empty_body


def test_web_query_inbox_result_sort_controls_apply_to_materialized_rows(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 3,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "old-long-low-priority",
                        "score": 20,
                        "score_severity": "suspicious",
                        "duration_sec": 90,
                        "start_time": "2026-07-03T08:00:00Z",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                    {
                        "case_index": 2,
                        "query_id": "new-short-medium-priority",
                        "score": 40,
                        "score_severity": "suspicious",
                        "duration_sec": 10,
                        "start_time": "2026-07-03T10:00:00Z",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                    {
                        "case_index": 3,
                        "query_id": "middle-high-priority",
                        "score": 95,
                        "score_severity": "high",
                        "duration_sec": 30,
                        "start_time": "2026-07-03T09:00:00Z",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    status, default_body = handler_get_html(handler, "/?query_group=all")
    assert status == 200
    assert default_body.index("old-long-low-priority") < default_body.index(
        "new-short-medium-priority"
    )
    assert (
        'class="batch-spill-toggle batch-sort-toggle batch-spill-toggle--active" '
        'href="/?query_group=all#recent-results" aria-pressed="true"' in default_body
    )

    status, duration_body = handler_get_html(handler, "/?query_group=all&result_sort=duration")
    assert status == 200
    assert duration_body.index("old-long-low-priority") < duration_body.index(
        "middle-high-priority"
    )
    assert duration_body.index("middle-high-priority") < duration_body.index(
        "new-short-medium-priority"
    )
    assert (
        'class="batch-spill-toggle batch-sort-toggle batch-spill-toggle--active" '
        'href="/?query_group=all&amp;result_sort=duration#recent-results" '
        'aria-pressed="true" title="Longest duration first"><span>Duration</span></a>'
        in duration_body
    )
    assert (
        'href="/?query_group=all&result_sort=duration&only_with_spills=on#recent-results"'
        in duration_body
    )
    assert 'href="/?query_group=bad&result_sort=duration#recent-results"' in duration_body

    status, priority_body = handler_get_html(handler, "/?query_group=all&result_sort=priority")
    assert status == 200
    assert priority_body.index("middle-high-priority") < priority_body.index(
        "new-short-medium-priority"
    )
    assert priority_body.index("new-short-medium-priority") < priority_body.index(
        "old-long-low-priority"
    )

    status, start_body = handler_get_html(handler, "/?query_group=all&result_sort=start")
    assert status == 200
    assert start_body.index("new-short-medium-priority") < start_body.index("middle-high-priority")
    assert start_body.index("middle-high-priority") < start_body.index("old-long-low-priority")

    status, invalid_body = handler_get_html(handler, "/?query_group=all&result_sort=private-value")
    assert status == 200
    assert "result_sort=private-value" not in invalid_body
    assert (
        'class="batch-spill-toggle batch-sort-toggle batch-spill-toggle--active" '
        'href="/?query_group=all#recent-results" aria-pressed="true"' in invalid_body
    )


def test_web_query_inbox_result_sort_controls_apply_to_workload_groups(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 6,
                "cases": [
                    {
                        "case_index": index,
                        "query_id": f"workload-member-{index}",
                        "score": 40,
                        "score_severity": "suspicious",
                        "duration_sec": 10,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                    for index in range(1, 7)
                ],
                "workload_groups": {
                    "schema_version": 1,
                    "groups": [
                        {
                            "fingerprint": "wf_aaaaaaaaaaaaaaaaaaaaaaaa",
                            "member_count": 4,
                            "aggregates": {
                                "duration_sec_p95": 10,
                                "duration_sec_total": 1000,
                                "score_top": "suspicious",
                            },
                        },
                        {
                            "fingerprint": "wf_bbbbbbbbbbbbbbbbbbbbbbbb",
                            "member_count": 3,
                            "aggregates": {
                                "duration_sec_p95": 90,
                                "duration_sec_total": 120,
                                "score_top": "high",
                            },
                        },
                        {
                            "fingerprint": "wf_cccccccccccccccccccccccc",
                            "member_count": 2,
                            "aggregates": {
                                "duration_sec_p95": 80,
                                "duration_sec_total": 160,
                                "score_top": "clean",
                            },
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    status, default_body = handler_get_html(handler, "/?query_group=workloads")
    assert status == 200
    assert default_body.index("wf_aaaaaaaa") < default_body.index("wf_cccccccc")
    assert default_body.index("wf_cccccccc") < default_body.index("wf_bbbbbbbb")

    status, duration_body = handler_get_html(
        handler, "/?query_group=workloads&result_sort=duration"
    )
    assert status == 200
    assert duration_body.index("wf_bbbbbbbb") < duration_body.index("wf_cccccccc")
    assert duration_body.index("wf_cccccccc") < duration_body.index("wf_aaaaaaaa")
    assert (
        'href="/?query_group=workloads&amp;result_sort=duration#recent-results" '
        'aria-pressed="true" title="Longest duration first"><span>Duration</span></a>'
        in duration_body
    )

    status, priority_body = handler_get_html(
        handler, "/?query_group=workloads&result_sort=priority"
    )
    assert status == 200
    assert priority_body.index("wf_bbbbbbbb") < priority_body.index("wf_aaaaaaaa")
    assert priority_body.index("wf_aaaaaaaa") < priority_body.index("wf_cccccccc")


def test_web_query_inbox_owner_pool_value_filters_use_opaque_links():
    from query_doctor.web.ui.query_inbox import query_inbox_status_from_summary
    from query_doctor.web.ui.recent_scan_groups import render_result_filters
    from query_doctor.web.ui.recent_scan_result_filters import (
        RecentScanResultFilters,
        owner_filter_value_token,
        pool_filter_value_token,
    )

    summary = {
        "selected_count": 2,
        "cases": [
            {
                "case_index": 1,
                "query_id": "owner-tagged-case",
                "score": 90,
                "score_severity": "high",
                "duration_sec": 90,
                "collection_status": "ok",
                "analysis_status": "ok",
                "user": "owner_tagged",
            },
            {
                "case_index": 2,
                "query_id": "pool-tagged-case",
                "score": 80,
                "score_severity": "high",
                "duration_sec": 80,
                "collection_status": "ok",
                "analysis_status": "ok",
                "pool": "root.filtered",
            },
        ],
    }

    status = query_inbox_status_from_summary(summary)
    body = render_result_filters(status.result_rows, "all")

    owner_token = owner_filter_value_token("owner_tagged")
    pool_token = pool_filter_value_token("root.filtered")
    assert f"owner_filter={owner_token}#recent-results" in body
    assert f"pool_filter={pool_token}#recent-results" in body
    assert "Owner: owner_tagged" in body
    assert "Pool: root.filtered" in body
    assert 'Owner: owner_tagged</span><span class="batch-filter-count">1</span>' in body
    assert 'Pool: root.filtered</span><span class="batch-filter-count">1</span>' in body
    assert "owner_filter=owner_tagged" not in body
    assert "pool_filter=root.filtered" not in body

    filtered_body = render_result_filters(
        status.result_rows,
        "all",
        result_filters=RecentScanResultFilters(owner=owner_token),
        extra_query={"owner_filter": owner_token},
    )

    assert (
        '<a class="batch-filter-link" href="/?query_group=bad&owner_filter='
        f'{owner_token}#recent-results">Needs attention <span>1</span></a>' in filtered_body
    )
    assert (
        '<a class="batch-filter-link batch-filter-link--active" '
        f'href="/?query_group=all&owner_filter={owner_token}#recent-results">'
        "All analyzed <span>1</span></a>" in filtered_body
    )


def test_web_query_inbox_scope_redacts_existing_materialized_index_values():
    from query_doctor.web.ui.query_inbox import (
        query_inbox_status_from_summary,
        render_query_inbox_status,
    )

    summary = {
        "materialized_case_index": {
            "schema_version": 1,
            "source": {"query_profile_source": "/tmp/private-host"},
            "scope": {
                "recent_window_minutes": 30,
                "query_type_filter": "QUERY<script>",
                "include_running": False,
                "user_filter_present": False,
                "pool_filter_present": False,
                "duration_filter": "/tmp/private/case_dir SELECT secret FROM t",
            },
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                    "score": 90,
                    "score_severity": "high",
                    "duration_sec": 44,
                    "collection_status": "ok",
                    "analysis_status": "ok",
                }
            ],
        }
    }

    body = render_query_inbox_status(query_inbox_status_from_summary(summary))

    assert "<strong>Window</strong><span>Last 30 min</span>" in body
    assert "<strong>Owner/pool</strong><span>all users/pools</span>" in body
    assert "<strong>Source</strong>" not in body
    assert "&lt;local path hidden&gt;" in body
    assert "[SQL hidden]" in body
    assert "/tmp/private" not in body
    assert "case_dir" not in body
    assert "SELECT secret" not in body
    assert "<script" not in body
    assert "aaaaaaaaaaaaaaaa:0000000000000001" not in body


def test_web_query_inbox_new_scan_prefills_refresh_scope_from_summary(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "web_advanced_settings_enabled": True,
                "web_advanced_filters": ["query_type"],
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "query_profile_source": "impala",
                "recent_window_minutes": 45,
                "min_duration_sec": 8.25,
                "query_type_filter": "QUERY",
                "include_running": True,
                "only_running": True,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                        "score": 90,
                        "score_severity": "high",
                        "duration_sec": 44,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=config, batch_summary=summary)

    body = module.render_batch_page(settings)

    assert (
        '<form id="batch-form" class="batch-form" method="post" action="/running/run" '
        'data-scan-target-form data-active-scan-target="running" '
        'data-diagnosis-target-field="recent">' in body
    )
    assert (
        'name="diagnosis_workflow" value="running" data-diagnosis-workflow-choice checked' in body
    )
    assert 'type="radio" name="scan_target" value="running" data-scan-target-choice checked' in body
    assert (
        '<input type="hidden" name="scan_target" value="running" data-scan-target-hidden>' in body
    )
    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="45"' in body
    assert 'name="min_duration_sec" type="number" min="0" step="0.001" value="8.25"' in body
    assert 'name="query_type" type="text" value="QUERY"' in body
    assert '<input type="hidden" name="engine" value="impala" data-engine-hidden>' in body


def test_web_query_inbox_new_scan_selects_unique_matching_source_cluster(tmp_path):
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig

    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "query_profile_source": "cm",
                "recent_window_minutes": 90,
                "include_running": False,
                "only_running": False,
                "cases": [
                    {
                        "case_index": 1,
                        "score": 80,
                        "score_severity": "high",
                        "duration_sec": 120,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        clusters=(
            WebClusterConfig(
                key="cm",
                label="CM source",
                cm_url="https://cm.example.invalid",
                cm_cluster="cluster",
                cm_service="impala",
            ),
            WebClusterConfig(
                key="direct",
                label="Direct source",
                query_profile_source="impala",
                impala_profile_hosts=("impalad.example.invalid",),
            ),
        ),
        active_cluster_key="direct",
    )

    body = module.render_batch_page(settings)

    assert (
        '<option value="cm" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">CM source</option>' in body
    )
    assert '<input type="hidden" name="cluster_key" value="cm">' in body
    assert '<input type="hidden" name="engine" value="impala" data-engine-hidden>' in body
    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="90"' in body
    assert "cm.example.invalid" not in body
    assert "impalad.example.invalid" not in body


def test_web_query_inbox_refresh_form_values_ignore_unsafe_summary_values():
    from query_doctor.web.ui.query_inbox import query_inbox_refresh_form_values_from_summary

    values = query_inbox_refresh_form_values_from_summary(
        {
            "query_profile_source": "/tmp/private-host",
            "recent_window_minutes": "120",
            "min_duration_sec": "8.25",
            "query_type_filter": "QUERY<script>",
            "cases": [
                {
                    "case_index": 1,
                    "query_id": "aaaaaaaaaaaaaaaa:0000000000000001",
                    "score": 90,
                    "score_severity": "high",
                    "duration_sec": 44,
                    "collection_status": "ok",
                    "analysis_status": "ok",
                }
            ],
        }
    )

    assert values == {}


def test_web_query_inbox_presets_normalize_filter_state():
    from query_doctor.web.ui.query_inbox import QueryInboxStatus, render_query_inbox_status

    status = QueryInboxStatus(
        state="ready",
        badge_class="green",
        dot_class="",
        title="Inbox ready",
        message="Materialized raw-free cases are ranked and ready for filtering.",
        metrics=(("cases", "1"),),
    )

    body = render_query_inbox_status(
        status,
        active_group="<script>alert(1)</script>",
        only_with_spills=True,
    )

    assert (
        '<a class="query-inbox-view-preset" '
        'href="/?query_group=bad&amp;result_sort=duration#recent-results">'
        "Needs attention + duration</a>" in body
    )
    assert "<script" not in body
    assert "&lt;script" not in body


def test_web_query_inbox_empty_state_precedes_scan_form():
    module = load_web_module()

    body = module.render_batch_page(module.WebSettings(config=Path(".query-doctor-cm.local.json")))

    assert "<h1>No materialized cases yet</h1>" in body
    assert '<span class="badge gray">empty</span>' in body
    assert body.index('id="query-inbox-status"') < body.index('id="new-scan"')
    assert 'id="recent-results"' not in body


def test_web_query_inbox_running_state_uses_job_snapshot():
    module = load_web_module()
    job = module.WebJobSnapshot(
        job_id="0123456789abcdef0123456789abcdef",
        query_id="",
        report_mode="user",
        status="running",
        stage_label="Analyzing profiles",
        progress=48,
        kind="batch",
        result_html="",
        error="",
    )

    body = module.render_batch_page(
        module.WebSettings(config=Path(".query-doctor-cm.local.json")),
        job=job,
    )

    assert "<h1>Scan running</h1>" in body
    assert '<span class="badge blue">running</span>' in body
    assert (
        '<span class="query-inbox-metric"><strong>progress</strong><span>48%</span></span>' in body
    )
    assert (
        '<span class="query-inbox-metric"><strong>stage</strong><span>Analyzing profiles</span></span>'
        in body
    )
    assert body.index('id="query-inbox-status"') < body.index('id="new-scan"')


def test_web_batch_and_running_routes_paginate_large_summary_http_level(tmp_path):
    module = load_web_module()
    summary = write_large_recent_summary(tmp_path, rows=601)
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    store.set_latest_running_summary(summary)
    handler = module.make_handler(
        settings,
        analysis_func=lambda *args, **kwargs: None,
        job_store=store,
    )

    status, first_page = handler_get_html(handler, "/?query_group=all")
    assert status == 200
    assert "Rows 1-250 of 601; page 1 of 3" in first_page
    assert first_page.count('data-href="/batch/case/') == 250
    assert 'data-href="/batch/case/case-250"' in first_page
    assert 'data-href="/batch/case/case-251"' not in first_page

    status, sorted_first_page = handler_get_html(handler, "/?query_group=all&result_sort=duration")
    assert status == 200
    assert "Rows 1-250 of 601; page 1 of 3" in sorted_first_page
    assert 'data-href="/batch/case/case-601"' in sorted_first_page
    assert 'data-href="/batch/case/case-352"' in sorted_first_page
    assert 'data-href="/batch/case/case-351"' not in sorted_first_page
    assert (
        'href="/?query_group=all&amp;result_sort=duration&amp;results_page=2#recent-results"'
        in sorted_first_page
    )

    status, second_page = handler_get_html(handler, "/?query_group=all&results_page=2")
    assert status == 200
    assert "Rows 251-500 of 601; page 2 of 3" in second_page
    assert second_page.count('data-href="/batch/case/') == 250
    assert 'data-href="/batch/case/case-250"' not in second_page
    assert 'data-href="/batch/case/case-251"' in second_page
    assert 'data-href="/batch/case/case-500"' in second_page
    assert 'data-href="/batch/case/case-501"' not in second_page

    status, running_second_page = handler_get_html(
        handler, "/running?query_group=all&results_page=2"
    )
    assert status == 200
    assert "Rows 251-500 of 601; page 2 of 3" in running_second_page
    assert running_second_page.count('data-href="/running/case/') == 250
    assert 'data-href="/running/case/case-251"' in running_second_page
    assert 'data-href="/running/case/case-500"' in running_second_page

    status, running_sorted_first_page = handler_get_html(
        handler, "/running?query_group=all&result_sort=duration"
    )
    assert status == 200
    assert 'data-href="/running/case/case-601"' in running_sorted_first_page
    assert (
        'href="/running?query_group=all&amp;result_sort=duration&amp;results_page=2#recent-results"'
        in running_sorted_first_page
    )

    for invalid_page in ("-1", "abc"):
        status, body = handler_get_html(handler, f"/?query_group=all&results_page={invalid_page}")
        assert status == 200
        assert "Rows 1-250 of 601; page 1 of 3" in body
        assert 'data-href="/batch/case/case-001"' in body
        assert 'data-href="/batch/case/case-251"' not in body

    status, last_page = handler_get_html(handler, "/?query_group=all&results_page=999999")
    assert status == 200
    assert "Rows 501-601 of 601; page 3 of 3" in last_page
    assert last_page.count('data-href="/batch/case/') == 101
    assert 'data-href="/batch/case/case-500"' not in last_page
    assert 'data-href="/batch/case/case-501"' in last_page
    assert 'data-href="/batch/case/case-601"' in last_page


def test_web_batch_route_reuses_cached_safe_summary_view_for_repeated_gets(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web.ui import recent_scan_view_cache

    recent_scan_view_cache.clear_recent_scan_summary_view_cache()
    summary = write_large_recent_summary(tmp_path, rows=601)
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    original_presenter = recent_scan_view_cache.present_recent_scan_summary
    call_count = 0

    def counted_presenter(summary_payload, *, workload_outcome_metrics=None):
        nonlocal call_count
        call_count += 1
        return original_presenter(
            summary_payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )

    monkeypatch.setattr(recent_scan_view_cache, "present_recent_scan_summary", counted_presenter)

    status, first_page = handler_get_html(handler, "/?query_group=all")
    assert status == 200
    assert 'data-href="/batch/case/case-250"' in first_page

    status, second_page = handler_get_html(handler, "/?query_group=all&results_page=2")
    assert status == 200
    assert 'data-href="/batch/case/case-251"' in second_page

    assert call_count == 1
    recent_scan_view_cache.clear_recent_scan_summary_view_cache()


def test_web_batch_safe_summary_view_cache_invalidates_on_dynamic_state(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web.action_outcomes import (
        SCHEMA_VERSION,
        ActionOutcomeRecord,
        append_action_outcome,
    )
    from query_doctor.web.ui import recent_scan_view_cache

    recent_scan_view_cache.clear_recent_scan_summary_view_cache()
    outcome_path = tmp_path / "action_outcomes.jsonl"
    monkeypatch.setenv("QUERY_DOCTOR_ACTION_OUTCOMES_PATH", str(outcome_path))
    summary = write_large_recent_summary(tmp_path, rows=601)
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    original_presenter = recent_scan_view_cache.present_recent_scan_summary
    call_count = 0

    def counted_presenter(summary_payload, *, workload_outcome_metrics=None):
        nonlocal call_count
        call_count += 1
        return original_presenter(
            summary_payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )

    monkeypatch.setattr(recent_scan_view_cache, "present_recent_scan_summary", counted_presenter)

    status, body = handler_get_html(handler, "/?query_group=all")
    assert status == 200
    assert "Rows 1-250 of 601; page 1 of 3" in body
    assert call_count == 1

    append_action_outcome(
        ActionOutcomeRecord(
            schema_version=SCHEMA_VERSION,
            recorded_at_iso="2026-07-02T00:00:00+00:00",
            workload_fingerprint="wf_aaaaaaaaaaaaaaaaaaaaaaaa",
            case_fingerprint="cf_aaaaaaaaaaaaaaaaaaaaaaaa",
            case_id_local="case-001",
            recommendation_id="query_optimization_review.v1",
            applied="yes",
            outcome="improved",
            verification_status="comparable_rerun",
        ),
        path=outcome_path,
    )

    status, body = handler_get_html(handler, "/?query_group=all")
    assert status == 200
    assert "Rows 1-250 of 601; page 1 of 3" in body
    assert call_count == 2
    recent_scan_view_cache.clear_recent_scan_summary_view_cache()


def test_web_batch_safe_summary_view_cache_invalidates_on_optimizer_artifacts(
    tmp_path, monkeypatch
):
    module = load_web_module()
    from query_doctor.web.ui import recent_scan_view_cache

    recent_scan_view_cache.clear_recent_scan_summary_view_cache()
    case_dir = tmp_path / "case-001"
    case_dir.mkdir()
    (case_dir / "analysis_facts.md").write_text(
        "# Query Doctor deterministic analysis facts\n", encoding="utf-8"
    )
    (case_dir / "original_query.sql").write_text("SELECT id FROM table_a\n", encoding="utf-8")
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "cases": [
                    {
                        "case_index": 1,
                        "case_dir": str(case_dir),
                        "query_id": "optimizer-status-case",
                        "score": 60,
                        "score_severity": "suspicious",
                        "duration_sec": 10.0,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "query_optimization_candidate": {
                            "score": 70,
                            "tier": "high",
                            "confidence": "high",
                            "impact": "high",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    original_presenter = recent_scan_view_cache.present_recent_scan_summary
    call_count = 0

    def counted_presenter(summary_payload, *, workload_outcome_metrics=None):
        nonlocal call_count
        call_count += 1
        return original_presenter(
            summary_payload,
            workload_outcome_metrics=workload_outcome_metrics,
        )

    monkeypatch.setattr(recent_scan_view_cache, "present_recent_scan_summary", counted_presenter)

    status, body = handler_get_html(handler, "/?query_group=optimization")
    assert status == 200
    assert "optimizer-status-case" in body
    assert call_count == 1

    (case_dir / "optimized_query.partial.txt").write_text(
        "partial draft hidden\n", encoding="utf-8"
    )

    status, body = handler_get_html(handler, "/?query_group=optimization")
    assert status == 200
    assert "optimizer-status-case" in body
    assert call_count == 2
    recent_scan_view_cache.clear_recent_scan_summary_view_cache()


def test_web_quickstart_corpus_cases_render_on_home_and_details(tmp_path):
    module = load_web_module()
    corpus_dir = tmp_path / "cm-corpus"
    first_case = write_manual_profile_case(
        corpus_dir,
        "aaaaaaaaaaaaaaaa:0000000000000001",
    )
    second_case = write_manual_profile_case(
        corpus_dir,
        "bbbbbbbbbbbbbbbb:0000000000000002",
        score_reason="no analyzer-supported suspicious facts",
        parsed_operators=0,
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        corpus_dir=corpus_dir,
    )
    runtime = module.prepare_corpus_summary_runtime(settings)
    assert runtime is not None
    handler = module.make_handler(runtime.settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.write_html = write_html
    request.path = "/"
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert '<summary class="batch-head"><div><h1>Exported Profiles</h1></div></summary>' in body
    assert "All analyzed <span>2</span>" in body
    assert "batch-results-table--all" in body
    assert "aaaaaaaaaaaaaaaa:0000000000000001" in body
    assert "bbbbbbbbbbbbbbbb:0000000000000002" in body
    assert str(corpus_dir) not in body
    assert str(first_case) not in body
    assert str(second_case) not in body
    assert "profile_aaaaaaaaaaaaaaaa_0000000000000001" not in body

    request.path = "/batch/case/case-001"
    request.do_GET()
    detail_body = captured["body"]

    assert captured["status"] == 200
    assert '<section id="case-overview" class="case-verdict"' in detail_body
    assert "aaaaaaaaaaaaaaaa:0000000000000001" in detail_body
    assert str(corpus_dir) not in detail_body
    assert str(first_case) not in detail_body
    assert "case_dir" not in detail_body


def test_web_batch_case_detail_renders_known_case_safely(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc<script>",
                        "score": 22,
                        "duration_sec": 90.5,
                        "cardinality_anomaly_count": 5,
                        "memory_anomaly_count": 4,
                        "backend_data_skew": True,
                        "host_tail_candidate_count": 0,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "table_stats_status": "available",
                        "report_generated": True,
                        "report_validation_status": "passed",
                        "referenced_table_count": 3,
                        "collected_metadata_table_count": 0,
                        "too_large_count": 0,
                        "cm_collect_seconds": 1.2,
                        "analysis_seconds": 2.3,
                        "report_seconds": None,
                        "total_seconds": 3.5,
                        "score_reasons": [
                            "cardinality estimate anomalies: 5",
                            "memory estimate anomalies: 4",
                            "zero/unknown row estimate gaps: 2",
                            "zero/unknown memory estimate gaps: 1",
                            "backend data skew evidence",
                            "host tail candidates: 2",
                            "column stats completeness incomplete/unknown",
                            "cardinality <script>alert(1)</script>",
                        ],
                        "case_dir": "/tmp/query-doctor-secret-case",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Finished Queries details" in body
    assert 'href="/#recent-results"' in body
    assert "case-001" in body
    assert "Use the verdict to decide priority, then read the recommended change" in body
    assert "Jump to section" not in body
    assert 'class="detail-toc"' not in body
    assert '<section id="case-overview" class="case-verdict"' in body
    assert '<section id="analysis-summary"' not in body
    assert '<section id="action-plan"' in body
    assert '<section id="diagnostics"' in body
    assert 'href="#pipeline-status"' not in body
    assert 'href="#findings"' not in body
    assert 'href="#evidence-details"' not in body
    assert '<section id="case-actions"' in body
    assert "Read-only" not in body
    assert "This page does not render raw SQL" not in body
    assert "abc&lt;script&gt;" in body
    assert "abc<script>" not in body
    assert "High priority · 22" in body
    assert "90.5" in body
    assert "signals" in body
    assert "cardinality 5; memory 4; skew observed" in body
    assert "no spill evidence observed" not in body
    assert "table stats available" in body
    assert "collection ok; analysis ok; metadata skipped; report validated report" not in body
    assert "Python Report" in body
    assert "cardinality anomalies" in body
    assert "memory anomalies" in body
    assert "zero row estimate gaps" in body
    assert "zero memory estimate gaps" in body
    assert "backend data skew" in body
    assert "host-tail candidates" in body
    assert "Python Report" in body
    assert "LLM narrative" in body
    assert "Diagnostics" in body
    assert "Verdict" in body
    assert "Recommended change" in body
    assert "confidence" in body
    assert "Score" in body
    assert body.index("Diagnostics") < body.index("Python Report")
    assert '<details class="panel docs-panel diagnostics-details">' in body
    assert '<details class="panel docs-panel diagnostics-details" open>' not in body
    diagnostics_start = body.index('<section id="diagnostics"')
    diagnostics_end = body.index('<section id="case-actions"')
    diagnostics_html = body[diagnostics_start:diagnostics_end]
    pipeline_start = diagnostics_html.index('<section id="pipeline-status"')
    runtime_start = diagnostics_html.index('<section id="runtime-evidence"')
    metadata_start = diagnostics_html.index('<section id="metadata-evidence"')
    score_start = diagnostics_html.index('<section id="score-evidence"')
    pipeline_html = diagnostics_html[pipeline_start:runtime_start]
    runtime_html = diagnostics_html[runtime_start:metadata_start]
    metadata_html = diagnostics_html[metadata_start:score_start]
    score_html = diagnostics_html[score_start:]
    assert '<section id="diagnostic-questions"' not in diagnostics_html
    assert pipeline_start < runtime_start < metadata_start < score_start
    assert "<summary>Diagnostics and evidence</summary>" in diagnostics_html
    assert '<details class="panel docs-panel diagnostics-details">' in diagnostics_html
    assert '<details class="panel docs-panel diagnostics-details" open>' not in diagnostics_html
    assert '<section id="findings"' not in diagnostics_html
    assert '<section id="evidence-details"' not in diagnostics_html
    assert '<h2 class="section-title">Pipeline</h2>' in pipeline_html
    assert '<h2 class="section-title">Runtime</h2>' in runtime_html
    assert '<h2 class="section-title">Metadata</h2>' in metadata_html
    assert '<h2 class="section-title">Score</h2>' in score_html
    assert "Why this query is suspicious" in score_html
    assert (
        '<details class="analysis-subdetails" aria-label="Why this query is suspicious">'
        not in score_html
    )
    assert "estimated rows diverge strongly from actual rows" in body
    assert "runtime memory signals look inconsistent with estimates" in body
    assert "zero, non-positive, or unavailable" in body
    assert "planning/estimate signal, but not a root-cause claim" in body
    assert "Profile work distribution across backends looks uneven" in body
    assert "tail candidates based on deterministic profile timing signals" in body
    assert "Collected metadata shows incomplete or unknown column stats" in body
    assert "This is a limitation or check, not a root-cause claim." in body
    assert "Other deterministic reason" not in body
    assert "Additional deterministic signal" in body
    assert "Runtime signals" in body
    assert '<details class="analysis-subdetails" aria-label="Runtime signals">' in runtime_html
    assert '<details class="analysis-subdetails" aria-label="Metadata facts">' in metadata_html
    assert '<h2 class="section-title">Pipeline</h2>' in pipeline_html
    assert "<summary>Query Doctor processing timings</summary>" in pipeline_html
    assert "<summary>Query Doctor processing timings</summary>" not in runtime_html
    assert "Report generation requires a complete server-owned case. Re-run analysis first." in body
    assert "Generate LLM report" not in body
    assert "Generate report + optimizer" not in body
    assert 'action="/batch/case/case-001/case-actions"' not in body
    assert 'action="/batch/case/case-001/python-report"' not in body
    assert 'name="model"' not in body
    assert 'name="case_dir"' not in body
    assert "cardinality &lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "cardinality <script>alert(1)</script>" not in body
    assert "/tmp/query-doctor-secret-case" not in body
    assert 'href="/tmp' not in body
    assert "Run diagnosis" not in body
    assert "COMPUTE STATS" not in body


def test_web_batch_case_detail_renders_owner_coordinate_guidance(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "owner-coordinate:id",
                        "user": "alice",
                        "score": 82,
                        "score_severity": "high",
                        "duration_sec": 95,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "collected",
                        "query_optimization_rank": 1,
                        "query_optimization_candidate": {
                            "score": 88,
                            "tier": "high",
                            "confidence": "medium",
                            "impact": "high",
                            "reasons": [
                                "join row expansion or cardinality mismatch with join evidence"
                            ],
                            "counter_signals": ["stats refresh may still be needed"],
                            "suggested_review_areas": ["join keys and join cardinality"],
                        },
                        "optimizer_rewrite_support": {
                            "status": "guidance_only",
                            "label": "Guidance only",
                            "reason": "Manual review only",
                            "rewriteability_bucket": "recipe_adjacent_shape",
                            "rewriteability_label": "Recipe-adjacent shape",
                            "cte_count": 2,
                            "cte_graph_shape": "linear_chain",
                            "cte_predicate_origin_status": "final_select_filter",
                        },
                        "source_locators": {
                            "query_optimization": [
                                {
                                    "id": "sql_final_select_filter",
                                    "coordinate": "line 18",
                                    "detail": "predicate near final SELECT",
                                },
                                {
                                    "id": "plan_cardinality_anomaly",
                                    "detail": "node 02 HASH JOIN (inner join, partitioned)",
                                },
                            ]
                        },
                        "case_dir": "/tmp/query-doctor-secret-case",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]
    action_plan_html = html_between(body, '<section id="action-plan"', '<section id="diagnostics"')

    assert captured["status"] == 200
    assert "Finished Queries details" in body
    assert '<a class="query-inbox-action" href="/#new-scan" data-open-new-scan>New scan</a>' in body
    assert 'class="batch-head-actions"' in body
    assert "Recommended change" in action_plan_html
    assert "Where to inspect" in action_plan_html
    assert 'class="source-location-chip source-location-chip--sql"' in action_plan_html
    assert ">line 18</span>" in action_plan_html
    assert 'class="redacted-source-map"' in action_plan_html
    assert "source text hidden" in action_plan_html
    assert "SQL: final SELECT filter (line 18): predicate near final SELECT" in action_plan_html
    assert (
        "Plan: estimate-mismatch operator: node 02 HASH JOIN (inner join, partitioned)"
        in action_plan_html
    )
    assert "Why this query matters" in action_plan_html
    assert "join row expansion or cardinality mismatch with join evidence" in action_plan_html
    assert "What to try" in action_plan_html
    assert "Try to reduce rows earlier: move the final SELECT filter closer" in action_plan_html
    assert (
        "after the change, check whether fewer rows or better estimates feed that operator"
        in action_plan_html
    )
    assert "How to verify" in action_plan_html
    assert "Compare EXPLAIN before and after the change" in action_plan_html
    assert "Review first:" not in action_plan_html
    assert "owner-coordinate:id" not in action_plan_html
    assert "/tmp/query-doctor-secret-case" not in body
    assert "case_dir" not in body


def test_web_owner_raw_source_route_renders_owner_sql_with_secret_masking(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = owner_raw_web_settings(module, tmp_path, summary)
    store = module.WebJobStore()

    response = route_get_request("/batch/case/case-001/source", settings, store)

    assert response is not None
    assert response.status == 200
    assert "Owner-only raw source" in response.body
    assert "SELECT id" in response.body
    assert "qdleak_db_20260611.qdleak_table_20260611" in response.body
    assert "supersecret" not in response.body
    assert "password = &#x27;&lt;redacted&gt;&#x27;" in response.body
    assert "/tmp/owner-raw-private" not in response.body
    assert "local path hidden" in response.body
    assert "owner-raw-source-line--highlight" in response.body
    assert "final SELECT filter" in response.body
    assert 'data-reason-code="viewer_matches_query_user"' in response.body


def test_web_owner_raw_source_route_highlights_typed_line_span_without_coordinate(tmp_path):
    module = load_web_module()
    from query_doctor.web.owner_raw_source import owner_raw_source_highlights

    summary = write_owner_raw_source_summary(
        tmp_path,
        source_locators={
            "query_optimization": [
                {
                    "id": "sql_final_select_filter",
                    "coordinate": "SELECT secret_col FROM example_guarded_table",
                    "line_span": {"start_line": 3, "end_line": 3},
                    "line_span_source": "line_range_from_sql_parser",
                    "detail": "predicate near final SELECT",
                }
            ]
        },
    )
    settings = owner_raw_web_settings(module, tmp_path, summary)
    store = module.WebJobStore()

    response = route_get_request("/batch/case/case-001/source", settings, store)
    case = json.loads(summary.read_text(encoding="utf-8"))["cases"][0]
    highlights = owner_raw_source_highlights(case)

    assert response is not None
    assert response.status == 200
    assert "owner-raw-source-line--highlight" in response.body
    assert "final SELECT filter" in response.body
    assert "secret_col" not in response.body
    assert highlights[0].line_span_source == "line_range_from_sql_parser"


def test_web_owner_raw_source_route_denies_safe_mode_and_mismatched_viewer(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    store = module.WebJobStore()
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    safe_settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="safe",
        viewer_identity=local_first_viewer_identity(("analyst",)),
    )
    mismatch_settings = owner_raw_web_settings(module, tmp_path, summary, viewer="other_user")

    safe_response = route_get_request("/batch/case/case-001/source", safe_settings, store)
    mismatch_response = route_get_request("/batch/case/case-001/source", mismatch_settings, store)

    assert safe_response is not None
    assert safe_response.status == 403
    assert 'data-reason-code="source_visibility_not_owner_raw"' in safe_response.body
    assert "Owner raw source is not enabled for this request." in safe_response.body
    assert "D3 front-door checklist" in safe_response.body
    assert mismatch_response is not None
    assert mismatch_response.status == 403
    assert 'data-reason-code="viewer_not_authorized_for_query_user"' in mismatch_response.body
    assert "The current viewer is not authorized for this selected case." in mismatch_response.body
    assert "forwarded exactly one normalized simple owner value" in mismatch_response.body
    for body in (safe_response.body, mismatch_response.body):
        assert "qdleak_db_20260611" not in body
        assert "supersecret" not in body
        assert "/tmp/owner-raw-private" not in body
        assert "other_user" not in body


def test_web_owner_raw_source_route_uses_request_viewer_identity_header(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        viewer_identity_header="X-QD-Viewer",
    )
    handler = module.make_handler(settings, job_store=module.WebJobStore())

    allowed, allowed_body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        {"Host": "127.0.0.1", "X-QD-Viewer": "analyst"},
    )
    missing, missing_body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        {"Host": "127.0.0.1"},
    )
    service, service_body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        {
            "Host": "127.0.0.1",
            "X-QD-Viewer": "impala/host.example.com@EXAMPLE.COM",
        },
    )

    assert allowed["status"] == 200
    assert "qdleak_db_20260611.qdleak_table_20260611" in allowed_body
    assert missing["status"] == 403
    assert 'data-reason-code="viewer_not_authorized_for_query_user"' in missing_body
    assert "qdleak_db_20260611" not in missing_body
    assert service["status"] == 403
    assert 'data-reason-code="viewer_not_authorized_for_query_user"' in service_body
    assert "qdleak_db_20260611" not in service_body


def test_web_owner_raw_source_route_fails_closed_for_duplicate_viewer_header(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        viewer_identity_header="X-QD-Viewer",
    )
    handler = module.make_handler(settings, job_store=module.WebJobStore())
    captured, body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        MultiValueHeaders(
            {
                "Host": ["127.0.0.1"],
                "X-QD-Viewer": ["analyst", "other_user"],
            }
        ),
    )
    assert captured["status"] == 403
    assert 'data-reason-code="viewer_not_authorized_for_query_user"' in body
    assert "qdleak_db_20260611" not in body
    assert "supersecret" not in body


def test_web_case_details_hides_owner_raw_source_link_for_duplicate_viewer_header(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        viewer_identity_header="X-QD-Viewer",
    )
    handler = module.make_handler(settings, job_store=module.WebJobStore())
    captured, body = make_handler_get_request(
        handler,
        "/batch/case/case-001",
        MultiValueHeaders(
            {
                "Host": ["127.0.0.1"],
                "X-QD-Viewer": ["analyst", "other_user"],
            }
        ),
    )
    assert captured["status"] == 200
    assert 'href="/batch/case/case-001/source"' not in body
    assert "Owner raw source" not in body
    assert "qdleak_db_20260611" not in body
    assert "supersecret" not in body


def test_web_running_owner_raw_source_route_uses_request_viewer_identity_header(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings, store = owner_raw_running_web_settings(module, tmp_path, summary)
    handler = module.make_handler(settings, job_store=store)

    allowed, allowed_body = make_handler_get_request(
        handler,
        "/running/case/case-001/source",
        {"Host": "127.0.0.1", "X-QD-Viewer": "analyst"},
    )
    missing, missing_body = make_handler_get_request(
        handler,
        "/running/case/case-001/source",
        {"Host": "127.0.0.1"},
    )
    mismatch, mismatch_body = make_handler_get_request(
        handler,
        "/running/case/case-001/source",
        {"Host": "127.0.0.1", "X-QD-Viewer": "other_user"},
    )
    duplicate, duplicate_body = make_handler_get_request(
        handler,
        "/running/case/case-001/source",
        MultiValueHeaders(
            {
                "Host": ["127.0.0.1"],
                "X-QD-Viewer": ["analyst", "other_user"],
            }
        ),
    )

    assert allowed["status"] == 200
    assert "qdleak_db_20260611.qdleak_table_20260611" in allowed_body
    assert missing["status"] == 403
    assert mismatch["status"] == 403
    assert duplicate["status"] == 403
    for body in (missing_body, mismatch_body, duplicate_body):
        assert 'data-reason-code="viewer_not_authorized_for_query_user"' in body
        assert "qdleak_db_20260611" not in body
        assert "supersecret" not in body


def test_web_running_case_details_gates_owner_raw_source_link_by_request_viewer(
    tmp_path,
):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings, store = owner_raw_running_web_settings(module, tmp_path, summary)
    handler = module.make_handler(settings, job_store=store)

    allowed, allowed_body = make_handler_get_request(
        handler,
        "/running/case/case-001",
        {"Host": "127.0.0.1", "X-QD-Viewer": "analyst"},
    )
    missing, missing_body = make_handler_get_request(
        handler,
        "/running/case/case-001",
        {"Host": "127.0.0.1"},
    )
    duplicate, duplicate_body = make_handler_get_request(
        handler,
        "/running/case/case-001",
        MultiValueHeaders(
            {
                "Host": ["127.0.0.1"],
                "X-QD-Viewer": ["analyst", "other_user"],
            }
        ),
    )

    assert allowed["status"] == 200
    assert 'href="/running/case/case-001/source"' in allowed_body
    assert "Running Queries details" in allowed_body
    assert "qdleak_db_20260611" not in allowed_body
    assert missing["status"] == 200
    assert duplicate["status"] == 200
    for body in (missing_body, duplicate_body):
        assert 'href="/running/case/case-001/source"' not in body
        assert "Owner raw source" not in body
        assert "qdleak_db_20260611" not in body
        assert "supersecret" not in body


def test_web_running_owner_raw_source_kill_switch_blocks_route_and_link(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings, store = owner_raw_running_web_settings(
        module,
        tmp_path,
        summary,
        owner_raw_source_enabled=False,
        viewer_identity_header=None,
        viewer_identity=local_first_viewer_identity(("analyst",)),
    )

    source_response = route_get_request(
        "/running/case/case-001/source",
        settings,
        store,
    )
    details_response = route_get_request("/running/case/case-001", settings, store)

    assert source_response is not None
    assert source_response.status == 403
    assert 'data-reason-code="owner_raw_source_disabled"' in source_response.body
    assert "qdleak_db_20260611" not in source_response.body
    assert "supersecret" not in source_response.body
    assert source_response.audit_event is not None
    audit_fields = dict(source_response.audit_event.fields)
    assert audit_fields["route_source"] == "running"
    assert audit_fields["reason"] == "owner_raw_source_disabled"
    assert audit_fields["source_switch"] == "disabled"
    assert details_response is not None
    assert details_response.status == 200
    assert 'href="/running/case/case-001/source"' not in details_response.body
    assert "Owner raw source" not in details_response.body
    assert "qdleak_db_20260611" not in details_response.body


def test_web_owner_raw_source_route_can_be_disabled_by_kill_switch(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        owner_raw_source_enabled=False,
        viewer_identity=local_first_viewer_identity(("analyst",)),
    )

    response = route_get_request("/batch/case/case-001/source", settings, module.WebJobStore())

    assert response is not None
    assert response.status == 403
    assert 'data-reason-code="owner_raw_source_disabled"' in response.body
    assert "The owner raw source kill switch is disabled." in response.body
    assert "network isolation, and audit checks are verified" in response.body
    assert "qdleak_db_20260611" not in response.body
    assert "supersecret" not in response.body
    assert "analyst" not in response.body
    assert response.audit_event is not None
    audit_fields = dict(response.audit_event.fields)
    assert response.audit_event.name == "owner_raw_source_access"
    assert audit_fields["allowed"] == "false"
    assert audit_fields["reason"] == "owner_raw_source_disabled"
    assert audit_fields["source_switch"] == "disabled"
    assert audit_fields["status"] == "403"


def test_web_case_details_uses_request_viewer_identity_header_for_owner_raw_link(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        viewer_identity_header="X-QD-Viewer",
    )
    handler = module.make_handler(settings, job_store=module.WebJobStore())

    allowed, allowed_body = make_handler_get_request(
        handler,
        "/batch/case/case-001",
        {"Host": "127.0.0.1", "X-QD-Viewer": "analyst"},
    )
    missing, missing_body = make_handler_get_request(
        handler,
        "/batch/case/case-001",
        {"Host": "127.0.0.1"},
    )

    assert allowed["status"] == 200
    assert 'href="/batch/case/case-001/source"' in allowed_body
    assert "qdleak_db_20260611" not in allowed_body
    assert missing["status"] == 200
    assert 'href="/batch/case/case-001/source"' not in missing_body
    assert "qdleak_db_20260611" not in missing_body


def test_web_case_details_hides_owner_raw_source_link_when_disabled(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    from query_doctor.web.viewer_identity import local_first_viewer_identity

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        owner_raw_source_enabled=False,
        viewer_identity=local_first_viewer_identity(("analyst",)),
    )

    response = route_get_request("/batch/case/case-001", settings, module.WebJobStore())

    assert response is not None
    assert response.status == 200
    assert 'href="/batch/case/case-001/source"' not in response.body
    assert "Owner raw source" not in response.body
    assert "qdleak_db_20260611" not in response.body


def test_web_owner_raw_source_audit_log_is_reason_coded_and_raw_free(tmp_path, capsys):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = owner_raw_web_settings(module, tmp_path, summary)
    handler = module.make_handler(
        settings,
        job_store=module.WebJobStore(),
        request_id_factory=lambda: "req-owner-raw-1",
    )
    captured, _body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        {"Host": "127.0.0.1"},
    )

    captured_err = capsys.readouterr().err
    assert captured["status"] == 200
    assert "event=owner_raw_source_access" in captured_err
    assert "request_id=req-owner-raw-1" in captured_err
    assert "allowed=true" in captured_err
    assert "reason=viewer_matches_query_user" in captured_err
    assert "route_source=batch" in captured_err
    assert "qdleak_db_20260611" not in captured_err
    assert "supersecret" not in captured_err
    assert "case-001" not in captured_err
    assert "analyst" not in captured_err


def test_web_owner_raw_source_duplicate_viewer_header_audit_is_raw_free(tmp_path, capsys):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        batch_summary=summary,
        source_visibility="owner_raw",
        viewer_identity_header="X-QD-Viewer",
    )
    handler = module.make_handler(
        settings,
        job_store=module.WebJobStore(),
        request_id_factory=lambda: "req-owner-raw-duplicate",
    )
    captured, _body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        MultiValueHeaders(
            {
                "Host": ["127.0.0.1"],
                "X-QD-Viewer": ["analyst", "other_user"],
            }
        ),
    )

    captured_err = capsys.readouterr().err
    assert captured["status"] == 403
    assert "event=owner_raw_source_access" in captured_err
    assert "request_id=req-owner-raw-duplicate" in captured_err
    assert "allowed=false" in captured_err
    assert "reason=viewer_not_authorized_for_query_user" in captured_err
    assert "route_source=batch" in captured_err
    assert "status=403" in captured_err
    assert "viewer_mode=unauthenticated" in captured_err
    assert "viewer_identity_source=header" in captured_err
    assert "source_switch=enabled" in captured_err
    assert "qdleak_db_20260611" not in captured_err
    assert "supersecret" not in captured_err
    assert "case-001" not in captured_err
    assert "aaaabbbbccccdddd" not in captured_err
    assert "1111222233334444" not in captured_err
    assert "analyst" not in captured_err
    assert "other_user" not in captured_err
    assert "X-QD-Viewer" not in captured_err


def test_web_owner_raw_source_route_requires_read_only_source_scope(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(
        tmp_path,
        source_sql=(
            "INSERT INTO qdleak_db_20260611.target_table "
            "SELECT id FROM qdleak_db_20260611.qdleak_table_20260611"
        ),
    )
    settings = owner_raw_web_settings(module, tmp_path, summary)

    response = route_get_request("/batch/case/case-001/source", settings, module.WebJobStore())

    assert response is not None
    assert response.status == 403
    assert 'data-reason-code="unsupported_source_scope"' in response.body
    assert "qdleak_table_20260611" not in response.body


def test_web_owner_raw_source_route_is_no_store_without_download_header(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    settings = owner_raw_web_settings(module, tmp_path, summary)
    handler = module.make_handler(settings, job_store=module.WebJobStore())
    captured, body = make_handler_get_request(
        handler,
        "/batch/case/case-001/source",
        {"Host": "127.0.0.1"},
    )

    headers = dict(captured["headers"])
    assert captured["status"] == 200
    assert headers["Cache-Control"] == "no-store"
    assert "Content-Disposition" not in headers
    assert "qdleak_db_20260611.qdleak_table_20260611" in body


def test_web_case_details_links_owner_raw_source_only_when_allowed(tmp_path):
    module = load_web_module()
    summary = write_owner_raw_source_summary(tmp_path)
    store = module.WebJobStore()
    allowed_settings = owner_raw_web_settings(module, tmp_path, summary)
    blocked_settings = owner_raw_web_settings(module, tmp_path, summary, viewer="other_user")

    allowed_response = route_get_request("/batch/case/case-001", allowed_settings, store)
    blocked_response = route_get_request("/batch/case/case-001", blocked_settings, store)

    assert allowed_response is not None
    assert allowed_response.status == 200
    assert 'href="/batch/case/case-001/source"' in allowed_response.body
    assert "Owner raw source" in allowed_response.body
    assert "qdleak_db_20260611" not in allowed_response.body
    assert blocked_response is not None
    assert blocked_response.status == 200
    assert 'href="/batch/case/case-001/source"' not in blocked_response.body
    assert "qdleak_db_20260611" not in blocked_response.body


def test_web_batch_case_detail_rank_fields_match_result_groups():
    module = load_web_module()
    summary = {
        "cases": [
            {
                "case_index": 1,
                "query_id": "low",
                "duration_sec": 300,
                "query_optimization_candidate": {
                    "score": 30,
                    "tier": "medium",
                    "impact": "medium",
                    "confidence": "medium",
                },
                "stats_optimization_candidate": {
                    "score": 90,
                    "tier": "high",
                    "impact": "high",
                    "confidence": "medium",
                },
            },
            {
                "case_index": 2,
                "query_id": "high",
                "duration_sec": 120,
                "query_optimization_candidate": {
                    "score": 80,
                    "tier": "high",
                    "impact": "high",
                    "confidence": "medium",
                },
                "stats_optimization_candidate": {
                    "score": 50,
                    "tier": "medium",
                    "impact": "medium",
                    "confidence": "medium",
                },
            },
        ]
    }

    ranked = module.case_with_detail_ranks(summary, "case-002", summary["cases"][1])

    assert ranked["_detail_overall_rank"] == 2
    assert ranked["_detail_optimization_rank"] == 1
    assert ranked["_detail_stats_rank"] == 2


def test_web_batch_case_detail_renders_safe_metadata_facts(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "## Table Metadata Context",
                "",
                "- context file: available",
                "- table metadata facts: available",
                "- tables requested: 2",
                "- read-only statements only: yes",
                "",
                "### Table: db.fact<script>",
                "",
                "- object type: table",
                "- SHOW CREATE TABLE status: ok",
                "- SHOW TABLE STATS status: ok",
                "- SHOW COLUMN STATS status: ok",
                "- table stats rows: 100",
                "- table stats row-count completeness: available",
                "- table stats size: 10MB",
                "- column stats columns observed: 3",
                "- column stats missing/unknown markers: 1",
                "- column stats completeness: incomplete/unknown",
                "- column stats columns: `id`, `amount`",
                "- file format: PARQUET",
                "- partition columns: `ds`",
                "",
                "### Table: db.safe_view",
                "",
                "- object type: view",
                "- SHOW CREATE TABLE status: ok",
                "- SHOW TABLE STATS status: not_applicable",
                "- SHOW COLUMN STATS status: not_applicable",
                "- table stats row-count completeness: not_available",
                "- column stats columns observed: 0",
                "- column stats missing/unknown markers: 0",
                "- column stats completeness: not_available",
                "- file format: unknown",
                "- partition columns: unknown",
                "",
                "## Raw Context That Must Not Render",
                "CREATE TABLE raw_secret (id int)",
                "SHOW COLUMN STATS raw_secret",
            ]
        ),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "metadata_status": "collected",
                        "table_stats_status": "missing_or_incomplete",
                        "referenced_table_count": 2,
                        "collected_metadata_table_count": 2,
                        "too_large_count": 0,
                        "score_reasons": ["cardinality anomaly"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001?analysis_facts=/tmp/evil"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Metadata facts" in body
    assert "table stats missing/incomplete" in body
    assert "table stats missing_or_incomplete" not in body
    assert "db.fact&lt;script&gt;" in body
    assert "db.fact<script>" not in body
    assert "object type" not in body
    assert "PARQUET" in body
    assert "incomplete/unknown" in body
    assert "not_applicable" in body
    assert "not_available" in body
    assert "metadata command status" in body
    assert "ok</code> for SHOW commands means" not in body
    assert "stats coverage" not in body
    assert "metadata coverage" in body
    assert "some metadata commands not applicable" in body
    assert "not a missing-stats signal by itself" in body
    assert "Definition metadata" in body
    assert "Table stats metadata" in body
    assert "Column stats metadata" in body
    assert "SHOW CREATE command" not in body
    assert "TABLE STATS command" not in body
    assert "COLUMN STATS command" not in body
    assert "4 ok / 0 error / 2 not_applicable / 0 too_large" in body
    assert str(case_dir) not in body
    assert "raw_secret" not in body
    assert "CREATE TABLE" not in body
    assert "/tmp/evil" not in body


def test_web_batch_case_detail_parses_current_metadata_fact_blocks(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "analysis_facts.md").write_text(
        "\n".join(
            [
                "## Table Metadata Context",
                "",
                "- context file: available",
                "- table metadata facts: available",
                "- tables requested: 1",
                "",
                "- table: db.fact<script>",
                "- object type: unknown",
                "- SHOW CREATE TABLE status: ok",
                "- SHOW TABLE STATS status: ok",
                "- SHOW COLUMN STATS status: ok",
                "- table stats row-count completeness: available",
                "- column stats columns observed: 21",
                "- column stats missing/unknown markers: 74",
                "- column stats completeness: incomplete/unknown",
                "- column stats columns: `configuration_modes`, `crew`",
                "- file format: PARQUET",
                "- partition columns: unknown",
                "- Run SHOW TABLE STATS for referenced tables involved in this query.",
                "- Run SHOW COLUMN STATS for join/filter columns once join/filter columns are identified.",
                "- If stats are missing or stale, refresh stats through the approved operational process, then re-run the query.",
                "",
                "## Raw Context That Must Not Render",
                "CREATE TABLE raw_secret (id int)",
                "SHOW TABLE STATS raw_secret",
            ]
        ),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "metadata_status": "collected",
                        "referenced_table_count": 1,
                        "collected_metadata_table_count": 1,
                        "too_large_count": 0,
                        "score_reasons": ["column stats completeness incomplete/unknown"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "db.fact&lt;script&gt;" in body
    assert "db.fact<script>" not in body
    assert "PARQUET" in body
    assert "incomplete/unknown" in body
    assert "metadata command status" in body
    assert "stats coverage" in body
    assert "3 ok / 0 error / 0 not_applicable / 0 too_large" in body
    assert "Table-level metadata facts are unavailable" not in body
    assert "Run SHOW TABLE STATS" not in body
    assert "Run SHOW COLUMN STATS" not in body
    assert "approved operational process" not in body
    assert "stale" not in body
    assert "CREATE TABLE" not in body
    assert "raw_secret" not in body
    assert str(case_dir) not in body


def test_web_batch_case_detail_falls_back_to_aggregate_metadata_facts(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "impala_context.json").write_text(
        "{not valid json with CREATE TABLE raw_secret}",
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "metadata_status": "collected",
                        "referenced_table_count": 1,
                        "collected_metadata_table_count": 1,
                        "too_large_count": 0,
                        "score_reasons": ["column stats completeness incomplete/unknown"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    body = captured["body"]
    assert "Metadata facts" in body
    assert "metadata status" in body
    assert "collected" in body
    assert "metadata coverage" in body
    assert "collected status but no table rows available" in body
    assert "Treat stats coverage as unknown" in body
    assert "referenced tables" in body
    assert "collected metadata tables" in body
    assert "column stats completeness incomplete/unknown" in body
    assert "Table-level metadata facts are unavailable" in body
    assert "batch_summary.json" not in body
    assert "metadata facts unavailable" not in body
    assert "<span>metadata command status</span><strong>unknown</strong>" in body
    assert "0 ok / 0 error / 0 not_applicable / 0 too_large" not in body
    assert "raw_secret" not in body
    assert "CREATE TABLE" not in body
    assert str(case_dir) not in body


def test_web_batch_case_detail_renders_safe_impala_context_statement_facts(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_wrapper_dir = tmp_path / "cases" / "case-001"
    case_dir = case_wrapper_dir / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "analysis_facts.md").write_text(
        "## Table Metadata Context\n\n- context file: available\n- table metadata facts: aggregate only\n",
        encoding="utf-8",
    )
    (case_dir / "impala_context.json").write_text(
        json.dumps(
            {
                "tables": ["db.fact<script>"],
                "read_only_statements_only": True,
                "results": [
                    {
                        "table": "db.fact<script>",
                        "statement": "SHOW CREATE TABLE",
                        "status": "ok",
                        "sql": "SHOW CREATE TABLE db.fact<script>",
                        "stdout": (
                            "CREATE TABLE db.raw_secret (id BIGINT)\n"
                            "PARTITIONED BY (`ds` STRING)\n"
                            "STORED AS PARQUET\n"
                            "LOCATION 'hdfs://internal/path'\n"
                        ),
                    },
                    {
                        "table": "db.fact<script>",
                        "statement": "SHOW TABLE STATS",
                        "status": "ok",
                        "sql": "SHOW TABLE STATS db.fact<script>",
                        "stdout": "| #Rows | Size |\n| -1 | 10MB |\n",
                    },
                    {
                        "table": "db.fact<script>",
                        "statement": "SHOW COLUMN STATS",
                        "status": "ok",
                        "sql": "SHOW COLUMN STATS db.fact<script>",
                        "stdout": "| Column | NDV | #Nulls |\n| id | -1 | 0 |\n| amount | 10 | -1 |\n",
                        "stderr": "raw stderr secret",
                        "error": "raw error secret",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "metadata_status": "collected",
                        "referenced_table_count": 1,
                        "collected_metadata_table_count": 1,
                        "too_large_count": 0,
                        "score_reasons": ["table stats row-count completeness missing/unknown"],
                        "case_dir": str(case_wrapper_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001?impala_context=/tmp/evil"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "db.fact&lt;script&gt;" in body
    assert "db.fact<script>" not in body
    assert "PARQUET" in body
    assert "missing/unknown" in body
    assert "incomplete/unknown" in body
    assert "metadata command status" in body
    assert "3 ok / 0 error / 0 not_applicable / 0 too_large" in body
    assert "CREATE TABLE" not in body
    assert "SHOW CREATE TABLE db.fact" not in body
    assert "SHOW TABLE STATS db.fact" not in body
    assert "SHOW COLUMN STATS db.fact" not in body
    assert "raw_secret" not in body
    assert "hdfs://internal/path" not in body
    assert "raw stderr secret" not in body
    assert "raw error secret" not in body
    assert str(case_dir) not in body
    assert str(case_wrapper_dir) not in body
    assert "/tmp/evil" not in body


def test_web_batch_case_detail_handles_unknown_and_path_traversal_safely(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_index": 1, "query_id": "abc...000001"},
                    {"case_index": 1176, "query_id": "abc...001176"},
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    def get(path):
        request = handler.__new__(handler)
        captured: dict[str, object] = {}

        def write_html(status, body):
            captured["status"] = status
            captured["body"] = body

        request.path = path
        request.write_html = write_html
        request.do_GET()
        return captured

    unknown = get("/batch/case/case-999")
    broad = get("/batch/case/case-1176")
    traversal = get("/batch/case/..%2Fsecret")

    assert unknown["status"] == 404
    assert "Finished Queries case not found" in unknown["body"]
    assert "case-999" in unknown["body"]
    assert broad["status"] == 200
    assert "case-1176" in broad["body"]
    assert traversal["status"] == 404
    assert "Finished Queries case not found" in traversal["body"]
    assert "..%2Fsecret" in traversal["body"]
    assert "/tmp" not in traversal["body"]


def test_web_batch_case_report_action_builds_validated_python_report_command(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "collected",
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        batch_summary=summary,
        model="configured-model",
        source_visibility="owner_raw",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert isinstance(cmd, list)
        assert "shell" not in kwargs
        assert kwargs["cwd"] == str(REPO_DIR)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        (case_dir / module.PYTHON_REPORT_NAME).write_text(
            "# Validated report\n\nSafe body.\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            cmd, 0, stdout="raw stdout hidden", stderr="raw stderr hidden"
        )

    status, location = module.start_batch_case_report_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch_report"
    assert snapshot.status == "ok"
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert command_uses_role(cmd, "report")
    assert command_args(cmd, "report")[0] == str(case_dir)
    assert cmd[cmd.index("--mode") + 1] == "admin"
    assert cmd[cmd.index("--model") + 1] == "configured-model"
    assert cmd[cmd.index("--out") + 1] == module.PYTHON_REPORT_NAME
    assert cmd[cmd.index("--validation-mode") + 1] == "strict"
    assert "--no-llm" in cmd
    assert "--no-validate" not in cmd
    assert "--top-reports" not in cmd
    assert kwargs["env"] is not None
    assert str(case_dir) in cmd
    assert (case_dir / module.PYTHON_REPORT_VALIDATION_MARKER).is_file()

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}?query_group=suspicious"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Open full report" in captured["body"]
    assert "Generate Python report" not in captured["body"]
    assert "validated report" in captured["body"]
    assert ">Validated report</h1>" in captured["body"]
    assert "Safe body." in captured["body"]
    assert "raw stdout hidden" not in captured["body"]
    assert str(case_dir) not in captured["body"]


def test_web_batch_case_llm_report_action_builds_separate_validated_command(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "collected",
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        batch_summary=summary,
        model="configured-model",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        (case_dir / module.LLM_REPORT_NAME).write_text(
            "# Validated LLM narrative\n\nSafe body.\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_case_report_job(
        "case-001",
        settings,
        store,
        runner=fake_runner,
        report_variant=module.REPORT_VARIANT_LLM,
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch_llm_report"
    assert snapshot.status == "ok"
    cmd, kwargs = calls[0]
    assert command_uses_role(cmd, "report")
    assert cmd[cmd.index("--out") + 1] == module.LLM_REPORT_NAME
    assert cmd[cmd.index("--validation-mode") + 1] == "strict"
    assert "--no-llm" not in cmd
    assert kwargs["env"] is not None
    assert (case_dir / module.LLM_REPORT_VALIDATION_MARKER).is_file()
    assert not (case_dir / module.PYTHON_REPORT_VALIDATION_MARKER).exists()


def test_web_running_batch_report_renders_progress_steps(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    job = store.create_batch_report("case-001")
    store.update_stage(job.job_id, 1)
    report_state = module.load_batch_case_report_state(
        settings,
        "case-001",
        {"case_dir": str(case_dir)},
        store,
    )
    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job.job_id}"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert report_state["progress_view"].percent == 25
    assert report_state["progress_view"].steps[1].state == "running"
    assert report_state["progress_view"].steps[1].detail == "Generating validated report"
    assert "LLM report status: running" not in body
    assert "Generating Python report" in body
    assert "Generating validated report" in body
    assert f'data-report-job-status-url="/jobs/{job.job_id}/status"' in body
    assert f'data-report-job-url="/jobs/{job.job_id}"' in body
    assert 'style="width:25%"' in body
    assert "batch-progress-step--running" in body
    assert "batch-progress-step--done" in body


def test_web_batch_optimized_query_job_generates_validated_draft_without_echoing_source(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        batch_summary=summary,
        model="configured-model",
        source_visibility="owner_raw",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        (case_dir / "optimized_query.sql").write_text(
            "SELECT secret_col FROM db.source_table;\n", encoding="utf-8"
        )
        facts_text = (case_dir / "analysis_facts.md").read_text(encoding="utf-8")
        source_sql = "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
        (case_dir / "optimized_query.validated.json").write_text(
            json.dumps(
                {
                    "draft": "optimized_query.sql",
                    "draft_sha256": module.file_sha256(case_dir / "optimized_query.sql"),
                    "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
                    "risk_mode": "rewrite_allowed",
                    "risk_reasons": [],
                    "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
                    "source": "query_doctor_optimize_query",
                    "source_scope": "read_only_statement",
                    "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
                    "validated": True,
                    "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            cmd, 0, stdout="raw stdout hidden", stderr="raw stderr hidden"
        )

    status, location = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch_optimized_query"
    assert snapshot.status == "ok"
    cmd, kwargs = calls[0]
    assert command_uses_role(cmd, "optimize_query")
    assert command_args(cmd, "optimize_query")[0] == str(case_dir)
    assert cmd[cmd.index("--model") + 1] == module.DEFAULT_OPTIMIZER_MODEL
    assert kwargs["env"] is not None

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Query LLM optimizer" in captured["body"]
    assert "Open Query LLM optimizer draft" in captured["body"]
    assert "Validated SQL draft" in captured["body"]
    assert "Outcome: Validated SQL draft" in captured["body"]
    assert '<div class="optimized-query-copy" data-optimized-query-block>' in captured["body"]
    assert (
        '<button class="button copy-query-button" type="button" data-copy-optimized-query>Copy query</button>'
        in captured["body"]
    )
    assert "SELECT secret_col FROM db.source_table;" in captured["body"]
    assert "secret_flag" not in captured["body"]
    assert "raw stdout hidden" not in captured["body"]
    assert str(case_dir) not in captured["body"]


def test_web_batch_case_actions_job_generates_python_report_and_optimizer(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    source_sql = "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
    facts_text = "FACTS\n"
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        batch_summary=summary,
        model="configured-model",
        source_visibility="owner_raw",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if command_uses_role(cmd, "report"):
            (case_dir / module.PYTHON_REPORT_NAME).write_text(
                "# Validated report\n\nSafe body.\n", encoding="utf-8"
            )
        elif command_uses_role(cmd, "optimize_query"):
            (case_dir / "optimized_query.sql").write_text(
                "SELECT secret_col FROM db.source_table;\n", encoding="utf-8"
            )
            (case_dir / "optimized_query.validated.json").write_text(
                json.dumps(
                    {
                        "draft": "optimized_query.sql",
                        "draft_sha256": module.file_sha256(case_dir / "optimized_query.sql"),
                        "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
                        "risk_mode": "rewrite_allowed",
                        "risk_reasons": [],
                        "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
                        "source": "query_doctor_optimize_query",
                        "source_scope": "read_only_statement",
                        "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
                        "validated": True,
                        "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(
            cmd, 0, stdout="raw stdout hidden", stderr="raw stderr hidden"
        )

    status, location = module.start_batch_case_llm_actions_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch_case_actions"
    assert snapshot.status == "ok"
    assert len(calls) == 2
    assert command_uses_role(calls[0][0], "report")
    assert calls[0][0][calls[0][0].index("--out") + 1] == module.PYTHON_REPORT_NAME
    assert "--no-llm" in calls[0][0]
    assert command_uses_role(calls[1][0], "optimize_query")

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Open full report" in captured["body"]
    assert "Open Query LLM optimizer draft" in captured["body"]
    assert (
        '<button class="button copy-query-button" type="button" data-copy-optimized-query>Copy query</button>'
        in captured["body"]
    )
    assert "Safe body." in captured["body"]
    assert "SELECT secret_col FROM db.source_table;" in captured["body"]
    assert "secret_flag" not in captured["body"]
    assert "raw stdout hidden" not in captured["body"]
    assert str(case_dir) not in captured["body"]


def test_web_no_llm_combined_job_uses_python_result_label(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    source_sql = "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
    facts_text = "FACTS\n"
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"), batch_summary=summary, no_llm=True
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "report"):
            (case_dir / module.PYTHON_REPORT_NAME).write_text(
                "# Python report\n\nSafe body.\n", encoding="utf-8"
            )
        elif command_uses_role(cmd, "optimize_query"):
            recommendations_path = case_dir / "optimized_query_recommendations.md"
            recommendations_path.write_text("- Safe deterministic guidance.\n", encoding="utf-8")
            (case_dir / "optimized_query.validated.json").write_text(
                json.dumps(
                    {
                        "fallback_reason": "no_python_owned_recipe",
                        "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
                        "output_kind": "no_rewrite",
                        "recommendations": recommendations_path.name,
                        "recommendations_sha256": module.file_sha256(recommendations_path),
                        "risk_mode": "rewrite_allowed",
                        "risk_reasons": [],
                        "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
                        "source": "query_doctor_optimize_query",
                        "source_scope": "read_only_statement",
                        "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
                        "validated": True,
                        "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_case_llm_actions_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert snapshot.kind == "batch_case_actions"
    assert "Python report and optimizer generated" in snapshot.result_html
    assert "LLM report and optimizer generated" not in snapshot.result_html
    assert "--no-llm" in calls[0]
    assert "--no-llm" in calls[1]


def test_web_batch_case_actions_job_keeps_report_when_optimizer_fails(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    source_sql = "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "report"):
            (case_dir / module.PYTHON_REPORT_NAME).write_text(
                "# Validated report\n\nSafe body.\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(
                cmd, 0, stdout="raw stdout hidden", stderr="raw stderr hidden"
            )
        return subprocess.CompletedProcess(
            cmd, 1, stdout="raw stdout hidden", stderr="SECRET_OPTIMIZER_STDERR"
        )

    status, location = module.start_batch_case_llm_actions_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch_case_actions"
    assert snapshot.status == "failed"
    assert len(calls) == 2

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    body = captured["body"]
    assert captured["status"] == 200
    assert "Open full report" in body
    assert "Safe body." in body
    assert "Python report failed" not in body
    assert "Query LLM optimizer failed" in body
    assert "Unsafe output is hidden" in body
    assert "raw stdout hidden" not in body
    assert "SECRET_OPTIMIZER_STDERR" not in body
    assert "secret_flag" not in body
    assert str(case_dir) not in body


def test_web_batch_case_actions_job_stops_when_report_fails(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    source_sql = "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "report"):
            (case_dir / module.PYTHON_REPORT_PARTIAL_NAME).write_text(
                "# Partial\n\nSELECT secret_col FROM db.source_table\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd, 4, stdout="raw stdout hidden", stderr="raw stderr hidden"
            )
        raise AssertionError("optimizer must not run after report validation failure")

    status, location = module.start_batch_case_llm_actions_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 303
    assert fragment_from_location(location) == "case-actions"
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch_case_actions"
    assert snapshot.status == "failed"
    assert len(calls) == 1
    assert command_uses_role(calls[0], "report")
    assert not (case_dir / "optimized_query.sql").exists()
    assert not (case_dir / "optimized_query.validated.json").exists()

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    body = captured["body"]
    assert captured["status"] == 200
    assert "Python report failed" in body
    assert "Query LLM optimizer failed" not in body
    assert "The partial report is untrusted and hidden." in body
    assert "Open Query LLM optimizer" not in body
    assert "diagnosis.partial.md" not in body
    assert "SELECT secret_col" not in body
    assert "secret_flag" not in body
    assert "raw stdout hidden" not in body
    assert str(case_dir) not in body


def test_web_batch_optimized_query_renders_no_rewrite_outcome_without_sql_draft(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    source_sql = "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
    facts_text = "FACTS\n"
    recommendations_text = "- No trusted SQL rewrite is shown because the validated draft did not materially change the source query.\n"
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    (case_dir / "optimized_query_recommendations.md").write_text(
        recommendations_text, encoding="utf-8"
    )
    (case_dir / "optimized_query.validated.json").write_text(
        json.dumps(
            {
                "output_kind": "no_rewrite",
                "fallback_reason": "no_material_change",
                "recommendations": "optimized_query_recommendations.md",
                "recommendations_sha256": module.file_sha256(
                    case_dir / "optimized_query_recommendations.md"
                ),
                "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
                "risk_mode": "rewrite_allowed",
                "risk_reasons": [],
                "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
                "source": "query_doctor_optimize_query",
                "source_scope": "read_only_statement",
                "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
                "validated": True,
                "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
            }
        ),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001/optimized-query"
    request.write_html = write_html
    request.do_GET()

    body = captured["body"]
    assert captured["status"] == 200
    assert "Open Query LLM optimizer outcome" in body
    assert "Query LLM optimizer outcome" in body
    assert "No material rewrite" in body
    assert "The optimizer did not find a material validated SQL change" in body
    assert "No trusted SQL rewrite is shown" in body
    assert "Outcome: No trusted rewrite" in body
    assert "Reason: No material rewrite" in body
    assert "No rewrite needed" not in body
    assert "no_rewrite" not in body
    assert "Validate rewritten SQL" not in body
    assert "secret_flag" not in body
    assert "SELECT secret_col" not in body
    assert "optimized_query.sql" not in body
    assert str(case_dir) not in body


def test_web_batch_validation_failed_no_rewrite_shows_manual_validation_form_collapsed(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    source_sql = "SELECT a FROM db.source_table WHERE ds = 20260504"
    facts_text = "FACTS\n"
    recommendations_text = (
        "- The model could not write a SQL draft that passed deterministic validation.\n"
    )
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    (case_dir / "optimized_query_recommendations.md").write_text(
        recommendations_text, encoding="utf-8"
    )
    (case_dir / "optimized_query.validated.json").write_text(
        json.dumps(
            {
                "fallback_reason": "validation_failed",
                "output_kind": "no_rewrite",
                "recommendations": "optimized_query_recommendations.md",
                "recommendations_sha256": module.file_sha256(
                    case_dir / "optimized_query_recommendations.md"
                ),
                "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
                "risk_mode": "rewrite_allowed",
                "risk_reasons": [],
                "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
                "source": "query_doctor_optimize_query",
                "source_scope": "read_only_statement",
                "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
                "validated": True,
                "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
            }
        ),
        encoding="utf-8",
    )
    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 48,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    summary.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)

    body = module.render_batch_case_detail_for_request(
        settings, "case-001", case, module.WebJobStore()
    )

    assert "Query LLM optimizer outcome" in body
    assert "No trusted rewrite" in body
    assert "Draft failed deterministic validation" in body
    assert "No rewrite needed" not in body
    assert "Manual validation: Available" in body
    assert "Validate rewritten SQL" in body
    assert 'open aria-label="Validate rewritten SQL"' not in body
    assert "validation_failed" not in body
    assert "SELECT a FROM db.source_table" not in body
    assert str(case_dir) not in body


def test_web_batch_optimizer_partial_untrusted_shows_manual_guidance(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(optimizer_recipe_facts(), encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": post_union_aggregate_source_sql()}),
        encoding="utf-8",
    )
    (case_dir / "optimized_query.partial.txt").write_text(
        post_union_aggregate_draft_sql(where_value=2),
        encoding="utf-8",
    )
    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 48,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    summary.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)

    body = module.render_batch_case_detail_for_request(
        settings, "case-001", case, module.WebJobStore()
    )

    assert "Manual rewrite guidance" in body
    assert "Validation failed" in body
    assert "Manual validation: Available" in body
    assert "Python-owned bullets for manual rewrite review" in body
    assert "Validate rewritten SQL" in body
    assert 'open aria-label="Manual optimizer guidance"' not in body
    assert 'open aria-label="Validate rewritten SQL"' not in body
    assert "optimized_query.partial.txt" not in body
    assert "WHERE ds = 2" not in body
    assert str(case_dir) not in body


def test_web_batch_optimizer_does_not_show_manual_block_before_validation_failure(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(optimizer_recipe_facts(), encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": post_union_aggregate_source_sql()}),
        encoding="utf-8",
    )
    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 48,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    summary.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)

    body = module.render_batch_case_detail_for_request(
        settings, "case-001", case, module.WebJobStore()
    )

    assert "Manual rewrite guidance" not in body
    assert "Validate rewritten SQL" not in body


def test_web_batch_external_rewrite_validation_passes_recipe_without_echoing_sql(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(optimizer_recipe_facts(), encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": post_union_aggregate_source_sql()}),
        encoding="utf-8",
    )
    (case_dir / "optimized_query.partial.txt").write_text(
        post_union_aggregate_draft_sql(where_value=2),
        encoding="utf-8",
    )
    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 48,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    summary.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    form = {"rewritten_sql": [post_union_aggregate_draft_sql()]}

    status, body = module.handle_batch_case_external_rewrite_validation(
        "case-001",
        settings,
        module.WebJobStore(),
        form,
    )

    assert status == 200
    assert "External rewrite validation passed" in body
    assert "Physical table set was preserved" in body
    assert "GROUP BY category" not in body
    assert "SELECT category" not in body
    assert str(case_dir) not in body


def test_web_batch_external_rewrite_validation_fails_without_echoing_sql(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(optimizer_recipe_facts(), encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": post_union_aggregate_source_sql()}),
        encoding="utf-8",
    )
    (case_dir / "optimized_query.partial.txt").write_text(
        post_union_aggregate_draft_sql(where_value=2),
        encoding="utf-8",
    )
    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 48,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    summary.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    rejected_sql = post_union_aggregate_draft_sql(where_value=2)

    status, body = module.handle_batch_case_external_rewrite_validation(
        "case-001",
        settings,
        module.WebJobStore(),
        {"rewritten_sql": [rejected_sql]},
    )

    assert status == 200
    assert "External rewrite validation failed" in body
    assert "Reason" in body
    assert "web.optimizer_external_validation_failed" in body
    assert "Stage" in body
    assert "External rewrite validation" in body
    assert "Next step:" in body
    assert "Pasted SQL text remains hidden" in body
    assert "Source filter scope changed" in body
    assert "WHERE ds = 2" not in body
    assert "SELECT category" not in body
    assert str(case_dir) not in body


def test_web_running_batch_optimized_query_renders_progress_steps(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    job = store.create_batch_optimized_query("case-001")
    store.update_stage(job.job_id, 1)
    optimizer_state = module.load_optimized_query_state(case_dir, store, batch_case_id="case-001")
    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job.job_id}"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert optimizer_state["progress_view"].percent == 25
    assert optimizer_state["progress_view"].steps[1].state == "running"
    assert optimizer_state["progress_view"].steps[1].detail == "Generating optimizer draft"
    assert "Query LLM optimizer" in body
    assert "Running Query LLM optimizer" in body
    assert "Checking source SQL" in body
    assert "Generating draft" in body
    assert "Validating draft" in body
    assert "Generating LLM report" not in body
    assert 'data-report-job-status-url="/jobs/' not in body
    assert f'data-optimizer-job-status-url="/jobs/{job.job_id}/status"' in body
    assert f'data-optimizer-job-url="/jobs/{job.job_id}"' in body
    assert 'style="width:25%"' in body
    assert "batch-progress-step--running" in body
    assert "batch-progress-step--done" in body
    assert "secret_flag" not in body


def test_web_optimizer_action_failure_renders_structured_job_error(tmp_path):
    module = load_web_module()
    from query_doctor.web.ui.llm_actions import (
        present_optimized_query_action,
        render_optimizer_status,
    )

    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT a FROM db.source_table WHERE ds = 20260504"}),
        encoding="utf-8",
    )
    store = module.WebJobStore()
    job = store.create_batch_optimized_query("case-001")
    store.fail(
        job.job_id,
        RuntimeError(
            "Optimizer failed with stderr SELECT secret_col FROM db.source_table "
            f"and artifact {tmp_path / 'optimizer.raw'}"
        ),
    )
    failed_job = store.get(job.job_id)
    assert failed_job is not None

    state = module.load_optimized_query_state(
        case_dir, store, batch_case_id="case-001", job=failed_job
    )
    view = present_optimized_query_action(state)
    html = render_optimizer_status(view)

    assert state["status"] == "failed"
    assert state["error_info"]["reason_code"] == "batch_optimized_query.failed"
    assert "Reason" in html
    assert "batch_optimized_query.failed" in html
    assert "Stage" in html
    assert "Checking source SQL" in html
    assert "Next step:" in html
    assert "Review the selected inputs and local configuration" in html
    assert "SELECT" not in html
    assert "secret_col" not in html
    assert "optimizer.raw" not in html


def test_web_batch_optimizer_job_does_not_mark_report_as_running(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT a FROM db.source_table WHERE ds = 20260504"}),
        encoding="utf-8",
    )
    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 22,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    optimizer_job = store.create_batch_optimized_query("case-001")

    report_state = module.load_batch_case_report_state(
        settings, "case-001", case, store, job=optimizer_job
    )
    body = module.render_batch_case_detail_for_request(
        settings, "case-001", case, store, job=optimizer_job
    )

    assert report_state["status"] == "unavailable"
    assert report_state["running"] is False
    assert "Generating LLM report" not in body
    assert "Running Query LLM optimizer" in body
    assert 'data-report-job-status-url="/jobs/' not in body
    assert "data-optimizer-job-status-url" in body


def test_web_query_id_summary_prefers_profile_user_and_pool(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "profile_digest.md").write_text(
        json.dumps(
            {
                "details": (
                    "Query Runtime Profile\n"
                    "User: profile_user\n"
                    "Request Pool: profile_pool\n"
                    "Sql Statement: SELECT secret_col FROM db.source_table\n"
                )
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "cm_metadata.json").write_text(
        json.dumps(
            {
                "duration_sec": 12.0,
                "query_type": "QUERY",
                "user": "metadata_user",
                "pool": "metadata_pool",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")

    case = module.build_query_id_summary_case("abc:def", case_dir)

    assert case["user"] == "profile_user"
    assert case["pool"] == "profile_pool"
    assert case["query_type"] == "QUERY"
    assert "SELECT secret_col" not in json.dumps(case)


def test_web_query_id_summary_falls_back_to_metadata_user_and_pool(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "profile_digest.md").write_text("Query Runtime Profile\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps(
            {
                "duration_sec": 12.0,
                "query_type": "QUERY",
                "user": "metadata_user",
                "pool": "metadata_pool",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")

    case = module.build_query_id_summary_case("abc:def", case_dir)

    assert case["user"] == "metadata_user"
    assert case["pool"] == "metadata_pool"


def test_web_specific_optimizer_job_does_not_mark_report_as_running(tmp_path):
    module = load_web_module()
    query_id = "abc:def"
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps(
            {
                "statement": "SELECT a FROM db.source_table WHERE ds = 20260504",
                "user": "analyst",
                "pool": "root.analytics",
                "query_type": "QUERY",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "collection_warnings.txt").write_text("none\n", encoding="utf-8")
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"), corpus_dir=tmp_path / "cm-corpus"
    )
    store = module.WebJobStore()
    optimizer_job = store.create_query_optimized_query(query_id)

    report_state = module.load_specific_query_report_state(
        settings, query_id, case_dir, store, job=optimizer_job
    )
    status, body = module.render_specific_query_detail_for_request(
        settings, query_id, store, job=optimizer_job
    )

    assert status == 200
    assert report_state["status"] == "not_run"
    assert report_state["running"] is False
    assert "Generating LLM report" not in body
    assert "Running Query LLM optimizer" in body
    assert 'data-report-job-status-url="/jobs/' not in body
    assert "data-optimizer-job-status-url" in body
    assert "analyst" in body
    assert "SELECT a FROM db.source_table" not in body


def test_web_batch_optimized_query_validation_failure_hides_partial_draft(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        (case_dir / "optimized_query.partial.txt").write_text(
            "SELECT secret_col FROM db.source_table;\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            cmd, 4, stdout="raw stdout hidden", stderr="raw stderr hidden"
        )

    status, location = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )
    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "failed"
    assert not (case_dir / "optimized_query.validated.json").exists()

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Query LLM optimizer failed" in body
    assert "failed deterministic validation" in body
    assert "The partial draft is untrusted and hidden." in body
    assert "batch-progress-step--failed" in body
    assert "optimized_query.partial.txt" not in body
    assert "SELECT secret_col" not in body
    assert "secret_flag" not in body
    assert "raw stdout hidden" not in body
    assert str(case_dir) not in body


def test_web_batch_optimized_query_partial_state_is_explicit_and_hidden(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"}),
        encoding="utf-8",
    )
    (case_dir / "optimized_query.partial.txt").write_text(
        "SELECT secret_col FROM db.source_table;\n",
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001/optimized-query"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Validation failed" in body
    assert "The generated SQL draft failed deterministic validation." in body
    assert "Manual validation: Available" in body
    assert "Run Query LLM optimizer" in body
    assert (
        '<button class="button copy-query-button" type="button" data-copy-optimized-query>Copy query</button>'
        not in body
    )
    assert "SELECT secret_col" not in body
    assert "secret_flag" not in body
    assert "optimized_query.partial.txt" not in body
    assert str(case_dir) not in body


def test_web_batch_optimized_query_subprocess_failure_hides_output(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table WHERE secret_flag = 1"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            2,
            stdout="SELECT secret_col FROM db.source_table",
            stderr=f"local path {case_dir} secret_flag",
        )

    status, location = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )
    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Query LLM optimizer failed" in body
    assert "Query Doctor optimized query generation failed with exit code 2" in body
    assert "Captured subprocess output is not shown" in body
    assert "SELECT secret_col" not in body
    assert "secret_flag" not in body
    assert str(case_dir) not in body


def test_web_batch_optimized_query_unavailable_source_is_compact(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "DELETE FROM db.source_table WHERE secret_flag = 1"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "score_reasons": ["cardinality estimate anomalies: 5"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, body = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert status == 400
    assert calls == []
    assert (
        "Source SQL is unavailable or outside the optimizer read-only scope for this case." in body
    )
    assert 'aria-label="Unavailable actions"' in body
    assert (
        '<button class="button" type="button" disabled>Run Query LLM optimizer</button>' not in body
    )
    assert "DELETE" not in body
    assert "secret_col" not in body
    assert "secret_flag" not in body
    assert str(case_dir) not in body


def test_web_batch_details_disable_actions_when_analysis_facts_are_missing(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 22,
                        "analysis_status": "failed",
                        "score_reasons": ["analysis failed"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("runner should not be called")

    report_status, report_body = module.start_batch_case_report_job(
        "case-001", settings, store, runner=fake_runner
    )
    optimizer_status, optimizer_body = module.start_batch_case_optimized_query_job(
        "case-001", settings, store, runner=fake_runner
    )

    assert report_status == 400
    assert optimizer_status == 400
    assert calls == []
    assert (
        "Report generation requires successful deterministic processing for this case. "
        "Re-run analysis first." in report_body
    )
    assert (
        "Optimizer requires successful deterministic processing for this case. "
        "Re-run analysis first." in optimizer_body
    )
    for body in (report_body, optimizer_body):
        assert 'aria-label="Unavailable actions"' in body
        assert "Generate LLM report" not in body
        assert "Run Query LLM optimizer" not in body
        assert "Generate report + optimizer" not in body
        assert "Processing failure follow-up" in body
        assert "SELECT" not in body
        assert "secret_col" not in body
        assert str(case_dir) not in body


def test_web_batch_optimized_query_insert_select_source_is_available(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "cm_metadata.json").write_text(
        json.dumps(
            {
                "statement": "INSERT OVERWRITE TABLE target SELECT secret_col FROM db.source_table WHERE secret_flag = 1"
            }
        ),
        encoding="utf-8",
    )

    assert module.case_has_safe_source_sql(case_dir) is True


def test_web_batch_optimized_query_impala_context_source_is_available(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    context_dir = case_dir / "impala_context"
    context_dir.mkdir(parents=True)
    (context_dir / "original_query.sql").write_text(
        "SELECT secret_col FROM db.source_table WHERE secret_flag = 1",
        encoding="utf-8",
    )

    assert module.case_has_safe_source_sql(case_dir) is True


def test_web_batch_case_report_action_rejects_unknown_and_traversal_case(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps({"cases": [{"case_index": 1, "query_id": "abc...000001"}]}), encoding="utf-8"
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    unknown = module.start_batch_case_report_job("case-999", settings, store, runner=fake_runner)
    traversal = module.start_batch_case_report_job(
        "../case-001", settings, store, runner=fake_runner
    )

    assert unknown[0] == 404
    assert traversal[0] == 404
    assert calls == []
    assert "Finished Queries case not found" in unknown[1]
    assert "Finished Queries case not found" in traversal[1]


def test_web_batch_case_report_failure_keeps_partial_untrusted(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 7,
                        "score_reasons": ["analysis warning"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        (case_dir / module.PYTHON_REPORT_PARTIAL_NAME).write_text(
            "# Partial\n\nraw unsafe partial\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            cmd, 4, stdout="raw stdout hidden", stderr="raw stderr hidden"
        )

    status, location = module.start_batch_case_report_job(
        "case-001", settings, store, runner=fake_runner
    )
    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "failed"
    assert not (case_dir / module.PYTHON_REPORT_VALIDATION_MARKER).exists()

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}?query_group=suspicious"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Python Report" in body
    assert "Python report failed" in body
    assert "validation rejected the output" in body
    assert "The partial report is untrusted and hidden." in body
    assert "batch-progress-step--failed" in body
    assert module.PYTHON_REPORT_PARTIAL_NAME not in body
    assert "Partial report exists but is untrusted and hidden." not in body
    assert "raw unsafe partial" not in body
    assert "raw stdout hidden" not in body
    assert "Open Python report" not in body
    assert str(case_dir) not in body


def test_web_batch_case_validated_report_view_is_resolved_from_summary(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / module.PYTHON_REPORT_NAME).write_text(
        f"# Report\n\nValidated body with {case_dir} hidden.\n",
        encoding="utf-8",
    )
    module.write_batch_case_report_validation_marker(
        case_dir, report_variant=module.REPORT_VARIANT_PYTHON
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc<script>",
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001/python-report?case_dir=/tmp/evil"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "Validated Finished Queries case report" in body
    assert "Validated body with [local case path hidden] hidden." in body
    assert "abc&lt;script&gt;" in body
    assert "abc<script>" not in body
    assert str(case_dir) not in body
    assert "/tmp/evil" not in body


def test_web_validated_report_marker_must_match_report_and_facts(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / module.PYTHON_REPORT_NAME).write_text("# Report\n\nSafe body.\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")

    module.write_batch_case_report_validation_marker(
        case_dir, report_variant=module.REPORT_VARIANT_PYTHON
    )

    assert module.batch_case_validated_report_exists(
        case_dir, report_variant=module.REPORT_VARIANT_PYTHON
    )

    (case_dir / module.PYTHON_REPORT_NAME).write_text(
        "# Report\n\nChanged body.\n", encoding="utf-8"
    )

    assert not module.batch_case_validated_report_exists(
        case_dir, report_variant=module.REPORT_VARIANT_PYTHON
    )


def test_web_legacy_validation_marker_without_hashes_is_not_trusted(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "diagnosis.md").write_text("# Report\n\nSafe body.\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "diagnosis.validated.json").write_text(
        json.dumps({"validated": True}), encoding="utf-8"
    )

    assert not module.batch_case_validated_report_exists(case_dir)


def test_web_optimized_query_marker_must_match_draft_facts_and_source(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    source_sql = "SELECT a FROM db.source_table WHERE ds = 20260504"
    facts_text = "FACTS\n"
    draft_text = "SELECT a FROM db.source_table WHERE ds = 20260504;\n"
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    (case_dir / "optimized_query.sql").write_text(draft_text, encoding="utf-8")

    def write_marker() -> None:
        current_source = json.loads((case_dir / "cm_metadata.json").read_text(encoding="utf-8"))[
            "statement"
        ]
        current_facts = (case_dir / "analysis_facts.md").read_text(encoding="utf-8")
        marker = {
            "draft": "optimized_query.sql",
            "draft_sha256": module.file_sha256(case_dir / "optimized_query.sql"),
            "facts_sha256": hashlib.sha256(current_facts.encode("utf-8")).hexdigest(),
            "risk_mode": "rewrite_allowed",
            "risk_reasons": [],
            "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
            "source": "query_doctor_optimize_query",
            "source_scope": "read_only_statement",
            "source_sql_sha256": hashlib.sha256(current_source.encode("utf-8")).hexdigest(),
            "validated": True,
            "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
        }
        (case_dir / "optimized_query.validated.json").write_text(
            json.dumps(marker), encoding="utf-8"
        )

    write_marker()
    assert module.optimized_query_validated_exists(case_dir)

    (case_dir / "optimized_query.sql").write_text("WITH c AS (SELECT a,\n", encoding="utf-8")
    write_marker()
    assert not module.optimized_query_validated_exists(case_dir)
    state = module.load_optimized_query_state(case_dir, module.WebJobStore())
    assert state["status"] == "partial_untrusted"
    assert state["trusted"] is False

    (case_dir / "optimized_query.sql").write_text(draft_text, encoding="utf-8")
    write_marker()
    assert module.optimized_query_validated_exists(case_dir)

    (case_dir / "optimized_query.sql").write_text(
        "SELECT a FROM db.source_table;\n", encoding="utf-8"
    )
    assert not module.optimized_query_validated_exists(case_dir)

    (case_dir / "optimized_query.sql").write_text(draft_text, encoding="utf-8")
    write_marker()
    (case_dir / "analysis_facts.md").write_text("CHANGED FACTS\n", encoding="utf-8")
    assert not module.optimized_query_validated_exists(case_dir)

    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    write_marker()
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT a FROM db.source_table WHERE ds = 20260505"}),
        encoding="utf-8",
    )
    assert not module.optimized_query_validated_exists(case_dir)


def test_web_optimized_query_recommendations_marker_is_trusted_without_sql_draft(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    summary = tmp_path / "batch_summary.json"
    source_sql = "SELECT a FROM db.source_table WHERE ds = 20260504"
    facts_text = "FACTS\n"
    recommendations_text = "- Collect table and column statistics.\n"
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    (case_dir / "optimized_query_recommendations.md").write_text(
        recommendations_text, encoding="utf-8"
    )
    marker = {
        "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
        "output_kind": "recommendations_only",
        "recommendations": "optimized_query_recommendations.md",
        "recommendations_sha256": module.file_sha256(
            case_dir / "optimized_query_recommendations.md"
        ),
        "risk_mode": "recommendations_only",
        "risk_reasons": ["cte_body_validation_not_proven", "too_many_ctes_for_safe_rewrite"],
        "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
        "source": "query_doctor_optimize_query",
        "source_scope": "read_only_statement",
        "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
        "validated": True,
        "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
    }
    (case_dir / "optimized_query.validated.json").write_text(json.dumps(marker), encoding="utf-8")

    assert module.optimized_query_validated_exists(case_dir)
    assert module.load_validated_optimized_query(case_dir) is None
    assert module.load_validated_optimizer_recommendations(case_dir) == recommendations_text
    state = module.load_optimized_query_state(case_dir, module.WebJobStore())
    assert state["status"] == "generated"
    assert state["output_kind"] == "recommendations_only"
    assert state["risk_reasons"] == [
        "cte_body_validation_not_proven",
        "too_many_ctes_for_safe_rewrite",
    ]

    case = {
        "case_index": 1,
        "query_id": "abc...000001",
        "score": 22,
        "score_reasons": ["cardinality estimate anomalies: 5"],
        "case_dir": str(case_dir),
    }
    summary.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    body = module.render_batch_case_detail_for_request(
        settings, "case-001", case, module.WebJobStore()
    )

    assert "Query LLM optimizer recommendations" in body
    assert 'href="#query-optimizer-result"' in body
    assert 'id="query-optimizer-result"' in body
    assert "Recommendations only" in body
    assert "Outcome: Recommendations only" in body
    assert "CTE body equivalence is not proven by deterministic validation" in body
    assert "CTE count exceeds the safe SQL-draft threshold" in body
    assert "Deterministic risk checks skipped SQL rewrite" in body
    assert "recommendations_only" not in body
    assert "cte_body_validation_not_proven" not in body
    assert "too_many_ctes_for_safe_rewrite" not in body
    assert "SELECT a FROM db.source_table" not in body
    assert str(case_dir) not in body


def test_web_optimized_query_recommendations_text_is_revalidated_before_render(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    source_sql = "SELECT a FROM db.source_table WHERE ds = 20260504"
    facts_text = "FACTS\n"
    unsafe_recommendations = "- SELECT a FROM db.source_table\n"
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": source_sql}), encoding="utf-8"
    )
    (case_dir / "optimized_query_recommendations.md").write_text(
        unsafe_recommendations, encoding="utf-8"
    )
    marker = {
        "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
        "output_kind": "recommendations_only",
        "recommendations": "optimized_query_recommendations.md",
        "recommendations_sha256": module.file_sha256(
            case_dir / "optimized_query_recommendations.md"
        ),
        "risk_mode": "recommendations_only",
        "risk_reasons": ["too_many_ctes_for_safe_rewrite"],
        "schema_version": module.OPTIMIZED_QUERY_MARKER_SCHEMA_VERSION,
        "source": "query_doctor_optimize_query",
        "source_scope": "read_only_statement",
        "source_sql_sha256": hashlib.sha256(source_sql.encode("utf-8")).hexdigest(),
        "validated": True,
        "validation_mode": module.OPTIMIZED_QUERY_VALIDATION_MODE,
    }
    (case_dir / "optimized_query.validated.json").write_text(json.dumps(marker), encoding="utf-8")

    assert module.optimized_query_validated_exists(case_dir)
    assert module.load_validated_optimizer_recommendations(case_dir) is None


def test_web_legacy_optimized_query_marker_without_hashes_is_not_trusted(tmp_path):
    module = load_web_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT a FROM db.source_table"}),
        encoding="utf-8",
    )
    (case_dir / "optimized_query.sql").write_text(
        "SELECT a FROM db.source_table;\n", encoding="utf-8"
    )
    (case_dir / "optimized_query.validated.json").write_text(
        json.dumps({"draft": "optimized_query.sql", "validated": True}),
        encoding="utf-8",
    )

    assert not module.optimized_query_validated_exists(case_dir)


def test_web_batch_case_detail_keeps_partial_report_untrusted(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 2,
                        "query_id": "def...000002",
                        "score": 7,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "skipped",
                        "report_generated": False,
                        "report_validation_status": "failed_partial_untrusted",
                        "score_reasons": ["analysis warning"],
                        "case_dir": "/tmp/query-doctor-secret-case",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-002"
    request.write_html = write_html
    request.do_GET()
    body = captured["body"]

    assert captured["status"] == 200
    assert "partial untrusted" in body
    assert (
        "No trusted generated report is rendered here. Partial reports remain untrusted."
        not in body
    )
    assert (
        "Profile-based findings remain valid; metadata evidence for follow-up may be limited."
        in body
    )
    assert "Open LLM report" not in body
    assert "Validated report exists" not in body
    assert "/tmp/query-doctor-secret-case" not in body


def test_web_good_query_report_action_is_compact_and_rejected(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 0,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "collected",
                        "score_reasons": [],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    status, body = module.start_batch_case_report_job(
        "case-001",
        settings,
        store,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runner should not be called")
        ),
    )

    assert status == 400
    assert "Python Report is available only for suspicious or bad queries." in body
    assert "Generate Python report" not in body
    assert (
        '<button class="button" type="submit" disabled>Generate Python report</button>' not in body
    )
    assert store.running_batch_report("case-001") is None


def test_web_failed_query_report_action_is_compact_and_rejected(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT secret_col FROM db.source_table"}),
        encoding="utf-8",
    )
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc...000001",
                        "score": 0,
                        "score_severity": "failed",
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "failed",
                        "score_reasons": ["metadata collection failed"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    status, body = module.start_batch_case_report_job(
        "case-001",
        settings,
        store,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runner should not be called")
        ),
    )

    assert status == 400
    assert (
        "Report generation requires successful deterministic processing for this case. "
        "Re-run analysis first."
    ) in body
    assert "Generate LLM report" not in body
    assert "Processing failure follow-up" in body
    assert "SELECT" not in body
    assert "secret_col" not in body
    assert str(case_dir) not in body
    assert store.running_batch_report("case-001") is None


def test_web_batch_report_action_missing_case_dir_renders_typed_safe_details(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc /tmp/case_dir CM_PASSWORD",
                        "user": "alice /Users/example/case_dir",
                        "score": 22,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "metadata_status": "collected",
                        "score_reasons": ["raw stderr /tmp/case_dir qwen3-coder"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    status, body = module.start_batch_case_report_job(
        "case-001",
        settings,
        store,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runner should not be called")
        ),
    )

    assert status == 400
    assert "Report generation requires a complete server-owned case. Re-run analysis first." in body
    assert "Finished Queries details" in body
    assert "abc" in body
    assert "/tmp/" not in body
    assert "/Users/" not in body
    assert "case_dir" not in body
    assert "CM_PASSWORD" not in body
    assert "raw stderr" not in body
    assert "qwen" not in body
    assert store.running_batch_report("case-001") is None


def test_web_running_query_details_use_running_summary_for_report_action(tmp_path):
    module = load_web_module()
    summary = tmp_path / "running_summary.json"
    case_dir = tmp_path / "running-cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "only_running": True,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "running...000001",
                        "score": 9,
                        "score_reasons": ["host-tail candidates: 1"],
                        "case_dir": str(case_dir),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    store.set_latest_running_summary(summary)
    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/batch/case/case-001"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "running...000001" in captured["body"]
    assert 'action="/batch/case/case-001/python-report"' in captured["body"]
    assert 'action="/batch/case/case-001/llm-report"' in captured["body"]
    assert "Generate Python report</button>" in captured["body"]
    assert "Generate LLM narrative</button>" in captured["body"]
    assert "disabled>Generate Python report</button>" not in captured["body"]


def test_web_batch_running_job_page_keeps_form_visible_with_disabled_run():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()
    job = store.create_batch({"min_duration_sec": "12.5", "parallelism": "9"})
    assert job.batch_progress_path is not None
    job.batch_progress_path.parent.mkdir(parents=True, exist_ok=True)
    job.batch_progress_path.write_text(
        json.dumps(
            {
                "stage": "discovery",
                "status": "done",
                "summaries_inspected": 4,
                "candidates_selected": 2,
            }
        )
        + "\n"
        + json.dumps({"stage": "case_processing", "status": "started", "total": 2, "jobs": 9})
        + "\n"
        + json.dumps({"stage": "case", "case_id": "case-001", "status": "collection_done"})
        + "\n",
        encoding="utf-8",
    )
    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job.job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert '<form id="batch-form"' in captured["body"]
    assert '<button class="run-button" type="submit" disabled>Running</button>' in captured["body"]
    assert "Analysis running" in captured["body"]
    assert "Query discovery" in captured["body"]
    assert "Profile collection" in captured["body"]
    assert "cases processed" in captured["body"]
    assert (
        '<span id="job-stage" class="progress-stage">Profile collection</span>' in captured["body"]
    )
    assert 'id="job-progress-fill" class="progress-fill" style="width:25%"' in captured["body"]
    assert (
        'name="min_duration_sec" type="number" min="0" step="0.001" value="12.5"'
        in captured["body"]
    )
    assert 'name="cm_inspect_limit"' not in captured["body"]
    assert 'name="triage_profile_limit"' not in captured["body"]
    assert 'name="collect_cm_timeseries"' not in captured["body"]
    assert 'name="cm_metrics_profile"' not in captured["body"]
    assert 'name="parallelism"' not in captured["body"]
    assert 'name="analysis_depth"' not in captured["body"]
    assert 'name="order"' not in captured["body"]


def test_web_running_queries_page_matches_finished_queries_without_date_hour_filters():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    body = module.render_running_queries_page(settings)

    assert "Running Queries" in body
    assert '<form id="running-form"' in body
    assert 'method="post" action="/running/run"' in body
    assert "Scan currently running Impala queries from the selected source." not in body
    assert "Live scan" in body
    assert "current running-query snapshot" in body
    assert "Live snapshot:" not in body
    assert "current running query summaries" not in body
    assert "Query filters" not in body
    assert "Analysis settings" not in body
    assert 'name="min_duration_sec"' in body
    assert 'name="user"' not in body
    assert 'name="pool"' not in body
    assert 'name="parallelism"' not in body
    assert 'name="metadata_jobs"' not in body
    assert "Advanced settings" not in body
    assert 'name="collect_cm_timeseries"' not in body
    assert 'name="cm_metrics_profile"' not in body
    assert "Scan date" not in body
    assert "Scan Hour" not in body
    assert 'name="scan_date"' not in body
    assert 'name="scan_hour"' not in body
    assert '<form id="batch-form"' not in body


def test_web_running_queries_page_blocks_owner_raw_without_loaded_owner(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        source_visibility="owner_raw",
    )
    settings.config.write_text("{}", encoding="utf-8")

    body = module.render_running_queries_page(settings)

    assert '<form id="running-form"' in body
    assert '<select class="input" id="user" name="user" disabled>' in body
    assert '<option value="" selected>No configured owner</option>' in body
    assert '<button class="run-button" type="submit" disabled>Owner required</button>' in body


def test_web_running_queries_page_places_configured_source_before_live_scan():
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig

    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        clusters=(
            WebClusterConfig(key="prod", label="Production"),
            WebClusterConfig(key="stage", label="Staging"),
        ),
        active_cluster_key="stage",
    )

    body = module.render_running_queries_page(settings)

    assert '<label for="running_cluster_key">Source cluster</label>' in body
    assert '<select class="input" id="running_cluster_key" name="cluster_key">' in body
    assert (
        '<option value="stage" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Staging</option>' in body
    )
    assert body.index('<label for="running_cluster_key">Source cluster</label>') < body.index(
        "Live scan"
    )


def test_web_running_queries_page_shows_configured_advanced_filters(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "web_advanced_settings_enabled": True,
                "web_advanced_filters": ["user", "pool", "query_type"],
                "recent_user": "analyst",
                "recent_pool": "root.running",
                "query_type": "QUERY",
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=config)

    body = module.render_running_queries_page(settings)
    running_config = module.parse_running_run_config(
        {"metadata_top_limit": ["0"]}, settings=settings
    )
    cmd, _out_dir = module.build_batch_command("r" * 32, running_config, settings)

    assert "Advanced settings" in body
    assert "Secondary filters" in body
    assert 'name="user" type="text" value="analyst"' in body
    assert 'name="pool" type="text" value="root.running"' in body
    assert 'name="query_type" type="text" value="QUERY"' in body
    assert running_config.query_type == "QUERY"
    assert cmd[cmd.index("--query-type") + 1] == "QUERY"
    assert "--only-running" in cmd
    assert "Parallelism" not in body
    assert "Metadata parallelism" not in body


def test_web_running_queries_page_does_not_reuse_finished_queries_summary(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    summary.write_text(
        json.dumps(
            {
                "selected_count": 1,
                "cases": [
                    {
                        "query_id": "finished:def",
                        "score": 30,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    store = module.WebJobStore()
    handler = module.make_handler(settings, job_store=store)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/running"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Running Queries" in captured["body"]
    assert "finished:def" not in captured["body"]


def test_web_running_queries_job_builds_only_running_command(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        timeout_sec=77,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n", encoding="utf-8"
        )
        (out_dir / "batch_summary.json").write_text(
            json.dumps(
                {
                    "selected_count": 1,
                    "include_running": True,
                    "only_running": True,
                    "cases": [
                        {
                            "case_index": 1,
                            "query_id": "run:def",
                            "score": 30,
                            "duration_sec": 60,
                            "collection_status": "ok",
                            "analysis_status": "ok",
                            "metadata_status": "skipped",
                            "table_stats_status": "not_checked",
                            "score_reasons": ["memory estimate anomalies: 1"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="SELECT hidden", stderr="raw profile")

    status, location = module.start_running_job(
        {
            "scan_date": ["2026-01-01"],
            "scan_hour": ["1"],
            "min_duration_sec": ["5"],
            "parallelism": ["11"],
            "metadata_jobs": ["2"],
            "cm_events_max_events": ["9"],
            "cm_timeseries_top_limit": ["3"],
            "user": ["alice"],
            "pool": ["root.running"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "running"
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert kwargs["timeout"] == 77
    assert "--only-running" in cmd
    assert "--include-running" in cmd
    assert "--include-failed" not in cmd
    assert "--from-time" not in cmd
    assert "--to-time" not in cmd
    assert cmd[cmd.index("--recent-window-minutes") + 1] == "120"
    assert cmd[cmd.index("--cm-inspect-limit") + 1] == "500"
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "500"
    assert "--collect-cm-timeseries" in cmd
    assert "--collect-cm-events" in cmd
    assert cmd[cmd.index("--cm-events-max-events") + 1] == "9"
    assert cmd[cmd.index("--cm-metrics-profile") + 1] == "cm6"
    assert cmd[cmd.index("--cm-timeseries-top-limit") + 1] == "3"
    assert cmd[cmd.index("--min-duration-sec") + 1] == "5"
    assert cmd[cmd.index("--cm-jobs") + 1] == "11"
    assert cmd[cmd.index("--jobs") + 1] == "11"
    assert cmd[cmd.index("--metadata-jobs") + 1] == "1"
    assert cmd[cmd.index("--user") + 1] == "alice"
    assert cmd[cmd.index("--pool") + 1] == "root.running"
    payload = json.loads(module.render_job_status_json(snapshot))
    assert "Running Queries" in payload["result_html"]
    assert "run:def" in payload["result_html"]
    assert 'data-href="/running/case/case-001"' in payload["result_html"]
    assert 'data-href="/batch/case/case-001"' not in payload["result_html"]
    assert "Status: running only" in payload["result_html"]
    assert "SELECT hidden" not in payload["result_html"]
    assert "raw profile" not in payload["result_html"]

    handler = module.make_handler(settings, job_store=store, runner=fake_runner)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert '<form id="running-form"' in captured["body"]
    assert "Running Queries" in captured["body"]
    assert "run:def" in captured["body"]
    assert "Scan date" not in captured["body"]
    assert "Scan Hour" not in captured["body"]


def test_web_query_inbox_scope_filters_are_preserved_on_running_redirect(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        timeout_sec=77,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n", encoding="utf-8"
        )
        (out_dir / "batch_summary.json").write_text(
            json.dumps({"selected_count": 0, "include_running": True, "only_running": True}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_running_job(
        {
            "metadata_top_limit": ["0"],
            "inbox_source": ["cm"],
            "inbox_workflow": ["running"],
            "inbox_window": ["live"],
            "inbox_query_type": ["QUERY"],
            "inbox_from": ["2026-07-03T08:00Z"],
            "inbox_to": ["2026-07-03T09:30Z"],
            "query_group": ["suspicious"],
            "case_dir": ["private-case-value"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    parsed = urlparse(location)
    assert status == 303
    assert parsed.path.startswith("/jobs/")
    assert parse_qs(parsed.query) == {
        "inbox_source": ["cm"],
        "inbox_workflow": ["running"],
        "inbox_window": ["live"],
        "inbox_query_type": ["QUERY"],
    }
    assert "query_group" not in location
    assert "inbox_from" not in location
    assert "inbox_to" not in location
    assert "case_dir" not in location
    assert "private-case-value" not in location


def test_web_running_job_page_hides_mismatched_query_inbox_scope_snapshot(tmp_path):
    module = load_web_module()
    running_summary = tmp_path / "running_summary.json"
    running_summary.write_text(
        json.dumps(
            {
                "query_profile_source": "cm",
                "include_running": True,
                "only_running": True,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "old-running:def",
                        "score": 20,
                        "score_reasons": ["memory estimate anomalies: 1"],
                        "collection_status": "ok",
                        "analysis_status": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    store.set_latest_running_summary(running_summary)
    job = store.create_running_batch({"min_duration_sec": "5"})
    handler = module.make_handler(settings, job_store=store)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = (
        f"/jobs/{job.job_id}?inbox_source=impala&inbox_workflow=running"
        "&inbox_window=live&inbox_query_type=QUERY"
    )
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert "Analysis running" in captured["body"]
    assert "old-running:def" not in captured["body"]
    assert (
        '<input type="hidden" name="inbox_source" value="impala" data-inbox-scope-hidden>'
        in captured["body"]
    )
    assert (
        '<input type="hidden" name="inbox_workflow" value="running" data-inbox-scope-hidden>'
        in captured["body"]
    )
    assert (
        '<input type="hidden" name="inbox_window" value="live" data-inbox-scope-hidden>'
        in captured["body"]
    )
    assert (
        '<input type="hidden" name="inbox_query_type" value="QUERY" data-inbox-scope-hidden>'
        in captured["body"]
    )


def test_web_recent_scan_target_running_posts_to_running_job(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        timeout_sec=77,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n", encoding="utf-8"
        )
        (out_dir / "batch_summary.json").write_text(
            json.dumps(
                {
                    "selected_count": 0,
                    "include_running": True,
                    "only_running": True,
                    "cases": [],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    handler = module.make_handler(settings, job_store=store, runner=fake_runner)
    request = handler.__new__(handler)
    payload = urlencode(
        {"scan_target": "running", "parallelism": "3", "metadata_jobs": "1"}
    ).encode()
    captured: dict[str, object] = {"headers": []}

    request.path = "/batch/run"
    request.headers = {"Content-Length": str(len(payload))}
    request.rfile = io.BytesIO(payload)
    request.send_response = lambda status: captured.__setitem__("status", status)
    request.send_header = lambda name, value: captured["headers"].append((name, value))
    request.end_headers = lambda: None
    request.write_html = lambda status, body: captured.update({"status": status, "body": body})

    request.do_POST()

    assert captured["status"] == 303
    location = dict(captured["headers"])["Location"]
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    assert snapshot is not None
    assert snapshot.kind == "running"


def test_web_unknown_post_route_rejects_before_reading_body():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    handler = module.make_handler(settings)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    class UnreadableBody(io.BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("unknown POST route should not read the request body")

    request.path = "/not-a-route"
    request.headers = {"Content-Length": str(module.MAX_WEB_POST_BODY_BYTES + 1)}
    request.rfile = UnreadableBody(b"")
    request.send_error = lambda status: captured.__setitem__("status", status)
    request.write_html = lambda status, body: captured.update({"status": status, "body": body})

    request.do_POST()

    assert captured["status"] == 404
    assert "body" not in captured


def test_web_running_case_route_does_not_collide_with_finished_case_ids(tmp_path):
    module = load_web_module()
    finished_summary = tmp_path / "finished_summary.json"
    running_summary = tmp_path / "running_summary.json"
    finished_case_dir = tmp_path / "finished-cases" / "case-001" / "finished"
    running_case_dir = tmp_path / "running-cases" / "case-001" / "running"
    finished_case_dir.mkdir(parents=True)
    running_case_dir.mkdir(parents=True)
    (finished_case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (finished_case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (running_case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (running_case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    finished_summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "finished-query:def",
                        "score": 30,
                        "score_reasons": ["memory estimate anomalies: 1"],
                        "case_dir": str(finished_case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    running_summary.write_text(
        json.dumps(
            {
                "only_running": True,
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "running-query:def",
                        "score": 30,
                        "score_reasons": ["memory estimate anomalies: 1"],
                        "case_dir": str(running_case_dir),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"), batch_summary=finished_summary
    )
    store = module.WebJobStore()
    store.set_latest_running_summary(running_summary)
    handler = module.make_handler(settings, job_store=store)

    def get(path):
        request = handler.__new__(handler)
        captured: dict[str, object] = {}

        def write_html(status, body):
            captured["status"] = status
            captured["body"] = body

        request.path = path
        request.write_html = write_html
        request.do_GET()
        return captured

    running_page = get("/running")
    running_detail = get("/running/case/case-001")
    finished_detail = get("/batch/case/case-001")

    assert 'data-href="/running/case/case-001"' in running_page["body"]
    assert "running-query:def" in running_detail["body"]
    assert "finished-query:def" not in running_detail["body"]
    assert "Running Queries details" in running_detail["body"]
    assert 'action="/running/case/case-001/python-report"' in running_detail["body"]
    assert "finished-query:def" in finished_detail["body"]
    assert "running-query:def" not in finished_detail["body"]


def test_web_root_and_batch_render_batch_page_and_query_route_renders_query_form():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)

    def get(path):
        request = handler.__new__(handler)
        captured: dict[str, object] = {}

        def write_html(status, body):
            captured["status"] = status
            captured["body"] = body

        request.path = path
        request.write_html = write_html
        request.do_GET()
        return captured

    root = get("/")
    batch = get("/batch")
    query = get("/query")
    running = get("/running")

    assert root["status"] == 200
    assert "Query Inbox" in root["body"]
    assert "Batch query triage" not in root["body"]
    assert 'id="batch-form"' in root["body"]
    assert '<form id="analyze-form"' in root["body"]
    assert "Run diagnosis" not in root["body"]
    assert batch["status"] == 200
    assert "Query Inbox" in batch["body"]
    assert "Batch query triage" not in batch["body"]
    assert query["status"] == 200
    assert "Query Inbox" in query["body"]
    assert "One Query ID" in query["body"]
    assert '<form id="batch-form" class="batch-form manual-inputs-hidden"' in query["body"]
    assert '<form id="analyze-form"' in query["body"]
    assert '<a class="nav-link nav-link--active" href="/">Query Inbox</a>' in query["body"]
    assert 'href="/query">Specific Query</a>' not in query["body"]
    assert running["status"] == 200
    assert "Running Queries" in running["body"]
    assert '<a class="nav-link nav-link--active" href="/">Query Inbox</a>' in running["body"]
    assert '<form id="running-form"' in running["body"]
    assert "Scan date" not in running["body"]
    assert "Scan Hour" not in running["body"]


def test_web_batch_form_defaults_and_navigation_are_safe(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web.ui import recent_scan_form

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 5, 3, 18, 25, tzinfo=ZoneInfo("Etc/GMT-3"))
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(recent_scan_form, "datetime", FixedDateTime)
    settings = module.WebSettings(config=tmp_path / "missing-cm-config.json")

    body = module.render_batch_page(settings)
    styles = layout.render_shared_styles()
    script = layout.render_client_script()

    assert "Big Data query diagnostics" in body
    assert "Local-first Impala query diagnostics" not in body
    assert (
        '<form id="batch-form" class="batch-form" method="post" action="/batch/run" '
        'data-scan-target-form data-active-scan-target="finished" '
        'data-diagnosis-target-field="recent">' in body
    )
    assert "Query Inbox" in body
    assert "Scan recent query summaries, or analyze one known Query ID." not in body
    assert "One Query ID" in body
    assert '<summary aria-label="What to analyze help">i</summary>' in body
    assert "Choose the workflow first: completed queries for normal triage" in body
    assert (
        'name="diagnosis_workflow" value="finished" data-diagnosis-workflow-choice checked' in body
    )
    assert 'name="diagnosis_workflow" value="running" data-diagnosis-workflow-choice' in body
    assert 'name="diagnosis_workflow" value="query" data-diagnosis-workflow-choice' in body
    assert "Batch query triage" not in body
    assert "batch triage</span>" not in body
    assert '<button class="run-button" type="submit">Run scan</button>' in body
    assert "Finished scope:" not in body
    assert (
        'type="radio" name="scan_target" value="finished" data-scan-target-choice checked' in body
    )
    assert 'type="radio" name="scan_target" value="running" data-scan-target-choice' in body
    assert (
        '<input type="hidden" name="scan_target" value="finished" data-scan-target-hidden>' in body
    )
    assert '<select class="input" id="scan_target" name="scan_target">' not in body
    assert '<label for="scan_date">Scan date</label>' not in body
    assert 'name="scan_date"' not in body
    assert '<label for="scan_hour">Scan Hour' not in body
    assert 'name="scan_hour"' not in body
    assert '<label for="recent_window_minutes">Search depth (min)</label>' in body
    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="60"' in body
    assert "Query summary lookback for the selected source" in body
    assert "Large Search depth values can increase load on Cloudera Manager" in body
    assert 'data-large-window-threshold="1440"' in body
    assert "Basic scan" in body
    assert "Default triage" in body
    assert "Live snapshot" in body
    assert "Known query" in body
    assert (
        "Choose completed-query evidence or a live running snapshot, then narrow the scan before profile collection."
        not in body
    )
    assert "Secondary filters" not in body
    assert 'name="recent_window_minutes" type="number"' in body
    assert '<details class="batch-advanced">' not in body
    assert "Advanced settings" not in body
    assert "Collection settings" not in body
    assert 'name="cm_inspect_limit"' not in body
    assert 'name="triage_profile_limit"' not in body
    assert 'name="metadata_top_limit"' not in body
    assert "Queries to scan" not in body
    assert "Queries to analyze" not in body
    assert "Queries to fetch metadata for" not in body
    assert "Triage profile limit" not in body
    assert "Metadata top limit" not in body
    assert 'name="min_duration_sec" type="number" min="0" step="0.001" value=""' in body
    assert 'name="user"' not in body
    assert 'name="pool"' not in body
    assert "Max duration sec" not in body
    assert 'name="max_duration_sec"' not in body
    assert 'name="cm_jobs"' not in body
    assert 'name="jobs"' not in body
    assert 'name="parallelism"' not in body
    assert 'name="metadata_jobs"' not in body
    assert 'name="cm_metrics_profile"' not in body
    assert 'name="cm_events_max_events"' not in body
    assert 'name="collect_cm_events"' not in body
    assert 'name="cm_timeseries_top_limit"' not in body
    assert 'name="collect_cm_timeseries"' not in body
    assert "Query type" not in body
    assert 'name="query_type"' not in body
    assert 'name="analysis_depth"' not in body
    assert "Full scan" not in body
    assert "Fast scan" not in body
    assert "Full: bounded metadata for top cases · Fast: profiles only" not in body
    assert '<summary aria-label="Scan mode help">i</summary>' not in body
    assert 'id="scan-mode-help"' not in body
    assert "Full requires metadata settings and enriches only top-ranked cases." not in body
    assert (
        "Fast keeps the run profile-focused when metadata is unavailable or unnecessary."
        not in body
    )
    assert 'name="order"' not in body
    assert "Candidate order" not in body
    assert 'name="out"' not in body
    assert 'name="metadata_coordinator"' not in body
    assert 'name="metadata-impala-shell"' not in body
    assert 'name="top_reports"' not in body
    assert 'name="model"' not in body
    assert "CM_PASSWORD" not in body
    assert "CM_TOKEN" not in body
    assert "Metadata collection is not configured for this web session." not in body
    assert "selected Search depth → matching summaries" not in body
    assert "Live snapshot:" not in body
    assert "no date/hour window is used" not in body
    assert "Maximum recent matching CM summaries to inspect." not in body
    assert (
        "Maximum top-ranked analyzed queries enriched with bounded read-only table metadata. Set to 0 to skip metadata collection."
        not in body
    )
    assert (
        "Leave empty to include long queries and repeated short workload patterns. Set a value to narrow the scan to longer-running queries only."
        in body
    )
    assert 'id="only_with_spills" name="only_with_spills"' not in body
    assert (
        "After analysis, show only result rows with detected non-zero spill or scratch I/O evidence."
        not in body
    )
    assert (
        '<details class="info-popover"><summary aria-label="Queries to scan help">i</summary>'
        not in body
    )
    assert_css_contains(
        styles,
        ".batch-form-grid .field:nth-child(3n) .info-popover .info-body{left:auto;right:0}",
    )
    assert "function closeInfoPopovers(exceptPopover)" in script
    assert "input[data-server-owned-default]" in script
    assert "input.value = input.defaultValue || '';" in script
    assert "data-diagnosis-cluster-summary" not in script
    assert "function currentDiagnosisTarget(root)" in script
    assert "function currentScanTarget(root)" in script
    assert "function syncEngine(root)" in script
    assert "function syncEngineAvailability(root)" in script
    assert "function updateClusterOptionsForEngine(root)" in script
    assert "data-engine-impala-ready" in script
    assert "data-engine-trino-ready" in script
    assert "option.hidden = !visible;" in script
    assert "option.disabled = !visible;" in script
    assert "data-trino-beta-query-ready" in script
    assert "data-trino-beta-recent-ready" in script
    assert "impalaChoice.disabled = !impalaReady;" in script
    assert "trinoChoice.disabled = !trinoReady;" in script
    assert "trinoChoice.setAttribute('aria-disabled', 'true');" in script
    assert "trinoChoice.checked = false;" in script
    assert "impalaChoice.checked = true;" in script
    assert "function anyClusterTrinoReady(root)" in script
    assert "function forceEngine(root, value)" in script
    assert "selector.addEventListener('change', function () {" in script
    assert (
        "syncDiagnosisCluster(root);\n        syncEngineAvailability(root);\n"
        "        if (currentEngine(root) === 'trino' && !selectedClusterTrinoReady(root)) {\n"
        "          forceEngine(root, 'impala');\n        }\n"
        "        syncEngine(root);\n        applyDiagnosisTarget(root);" in script
    )
    assert "function syncKnownQueryEngineCopy(root, engine)" in script
    assert "syncKnownQueryEngineCopy(root, engine);" in script
    assert "Trino Query ID" in script
    assert "20260603_120102_00001_abcde" in script
    assert "Run Trino" in script
    assert "function selectedTrinoDisplayLabel(root)" in script
    assert "data-trino-display-label" in script
    assert "button.setAttribute('data-ready-label', readyLabel);" in script
    assert "function jobPanelTitle(kind, status)" in script
    assert "Trino complete" in script
    assert "Trino stopped" in script
    assert "Trino failed" in script
    assert "kind === 'query' || kind === 'trino_query'" in script
    assert "kind === 'batch' || kind === 'trino_recent'" in script
    assert "kind === 'trino_query' || kind === 'trino_recent' ? trinoRunButtonLabel(root)" in script
    assert (
        "Running scans, query-history crawling, metadata collection, "
        "LLM reports, Query Optimizer jobs, generated SQL, and SQL execution remain "
        "unavailable" in script
    )
    assert "One explicit Query ID. Query Doctor collects or reuses the profile" in script
    assert "function workflowSelection(root)" in script
    assert "function enforceTrinoWorkflow(root)" in script
    assert "selectedClusterTrinoRecentReady(root)" in script
    assert "function syncWorkflowState(root)" in script
    assert "form.setAttribute('data-active-scan-target', target);" in script
    assert "function updateRecentResultsContext(scanTarget, diagnosisTarget)" in script
    assert "Previous Recent Results" in script
    assert "Previous Finished Queries" in script
    assert "results.removeAttribute('open');" in script
    assert "data-query-mode-results" in script
    assert "popover.removeAttribute('open');" in script
    assert "event.target.closest('.info-popover')" in script
    assert_css_contains(
        styles,
        ".batch-form .run-button{min-height:38px;width:148px;max-width:100%;justify-self:start;",
    )
    assert_css_contains(styles, ".diagnosis-cluster-control{width:260px;max-width:100%}")
    assert_css_contains(styles, ".batch-source-settings{display:block;width:min(360px,100%);")
    assert_css_contains(styles, ".batch-run-panel .input{min-height:36px;padding:0 10px}")
    assert_css_contains(
        styles,
        ".field label,.mode-control>span,.mode-control .mode-label{color:var(--strong);",
    )
    assert_css_contains(
        styles,
        ".batch-form-grid--simple{grid-template-columns:minmax(160px,1fr) "
        "minmax(160px,1fr) minmax(140px,1fr) minmax(150px,1fr) minmax(120px,148px);"
        "align-items:end}",
    )
    assert_css_contains(
        styles,
        ".engine-segmented,.workflow-segmented{gap:0;width:100%;min-height:44px;",
    )
    assert_css_contains(
        styles, ".workflow-segmented{grid-template-columns:repeat(3,minmax(0,1fr));"
    )
    assert_css_contains(styles, ".engine-control{display:grid;align-items:start;gap:6px;")
    assert_css_contains(styles, ".workflow-control{display:grid;align-items:start;gap:6px;")
    assert_css_contains(
        styles,
        ".engine-segmented label+label,.workflow-segmented label+label{"
        "border-left:1px solid var(--border-strong)}",
    )
    assert_css_contains(
        styles,
        ".engine-segmented span,.workflow-segmented span{align-content:center;justify-items:start;",
    )
    assert ".batch-scan-options" not in styles
    assert_css_contains(
        styles,
        ".batch-form[data-active-scan-target=running] .batch-form-grid--simple{"
        "grid-template-columns:minmax(160px,260px);"
        "justify-content:start}",
    )
    assert_css_contains(
        styles,
        ".batch-form[data-active-scan-target=running] .batch-run-action{grid-column:auto}",
    )
    assert_css_contains(styles, ".segmented input:focus-visible+span{box-shadow:")
    assert_css_contains(styles, "select.input{appearance:none;padding-right:42px;")
    assert_css_contains(
        styles, ".batch-filter-row{display:grid;grid-template-columns:96px minmax(0,1fr);"
    )
    assert_css_contains(
        styles, ".batch-result-filters{display:flex;flex-wrap:wrap;align-items:center;"
    )
    assert_css_contains(styles, ".batch-result-filter-row{display:flex;align-items:flex-start;")
    assert_css_contains(
        styles,
        ".batch-progress-steps{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:6px}",
    )
    assert_css_contains(styles, "#job-result-slot:not(:empty){margin-top:16px}")
    assert "resultSlot && !resultSlot.querySelector('#recent-results')" in script
    assert 'class="run-button run-button--full-width"' not in body
    assert body.index("What to analyze") < body.index("Basic scan")
    assert body.index("Search depth") < body.index("Minimum duration")
    assert body.index("Minimum duration (sec)") < body.index(
        '<button class="run-button" type="submit">Run scan</button>'
    )
    assert "More scan options" not in body
    assert "Scan preset" not in body
    assert 'name="scan_preset"' not in body
    assert body.index("Minimum duration") < body.index(
        '<button class="run-button" type="submit">Run scan</button>'
    )
    assert body.index("Search depth") < body.index(
        '<button class="run-button" type="submit">Run scan</button>'
    )
    assert "Include failed" not in body
    assert "Include running" not in body
    assert 'name="include_failed"' not in body
    assert 'name="include_running"' not in body
    assert "Parallelism" not in body
    assert "Queries to fetch metadata for" not in body
    assert "Metadata parallelism" not in body
    assert "Full: --metadata-mode on with server-startup metadata settings" not in body
    assert "Fast: --metadata-mode off; metadata top limit is ignored" not in body
    assert "Always: --top-reports 0; no LLM report generation" not in body
    assert "generated dedicated /tmp/query-doctor-web-batch-*" not in body
    assert "Rendered summaries are read-only" not in body
    assert "Credentials: environment or local config only; never entered here" not in body


def test_web_batch_form_running_target_hides_finished_scope(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "missing-cm-config.json")

    body = module.render_batch_page(settings, form_values={"scan_target": "running"})

    assert (
        '<form id="batch-form" class="batch-form" method="post" action="/running/run" '
        'data-scan-target-form data-active-scan-target="running" '
        'data-diagnosis-target-field="recent">' in body
    )
    assert "Finished scope:" not in body
    assert "Running scope:" not in body
    assert "Advanced settings" not in body
    assert (
        'name="diagnosis_workflow" value="running" data-diagnosis-workflow-choice checked' in body
    )
    assert 'type="radio" name="scan_target" value="running" data-scan-target-choice checked' in body
    assert (
        '<input type="hidden" name="scan_target" value="running" data-scan-target-hidden>' in body
    )
    assert 'batch-target-field manual-inputs-hidden" data-scan-target-field="finished">' in body
    assert "Running now:</strong> live snapshot of currently executing queries." not in body
    assert "running queries for a live lower-confidence snapshot" in body


def test_web_batch_status_filters_are_fixed_server_side():
    module = load_web_module()

    config = module.parse_batch_run_config(
        {"include_failed": ["off"], "include_running": ["on"], "metadata_top_limit": ["0"]}
    )

    assert config.include_failed is True
    assert config.include_running is False


def test_web_batch_scan_date_labels_include_year_and_today_hours_exclude_later_hours():
    from query_doctor.web.ui import recent_scan_form as form

    now = datetime(2026, 5, 3, 18, 25, tzinfo=ZoneInfo("Etc/GMT-3"))

    assert form.recent_scan_date_options(now=now) == [
        ("2026-05-03", "03.05.2026"),
        ("2026-05-02", "02.05.2026"),
        ("2026-05-01", "01.05.2026"),
    ]
    today_hours = form.scan_hour_options("2026-05-03", now=now)
    previous_day_hours = form.scan_hour_options("2026-05-02", now=now)

    assert today_hours[-1] == ("15", "15:00 - 16:00")
    assert ("16", "16:00 - 17:00") not in today_hours
    assert previous_day_hours[-1] == ("23", "23:00 - 00:00")

    hour_options_by_date = form.scan_hour_options_by_date(
        form.recent_scan_date_options(now=now), now=now
    )
    assert hour_options_by_date["2026-05-03"][-1] == ("15", "15:00 - 16:00")
    assert hour_options_by_date["2026-05-02"][-1] == ("23", "23:00 - 00:00")


def test_web_batch_form_uses_configured_recent_window_default(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web import batch_scan
    from query_doctor.web.ui import recent_scan_form

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 5, 3, 18, 25, tzinfo=ZoneInfo("Etc/GMT-3"))
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(recent_scan_form, "datetime", FixedDateTime)
    monkeypatch.setattr(batch_scan, "datetime", FixedDateTime)
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps({"recent_scan_timezone": "UTC", "recent_window_minutes": 90}),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)
    batch_config = module.parse_batch_run_config(
        {"scan_date": ["2026-05-03"], "scan_hour": ["15"], "metadata_top_limit": ["0"]},
        settings=settings,
    )

    assert '<label for="scan_hour">Scan Hour' not in body
    assert '<label for="recent_window_minutes">Search depth (min)</label>' in body
    assert batch_config.recent_window_minutes == 90
    assert batch_config.from_time is None
    assert batch_config.to_time is None


def test_web_batch_form_rejects_invalid_search_depth_without_subprocess():
    module = load_web_module()
    status, body = module.start_batch_job(
        {"recent_window_minutes": ["0"], "metadata_top_limit": ["0"]},
        module.WebSettings(config=Path(".query-doctor-cm.local.json")),
        module.WebJobStore(),
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid search depth must not run subprocess")
        ),
    )

    assert status == 400
    assert "recent_window_minutes must be a positive integer." in body


def test_web_batch_form_rejects_invalid_direct_impala_search_depth_without_subprocess(tmp_path):
    module = load_web_module()
    config = tmp_path / "direct-config.json"
    config.write_text(
        json.dumps(
            {
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    status, body = module.start_batch_job(
        {
            "recent_window_minutes": ["0"],
            "metadata_top_limit": ["0"],
        },
        settings,
        module.WebJobStore(),
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid search depth must not run subprocess")
        ),
    )

    assert status == 400
    assert "recent_window_minutes must be a positive integer." in body


def test_web_batch_form_rejects_unsafe_query_type_without_subprocess(tmp_path):
    module = load_web_module()

    status, body = module.start_batch_job(
        {
            "query_type": ["SELECT hidden"],
            "metadata_top_limit": ["0"],
        },
        module.WebSettings(config=tmp_path / "cm-config.json"),
        module.WebJobStore(),
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe query type must not run subprocess")
        ),
    )

    assert status == 400
    assert "Query type filter must be a short identifier" in body
    assert "SELECT hidden" not in body


def test_web_batch_form_uses_local_recent_config_defaults(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_window_minutes": 60,
                "recent_cm_jobs": 12,
                "recent_cm_summary_limit": 4321,
                "recent_profile_analysis_limit": 123,
                "recent_metadata_jobs": 3,
                "recent_metadata_top_limit": 7,
                "recent_min_duration_sec": 1.5,
                "recent_order": "recent",
                "recent_include_failed": True,
                "recent_include_running": True,
                "recent_user": "impala_user",
                "recent_pool": "root.analytics",
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)
    running_body = module.render_running_queries_page(settings)
    batch_config = module.parse_batch_run_config({"metadata_top_limit": ["0"]}, settings=settings)
    running_config = module.parse_running_run_config(
        {"metadata_top_limit": ["0"]}, settings=settings
    )

    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="60"' in body
    assert 'name="cm_jobs"' not in body
    assert 'name="parallelism"' not in body
    assert 'name="cm_inspect_limit"' not in body
    assert 'name="triage_profile_limit"' not in body
    assert 'name="metadata_jobs"' not in body
    assert 'name="metadata_top_limit"' not in body
    assert 'name="min_duration_sec" type="number" min="0" step="0.001" value=""' in body
    assert 'name="min_duration_sec" type="number" min="0" step="0.001" value=""' in running_body
    assert 'name="order"' not in body
    assert 'name="user"' not in body
    assert 'name="pool"' not in body
    assert 'name="include_failed"' not in body
    assert 'name="include_running"' not in body
    assert batch_config.min_duration_sec is None
    assert batch_config.recent_window_minutes == 60
    assert batch_config.parallelism == 12
    assert batch_config.metadata_jobs == 3
    assert batch_config.user == "impala_user"
    assert batch_config.pool == "root.analytics"
    assert running_config.parallelism == 12
    assert running_config.metadata_jobs == 3
    assert running_config.user == "impala_user"
    assert running_config.pool == "root.analytics"


def test_web_batch_empty_min_duration_includes_all_patterns_by_default(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"recent_min_duration_sec": 60}), encoding="utf-8")
    settings = module.WebSettings(config=config, repo_dir=REPO_DIR)

    body = module.render_batch_page(settings)
    batch_config = module.parse_batch_run_config(
        {
            "min_duration_sec": [""],
            "metadata_top_limit": ["0"],
        },
        settings=settings,
    )
    cmd, _out_dir = module.build_batch_command("f" * 32, batch_config, settings)

    assert 'name="min_duration_sec" type="number" min="0" step="0.001" value=""' in body
    assert "More scan options" not in body
    assert "Scan preset" not in body
    assert 'name="scan_preset"' not in body
    assert "Frequent short removes the minimum-duration default" not in body
    assert batch_config.scan_preset == "standard"
    assert batch_config.min_duration_sec is None
    assert batch_config.order == "duration-desc"
    assert cmd[cmd.index("--order") + 1] == "duration-desc"
    assert "--no-min-duration-filter" in cmd
    assert "--min-duration-sec" not in cmd
    assert cmd[cmd.index("--cm-inspect-limit") + 1] == "5000"


def test_web_batch_explicit_min_duration_keeps_long_query_filter(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"recent_min_duration_sec": 60}), encoding="utf-8")
    settings = module.WebSettings(config=config, repo_dir=REPO_DIR)

    batch_config = module.parse_batch_run_config(
        {
            "min_duration_sec": ["60"],
            "metadata_top_limit": ["0"],
        },
        settings=settings,
    )
    cmd, _out_dir = module.build_batch_command("e" * 32, batch_config, settings)

    assert batch_config.scan_preset == "standard"
    assert batch_config.min_duration_sec == 60
    assert batch_config.order == "duration-desc"
    assert cmd[cmd.index("--order") + 1] == "duration-desc"
    assert cmd[cmd.index("--min-duration-sec") + 1] == "60"
    assert "--no-min-duration-filter" not in cmd


def test_web_batch_form_hides_advanced_filters_when_only_window_depth_is_configured(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"recent_window_minutes": 60}), encoding="utf-8")
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)

    assert "Advanced settings" not in body
    assert 'name="pool"' not in body
    assert 'name="pool" type="text" value="60"' not in body


def test_web_batch_form_shows_configured_advanced_filters_only_when_enabled(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "web_advanced_settings_enabled": True,
                "web_advanced_filters": ["pool"],
                "recent_pool": "root.analytics",
                "recent_user": "impala_user",
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)

    assert '<details class="batch-advanced">' in body
    assert "Advanced settings" in body
    assert "Secondary filters" in body
    assert 'name="pool" type="text" value="root.analytics"' in body
    assert 'name="user" type="text" value="impala_user"' not in body
    assert body.index('<button class="run-button" type="submit">Run scan</button>') < body.index(
        '<details class="batch-advanced"><summary>Advanced settings</summary>'
    )


def test_web_batch_form_can_show_query_type_advanced_filter(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "web_advanced_settings_enabled": True,
                "web_advanced_filters": ["query_type"],
                "query_type": "QUERY",
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=config, repo_dir=REPO_DIR)

    body = module.render_batch_page(settings)
    batch_config = module.parse_batch_run_config({"metadata_top_limit": ["0"]}, settings=settings)
    cmd, _out_dir = module.build_batch_command("q" * 32, batch_config, settings)

    assert "Advanced settings" in body
    assert "Query type" in body
    assert 'name="query_type" type="text" value="QUERY"' in body
    assert batch_config.query_type == "QUERY"
    assert cmd[cmd.index("--query-type") + 1] == "QUERY"


def test_web_batch_form_renders_configured_24_hour_search_depth_as_standard_option(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"recent_window_minutes": 1440}), encoding="utf-8")
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)

    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="1440"' in body
    assert "Search depth" in body
    assert "Scan date" not in body
    assert "Large Search depth values can increase load" in body
    assert 'batch-note batch-note--large-window manual-inputs-hidden"' in body


def test_web_batch_form_renders_nonstandard_configured_search_depth(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"recent_window_minutes": 1080}), encoding="utf-8")
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)

    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="1080"' in body
    assert "18 hours (configured)" not in body
    assert "Search depth" in body
    assert "Scan date" not in body


def test_web_batch_form_uses_search_depth_for_direct_impala_recent(tmp_path):
    module = load_web_module()
    config = tmp_path / "direct-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_window_minutes": 43200,
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
                "metadata_coordinator": "impala-coordinator.example.com:21000",
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    body = module.render_batch_page(settings)
    batch_config = module.parse_batch_run_config({"metadata_top_limit": ["0"]}, settings=settings)
    cmd, _out_dir = module.build_batch_command("d" * 32, batch_config, settings)

    assert '<label for="recent_window_minutes">Search depth (min)</label>' in body
    assert 'name="recent_window_minutes" type="number" min="1" step="1" value="43200"' in body
    assert "Query summary lookback for the selected source" in body
    assert "Large Search depth values can increase load on Cloudera Manager" in body
    assert 'batch-note batch-note--large-window" data-large-window-warning' in body
    assert '<label for="scan_date">Scan date</label>' not in body
    assert '<label for="scan_hour">Scan Hour' not in body
    assert batch_config.recent_window_minutes == 43200
    assert batch_config.from_time is None
    assert batch_config.to_time is None
    assert cmd[cmd.index("--recent-window-minutes") + 1] == "43200"
    assert "--from-time" not in cmd
    assert "--to-time" not in cmd


def test_web_batch_form_does_not_use_metadata_max_tables_as_metadata_top_default(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"metadata_max_tables": 5}), encoding="utf-8")
    settings = module.WebSettings(config=config)

    body = module.render_batch_page(settings)

    assert 'name="metadata_top_limit"' not in body


def test_web_batch_form_defaults_to_full_when_metadata_configured(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        metadata_coordinator="impala.example.com:21000",
    )

    body = module.render_batch_page(settings)

    assert 'name="analysis_depth"' not in body
    assert 'name="metadata_top_limit"' not in body
    assert 'name="parallelism"' not in body
    assert "Full scan" not in body
    assert "Fast scan" not in body
    assert "scanModeHelp.textContent" not in body
    assert "Metadata collection is configured for this web session." not in body
    assert "Metadata collection is not configured for this web session." not in body


def test_web_batch_job_builds_safe_analyzer_only_command(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        timeout_sec=77,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert isinstance(cmd, list)
        assert "shell" not in kwargs
        assert kwargs["cwd"] == str(REPO_DIR)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 77
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        assert progress_path == out_dir / "progress.jsonl"
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            "\n".join(
                [
                    json.dumps({"stage": "discovery", "status": "started"}),
                    json.dumps(
                        {
                            "stage": "discovery",
                            "status": "done",
                            "summaries_inspected": "<script>9</script>",
                            "candidates_selected": 1,
                            "duration_filter": "server-side",
                        }
                    ),
                    json.dumps(
                        {"stage": "case_processing", "status": "started", "total": 1, "jobs": 20}
                    ),
                    json.dumps(
                        {"stage": "case", "case_id": "case-001", "status": "collection_done"}
                    ),
                    json.dumps(
                        {
                            "stage": "case",
                            "case_id": "case-001",
                            "status": "analysis_done",
                            "score": 0,
                        }
                    ),
                    json.dumps({"stage": "summary", "status": "done"}),
                    json.dumps({"stage": "batch", "status": "done"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(
            json.dumps(
                {
                    "selected_count": 1,
                    "jobs": 20,
                    "cases": [
                        {
                            "query_id": "abc:def",
                            "score": 12,
                            "score_severity": "suspicious",
                            "collection_status": "ok",
                            "analysis_status": "ok",
                            "metadata_status": "skipped",
                            "score_reasons": [
                                "spill/scratch evidence: non-zero metrics",
                                "no analyzer-supported suspicious facts",
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="SELECT hidden", stderr="raw profile")

    status, location = module.start_batch_job(
        {
            "analysis_depth": ["fast"],
            "recent_window_minutes": ["1440"],
            "cm_inspect_limit": ["1000"],
            "triage_profile_limit": ["200"],
            "min_duration_sec": ["10.5"],
            "max_duration_sec": ["99"],
            "order": ["duration-desc"],
            "jobs": ["20"],
            "user": ["alice"],
            "pool": ["root.pool"],
            "query_type": ["QUERY"],
            "include_failed": ["on"],
            "include_running": ["on"],
            "collect_cm_events": ["on"],
            "cm_events_max_events": ["8"],
            "collect_cm_timeseries": ["on"],
            "cm_timeseries_top_limit": ["12"],
            "out": ["/etc/query-doctor-unsafe"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.kind == "batch"
    assert snapshot.status == "ok"
    assert len(calls) == 1
    cmd, _kwargs = calls[0]
    assert command_uses_role(cmd, "batch_recent")
    assert cmd[cmd.index("--config") + 1] == str(settings.config)
    out_dir = Path(cmd[cmd.index("--out") + 1])
    assert str(out_dir).startswith("/tmp/query-doctor-web-batch-")
    assert str(out_dir).endswith(job_id)
    assert "/etc/query-doctor-unsafe" not in cmd
    assert cmd[cmd.index("--metadata-mode") + 1] == "off"
    assert cmd[cmd.index("--top-reports") + 1] == "0"
    assert cmd[cmd.index("--cm-jobs") + 1] == "20"
    assert cmd[cmd.index("--jobs") + 1] == "20"
    assert cmd[cmd.index("--metadata-jobs") + 1] == "1"
    assert cmd[cmd.index("--cm-inspect-limit") + 1] == "5000"
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "200"
    assert cmd[cmd.index("--min-duration-sec") + 1] == "10.5"
    assert cmd[cmd.index("--metadata-top-limit") + 1] == "0"
    assert "--progress-jsonl" in cmd
    assert Path(cmd[cmd.index("--progress-jsonl") + 1]) == out_dir / "progress.jsonl"
    assert "--allow-high-jobs" in cmd
    assert "--metadata-coordinator" not in cmd
    assert "--metadata-impala-shell" not in cmd
    assert "--top-reports" in cmd
    assert "--max-duration-sec" in cmd
    assert cmd[cmd.index("--max-duration-sec") + 1] == "99"
    assert cmd[cmd.index("--user") + 1] == "alice"
    assert cmd[cmd.index("--pool") + 1] == "root.pool"
    assert cmd[cmd.index("--query-type") + 1] == "QUERY"
    assert "--include-failed" in cmd
    assert "--include-running" not in cmd
    assert "--collect-cm-events" in cmd
    assert cmd[cmd.index("--cm-events-max-events") + 1] == "8"
    assert "--collect-cm-timeseries" in cmd
    assert cmd[cmd.index("--cm-metrics-profile") + 1] == "cm6"
    assert cmd[cmd.index("--cm-timeseries-top-limit") + 1] == "12"
    payload = json.loads(module.render_job_status_json(snapshot))
    assert payload["progress"] == 100
    assert "Finished Queries" in payload["result_html"]
    assert "Query discovery" in payload["progress_html"]
    assert "&lt;script&gt;9&lt;/script&gt;" in payload["progress_html"]
    assert "<script>9</script>" not in payload["progress_html"]
    assert "SELECT hidden" not in payload["result_html"]
    assert "raw profile" not in payload["result_html"]

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}?query_group=suspicious&only_with_spills=on"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert '<form id="batch-form"' in captured["body"]
    assert '<button class="run-button" type="submit">Run scan</button>' in captured["body"]
    assert "Analysis complete" in captured["body"]
    assert "Query discovery" in captured["body"]
    assert "Profile collection" in captured["body"]
    assert "Analyzer scoring" in captured["body"]
    assert "Metadata refresh" in captured["body"]
    assert "Ranking / summary" in captured["body"]
    assert "Completed" in captured["body"]
    assert 'id="job-progress-fill" class="progress-fill" style="width:100%"' in captured["body"]
    assert "&lt;script&gt;9&lt;/script&gt;" in captured["body"]
    assert "<script>9</script>" not in captured["body"]
    assert (
        'name="min_duration_sec" type="number" min="0" step="0.001" value="10.5"'
        in captured["body"]
    )
    assert 'name="max_duration_sec"' not in captured["body"]
    assert 'name="triage_profile_limit"' not in captured["body"]
    assert 'name="metadata_top_limit"' not in captured["body"]
    assert 'name="parallelism"' not in captured["body"]
    assert 'name="jobs"' not in captured["body"]
    assert 'name="analysis_depth"' not in captured["body"]
    assert 'name="user" type="text" value="alice"' not in captured["body"]
    assert 'name="pool" type="text" value="root.pool"' not in captured["body"]
    assert 'class="batch-spill-toggle batch-spill-toggle--active"' in captured["body"]
    assert 'aria-pressed="true"' in captured["body"]
    assert 'href="/?query_group=suspicious#recent-results"' in captured["body"]
    assert 'name="order"' not in captured["body"]
    assert "abc:def" in captured["body"]
    assert captured["body"].index('<form id="batch-form"') < captured["body"].index("abc:def")
    assert captured["body"].count('class="batch-table-wrap"') == 1

    captured.clear()
    request.path = "/?query_group=suspicious"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert '<form id="batch-form"' in captured["body"]
    assert '<button class="run-button" type="submit">Run scan</button>' in captured["body"]
    assert "abc:def" in captured["body"]


def test_web_job_not_found_result_filters_link_back_to_home(tmp_path):
    module = load_web_module()
    summary = tmp_path / "batch_summary.json"
    case_dir = tmp_path / "cases" / "case-001" / "abc"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    summary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_index": 1,
                        "query_id": "abc:def",
                        "score": 9,
                        "score_severity": "suspicious",
                        "duration_sec": 10.5,
                        "collection_status": "ok",
                        "analysis_status": "ok",
                        "score_reasons": ["memory estimate anomalies: 1"],
                        "case_dir": str(case_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary)
    handler = module.make_handler(settings, analysis_func=lambda *args, **kwargs: None)
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = "/jobs/00000000000000000000000000000000"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 404
    assert 'href="/?query_group=suspicious#recent-results"' in captured["body"]
    assert 'href="/?query_group=all#recent-results"' in captured["body"]
    assert 'href="/jobs/00000000000000000000000000000000?query_group=' not in captured["body"]


def test_web_batch_progress_renders_skipped_metadata_refresh(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                json.dumps({"stage": "discovery", "status": "done", "candidates_selected": 1}),
                json.dumps({"stage": "case_processing", "status": "done", "total": 1}),
                json.dumps({"stage": "case", "case_id": "case-001", "status": "analysis_done"}),
                json.dumps(
                    {
                        "stage": "metadata_refresh",
                        "status": "skipped",
                        "reason": "metadata disabled",
                    }
                ),
                json.dumps({"stage": "summary", "status": "done"}),
                json.dumps({"stage": "batch", "status": "done"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    body = module.render_batch_progress_panel(progress_path, "ok")

    assert "Metadata refresh" in body
    assert "batch-progress-step--skipped" in body
    assert "metadata disabled" in body


def test_web_batch_progress_keeps_early_stage_events_for_large_batches(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    events = [
        {
            "stage": "discovery",
            "status": "done",
            "summaries_inspected": 6100,
            "candidates_selected": 2501,
        },
        {"stage": "case_processing", "status": "started", "total": 2501, "jobs": 50},
    ]
    events.extend(
        {"stage": "case", "case_id": f"case-{index:03d}", "status": "collection_done"}
        for index in range(1, 2502)
    )
    progress_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    body = module.render_batch_progress_panel(progress_path, "running")

    assert "6100" in body
    assert "2501 selected" in body
    assert "2501/2501" in body
    assert "Profile collection" in body
    assert "batch-progress-step--done" in body


def test_web_batch_progress_explains_running_discovery_for_broad_cm_windows(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        json.dumps({"stage": "discovery", "status": "started"}) + "\n",
        encoding="utf-8",
    )

    body = module.render_batch_progress_panel(progress_path, "running")

    assert "… Query discovery" in body
    assert "reading query summaries; broad CM windows can take a minute" in body


def test_web_batch_progress_advances_stages_incrementally(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                json.dumps({"stage": "discovery", "status": "done", "candidates_selected": 2}),
                json.dumps(
                    {"stage": "case_processing", "status": "started", "total": 2, "jobs": 2}
                ),
                json.dumps({"stage": "case", "case_id": "case-001", "status": "collection_done"}),
                json.dumps({"stage": "case", "case_id": "case-002", "status": "collection_done"}),
                json.dumps({"stage": "case", "case_id": "case-001", "status": "analysis_started"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    body = module.render_batch_progress_panel(progress_path, "running")

    assert "✓ Query discovery" in body
    assert "✓ Profile collection" in body
    assert "… Analyzer scoring" in body
    assert "· Runtime metrics" in body
    assert "· Metadata refresh" in body
    assert module.batch_progress_percent(progress_path, "running") == 38


def test_web_batch_progress_keeps_collection_running_during_streaming_analysis(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                json.dumps({"stage": "discovery", "status": "done", "candidates_selected": 3}),
                json.dumps(
                    {"stage": "profile_collection", "status": "started", "total": 3, "cm_jobs": 2}
                ),
                json.dumps(
                    {"stage": "analyzer_scoring", "status": "started", "total": 3, "jobs": 2}
                ),
                json.dumps({"stage": "case", "case_id": "case-001", "status": "collection_done"}),
                json.dumps({"stage": "case", "case_id": "case-001", "status": "analysis_started"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    body = module.render_batch_progress_panel(progress_path, "running")

    assert "✓ Query discovery" in body
    assert "… Profile collection" in body
    assert "✓ Profile collection" not in body
    assert "… Analyzer scoring" in body
    assert "1/3" in body
    assert module.batch_progress_percent(progress_path, "running") == 12


def test_web_batch_progress_renders_step_timings(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "stage": "discovery",
                        "status": "done",
                        "candidates_selected": 2,
                        "seconds": 1.2,
                    }
                ),
                json.dumps(
                    {
                        "stage": "cm_events",
                        "status": "done",
                        "product_status": "cluster_context_clean",
                        "seconds": 0.4,
                    }
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-001",
                        "status": "collection_done",
                        "seconds": 1.0,
                    }
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-002",
                        "status": "collection_done",
                        "seconds": 1.5,
                    }
                ),
                json.dumps(
                    {"stage": "profile_collection", "status": "done", "total": 2, "seconds": 2.5}
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-001",
                        "status": "analysis_done",
                        "seconds": 1.4,
                    }
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-002",
                        "status": "analysis_done",
                        "seconds": 1.6,
                    }
                ),
                json.dumps(
                    {"stage": "analyzer_scoring", "status": "done", "total": 2, "seconds": 3.0}
                ),
                json.dumps(
                    {
                        "stage": "cm_timeseries_refresh",
                        "case_id": "case-001",
                        "status": "done",
                        "seconds": 4.5,
                    }
                ),
                json.dumps(
                    {
                        "stage": "cm_timeseries_refresh",
                        "status": "done",
                        "total": 1,
                        "jobs": 1,
                        "seconds": 4.5,
                    }
                ),
                json.dumps(
                    {
                        "stage": "metadata_refresh",
                        "case_id": "case-001",
                        "status": "done",
                        "seconds": 0.8,
                    }
                ),
                json.dumps(
                    {"stage": "metadata_refresh", "status": "done", "total": 1, "seconds": 0.8}
                ),
                json.dumps({"stage": "summary", "status": "done", "seconds": 0.1}),
                json.dumps({"stage": "batch", "status": "done", "total_seconds": 12.3}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    body = module.render_batch_progress_panel(progress_path, "ok")

    assert "Cluster event context" in body
    assert "2 selected, elapsed 1.2s" in body
    assert "cluster_context_clean, elapsed 0.4s" in body
    assert "2/2, elapsed 2.5s" in body
    assert "1/1 refreshed, elapsed 4.5s" in body
    assert "1/1 refreshed, elapsed 0.8s" in body
    assert "summary written, elapsed 0.1s" in body
    assert "batch done, total elapsed 12s" in body


def test_web_batch_progress_does_not_show_parallel_worker_time_as_elapsed(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                json.dumps({"stage": "discovery", "status": "done", "candidates_selected": 3}),
                json.dumps(
                    {"stage": "case_processing", "status": "started", "total": 3, "jobs": 3}
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-001",
                        "status": "collection_done",
                        "seconds": 120.0,
                    }
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-002",
                        "status": "collection_done",
                        "seconds": 120.0,
                    }
                ),
                json.dumps(
                    {
                        "stage": "case",
                        "case_id": "case-001",
                        "status": "analysis_done",
                        "seconds": 90.0,
                    }
                ),
                json.dumps(
                    {"stage": "cm_timeseries_refresh", "status": "started", "total": 2, "jobs": 2}
                ),
                json.dumps(
                    {
                        "stage": "cm_timeseries_refresh",
                        "case_id": "case-001",
                        "status": "done",
                        "seconds": 60.0,
                    }
                ),
                json.dumps(
                    {"stage": "metadata_refresh", "status": "started", "total": 2, "jobs": 2}
                ),
                json.dumps(
                    {
                        "stage": "metadata_refresh",
                        "case_id": "case-001",
                        "status": "done",
                        "seconds": 30.0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    body = module.render_batch_progress_panel(progress_path, "running")

    assert "2/3, elapsed" not in body
    assert "1/3, elapsed" not in body
    assert "1/2 refreshed, elapsed" not in body
    assert "2/3" in body
    assert "1/3" in body
    assert "1/2 refreshed" in body


def test_web_batch_progress_renders_cm_metrics_refresh(tmp_path):
    module = load_web_module()
    progress_path = tmp_path / "progress.jsonl"
    events = [
        {"stage": "discovery", "status": "done", "candidates_selected": 2},
        {"stage": "case_processing", "status": "started", "total": 2, "jobs": 2},
        {"stage": "case", "case_id": "case-001", "status": "collection_done"},
        {"stage": "case", "case_id": "case-002", "status": "collection_done"},
        {"stage": "case", "case_id": "case-001", "status": "analysis_done"},
        {"stage": "case", "case_id": "case-002", "status": "analysis_done"},
        {"stage": "cm_timeseries_refresh", "status": "started", "total": 2, "jobs": 2},
        {"stage": "cm_timeseries_refresh", "case_id": "case-001", "status": "started"},
        {"stage": "cm_timeseries_refresh", "case_id": "case-002", "status": "started"},
        {"stage": "cm_timeseries_refresh", "case_id": "case-001", "status": "done"},
    ]
    progress_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    body = module.render_batch_progress_panel(progress_path, "running")

    assert "… Runtime metrics" in body
    assert "1/2 refreshed, 1 active" in body
    assert "· Metadata refresh" in body
    assert module.batch_progress_percent(progress_path, "running") == 50

    store = module.WebJobStore()
    snapshot = store.create_batch()
    assert snapshot.batch_progress_path is not None
    snapshot.batch_progress_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.batch_progress_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    payload = json.loads(module.render_job_status_json(snapshot))

    assert payload["progress"] == 50
    assert payload["progress_view"]["percent"] == 50
    assert payload["progress_view"]["current_stage"] == "Runtime metrics"
    assert payload["progress_view"]["steps"][4] == {
        "label": "Runtime metrics",
        "state": "running",
        "icon": "…",
        "detail": "1/2 refreshed, 1 active",
    }


def test_web_batch_job_progress_path_uses_configured_recent_batch_root(tmp_path):
    module = load_web_module()
    recent_batch_root = tmp_path / "query-doctor-web-batches"
    store = module.WebJobStore()

    snapshot = store.create_batch(batch_root=recent_batch_root)

    assert snapshot.batch_progress_path == recent_batch_root / (
        f"query-doctor-web-batch-{snapshot.job_id}/progress.jsonl"
    )


@pytest.mark.parametrize(
    ("env_cache", "expected_cache"),
    [
        (None, "FILE:/tmp/krb5cc_web_config"),
        ("FILE:/tmp/krb5cc_web_env", "FILE:/tmp/krb5cc_web_env"),
    ],
)
def test_web_batch_subprocess_env_uses_effective_krb5ccname(
    tmp_path, monkeypatch, env_cache, expected_cache
):
    module = load_web_module()
    if env_cache is None:
        monkeypatch.delenv("KRB5CCNAME", raising=False)
    else:
        monkeypatch.setenv("KRB5CCNAME", env_cache)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        krb5ccname="FILE:/tmp/krb5cc_web_config",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {"analysis_depth": ["fast"], "jobs": ["4"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    cmd, kwargs = calls[0]
    assert kwargs["env"]["KRB5CCNAME"] == expected_cache
    assert expected_cache not in cmd
    payload = json.loads(module.render_job_status_json(snapshot))
    assert "krb5cc_web_config" not in payload["result_html"]
    assert "krb5cc_web_env" not in payload["result_html"]


def test_web_loads_krb5ccname_from_local_config_and_rejects_invalid_values(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"krb5ccname": "FILE:/tmp/krb5cc_web_local"}), encoding="utf-8")

    assert (
        module.load_krb5ccname_from_local_config(config, cwd=tmp_path)
        == "FILE:/tmp/krb5cc_web_local"
    )

    bad_config = tmp_path / "bad-cm-config.json"
    bad_config.write_text(json.dumps({"krb5ccname": "FILE:/tmp/bad\ncache"}), encoding="utf-8")
    with pytest.raises(module.cm_collector.ConfigError):
        module.load_krb5ccname_from_local_config(bad_config, cwd=tmp_path)


def test_web_parses_keytab_principals_to_username_options():
    from query_doctor.web.config import source_owner_user_options_from_klist

    assert source_owner_user_options_from_klist(
        "\n".join(
            [
                "Keytab name: FILE:/tmp/query-doctor.keytab",
                "KVNO Principal",
                "---- --------------------------------------------------------------------------",
                "   1 sa@EXAMPLE.COM",
                "   1 hive/host.example.com@EXAMPLE.COM",
                "   2 analyst_one@EXAMPLE.COM",
                "   2 report_user@EXAMPLE.COM",
                "   3 sa@EXAMPLE.COM",
            ]
        )
    ) == ("analyst_one", "report_user", "sa")


def test_web_settings_loads_keytab_username_options(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web import config as web_config
    from query_doctor.web.viewer_identity import VIEWER_IDENTITY_LOCAL_FIRST

    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("QD_KEYTAB", str(tmp_path / "query-doctor.keytab"))
    monkeypatch.setattr(
        web_config,
        "source_owner_user_options_from_keytab",
        lambda _env: ("sa", "analyst_one"),
    )

    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    assert settings.source_owner_user_options == ("analyst_one", "sa")
    assert settings.source_owner_user == "analyst_one"
    assert settings.viewer_identity.mode == VIEWER_IDENTITY_LOCAL_FIRST
    assert settings.viewer_identity.viewer_raw_subjects == ("analyst_one", "sa")


def test_web_settings_loads_viewer_identity_header_from_config(tmp_path):
    module = load_web_module()

    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"viewer_identity_header": "X-QD-Viewer"}), encoding="utf-8")

    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    assert settings.viewer_identity_header == "X-QD-Viewer"


def test_web_settings_cli_viewer_identity_header_overrides_config(tmp_path):
    module = load_web_module()

    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"viewer_identity_header": "X-Config-Viewer"}), encoding="utf-8")

    settings = module.build_web_settings(
        module.parse_args(["--config", str(config), "--viewer-identity-header", "X-CLI-Viewer"]),
        cwd=tmp_path,
    )

    assert settings.viewer_identity_header == "X-CLI-Viewer"


def test_web_settings_rejects_invalid_viewer_identity_header(tmp_path):
    module = load_web_module()

    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"viewer_identity_header": "Bad Header"}), encoding="utf-8")

    with pytest.raises(module.WebError, match="HTTP header token"):
        module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)


def test_web_settings_loads_owner_raw_source_kill_switch_from_config(tmp_path):
    module = load_web_module()

    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"owner_raw_source_enabled": False}), encoding="utf-8")

    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    assert settings.owner_raw_source_enabled is False


def test_web_settings_cli_owner_raw_source_kill_switch_overrides_config(tmp_path):
    module = load_web_module()

    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"owner_raw_source_enabled": True}), encoding="utf-8")

    settings = module.build_web_settings(
        module.parse_args(["--config", str(config), "--disable-owner-raw-source"]),
        cwd=tmp_path,
    )

    assert settings.owner_raw_source_enabled is False


def test_web_keytab_username_options_fall_back_to_ktutil(tmp_path, monkeypatch):
    from query_doctor.web.config import source_owner_user_options_from_keytab

    keytab = tmp_path / "query-doctor.keytab"
    keytab.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv("QD_KEYTAB", str(keytab))
    calls: list[list[str]] = []

    def fake_runner(command, **_kwargs):
        calls.append(command)
        if command[0] == "klist":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="unsupported")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="\n".join(
                ["slot KVNO Principal", "---- ---- ----------------", "1 1 analyst_one@EXAMPLE.COM"]
            ),
            stderr="",
        )

    assert source_owner_user_options_from_keytab(runner=fake_runner) == ("analyst_one",)
    assert calls == [["klist", "-k", str(keytab)], ["ktutil", "-k", str(keytab), "list"]]


def test_web_settings_derives_single_keytab_owner_without_config(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web import config as web_config
    from query_doctor.web.viewer_identity import VIEWER_IDENTITY_LOCAL_FIRST

    config = tmp_path / "cm-config.json"
    config.write_text(json.dumps({"source_visibility": "owner_raw"}), encoding="utf-8")

    monkeypatch.delenv("KRB5_PRINCIPAL", raising=False)
    monkeypatch.delenv("QD_KRB5_PRINCIPAL", raising=False)
    monkeypatch.delenv("QD_SOURCE_OWNER_USER", raising=False)
    monkeypatch.setattr(
        web_config,
        "source_owner_user_options_from_keytab",
        lambda _env: ("analyst_one",),
    )

    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    assert settings.source_owner_user == "analyst_one"
    assert settings.source_owner_user_options == ("analyst_one",)
    assert settings.viewer_identity.mode == VIEWER_IDENTITY_LOCAL_FIRST
    assert settings.viewer_identity.viewer_raw_subjects == ("analyst_one",)


def test_web_cluster_settings_keep_keytab_derived_owner(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web import config as web_config
    from query_doctor.web.viewer_identity import VIEWER_IDENTITY_LOCAL_FIRST

    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "source_visibility": "safe",
                "clusters": [
                    {
                        "id": "direct",
                        "query_profile_source": "impala",
                        "source_visibility": "owner_raw",
                        "impala_profile_hosts": ["impalad.example.com"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("KRB5_PRINCIPAL", raising=False)
    monkeypatch.delenv("QD_KRB5_PRINCIPAL", raising=False)
    monkeypatch.delenv("QD_SOURCE_OWNER_USER", raising=False)
    monkeypatch.setattr(
        web_config,
        "source_owner_user_options_from_keytab",
        lambda _env: ("analyst_one",),
    )

    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    assert settings.active_cluster_key == "direct"
    assert settings.source_visibility == "owner_raw"
    assert settings.source_owner_user == "analyst_one"
    assert settings.viewer_identity.mode == VIEWER_IDENTITY_LOCAL_FIRST
    assert settings.viewer_identity.viewer_raw_subjects == ("analyst_one",)


def test_canonical_local_config_template_contains_web_metadata_placeholders():
    module = load_web_module()
    template = REPO_DIR / "query-doctor-config.example.json"
    values = json.loads(template.read_text(encoding="utf-8"))
    loaded = module.cm_collector.load_local_config(str(template), cwd=REPO_DIR)

    assert "host" not in values
    assert "port" not in values
    assert "krb5ccname" not in values
    assert "optimizer_model" not in values
    assert values["report_llm_provider"] == "ollama"
    assert values["report_llm_model"] == module.DEFAULT_MODEL
    assert "report_llm_base_url" not in values
    assert values["optimizer_llm_provider"] == "ollama"
    assert values["optimizer_llm_model"] == BUILTIN_OPTIMIZER_MODEL
    assert "optimizer_llm_base_url" not in values
    assert values["recent_scan_timezone"] == "UTC"
    assert loaded["optimizer_model"] == BUILTIN_OPTIMIZER_MODEL
    assert loaded["recent_scan_timezone"] == "UTC"
    assert set(values) == {
        "clusters",
        "language",
        "optimizer_llm_model",
        "optimizer_llm_provider",
        "out",
        "recent_scan_timezone",
        "recent_window_minutes",
        "report_llm_model",
        "report_llm_provider",
    }
    assert len(loaded["clusters"]) == 2
    cm_cluster, direct_cluster = loaded["clusters"]
    assert cm_cluster["query_profile_source"] == "cm"
    assert cm_cluster["cm_url"] == "https://cm-prod.example.com:7183/"
    assert cm_cluster["cluster"] == "prod_cluster"
    assert cm_cluster["service"] == "impala"
    assert cm_cluster["metadata_coordinator"] == "impala-prod-coordinator.example.com:21050"
    assert direct_cluster["query_profile_source"] == "impala"
    assert direct_cluster["impala_profile_hosts"] == [
        "impalad-worker-1.example.com",
        "impalad-worker-2.example.com",
    ]
    assert direct_cluster["metadata_coordinator"] == "impala-coordinator.example.com:21050"
    assert direct_cluster["metadata_kerberos_service_name"] == "hive"
    assert direct_cluster["collect_prometheus_timeseries"] is True
    assert "cm_url" not in direct_cluster
    assert "cluster" not in direct_cluster
    assert "service" not in direct_cluster
    assert "source_owner_user" not in values
    assert "metadata_auth" not in values
    assert "metadata_protocol" not in values
    assert "metadata_redact" not in values
    assert "metadata_timeout_sec" not in values
    assert "metadata_max_tables" not in values
    assert "metadata_max_output_bytes" not in values
    assert "recent_parallelism" not in values
    assert "recent_cm_jobs" not in values
    assert "recent_metadata_jobs" not in values
    assert "recent_min_duration_sec" not in values
    assert "recent_max_duration_sec" not in values
    assert "recent_user" not in values
    assert "recent_pool" not in values
    assert "metadata_top_limit" not in values
    assert "password" not in values
    assert "token" not in values
    assert "keytab" not in values
    assert not (REPO_DIR / "docs" / "cm-config.example.json").exists()


def test_web_settings_loads_metadata_from_local_config(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 9876,
                "metadata_coordinator": "impala-config.example.net:21000",
                "metadata_auth": "kerberos",
                "metadata_protocol": "hs2",
                "impala_kerberos_service_name": "hive",
                "metadata_kerberos_host_fqdn": "impala-lb.example.net",
                "metadata_ssl": True,
                "metadata_ca_cert": "/tmp/example-ca.pem",
                "metadata_timeout_sec": 44,
                "metadata_max_tables": 6,
                "metadata_max_output_bytes": 123456,
                "metadata_redact": True,
                "no_llm": True,
                "privacy_mode": False,
                "redact_identifiers": False,
                "redact_hosts": False,
                "source_visibility": "owner_raw",
                "source_owner_user": "analyst_one",
                "max_profile_bytes": 654321,
                "report_llm_provider": "openai_compatible",
                "report_llm_model": "report-config-model",
                "report_llm_base_url": "https://llm.example.com",
                "report_llm_chat_path": "/v1/chat/completions",
                "optimizer_llm_provider": "ollama",
                "optimizer_llm_model": "optimizer-config-model",
                "optimizer_llm_base_url": "http://localhost:11434",
                "krb5ccname": "FILE:/tmp/krb5cc_config_web",
                "language": "RU",
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(["--config", str(config)])
    settings = module.build_web_settings(args, cwd=tmp_path)

    assert settings.host == "127.0.0.1"
    assert settings.port == 9876
    assert settings.metadata_coordinator == "impala-config.example.net:21000"
    assert settings.metadata_auth == "kerberos"
    assert settings.metadata_protocol == "hs2"
    assert settings.metadata_kerberos_service_name == "hive"
    assert settings.metadata_kerberos_host_fqdn == "impala-lb.example.net"
    assert settings.metadata_ssl is True
    assert settings.metadata_ca_cert == "/tmp/example-ca.pem"
    assert settings.metadata_timeout_sec == 44
    assert settings.metadata_max_tables == 6
    assert settings.metadata_max_output_bytes == 123456
    assert settings.metadata_redact is True
    assert settings.no_llm is True
    assert settings.privacy_mode is False
    assert settings.redact_identifiers is False
    assert settings.redact_hosts is False
    assert settings.source_visibility == "owner_raw"
    assert settings.source_owner_user == "analyst_one"
    assert settings.max_profile_bytes == 654321
    assert settings.model == "report-config-model"
    assert settings.report_llm_provider == "openai_compatible"
    assert settings.report_llm_base_url == "https://llm.example.com"
    assert settings.report_llm_chat_path == "/v1/chat/completions"
    assert settings.optimizer_model == "optimizer-config-model"
    assert settings.optimizer_llm_provider == "ollama"
    assert settings.optimizer_llm_base_url == "http://localhost:11434"
    assert settings.krb5ccname == "FILE:/tmp/krb5cc_config_web"
    assert settings.language == "ru"
    body = module.render_batch_page(settings)
    assert '<html lang="ru">' in body
    assert 'name="analysis_depth"' not in body
    assert 'name="metadata_top_limit"' not in body
    assert "krb5cc_config_web" not in body


def test_web_batch_form_renders_keytab_usernames_as_dropdown(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        source_visibility="owner_raw",
        source_owner_user_options=("sa", "analyst_one"),
    )
    settings.config.write_text("{}", encoding="utf-8")

    body = module.render_batch_page(settings)

    assert '<select class="input" id="user" name="user"' in body
    assert "Select username" not in body
    assert '<option value="" selected>All configured owners</option>' in body
    assert '<option value="analyst_one">analyst_one</option>' in body
    assert '<option value="sa">sa</option>' in body
    assert (
        "Optional owner filter for this source visibility. Empty means all configured owners."
        in body
    )
    assert body.count('id="user" name="user"') == 1
    assert body.index("Basic scan") < body.index('<select class="input" id="user" name="user"')
    assert "Advanced settings" not in body
    assert '<input class="input" id="user" name="user" type="text"' not in body


def test_web_batch_form_blocks_owner_raw_without_loaded_owner(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        source_visibility="owner_raw",
    )
    settings.config.write_text("{}", encoding="utf-8")

    body = module.render_batch_page(settings)

    assert '<select class="input" id="user" name="user" disabled>' in body
    assert '<option value="" selected>No configured owner</option>' in body
    assert '<button class="run-button" type="submit" disabled>Owner required</button>' in body


def test_web_batch_form_keeps_all_users_option_for_optional_user_filter(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        source_visibility="safe",
        source_owner_user_options=("sa", "analyst_one"),
    )
    settings.config.write_text(
        json.dumps({"web_advanced_settings_enabled": True, "web_advanced_filters": ["user"]}),
        encoding="utf-8",
    )

    body = module.render_batch_page(settings)

    assert "Advanced settings" in body
    assert '<select class="input" id="user" name="user"' in body
    assert '<option value="" selected>All users</option>' in body
    assert '<option value="analyst_one">analyst_one</option>' in body
    assert '<option value="sa">sa</option>' in body
    assert "Select username" not in body


def test_web_settings_reads_cluster_selector_options_from_local_config(tmp_path, monkeypatch):
    module = load_web_module()
    from query_doctor.web import config as web_config
    from query_doctor.web.cluster_selection import settings_for_cluster_key
    from query_doctor.web.viewer_identity import VIEWER_IDENTITY_LOCAL_FIRST

    monkeypatch.delenv("KRB5_PRINCIPAL", raising=False)
    monkeypatch.delenv("QD_KRB5_PRINCIPAL", raising=False)
    monkeypatch.delenv("QD_SOURCE_OWNER_USER", raising=False)
    monkeypatch.setattr(web_config, "source_owner_user_options_from_keytab", lambda _env: ())
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "username": "query_doctor_user",
                "recent_scan_timezone": "UTC",
                "clusters": [
                    {
                        "id": "prod",
                        "label": "Production",
                        "cm_url": "https://cm-prod.example.com:7183/",
                        "cluster": "prod_cluster",
                        "service": "impala",
                        "cm_metrics_profile": "cm7",
                        "metadata_coordinator": "impala-prod.example.com:21000",
                    },
                    {
                        "id": "stage",
                        "label": "Staging",
                        "cm_url": "https://cm-stage.example.com:7183/",
                        "cluster": "stage_cluster",
                        "service": "impala",
                        "cm_metrics_profile": "cm6",
                        "query_profile_source": "impala",
                        "impala_profile_hosts": ["impalad-stage.example.com"],
                        "collect_prometheus_timeseries": True,
                        "prometheus_url": "https://prometheus-stage.example.com",
                        "prometheus_timeseries_padding_sec": 300,
                        "metadata_coordinator": "impala-stage.example.com:21000",
                        "metadata_kerberos_service_name": "hive",
                        "metadata_kerberos_host_fqdn": "impala-stage-lb.example.com",
                        "recent_scan_timezone": "Europe/Berlin",
                        "source_visibility": "owner_raw",
                        "source_owner_user": "stage_user",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    body = module.render_batch_page(settings)

    assert settings.active_cluster_key == "prod"
    assert settings.cm_url == "https://cm-prod.example.com:7183/"
    assert settings.cm_cluster == "prod_cluster"
    assert settings.cm_service == "impala"
    assert settings.cm_metrics_profile == "cm7"
    assert settings.recent_scan_timezone == "UTC"
    assert settings.metadata_coordinator == "impala-prod.example.com:21000"
    assert settings.clusters[1].query_profile_source == "impala"
    assert settings.clusters[1].collect_prometheus_timeseries is True
    assert settings.clusters[1].prometheus_url == "https://prometheus-stage.example.com"
    assert settings.clusters[1].prometheus_timeseries_padding_sec == 300
    assert settings.clusters[1].metadata_kerberos_service_name == "hive"
    assert settings.clusters[1].metadata_kerberos_host_fqdn == "impala-stage-lb.example.com"
    assert settings.clusters[1].recent_scan_timezone == "Europe/Berlin"
    assert settings.clusters[1].source_visibility == "owner_raw"
    assert settings.clusters[1].source_owner_user == "stage_user"
    assert settings.viewer_identity.mode == VIEWER_IDENTITY_LOCAL_FIRST
    assert settings.viewer_identity.viewer_raw_subjects == ()
    stage_settings = settings_for_cluster_key(settings, "stage")
    assert stage_settings.viewer_identity.mode == VIEWER_IDENTITY_LOCAL_FIRST
    assert stage_settings.viewer_identity.viewer_raw_subjects == ("stage_user",)
    with pytest.raises(module.WebError) as exc:
        settings_for_cluster_key(settings, "missing-source")
    assert exc.value.reason_code == "web.cluster_not_found"
    assert exc.value.stage == "Checking selected source"
    assert '<div class="batch-source-settings">' in body
    assert '<label for="diagnosis_cluster_key">Source cluster</label>' in body
    assert '<select class="input" id="diagnosis_cluster_key" name="cluster_key">' in body
    assert (
        '<option value="prod" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Production</option>' in body
    )
    assert (
        '<option value="stage" data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Staging</option>' in body
    )
    assert "data-scan-source-field=" not in body
    assert '<label for="recent_window_minutes">Search depth (min)</label>' in body
    assert body.index('<label for="diagnosis_cluster_key">Source cluster</label>') < body.index(
        "What to analyze"
    )
    assert "Direct Impala clusters can add Prometheus runtime metrics when configured." in body
    assert 'name="cm_metrics_profile"' not in body
    assert "cm-prod.example.com" not in body
    assert "impala-prod.example.com" not in body


def test_web_cluster_select_marks_engine_specific_sources(tmp_path):
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig

    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        clusters=(
            WebClusterConfig(
                key="impala",
                label="Impala source",
                cm_url="https://cm.example.invalid",
                cm_cluster="cluster",
                cm_service="impala",
            ),
            WebClusterConfig(
                key="trino",
                label="Trino source",
                trino_beta_enabled=True,
                trino_coordinator_url="https://trino.example.invalid",
                trino_query_info_source_contract=tmp_path / "trino-query-info-contract.json",
                trino_query_list_source_contract=tmp_path / "trino-query-list-contract.json",
            ),
        ),
        active_cluster_key="impala",
    )

    body = module.render_batch_page(settings)

    assert (
        '<option value="impala" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Impala source</option>' in body
    )
    assert (
        '<option value="trino" data-engine-impala-ready="false" '
        'data-engine-trino-ready="true" data-trino-beta-query-ready="true" '
        'data-trino-beta-recent-ready="true" data-trino-display-label="Trino Beta">'
        "Trino source - Trino Beta Recent + One Query ID</option>" in body
    )

    trino_body = module.render_batch_page(
        settings,
        form_values={"engine": "trino", "cluster_key": "impala"},
    )
    assert (
        '<option value="trino" selected data-engine-impala-ready="false" '
        'data-engine-trino-ready="true" data-trino-beta-query-ready="true" '
        'data-trino-beta-recent-ready="true" data-trino-display-label="Trino Beta">'
        "Trino source - Trino Beta Recent + One Query ID</option>" in trino_body
    )
    assert '<input type="hidden" name="cluster_key" value="trino">' in trino_body
    assert '<input type="hidden" name="engine" value="trino" data-engine-hidden>' in trino_body

    impala_body = module.render_batch_page(
        settings,
        form_values={"engine": "impala", "cluster_key": "trino"},
    )
    assert (
        '<option value="impala" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Impala source</option>' in impala_body
    )
    assert '<input type="hidden" name="cluster_key" value="impala">' in impala_body
    assert '<input type="hidden" name="engine" value="impala" data-engine-hidden>' in impala_body


def test_web_settings_uses_default_config_discovery(tmp_path, capsys):
    module = load_web_module()
    config = tmp_path / module.cm_collector.DEFAULT_LOCAL_CONFIG_NAME
    config.write_text(
        json.dumps(
            {
                "metadata_coordinator": "impala-default.example.net:21000",
                "metadata_auth": "kerberos",
            }
        ),
        encoding="utf-8",
    )

    settings = module.build_web_settings(module.parse_args([]), cwd=tmp_path)

    assert settings.config == config
    assert settings.metadata_coordinator == "impala-default.example.net:21000"
    assert module.cm_collector.LEGACY_LOCAL_CONFIG_WARNING not in capsys.readouterr().err


def test_web_settings_uses_legacy_default_config_with_warning(tmp_path, capsys, monkeypatch):
    module = load_web_module()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    legacy_config = tmp_path / module.cm_collector.LEGACY_LOCAL_CONFIG_NAME
    legacy_config.write_text(
        json.dumps(
            {
                "metadata_coordinator": "impala-legacy.example.net:21000",
                "metadata_auth": "kerberos",
            }
        ),
        encoding="utf-8",
    )

    settings = module.build_web_settings(module.parse_args([]), cwd=tmp_path)

    captured = capsys.readouterr()
    assert settings.config == legacy_config
    assert settings.metadata_coordinator == "impala-legacy.example.net:21000"
    assert module.cm_collector.LEGACY_LOCAL_CONFIG_WARNING in captured.err


def test_web_metadata_cli_options_override_local_config(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "metadata_coordinator": "impala-config.example.net:21000",
                "metadata_protocol": "hs2",
                "metadata_timeout_sec": 30,
                "metadata_max_tables": 5,
                "metadata_max_output_bytes": 111111,
                "metadata_redact": False,
            }
        ),
        encoding="utf-8",
    )

    args = module.parse_args(
        [
            "--config",
            str(config),
            "--metadata-coordinator",
            "impala-cli.example.net:21000",
            "--metadata-protocol",
            "hs2-http",
            "--metadata-timeout-sec",
            "55",
            "--metadata-max-tables",
            "8",
            "--metadata-max-output-bytes",
            "222222",
            "--metadata-redact",
        ]
    )
    settings = module.build_web_settings(args, cwd=tmp_path)

    assert settings.metadata_coordinator == "impala-cli.example.net:21000"
    assert settings.metadata_protocol == "hs2-http"
    assert settings.metadata_timeout_sec == 55
    assert settings.metadata_max_tables == 8
    assert settings.metadata_max_output_bytes == 222222
    assert settings.metadata_redact is True


def test_web_optimizer_model_overrides_report_model(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        model="report-model",
        optimizer_model="optimizer-model",
        report_llm_provider="openai_compatible",
        report_llm_base_url="https://llm.example.com",
        optimizer_llm_provider="ollama",
        optimizer_llm_base_url="http://localhost:11434",
    )

    cmd = module.build_optimized_query_command(tmp_path / "case-001", settings)
    report_cmd = module.build_report_command(
        tmp_path / "case-001", "admin", "report_admin.md", settings
    )

    assert cmd[cmd.index("--model") + 1] == "optimizer-model"
    assert report_cmd[report_cmd.index("--model") + 1] == "report-model"
    assert report_cmd[report_cmd.index("--llm-provider") + 1] == "openai_compatible"
    assert report_cmd[report_cmd.index("--llm-base-url") + 1] == "https://llm.example.com"
    assert cmd[cmd.index("--llm-provider") + 1] == "ollama"
    assert cmd[cmd.index("--llm-base-url") + 1] == "http://localhost:11434"
    assert cmd[cmd.index("--source-visibility") + 1] == "safe"


def test_web_optimizer_default_does_not_inherit_report_model(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        model="report-model",
    )

    cmd = module.build_optimized_query_command(tmp_path / "case-001", settings)
    report_cmd = module.build_report_command(
        tmp_path / "case-001", "admin", "report_admin.md", settings
    )

    assert cmd[cmd.index("--model") + 1] == module.DEFAULT_OPTIMIZER_MODEL
    assert report_cmd[report_cmd.index("--model") + 1] == "report-model"


def test_web_optimizer_command_passes_owner_raw_source_visibility(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        source_visibility="owner_raw",
    )

    cmd = module.build_optimized_query_command(tmp_path / "case-001", settings)

    assert cmd[cmd.index("--source-visibility") + 1] == "owner_raw"


def test_web_report_commands_use_configured_language(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", language="ru")
    case_dir = tmp_path / "case-001"

    report_cmd = module.build_report_command(case_dir, "admin", "report_admin.md", settings)
    batch_report_cmd = module.build_batch_case_report_command(case_dir, settings)

    assert report_cmd[report_cmd.index("--language") + 1] == "ru"
    assert batch_report_cmd[batch_report_cmd.index("--language") + 1] == "ru"


def test_web_no_llm_setting_is_passed_to_report_pipeline_and_optimizer(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", no_llm=True)
    case_dir = tmp_path / "case-001"
    from query_doctor.web.batch_case_actions import detail_job_redirect_url as batch_redirect
    from query_doctor.web.specific_query_actions import detail_job_redirect_url as query_redirect

    assert "--no-llm" in module.build_report_command(case_dir, "admin", "report_admin.md", settings)
    assert "--no-llm" in module.build_batch_case_report_command(case_dir, settings)
    assert "--no-llm" in module.build_optimized_query_command(case_dir, settings)
    assert batch_redirect("0" * 32, settings) == f"/jobs/{'0' * 32}#case-actions"
    assert query_redirect("0" * 32, settings) == f"/jobs/{'0' * 32}#case-actions"


def test_web_full_batch_command_uses_metadata_args_from_local_config(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "metadata_coordinator": "impala-config.example.net:21000",
                "metadata_protocol": "hs2",
                "metadata_timeout_sec": 44,
                "metadata_max_tables": 6,
                "metadata_max_output_bytes": 123456,
                "metadata_redact": True,
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["8"], "parallelism": ["4"]}
    )

    cmd, _out_dir = module.build_batch_command("c" * 32, batch_config, settings)

    assert cmd[cmd.index("--metadata-mode") + 1] == "on"
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "5000"
    assert cmd[cmd.index("--metadata-top-limit") + 1] == "8"
    assert cmd[cmd.index("--metadata-coordinator") + 1] == "impala-config.example.net:21000"
    assert "--metadata-impala-shell" not in cmd
    assert cmd[cmd.index("--metadata-protocol") + 1] == "hs2"
    assert cmd[cmd.index("--metadata-timeout-sec") + 1] == "44"
    assert cmd[cmd.index("--metadata-max-tables") + 1] == "6"
    assert cmd[cmd.index("--metadata-max-output-bytes") + 1] == "123456"
    assert "--metadata-redact" in cmd
    assert "--top-reports" in cmd
    assert "--allow-high-jobs" not in cmd


def test_web_batch_command_uses_selected_cluster_from_local_config(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "username": "query_doctor_user",
                "clusters": [
                    {
                        "id": "prod",
                        "label": "Production",
                        "cm_url": "https://cm-prod.example.com:7183/",
                        "cluster": "prod_cluster",
                        "service": "impala",
                        "cm_metrics_profile": "cm7",
                        "metadata_coordinator": "impala-prod.example.com:21000",
                    },
                    {
                        "id": "stage",
                        "label": "Staging",
                        "cm_url": "https://cm-stage.example.com:7183/",
                        "cluster": "stage_cluster",
                        "service": "impala-stage",
                        "cm_metrics_profile": "cm6",
                        "metadata_coordinator": "impala-stage.example.com:21000",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    batch_config = module.parse_batch_run_config(
        {
            "cluster_key": ["stage"],
            "metadata_top_limit": ["8"],
            "parallelism": ["4"],
            "collect_cm_timeseries": ["on"],
        },
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("d" * 32, batch_config, settings)

    assert cmd[cmd.index("--cm-url") + 1] == "https://cm-stage.example.com:7183/"
    assert cmd[cmd.index("--cluster") + 1] == "stage_cluster"
    assert cmd[cmd.index("--service") + 1] == "impala-stage"
    assert cmd[cmd.index("--cm-metrics-profile") + 1] == "cm6"
    assert cmd[cmd.index("--metadata-coordinator") + 1] == "impala-stage.example.com:21000"


def test_web_batch_hidden_runtime_context_controls_use_local_config_defaults(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "prod_cluster",
                "service": "impala",
                "username": "query_doctor_user",
                "recent_collect_cm_events": False,
                "recent_collect_cm_timeseries": True,
                "recent_profile_analysis_limit": 750,
                "recent_cm_events_max_events": 7,
                "recent_cm_timeseries_top_limit": 6,
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)

    batch_config = module.parse_batch_run_config({"metadata_top_limit": ["0"]}, settings=settings)
    running_config = module.parse_running_run_config(
        {"metadata_top_limit": ["0"]}, settings=settings
    )

    assert batch_config.collect_cm_events is False
    assert batch_config.collect_cm_timeseries is True
    assert batch_config.triage_profile_limit == 750
    assert batch_config.cm_events_max_events == 7
    assert batch_config.cm_timeseries_top_limit == 6
    assert running_config.collect_cm_events is True
    assert running_config.collect_cm_timeseries is True
    assert running_config.cm_events_max_events == 7
    assert running_config.cm_timeseries_top_limit == 6


def test_web_batch_command_uses_direct_impala_source_for_selected_cluster(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "recent_window_minutes": 43200,
                "clusters": [
                    {
                        "id": "cm",
                        "label": "CM",
                        "cm_url": "https://cm.example.com:7183/",
                        "cluster": "prod_cluster",
                        "service": "impala",
                        "username": "query_doctor_user",
                    },
                    {
                        "id": "ambari",
                        "label": "Ambari",
                        "query_profile_source": "impala",
                        "impala_profile_hosts": [
                            "impalad-1.example.com",
                            "impalad-2.example.com:25001",
                        ],
                        "impala_profile_timeout_sec": 12,
                        "impala_profile_prefer_json": True,
                        "impala_profile_collect_docs": True,
                        "impala_collect_admission_context": True,
                        "collect_prometheus_timeseries": True,
                        "prometheus_url": "http://prometheus.example.com:9090",
                        "prometheus_metrics_profile": "ambari-hadoop",
                        "prometheus_step_sec": 45,
                        "prometheus_timeseries_padding_sec": 180,
                        "metadata_coordinator": "impala-ambari.example.com:21000",
                        "metadata_protocol": "hs2",
                        "metadata_kerberos_service_name": "hive",
                        "metadata_redact": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    batch_config = module.parse_batch_run_config(
        {
            "cluster_key": ["ambari"],
            "metadata_top_limit": ["8"],
            "parallelism": ["4"],
            "collect_cm_events": ["on"],
            "collect_cm_timeseries": ["on"],
        },
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("e" * 32, batch_config, settings)

    assert batch_config.recent_window_minutes == 43200
    assert batch_config.from_time is None
    assert batch_config.to_time is None
    assert cmd[cmd.index("--query-profile-source") + 1] == "impala"
    assert cmd[cmd.index("--recent-window-minutes") + 1] == "43200"
    assert "--from-time" not in cmd
    assert "--to-time" not in cmd
    assert cmd.count("--impala-profile-host") == 2
    assert cmd[cmd.index("--impala-profile-timeout-sec") + 1] == "12"
    assert "--impala-profile-prefer-json" in cmd
    assert "--impala-profile-collect-docs" in cmd
    assert "--impala-collect-admission-context" in cmd
    assert cmd[cmd.index("--prometheus-url") + 1] == "http://prometheus.example.com:9090"
    assert "--collect-prometheus-timeseries" in cmd
    assert cmd[cmd.index("--prometheus-metrics-profile") + 1] == "ambari-hadoop"
    assert cmd[cmd.index("--prometheus-step-sec") + 1] == "45"
    assert cmd[cmd.index("--prometheus-timeseries-padding-sec") + 1] == "180"
    assert cmd[cmd.index("--metadata-mode") + 1] == "on"
    assert cmd[cmd.index("--metadata-coordinator") + 1] == "impala-ambari.example.com:21000"
    assert "--metadata-impala-shell" not in cmd
    assert cmd[cmd.index("--metadata-kerberos-service-name") + 1] == "hive"
    assert "--metadata-redact" in cmd
    assert "--cm-url" not in cmd
    assert "--collect-cm-events" not in cmd
    assert "--collect-cm-timeseries" not in cmd


def test_web_batch_command_passes_owner_source_visibility_for_direct_impala(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
                "source_visibility": "owner_raw",
                "source_owner_user": "analyst_one",
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["0"], "parallelism": ["4"]},
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("f" * 32, batch_config, settings)

    assert cmd[cmd.index("--source-visibility") + 1] == "owner_raw"
    assert cmd[cmd.index("--source-owner-user") + 1] == "analyst_one"


def test_web_batch_command_passes_owner_source_visibility_for_cm(tmp_path):
    module = load_web_module()
    from query_doctor.cli import batch_recent

    config = tmp_path / "cm-config.json"
    config.write_text(
        json.dumps(
            {
                "cm_url": "https://cm.example.com:7183/",
                "cluster": "prod_cluster",
                "service": "impala",
                "source_visibility": "owner_raw",
                "source_owner_user": "analyst_one",
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["0"], "parallelism": ["4"]},
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("f" * 32, batch_config, settings)
    batch_args = batch_recent.parse_args(command_args(cmd, "batch_recent"))
    batch_settings = batch_recent.build_batch_config(
        batch_args,
        env={"CM_USERNAME": "collector", "CM_PASSWORD": "secret"},
        cwd=tmp_path,
        repo_root=REPO_DIR,
    )

    assert cmd[cmd.index("--cm-url") + 1] == "https://cm.example.com:7183/"
    assert "--query-profile-source" not in cmd
    assert cmd[cmd.index("--source-visibility") + 1] == "owner_raw"
    assert cmd[cmd.index("--source-owner-user") + 1] == "analyst_one"
    assert batch_settings.query_profile_source == "cm"
    assert batch_settings.user == "analyst_one"


def test_web_batch_command_uses_selected_keytab_owner_for_owner_raw(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        cm_url="https://cm.example.com:7183/",
        cm_cluster="prod_cluster",
        cm_service="impala",
        source_visibility="owner_raw",
        source_owner_user="sa",
        source_owner_user_options=("sa", "analyst_one"),
    )
    settings.config.write_text("{}", encoding="utf-8")
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["0"], "user": ["analyst_one"]},
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("f" * 32, batch_config, settings)

    assert cmd[cmd.index("--source-owner-user") + 1] == "analyst_one"
    assert cmd[cmd.index("--user") + 1] == "analyst_one"


def test_web_batch_command_passes_all_keytab_owners_for_owner_raw_without_user(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        cm_url="https://cm.example.com:7183/",
        cm_cluster="prod_cluster",
        cm_service="impala",
        source_visibility="owner_raw",
        source_owner_user="sa",
        source_owner_user_options=("sa", "analyst_one"),
    )
    settings.config.write_text("{}", encoding="utf-8")
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["0"]},
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("f" * 32, batch_config, settings)

    owner_indexes = [index + 1 for index, token in enumerate(cmd) if token == "--source-owner-user"]
    assert [cmd[index] for index in owner_indexes] == ["analyst_one", "sa"]
    assert "--user" not in cmd


def test_web_batch_command_accepts_selected_keytab_owner_without_configured_owner(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        cm_url="https://cm.example.com:7183/",
        cm_cluster="prod_cluster",
        cm_service="impala",
        source_visibility="owner_raw",
        source_owner_user_options=("analyst_one",),
    )
    settings.config.write_text("{}", encoding="utf-8")
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["0"], "user": ["analyst_one"]},
        settings=settings,
    )

    cmd, _out_dir = module.build_batch_command("f" * 32, batch_config, settings)

    assert cmd[cmd.index("--source-owner-user") + 1] == "analyst_one"
    assert cmd[cmd.index("--user") + 1] == "analyst_one"


def test_web_batch_command_rejects_owner_source_visibility_user_mismatch(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        query_profile_source="impala",
        impala_profile_hosts=("impalad-1.example.com",),
        source_visibility="owner_raw",
        source_owner_user="analyst_one",
    )
    settings.config.write_text("{}", encoding="utf-8")
    batch_config = module.parse_batch_run_config(
        {"metadata_top_limit": ["0"], "user": ["other_user"]},
        settings=settings,
    )

    with pytest.raises(module.WebError, match="User filter to match"):
        module.build_batch_command("f" * 32, batch_config, settings)


def test_web_batch_default_post_uses_fast_mode_without_metadata_config(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        timeout_sec=77,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n", encoding="utf-8"
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {
            "recent_window_minutes": ["60"],
            "cm_inspect_limit": ["1000"],
            "triage_profile_limit": ["200"],
            "min_duration_sec": ["10"],
            "order": ["duration-desc"],
            "jobs": ["4"],
            "query_type": ["QUERY"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    cmd, _kwargs = calls[0]
    assert cmd[cmd.index("--metadata-mode") + 1] == "off"
    assert cmd[cmd.index("--top-reports") + 1] == "0"
    assert cmd[cmd.index("--recent-window-minutes") + 1] == "60"
    assert "--from-time" not in cmd
    assert "--to-time" not in cmd
    assert cmd[cmd.index("--cm-jobs") + 1] == "4"
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "200"
    assert cmd[cmd.index("--metadata-jobs") + 1] == "1"
    assert cmd[cmd.index("--query-type") + 1] == "QUERY"
    assert cmd[cmd.index("--metadata-top-limit") + 1] == "0"
    assert "--metadata-coordinator" not in cmd
    assert "--allow-high-jobs" not in cmd


def test_web_batch_job_uses_query_inbox_time_range_for_finished_scan(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        timeout_sec=77,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n", encoding="utf-8"
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {
            "recent_window_minutes": ["60"],
            "metadata_top_limit": ["0"],
            "inbox_source": ["cm"],
            "inbox_workflow": ["finished"],
            "inbox_from": ["2026-07-03T08:00Z"],
            "inbox_to": ["2026-07-03T09:30Z"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert snapshot.batch_form_values is not None
    assert snapshot.batch_form_values["from_time"] == "2026-07-03T08:00:00Z"
    assert snapshot.batch_form_values["to_time"] == "2026-07-03T09:30:00Z"
    parsed = urlparse(location)
    assert parse_qs(parsed.query) == {
        "inbox_source": ["cm"],
        "inbox_workflow": ["finished"],
        "inbox_from": ["2026-07-03T08:00Z"],
        "inbox_to": ["2026-07-03T09:30Z"],
    }
    cmd, _kwargs = calls[0]
    assert "--recent-window-minutes" not in cmd
    assert cmd[cmd.index("--from-time") + 1] == "2026-07-03T08:00:00Z"
    assert cmd[cmd.index("--to-time") + 1] == "2026-07-03T09:30:00Z"


def test_web_batch_duplicate_finished_scan_reuses_running_job_without_subprocess(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    form = {
        "recent_window_minutes": ["60"],
        "metadata_top_limit": ["0"],
        "min_duration_sec": ["10"],
    }
    config = module.parse_batch_run_config(
        form,
        settings=settings,
        default_metadata_top_limit=0,
    )
    store = module.WebJobStore()
    existing = store.create_batch(module.form_values_from_config(config))

    status, location = module.start_batch_job(
        form,
        settings,
        store,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate scan must not start another subprocess")
        ),
    )

    assert status == 303
    assert location == f"/jobs/{existing.job_id}"


def test_web_query_inbox_scope_filters_are_preserved_on_batch_redirect(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    form = {
        "recent_window_minutes": ["60"],
        "metadata_top_limit": ["0"],
        "min_duration_sec": ["10"],
        "inbox_source": ["impala"],
        "inbox_workflow": ["running"],
        "inbox_window": ["120"],
        "inbox_query_type": ["QUERY"],
        "query_group": ["suspicious"],
        "case_dir": ["private-case-value"],
    }
    config = module.parse_batch_run_config(
        form,
        settings=settings,
        default_metadata_top_limit=0,
    )
    store = module.WebJobStore()
    existing = store.create_batch(module.form_values_from_config(config))

    status, location = module.start_batch_job(
        form,
        settings,
        store,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate scan must not start another subprocess")
        ),
    )

    parsed = urlparse(location)
    assert status == 303
    assert parsed.path == f"/jobs/{existing.job_id}"
    assert parse_qs(parsed.query) == {
        "inbox_source": ["impala"],
        "inbox_workflow": ["running"],
        "inbox_window": ["120"],
        "inbox_query_type": ["QUERY"],
    }
    assert "query_group" not in location
    assert "case_dir" not in location
    assert "private-case-value" not in location


def test_web_batch_empty_advanced_fields_use_backend_defaults_without_form_backfill(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps(
                {
                    "stage": "discovery",
                    "status": "done",
                    "summaries_inspected": 2810,
                    "candidates_selected": 2,
                    "duration_filter": "none",
                }
            )
            + "\n"
            + json.dumps({"stage": "batch", "status": "done"})
            + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(
            json.dumps(
                {
                    "summaries_inspected": 2810,
                    "duration_filter": "none",
                    "triage_profile_limit": 5000,
                    "cm_summary_safety_cap": 5000,
                    "cm_summary_safety_cap_hit": False,
                    "cases": [],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {
            "analysis_depth": ["fast"],
            "recent_window_minutes": ["30"],
            "cm_inspect_limit": [""],
            "triage_profile_limit": [""],
            "metadata_top_limit": [""],
            "min_duration_sec": [""],
            "order": ["duration-desc"],
            "jobs": ["4"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    cmd, _kwargs = calls[0]
    assert cmd[cmd.index("--cm-inspect-limit") + 1] == "5000"
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "5000"
    assert "--no-min-duration-filter" in cmd
    assert "--min-duration-sec" not in cmd
    assert cmd[cmd.index("--top-reports") + 1] == "0"
    payload = json.loads(module.render_job_status_json(snapshot))
    assert "Scanned 2810 summaries" in payload["result_html"]
    assert "Duration filter: none" not in payload["result_html"]
    assert "Analyzer limit: 5000" not in payload["result_html"]

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    assert 'name="cm_inspect_limit"' not in captured["body"]
    assert 'name="triage_profile_limit"' not in captured["body"]
    assert 'name="min_duration_sec" type="number" min="0" step="0.001" value=""' in captured["body"]


def test_web_batch_summary_shows_cm_safety_cap_truncation(tmp_path):
    module = load_web_module()
    summary_path = tmp_path / "batch_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "summaries_inspected": 2656,
                "duration_filter": ">= 5 sec",
                "triage_profile_limit": 100,
                "cm_summary_safety_cap": 5000,
                "cm_summary_safety_cap_hit": True,
                "cm_summary_raw_scan_cap": 20000,
                "cm_summary_raw_scan_cap_hit": True,
                "scan_too_broad": True,
                "cases": [],
            }
        ),
        encoding="utf-8",
    )

    body = module.render_batch_card(
        module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary_path)
    )

    assert "Partial CM scan: cap 20000" in body
    assert "Duration filter: &gt;= 5 sec" not in body
    assert "Analyzer limit: 100" not in body
    assert '<details class="batch-notices" aria-label="Scan notes" open>' in body
    assert "<summary>Scan notes</summary>" in body
    assert "<strong>Partial scan</strong>" in body
    assert "CM query summary scan cap was reached before discovery completed" in body
    assert "partial bounded analysis of the summaries collected so far" in body
    assert "use a smaller Search depth or add user, pool, or query type filters" in body


def test_web_batch_summary_shows_safe_cm_events_context(tmp_path):
    module = load_web_module()
    summary_path = tmp_path / "batch_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "summaries_inspected": 10,
                "duration_filter": "none",
                "triage_profile_limit": 10,
                "collect_cm_events": True,
                "cluster_context": {
                    "status": "degraded_service_candidate",
                    "signal_counts": {"metastore_error_event": 2},
                    "raw_provider_payload": "RAW_PROVIDER_TOKEN",
                },
                "cases": [],
            }
        ),
        encoding="utf-8",
    )

    body = module.render_batch_card(
        module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary_path)
    )

    assert "Cluster event context: degraded_service_candidate, signals 2" in body
    assert "RAW_PROVIDER_TOKEN" not in body
    assert "raw_provider_payload" not in body


def test_web_batch_summary_shows_profile_reuse_count(tmp_path):
    module = load_web_module()
    summary_path = tmp_path / "batch_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "summaries_inspected": 10,
                "selected_count": 4,
                "profile_reuse": {"status_counts": {"reused": 2, "miss": 2}},
                "cases": [],
            }
        ),
        encoding="utf-8",
    )

    body = module.render_batch_card(
        module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary_path)
    )

    assert "Reused analyzed profiles: 2" in body


def test_web_batch_summary_renders_empty_scan_as_non_failed_state(tmp_path):
    module = load_web_module()
    summary_path = tmp_path / "batch_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "summaries_inspected": 0,
                "selected_count": 0,
                "duration_filter": "none",
                "triage_profile_limit": 200,
                "recent_window_minutes": 120,
                "from_time": "2026-05-02T21:00:00Z",
                "to_time": "2026-05-02T22:00:00Z",
                "query_type_filter": "QUERY",
                "include_failed": False,
                "include_running": False,
                "user_filter_present": False,
                "pool_filter_present": False,
                "discovery_failed": False,
                "warnings": ["CM returned no matching query summaries"],
                "cases": [],
            }
        ),
        encoding="utf-8",
    )

    body = module.render_batch_card(
        module.WebSettings(config=Path(".query-doctor-cm.local.json"), batch_summary=summary_path)
    )

    assert (
        "No matching queries found for this hour bucket. Try another hour or changing filters."
        in body
    )
    assert "Scanned 0 summaries -&gt; Analyzed 0 cases" in body
    assert "Duration filter: none" not in body
    assert "Scan time window: 2026-05-02T21:00:00Z -&gt; 2026-05-02T22:00:00Z" not in body
    assert "Search depth: 120 minutes" not in body
    assert "Query type: QUERY" not in body
    assert "Include failed:" not in body
    assert "Include running:" not in body
    assert "generic_collector_user" not in body
    assert "root.generic" not in body
    assert "Analysis failed" not in body
    assert "SELECT" not in body
    assert "stdout" not in body
    assert "stderr" not in body


def test_web_batch_job_omits_high_jobs_flag_for_small_jobs(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    config = module.parse_batch_run_config({"parallelism": ["4"], "metadata_top_limit": ["0"]})

    cmd, out_dir = module.build_batch_command("a" * 32, config, settings)

    assert "--allow-high-jobs" not in cmd
    assert out_dir == Path("/tmp") / f"query-doctor-web-batch-{'a' * 32}"
    assert Path(cmd[cmd.index("--progress-jsonl") + 1]) == out_dir / "progress.jsonl"
    assert cmd[cmd.index("--reuse-analyzed-profiles-from") + 1] == "/tmp"


def test_web_batch_command_uses_configured_recent_batch_root(tmp_path):
    module = load_web_module()
    recent_batch_root = tmp_path / "query-doctor-web-batches"
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        recent_batch_root=recent_batch_root,
    )
    settings.config.write_text("{}", encoding="utf-8")
    config = module.parse_batch_run_config({"parallelism": ["4"], "metadata_top_limit": ["0"]})

    cmd, out_dir = module.build_batch_command("b" * 32, config, settings)

    assert out_dir == recent_batch_root / f"query-doctor-web-batch-{'b' * 32}"
    assert Path(cmd[cmd.index("--out") + 1]) == out_dir
    assert Path(cmd[cmd.index("--progress-jsonl") + 1]) == out_dir / "progress.jsonl"
    assert Path(cmd[cmd.index("--reuse-analyzed-profiles-from") + 1]) == recent_batch_root


def test_web_batch_start_uses_configured_recent_batch_root_for_progress(tmp_path):
    module = load_web_module()
    recent_batch_root = tmp_path / "query-doctor-web-batches"
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        recent_batch_root=recent_batch_root,
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {"metadata_top_limit": ["0"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    assert snapshot is not None
    assert snapshot.batch_progress_path == recent_batch_root / (
        f"query-doctor-web-batch-{job_id}/progress.jsonl"
    )


def test_web_running_batch_command_does_not_enable_profile_reuse(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    config = module.parse_running_run_config({"parallelism": ["4"], "metadata_top_limit": ["0"]})

    cmd, _out_dir = module.build_batch_command("r" * 32, config, settings)

    assert "--reuse-analyzed-profiles-from" not in cmd


def test_web_batch_full_mode_builds_metadata_command(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        metadata_coordinator="impala.example.com:21000",
        metadata_auth="kerberos",
        metadata_protocol="hs2",
        metadata_kerberos_service_name="hive",
        metadata_kerberos_host_fqdn="impala-lb.example.com",
        metadata_ssl=True,
        metadata_ca_cert="/tmp/example-ca.pem",
        metadata_timeout_sec=45,
        metadata_max_tables=5,
        metadata_max_output_bytes=2097152,
        metadata_redact=True,
    )
    settings.config.write_text("{}", encoding="utf-8")
    config = module.parse_batch_run_config({"parallelism": ["4"], "metadata_top_limit": ["8"]})

    cmd, _out_dir = module.build_batch_command("b" * 32, config, settings)

    assert cmd[cmd.index("--metadata-mode") + 1] == "on"
    assert cmd[cmd.index("--cm-jobs") + 1] == "4"
    assert cmd[cmd.index("--jobs") + 1] == "4"
    assert cmd[cmd.index("--metadata-jobs") + 1] == "5"
    assert cmd[cmd.index("--metadata-coordinator") + 1] == "impala.example.com:21000"
    assert "--metadata-impala-shell" not in cmd
    assert cmd[cmd.index("--metadata-auth") + 1] == "kerberos"
    assert cmd[cmd.index("--metadata-protocol") + 1] == "hs2"
    assert cmd[cmd.index("--metadata-kerberos-service-name") + 1] == "hive"
    assert cmd[cmd.index("--metadata-kerberos-host-fqdn") + 1] == "impala-lb.example.com"
    assert cmd[cmd.index("--metadata-timeout-sec") + 1] == "45"
    assert "--metadata-ssl" in cmd
    assert cmd[cmd.index("--metadata-ca-cert") + 1] == "/tmp/example-ca.pem"
    assert cmd[cmd.index("--metadata-max-tables") + 1] == "5"
    assert cmd[cmd.index("--metadata-max-output-bytes") + 1] == "2097152"
    assert "--metadata-redact" in cmd
    assert cmd[cmd.index("--metadata-top-limit") + 1] == "8"
    assert cmd[cmd.index("--top-reports") + 1] == "0"
    assert "--allow-high-jobs" not in cmd
    assert "--model" not in cmd


def test_web_batch_metadata_default_budget_matches_bad_and_suspicious_policy():
    module = load_web_module()

    config = module.parse_batch_run_config(
        {}, default_metadata_top_limit=module.WEB_BATCH_METADATA_TOP_LIMIT_DEFAULT
    )

    assert config.metadata_top_limit == 70


def test_web_batch_full_mode_preflight_passes_before_starting_batch(tmp_path, monkeypatch):
    module = load_web_module()
    monkeypatch.delenv("KRB5CCNAME", raising=False)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        metadata_coordinator="impala.example.com:21000",
        krb5ccname="FILE:/tmp/krb5cc_web_config",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert kwargs.get("shell") is not True
        if cmd == ["klist"]:
            assert kwargs["env"]["KRB5CCNAME"] == "FILE:/tmp/krb5cc_web_config"
            return subprocess.CompletedProcess(cmd, 0, stdout="ticket ok", stderr="")
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {"metadata_top_limit": ["8"], "parallelism": ["4"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert calls[0][0] == ["klist"]
    batch_cmd = calls[1][0]
    assert batch_cmd[batch_cmd.index("--metadata-mode") + 1] == "on"
    assert "FILE:/tmp/krb5cc_web_config" not in batch_cmd


def test_web_batch_full_mode_klist_failure_rejects_before_subprocess(tmp_path, monkeypatch):
    module = load_web_module()
    monkeypatch.delenv("KRB5CCNAME", raising=False)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        metadata_coordinator="impala.example.com:21000",
        krb5ccname="FILE:/tmp/krb5cc_secret_path",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert kwargs.get("shell") is not True
        assert cmd == ["klist"]
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="FILE:/tmp/krb5cc_secret_path expired",
            stderr="FILE:/tmp/krb5cc_secret_path expired",
        )

    status, body = module.start_batch_job(
        {"metadata_top_limit": ["8"], "parallelism": ["4"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 400
    assert len(calls) == 1
    assert calls[0][0] == ["klist"]
    assert "Metadata preflight failed: Kerberos cache is not available or expired" in body
    assert "Queries to fetch metadata for" not in body
    assert "krb5cc_secret_path" not in body
    assert store.latest_batch_summary() is None


def test_web_batch_full_mode_missing_driver_rejects_before_klist(tmp_path, monkeypatch):
    module = load_web_module()
    monkeypatch.setattr(hs2_runner, "driver_available", lambda: False)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        metadata_coordinator="impala.example.com:21000",
        krb5ccname="FILE:/tmp/krb5cc_web_config",
    )
    settings.config.write_text("{}", encoding="utf-8")

    def fail_runner(*args, **kwargs):
        raise AssertionError("preflight must fail before running klist or batch")

    status, body = module.start_batch_job(
        {"metadata_top_limit": ["8"], "parallelism": ["4"]},
        settings,
        module.WebJobStore(),
        runner=fail_runner,
    )

    assert status == 400
    assert "the impyla metadata driver is not installed" in body


def test_web_batch_fast_mode_skips_metadata_preflight(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        metadata_coordinator="impala.example.com:21000",
        krb5ccname="FILE:/tmp/krb5cc_web_config",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert cmd != ["klist"]
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {"metadata_top_limit": ["0"], "parallelism": ["4"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert len(calls) == 1
    cmd = calls[0][0]
    assert cmd[cmd.index("--metadata-mode") + 1] == "off"


def test_nonlocal_web_batch_uses_conservative_recent_defaults(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        allow_nonlocal_web_bind=True,
        cm_url="https://cm.example.com:7183",
        cm_cluster="prod_cluster",
        cm_service="impala",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job({}, settings, store, runner=fake_runner)

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert len(calls) == 1
    cmd = calls[0][0]
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "50"
    assert cmd[cmd.index("--cm-jobs") + 1] == "4"
    assert cmd[cmd.index("--jobs") + 1] == "4"
    assert cmd[cmd.index("--metadata-mode") + 1] == "off"
    assert "--allow-high-jobs" not in cmd


def test_nonlocal_web_batch_with_history_uses_discover_only(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        allow_nonlocal_web_bind=True,
        cm_url="https://cm.example.com:7183",
        cm_cluster="prod_cluster",
        cm_service="impala",
    )
    settings.config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": str(tmp_path / "recent-history.sqlite3"),
            }
        ),
        encoding="utf-8",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job({}, settings, store, runner=fake_runner)

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert len(calls) == 1
    cmd = calls[0][0]
    assert "--discover-only" in cmd
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "5000"
    assert cmd[cmd.index("--metadata-mode") + 1] == "off"


def test_nonlocal_web_history_refresh_preserves_profile_worker_state(tmp_path, monkeypatch):
    from query_doctor.cli import batch_recent
    from query_doctor.recent.history_store import history_record_from_candidate
    from query_doctor.recent.profile_budget import (
        PROFILE_STATUS_RETRY_PENDING,
        ProfileBudgetPolicy,
        plan_recent_profile_jobs,
    )
    from query_doctor.recent.sqlite_history_store import SqliteRecentHistoryStore

    module = load_web_module()
    history_db = tmp_path / "recent-history.sqlite3"
    query_id = "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb"
    candidate = batch_recent.cm_profiles.RecentQueryCandidate(
        summary=batch_recent.cm_profiles.CMQuerySummary(
            query_id=query_id,
            duration_ms=3_700_000,
            query_type="QUERY",
            status="failed",
            statement="SELECT secret_column FROM sensitive_table",
            user="analyst",
            pool="root.analytics",
        ),
        selected=True,
        reason="selected: SELECT-like user query",
        sql_verb="SELECT",
    )
    record = history_record_from_candidate(
        candidate,
        engine="impala",
        source_kind="cm",
        source_key="cm:prod_cluster:impala",
        recorded_at_iso="2026-07-03T10:05:00+00:00",
    )
    job = plan_recent_profile_jobs(
        [record],
        policy=ProfileBudgetPolicy(max_jobs=1, min_suspicion_score=20),
        planned_at_iso="2026-07-03T10:06:00+00:00",
    )[0]
    store = SqliteRecentHistoryStore(history_db)
    store.upsert_summaries([record])
    store.enqueue_profile_jobs([job])
    claimed = store.claim_profile_jobs(
        max_jobs=1,
        lease_owner="worker-A",
        lease_until_iso="2026-07-03T10:20:00+00:00",
        now_iso="2026-07-03T10:10:00+00:00",
    )
    assert [claim.query_id for claim in claimed] == [query_id]
    assert store.fail_profile_job(
        engine=job.engine,
        source_kind=job.source_kind,
        source_key=job.source_key,
        query_id=job.query_id,
        lease_owner="worker-A",
        failed_at_iso="2026-07-03T10:12:00+00:00",
        error_code="profile-fetch-http-503",
        retry=True,
    )
    assert store.load_payloads()[0]["profile_status"] == PROFILE_STATUS_RETRY_PENDING
    assert len(store.load_profile_jobs()) == 1

    monkeypatch.setattr(
        batch_recent,
        "discover_candidates",
        lambda config, env: batch_recent.DiscoveryResult([candidate], [], "client-side", None),
    )
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        recent_batch_root=tmp_path,
        allow_nonlocal_web_bind=True,
        cm_url="https://cm.example.com:7183",
        cm_cluster="prod_cluster",
        cm_service="impala",
        cm_username="collector",
    )
    settings.config.write_text(
        json.dumps(
            {
                "recent_history_backend": "sqlite",
                "recent_history_db": str(history_db),
            }
        ),
        encoding="utf-8",
    )
    web_store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        args = command_args(cmd, "batch_recent")
        assert "--discover-only" in args
        assert "--collect-cm-events" not in args
        assert "--collect-cm-timeseries" not in args
        assert args[args.index("--metadata-mode") + 1] == "off"
        assert args[args.index("--top-reports") + 1] == "0"
        result = batch_recent.main(
            args,
            env={
                **kwargs.get("env", {}),
                "CM_USERNAME": "collector",
                "CM_PASSWORD": "secret",
            },
        )
        return subprocess.CompletedProcess(cmd, result, stdout="", stderr="")

    status, location = module.start_batch_job({}, settings, web_store, runner=fake_runner)

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = web_store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = web_store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert len(calls) == 1
    payloads = store.load_payloads()
    assert len(payloads) == 1
    assert payloads[0]["query_id"] == query_id
    assert payloads[0]["profile_status"] == PROFILE_STATUS_RETRY_PENDING
    assert payloads[0]["profile_last_error_code"] == "profile_fetch_http_503"
    jobs = store.load_profile_jobs()
    assert len(jobs) == 1
    assert jobs[0]["query_id"] == query_id
    assert jobs[0]["last_error_code"] == "profile_fetch_http_503"
    summary_path = tmp_path / f"query-doctor-web-batch-{job_id}" / "batch_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["recent_history"]["status"] == "recorded"
    assert summary["recent_history"]["profile_jobs_planned"] == 1
    summary_text = json.dumps(summary, sort_keys=True)
    assert "SELECT secret_column" not in summary_text
    assert "sensitive_table" not in summary_text


def test_nonlocal_web_batch_history_disabled_does_not_use_discover_only(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        allow_nonlocal_web_bind=True,
        cm_url="https://cm.example.com:7183",
        cm_cluster="prod_cluster",
        cm_service="impala",
    )
    settings.config.write_text(
        json.dumps(
            {
                "recent_history_backend": "disabled",
                "recent_history_db": str(tmp_path / "recent-history.sqlite3"),
            }
        ),
        encoding="utf-8",
    )
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job({}, settings, store, runner=fake_runner)

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert len(calls) == 1
    cmd = calls[0][0]
    assert "--discover-only" not in cmd
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "50"
    assert cmd[cmd.index("--metadata-mode") + 1] == "off"


def test_nonlocal_web_batch_uses_conservative_metadata_defaults(tmp_path, monkeypatch):
    module = load_web_module()
    monkeypatch.delenv("KRB5CCNAME", raising=False)
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        allow_nonlocal_web_bind=True,
        cm_url="https://cm.example.com:7183",
        cm_cluster="prod_cluster",
        cm_service="impala",
        metadata_coordinator="impala.example.com:21000",
        krb5ccname="FILE:/tmp/krb5cc_web_config",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["klist"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="ticket ok", stderr="")
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job({}, settings, store, runner=fake_runner)

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert calls[0][0] == ["klist"]
    cmd = calls[1][0]
    assert cmd[cmd.index("--triage-profile-limit") + 1] == "50"
    assert cmd[cmd.index("--metadata-top-limit") + 1] == "10"
    assert cmd[cmd.index("--metadata-jobs") + 1] == "2"
    assert cmd[cmd.index("--cm-jobs") + 1] == "4"
    assert cmd[cmd.index("--jobs") + 1] == "4"
    assert "--allow-high-jobs" not in cmd


def test_web_batch_smoke_can_skip_publishing_latest_summary(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(
        config=tmp_path / "cm-config.json",
        repo_dir=REPO_DIR,
        cm_url="https://cm.example.com:7183",
        cm_cluster="prod_cluster",
        cm_service="impala",
    )
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "progress.jsonl").write_text(
            json.dumps({"stage": "batch", "status": "done"}) + "\n",
            encoding="utf-8",
        )
        (out_dir / "batch_summary.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    status, location = module.start_batch_job(
        {"metadata_top_limit": ["0"], "publish_latest_summary": ["0"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "ok":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "ok"
    assert snapshot.result_html == "Recent scan completed."
    assert store.latest_batch_summary() is None


def test_web_batch_metadata_mode_requires_metadata_config(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")

    missing_metadata = module.start_batch_job(
        {"metadata_top_limit": ["10"], "parallelism": ["50"]},
        settings,
        module.WebJobStore(),
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("subprocess must not run")
        ),
    )

    assert missing_metadata[0] == 400
    assert "Metadata collection is not configured for this web session." in missing_metadata[1]
    assert "impala.metadata_not_configured" in missing_metadata[1]
    assert "Restart with metadata coordinator" in missing_metadata[1]


@pytest.mark.parametrize(
    "form",
    [
        {"jobs": ["101"]},
        {"jobs": ["0"]},
        {"cm_jobs": ["101"]},
        {"metadata_jobs": ["6"]},
        {"metadata_top_limit": ["201"]},
        {"order": ["score-desc"]},
        {"min_duration_sec": ["20"], "max_duration_sec": ["10"]},
    ],
)
def test_web_batch_form_rejects_invalid_values_without_subprocess(form):
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    store = module.WebJobStore()

    def fail_runner(*args, **kwargs):
        raise AssertionError("invalid batch form must not run subprocess")

    status, body = module.start_batch_job(form, settings, store, runner=fail_runner)

    assert status == 400
    assert 'class="error-card"' in body
    assert "Reason" in body
    assert "web." in body
    assert "Query Inbox" in body


def test_web_batch_job_failure_hides_raw_subprocess_output(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "discovery", "status": "failed", "phase": "discovery"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="SELECT secret_column FROM table",
            stderr='{"profile": "raw json"}',
        )

    status, location = module.start_batch_job(
        {
            "analysis_depth": ["fast"],
            "cm_inspect_limit": ["33"],
            "min_duration_sec": ["8.25"],
            "order": ["recent"],
            "jobs": ["2"],
            "user": ["bob"],
        },
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "failed"
    payload = json.loads(module.render_job_status_json(snapshot))
    assert "Query Doctor recent scan failed with exit code 1" in payload["error"]
    assert "Query discovery" in payload["progress_html"]
    assert "batch-progress-step--failed" in payload["progress_html"]
    assert "SELECT" not in payload["error"]
    assert "secret_column" not in payload["error"]
    assert "raw json" not in payload["error"]

    handler = module.make_handler(
        settings, analysis_func=lambda *args, **kwargs: None, job_store=store, runner=fake_runner
    )
    request = handler.__new__(handler)
    captured: dict[str, object] = {}

    def write_html(status, body):
        captured["status"] = status
        captured["body"] = body

    request.path = f"/jobs/{job_id}"
    request.write_html = write_html
    request.do_GET()

    assert captured["status"] == 200
    assert '<form id="batch-form"' in captured["body"]
    assert '<button class="run-button" type="submit">Run scan</button>' in captured["body"]
    assert "Analysis failed" in captured["body"]
    assert "Query discovery" in captured["body"]
    assert "batch-progress-step--failed" in captured["body"]
    assert (
        'name="min_duration_sec" type="number" min="0" step="0.001" value="8.25"'
        in captured["body"]
    )
    assert 'name="cm_inspect_limit"' not in captured["body"]
    assert 'name="triage_profile_limit"' not in captured["body"]
    assert 'name="parallelism"' not in captured["body"]
    assert 'name="user" type="text" value="bob"' not in captured["body"]
    assert 'name="order"' not in captured["body"]
    assert "Query Doctor recent scan failed with exit code 1" in captured["body"]
    assert "SELECT" not in captured["body"]
    assert "secret_column" not in captured["body"]
    assert "raw json" not in captured["body"]


def test_web_batch_job_failure_shows_safe_subprocess_hint(tmp_path):
    module = load_web_module()
    settings = module.WebSettings(config=tmp_path / "cm-config.json", repo_dir=REPO_DIR)
    settings.config.write_text("{}", encoding="utf-8")
    store = module.WebJobStore()

    def fake_runner(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--out") + 1])
        progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps({"stage": "batch", "status": "failed", "phase": "runtime"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            cmd,
            2,
            stdout="SELECT secret_column FROM table",
            stderr=(
                "[batch] ERROR: Kerberos ticket cache is missing or expired; "
                "refresh it before metadata collection. FILE:/tmp/krb5cc_secret"
            ),
        )

    status, location = module.start_batch_job(
        {"analysis_depth": ["fast"], "parallelism": ["1"]},
        settings,
        store,
        runner=fake_runner,
    )

    assert status == 303
    job_id = job_id_from_location(location)
    snapshot = store.get(job_id)
    for _ in range(50):
        if snapshot is not None and snapshot.status == "failed":
            break
        time.sleep(0.01)
        snapshot = store.get(job_id)

    assert snapshot is not None
    assert snapshot.status == "failed"
    payload = json.loads(module.render_job_status_json(snapshot))
    assert "metadata Kerberos ticket is missing or expired" in payload["error"]
    assert payload["error_info"]["reason_code"] == "impala.metadata_kerberos_ticket_unavailable"
    assert payload["error_info"]["stage"] == "Query Doctor recent scan"
    assert "Renew the Kerberos ticket" in payload["error_info"]["next_step"]
    assert "impala.metadata_kerberos_ticket_unavailable" in payload["error_html"]
    assert "Captured subprocess output is not shown" in payload["error"]
    assert "SELECT" not in payload["error"]
    assert "secret_column" not in payload["error"]
    assert "krb5cc_secret" not in payload["error"]


def test_web_handler_rejects_missing_query_id_without_calling_analysis():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("analysis must not run without query id")

    status, body = module.handle_analyze_request({}, settings, analysis_func=fail_if_called)

    assert status == 400
    assert "Query ID is required." in body


def test_web_handler_preserves_selected_cluster_when_query_id_missing():
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig

    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        clusters=(
            WebClusterConfig(key="prod", label="Production"),
            WebClusterConfig(key="stage", label="Staging"),
        ),
        active_cluster_key="prod",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("analysis must not run without query id")

    status, body = module.handle_analyze_request(
        {"cluster_key": ["stage"]},
        settings,
        analysis_func=fail_if_called,
    )

    assert status == 400
    assert "Query ID is required." in body
    assert '<select class="input" id="diagnosis_cluster_key" name="cluster_key">' in body
    assert (
        '<option value="stage" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Staging</option>' in body
    )
    assert '<input type="hidden" name="cluster_key" value="stage">' in body
    assert 'class="run-main-row known-query-row"' in body
    assert 'id="query_cluster_key"' not in body


def test_web_job_preserves_selected_cluster_during_query_id_analysis():
    module = load_web_module()
    from query_doctor.web.models import WebClusterConfig

    settings = module.WebSettings(
        config=Path(".query-doctor-cm.local.json"),
        clusters=(
            WebClusterConfig(key="prod", label="Production"),
            WebClusterConfig(key="ambari", label="Ambari"),
        ),
        active_cluster_key="prod",
    )
    store = module.WebJobStore()

    def slow_analysis(*args, **kwargs):
        time.sleep(0.05)
        raise module.WebError("stop after form state assertion")

    status, location = module.start_analyze_job(
        {"query_id": ["abc:def"], "cluster_key": ["ambari"]},
        settings,
        store,
        analysis_func=slow_analysis,
    )

    assert status == 303
    snapshot = store.get(job_id_from_location(location))
    assert snapshot is not None
    body = module.render_query_page(settings, job=snapshot)

    assert '<select class="input" id="diagnosis_cluster_key" name="cluster_key">' in body
    assert (
        '<option value="ambari" selected data-engine-impala-ready="true" '
        'data-engine-trino-ready="false" data-trino-beta-query-ready="false" '
        'data-trino-beta-recent-ready="false">Ambari</option>' in body
    )
    assert '<input type="hidden" name="cluster_key" value="ambari">' in body
    assert 'class="run-main-row known-query-row"' in body
    assert 'class="batch-progress-steps job-progress-steps"' in body
    assert 'id="query_cluster_key"' not in body


def test_web_handler_defaults_to_analysis_mode_with_privacy_redaction():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    def fake_analysis(query_id, report_mode, redact_identifiers, received_settings):
        assert query_id == "abc:def"
        assert report_mode == "analysis"
        assert redact_identifiers is True
        assert received_settings is settings
        return module.WebQueryAnalysisResult(
            query_id=query_id,
            case={
                "query_id": query_id,
                "score": 0,
                "duration_sec": None,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["no analyzer-supported suspicious facts"],
                "report_generated": False,
                "report_validation_status": "not_run",
            },
        )

    status, body = module.handle_analyze_request(
        {"query_id": ["abc:def"]}, settings, analysis_func=fake_analysis
    )

    assert status == 200
    assert "Known Query ID analysis" in body
    assert "Mode" not in body


def test_web_handler_sanitizes_error_secrets(monkeypatch):
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))
    monkeypatch.setenv("CM_PASSWORD", "secret-password")
    monkeypatch.setenv("CM_TOKEN", "secret-token")

    def fake_analysis(*args, **kwargs):
        raise module.WebError(
            "password=secret-password token=secret-token Authorization: Bearer secret-token"
        )

    status, body = module.handle_analyze_request(
        {"query_id": ["abc:def"], "mode": ["admin"]},
        settings,
        analysis_func=fake_analysis,
    )

    assert status == 400
    assert "secret-password" not in body
    assert "secret-token" not in body
    assert "&lt;secret&gt;" in body or "&lt;redacted&gt;" in body
    assert "Safe inspection state" in body
    assert "Unvalidated or partial report output is hidden." in body


def test_web_handler_sanitizes_error_local_paths():
    module = load_web_module()
    settings = module.WebSettings(config=Path(".query-doctor-cm.local.json"))

    def fake_analysis(*args, **kwargs):
        raise module.WebError(
            "failed at /Users/example/query-doctor/cases/case-001 "
            "and /private/tmp/query-doctor-web-batch-abc "
            "and C:\\Users\\example\\query-doctor\\case"
        )

    status, body = module.handle_analyze_request(
        {"query_id": ["abc:def"], "mode": ["admin"]},
        settings,
        analysis_func=fake_analysis,
    )

    assert status == 400
    assert "/Users/example" not in body
    assert "/private/tmp" not in body
    assert "C:\\Users\\example" not in body
    assert "local path hidden" in body
    assert "Safe inspection state" in body


def test_web_subprocess_failures_do_not_render_raw_output(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    settings = module.WebSettings(config=config, repo_dir=REPO_DIR)

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout='{"profile": "raw json"}',
            stderr="SELECT secret_column FROM sensitive_table",
        )

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "exit code 1" in message
    assert "SELECT" not in message
    assert "secret_column" not in message
    assert "raw json" not in message


def test_web_missing_cm_credentials_fails_before_collector(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.delenv("CM_TOKEN", raising=False)
    monkeypatch.delenv("CM_USERNAME", raising=False)
    monkeypatch.delenv("CM_PASSWORD", raising=False)
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("collector/analyzer/report subprocess must not run")

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "CM credentials were not found in the web server environment" in message
    assert "CM_USERNAME/CM_PASSWORD or CM_TOKEN" in message
    assert calls == []


def test_web_missing_cm_credentials_renders_safe_english_ui_message(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.delenv("CM_TOKEN", raising=False)
    monkeypatch.setenv("CM_USERNAME", "alice-secret")
    monkeypatch.delenv("CM_PASSWORD", raising=False)
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    status, body = module.handle_analyze_request(
        {"query_id": ["abc:def"], "mode": ["admin"]},
        settings,
    )

    assert status == 400
    assert "CM credentials were not found in the web server environment" in body
    assert "CM_USERNAME/CM_PASSWORD or CM_TOKEN" in body
    assert "alice-secret" not in body


def test_web_cm_token_alone_allows_collector(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    monkeypatch.delenv("CM_USERNAME", raising=False)
    monkeypatch.delenv("CM_PASSWORD", raising=False)
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        case_dir.mkdir(parents=True)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"Output case directory: {case_dir}\n", stderr=""
        )

    result = module.collect_case("abc:def", case_dir, False, settings, fake_runner)

    assert result == case_dir
    assert len(calls) == 1
    assert command_uses_role(calls[0], "collect_cm")


def test_web_username_password_allows_collector(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.delenv("CM_TOKEN", raising=False)
    monkeypatch.setenv("CM_USERNAME", "alice")
    monkeypatch.setenv("CM_PASSWORD", "secret-password")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        case_dir.mkdir(parents=True)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"Output case directory: {case_dir}\n", stderr=""
        )

    result = module.collect_case("abc:def", case_dir, False, settings, fake_runner)

    assert result == case_dir
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("CM_USERNAME", "alice-secret"),
        ("CM_PASSWORD", "password-secret"),
    ],
)
def test_web_partial_cm_credentials_fail_without_rendering_values(
    monkeypatch, tmp_path, env_name, env_value
):
    module = load_web_module()
    monkeypatch.delenv("CM_TOKEN", raising=False)
    monkeypatch.delenv("CM_USERNAME", raising=False)
    monkeypatch.delenv("CM_PASSWORD", raising=False)
    monkeypatch.setenv(env_name, env_value)
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("collector/analyzer/report subprocess must not run")

    with pytest.raises(module.WebError) as excinfo:
        module.collect_case("abc:def", case_dir, False, settings, fake_runner)

    message = str(excinfo.value)
    assert "CM credentials were not found" in message
    assert env_value not in message
    assert calls == []


def test_web_invalid_report_mode_is_rejected(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess must not run for invalid mode")

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "developer", False, settings, runner=fail_if_called)

    assert "admin or user" in str(excinfo.value)


def test_web_run_analysis_uses_subprocess_list_args_and_tmp_outputs(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        max_profile_bytes=12345,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        timeout_sec=99,
    )
    calls = []
    progress_stages = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert isinstance(cmd, list)
        assert "shell" not in kwargs
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 99
        if command_uses_role(cmd, "collect_cm"):
            assert "--query-id" in cmd
            assert "--limit" in cmd
            assert cmd[cmd.index("--limit") + 1] == "1"
            assert "--redact" in cmd
            assert "--max-profile-bytes" in cmd
            assert "--out" in cmd
            assert cmd[cmd.index("--out") + 1] == str(settings.corpus_dir)
            case_dir.mkdir(parents=True)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "pipeline"):
            assert "--skip-report" in cmd
            (case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 7\n- Cardinality anomalies: 0\n- Memory anomalies: 2\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            assert "--model" in cmd
            assert "qwen3-coder:30b-a3b-q8_0" in cmd
            assert "--validation-mode" in cmd
            assert cmd[cmd.index("--validation-mode") + 1] == "strict"
            (case_dir / "report_admin.md").write_text("## Safe report\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_web_analysis(
        "abc:def",
        "admin",
        False,
        settings,
        runner=fake_runner,
        progress=progress_stages.append,
    )

    assert result.case_dir == case_dir
    assert result.case_source == "collected now"
    assert result.parsed_operators == "7"
    assert result.cardinality_anomalies == "0"
    assert result.memory_anomalies == "2"
    assert result.report_text == "## Safe report\n"
    assert len(calls) == 3
    assert progress_stages == [0, 1, 2, 3, 4, 5]


def test_web_query_id_analysis_generates_python_report_without_llm(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        max_profile_bytes=12345,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        timeout_sec=99,
    )
    calls = []
    progress_stages = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert isinstance(cmd, list)
        assert "shell" not in kwargs
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 99
        if command_uses_role(cmd, "collect_cm"):
            out_dir = Path(cmd[cmd.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            (staged_case_dir / "cm_metadata.json").write_text(
                json.dumps({"duration_sec": 90.5}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {staged_case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "pipeline"):
            assert "--stop-after-analysis" in cmd
            assert "--skip-report" not in cmd
            assert cmd[cmd.index("--metadata-mode") + 1] == "off"
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            (command_case_dir / "analysis_facts.md").write_text(
                "\n".join(
                    [
                        "- Parsed operators: 7",
                        "- Cardinality anomalies: 2",
                        "- Memory anomalies: 1",
                        "- table stats row-count completeness: available",
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def",
        "analysis",
        False,
        settings,
        runner=fake_runner,
        progress=progress_stages.append,
    )

    assert result.query_id == "abc:def"
    assert result.case["query_id"] == "abc:def"
    assert result.case["duration_sec"] == 90.5
    assert result.case["score"] > 0
    assert result.case["table_stats_status"] == "available"
    assert "case_dir" not in result.case
    assert "case_index" not in result.case
    assert (case_dir / "diagnosis_python.md").is_file()
    assert (case_dir / "diagnosis_python.validated.json").is_file()
    assert len(calls) == 3
    assert progress_stages == [0, 1, 2, 3, 4, 5]


def test_web_query_id_analysis_reuses_manual_profile_case_without_collector(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"profile_source": "manual_profile_text"}),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        timeout_sec=99,
    )
    calls = []
    progress_stages = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm") or command_uses_role(cmd, "collect_impala_profile"):
            raise AssertionError("collector must not run for a complete existing case")
        if command_uses_role(cmd, "pipeline"):
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            assert command_case_dir == case_dir
            (command_case_dir / "analysis_facts.md").write_text(
                "\n".join(
                    [
                        "- Parsed operators: 3",
                        "- Cardinality anomalies: 1",
                        "- Memory anomalies: 0",
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def",
        "analysis",
        False,
        settings,
        runner=fake_runner,
        progress=progress_stages.append,
    )

    assert result.query_id == "abc:def"
    assert result.case["query_id"] == "abc:def"
    assert result.case["score"] > 0
    assert (case_dir / "diagnosis_python.md").is_file()
    assert (case_dir / "diagnosis_python.validated.json").is_file()
    assert len(calls) == 2
    assert command_uses_role(calls[0], "pipeline")
    assert command_uses_role(calls[1], "report")
    assert progress_stages == [0, 1, 2, 3, 4, 5]


def test_web_query_id_analysis_stages_matching_manual_profile_from_directory_without_collector(
    tmp_path,
):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    profile_path = profile_dir / "abc_def.txt"
    profile_path.write_text("Query Runtime Profile\nQuery ID: abc:def\n", encoding="utf-8")
    final_case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        manual_profile_dir=profile_dir,
        timeout_sec=99,
    )
    calls = []
    progress_stages = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm") or command_uses_role(cmd, "collect_impala_profile"):
            raise AssertionError("collector must not run for a matching manual profile")
        if command_uses_role(cmd, "analyze"):
            args = command_args(cmd, "analyze")
            assert args[args.index("--profile-text") + 1] == str(profile_path.resolve())
            assert args[args.index("--query-id") + 1] == "abc:def"
            out_dir = Path(args[args.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            (staged_case_dir / "cm_metadata.json").write_text(
                json.dumps({"profile_source": "manual_profile_text"}),
                encoding="utf-8",
            )
            (staged_case_dir / "analysis_facts.md").write_text(
                "\n".join(
                    [
                        "- Parsed operators: 3",
                        "- Cardinality anomalies: 1",
                        "- Memory anomalies: 0",
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {staged_case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def",
        "analysis",
        False,
        settings,
        runner=fake_runner,
        progress=progress_stages.append,
    )

    assert result.query_id == "abc:def"
    assert result.case["query_id"] == "abc:def"
    assert result.case["score"] > 0
    assert final_case_dir.is_dir()
    assert (final_case_dir / "analysis_facts.md").is_file()
    assert (final_case_dir / "diagnosis_python.md").is_file()
    assert (final_case_dir / "diagnosis_python.validated.json").is_file()
    assert len(calls) == 2
    assert command_uses_role(calls[0], "analyze")
    assert command_uses_role(calls[1], "report")
    assert progress_stages == [0, 1, 2, 3, 4, 5]


def test_web_query_id_analysis_uses_configured_corpus_dir_for_manual_profile_inbox(
    tmp_path,
):
    module = load_web_module()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    profile_dir = config_dir / "profile-inbox"
    profile_dir.mkdir()
    profile_path = profile_dir / "abc_def.txt"
    profile_path.write_text("Query Runtime Profile\nQuery ID: abc:def\n", encoding="utf-8")
    config = config_dir / "manual-profile-config.json"
    config.write_text(
        json.dumps(
            {
                "manual_profile_dir": "profile-inbox",
                "corpus_dir": "web-corpus",
            }
        ),
        encoding="utf-8",
    )
    settings = module.build_web_settings(module.parse_args(["--config", str(config)]), cwd=tmp_path)
    final_case_dir = config_dir / "web-corpus" / "abc_def"
    calls = []

    def fake_runner(cmd, **_kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm") or command_uses_role(cmd, "collect_impala_profile"):
            raise AssertionError("collector must not run for a matching manual profile")
        if command_uses_role(cmd, "analyze"):
            args = command_args(cmd, "analyze")
            assert args[args.index("--profile-text") + 1] == str(profile_path.resolve())
            out_dir = Path(args[args.index("--out") + 1])
            assert out_dir.parent == config_dir / "web-corpus"
            assert out_dir.name.startswith(".query-refresh-")
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            (staged_case_dir / "cm_metadata.json").write_text(
                json.dumps({"profile_source": "manual_profile_text"}),
                encoding="utf-8",
            )
            (staged_case_dir / "analysis_facts.md").write_text(
                "\n".join(
                    [
                        "- Parsed operators: 3",
                        "- Cardinality anomalies: 1",
                        "- Memory anomalies: 0",
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {staged_case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def",
        "analysis",
        False,
        settings,
        runner=fake_runner,
    )

    assert result.query_id == "abc:def"
    assert final_case_dir.is_dir()
    assert not (tmp_path / "cm-corpus" / "abc_def").exists()
    assert (final_case_dir / "diagnosis_python.md").is_file()
    assert (final_case_dir / "diagnosis_python.validated.json").is_file()
    assert len(calls) == 2
    assert command_uses_role(calls[0], "analyze")
    assert command_uses_role(calls[1], "report")


def test_web_query_id_analysis_prefers_manual_profile_over_configured_live_collector(
    tmp_path,
):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    profile_path = profile_dir / "abc_def.txt"
    profile_path.write_text("Query Runtime Profile\nQuery ID: abc:def\n", encoding="utf-8")
    final_case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        manual_profile_dir=profile_dir,
        cm_url="https://cm.example.invalid",
        cm_cluster="cluster",
        cm_service="impala",
        timeout_sec=99,
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm") or command_uses_role(cmd, "collect_impala_profile"):
            raise AssertionError("manual profile inbox must take precedence over live collection")
        if command_uses_role(cmd, "analyze"):
            args = command_args(cmd, "analyze")
            assert args[args.index("--profile-text") + 1] == str(profile_path.resolve())
            assert args[args.index("--query-id") + 1] == "abc:def"
            out_dir = Path(args[args.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            (staged_case_dir / "cm_metadata.json").write_text(
                json.dumps({"profile_source": "manual_profile_text"}),
                encoding="utf-8",
            )
            (staged_case_dir / "analysis_facts.md").write_text(
                "\n".join(
                    [
                        "- Parsed operators: 3",
                        "- Cardinality anomalies: 1",
                        "- Memory anomalies: 0",
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {staged_case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def",
        "analysis",
        False,
        settings,
        runner=fake_runner,
    )

    assert result.query_id == "abc:def"
    assert final_case_dir.is_dir()
    assert (final_case_dir / "diagnosis_python.md").is_file()
    assert (final_case_dir / "diagnosis_python.validated.json").is_file()
    assert len(calls) == 2
    assert command_uses_role(calls[0], "analyze")
    assert command_uses_role(calls[1], "report")


def test_manual_profile_file_for_query_uses_deterministic_suffix_order(tmp_path):
    module = load_web_module()

    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        manual_profile_dir=profile_dir,
    )
    slug = "abc_def"
    suffixes = (".profile", ".log", ".md", ".text", "")
    for suffix in suffixes:
        (profile_dir / f"{slug}{suffix}").write_text(
            f"Query Runtime Profile\nQuery ID: abc:def\nsuffix={suffix or '<none>'}\n",
            encoding="utf-8",
        )

    assert (
        manual_profile_file_for_query("abc:def", settings)
        == (profile_dir / "abc_def.profile").resolve()
    )

    (profile_dir / "abc_def.txt").write_text(
        "Query Runtime Profile\nQuery ID: abc:def\nsuffix=.txt\n",
        encoding="utf-8",
    )

    assert (
        manual_profile_file_for_query("abc:def", settings)
        == (profile_dir / "abc_def.txt").resolve()
    )


def test_manual_profile_file_for_query_ignores_symlink_escape(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    outside_profile = tmp_path / "outside-profile.txt"
    outside_profile.write_text("Query Runtime Profile\nQuery ID: abc:def\n", encoding="utf-8")
    inbox_link = profile_dir / "abc_def.txt"
    try:
        inbox_link.symlink_to(outside_profile)
    except OSError as exc:
        pytest.skip(f"symlink creation is not supported in this test environment: {exc}")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        manual_profile_dir=profile_dir,
    )

    def fake_runner(cmd, **_kwargs):
        raise AssertionError(f"escaped manual profile must not run subprocess: {cmd}")

    with pytest.raises(module.WebError) as exc:
        module.run_query_id_analysis(
            "abc:def",
            "analysis",
            False,
            settings,
            runner=fake_runner,
        )

    assert "No matching local exported profile" in str(exc.value)


def test_web_query_id_analysis_rejects_misnamed_manual_profile_without_replacing_case(
    tmp_path,
):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    profile_path = profile_dir / "abc_def.txt"
    requested_query_id = "abc:def"
    mismatched_profile_query_id = "wrong:def"
    profile_path.write_text(
        f"Query Runtime Profile\nQuery ID: {mismatched_profile_query_id}\n",
        encoding="utf-8",
    )
    final_case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(final_case_dir)
    existing_facts = final_case_dir / "analysis_facts.md"
    existing_facts.write_text("existing safe facts\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=REPO_DIR,
        corpus_dir=tmp_path / "cm-corpus",
        manual_profile_dir=profile_dir,
        timeout_sec=30,
    )

    with pytest.raises(module.WebError) as exc:
        module.run_query_id_analysis(
            requested_query_id,
            "analysis",
            False,
            settings,
        )

    message = str(exc.value)
    assert "Manual profile analysis failed with exit code 2" in message
    assert requested_query_id not in message
    assert mismatched_profile_query_id not in message
    assert existing_facts.read_text(encoding="utf-8") == "existing safe facts\n"
    assert not list((tmp_path / "cm-corpus").glob(".query-refresh-*"))


def test_web_query_id_analysis_manual_profile_only_missing_file_does_not_collect(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    profile_dir = tmp_path / "profile-inbox"
    profile_dir.mkdir()
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        manual_profile_dir=profile_dir,
    )

    def fake_runner(cmd, **_kwargs):
        raise AssertionError(f"manual-only missing profile must not run subprocess: {cmd}")

    with pytest.raises(module.WebError) as exc:
        module.run_query_id_analysis(
            "abc:def",
            "analysis",
            False,
            settings,
            runner=fake_runner,
        )

    assert "No matching local exported profile" in str(exc.value)
    assert "replace the ':' separator with '_'" in str(exc.value)
    assert ".txt" in str(exc.value)


def test_web_query_id_analysis_incomplete_manual_profile_case_has_actionable_message(
    tmp_path,
):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    case_dir.mkdir(parents=True)
    (case_dir / "query_metadata.json").write_text(
        json.dumps({"profile_source": "manual_profile_text"}),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fake_runner(cmd, **_kwargs):
        raise AssertionError(f"incomplete manual case must not run subprocess: {cmd}")

    with pytest.raises(module.WebError) as exc:
        module.run_query_id_analysis(
            "abc:def",
            "analysis",
            False,
            settings,
            runner=fake_runner,
        )

    message = str(exc.value)
    assert "Existing local manual-profile case is incomplete" in message
    assert "configured manual profile directory" in message
    assert "replace ':' with '_'" in message
    assert "remove the incomplete local case" in message
    assert "Re-run analysis to regenerate required artifacts" not in message
    assert "profile_digest.md" not in message
    assert "query_metadata.json" not in message
    assert str(case_dir) not in message
    assert "case_dir" not in message


def test_web_query_id_analysis_can_collect_direct_impala_profile_without_cm_credentials(
    monkeypatch, tmp_path
):
    module = load_web_module()
    monkeypatch.delenv("CM_TOKEN", raising=False)
    monkeypatch.delenv("CM_PASSWORD", raising=False)
    config = tmp_path / "impala-config.json"
    config.write_text(
        json.dumps(
            {
                "query_profile_source": "impala",
                "impala_profile_hosts": [
                    "impalad-1.example.com",
                    "impalad-2.example.com",
                    "impalad-3.example.com",
                ],
                "impala_profile_prefer_json": True,
                "impala_profile_collect_docs": True,
                "impala_collect_admission_context": True,
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        max_profile_bytes=12345,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        timeout_sec=99,
        query_profile_source="impala",
        impala_profile_hosts=(
            "impalad-1.example.com",
            "impalad-2.example.com",
            "impalad-3.example.com",
        ),
        impala_profile_port=25000,
        impala_profile_scheme="http",
        impala_profile_timeout_sec=12,
        impala_profile_prefer_json=True,
        impala_profile_collect_docs=True,
        impala_collect_admission_context=True,
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_impala_profile"):
            assert "--config" not in cmd
            assert cmd[cmd.index("--query-id") + 1] == "abc:def"
            assert cmd[cmd.index("--max-profile-bytes") + 1] == "12345"
            assert cmd[cmd.index("--timeout-sec") + 1] == "12"
            assert "--prefer-json-profile" in cmd
            assert "--collect-profile-docs" in cmd
            assert "--collect-admission-context" in cmd
            assert cmd.count("--host") == 3
            out_dir = Path(cmd[cmd.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {staged_case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "pipeline"):
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            (command_case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 1\n- Cardinality anomalies: 0\n- Memory anomalies: 0\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def", "analysis", False, settings, runner=fake_runner
    )

    assert result.query_id == "abc:def"
    assert any(command_uses_role(cmd, "collect_impala_profile") for cmd in calls)
    assert not any(command_uses_role(cmd, "collect_cm") for cmd in calls)
    assert sum(command_uses_role(cmd, "report") for cmd in calls) == 1


def test_web_query_id_analysis_passes_prometheus_metrics_flags_for_direct_impala(
    monkeypatch, tmp_path
):
    module = load_web_module()
    monkeypatch.delenv("CM_TOKEN", raising=False)
    monkeypatch.delenv("CM_PASSWORD", raising=False)
    config = tmp_path / "impala-config.json"
    config.write_text(
        json.dumps(
            {
                "query_profile_source": "impala",
                "impala_profile_hosts": ["impalad-1.example.com"],
                "collect_prometheus_timeseries": True,
                "prometheus_url": "http://prometheus.example.com:9090",
            }
        ),
        encoding="utf-8",
    )
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        query_profile_source="impala",
        impala_profile_hosts=("impalad-1.example.com",),
        collect_prometheus_timeseries=True,
        prometheus_url="http://prometheus.example.com:9090",
        prometheus_metrics_profile="ambari-hadoop",
        prometheus_step_sec=45,
        prometheus_timeseries_padding_sec=180,
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_impala_profile"):
            assert cmd[cmd.index("--prometheus-url") + 1] == "http://prometheus.example.com:9090"
            assert "--collect-prometheus-timeseries" in cmd
            assert cmd[cmd.index("--prometheus-metrics-profile") + 1] == "ambari-hadoop"
            assert cmd[cmd.index("--prometheus-step-sec") + 1] == "45"
            assert cmd[cmd.index("--prometheus-timeseries-padding-sec") + 1] == "180"
            out_dir = Path(cmd[cmd.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {staged_case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "pipeline"):
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            (command_case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 1\n- Cardinality anomalies: 0\n- Memory anomalies: 0\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def", "analysis", False, settings, runner=fake_runner
    )

    assert result.query_id == "abc:def"
    assert any(command_uses_role(cmd, "collect_impala_profile") for cmd in calls)
    assert sum(command_uses_role(cmd, "report") for cmd in calls) == 1


def test_web_query_id_analysis_collects_metadata_when_configured(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
        metadata_coordinator="impala.example.com:21000",
        metadata_auth="kerberos",
        metadata_protocol="hs2",
        metadata_kerberos_service_name="hive",
        metadata_timeout_sec=45,
        metadata_max_tables=7,
        metadata_max_output_bytes=123456,
        metadata_redact=True,
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            out_dir = Path(cmd[cmd.index("--out") + 1])
            assert "--metadata-source-tables-out" in cmd
            source_tables_path = Path(cmd[cmd.index("--metadata-source-tables-out") + 1])
            assert source_tables_path.parent == out_dir
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            source_tables_path.write_text(
                json.dumps(["example_warehouse.real_table"]) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"Output case directory: {staged_case_dir}\n", stderr=""
            )
        if command_uses_role(cmd, "pipeline"):
            assert kwargs["env"]["QD_METADATA_SOURCE_TABLES_JSON"] == json.dumps(
                ["example_warehouse.real_table"]
            )
            assert "--stop-after-analysis" in cmd
            assert "--skip-report" not in cmd
            assert cmd[cmd.index("--metadata-mode") + 1] == "on"
            assert cmd[cmd.index("--metadata-coordinator") + 1] == "impala.example.com:21000"
            assert "--metadata-impala-shell" not in cmd
            assert cmd[cmd.index("--metadata-kerberos-service-name") + 1] == "hive"
            assert cmd[cmd.index("--metadata-timeout-sec") + 1] == "45"
            assert cmd[cmd.index("--metadata-max-tables") + 1] == "7"
            assert cmd[cmd.index("--metadata-max-output-bytes") + 1] == "123456"
            assert "--metadata-redact" in cmd
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            (command_case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 1\n- Cardinality anomalies: 0\n- Memory anomalies: 0\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    module.run_query_id_analysis("abc:def", "analysis", False, settings, runner=fake_runner)

    assert sum(command_uses_role(cmd, "pipeline") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "report") for cmd in calls) == 1
    assert not any((tmp_path / "cm-corpus").glob(".query-refresh-*"))
    assert not (case_dir / ".metadata-source-tables.json").exists()


def test_web_query_id_analysis_refreshes_existing_case_after_success(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text(
        "- Parsed operators: 1\n- Cardinality anomalies: 0\n- Memory anomalies: 0\n",
        encoding="utf-8",
    )
    (case_dir / "old_report.md").write_text("old trusted output\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            out_dir = Path(cmd[cmd.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            (staged_case_dir / "cm_metadata.json").write_text(
                json.dumps({"duration_sec": 321.0}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"Output case directory: {staged_case_dir}\n", stderr=""
            )
        if command_uses_role(cmd, "pipeline"):
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            assert command_case_dir != case_dir
            (command_case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 9\n- Cardinality anomalies: 2\n- Memory anomalies: 0\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return complete_python_report_command(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_query_id_analysis(
        "abc:def", "analysis", False, settings, runner=fake_runner
    )

    assert result.case["duration_sec"] == 321.0
    assert (
        (case_dir / "analysis_facts.md")
        .read_text(encoding="utf-8")
        .startswith("- Parsed operators: 9")
    )
    assert not (case_dir / "old_report.md").exists()
    assert (case_dir / "diagnosis_python.md").is_file()
    assert (case_dir / "diagnosis_python.validated.json").is_file()
    assert not list((tmp_path / "cm-corpus").glob(".query-refresh-*"))
    assert sum(command_uses_role(cmd, "collect_cm") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "pipeline") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "report") for cmd in calls) == 1


def test_web_query_id_analysis_keeps_existing_case_when_refresh_analysis_fails(
    monkeypatch, tmp_path
):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text("old safe facts\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fake_runner(cmd, **kwargs):
        if command_uses_role(cmd, "collect_cm"):
            out_dir = Path(cmd[cmd.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"Output case directory: {staged_case_dir}\n", stderr=""
            )
        if command_uses_role(cmd, "pipeline"):
            return subprocess.CompletedProcess(
                cmd, 5, stdout="raw profile text", stderr="SELECT sensitive"
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with pytest.raises(module.WebError) as excinfo:
        module.run_query_id_analysis("abc:def", "analysis", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "Query Doctor analyzer failed" in message
    assert "SELECT" not in message
    assert "sensitive" not in message
    assert (case_dir / "analysis_facts.md").read_text(encoding="utf-8") == "old safe facts\n"
    assert not list((tmp_path / "cm-corpus").glob(".query-refresh-*"))


def test_web_query_id_analysis_keeps_existing_case_when_refresh_report_fails(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    (case_dir / "analysis_facts.md").write_text("old safe facts\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            out_dir = Path(cmd[cmd.index("--out") + 1])
            staged_case_dir = out_dir / "abc_def"
            write_complete_collected_case(staged_case_dir)
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"Output case directory: {staged_case_dir}\n", stderr=""
            )
        if command_uses_role(cmd, "pipeline"):
            command_case_dir = Path(command_args(cmd, "pipeline")[0])
            assert command_case_dir != case_dir
            (command_case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 9\n- Cardinality anomalies: 2\n- Memory anomalies: 0\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            command_case_dir = Path(command_args(cmd, "report")[0])
            assert command_case_dir != case_dir
            return subprocess.CompletedProcess(
                cmd,
                module.REPORT_VALIDATION_EXIT_CODE,
                stdout="invalid report with raw profile text",
                stderr="SELECT sensitive",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with pytest.raises(module.WebError) as excinfo:
        module.run_query_id_analysis("abc:def", "analysis", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "Report generation completed but validation rejected the output" in message
    assert "SELECT" not in message
    assert "sensitive" not in message
    assert (case_dir / "analysis_facts.md").read_text(encoding="utf-8") == "old safe facts\n"
    assert not (case_dir / "diagnosis_python.md").exists()
    assert not list((tmp_path / "cm-corpus").glob(".query-refresh-*"))
    assert sum(command_uses_role(cmd, "collect_cm") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "pipeline") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "report") for cmd in calls) == 1


def test_web_retries_report_generation_once_after_validation_failure(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []
    report_attempts = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            write_complete_collected_case(case_dir)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"Output case directory: {case_dir}\n",
                stderr="",
            )
        if command_uses_role(cmd, "pipeline"):
            (case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 4\n- Cardinality anomalies: 0\n- Memory anomalies: 1\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            report_attempts.append(cmd)
            if len(report_attempts) == 1:
                return subprocess.CompletedProcess(
                    cmd,
                    4,
                    stdout="invalid report with raw profile text",
                    stderr="SELECT secret FROM table",
                )
            (case_dir / "report_admin.md").write_text("## Retried safe report\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    assert result.report_retry is True
    assert result.report_text == "## Retried safe report\n"
    assert sum(command_uses_role(cmd, "collect_cm") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "pipeline") for cmd in calls) == 1
    assert sum(command_uses_role(cmd, "report") for cmd in calls) == 2


def test_web_report_validation_failure_message_is_safe_after_retry_failure(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            raise AssertionError("collector must not run for a reused case or report retry")
        if command_uses_role(cmd, "pipeline"):
            (case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 4\n- Cardinality anomalies: 0\n- Memory anomalies: 1\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return subprocess.CompletedProcess(
                cmd,
                4,
                stdout='{"profile": "raw json"}',
                stderr="Authorization: Bearer secret-token SELECT sensitive_sql",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "deterministic validator rejected" in message
    assert "unsafe report is not shown" in message
    assert "SELECT" not in message
    assert "secret_column" not in message
    assert "raw json" not in message
    assert "secret-token" not in message
    assert len(calls) == 3


def test_web_other_report_generation_failure_remains_generic_and_sanitized(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fake_runner(cmd, **kwargs):
        if command_uses_role(cmd, "pipeline"):
            (case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 4\n- Cardinality anomalies: 0\n- Memory anomalies: 1\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            return subprocess.CompletedProcess(
                cmd,
                5,
                stdout='{"profile": "raw json"}',
                stderr="SELECT secret_column FROM sensitive_table",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "Query Doctor report generation failed with exit code 5" in message
    assert "SELECT" not in message
    assert "secret_column" not in message
    assert "raw json" not in message


def test_subprocess_failure_message_adds_safe_exit_2_hint():
    module = load_web_module()
    message = module.subprocess_failure_message(
        "Query Doctor recent scan",
        subprocess.CompletedProcess(
            [],
            2,
            stdout='{"profile": "raw json"}',
            stderr="SELECT secret_column FROM sensitive_table",
        ),
    )

    assert "Query Doctor recent scan failed with exit code 2" in message
    assert "command-line argument validation or local configuration validation failed" in message
    assert "SELECT" not in message
    assert "secret_column" not in message
    assert "raw json" not in message


def test_subprocess_failure_web_error_adds_safe_reason_code():
    module = load_web_module()
    error = module.subprocess_failure_web_error(
        "CM single-query collection",
        subprocess.CompletedProcess(
            [],
            2,
            stdout="SELECT secret_column FROM sensitive_table",
            stderr=(
                "[CM profile collector] Collection result: FAILED\n"
                "Single-query collection failed: HTTP 404 from CM: "
                "https://cm-private.example.com/api/v32/clusters/prod/services/impala/"
                "impalaQueries/aaaaaaaaaaaaaaaa%3A0000000000000001?format=text"
            ),
        ),
    )

    message = str(error)
    assert error.reason_code == "impala.cm_profile_not_found"
    assert error.title == "Cloudera Manager profile was not found"
    assert error.stage == "CM single-query collection"
    assert "Check CM retention" in str(error.next_step)
    assert "Cloudera Manager did not find a profile" in message
    assert "Captured subprocess output is not shown" in message
    assert "SELECT" not in message
    assert "secret_column" not in message
    assert "sensitive_table" not in message
    assert "cm-private.example.com" not in message
    assert "aaaaaaaaaaaaaaaa" not in message


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "[batch] ERROR: KRB5CCNAME is required before metadata collection can use Kerberos.",
            "metadata Kerberos cache is not configured",
        ),
        (
            "[batch] ERROR: Kerberos ticket cache is missing or expired; refresh it before metadata collection.",
            "metadata Kerberos ticket is missing or expired",
        ),
        (
            "[batch] ERROR: impala metadata driver is not available; install query-doctor[impala]",
            "the impyla metadata driver is not installed",
        ),
        (
            "[batch] ERROR: Impala query discovery requires --impala-profile-host or local config impala_profile_hosts.",
            "direct Impala discovery has no configured impalad host",
        ),
        (
            "[Impala profile collector] ERROR: Single-query Impala profile collection failed: "
            "Impala profile was not found on the configured impalad endpoints. "
            "Query id aa:bb, host impalad-private.example.com, path /private/tmp/raw",
            "direct Impala profile was not found on the configured impalad endpoints",
        ),
        (
            "[Impala profile collector] ERROR: Single-query Impala profile collection failed: "
            "Last error: profile endpoint unavailable for https://impalad-private.example.com",
            "direct Impala profile endpoint is unavailable",
        ),
        (
            "[Impala profile collector] ERROR: Single-query Impala profile collection failed: "
            "Impala profile endpoint request failed safely.",
            "direct Impala profile endpoint request failed",
        ),
        (
            "[Impala profile collector] ERROR: Single-query Impala profile collection failed: "
            "Impala profile endpoint response exceeded the configured byte limit.",
            "direct Impala profile response exceeded the configured byte limit",
        ),
        (
            "[CM profile collector] Collection result: FAILED\n"
            "Single-query collection failed: HTTP 404 from CM: "
            "https://cm-private.example.com/api/v32/clusters/prod/services/impala/"
            "impalaQueries/aaaaaaaaaaaaaaaa%3A0000000000000001?format=text",
            "Cloudera Manager did not find a profile for the selected Query ID",
        ),
        (
            "[batch] ERROR: CM auth env is not set in this execution environment.",
            "Cloudera Manager credentials are not available",
        ),
        (
            "[batch] ERROR: --config-cluster 'private-prod' was not found in local config clusters[].",
            "selected cluster was not found in local config",
        ),
        (
            "[batch] ERROR: --out must point to a dedicated batch directory, not filesystem root",
            "batch output directory failed safety validation",
        ),
    ],
)
def test_subprocess_failure_message_adds_safe_allowlisted_hints(stderr, expected):
    module = load_web_module()
    message = module.subprocess_failure_message(
        "Query Doctor recent scan",
        subprocess.CompletedProcess(
            [],
            2,
            stdout="SELECT secret_column FROM sensitive_table",
            stderr=stderr,
        ),
    )

    assert expected in message
    assert "Captured subprocess output is not shown" in message
    assert "SELECT" not in message
    assert "secret_column" not in message
    assert "sensitive_table" not in message
    assert "private-prod" not in message
    assert "/private/tmp" not in message
    assert "aa:bb" not in message
    assert "impalad-private.example.com" not in message


def test_web_reuses_existing_complete_case_without_collector(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    write_complete_collected_case(case_dir)
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if command_uses_role(cmd, "collect_cm"):
            raise AssertionError("collector must not run for a complete existing case")
        if command_uses_role(cmd, "pipeline"):
            (case_dir / "analysis_facts.md").write_text(
                "- Parsed operators: 3\n- Cardinality anomalies: 0\n- Memory anomalies: 1\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if command_uses_role(cmd, "report"):
            (case_dir / "report_user.md").write_text("## Reused report\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    result = module.run_web_analysis(
        "abc:def",
        "user",
        False,
        settings,
        runner=fake_runner,
    )

    assert result.case_dir == case_dir
    assert result.case_source == "reused existing local case"
    assert result.report_text == "## Reused report\n"
    assert len(calls) == 2
    assert command_uses_role(calls[0], "pipeline")
    assert command_uses_role(calls[1], "report")


def test_web_existing_incomplete_case_fails_closed_without_collector(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "cm-corpus" / "abc_def"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fake_runner(*args, **kwargs):
        raise AssertionError("no subprocess should run for an incomplete existing case")

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "incomplete" in message
    assert "profile_digest.md" not in message
    assert "cm_metadata.json" not in message
    assert "collection_warnings.txt" not in message
    assert "Re-run analysis to regenerate required artifacts" in message
    assert str(case_dir) not in message
    assert "case_dir" not in message


def test_web_existing_case_file_error_hides_local_path(tmp_path):
    module = load_web_module()
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    case_path = tmp_path / "cm-corpus" / "abc_def"
    case_path.parent.mkdir(parents=True)
    case_path.write_text("not a directory\n", encoding="utf-8")
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fake_runner(*args, **kwargs):
        raise AssertionError("no subprocess should run for an incomplete existing case")

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    message = str(excinfo.value)
    assert "Existing Query ID case is incomplete" in message
    assert "Re-run analysis to regenerate required artifacts" in message
    assert str(case_path) not in message
    assert "case_dir" not in message


def test_web_rejects_collector_case_dir_outside_web_corpus(monkeypatch, tmp_path):
    module = load_web_module()
    monkeypatch.setenv("CM_TOKEN", "secret-token")
    config = tmp_path / "cm-config.json"
    config.write_text("{}", encoding="utf-8")
    outside_case_dir = tmp_path / "other-output" / "abc_def"
    settings = module.WebSettings(
        config=config,
        repo_dir=tmp_path,
        corpus_dir=tmp_path / "cm-corpus",
    )

    def fake_runner(cmd, **kwargs):
        outside_case_dir.mkdir(parents=True)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=f"Output case directory: {outside_case_dir}\n",
            stderr="",
        )

    with pytest.raises(module.WebError) as excinfo:
        module.run_web_analysis("abc:def", "admin", False, settings, runner=fake_runner)

    assert "outside the web corpus directory" in str(excinfo.value)
