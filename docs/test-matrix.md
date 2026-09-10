# Test Matrix

Last updated: 2026-09-10

Use this matrix after `python3 scripts/agent_preflight.py --paths
<planned-paths>` to choose focused validation. It owns test selection, not
feature runbooks: long live-system, retained-evidence, image-build, and release
sequences stay in the canonical documents linked below. Run those sequences
only when the task and environment explicitly require them.

Always run `git diff --check` before committing. Before public sharing or
release cleanup, also run `pre-commit run --all-files`.

`tests/test_web_e2e.py` drives a real browser, and without one its 18 tests
skip rather than fail. A local run that reports "4796 passed, 19 skipped" has
therefore checked no rendered page at all, while CI checks them in its own
Chromium job — so a green local suite can still fail there on layout. Install
them with `python -m pip install -e '.[e2e]' && python -m playwright install
chromium`, which is what the skip reason itself says. Read skip reasons with
`-rs` rather than trusting the count.

## Quick Selection

| Touched area | Read first | Focused validation |
| --- | --- | --- |
| `docs/**` or other committed Markdown | Changed doc, [documentation index](README.md), [public boundary](public-documentation-boundary.md) | `python3 scripts/check_active_docs.py`; `python3 scripts/audit_public_docs.py`; `python3 scripts/check_markdown_links.py`; `python3 scripts/check_staged_public_safety.py --changed`; `git diff --check` |
| Active docs routing or baseline | [Codex handoff](codex-handoff.md), [public boundary](public-documentation-boundary.md), [code map](code-map.md) | Run all documentation checks above. |
| Agent operating docs | [agent quickstart](agent-quickstart.md), [agent playbook](agent-playbook.md), [public boundary](public-documentation-boundary.md) | `python3 -m pytest -q tests/test_agent_preflight.py tests/test_check_active_docs.py tests/test_check_markdown_links.py tests/test_check_staged_public_safety.py tests/test_audit_public_docs.py`; run all documentation checks above. |
| `query_doctor/web/ui/**` | [safety contract](safety-contract.md), [code audit](code-audit.md) | `python3 -m pytest -q tests/test_web_ui_home.py tests/test_web_ui_help.py tests/test_web_ui_readme.py tests/test_web_server.py tests/test_web_e2e.py` (see the browser note below) |
| Web routes or jobs | [Codex handoff](codex-handoff.md), [code audit](code-audit.md) | `python3 -m pytest -q tests/test_web_server.py tests/test_web_optimizer.py` |
| Container or Kubernetes packaging | [Kubernetes deployment](../deploy/kubernetes/README.md), [Helm chart](../deploy/helm/query-doctor/README.md), [release checklist](release-checklist.md) | `python3 -m pytest -q tests/test_kubernetes_packaging.py tests/test_deployment_readiness.py tests/test_web_app.py::test_health_probe_routes_are_raw_free_json`; `bash -n scripts/kubernetes-kerberos-renewer-smoke.sh scripts/kubernetes-online-history-smoke.sh`; `kubeconform -strict -summary deploy/kubernetes/public-demo.yaml deploy/kubernetes/configured-web.yaml deploy/kubernetes/self-test-job.yaml`; `scripts/helm-chart-smoke.sh` |
| Recent history operator path | [recent history store](recent-history-store.md), [release checklist](release-checklist.md) | `python3 -m pytest -q tests/test_recent_history_operator_readiness.py tests/test_recent_history_postgres_readiness.py tests/test_recent_profile_worker.py tests/test_recent_history_retention_cli.py tests/test_online_history_maintenance_smoke.py` |
| Kubernetes configured auth front door | [auth front-door contract](kubernetes-auth-front-door.md), [owner-raw deployment](owner-raw-d3-deployment.md), [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_kubernetes_auth_front_door.py tests/test_kubernetes_auth_front_door_smoke.py tests/test_kubernetes_packaging.py` |
| Browser safety text | [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_web_display_safety.py tests/test_web_server.py` |
| Owner-raw D3 viewer identity, front-door contract, dev SSO harness, or isolated raw source surface | [owner-raw deployment](owner-raw-d3-deployment.md), [dev SSO harness](dev-sso-keycloak.md), [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_*owner_raw*.py tests/test_viewer_identity.py tests/test_web_app.py tests/test_web_server.py -k "owner_raw or viewer_identity_header"`; `python3 -m pytest -q tests/test_dev_sso_keycloak*.py`; for UI/error wording, also run `python3 -m pytest -q tests/test_web_server.py tests/test_web_ui_home.py tests/test_web_ui_help.py`; run changed public-safety and public-doc checks. |
| Owner-raw trusted SSO/auth proxy support wording | [support-readiness gate](owner-raw-d3-deployment.md#ssoauth-proxy-support-readiness-gate), [dev SSO handoff](dev-sso-keycloak.md#production-handoff) | `python3 -m pytest -q tests/test_audit_owner_raw_sso_proxy_support_readiness.py tests/test_prepare_owner_raw_d3_artifacts.py`; run changed public-safety and public-doc checks. |
| Trusted artifacts | [code audit](code-audit.md), [optimizer contract](query-optimizer-contract.md) | `python3 -m pytest -q tests/test_web_trusted_artifacts.py tests/test_web_optimizer.py` |
| `query_doctor/report/**` | [safety contract](safety-contract.md), [code audit](code-audit.md) | `python3 -m pytest -q tests/test_report_sanitizer.py tests/test_web_ui_report.py` |
| Optimizer parser or validator | [optimizer contract](query-optimizer-contract.md) | `python3 -m pytest -q tests/test_query_optimizer.py tests/test_optimizer_sql.py` |
| Optimizer recipes or fixtures | [optimizer contract](query-optimizer-contract.md), [model bakeoff](model-bakeoff.md) | `python3 -m pytest -q tests/test_optimizer_sql.py tests/test_optimizer_benchmark_fixtures.py tests/test_audit_optimizer_funnel.py` |
| Pasted-SQL optimizer page | [optimizer contract](query-optimizer-contract.md), [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_web_optimizer.py tests/test_query_optimizer.py` |
| `query_doctor/cm/**` | [safety contract](safety-contract.md), [Codex handoff](codex-handoff.md) | `python3 -m pytest -q tests/test_cm_*` |
| Cloudera Manager metrics or events | [Codex handoff](codex-handoff.md), [code audit](code-audit.md) | `python3 -m pytest -q tests/test_cm_* tests/test_analyzer_*` |
| `query_doctor/impala/**` | [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_impala_* tests/test_metadata_*` |
| Analyzer facts or scoring | [analyzer audit](analyzer-audit.md), [code audit](code-audit.md) | `python3 -m pytest -q tests/test_stats_optimization_score.py tests/test_query_optimization_score.py tests/test_analyzer_cli.py tests/test_batch_recent_cli.py tests/test_web_ui_recent_scan.py tests/test_web_ui_recent_scan_presenter.py` |
| Trino compact intake or local production web lanes | [support matrix](engine-support-gap-matrix.md), [Trino diagnostic contract](engines/trino-diagnostic-contract.md), [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_*trino*.py tests/test_engine_fact_boundary_payload.py tests/test_engine_fact_promotion_policy.py tests/test_web_display_safety.py tests/test_engine_capabilities.py` |
| Spark compact intake or diagnosis | [support matrix](engine-support-gap-matrix.md), [Spark evidence checklist](engines/spark-test-cluster-evidence-checklist.md), [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_*spark*.py tests/test_engine_fact_contract.py tests/test_engine_fact_consumer_probe.py tests/test_engine_capabilities.py tests/test_cli_commands.py tests/test_installed_cli_contract.py` |
| Batch or Recent scan | [Codex handoff](codex-handoff.md), [code audit](code-audit.md), [representative Impala gates](local-smoke.md#representative-impala-audit-gates) | `python3 -m pytest -q tests/test_batch_recent_cli.py tests/test_recent_profile_worker.py tests/test_web_ui_recent_scan.py tests/test_web_ui_recent_scan_presenter.py tests/test_web_server.py`; use the Impala audit row below when a representative gate changes. |
| Impala representative audit gates | [representative Impala gates](local-smoke.md#representative-impala-audit-gates) | `python3 -m pytest -q tests/test_audit_impala_*.py tests/test_build_impala_*.py tests/test_impala_synthetic_*_gate.py tests/test_audit_recent_details.py tests/test_audit_optimizer_funnel.py tests/test_audit_profile_evidence_gates.py tests/test_audit_workload_diagnostics.py tests/test_audit_stats_diagnostics.py` |
| Trino/Spark handoff artifact helpers | Relevant engine evidence checklist and [safety contract](safety-contract.md) | `python3 -m pytest -q tests/test_handoff_artifacts.py tests/test_trino_one_query_live_handoff_script.py tests/test_spark_one_application_handoff_script.py tests/test_audit_trino_evidence_handoff.py tests/test_audit_spark_evidence_handoff.py` |
| CLI command building | [development practices](development-practices.md) | `python3 -m pytest -q tests/test_cli_* tests/test_web_server.py` |
| Config behavior | [development practices](development-practices.md), [credentials](credentials.md) | `python3 -m pytest -q tests/test_config* tests/test_*config*` |
| Agent tooling or validation routing | [agent playbook](agent-playbook.md), [code map](code-map.md) | `python3 -m pytest -q tests/test_agent_code_graph.py tests/test_agent_preflight.py tests/test_check_active_docs.py tests/test_check_markdown_links.py tests/test_check_release_history_shape.py tests/test_check_staged_public_safety.py tests/test_audit_public_docs.py tests/test_worktree_status.py`; run all documentation checks above. |

## Operator And Retained-Evidence Routes

Use the focused row above for code changes. Use these canonical runbooks only
when the task needs their live or retained-evidence path:

- Kubernetes configured auth: [Live External Smoke](kubernetes-auth-front-door.md#live-external-smoke) and [Raw-Free Audit](kubernetes-auth-front-door.md#raw-free-audit).
- Owner-raw D3: [Artifact Workspace Helper](owner-raw-d3-deployment.md#artifact-workspace-helper), [SSO/Auth Proxy Support Readiness Gate](owner-raw-d3-deployment.md#ssoauth-proxy-support-readiness-gate), [Live Front Door Validation Gate](owner-raw-d3-deployment.md#live-front-door-validation-gate), and [Validate The Contract](dev-sso-keycloak.md#validate-the-contract).
- Trino: [Readiness Gates](engines/trino-diagnostic-contract.md#readiness-gates), [Handoff Package](engines/trino-test-cluster-evidence-checklist.md#handoff-package), [Release Gates](engines/trino-private-preview-release.md#release-gates), [Broader Production Closure Plan](engines/trino-live-collection-design.md#broader-production-closure-plan), [Implementation Gates](engines/trino-live-collection-design.md#implementation-gates), and the shared-deployment [Audit Path](trino-shared-deployment-hardening.md#audit-path).
- Spark: [Local Validation Gate](engines/spark-test-cluster-evidence-checklist.md#local-validation-gate) and [Acceptance Gate](engines/spark-test-cluster-evidence-checklist.md#acceptance-gate).
- Impala: [Representative Impala Audit Gates](local-smoke.md#representative-impala-audit-gates).
- Recent history and deployment packaging: [recent history store](recent-history-store.md) and the relevant deployment README.

If a listed test file does not exist, treat that as validation-documentation
drift. Use preflight and the nearest test only for diagnosis; update the matrix
or restore the intended regression before handoff. Do not silently substitute a
different test and claim the listed route passed.

## When To Run Full Pytest

Run `python3 -m pytest` when:

- a safety or trust boundary moves;
- collector, analyzer, report, optimizer, and web contracts change together;
- a shared helper affects several workflows;
- focused failures suggest cross-module risk;
- a release or demo baseline requires the full gate.
