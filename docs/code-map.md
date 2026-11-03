# Code Map

Last reviewed: 2026-11-03

This is the practical lookup map for coding agents. Use it to find the likely
owner of a behavior before reading large modules.

## Graph-Assisted Lookup

This map remains the durable source of behavior ownership. Local code graphs
can help with unfamiliar or cross-module work, but they are orientation tools,
not authority.

- Prefer this map and focused code search first. Use a local graph only when
  import, call, route, presenter, validator, or test ownership is still unclear.
- Run `python3 scripts/agent_code_graph.py` for a compact local
  `SUMMARY.md` and `graph.json` in the system temporary directory when a
  repeated lookup would otherwise require broad file reads.
- Use `python3 scripts/agent_code_graph.py --changed` for a graph-derived
  read-scope hint around current Git changes, or `--explain <path>` when one
  unfamiliar file needs nearby tests, docs, entrypoints, and support files. Add
  `--compact` for a shorter hint. Validation hints in graph output are a
  starting point; use `scripts/agent_preflight.py` or the test matrix when scope
  is unclear.
- Use `--symbol <name>` when a Python class, function, method, or failing test
  name is known but its focused implementation window is not. Symbol indexing
  is built only for that query, ranks exact names first, and keeps ordinary
  file-level graph calls on their existing lightweight path. With `--context
  --detail preview`, the first source window starts around the matched symbol;
  related production or test files still fall back to file-level ranking when
  no nearby Python symbol matches.
- When the ranked paths are known but source context is still needed, add
  `--context --detail fold|preview|full --line-budget <lines>` to an
  `--explain <path>` or `--symbol <name>` query. `fold` emits paths only,
  `preview` shares the budget across ranked files, and `full` spends it in rank
  order. An optional
  `--context-ledger <ignored-or-external.jsonl>` records only content hashes and
  emitted line ranges, skips unchanged ranges on later calls, and automatically
  invalidates entries after a file changes. Keep ledgers out of committed docs
  and generated artifacts.
- Run `python3 scripts/agent_code_graph.py --merge-risk --base main --compact`
  before merging task branches to detect exact file overlaps and area overlaps
  against `main` and sibling worktrees. Use it as an early coordination signal;
  it does not prove whether a merge will conflict.
- Safe aggregate usage metrics are recorded by default in shared local state
  outside the repository so they survive task-worktree cleanup. Review only the
  aggregate with `python3 scripts/agent_code_graph.py --usage-summary`. The
  summary includes daily mode counts and a local `main` reflog proxy for
  merge-risk runs before merge events; use it as adoption signal, not causal
  proof. Use `--no-record-usage` for a run that should not be counted, and do
  not commit or quote raw usage JSONL records.
- Keep graph generation local or ignored. Do not commit generated graph JSON,
  HTML, caches, reports, screenshots, or `graphify-out` output.
- Keep graph inputs narrow and public-safe by default: source code, deployment
  templates, schemas, build scripts, public config examples, and public docs
  are reasonable; local notes, generated cases, raw profiles, raw SQL, raw
  metadata, private configs, and retained local smoke outputs are not.
- Avoid LLM/API-backed semantic extraction for repository graphs unless the
  user explicitly approves that data boundary. Prefer deterministic AST/import
  extraction for routine ownership lookup.
- Treat confidence labels literally. `EXTRACTED` means a tool found a
  structural edge; it does not prove product behavior. `INFERRED` and
  ambiguous edges are leads to check, not facts to cite.
- When a graph reveals a durable ownership improvement, verify the claim
  against current code, tests, and docs, then update this file with the
  public-safe conclusion rather than preserving the raw graph artifact.

## Product Surfaces

