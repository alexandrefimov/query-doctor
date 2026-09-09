"""Optional browser-level smoke tests for the local web UI."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from query_doctor.web.models import (
    WebClusterConfig,
    WebTrinoQueryAnalysisResult,
    WebTrinoRecentScanResult,
    WebTrinoRecentScanRow,
)
from web_server_test_support import REPO_DIR, load_web_module


E2E_QUERY_ID = "aaaaaaaaaaaaaaaa:0000000000000001"
TRINO_E2E_QUERY_ID = "20260603_120102_00001_abcde"
DRAFT_ELIGIBLE_E2E_SQL = """WITH base AS (
  SELECT user_id, bytes_sent
  FROM example_events.fact_events
), filtered AS (
  SELECT user_id, bytes_sent
  FROM base
)
SELECT user_id, bytes_sent
FROM filtered
WHERE bytes_sent > 0
"""
DRAFT_ELIGIBLE_E2E_FACTS = """# Query Doctor deterministic analysis facts

## Summary
- Cardinality anomalies: 2

## Findings

### Large intermediate or exchange traffic [high]

- TotalBytesSent is large relative to the configured threshold.
- join row expansion observed.
"""


def select_diagnosis_workflow(page, value: str) -> None:
    option = page.locator(f'label:has(input[name="diagnosis_workflow"][value="{value}"])')
    if not option.is_visible():
        page.locator("details[data-diagnosis-target-root] > summary").click()
    option.click()


def open_recent_results(page) -> None:
    if page.locator("#recent-results").get_attribute("open") is None:
        page.locator("#recent-results > summary").click()


def ensure_query_groups_visible(page) -> None:
    query_groups = page.locator(".batch-query-groups")
    assert query_groups.is_visible()
    assert query_groups.locator(".batch-filter-link", has_text="Stats to check").is_visible()


def open_result_filters(page) -> None:
    drawer = page.locator("details.batch-result-filter-drawer")
    if drawer.get_attribute("open") is None:
        drawer.locator("summary.batch-result-filter-drawer-summary").click()
    assert drawer.get_attribute("open") == ""


def assert_error_card_contains(
    page, *, title: str, reason: str, stage: str, next_step: str
) -> None:
    page.wait_for_selector(".error-card", timeout=5000)
    error_card = page.locator(".error-card").first
    assert error_card.get_by_text(title).is_visible()
    assert error_card.get_by_text(reason).is_visible()
    assert error_card.get_by_text(stage).is_visible()
    assert error_card.get_by_text("Next step:").is_visible()
    assert error_card.get_by_text(next_step).is_visible()


def synthetic_batch_summary(*, cases_root: Path | None = None) -> dict[str, object]:
    case_dir = str(cases_root / "case-001" / "bad-e2e") if cases_root is not None else None
    return {
        "selected_count": 4,
        "cases": [
            {
                "case_index": 1,
                "query_id": "bad:e2e",
                "user": "alice",
                "score": 31,
                "score_severity": "high",
                "duration_sec": 120,
                "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                "collection_status": "ok",
                "analysis_status": "ok",
                **({"case_dir": case_dir} if case_dir is not None else {}),
            },
            {
                "case_index": 2,
                "query_id": "suspicious:e2e",
                "user": "bob",
                "score": 12,
                "score_severity": "suspicious",
                "duration_sec": 80,
                "collection_status": "ok",
                "analysis_status": "ok",
            },
            {
                "case_index": 3,
                "query_id": "stats:e2e",
                "user": "carol",
                "score": 0,
                "score_severity": "clean",
                "duration_sec": 70,
                "score_reasons": ["spill/scratch evidence: non-zero metrics"],
                "collection_status": "ok",
                "analysis_status": "ok",
                "stats_optimization_candidate": {
                    "score": 72,
                    "tier": "high",
                    "confidence": "medium",
                    "impact": "high",
                    "need_type": "table_and_column_stats",
                    "speed_benefit": "medium",
                    "reasons": ["missing table stats before expensive join"],
                    "counter_signals": [],
                    "suggested_review_areas": ["table/partition row counts"],
                    "required_confirmation": ["compare EXPLAIN before and after stats collection"],
                },
            },
            {
                "case_index": 4,
                "query_id": "optimization:e2e",
                "user": "dave",
                "score": 0,
                "score_severity": "clean",
                "duration_sec": 95,
                "collection_status": "ok",
                "analysis_status": "ok",
                "query_optimization_candidate": {
                    "score": 55,
                    "tier": "medium",
                    "confidence": "medium",
                    "impact": "medium",
                    "reasons": ["join row expansion or cardinality mismatch with join evidence"],
                    "counter_signals": [],
                    "suggested_review_areas": ["join keys and join cardinality"],
                },
                "optimizer_rewrite_support": {
                    "status": "guidance_only",
                    "label": "Guidance only",
                    "reason": "Manual review only",
                    "rewriteability_bucket": "not_rewriteable",
                    "rewriteability_label": "Not rewriteable",
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
            },
        ],
    }


def write_summary(path: Path) -> Path:
    path.write_text(json.dumps(synthetic_batch_summary()), encoding="utf-8")
    return path


def write_action_summary(path: Path) -> tuple[Path, Path]:
    cases_root = path.parent / "cases"
    case_dir = cases_root / "case-001" / "bad-e2e"
    case_dir.mkdir(parents=True)
    (case_dir / "profile_digest.md").write_text("PROFILE\n", encoding="utf-8")
    (case_dir / "analysis_facts.md").write_text("FACTS\n", encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps({"statement": "SELECT a FROM db.source_table WHERE ds = 20260504"}),
        encoding="utf-8",
    )
    path.write_text(json.dumps(synthetic_batch_summary(cases_root=cases_root)), encoding="utf-8")
    return path, case_dir


def e2e_settings(
    tmp_path: Path,
    *,
    batch_summary: Path | None = None,
    no_llm: bool = False,
):
    module = load_web_module()
    config = tmp_path / "query-doctor-e2e-config.json"
    config.write_text("{}", encoding="utf-8")
    return module.WebSettings(
        config=config,
        repo_dir=REPO_DIR,
        corpus_dir=tmp_path / "query-corpus",
        batch_summary=batch_summary,
        clusters=(
            WebClusterConfig(
                key="cm",
                label="CM prod",
                cm_url="https://cm.example.invalid",
                cm_cluster="prod",
                cm_service="impala",
            ),
            WebClusterConfig(
                key="ambari",
                label="Ambari prod",
                query_profile_source="impala",
                impala_profile_hosts=("impalad.example.invalid",),
            ),
        ),
        active_cluster_key="cm",
        no_llm=no_llm,
    )


def e2e_trino_switch_settings(tmp_path: Path):
    module = load_web_module()
    config = tmp_path / "query-doctor-e2e-trino-config.json"
    config.write_text("{}", encoding="utf-8")
    return module.WebSettings(
        config=config,
        repo_dir=REPO_DIR,
        corpus_dir=tmp_path / "query-corpus",
        clusters=(
            WebClusterConfig(
                key="cm",
                label="CM prod",
                cm_url="https://cm.example.invalid",
                cm_cluster="prod",
                cm_service="impala",
            ),
            WebClusterConfig(
                key="trino",
                label="Trino beta",
                trino_beta_enabled=True,
                trino_coordinator_url="https://trino.example.invalid",
                trino_query_info_source_contract=tmp_path / "trino-query-info-contract.json",
                trino_query_list_source_contract=tmp_path / "trino-query-list-contract.json",
            ),
        ),
        active_cluster_key="cm",
        no_llm=True,
    )


def write_known_query_case(
    module, settings, query_id: str, *, optimizer_draft_eligible: bool = False
) -> Path:
    case_dir = module.expected_case_dir_for_query(query_id, settings)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "profile_digest.md").write_text(
        "User: e2e-user\nPool: e2e-pool\n", encoding="utf-8"
    )
    if optimizer_draft_eligible:
        facts_text = DRAFT_ELIGIBLE_E2E_FACTS
        statement = DRAFT_ELIGIBLE_E2E_SQL
        (case_dir / "original_query.sql").write_text(statement, encoding="utf-8")
    else:
        facts_text = "- Parsed operators: 4\n- Cardinality anomalies: 0\n- Memory anomalies: 2\n"
        statement = "SELECT a FROM db.source_table WHERE ds = 20260504"
    (case_dir / "analysis_facts.md").write_text(facts_text, encoding="utf-8")
    (case_dir / "cm_metadata.json").write_text(
        json.dumps(
            {
                "duration_sec": 42.0,
                "user": "e2e-user",
                "pool": "e2e-pool",
                "statement": statement,
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "collection_warnings.txt").write_text("", encoding="utf-8")
    return case_dir


def fake_batch_runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    out_dir = Path(cmd[cmd.index("--out") + 1])
    progress_path = Path(cmd[cmd.index("--progress-jsonl") + 1])
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(
        json.dumps({"stage": "discovery", "status": "done", "candidates_selected": 4}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "batch_summary.json").write_text(
        json.dumps(synthetic_batch_summary()),
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def fake_failing_batch_runner(
    cmd: list[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        cmd,
        2,
        stdout="SELECT secret_col FROM example_hidden.table WHERE token = 'raw-secret';",
        stderr="LEAKED_PROFILE_BODY with /private/tmp/query-doctor-sensitive-case",
    )


def fake_detail_action_runner(
    cmd: list[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    module = load_web_module()
    case_dir = next(
        (
            Path(value)
            for value in cmd
            if Path(value).is_dir() and (Path(value) / "analysis_facts.md").is_file()
        ),
        None,
    )
    if case_dir is None:
        return fake_batch_runner(cmd)

    if any("optimize_query" in value for value in cmd):
        from query_doctor.cli.optimize_query import (
            extract_optimizable_source_sql,
            read_source_sql,
        )

        source_sql = extract_optimizable_source_sql(read_source_sql(case_dir)).sql
        facts_text = (case_dir / "analysis_facts.md").read_text(encoding="utf-8")
        recommendations_text = "- Collect table and column statistics.\n"
        recommendations_path = case_dir / "optimized_query_recommendations.md"
        recommendations_path.write_text(recommendations_text, encoding="utf-8")
        (case_dir / "optimized_query.validated.json").write_text(
            json.dumps(
                {
                    "facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(),
                    "output_kind": "recommendations_only",
                    "recommendations": "optimized_query_recommendations.md",
                    "recommendations_sha256": module.file_sha256(recommendations_path),
                    "risk_mode": "recommendations_only",
                    "risk_reasons": ["too_many_ctes_for_safe_rewrite"],
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
    else:
        report_name = cmd[cmd.index("--out") + 1] if "--out" in cmd else module.PYTHON_REPORT_NAME
        report_variant = (
            module.REPORT_VARIANT_LLM
            if report_name == module.LLM_REPORT_NAME
            else module.REPORT_VARIANT_PYTHON
        )
        (case_dir / report_name).write_text(
            "# Validated report\n\nSafe E2E report body.\n", encoding="utf-8"
        )
        module.write_batch_case_report_validation_marker(case_dir, report_variant=report_variant)

    return subprocess.CompletedProcess(
        cmd, 0, stdout="raw stdout hidden", stderr="raw stderr hidden"
    )


def fake_trino_e2e_diagnosis() -> dict[str, object]:
    return {
        "schema_version": "trino_compact_diagnosis_v1",
        "support_status": "preview",
        "parser_coverage": "known",
        "lifecycle": "finished",
        "diagnostic_lane": {
            "evidence_readiness": "one_query_attention_ready",
            "source_granularity": "one_query_boundary",
            "verification_scope": "comparable_one_query_rerun",
            "supported_attention_area_count": 1,
        },
        "attention_areas": [
            {
                "id": "trino_spill_observed",
                "state": "supported",
                "summary": "Bounded compact facts show spill evidence.",
                "change_direction": "Review spill-heavy stages with the Trino operator.",
                "verification": "Compare a later bounded rerun against the same workload window.",
            }
        ],
        "limitations": [
            {
                "id": "no_metadata_collection",
                "state": "not_wired",
                "summary": "Trino Beta did not collect metadata.",
            }
        ],
        "diagnosis_boundary": {
            "root_cause": "not_claimed",
            "details_trusted_report_surface": "not_wired",
            "optimizer_behavior": "not_wired",
            "trino_sql_execution": "not_performed",
            "live_recent_scan": "retained_query_list_local_production",
        },
    }


def fake_trino_analysis(
    query_id: str, _report_mode: str, _redact_identifiers: bool, _settings: object
) -> WebTrinoQueryAnalysisResult:
    assert query_id == TRINO_E2E_QUERY_ID
    return WebTrinoQueryAnalysisResult(
        query_id=query_id,
        diagnosis=fake_trino_e2e_diagnosis(),
    )


def fake_trino_recent_scan(*_args: object, **_kwargs: object) -> WebTrinoRecentScanResult:
    return WebTrinoRecentScanResult(
        rows=(
            WebTrinoRecentScanRow(
                query_id=TRINO_E2E_QUERY_ID,
                status="diagnosed",
                lifecycle="finished",
                parser_coverage="known",
                supported_attention_area_count=1,
                attention_areas=("trino_spill_observed",),
            ),
        ),
        records_seen=1,
        records_selected=1,
        records_diagnosed=1,
        query_bound=50,
        cluster_key="trino",
    )


@contextmanager
def run_test_server(
    settings: object, *, runner=fake_batch_runner, analysis_func=None
) -> Iterator[str]:
    module = load_web_module()
    handler_kwargs = {"runner": runner}
    if analysis_func is not None:
        handler_kwargs["analysis_func"] = analysis_func
    handler = module.make_handler(settings, **handler_kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.fixture()
def page():
    sync_api = pytest.importorskip(
        "playwright.sync_api",
        reason=(
            "Install the optional E2E extra and Chromium browser: "
            "python -m pip install -e '.[e2e]' && python -m playwright install chromium"
        ),
    )
    try:
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as exc:  # noqa: BLE001 - optional local browser dependency.
                pytest.skip(f"Playwright Chromium is not available: {exc}")
            try:
                browser_page = browser.new_page()
                yield browser_page
            finally:
                browser.close()
    except RuntimeError as exc:
        pytest.skip(f"Playwright runtime is not available: {exc}")


def test_e2e_diagnose_controls_preserve_cluster_and_scan_target(tmp_path, page):
    with run_test_server(e2e_settings(tmp_path)) as base_url:
        page.goto(base_url)

        assert page.locator("#diagnosis_cluster_key").is_visible()
        page.locator("#diagnosis_cluster_key").select_option("ambari")
        batch_cluster = page.locator('#batch-form input[name="cluster_key"]')
        query_cluster = page.locator('#analyze-form input[name="cluster_key"]')
        assert batch_cluster.input_value() == "ambari"
        assert query_cluster.input_value() == "ambari"

        select_diagnosis_workflow(page, "query")
        assert page.locator("#analyze-form").is_visible()
        assert not page.locator("#batch-form").is_visible()
        assert query_cluster.get_attribute("value") == "ambari"

        select_diagnosis_workflow(page, "running")
        assert page.locator("#batch-form").get_attribute("action") == "/running/run"
        assert not page.locator("#scan_date").is_visible()
        assert not page.locator("#scan_hour").is_visible()
        assert page.locator(".batch-note--simple-running").count() == 0
        assert page.locator("summary", has_text="Advanced settings").count() == 0

        select_diagnosis_workflow(page, "finished")
        assert page.locator("#batch-form").get_attribute("action") == "/batch/run"
        assert not page.locator("#scan_date").is_visible()
        assert not page.locator("#scan_hour").is_visible()
        assert page.locator("#recent_window_minutes").is_visible()


def test_e2e_engine_switch_selects_trino_ready_source(tmp_path, page):
    with run_test_server(e2e_trino_switch_settings(tmp_path)) as base_url:
        page.goto(base_url)

        source_select = page.locator("#diagnosis_cluster_key")
        trino_choice = page.locator('input[name="engine_choice"][value="trino"]')
        assert source_select.input_value() == "cm"
        assert not trino_choice.is_disabled()
        assert page.locator(".engine-control").evaluate(
            """(engine) => Boolean(
                engine.compareDocumentPosition(
                    document.querySelector('[data-diagnosis-cluster-control]')
                ) & Node.DOCUMENT_POSITION_FOLLOWING
            )"""
        )
        assert source_select.evaluate(
            """(select) => Array.from(select.options).map((option) => ({
                value: option.value,
                hidden: option.hidden,
                disabled: option.disabled
            }))"""
        ) == [
            {"value": "cm", "hidden": False, "disabled": False},
            {"value": "trino", "hidden": True, "disabled": True},
        ]

        page.locator('label:has(input[name="engine_choice"][value="trino"])').click()

        assert source_select.input_value() == "trino"
        assert source_select.evaluate(
            """(select) => Array.from(select.options).map((option) => ({
                value: option.value,
                hidden: option.hidden,
                disabled: option.disabled
            }))"""
        ) == [
            {"value": "cm", "hidden": True, "disabled": True},
            {"value": "trino", "hidden": False, "disabled": False},
        ]
        assert trino_choice.is_checked()
        assert page.locator('#batch-form input[name="engine"]').input_value() == "trino"
        assert page.locator('#analyze-form input[name="engine"]').input_value() == "trino"
        assert page.locator('input[name="diagnosis_workflow"][value="running"]').is_disabled()

        page.locator('label:has(input[name="engine_choice"][value="impala"])').click()

        assert source_select.input_value() == "cm"
        assert source_select.evaluate(
            """(select) => Array.from(select.options).map((option) => ({
                value: option.value,
                hidden: option.hidden,
                disabled: option.disabled
            }))"""
        ) == [
            {"value": "cm", "hidden": False, "disabled": False},
            {"value": "trino", "hidden": True, "disabled": True},
        ]
        assert page.locator('#batch-form input[name="engine"]').input_value() == "impala"
        assert page.locator('#analyze-form input[name="engine"]').input_value() == "impala"

        page.locator('label:has(input[name="engine_choice"][value="trino"])').click()

        select_diagnosis_workflow(page, "query")

        assert page.locator("#analyze-form").is_visible()
        assert page.locator('label[for="query_id"]').inner_text() == "Trino Query ID"
        assert (
            page.locator("#query_id").get_attribute("placeholder") == "20260603_120102_00001_abcde"
        )
        assert page.locator('#analyze-form button[type="submit"]').inner_text() == "Run Trino Beta"


def test_e2e_trino_query_id_renders_beta_result(tmp_path, page):
    with run_test_server(
        e2e_trino_switch_settings(tmp_path), analysis_func=fake_trino_analysis
    ) as base_url:
        page.goto(base_url)

        page.locator('label:has(input[name="engine_choice"][value="trino"])').click()
        select_diagnosis_workflow(page, "query")
        page.locator("#query_id").fill(TRINO_E2E_QUERY_ID)
        page.locator('#analyze-form button[type="submit"]').click()

        page.wait_for_url("**/jobs/*")
        page.wait_for_selector('[aria-label="Trino Beta Query ID diagnosis"]', timeout=5000)

        body = page.locator("body")
        assert body.get_by_role("heading", name="Trino Beta Query ID diagnosis").is_visible()
        assert body.get_by_text("Beta boundary").is_visible()
        assert body.get_by_text("Trino spill observed").is_visible()
        blocked = page.locator('[aria-label="Trino Beta blocked surfaces"]')
        sql_execution = blocked.locator(".status-item", has_text="SQL execution")
        assert sql_execution.get_by_text("not performed").is_visible()
        assert page.locator('a[href^="/query/details/"]').count() == 0
        assert page.locator('a[href*="/optimized-query"]').count() == 0
        assert not body.get_by_text("Known Query ID analysis").is_visible()
        assert not body.get_by_text("Generating Python report").is_visible()
        assert not body.get_by_text("sensitive_table").is_visible()
        assert not body.get_by_text(str(tmp_path)).is_visible()


def test_e2e_trino_recent_result_opens_beta_query_id(
    tmp_path, page, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("query_doctor.web.batch_jobs.run_trino_recent_scan", fake_trino_recent_scan)
    with run_test_server(
        e2e_trino_switch_settings(tmp_path), analysis_func=fake_trino_analysis
    ) as base_url:
        page.goto(base_url)

        page.locator('label:has(input[name="engine_choice"][value="trino"])').click()
        select_diagnosis_workflow(page, "finished")
        page.locator("#batch-form button[type='submit']").click()

        page.wait_for_url("**/jobs/*")
        page.wait_for_selector('[aria-label="Trino Beta Recent diagnosis"]', timeout=5000)

        recent_result = page.locator('[aria-label="Trino Beta Recent diagnosis"]')
        assert recent_result.get_by_role("heading", name="Trino Beta Recent diagnosis").is_visible()
        assert recent_result.get_by_text(TRINO_E2E_QUERY_ID).is_visible()
        assert recent_result.get_by_text("Trino spill observed").is_visible()
        assert page.locator('a[href^="/batch/case/"]').count() == 0
        assert page.locator('a[href^="/query/details/"]').count() == 0
        assert page.locator('a[href*="/optimized-query"]').count() == 0
        assert not page.locator("body").get_by_text("sensitive_table").is_visible()
        assert not page.locator("body").get_by_text(str(tmp_path)).is_visible()

        recent_result.get_by_role("button", name="Open Trino Beta Query ID diagnosis").click()

        page.wait_for_url("**/jobs/*")
        page.wait_for_selector('[aria-label="Trino Beta Query ID diagnosis"]', timeout=5000)
        body = page.locator("body")
        assert body.get_by_role("heading", name="Trino Beta Query ID diagnosis").is_visible()
        assert body.get_by_text("Beta boundary").is_visible()
        assert body.get_by_text("Trino spill observed").is_visible()
        assert not body.get_by_text("Known Query ID analysis").is_visible()
        assert page.locator('a[href^="/query/details/"]').count() == 0
        assert page.locator('a[href*="/optimized-query"]').count() == 0


def test_e2e_optimizer_scope_guidance_is_secondary(tmp_path, page):
    with run_test_server(e2e_settings(tmp_path)) as base_url:
        page.goto(f"{base_url}/optimizer")

        sql_input = page.locator("#optimizer_sql")
        assert sql_input.is_visible()
        assert sql_input.evaluate("(node) => node.getBoundingClientRect().height") >= 220
        assert page.locator(".optimizer-panel .scope-line").count() == 0
        boundary_summary = page.locator(".optimizer-boundary-summary")
        assert boundary_summary.is_visible()
        assert boundary_summary.locator(".optimizer-boundary-item").count() == 3
        assert boundary_summary.get_by_text("Never executed or echoed after submit").is_visible()
        assert (
            page.locator(".optimizer-submit-row").get_by_role("button", name="Analyze").is_visible()
        )
        scope_details = page.locator(".optimizer-scope-details")
        metadata_scope = scope_details.locator("li", has_text="Metadata:")
        assert scope_details.is_visible()
        assert not metadata_scope.is_visible()

        scope_details.locator("summary").click()
        assert metadata_scope.is_visible()


def test_e2e_help_page_shortcuts_and_topics_are_interactive(tmp_path, page):
    with run_test_server(e2e_settings(tmp_path)) as base_url:
        page.goto(f"{base_url}/help")

        assert page.locator(".help-card-grid .help-card").count() == 5
        assert page.get_by_role("link", name="Workloads").is_visible()
        assert page.get_by_role("link", name="Trino compact").count() == 0
        assert page.locator("#workflows[open]").count() == 0
        assert page.locator("#workflows .help-topic-body").is_visible() is False
        assert page.locator("#safety .help-topic-body").is_visible() is False

        page.locator("#workflows > summary").click()
        assert page.locator("#workflows .help-topic-body").is_visible()

        page.locator("#safety > summary").click()

        assert page.locator("#safety .help-topic-body").is_visible()
        assert page.get_by_text("Browser UI intentionally hides").is_visible()


def test_e2e_result_filters_preserve_group_and_open_details(tmp_path, page):
    summary_path = write_summary(tmp_path / "batch_summary.json")
    with run_test_server(e2e_settings(tmp_path, batch_summary=summary_path)) as base_url:
        page.goto(base_url)

        select_diagnosis_workflow(page, "query")
        assert page.locator("#recent-results .batch-head h1").inner_text() == (
            "Previous Recent Results"
        )
        assert page.locator("#recent-results").get_attribute("open") is None
        assert not page.locator("#recent-results .batch-table-wrap").is_visible()

        select_diagnosis_workflow(page, "finished")
        assert page.locator("#recent-results .batch-head h1").inner_text() == "Finished Queries"
        assert page.locator("#recent-results").get_attribute("open") == ""

        select_diagnosis_workflow(page, "running")
        assert page.locator("#recent-results .batch-head h1").inner_text() == (
            "Previous Finished Queries"
        )
        select_diagnosis_workflow(page, "finished")
        assert page.locator("#recent-results .batch-head h1").inner_text() == "Finished Queries"

        open_recent_results(page)
        ensure_query_groups_visible(page)
        page.locator('a.batch-filter-link[href="/?query_group=stats#recent-results"]').click()
        page.wait_for_url("**/?query_group=stats#recent-results")
        active_filter = page.locator(
            ".batch-filter-link--active",
            has_text="Stats to check",
        )
        assert active_filter.is_visible()
        assert page.locator("tr", has_text="stats:e2e").is_visible()

        open_result_filters(page)
        spill_toggle = page.locator('a.batch-spill-toggle[aria-label="Only queries with spills"]')
        spill_toggle.click()
        page.wait_for_url("**/?query_group=stats&only_with_spills=on#recent-results")
        assert "batch-spill-toggle--active" in spill_toggle.get_attribute("class")
        assert active_filter.is_visible()
        assert page.locator("tr", has_text="stats:e2e").is_visible()
        assert not page.locator("tr", has_text="bad:e2e").is_visible()

        page.locator('tr[data-href="/batch/case/case-003"]').click()
        page.wait_for_url("**/batch/case/case-003")
        assert page.url.endswith("/batch/case/case-003")


def test_e2e_batch_detail_renders_owner_coordinate_action_card(tmp_path, page):
    summary_path = write_summary(tmp_path / "batch_summary.json")
    with run_test_server(e2e_settings(tmp_path, batch_summary=summary_path)) as base_url:
        page.goto(f"{base_url}/batch/case/case-004")

        action_plan = page.locator("#action-plan")
        assert action_plan.get_by_role("heading", name="Recommended change").is_visible()
        assert action_plan.locator(
            ".action-candidate-section--locations > span",
            has_text="Where to inspect",
        ).is_visible()
        safe_review_locations = action_plan.locator('[aria-label="Safe review locations"]')
        assert safe_review_locations.get_by_text(
            "SQL: final SELECT filter (line 18): predicate near final SELECT", exact=True
        ).is_visible()
        assert safe_review_locations.get_by_text(
            "Plan: estimate-mismatch operator: node 02 HASH JOIN (inner join, partitioned)",
            exact=True,
        ).is_visible()
        assert action_plan.get_by_text(
            "Try to reduce rows earlier: move the final SELECT filter closer"
        ).is_visible()
        assert action_plan.get_by_text("Compare EXPLAIN before and after the change").is_visible()
        assert not action_plan.get_by_text("Review first:").is_visible()
        assert not action_plan.get_by_text("optimization:e2e").is_visible()


def test_e2e_running_scan_submit_renders_synthetic_job_result(tmp_path, page):
    with run_test_server(e2e_settings(tmp_path)) as base_url:
        page.goto(base_url)

        assert page.locator("#diagnosis_cluster_key").is_visible()
        page.locator("#diagnosis_cluster_key").select_option("ambari")
        select_diagnosis_workflow(page, "running")
        page.locator("#batch-form button[type='submit']").click()

        page.wait_for_url("**/jobs/*")
        page.wait_for_selector("#recent-results", timeout=5000)
        assert (
            page.locator("#job-result-slot")
            .get_by_role("heading", name="Running Queries")
            .is_visible()
        )
        assert page.locator("#job-result-slot").locator("tr", has_text="bad:e2e").is_visible()


def test_e2e_recent_scan_failure_hides_subprocess_output(tmp_path, page):
    with run_test_server(e2e_settings(tmp_path), runner=fake_failing_batch_runner) as base_url:
        page.goto(base_url)

        page.locator("#batch-form button[type='submit']").click()

        page.wait_for_url("**/jobs/*")
        error_slot = page.locator("#job-error-slot")
        page.wait_for_selector(
            "#job-error-slot >> text=Query Doctor recent scan failed", timeout=5000
        )

        assert error_slot.get_by_text("exit code 2").is_visible()
        assert error_slot.get_by_text("Reason").is_visible()
        assert error_slot.get_by_text("impala.recent_scan_failed").is_visible()
        assert error_slot.get_by_text("Stage").is_visible()
        assert "Query Doctor recent scan" in error_slot.inner_text()
        assert error_slot.get_by_text("Next step:").is_visible()
        assert error_slot.get_by_text("Review the selected local configuration").is_visible()
        assert error_slot.locator(
            "li", has_text="Captured subprocess output is not shown"
        ).is_visible()
        body = page.locator("body")
        assert not body.get_by_text("SELECT secret_col").is_visible()
        assert not body.get_by_text("raw-secret").is_visible()
        assert not body.get_by_text("LEAKED_PROFILE_BODY").is_visible()
        assert not body.get_by_text("/private/tmp/query-doctor-sensitive-case").is_visible()


def test_e2e_form_errors_render_structured_safe_details(tmp_path, page):
    with run_test_server(e2e_settings(tmp_path)) as base_url:
        page.goto(base_url)
        select_diagnosis_workflow(page, "query")
        page.locator("#analyze-form").evaluate("(form) => form.submit()")

        assert_error_card_contains(
            page,
            title="Query ID is missing",
            reason="web.query_id_required",
            stage="Checking Query ID form",
            next_step="Paste one explicit Query ID",
        )

        page.goto(f"{base_url}/optimizer")
        page.locator(".optimizer-form").evaluate("(form) => form.submit()")

        assert_error_card_contains(
            page,
            title="Optimizer SQL is missing",
            reason="web.optimizer_sql_required",
            stage="Checking Query Optimizer input",
            next_step="Paste one read-only SELECT or WITH statement",
        )

        page.goto(base_url)
        page.locator("#recent_window_minutes").evaluate("(node) => { node.value = '0'; }")
        page.locator("#batch-form").evaluate("(form) => form.submit()")

        assert_error_card_contains(
            page,
            title="Form input was rejected",
            reason="web.form_positive_integer_required",
            stage="Checking form field recent_window_minutes",
            next_step="Correct the highlighted form value and retry.",
        )

        body = page.locator("body")
        assert not body.get_by_text("SELECT secret_col").is_visible()
        assert not body.get_by_text("/private/tmp").is_visible()


def test_e2e_known_query_preserves_selected_cluster_after_submit(tmp_path, page):
    module = load_web_module()
    query_id = E2E_QUERY_ID
    calls = []

    def fake_analysis(query_id_arg, report_mode, redact_identifiers, settings):
        write_known_query_case(module, settings, query_id_arg)
        calls.append(
            {
                "query_id": query_id_arg,
                "report_mode": report_mode,
                "redact_identifiers": redact_identifiers,
                "active_cluster_key": settings.active_cluster_key,
                "query_profile_source": settings.query_profile_source,
                "impala_profile_hosts": settings.impala_profile_hosts,
            }
        )
        return module.WebQueryAnalysisResult(
            query_id=query_id_arg,
            case={
                "query_id": query_id_arg,
                "score": 9,
                "score_severity": "suspicious",
                "duration_sec": 42.0,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 2"],
                "memory_anomaly_count": 2,
            },
        )

    with run_test_server(e2e_settings(tmp_path), analysis_func=fake_analysis) as base_url:
        page.goto(base_url)

        assert page.locator("#diagnosis_cluster_key").is_visible()
        page.locator("#diagnosis_cluster_key").select_option("ambari")
        select_diagnosis_workflow(page, "query")
        page.locator("#query_id").fill(query_id)
        page.locator("#analyze-form button[type='submit']").click()

        page.wait_for_url("**/jobs/*")
        result_slot = page.locator("#job-result-slot")
        page.wait_for_selector("#job-result-slot >> text=Known Query ID analysis", timeout=5000)

        assert page.locator("#diagnosis_cluster_key").input_value() == "ambari"
        assert (
            page.locator('#analyze-form input[name="cluster_key"]').get_attribute("value")
            == "ambari"
        )
        assert page.locator("#query_id").input_value() == ""
        assert result_slot.get_by_role("heading", name="Known Query ID analysis").is_visible()
        assert result_slot.locator("tr", has_text=query_id).is_visible()
        assert result_slot.get_by_text("memory 2").is_visible()
        assert not page.locator("body").get_by_text("raw stdout hidden").is_visible()
        assert not page.locator("body").get_by_text("case_dir").is_visible()

    assert calls == [
        {
            "query_id": query_id,
            "report_mode": "analysis",
            "redact_identifiers": True,
            "active_cluster_key": "ambari",
            "query_profile_source": "impala",
            "impala_profile_hosts": ("impalad.example.invalid",),
        }
    ]


def test_e2e_known_query_result_opens_details_without_auto_llm_actions(tmp_path, page):
    module = load_web_module()
    query_id = E2E_QUERY_ID

    def fake_analysis(query_id_arg, _report_mode, _redact_identifiers, settings):
        write_known_query_case(module, settings, query_id_arg)
        return module.WebQueryAnalysisResult(
            query_id=query_id_arg,
            case={
                "query_id": query_id_arg,
                "score": 9,
                "score_severity": "suspicious",
                "duration_sec": 42.0,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 2"],
                "memory_anomaly_count": 2,
            },
        )

    with run_test_server(e2e_settings(tmp_path), analysis_func=fake_analysis) as base_url:
        page.goto(base_url)

        assert page.locator("#diagnosis_cluster_key").is_visible()
        page.locator("#diagnosis_cluster_key").select_option("ambari")
        select_diagnosis_workflow(page, "query")
        page.locator("#query_id").fill(query_id)
        page.locator("#analyze-form button[type='submit']").click()

        page.wait_for_selector("#job-result-slot >> text=Known Query ID analysis", timeout=5000)
        page.locator("#job-result-slot").locator("tr", has_text=query_id).click()
        page.wait_for_url("**/query/details/*")
        detail_page = page
        detail_page.wait_for_load_state("domcontentloaded")

        assert "/query/details/" in detail_page.url
        assert detail_page.get_by_role("heading", name="Known Query ID details").is_visible()
        actions = detail_page.locator("#case-actions")
        assert actions.get_by_role("heading", name="Reports and optimizer").is_visible()
        assert actions.get_by_role("button", name="Generate Python report", exact=True).is_visible()
        assert actions.get_by_role("button", name="Generate LLM narrative").is_visible()
        assert not actions.get_by_role("button", name="Run Query LLM optimizer").is_visible()
        assert not actions.get_by_role(
            "button", name="Generate Python report + optimizer"
        ).is_visible()
        assert actions.get_by_text("Query LLM optimizer").is_visible()
        assert actions.get_by_text("not eligible for an optimizer job").is_visible()
        assert not actions.locator(".report-progress").is_visible()
        assert not actions.get_by_text("Open full report").is_visible()
        assert not actions.get_by_text("Open Query LLM optimizer").is_visible()
        assert not detail_page.locator("body").get_by_text("case_dir").is_visible()
        assert not detail_page.locator("body").get_by_text(str(tmp_path)).is_visible()
        detail_page.close()


def test_e2e_detail_report_action_renders_trusted_result(tmp_path, page):
    summary_path, case_dir = write_action_summary(tmp_path / "batch_summary.json")
    settings = e2e_settings(tmp_path, batch_summary=summary_path)
    with run_test_server(settings, runner=fake_detail_action_runner) as base_url:
        page.goto(f"{base_url}/batch/case/case-001")

        actions = page.locator("#case-actions")
        assert actions.locator("strong", has_text="Python Report").is_visible()
        assert actions.locator("strong", has_text="LLM narrative").is_visible()
        actions.get_by_role("button", name="Generate Python report", exact=True).click()
        page.wait_for_url("**/jobs/*#case-actions")
        page.wait_for_selector("text=Open full report", timeout=5000)

        body = page.locator("body")
        body.locator("summary", has_text="Python Report body").click()
        assert body.get_by_text("Safe E2E report body.").is_visible()
        assert body.locator('[aria-label="Python report result"]').is_visible()
        assert not body.get_by_text("raw stdout hidden").is_visible()
        assert not body.get_by_text(str(case_dir)).is_visible()


def test_e2e_detail_optimizer_action_renders_trusted_recommendations(tmp_path, page):
    summary_path, case_dir = write_action_summary(tmp_path / "batch_summary.json")
    settings = e2e_settings(tmp_path, batch_summary=summary_path)
    with run_test_server(settings, runner=fake_detail_action_runner) as base_url:
        page.goto(f"{base_url}/batch/case/case-001")

        page.locator("#case-actions").get_by_role("button", name="Run Query LLM optimizer").click()
        page.wait_for_url("**/jobs/*#case-actions")
        open_link = page.get_by_role("link", name="Open Query LLM optimizer recommendations")
        open_link.wait_for(timeout=5000)
        assert open_link.get_attribute("href") == "#query-optimizer-result"
        open_link.click()
        page.wait_for_url("**/jobs/*#query-optimizer-result")

        body = page.locator("body")
        optimizer_result = body.locator('[aria-label="Query LLM optimizer result"]')
        assert optimizer_result.locator(
            "summary", has_text="Query LLM optimizer recommendations"
        ).is_visible()
        optimizer_result.locator("summary", has_text="Query LLM optimizer recommendations").click()
        assert optimizer_result.get_by_text("Collect table and column statistics.").is_visible()
        assert optimizer_result.locator("strong", has_text="Recommendations only").is_visible()
        assert not body.get_by_text("SELECT a FROM db.source_table").is_visible()
        assert not body.get_by_text("raw stderr hidden").is_visible()
        assert not body.get_by_text(str(case_dir)).is_visible()


def test_e2e_no_llm_detail_actions_use_case_actions_and_python_labels(tmp_path, page):
    summary_path, case_dir = write_action_summary(tmp_path / "batch_summary.json")
    settings = e2e_settings(tmp_path, batch_summary=summary_path, no_llm=True)
    calls: list[list[str]] = []

    def tracking_runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return fake_detail_action_runner(cmd, **kwargs)

    with run_test_server(settings, runner=tracking_runner) as base_url:
        page.goto(f"{base_url}/batch/case/case-001")

        actions = page.locator("#case-actions")
        assert actions.get_by_role("heading", name="Reports and optimizer").is_visible()
        assert actions.locator("strong", has_text="Python Report").is_visible()
        assert actions.locator("strong", has_text="Query optimizer").is_visible()
        assert actions.get_by_role("button", name="Generate Python report", exact=True).is_visible()
        assert actions.get_by_role("button", name="Run Query optimizer").is_visible()
        assert actions.get_by_role("button", name="Generate Python report + optimizer").is_visible()
        assert not page.locator("#llm-actions").is_visible()
        assert not page.locator("body").get_by_text("Generate LLM narrative").is_visible()
        assert not page.locator("body").get_by_text("Query LLM optimizer").is_visible()

        actions.get_by_role("button", name="Generate Python report", exact=True).click()
        page.wait_for_url("**/jobs/*#case-actions")
        page.wait_for_selector("text=Open full report", timeout=5000)

        body = page.locator("body")
        body.locator("summary", has_text="Python Report body").click()
        assert body.get_by_text("Safe E2E report body.").is_visible()
        assert body.locator('[aria-label="Python report result"]').is_visible()
        assert not body.get_by_text("raw stdout hidden").is_visible()
        assert not body.get_by_text("raw stderr hidden").is_visible()
        assert not body.get_by_text(str(case_dir)).is_visible()

    assert calls
    assert "--no-llm" in calls[0]


def test_e2e_no_llm_combined_action_renders_report_and_optimizer(tmp_path, page):
    summary_path, case_dir = write_action_summary(tmp_path / "batch_summary.json")
    settings = e2e_settings(tmp_path, batch_summary=summary_path, no_llm=True)
    calls: list[list[str]] = []

    def tracking_runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return fake_detail_action_runner(cmd, **kwargs)

    with run_test_server(settings, runner=tracking_runner) as base_url:
        page.goto(f"{base_url}/batch/case/case-001")

        page.locator("#case-actions").get_by_role(
            "button", name="Generate Python report + optimizer"
        ).click()
        page.wait_for_url("**/jobs/*#case-actions")
        page.wait_for_selector("text=Open full report", timeout=5000)
        open_link = page.get_by_role("link", name="Open Query optimizer recommendations")
        open_link.wait_for(timeout=5000)
        assert open_link.get_attribute("href") == "#query-optimizer-result"
        open_link.click()
        page.wait_for_url("**/jobs/*#query-optimizer-result")

        body = page.locator("body")
        body.locator("summary", has_text="Python Report body").click()
        assert body.get_by_text("Safe E2E report body.").is_visible()
        assert body.locator('[aria-label="Python report result"]').is_visible()
        optimizer_result = body.locator('[aria-label="Query optimizer result"]')
        assert optimizer_result.locator(
            "summary", has_text="Query optimizer recommendations"
        ).is_visible()
        optimizer_result.locator("summary", has_text="Query optimizer recommendations").click()
        assert optimizer_result.get_by_text("Collect table and column statistics.").is_visible()
        assert optimizer_result.locator("strong", has_text="Recommendations only").is_visible()
        assert not body.get_by_text("SELECT a FROM db.source_table").is_visible()
        assert not body.get_by_text("raw stdout hidden").is_visible()
        assert not body.get_by_text("raw stderr hidden").is_visible()
        assert not body.get_by_text(str(case_dir)).is_visible()
        assert not page.locator("#llm-actions").is_visible()

    assert len(calls) == 2
    assert all("--no-llm" in call for call in calls)


def test_e2e_known_query_no_llm_combined_action_renders_python_outputs(tmp_path, page):
    module = load_web_module()
    query_id = E2E_QUERY_ID
    settings = e2e_settings(tmp_path, no_llm=True)
    calls: list[list[str]] = []

    def fake_analysis(query_id_arg, _report_mode, _redact_identifiers, analysis_settings):
        write_known_query_case(
            module,
            analysis_settings,
            query_id_arg,
            optimizer_draft_eligible=True,
        )
        return module.WebQueryAnalysisResult(
            query_id=query_id_arg,
            case={
                "query_id": query_id_arg,
                "score": 9,
                "score_severity": "suspicious",
                "duration_sec": 42.0,
                "collection_status": "ok",
                "analysis_status": "ok",
                "metadata_status": "skipped",
                "table_stats_status": "not_checked",
                "score_reasons": ["memory estimate anomalies: 2"],
                "memory_anomaly_count": 2,
            },
        )

    def tracking_runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return fake_detail_action_runner(cmd, **kwargs)

    with run_test_server(
        settings,
        runner=tracking_runner,
        analysis_func=fake_analysis,
    ) as base_url:
        page.goto(base_url)

        select_diagnosis_workflow(page, "query")
        page.locator("#query_id").fill(query_id)
        page.locator("#analyze-form button[type='submit']").click()

        page.wait_for_selector("#job-result-slot >> text=Known Query ID analysis", timeout=5000)
        page.locator("#job-result-slot").locator("tr", has_text=query_id).click()
        page.wait_for_url("**/query/details/*")
        detail_page = page
        detail_page.wait_for_load_state("domcontentloaded")

        actions = detail_page.locator("#case-actions")
        assert actions.get_by_role("heading", name="Reports and optimizer").is_visible()
        assert actions.get_by_role("button", name="Generate Python report + optimizer").is_visible()
        assert not detail_page.locator("#llm-actions").is_visible()
        assert not detail_page.locator("body").get_by_text("Query LLM optimizer").is_visible()

        actions.get_by_role("button", name="Generate Python report + optimizer").click()
        detail_page.wait_for_url("**/jobs/*#case-actions")
        detail_page.wait_for_selector("text=Open full report", timeout=5000)
        open_link = detail_page.get_by_role("link", name="Open Query optimizer recommendations")
        open_link.wait_for(timeout=5000)
        assert open_link.get_attribute("href") == "#query-optimizer-result"
        open_link.click()
        detail_page.wait_for_url("**/jobs/*#query-optimizer-result")

        body = detail_page.locator("body")
        body.locator("summary", has_text="Python Report body").click()
        assert body.get_by_text("Safe E2E report body.").is_visible()
        assert body.locator('[aria-label="Python report result"]').is_visible()
        optimizer_result = body.locator('[aria-label="Query optimizer result"]')
        assert optimizer_result.locator(
            "summary", has_text="Query optimizer recommendations"
        ).is_visible()
        optimizer_result.locator("summary", has_text="Query optimizer recommendations").click()
        assert optimizer_result.get_by_text("Collect table and column statistics.").is_visible()
        assert not body.get_by_text("SELECT a FROM db.source_table").is_visible()
        assert not body.get_by_text("raw stdout hidden").is_visible()
        assert not body.get_by_text("raw stderr hidden").is_visible()
        assert not body.get_by_text(str(tmp_path)).is_visible()
        assert not detail_page.locator("#llm-actions").is_visible()
        detail_page.close()

    assert len(calls) == 2
    assert all("--no-llm" in call for call in calls)
