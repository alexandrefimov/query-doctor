from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "trino-beta-ui-readiness.md"


def test_trino_beta_ui_readiness_doc_pins_showable_surface_and_prerequisites() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "local production Trino\nweb surface" in text
    assert "not broad Trino\nproduction engine support" in text
    assert "Trino Beta" in text
    assert "One Query ID" in text
    assert "`trino_support_mode=beta` or `trino_support_mode=production`" in text
    assert "Legacy `trino_beta_enabled=true` maps only to beta mode" in text
    assert "`trino_coordinator_url`" in text
    assert "`trino_query_info_source_contract`" in text
    assert "`trino_query_list_source_contract`" in text
    assert "bounded retained pruned coordinator\nquery-list read" in text
    assert "one bounded pruned\ncoordinator QueryInfo read" in text
    normalized_text = " ".join(text.split())
    assert (
        "Those results may link to raw-free Trino Details only after "
        "server-owned case materialization." in normalized_text
    )
    assert (
        "Deterministic Python Report and optimizer guidance are available "
        "from those materialized Details cases." in normalized_text
    )


def test_trino_beta_ui_readiness_doc_pins_blocked_surfaces() -> None:
    text = DOC.read_text(encoding="utf-8")

    for fragment in (
        "Running scans",
        "query-history crawling",
        "metadata collection",
        "Details before case materialization",
        "Python Report before case materialization",
        "LLM reports",
        "Query Optimizer jobs",
        "Query Doctor-generated Trino SQL",
        "SQL execution",
        "broader/shared production Trino support",
    ):
        assert fragment in text
    assert (
        "Recent must be disabled unless the\n  selected source has the query-list source contract"
        in text
    )


def test_trino_beta_ui_readiness_doc_lists_release_gates() -> None:
    text = DOC.read_text(encoding="utf-8")

    for command in (
        "python3 scripts/audit_trino_beta_release_readiness.py --config <ignored-local-web-config.json> --selected-query-limit 1",
        "python3 scripts/audit_trino_shared_deployment_preflight.py --config <ignored-local-web-config.json>",
        "python3 scripts/audit_trino_shared_deployment_boundary.py --config <ignored-local-web-config.json>",
        "python3 scripts/audit_trino_web_beta_readiness.py --require-query-id --require-recent",
        "python3 scripts/audit_trino_web_beta_live_smoke.py --config <ignored-local-web-config.json> --selected-query-limit 1",
        "scripts/query-doctor-web-trino-beta-smoke --config <ignored-local-web-config.json> --limit 1",
        "python3 -m pytest -q tests/test_web_trino_beta_query.py",
        "python3 -m pytest -q tests/test_web_ui_help.py tests/test_web_ui_home.py",
        "python3 scripts/audit_trino_product_surface_boundary.py --registry-only",
        "python3 scripts/audit_trino_support_gap_matrix.py",
        "tests/*trino*.py tests/test_engine_capabilities.py",
    ):
        assert command in text
    assert "shared deployment preflight is a dev-only/static hardening wrapper" in text
    assert "product-surface audit, support-gap audit, and active-docs\ncheck" in text
    assert "live smoke, UI smoke, metadata\ncollection, or SQL execution" in text
    assert "performs no coordinator network read, live smoke, UI smoke" in text
    assert "trusted front-door viewer\nidentity for shared/non-local Trino web deployment" in text
    assert "raw-source reveal\nto stay isolated and disabled for shared Trino" in text
    assert "--trusted-front-door-reviewed" in text
    assert (
        "strips inbound viewer headers and sets\nexactly one normalized simple viewer value" in text
    )
    assert "optional `trino_metadata_cli_summary_smoke`" in text
    assert "--metadata-smoke-redaction-reviewed" in text
    assert (
        "does not add product metadata\ncollection or any browser/report/optimizer surface" in text
    )
    assert "performs the bounded Trino Recent path and\nselected QueryInfo diagnosis" in text
    assert "submits One Query ID using one\nselected retained Query ID without printing it" in text
    assert "--static-only` mode runs only static audits and focused tests" in text
    normalized_text = " ".join(text.split())
    assert "HTML may link only to materialized Trino Details." in normalized_text
    assert (
        "It must not expose Impala Details routes, direct report/optimizer job actions, "
        "LLM reports, generated SQL, or SQL execution." in normalized_text
    )
    assert "does not require a README screenshot refresh by itself" in text