| Surface | Primary code | Notes |
| --- | --- | --- |
| Diagnose UI: Recent queries, Running now, Known Query ID | `query_doctor/web/`, `query_doctor/recent/` | Web routes and presenters should keep browser output safe. |
| Details pages | `query_doctor/web/ui/recent_scan_details.py`, `query_doctor/web/presenters/`, `query_doctor/web/trusted_artifacts.py` | Keep data assembly, trusted artifact loading, presentation, and rendering separate. |
| Reports | `query_doctor/report/`, `query_doctor/cli/report.py` | Python Report is the deterministic baseline; optional LLM narrative owns wording only after validation. |
| Query LLM optimizer | `query_doctor/optimizer/`, `query_doctor/cli/optimize_query.py`, `query_doctor/web/trusted_artifacts.py` | Trusted SQL drafts require deterministic validation and current markers. |
| Pasted-SQL Query Optimizer | `query_doctor/optimizer/`, `query_doctor/web/` | Read-only parse/analyze only. Do not echo submitted SQL after submit. |
| Cloudera Manager collection | `query_doctor/cm/`, `query_doctor/cli/collect_cm_profiles.py` | Explicit, bounded, read-only, redacted. |
| Direct Impala daemon collection | `query_doctor/impala/`, `query_doctor/recent/`, `query_doctor/cli/collect_impala_profile.py` | Bounded Recent, Running, and one Known Query ID profile source; optional JSON profile, `/profile_docs`, and `/admission?json` probes; no Cloudera Manager events. |
| Engine capability manifest | `query_doctor/engines/capabilities.py`, `query_doctor/engines/base.py`, `query_doctor/cli/commands.py`, `query_doctor/web/preview_surfaces.py`, `pyproject.toml` | Machine-checkable product/preview/dev-gate capability graph. Adapter flags, second-engine CLI roles, isolated preview routes, and Trino/Spark script taxonomy should stay aligned with this manifest. |
| Trino browser/report regression audit | `query_doctor/trino/browser_report_regression.py`, `scripts/audit_trino_browser_report_regression.py` | Dev-only raw-free closure-gate audit over the required browser/report test catalog, materialized route capabilities, adapter flags, blocked raw-output classes, per test/route/product-surface requirement tracking, and `production_review_browser_report_v1` profile tracking. It does not render pages, load artifacts, collect from Trino, generate SQL, execute SQL, or promote broader support. |
| Spark compact History Server intake and evidence packages | `query_doctor/spark/`, `query_doctor/analyzer/engine_intake_primitives.py`, `query_doctor/analyzer/engine_redaction_note.py`, `query_doctor/analyzer/spark_evidence_package.py`, `query_doctor/analyzer/spark_evidence_package_builder.py`, `query_doctor/cli/collect_spark_history.py`, `query_doctor/cli/diagnose_spark_compact.py`, `query_doctor/cli/build_spark_evidence_package.py`, `query_doctor/cli/validate_spark_evidence_package.py`, `query_doctor/cli/export_spark_evidence_fixtures.py`, `query_doctor/safety/manifest_references.py`, `scripts/spark_evidence_package_requirements.py`, `scripts/spark_one_application_handoff.py`, `scripts/build_spark_one_application_handoff_suite_manifest.py`, `scripts/build_spark_evidence_package_from_one_application_suite.py`, `scripts/audit_spark_compact_readiness.py`, `scripts/audit_spark_product_surface_boundary.py`, `scripts/audit_spark_evidence_handoff.py`, `scripts/build_spark_handoff_suite_manifest.py`, `scripts/audit_spark_support_boundary.py`, `query_doctor/web/preview_surfaces.py`, `query_doctor/web/spark_compact.py`, `query_doctor/web/ui/spark.py`, `query_doctor/engines/spark.py` | Registered bounded summary-only `/api/v1` intake plus local CLI/direct preview web collect-and-diagnose, shared bounded JSON intake primitives and package-style `redaction_note_v1` validation for evidence-package wrappers, shared safe relative JSON manifest reference checks for retained handoff-suite artifacts, dev-only requirements printing from the Python-owned evidence package contract, dev-only one-application handoff wrapper over the same bounded History Server compact path with optional product-surface summary audit over the written compact/diagnosis artifacts, dev-only one-application handoff-suite manifest metadata over retained raw-free compact/diagnosis/boundary artifact triples and optional matching `spark_one_application_handoff_summary_v1` and `spark_product_surface_boundary_audit_v1` artifacts, optional raw-free compact readiness summary JSON for suite audit output, dev-only product-surface boundary audit over retained compact/diagnosis artifacts or one-application handoff manifests, dev-only one-application-suite-to-package bridge that rechecks diagnosis/boundary/summary consistency before building a sanitized evidence package wrapper, local compact evidence-package build/validation, fixture-ready compact sample export with a safe manifest, strict package-to-fixture handoff audit for raw-free Spark fact-contract shaping with retained diagnostic-lane checked/readiness/source-granularity/verification-scope and fact-state counters, dev-only handoff-suite manifest metadata over retained raw-free handoff summaries, and a static support-boundary audit with optional `spark_support_boundary_audit_v1` summary output that keeps the Spark adapter compact-only while checking Spark CLI roles, support docs, and Details/report/optimizer/recent imports below production product support; includes optional application lifecycle/attempt-state facts, source coverage warning IDs, application attempt counts, aggregate job-state counts, executor loss/churn aggregates, explicit dynamic-allocation markers from executor summaries, and raw-free Spark version-family facts; no Recent workflow, Details/trusted report surface, optimizer behavior, raw event-log download, raw SQL/plan/log/environment collection, or Spark job execution. |
| Prometheus runtime metrics | `query_doctor/prometheus/`, `query_doctor/impala/`, analyzer runtime metrics modules | Optional bounded runtime context for configured direct Impala workflows; allowlisted PromQL only. |
| Impala metadata | `query_doctor/impala/` | Allowlisted `SHOW` statements only. |
| Analyzer facts and scoring | `query_doctor/analyzer/`, `query_doctor/recent/` | Deterministic facts, score reasons, and action candidates. |
| Already-provided Impala EXPLAIN facts | `query_doctor/analyzer/impala_explain.py`, `query_doctor/analyzer/impala_explain_loader.py`, `query_doctor/cli/analyze_profile.py` | Bounded case-contained loader and pure parser with a raw-free analyzer-result projection (`analysis.json` when requested); no EXPLAIN execution, scoring, recommendations, report/browser rendering, or platform contract. |
| Representative validation audits | `scripts/audit_impala_diagnostic_loop.py`, `scripts/audit_impala_north_star_gate.py`, `scripts/build_impala_north_star_suite_manifest.py`, `scripts/audit_recent_details.py`, `scripts/audit_profile_evidence_gates.py`, `scripts/audit_impala_coverage_gaps.py`, `scripts/audit_workload_diagnostics.py`, `scripts/audit_stats_diagnostics.py`, `scripts/audit_optimizer_funnel.py` | Aggregate and component strict gates for raw-free Impala representative-batch calibration. |
| Dev-only handoff artifact safety | `query_doctor/safety/handoff_artifacts.py`, `scripts/*handoff*.py` | Shared path-overlap checks and ASCII/sorted JSON artifact writing for retained raw-free handoff summaries and compact artifacts. Engine-specific redaction checks, readiness gates, and support-boundary wording remain in the owning Trino/Spark scripts. |
| Engine fact contract and Trino intake | `query_doctor/analyzer/engine_facts.py`, `query_doctor/analyzer/engine_fact_promotion_policy.py`, `query_doctor/analyzer/engine_fact_consumer.py`, `query_doctor/analyzer/engine_intake_primitives.py`, `query_doctor/analyzer/engine_redaction_note.py`, `query_doctor/analyzer/impala_engine_facts.py`, `query_doctor/analyzer/trino_fixture_facts.py`, `query_doctor/analyzer/trino_evidence_package.py`, `query_doctor/analyzer/spark_fixture_facts.py`, `query_doctor/analyzer/spark_fixture_schema.py`, `query_doctor/trino/local_event_store.py`, `query_doctor/trino/http_event_archive.py`, `query_doctor/trino/http_query_detail_archive.py`, `query_doctor/trino/local_query_detail.py`, `query_doctor/trino/local_query_list.py`, `query_doctor/trino/local_statement_stats.py`, `query_doctor/trino/source_contract_registry.py`, `query_doctor/trino/production_collector_contracts.py`, `query_doctor/trino/representative_evidence.py`, `query_doctor/trino/query_linked_fact_coverage.py`, `query_doctor/trino/product_metadata_collection.py`, `query_doctor/trino/report_optimizer_safety.py`, `query_doctor/trino/production_closure_gates.py`, `query_doctor/trino/event_source_contract.py`, `query_doctor/trino/coordinator_query_info_target.py`, `query_doctor/trino/coordinator_query_info_pruned_import.py`, `query_doctor/trino/diagnosis.py`, `query_doctor/cli/build_trino_evidence_package.py`, `query_doctor/cli/validate_trino_evidence_package.py`, `query_doctor/cli/trino_import.py`, `query_doctor/cli/trino_event_store_import.py`, `query_doctor/cli/trino_http_event_archive_import.py`, `query_doctor/cli/trino_http_query_detail_archive_import.py`, `query_doctor/cli/trino_query_detail_import.py`, `query_doctor/cli/trino_query_list_import.py`, `query_doctor/cli/trino_statement_stats_import.py`, `query_doctor/cli/trino_query_info_pruned_import.py`, `query_doctor/cli/trino_event_source_contract_check.py`, `query_doctor/cli/trino_coordinator_query_info_target_check.py`, `query_doctor/cli/trino_coordinator_query_info_pruned_probe.py`, `query_doctor/cli/trino_coordinator_query_info_pruned_import.py`, `query_doctor/cli/trino_diagnosis_output.py`, `query_doctor/cli/diagnose_trino_compact.py`, `query_doctor/safety/manifest_references.py`, `query_doctor/web/preview_surfaces.py`, `query_doctor/web/trino_compact.py`, `query_doctor/web/ui/trino.py`, `scripts/trino_evidence_package_requirements.py`, `scripts/build_trino_evidence_package.py`, `scripts/validate_trino_evidence_package.py`, `scripts/audit_trino_evidence_handoff.py`, `scripts/build_trino_evidence_handoff_suite_manifest.py`, `scripts/audit_trino_product_surface_boundary.py`, `scripts/audit_trino_production_collector_contracts.py`, `scripts/audit_trino_representative_evidence.py`, `scripts/audit_trino_query_linked_fact_coverage.py`, `scripts/audit_trino_product_metadata_collection.py`, `scripts/audit_trino_report_optimizer_safety.py`, `scripts/audit_trino_production_closure_gates.py`, `scripts/audit_trino_shared_deployment_boundary.py`, `scripts/audit_trino_shared_deployment_preflight.py`, `scripts/audit_trino_support_gap_matrix.py`, `scripts/audit_trino_web_beta_readiness.py`, `scripts/audit_trino_web_beta_live_smoke.py`, `scripts/audit_trino_beta_release_readiness.py`, `scripts/trino_one_query_live_handoff.py`, `scripts/build_trino_handoff_suite_manifest.py`, `scripts/audit_trino_compact_readiness.py`, `tests/engine_fact_contract_harness.py` | Typed normalized facts, explicit fact namespace registration, checked promotion-policy registry for shared/distributed-SQL-family/source-boundary/support-boundary facts, raw-free boundary payloads, shared bounded JSON intake primitives and package-style `redaction_note_v1` validation for evidence-package wrappers, shared safe relative JSON manifest reference checks for retained handoff-suite artifacts, source-contract/source-type registry for accepted bounded local production and preview source kinds, production collector closure-gate audit over existing local lanes, preview readers, contract-only sources, open blockers, and optional retained representative-evidence summary handoff checks that require the production_review_breadth_v1 ready profile, representative-evidence closure-gate audit over already retained raw-free summary-counter payload breadth with the production_review_breadth_v1 profile, query-linked fact coverage closure-gate audit over bounded compact facts and open operator/split/telemetry blockers, product metadata collection closure-gate audit over metadata source registry, fact namespace, adapter flags, product-surface blocks, identifier/raw metadata redaction, and no user SQL execution, report optimizer safety closure-gate audit over materialized report/guidance capabilities, raw-source policy, validator sentinels, adapter flags, and blocked LLM/job/generated-SQL/SQL-execution surfaces, consolidated bounded production claim closure-gate audit over the support-gap gate list, Trino dev-gate capability wiring, retained closure tracking summaries, and collector-to-representative-evidence linkage with raw-free current-tracking validity, invalid-summary count, linkage validity/missing-summary, claim-ready status, and CLI output, raw policy, bounds, and promotion gates, sanitized Trino package build/validation through CLI modules with script wrappers, local event-store import, bounded HTTP event archive import, bounded HTTP query-detail archive import, local query-detail import, local query-list aggregate import, local statement-stats import, local compact pruned QueryInfo import, event-source contract checks, dry-run coordinator query-info target checks, bounded pruned coordinator probes, one-query pruned coordinator fact import with direct `--boundary-out`, dev-only evidence-package requirements printing from the Python-owned contract, dev-only package-to-boundary handoff audit over sanitized packages with optional retained raw-free handoff summary JSON, dev-only retained evidence-handoff summary suite manifest and audit, dev-only product-surface boundary audit over retained raw-free compact artifacts and handoff-suite manifests with optional retained product-surface summary checks, dev-only shared-deployment boundary audit over local config shape and static surface registries for trusted front-door identity plus raw-source isolation, dev-only shared-deployment preflight over the shared boundary, product-surface, support-gap, and active-docs gates with child output captured and only raw-free gate counts/categories emitted, dev-only static support-gap audit over registered Trino fact-family coverage, source-type registry coverage, engine fact promotion-policy coverage, adapter flags, and bounded production claim status, dev-only Trino Beta web readiness/live-smoke/release-readiness gates with raw-free summaries and no unsupported product output, dev-only one-query handoff wrapper with strict readiness audit, optional `--query-id-file`, and optional handoff summary output, and optional product-surface summary output, dev-only handoff-suite manifest builder, manifest-driven one-query handoff suite readiness over raw-free boundary/diagnosis/smoke artifacts with optional matching per-entry readiness summary checks, optional retained handoff summary refs, and optional retained product-surface summary refs and optional raw-free suite summary JSON, local compact diagnosis over raw-free direct boundary JSON excluding metadata summary boundaries or selected package sample boundaries with a checked `diagnostic_lane` summary, single-boundary import `--diagnosis-out`, registered isolated local `/trino/compact-diagnosis` rendering for the same already raw-free inputs, fixture/experimental Spark mapping, and a read-only consumer probe; not a live engine selector. |
| Browser safety | `query_doctor/safety/browser_display.py`, web presenters | Dynamic browser text should cross this boundary. |

Trino production closure gate tracking also emits raw-free per-gate
`gate_tracking` entries and aggregate `gate_tracking` counts for accepted,
missing, invalid, and not-required retained tracking inputs; the CLI prints the
same path-free counts and reports `bounded_production_claim_ready` only for a
complete accepted retained summary bundle, without collecting from Trino,
executing SQL, or promoting broader/shared expansion.

Trino production collector contract tracking also emits raw-free
`source_requirement_tracking` entries and aggregate
`source_requirement_tracking` counts for accepted, missing, and invalid
collector source requirements, plus auth-reference policy, source-schema gate,
retry-policy, fail-closed policy, reader-status, reader-scope, CLI-role,
capability, and forbidden-reader counters; the CLI prints the same path-free
tracking, policy, and reader counts while keeping the collector gate
`not_closed`.

Trino representative evidence tracking also emits raw-free
`breadth_requirement_tracking` entries and aggregate
`breadth_requirement_tracking` counts for accepted, insufficient, and
not-required retained breadth requirements, including retained summary-kind mix
and accepted input-status requirements for the production-review profile; the
CLI prints the same path-free summary-kind and breadth counts while keeping the
representative evidence gate `not_closed`.

Trino query-linked fact coverage tracking also emits raw-free
`query_linked_requirement_tracking` entries and aggregate
`query_linked_requirement_tracking` counts for accepted, missing, and invalid
fact/source requirements, source-granularity counts, and
`production_review_query_linked_v1` coverage-profile tracking for required
core fact families, query-linked scopes, one-query source granularities, and
retained operator/split-detail/telemetry blockers, plus
`operator_connector_telemetry_decision_v1` tracking that keeps connector metric
signal bounded-supported and operator-level, split-detail, and telemetry
linkage as deliberate unsupported gaps; the CLI prints the same path-free
counts while keeping the query-linked coverage gate `not_closed`.

Trino product metadata collection tracking also emits raw-free
`product_metadata_requirement_tracking` entries and aggregate
`product_metadata_requirement_tracking` counts for accepted, missing, and
invalid fact/source/product-surface requirements, plus
`production_review_metadata_v1` tracking for allowlist/source lanes,
aggregate-only metadata boundary, metadata fact namespace, bounded sources,
redaction blocks, explicit metadata SQL policy, product-surface blocks, and the
retained product-metadata open blocker; the CLI prints the same path-free
counts while keeping product metadata collection unsupported.

Trino report and optimizer safety tracking also emits raw-free
`report_optimizer_requirement_tracking` entries and aggregate
`report_optimizer_requirement_tracking_counts` for accepted, missing, and
invalid capability/policy/validator/product-surface requirements, plus
`production_review_report_optimizer_v1` tracking for report/guidance families,
materialized capabilities, raw-source policy fields, validator sentinel matrix,
and blocked product-surface requirements; the CLI prints the same path-free
counts while keeping the report optimizer safety gate `not_closed`.

Trino browser/report regression tracking also emits raw-free
`browser_report_requirement_tracking` entries and aggregate
`browser_report_requirement_tracking_counts` for accepted, missing, and invalid
test/route/product-surface requirements, plus
`production_review_browser_report_v1` tracking for regression families, test
files, materialized route capabilities, raw-output blocks, unsupported-surface
blocks, download regressions, and public-claim regressions; the CLI prints the
same path-free requirement and production-review counts while keeping the
browser/report regression gate `not_closed`.

Trino metadata CLI summary ownership also includes
`query_doctor/trino/metadata_cli_summary.py`,
`query_doctor/trino/local_metadata_summary.py`,
`query_doctor/cli/trino_metadata_cli_summary.py`, and the dev-only
`scripts/trino_metadata_cli_summary_smoke.py` round-trip gate, with
`scripts/audit_trino_product_metadata_collection.py` tracking the current
product metadata collection closure gate from only the metadata source registry,
metadata fact namespace, adapter flags, and product-surface blocks. That path
writes only raw-free smoke or aggregate metadata summaries and remains outside
Recent, Details, trusted reports, optimizer behavior, Running scans,
query-history crawling, product metadata collection, generated SQL, and user SQL
execution.

Trino shared/non-local deployment hardening ownership includes
`scripts/audit_trino_shared_deployment_boundary.py`,
`scripts/audit_trino_shared_deployment_preflight.py`,
`scripts/audit_owner_raw_live_front_door_review.py`, and
[trino-shared-deployment-hardening.md](trino-shared-deployment-hardening.md).
It remains a dev-only static/config-shape and preflight gate for trusted
front-door identity, explicit operator front-door review confirmation,
machine-checkable raw-free live proxy review summaries, and raw-source
isolation. The shared boundary audit records raw-free
`shared_deployment_requirement_tracking` entries and
`shared_deployment_requirement_tracking_counts` for deployment-config,
product-boundary, capability, release-gate, and documentation requirements,
plus `production_review_shared_deployment_v1` tracking for review families,
deployment-config requirements, product-boundary requirements, capability
requirements, release requirements, doc requirements, and unsupported-surface
blocks. It is not broader/shared production support.

Trino package-level retained evidence-handoff suite audits can also require
selected source-contract, connector-family, diagnostic-lane source-granularity,
and verification-scope labels through
`scripts/audit_trino_evidence_handoff.py --handoff-suite-manifest`.
`scripts/audit_trino_representative_evidence.py`
aggregates already raw-free handoff/readiness summaries for the representative
real-cluster evidence closure gate. Both remain dev-only retained metadata and
do not create live or product Trino surfaces.

## Common Change Targets

### Add Or Change A Details Block

Start with:

- `query_doctor/web/presenters/`;
- `query_doctor/web/ui/recent_scan_details.py`;
- `query_doctor/web/trusted_artifacts.py` if the block depends on report or
  optimizer trust state.

Do not pass raw domain objects directly to HTML helpers when a presenter/view
model can own safe display text.

### Add A Report Claim Or Section

Start with:

- `query_doctor/report/`;
- `query_doctor/cli/report.py`;
- report sanitizer/validator tests.

The claim should be backed by analyzer facts before prompt or wording changes.

### Add An Optimizer Recipe

Start with:

- `query_doctor/optimizer/`;
- `query_doctor/cli/optimize_query.py`;
- `tests/fixtures/optimizer_cases/`;
- `docs/query-optimizer-contract.md`.

Add accepted and rejected fixtures. A recipe should document what it preserves,
not just what it rewrites.

### Change Cloudera Manager Metrics Or Events

Start with:

- `query_doctor/cm/`;
- `query_doctor/analyzer/`;
- `query_doctor/web/presenters/` if surfaced in browser output;
- `docs/architecture.md` and `docs/demo-data-engineer-brief.md` if user-facing
  meaning changes.

Metrics and events are runtime context unless analyzer facts support a stronger
claim.

### Change Metadata Collection

Start with:

- `query_doctor/impala/`;
- analyzer metadata fact rendering;
- recent scan metadata policy and candidate scoring tests.

Keep metadata status distinct: `not_requested`, `partial`, `failed`,
`insufficient_metadata`, and collected-but-not-proof.

### Change Batch Or Progress Behavior

Start with:

- `query_doctor/recent/`;
- `query_doctor/cli/batch_recent.py`;
- `query_doctor/cli/recent_profile_worker.py` for shared Recent profile-job
  worker behavior;
- `query_doctor/web/jobs.py`;
- Details progress rendering.

Keep analyzer, metadata, metrics, events, report, and optimizer stages
separately bounded and safely reported.

## Trust Boundaries

| Boundary | Owner | What to protect |
| --- | --- | --- |
| Raw collection to local case | `query_doctor/cm`, `query_doctor/impala`, `query_doctor/spark` | Bounds, redaction, read-only source access. |
| Local case to analyzer facts | `query_doctor/analyzer` | Deterministic facts, limitations, no unsupported root causes. |
| Analyzer facts to report | `query_doctor/report` | Prompt scope, sanitizer, validator, trusted report marker. |
| Source SQL to optimizer result | `query_doctor/optimizer`, `query_doctor/cli/optimize_query.py` | Read-only scope, semantic guards, recipe checks, trust marker. |
| Trusted artifacts to browser | `query_doctor/web/trusted_artifacts.py` | Marker validity, stale artifacts, safe recommendation text. |
| Dynamic text to browser | `query_doctor/safety/browser_display.py`, presenters | No raw SQL/profile/metadata/paths/artifact names/model names. |

## Where Not To Put Logic

- Do not put collector source rules in UI modules.
- Do not put browser display policy in collectors.
- Do not put optimizer trust in prompts.
- Do not put report facts in LLM wording.
- Do not add fake engine support under `engines/`.
- Do not create placeholder packages without current callers.

## Command Entrypoints

Current docs and subprocess builders should use packaged console scripts or
`python -m query_doctor.cli.<module>`. Root-level prototype commands are not
current product entrypoints.
