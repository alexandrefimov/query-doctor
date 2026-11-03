# Query Doctor Roadmap

Last reviewed: 2026-11-03

Required reading before any PR: hard rules in `AGENTS.md`,
`docs/agent-quickstart.md`, Product Direction, and the Near-Term Priorities
section relevant to the touched area. Other sections are reference.

This roadmap tracks active product direction. It is not a support matrix and it
is not a historical audit log. For engineering risks, use
[code-audit.md](code-audit.md). For architecture boundaries, use
[architecture.md](architecture.md). For optimizer trust rules, use
[query-optimizer-contract.md](query-optimizer-contract.md).

## Current Scope

- Apache Impala is the full production triage SQL engine. Trino has bounded
  local production support only for retained-list Recent, one explicit Query
  ID, raw-free materialized Details, deterministic Python Report, and optimizer
  guidance over server-owned materialized cases, plus the supporting raw-free
  import, source-contract, handoff, and audit surfaces listed in the support
  matrix. Broader/shared Trino expansion remains behind separate promotion
  gates.
- Query Doctor is positioned as a local-first Big Data query diagnostic tool
  focused on Impala production triage, with bounded local Trino production
  lanes, not as a generic AI query profile analyzer.
- Cloudera Manager is the full implemented query discovery, profile, metrics,
  and events source for Recent queries.
- Direct Impala daemon collection supports bounded Recent scans, Running scans,
  and one explicit Known Query ID through daemon query-list/profile endpoints.
  It does not provide Cloudera Manager events. It can optionally collect
  bounded Prometheus runtime metrics when explicitly configured.
- Direct Impala deeper-history options are distinct source families: increasing
  coordinator query-log retention on Impala daemons, future operator-managed
  read-only profile-log directory ingestion, or future bounded external history
  sources such as Loki or OpenSearch. Only the daemon query-list/profile endpoint
  path is current support.
- Current-upstream Apache Impala compatibility uses the bounded direct Impala
  path. Optional JSON profiles, `/profile_docs`, and `/admission?json` need
  version-specific fixture and representative read-only validation; missing
  optional endpoints must degrade safely. A single source smoke is not a
  representative support claim.
- Direct Impala diagnosis now extracts raw-free profile format,
  source provenance, resource balance, per-node read/user/system time, Query
  Timeline, and fragment lifecycle timing facts, and can use profile resource
  and timing signals in Runtime Diagnosis.
- The current web UI groups query workflows under Query Inbox.
- Recent Scan, evolving into a Query Inbox over materialized safe cases, is the
  flagship workflow for production triage across many queries.
- Query Inbox now has an explicit first-screen state strip for empty, ready,
  running, partial, and stale safe summaries. Stale uses parseable safe
  freshness fields from the materialized summary; missing or synthetic
  freshness stays unknown rather than becoming a claim. When materialized
  results are available, the strip also exposes safe
  source/window/time-range/query-type scope chips and allowlisted result
  presets plus view-only owner/pool tag, opaque owner/pool value,
  lifecycle/readiness/action filters for owner-tagged rows, pool-tagged rows,
  concrete safe owner/pool values, clean analysis, status follow-up, metadata
  availability, validated reports, optimizer guidance, and recorded action
  outcomes over existing URL filters.
  The collapsed New scan form reuses safe
  source/window/time-range/workflow/query-type refresh defaults from the
  materialized scope; it does not store browser state or start collection.
- Recent queries is the default Query Inbox mode.
- Finished queries is the default completed-query scan target.
- Running now is a lower-confidence live scan target inside Recent queries.
- Known Query ID is the secondary Query Inbox mode for one known Impala query ID.
- Recent results now include raw-free workload diagnostics for repeated,
  frequent-short, and regressed workload fingerprints, plus workload detail
  pages, admin pool/owner digests, an analyst action queue, and compact action
  outcome rollups.
- Details pages show deterministic findings, an explicit Python Report
  baseline, optional LLM narrative, and Query LLM optimizer actions.
  `no_llm=true` is a supported safety and privacy mode for selected-case
  report and optimizer actions; in that mode the outputs stay Python-owned and
  do not require a model route.
- Help, Details static UI copy, and newly generated trusted reports are
  controlled by the global `language` config. English is the default; Russian
  remains a localized companion mode, not a separately generated second report.
- Finished-query web scans use the configured `recent_window_minutes` Search
  depth lookback across Cloudera Manager and direct Impala sources, with a
  browser warning for large windows that may increase source or optional
  Prometheus load.
- Recent summary history has a raw-free storage contract. SQLite is the
  dependency-free local adapter, and optional Postgres is the configured
  production storage target for `recent_query_summary`. The Helm chart can wire
  its DSN from an existing Secret key in configured mode. The storage schema now
  includes raw-free profile-job, analysis-cache, and profile-artifact metadata
  tables plus a deterministic profile-budget planner and atomic claim semantics
  for pending or expired jobs. Profile jobs also support owner-guarded lease
  renewal, completion, retryable failure, and terminal failure transitions. The
  analysis cache can upsert/load raw-free analyzer payloads by Query ID, profile
  fingerprint, and analyzer contract; the profile-artifact table stores only
  compatibility keys, status, size, and `fingerprint_only` metadata. The Helm chart
  can optionally render a CloudNativePG `Cluster` for configured mode while
  keeping DSN and owner Secrets external. Storage backends expose explicit
  retention pruning for old summaries, terminal profile jobs, analysis-cache
  rows, and profile-artifact metadata, and Batch Recent can opt in through
  retention-day config/CLI settings. The Recent history Postgres readiness CLI
  checks DSN env handoff and schema initialization with raw-free output; the
  Helm chart wires pod readiness, scheduled background retention, and the
  shared profile worker. Future byte-retaining artifact storage still requires
  an explicit bounded delete implementation before any local/object artifact
  references can be accepted.
- Query Optimizer is a separate pasted-SQL parse/analyze workflow. It never
  executes SQL and does not echo submitted SQL after submit.
- Apache Impala upstream work around IMPALA-14953 is an explicit alignment
  point. Query Doctor should consume or compare against stable upstream profile
  JSON/parser/redactor contracts when they become available instead of
  duplicating the native one-profile AI analysis surface.
- Apache Impala aggregated / experimental profile-v2 work is an additional
  compatibility risk. Query Doctor should treat profile representation as a
  dialect, detect it before deterministic analysis, and fail closed when the
  dialect or required evidence sections are unknown or only partially mapped.
- Bounded Impala metadata collection is read-only, allowlisted, explicit, and
  redacted.
- Cloudera Manager metrics and events are runtime context. They can strengthen
  analyzer-supported hypotheses, but they are not standalone root-cause proof.
- Prometheus runtime metrics are optional context for configured direct Impala
  workflows. Prepared event/log sources remain future optional context.
- Synthetic Demo Mode is local-only and must not contact Cloudera Manager,
  Impala, Ollama, or the network.

## Customer Readiness Focus

The near-term product priority is to make the existing Impala workflow easier
to run, trust, and show before broadening Trino or Spark product surfaces. Use
[customer-readiness-priorities.md](customer-readiness-priorities.md) as the
working backlog for demo site planning, read-only Impala validation access,
minimal configuration, documentation hygiene, Russian-doc review, UI/UX polish,
and script/test role audit.

The near-term adoption gate is five external or design-partner Impala
diagnostic runs with useful feedback, using either one-profile diagnosis or a
bounded Recent scan. Work that does not shorten the path to those runs should
stay behind customer-readiness, trust-boundary, or support-boundary work.

The cheapest first-value path should stay one profile to one diagnosis without
requiring a full Cloudera Manager, Kerberos, metadata, Prometheus, or LLM setup.
Recent scans remain the flagship production workflow, but the first operator
experience should not require the broadest workflow to prove value.

This focus does not remove bounded Trino or Spark contract work from the
repository. It keeps the main customer path Impala-first, keeps Trino limited
to the bounded local production lanes in the support matrix, and keeps Spark
below product support until the Impala Recent and Details experience is strong
enough for design-partner review.

## Product Direction

Query Doctor should be positioned and developed as a local-first Big Data query
diagnostic tool focused on Impala production triage, with bounded local Trino
production lanes, not as an "AI profile analyzer" button or an "AI SQL
optimizer" whose primary promise is automatic SQL rewriting.

- Keep Big Data SQL/lakehouse diagnostics as the long-term category, but make
  the current product promise Impala-first. Trino wording must stay limited to
  the bounded local production lanes until any broader/shared surface has
  implemented facts, fixtures, collection contracts, retained evidence, docs,
  and safety tests.
- Treat upstream native Impala AI profile analysis as a reason to strengthen
  Query Doctor's cross-engine operator-workbench direction, not as permission to
  claim broad multi-engine support early.
- Start second-engine exploration when it helps shape the real engine fact
  contract, even before support-claim gates are complete. Keep exploratory work
  fixture-driven, non-public, and unable to affect normal Impala workflows or
  the bounded Trino local production claim until it has safety coverage and
  diagnostic value.
- The primary product value is ranking suspicious Recent queries, explaining
  which evidence is supported, not observed, or unknown, and routing operators
  toward a safe inspection/change/verification loop.
- Results pages are routing and triage surfaces. Details and Workload Details
  are the decision surfaces for why a query or repeated pattern matters, where
  to inspect, what supported change direction to try, and how to verify it.
- A trusted SQL draft is a useful outcome only when Python-owned facts,
  deterministic execution, and validation prove the rewrite boundary. It is not
  the flagship success metric.
- The main success metrics should be diagnosis coverage, primary-bottleneck
  coverage, evidence quality, reproducible workload impact, and action outcome
  learning.
- The LLM's durable role is report wording, recommendation wording, and
  engineering review support. It should not be treated as the trusted SQL writer
  for supported optimizer recipes or as the source of diagnostic facts.
- The core product should remain deterministic-first and no-LLM-capable:
  Recent diagnosis, Details, Python reports, trusted optimizer outcomes, demos,
  and validation must stay useful when `no_llm=true`. LLM-backed wording can
  remain an optional selected-case extension when it improves communication
  without weakening trust, privacy, latency, or local-first operation.
- Marketing, demos, and benchmarks should lead with evidence-backed diagnosis:
  stats vs SQL shape vs runtime/admission/skew/data movement vs unknown, then
  show SQL rewrites only for recipe-backed cases.
- Upstream Impala profile-analysis work should become a compatibility target:
  profile JSON ingestion, parser coverage, redaction edge cases, and confidence
  labels are useful seams; duplicating the Impala Web UI native AI tab is not.
- Profile dialect and counter semantics are part of the trust contract. The
  analyzer should not assume that classic text, classic JSON, classic Thrift,
  and experimental profile-v2 expose equivalent sections, counter totals, or
  instance-level detail. Impala-provided counter significance/stability labels
  should improve future profile evidence quality when available, but they do
  not replace deterministic analyzer support.
- Product growth should deepen the current Impala wedge while keeping Trino's
  local production lanes narrow and evidence-gated. Spark SQL is not a
  near-term direction: it would require a different runtime/profile fact model,
  collector surface, optimizer contract, and market positioning before Query
  Doctor has proven enough value on Impala workloads.

## Query Inbox Direction

The main product direction is a Recent-first Query Inbox: when an operator opens
the UI, the default production triage path should show already materialized,
raw-free cases for the configured time range, ranked by deterministic evidence
and ready for filtering by source, owner, pool, workload, state, attention area,
and action readiness.

This is an evolution of the existing Recent workflow, not a support-claim
shortcut. The inbox contract is:

- Source adapters perform only explicit, bounded, read-only collection through
  implemented source contracts. Cloudera Manager remains the full Impala
  discovery/profile/metrics/events source; direct Impala and local Trino lanes
  stay within their documented bounds.
- The browser works from server-owned materialized case facts, summary indexes,
  workload digests, action queues, and freshness/coverage states. Trusted
  surfaces still must not expose raw SQL, raw profiles, raw metadata, local
  paths, raw artifact filenames, subprocess output, secrets, model names, or
  runtime internals.
- Online collection means an operator-configured local materialization loop or
  explicit scan job that reuses the same bounded collectors and analyzer. It
  must not auto-run LLM reports, Query Optimizer jobs, generated SQL, SQL
  execution, metadata crawls, broad query-history crawls, or shared-deployment
  identity behavior.
- Query ID diagnosis remains a secondary single-case path, and one-profile
  intake remains the cheapest first-value path for new users or restricted
  access. They should feed the same safe Details and report boundaries when a
  case is materialized.

Pull this direction in staged slices:

1. Define the materialized case index contract: safe case reference, source
   provenance, time-window/freshness state, coverage and limitation counters,
   severity/ranking fields, workload fingerprint summaries, and action-readiness
   fields without raw identifiers or paths.
2. Make the Recent results page consume that contract first, while preserving
   the existing explicit scan path and focused tests for raw-free rendering,
   pagination, grouping, and Details links.
3. Extend Query Inbox filtering over already materialized summaries. The
   shipped slices expose safe source/window/time-range/query-type scope
   context, allowlisted source/window/time-range/workflow/query-type URL scope
   filters, plus result presets for attention area, workload, stats,
   rewrite-guidance readiness, all analyzed cases, and spill evidence through
   existing URL filters. They also expose allowlisted view-only result filters
   for owner-tagged rows, pool-tagged rows, concrete safe owner/pool values via
   opaque URL tokens, clean analysis, status follow-up, metadata availability,
   validated reports, optimizer guidance readiness, and recorded action
   outcomes over already materialized raw-free rows and workload groups. If
   selected scope filters do not match the current
   materialized snapshot, Query Inbox shows a safe filtered state and New scan
   controls rather than mismatched rows. New scan is prefilled with selected
   safe source/window/time-range/workflow/query-type URL filters when they map
   to supported scan controls, otherwise with safe refresh defaults from the
   current materialized scope. The same allowlisted scope filters are preserved
   through New scan submit and job pages without echoing arbitrary query
   parameters. The window, UTC time-range, and query type filters now include
   inline controls for changing that safe lookback URL scope, exact
   finished-query range, and short query type identifier inline. Later slices
   can tune owner/pool value ranking, bucketing, or labels without widening the
   raw-free URL contract.
4. Add an operator-controlled local materialization loop only after the index is
   stable. It should record safe last-run status, collection coverage,
   freshness, and failure categories, not raw child output or source payloads.
5. Calibrate on synthetic and sanitized real Impala batches with the existing
   diagnostic-loop, north-star, leak-canary, browser-safety, and public-doc
   gates before changing user-facing support wording or default first-screen
   behavior.

## Success Metrics

Customer-readiness metrics:

- At least five external or design-partner Impala diagnostic runs produce
  actionable feedback before any broad second-engine product surface is
  promoted.
- A first-time operator can reach a useful one-profile diagnosis without
  configuring Cloudera Manager, Kerberos, metadata collection, Prometheus, or an
  LLM provider.
- Public demo, README, and setup paths make the one-profile entry path,
  read-only synthetic demo, and bounded Recent scan relationship clear without
  requiring new users to read the full documentation set.

Primary diagnosis metrics:

- `case_primary_bottleneck` has medium-or-better confidence on at least 70% of
  representative real-case batches.
- `case_primary_bottleneck = unknown` is below 30% for normal Impala diagnosis
  work, and below roughly 20% before adding another broad SQL-engine product
  surface.
- Representative calibration should run
  `scripts/audit_impala_diagnostic_loop.py <batch_summary.json>` as the
  aggregate strict gate over Details, profile evidence, diagnostic coverage,
  workload, stats, and optimizer readiness. Add `--action-outcomes
  <action_outcomes.jsonl> --require-action-outcomes` for outcome feedback and
  `--require-direct-source-readiness` for direct Impala batches. Retained
  raw-free loop summaries written with `--summary-json` should then run through
  `scripts/audit_impala_north_star_gate.py <raw-free-impala-loop-summary.json>`
  so representative batches have one aggregate gate for unknown rate,
  medium-or-better confidence, and measured action-outcome feedback. Retained
  multi-batch calibration should use
  `scripts/build_impala_north_star_suite_manifest.py` plus
  `scripts/audit_impala_north_star_gate.py --suite-manifest ... --require-min-inputs ...`
  so local real-batch sets can enforce suite breadth and safe trend output
  without committing raw cases, local paths, artifact filenames, or private
  cluster numbers. The retained north-star aggregate should use its safe
  top-unknown-category and unknown-resolution rollups to choose deterministic
  evidence work by contribution while keeping clean or out-of-scope boundaries
  out of the evidence backlog, not by stronger wording.
- Component drilldown should run
  `scripts/audit_impala_coverage_gaps.py <batch_summary.json> --fail-on-diagnostic-coverage-gaps`
  so missing analyzer output, missing primary labels, high unknown-primary rate,
  and low medium/high primary coverage become raw-free audit blockers.
- Representative stats calibration should run
  `scripts/audit_stats_diagnostics.py <batch_summary.json> --fail-on-stats-readiness-gaps`
  so Medium/High stats candidates need structured metadata detail, usable
  metadata status, safe review areas, and comparable rerun confirmation before
  they support readiness claims.
- Evidence quality improves over time through deterministic facts, metadata
  coverage, workload baselines, and action outcome history rather than through
  stronger wording.
- No-LLM selected-case actions produce strict-validated trusted reports and
  trusted optimizer outcomes on supported synthetic and sanitized real fixtures.
- LLM-backed selected-case actions are evaluated as optional wording quality,
  not as diagnosis coverage or proof of optimizer capability.

Optimizer-specific metrics:

- `trusted_sql_draft_rate` is measured only on recipe-supported or
  draft-ready cases, not on all expensive candidates.
- On recipe-supported cases, trusted SQL draft production should be at least
  80% and `partial_untrusted_rate` should stay below 5%.
- A low `safe_to_attempt` rate on broad real Impala workloads is acceptable
  when no Python-owned recipe can prove the transform. It should be measured as
  recipe coverage, not as model failure.
- Broad Recent smoke runs should track optimizer funnel coverage with
  `scripts/audit_optimizer_funnel.py`, separating medium/high candidate share,
  draft-supported, guidance-only, source-unavailable, not-applicable,
  no-recipe, safety-threshold, recipe-adjacent, and draft-ready cases before
  recipe yield is interpreted.
- No-recipe usefulness work should track allowlisted
  `no_recipe_review_track_counts`, including narrow guidance-only subtracks
  such as filtered scalar aggregate review, grouped/distinct/scalar aggregate
  review, and UNION ALL branch-boundary review, before turning any repeated
  family into a recipe candidate. Strict representative calibration should add
  `--fail-on-repeated-no-recipe-readiness-gaps` so repeated no-recipe workload
  groups without one specific safe review track, allowlisted review area,
  bounded change direction, workload metric, and compare/rerun verification
  wording are treated as audit blockers before recipe work.

## Near-Term Trust Architecture Backlog

- Keep the full-pipeline leak-canary regression baseline active and expand it
  when new public or local sinks are added. The baseline synthetic salted case
  flows through manual-profile intake, analysis, scoring, report prompt
  assembly, trusted report output, and browser rendering, with generated sinks
  classified fail-closed before marker checks run.
- Move load-bearing analyzer consumers away from rendered
  `analysis_facts.md`. `analysis.json` or a typed loader over the same
  deterministic facts should become the consumed contract for scoring, prompt
  facts, and web presenters; Markdown should be a render-only view.
- During that migration, add renderer-to-parser contract tests so any remaining
  Markdown parser fails loudly when renderer labels or summary shape change.
- Characterize current scoring over representative fixtures before replacing a
  parser or score source. The first slices should preserve behavior unless the
  changed behavior is explicitly documented and tested.
- A future claim-family registry can reduce duplicated report prompt and
  validator policy, but it should follow the typed-fact migration rather than
  precede it.
- A behavioral engine seam should route one production Impala consumer through
  the normalized fact boundary only after parity is proven. It must not promote
  Trino, Spark, or another engine into product support by architecture alone.

## Safety Baseline

The canonical safety contract lives in [safety-contract.md](safety-contract.md).
Roadmap work must not weaken that contract.

Planning summary:

- Query Doctor's deterministic Python analyzers own diagnostic facts, evidence
  merging, and causal promotion; LLMs own wording only.
- Browser and trusted report output must stay raw-free.
- External collection stays explicit, bounded, read-only, redacted, and safe by
  default.
- Report and optimizer generation stays explicit and validation-gated.
- Profile-derived findings need a known profile dialect and an evidence tier
  before they can influence primary bottleneck classification or trusted report
  wording.

## Web UI And Deployment Direction

- Keep the web UI server-rendered by Python with small vanilla JavaScript
  helpers. A React, SPA, or other client-app migration is not a roadmap goal by
  itself because it expands the browser trust boundary and adds a build/runtime
  dependency that local-first users do not need.
- Treat a richer client application as future work only for a specific
  state-heavy surface, such as complex in-browser filtering, graph or timeline
  visualization, comparison workflows, or a richer optimizer editor. Such work
  must first define raw-free JSON/view-model contracts, preserve no-echo rules
  for pasted SQL, add browser-safety tests, and justify the dependency.
- Supported default deployment is single-user local-first: the process runs
  under the user's own Cloudera Manager, Kerberos, Impala, Prometheus, and LLM
  context as configured, and stores artifacts locally.
- Shared network deployment is not a general supported product surface in the
  current architecture. The narrow shared/non-local `owner_raw` D3 contract is
  an explicit "if you must" exception for one raw source surface only: a trusted
  auth front door sets exactly one normalized viewer header and Query Doctor
  still performs the owner check.
- Non-local or shared deployment beyond that narrow owner-raw source exception
  must wait until there is a real design partner. It needs corporate TLS/auth,
  trusted reverse-proxy boundaries, per-user job/artifact ownership, audit,
  persistence, and operational support before it becomes a product surface.
- Improve team usage through local-first patterns before building
  multi-tenancy: pinned versions, shared report repositories, CI-driven
  `query-doctor-batch-recent` runs, jumpboxes or remote devboxes, and shared
  internal LLM endpoints.
- Enterprise/commercial boundaries, if they are added later, should cover
  shared-deploy operations such as SSO, RBAC, artifact storage, audit export,
  centralized LLM quotas, Helm/reference deploy, and support. Safety,
  validation, diagnostic quality, and the single-user local workflow stay in
  core.
- Future-compatible web work must have immediate local-first value. Do not add
  speculative tenant scaffolding such as `actor`, `tenant_id`,
  `deployment_mode`, actor-scoped job-store APIs, or persistent shared stores
  until a concrete shared-deploy design exists. Prefer present-day safety
  boundaries that would also be correct later.

## Priority Bands

Use these bands when choosing work. The numbered Near-Term sections below stay
as domain backlogs; the priority band decides what should be pulled first when
items compete.

### P0 - Safety And Contracts

Do first when touched, because these items protect the trust boundary or unlock
multiple later changes:

- Product positioning contract: keep public docs and in-product help aligned on
  local-first Impala production triage, Recent Scan as the flagship workflow,
  Query ID as secondary, Query Optimizer as separate/read-only, and LLM wording
  downstream of Python-owned facts.
- Upstream alignment tracker: keep
  [upstream-impala-ai-analyzer.md](upstream-impala-ai-analyzer.md) current when
  IMPALA-14953 changes materially enough to affect Query Doctor scope.
- Browser and trusted-report safety: keep typed raw-free view models,
  presenter-owned display strings, consolidated trusted artifact loading,
  export of validated safe Markdown, and browser-safety tests for any new
  dynamic Details content.
- Local web hardening: package-owned static assets, tighter Content Security
  Policy, local POST origin/host checks, no-store/security headers, and bounded
  local job cleanup.
- Report and optimizer trust contracts: validator allowlists, trusted artifact
  predicates, marker validation, no-echo behavior, and strict
  recommendations-only or `no_rewrite` fallbacks.
- Deterministic-first / no-LLM-capable posture: keep report, optimizer, web
  action labels, config, demo, and docs aligned so the core selected-case flow
  remains useful without a model route, while LLM-backed wording stays an
  optional explicit extension.
- Provider-neutral analyzer contracts for the current Cloudera Manager path:
  canonical context keys/headings, legacy `cm_*` fallbacks, metrics reads by
  abstract `signal_id`, source provenance coverage wording, and legacy-safe
  compatibility.
- Profile dialect and evidence-tier contract: detect `classic_text_profile`,
  `classic_json_profile`, `classic_thrift_profile`,
  `experimental_profile_v2`, or `unknown` before profile-derived analysis;
  classify profile signals as `strong`, `medium`, `context_only`, or
  `unsupported`; map Impala-provided counter significance/stability labels into
  evidence quality when available; and prevent unknown or partially mapped
  dialects from driving primary bottleneck claims.

### P1 - Diagnostic Quality

Pull next, because these improve the core product value on current Impala and
Cloudera Manager deployments:

- Details evidence flow: deterministic findings first, concise evidence
  quality, limitations, runtime context, metadata status, and action routing.
- Runtime-context quality: collection coverage, observed and correlated
  signals, admission/pool context, host-tail diagnostics, and sanitized real
  fixtures.
- Direct Impala quality: sanitized real fixtures for fresh daemon profile
  layouts, current-upstream Kubernetes Impala batches, Prometheus metric
  coverage, profile resource/timing action cards, and safe limitation wording
  when metrics, events, or metadata are unavailable.
- Profile dialect quality: add fixtures for classic text, classic JSON,
  classic Thrift, and experimental profile-v2 layouts; map only explicitly
  supported sections; and add regression tests that keep profile-v2 limitations
  visible without inventing missing instance-level evidence.
- Metadata and stats diagnosis: structured stats/query-shape facts, partition
  and join/filter column coverage, bottleneck calibration, and unknown-rate
  measurement on real batches.
- Workload-level diagnosis: raw-free fingerprint grouping, frequent-short
  workload triage, baseline/regression detection, and action outcome tracking.
- Upstream compatibility preparation: design a narrow profile JSON /
  parser/redactor compatibility plan and golden-profile quality harness before
  implementing any adapter.
- Multi-engine preparation: shape the engine fact contract from implemented
  Impala behavior so a later second engine can publish supported, not observed,
  and unknown facts without pretending every engine has Impala counters.
- Experimental second-engine discovery: choose one named analytical SQL engine
  from available artifacts or design-partner demand, then build a fixture-only
  parser/fact spike that exists to test the engine fact contract. It must not
  add public support claims, a runtime engine selector, or browser/report output
  before safety tests exist. Query Doctor does not own final execution-engine
  selection.
- Optimizer usefulness: fresh optimizer funnel measurement, expression-projection
  predicate pushdown, UNION ALL branch predicate pushdown, narrow Python-owned
  recipes for repeated expensive ETL shapes, and action-quality feedback.
- Diagnose and Recent results usability: keep the main page focused on which
  queries deserve attention, not on explaining every technical cause inline.
  The default result table should prioritize priority, short analyst-readable
  summary, duration, user/owner context, and a clear Details path. Table
  stats, metadata status, score reasons, result-group explanations, and other
  "why" evidence should move to Details or an explicit collapsed row/context
  disclosure unless they are needed to choose the next row to open.
- Web UI audit remains desktop-first. The next cleanup slices should keep
  reducing first-screen noise without hiding primary decisions: make the
  standalone Query Optimizer page feel less empty, continue simplifying Recent
  result rows, and re-audit Details, Running, Help, and Outcomes after each
  visible workflow change. Mobile polish is secondary until the desktop
  analyst flow is stable.

When choosing P1 product-growth work after required P0 contract work, target
these product areas first. The first workload-diagnostics slice has landed, so
the next product-growth work should stabilize it on real sanitized batches
before adding broader grouping dimensions.

1. Validate workload grouping, Frequent short, regressed workload labels,
   action queues, and outcome rollups on sanitized real batches.
2. Pool and admission diagnostics as analyzer-owned first-class causes.
3. Safe query-type grouping for Recent results, derived from deterministic
   facts rather than raw SQL.
4. Direct Impala daemon depth, fixtures, Prometheus coverage, and profile action
   cards.
5. Metadata and stats depth for join/filter column coverage, freshness,
   selectivity mismatch, and bottleneck calibration.

## Impala Strategic Goals

These goals define the next Impala diagnostic-quality direction. They deepen the
current Impala product wedge and do not add a new engine, provider, support
claim, or raw-profile analysis surface.

1. Bring direct Impala to a reliable diagnostic-quality level for the existing
   bounded Recent, Running, and Known Query ID workflows. Direct Impala should
   degrade safely when optional JSON profiles, `/profile_docs`, `/admission?json`,
   Prometheus metrics, metadata, or Cloudera Manager-only events are unavailable,
   and Details/reports should explain that coverage in raw-free limitation
   wording. Representative direct-source calibration should add
   `--fail-on-direct-source-readiness-gaps` to
   `scripts/audit_impala_coverage_gaps.py` so unknown source provenance or
   optional-source states block readiness claims while explicit unavailable or
   not-configured limitations remain acceptable.
2. Expand profile evidence only through explicit dialect and section mappings.
   Classic JSON, classic Thrift, and experimental/profile-v2 profiles are separate
   dialects, not classic text profiles with different field names. Unknown,
   unsupported, or partially mapped sections must stay below primary-bottleneck
   promotion until fixtures, parser support, evidence tiers, and safety tests
   prove the mapped interpretation.
3. Improve primary-bottleneck coverage by adding deterministic evidence, not by
   strengthening prose. Admission, memory pressure, scan skew, client-fetch tail,
   resource/timing facts, metadata/stats, and workload-repeatability work should
   reduce `unknown` only when analyzer-owned facts support the narrower claim.
4. Keep Details as an analyst decision page. The visible path should explain why
   the query deserves attention, where to inspect, what supported change
   direction to try, and how to verify a comparable rerun. Collector internals,
   profile-section status, metric-source status, and raw fact categories belong in
   limitations or collapsed diagnostics unless they directly support that
   decision loop.
5. Strengthen Impala metadata and stats diagnosis. Join/filter column stats,
   partition coverage, stale or missing stats, metadata divergence, and
   query-shape versus runtime separation should help route a case toward stats
   maintenance, SQL-shape review, runtime follow-up, or `unknown` without exposing
   raw metadata. Representative calibration should run
   `scripts/audit_stats_diagnostics.py <batch_summary.json> --fail-on-stats-readiness-gaps`
   before broadening stats-readiness claims on real batches.
6. Make runtime, admission, and pool context first-class supporting evidence
   without turning context into root-cause proof. `/admission?json`, Cloudera
   Manager metrics/events, Prometheus metrics, and concurrent workload signals can
   corroborate query-specific findings, but they must not replace selected-query
   admission waits, profile facts, metadata facts, or mapped runtime evidence.
7. Grow the sanitized fixture corpus and representative real-batch audit loop.
   Fresh daemon profile layouts, JSON/profile-v2 edge cases, incomplete or
   cancelled nodes, counter aliases, admission gaps, Prometheus partial coverage,
   and mixed-signal batches need fixtures or path-free aggregate audits before
   support wording or scoring behavior is broadened.
8. Keep every new Impala fact on the raw-free trust path: bounded/redacted
   collection, analyzer-owned structured fact, report-safe renderer wording, and
   validation tests. Browser-visible UI and trusted reports must not expose raw
   SQL, raw profile text, raw metadata, local paths, hostnames, artifact names, or
   model/runtime internals.
9. Stabilize the workload-level Impala loop. Recent should rank individual
   expensive queries and also surface repeated workload fingerprints,
   frequent-short groups, regressions, owner/pool/admin digests, action queues,
   and action outcome history from raw-free deterministic facts. Representative
   calibration should run
   `scripts/audit_workload_diagnostics.py <batch_summary.json> --fail-on-workload-readiness-gaps`
   so repeated groups without usable details, representatives, regression
   baselines, workload-history status, or comparable verification guidance block
   workload-readiness claims. Runs that validate outcome feedback should add
   `--action-outcomes <action_outcomes.jsonl> --fail-on-action-outcome-readiness-gaps`
   so action queues and detail hints prove they carry safe aggregate feedback
   summaries and measured result evidence, not only all-`unsure` comparable
   reruns, without embedding local outcome files in batch summaries.
10. Prepare future engine contracts by making Impala facts explicit, not by
   generalizing prematurely. Future engines should inherit the ability to publish
   `supported`, `not_observed`, and `unknown` facts from proven Impala contracts,
   without fake adapters, runtime engine selectors, or public support claims.

### P2 - Expansion Readiness

Do after P0 contracts are stable and P1 diagnostic quality is useful enough on
real Impala workloads:

- Thin source-family interfaces over real existing paths: `ProfileSource`,
  `QueryDiscoverySource`, `MetricsSource`, and `EventSource`.
- Direct Impala daemon source follow-up: add real fixture coverage, profile
  action cards, and a normalized engine fact contract before broadening beyond
  the current bounded Recent/Running/Known Query ID workflows.
- Profile JSON compatibility adapter only after a stable upstream contract,
  dialect detection, parser fixtures, redaction tests, evidence-tier mapping,
  and raw-free fact mapping exist.
- Prometheus-style metrics source follow-up: add sanitized real Ambari/Hadoop
  fixtures, strengthen direct Impala workflow coverage, and add additional
  allowlisted metric profiles only with tests.
- Engine profile-fact contract refactor before supporting any second SQL engine.
- Fixture-only second-engine spike may run in parallel with the engine
  fact-contract refactor when it is explicitly non-product behavior and cannot
  affect the default Impala workflow.
- Supported second-engine product path only after parser/fact fixtures,
  collection contracts, metadata allowlists, browser/report safety tests, and
  a documented support gap matrix exist.
- Storage/table-format facts only after provider and engine boundaries
  stabilize.

### Deferred - Not Current Support

Keep these out of implementation plans until their explicit readiness signal is
met: shared deployment, multi-tenancy, public second-engine support, plugin
framework, generic SQL execution, broad package reorganization, and fake
adapters.

## Impala Implemented-Signal Backlog

These are roadmap items from already-visible Impala or adjacent Cloudera
diagnostic surfaces. They are not current Query Doctor behavior unless the
status says an analyzer baseline already exists.

| Item | Priority | Why it matters | Safety rule | Status |
| --- | --- | --- | --- | --- |
| Profile counter stability and versioned aliases | P0 | Impala counter significance labels and versioned counter names can reduce name-based guesswork. | Stability and aliases are eligibility only; deterministic interpretation, thresholds, query-specific support, and raw-free summaries are still required. | Bundled registry exists for client-fetch and spill/scratch counters; optional direct `/profile_docs` probing can write a safe allowlisted context for interpreted counters. Broader aliases and registry refresh tooling remain future work. |
| Profile input dialect and Web UI JSON export | P0 | The Impala Web UI can export profiles as text, Thrift, or Json; JSON may become a less fragile ingestion path than text parsing when fixtures prove it. | Unknown, partial, or experimental dialects fail closed for primary bottleneck claims; no raw profile rendering. | Dialect detection, limited classic JSON mapped-counter ingestion, and opt-in direct JSON endpoint probing with text fallback exist; fuller JSON/Thrift/profile-v2 mapping remains future work. |
| Admission context collector | P1 | `/admission?json` exposes pool queue/running context and pool stats that can explain workload pressure around a selected query. | Collect only bounded aggregate facts such as queue present, queue-time bucket, pressure, and freshness; never expose raw queued/running lists or promote admission without query-specific wait/result evidence. | Opt-in direct collection exists for bounded aggregate context. Missing old endpoints are non-fatal, analyzer facts re-whitelist the context, and selected-query admission facts still gate `runtime_admission` promotion. |
| Storage-aware scan diagnostics | P1 | HDFS locality, remote HDFS, S3, ADLS/ABFS, Ozone, and remote-read data cache have different scan semantics. | Derive a safe `storage_context`; do not expose paths, object URIs, credentials, hosts, or treat object-store remote reads as HDFS locality failures. | Baseline exists: analyzer derives safe storage family from metadata location scheme when available, emits aggregate `storage_context`, and keeps object-store HDFS/DataNode locality signals from promoting HDFS findings. Data-cache counters, finer remote-HDFS semantics, and richer S3/ADLS/Ozone interpretation remain future work. |
| Resource-trace CPU/I/O facts | P1 | `RESOURCE_TRACE_RATIO` can add Per Node Profiles metrics for CPU usage, I/O wait, disk throughput, and network throughput when enabled. | Treat absence as unknown; host-wide throughput is context-only unless mapped to selected-query evidence, and raw per-node or host rows must not reach UI/reports. | Baseline exists: analyzer parses safe aggregate CPU, disk, and network resource-trace samples into `context_only` facts, treats absence as `unknown`, and never promotes these facts to primary-bottleneck routing. Selected-query isolation and stronger CPU-vs-I/O diagnosis remain future work. |
| Runtime filter diagnostics | P1 | EXPLAIN and PROFILE can expose runtime filter producer/consumer and routing details. | Do not claim missing or late runtime filters as a root cause without deterministic producer, consumer, target scan, timing, and spill-context evidence. | Baseline exists: analyzer emits raw-free context counts for runtime-filter plan arrows, filter IDs, aggregate producer/consumer pairing coverage, aggregate target-scan mapping coverage, routing/final table aggregates, arrival gaps/waits, and BloomFilterBytes counters. These facts remain `context_only`; missing/late filter diagnosis and root-cause promotion remain future work. |
| Skew detection refinement | P1 | Stronger skew findings should prefer multi-host, long-running phases with Max Time vs Avg Time imbalance and corroborating bytes, memory, or network spread. | Avoid timing-ratio-only findings and keep aggregate-only skew context below primary-bottleneck promotion. | Scan-skew baseline refined: selected evidence now records group host count, corroborating metric count, phase runtime, and Max/Avg execution ratio; primary `runtime_skew` routing requires a long-running imbalanced phase when timing is available. Exchange/data-movement primary routing now requires material mapped `EXCHANGE` elapsed time; exchange-partition skew hardening remains future work. |
| Statistics UNKNOWN normalization | P1 | Impala uses placeholders such as `-1` for unavailable table or column stats. | Normalize placeholders to `unknown` or `missing`; trusted reports must not present placeholder values as literal business facts. | Baseline exists: `SHOW TABLE STATS` / `SHOW COLUMN STATS` parsers, analyzer metadata rendering, metadata digest, and web details normalize stats placeholders before browser/report surfaces. Keep this as a regression rule for future metadata sources. |
| Observability health-check parity matrix | P2 / research | Cloudera Observability health checks are a useful benchmark for missing/corrupt stats, spilling, slow scan/hash join/planning/materialization/sorting, and skew categories. | Do not copy health-check wording blindly; map each category to Query Doctor support status, deterministic evidence required, safe report wording, and unsupported/unknown gaps. | Future research/backlog matrix. |
| Built-in Impala AI analyzer watchlist | P2 / research | Upstream native Impala AI profile-analysis work shifts Query Doctor away from a one-profile AI button. | Keep positioning around local-first Recent triage, Cloudera Manager integration, bounded metadata/context, deterministic analyzer facts, validation, and raw-free reports. | Existing upstream tracker; keep current when IMPALA-14953 changes materially. |

## Next Pull Queue

This is the short ordered queue for the next roadmap pulls. Pull a different
item first only when the touched area has a direct P0 safety or contract risk.

1. Validate the implemented profile evidence-tier gates against sanitized real
   profiles before broadening upstream profile compatibility. The current
   client-fetch-tail, runtime-admission, memory-pressure, scan-skew,
   exchange/data-movement, and storage-aware scan gates are implemented; the
   next proof points are longer current-upstream Kubernetes Impala batches,
   real-case calibration, and old-source compatibility, not stronger wording.
2. Run the live owner-raw D3 front-door validation gate once a real proxy or
   ingress exists. The application-side contract, synthetic front-door smoke,
   policy simulator, fail-closed startup/source checks, kill switch, audit
   wording, and public-safe runbook are in place; the remaining proof is real
   TLS/auth, direct-network blocking, header stripping/replacement,
   identity-to-owner mapping, and raw-free audit evidence in the target
   deployment.
3. Continue the desktop Web UI audit with the standalone Query Optimizer page:
   tighten the first screen around the SQL input, Analyze action, and
   scope/safety disclosure without weakening the no-echo or read-only trust
   boundary.
4. Keep the IMPALA-14953 upstream tracker current and turn stable upstream
   profile JSON/parser/redactor signals into a narrow compatibility plan before
   coding an adapter.
5. Extend the implemented engine fact contract only where fixtures prove a
   gap. Keep Impala consumer migration behind parity evidence, Trino within its
   bounded local production lanes, and Spark compact-only. Use
   [engine-support-gap-matrix.md](engine-support-gap-matrix.md) for current
   promotion gates rather than restarting the completed initial Trino spike.
6. Repeat the Diagnose results-table audit after sanitized real-workload and
   operator feedback. The default Recent rows now answer "which queries are bad
   and worth opening" with one short classification, priority, duration,
   owner context, and Details; the repeated-workload table keeps only priority,
   p95, total impact, top owner, and Details alongside its summary. Keep score
   reasons, p50, pool, bottleneck, and other supporting context in Details or
   explicit disclosures unless feedback shows they are required for row choice.
7. When selected-case report or optimizer UI/help is touched, move visible
   action wording toward neutral `Report` and `Query optimizer` labels while
   keeping backend status explicit (`Python-owned`, `LLM-backed`, or
   `no_llm=true`). Do not hide trust-relevant backend state and do not promote
   LLM output as evidence.
8. Finish the remaining provider-neutral runtime contract tail: report-validator
   heading tests when aliases or headings change, plus compatibility-safe
   UI/presenter naming cleanup.
9. Keep source-provenance wording compatibility-safe across Details, trusted
   reports, and future UI/presenter naming cleanup, including explicit direct
   Impala coverage for profile, optional Prometheus metrics, unavailable events,
   and metadata status.
10. Stabilize the new workload-diagnostics surfaces on sanitized real batches:
   repeated/frequent-short/regressed groups, admin digest, action queue,
   workload detail pages, and action outcome rollups.
11. Use `scripts/audit_optimizer_funnel.py`,
   `scripts/audit_optimizer_plain_shapes.py`, and
   `scripts/audit_optimizer_representative_shapes.py` on the latest broad
   Recent smoke to choose the next optimizer slice from repeated workload
   groups and no-recipe shape families. Start with the largest repeated family
   where analyzer facts and validation can prove a safe Python-owned transform;
   otherwise improve family-specific review guidance rather than producing a
   trusted SQL draft.
12. Continue replacing report-side stats/query-shape extraction with structured
   analyzer facts and validate the result on real sanitized batches.
13. Add safe query-type grouping only after deterministic classifier facts can
   explain unknown or unsupported shapes without reading raw SQL.

## Dependency And Readiness Rules

Use these rules to keep roadmap work ordered. They override local convenience
when a tempting implementation would skip a contract boundary.

- Provider decoupling order is fixed: canonical context keys/headings,
  report-validator snapshots, metrics by `signal_id`, per-case provenance,
  and thin source-family interfaces over the current Cloudera Manager, direct
  Impala, and Prometheus paths before broader provider expansion.
- P2 provider work must not start until the P0 provider-neutral contracts it
  depends on are in place. Do not add placeholder provider packages; expand
  provider boundaries only from implemented Cloudera Manager, direct Impala, and
  Prometheus paths.
- Profile-derived classification order is fixed: detect dialect, map supported
  sections, assign evidence tiers, then classify. Unknown profiles must not
  produce primary bottleneck classification. Experimental profile-v2 sections
  may produce only limited findings until each section has explicit mapping,
  fixtures, and safety tests. Scan-skew and backend-tail claims require
  per-instance or equivalent aggregate evidence, not just operator totals.
- Second-engine exploration and second-engine support are different gates.
  Exploration may start earlier when it is fixture-only, scoped to shaping the
  engine fact contract, does not add a public support claim, does not add fake
  adapters, and cannot affect default Impala workflows.
- A supported second SQL engine must wait until all of these are true:
  `case_primary_bottleneck = unknown` is below roughly 20% on a representative
  100+ case real Impala batch or there is a design-partner workload that proves
  cross-engine urgency, workload fingerprinting and baselines work on real data,
  action outcome tracking has comparable-rerun verified applied/not-applied
  records, direct Impala diagnosis is stable on non-Cloudera-Manager
  deployments, a real design partner or real artifact set identifies a
  specific second engine, and an
  engine profile-fact contract already exists from implemented behavior.
- Shared deployment work must wait for an explicit shared-deploy product
  decision, a real design partner, and a design for authentication, ownership,
  audit, persistence, and operational support. The narrow owner-raw D3
  front-door contract is not a general shared-deploy support claim and must not
  be broadened until its live validation gate passes in a real target
  deployment.
- Keep P0 narrow. An item belongs in P0 only when it protects browser/report
  safety, validation/trust contracts, no-echo/raw-free behavior, or a schema
  boundary that unlocks several later changes. Product-quality improvements
  without that contract effect belong in P1.

## Near-Term Priorities

### 1. Local Web Hardening And Team Workflow

Make the local web UI easier to maintain and safer to operate without turning
it into a shared service.

- Preserve package-owned CSS/JavaScript, external theme bootstrap, explicit
  static content types, and the CSP without inline script/style allowances.
  Keep no-store caching, Host and POST Origin checks, `Permissions-Policy`, and
  focused browser-safety tests as regression guards. Further CSRF/session
  changes need an explicit shared-deployment design.
- Keep supported deployment guidance current in README and SECURITY guidance:
  single-user, local-first, behind the user's own configured credentials and
  local access. Call out that binding the current server as a shared corporate
  service is not supported.
- Document team usage patterns before building multi-tenancy: shared reports
  repository, CI scheduled batch scans, shared internal LLM endpoint, team
  jumpbox or remote devbox, and version pinning.
- Preserve the implemented trusted-report Markdown download boundary when
  improving sharing workflows: current markers, path safety, fixed filenames,
  and raw-free content remain required.
- Verify and document case deep links such as `/batch/case/<id>` for users who
  open the same `batch_summary.json`; keep request paths unable to choose local
  files.
- Keep the existing terminal-job TTL and count cap bounded. Persistent job
  history, multi-user ACLs, and shared artifact ownership remain separate
  design work.
- Keep browser rendering on typed raw-free view models. New Details blocks
  should accept presenter-owned dataclasses with safe primitive fields or
  `SafeHtml`, rather than raw case/facts dictionaries.
- Keep trusted artifact access behind `query_doctor.web.trusted_artifacts`.
  Validated reports and optimizer outcomes should reach UI code only after
  marker checks, path safety checks, and display redaction in that boundary.
- Preserve existing request IDs and raw-free audit correlation. Broader
  cross-job/subprocess tracing remains follow-up work and must not expose
  source identifiers, viewer values, or raw child output.

### 2. Details Usability And Evidence Flow

Make Details efficient for Recent queries, Running now, and Known Query ID
workflows.

- Keep the visible page question-oriented: why this query deserves attention,
  where to inspect it, what supported change direction to try, and how to
  verify the change.
- Keep deterministic findings first, but phrase them as decision support rather
  than as a dump of collector-source facts.
- Make evidence quality, runtime context, Cloudera Manager metrics, Cloudera
  Manager events, metadata status, and limitations easy to scan.
- Remove duplicated or low-value blocks when they make the page harder to use.
- Keep pipeline status, profile sections, metric-provider details, and broad
  fact tables in collapsed Diagnostics unless they directly support the
  verdict, recommendation, verification step, or an explicit limitation.
- Keep all dynamic browser text behind presenter/display safety helpers.
- Do not render raw artifacts or arbitrary docs in the browser.
- Post-release Details audit follow-ups: align the Recent Results `Finding`
  wording with Details verdict wording, replace vague score-summary copy such
  as "positive score from detailed analyzer reasons" with a signal-class
  summary, and evaluate whether local `batch_summary.json` still needs to store
  `case_dir` or can move to a narrower server-owned case reference without
  disrupting Details, report, or optimizer routes.

### 3. Runtime Context Quality

Improve how runtime context supports diagnosis without overclaiming.

- Show collection status, coverage, observed signals, correlated signals,
  context-only signals, and limitations.
- Keep Details verdict and diagnostic facts current so Details separate strong
  analyzer-backed findings, plausible follow-up checks, context-only runtime
  signals, unknown evidence, metadata coverage, and stats evidence without
  duplicating the same triage cards in multiple visible blocks.
- Keep Cloudera Manager metrics and events as normalized analyzer facts.
- Treat duration and runtime context as supporting evidence unless direct facts
  support a stronger claim.
- Add more sanitized fixtures for long-running writer, scan, exchange,
  join-heavy, metrics, and events cases.
- Add fingerprint and workload baseline comparisons so the analyzer can
  distinguish chronic bad queries from regressions without exposing raw SQL.
- Improve profile-to-plan mapping across fragments, operators, tables, joins,
  exchanges, and write paths so findings point to the supported execution
  location instead of a generic symptom.
- Stabilize the implemented bounded parser for already-provided Impala EXPLAIN
  evidence across representative sanitized layouts. The current baseline
  covers classic, legacy, boxed, and modern fragment host/instance syntax with
  operator-specific bare partition attributes. It publishes raw-free typed
  optimizer-intent and estimate facts plus conservative structural-link
  coverage in the analyzer result (`analysis.json` when requested); it does not
  feed scoring, recommendations, primary diagnosis, reports, browser surfaces,
  or platform contracts. An accepted external artifact remains explicitly
  unbound to the runtime profile even when recognized operators overlap.
  Synthetic fixtures now cover minimal-, standard-, extended-, and
  verbose-like detail, current fragment resource headers, `HDFS partitions`
  scan lines, and stored statistics availability without retaining relation or
  column names. Validate those layouts against representative sanitized
  current-upstream artifacts before widening fact extraction. Treat bounded
  extraction of an embedded profile `Plan:` section, including source
  precedence and truncated/duplicate-section behavior, as a separate slice.
  Require verified same-snapshot provenance and stronger runtime correlation
  before any diagnostic promotion. Query Doctor must not submit SQL or generate
  EXPLAIN.
- Improve metadata/stats quality facts for stale or missing table stats, column
  stats on join/filter columns, partition coverage, selectivity mismatch, and
  real-fixture validation for stats-present-but-not-explanatory cases.

Current workload-level diagnostics baseline and follow-ups:

- Current-scan workload groups are implemented with `schema_version: 1`, safe
  `wf_` fingerprints, raw-free aggregate fields, result groups for repeated,
  frequent-short, and regressed workload fingerprints, plus workload detail
  pages, admin pool/owner digest, analyst action queue, and action outcome
  rollups.
- Frequent short is now a default Recent-result group when the web scan has no
  explicit minimum-duration filter. An empty Minimum duration keeps long-query
  and repeated-short workload patterns available; setting a duration narrows
  the scan to longer-running queries. The group ranks repeated fingerprints by
  current-scan impact while keeping bounded scan caps and raw-free limitations
  visible.
- Local workload history and action outcomes remain local, raw-free, and
  explicit. Do not expose local history paths, raw SQL, raw profiles, raw
  metadata, optimizer source SQL, column lists, predicates, literals, aliases,
  comments, host/daemon identifiers, raw artifact names, or free-form analyzer
  wording.
- Follow-up work is to validate group ranking and limitations on sanitized real
  batches, improve baseline/regression calibration, add safe query-type
  grouping (#67) from deterministic classifier facts, add stable demo/operator
  notes for synthetic scenarios (#47), and keep the expanded synthetic demo pack
  aligned with current workload action queue, local action outcomes,
  primary-bottleneck, runtime-evidence, and source compatibility diagnostics
  (#89).
- Keep LLM group summaries out until deterministic group facts and
  browser-safe validation boundaries exist.

Current pool/admission baseline and follow-ups:

- Keep the existing `runtime_admission` `case_primary_bottleneck` label rather
  than adding a parallel admission label. Promote it only when analyzer-owned
  query-specific facts support admission wait or admission result evidence,
  such as direct profile counters, profile timeline facts, or Cloudera Manager
  query attributes.
- Keep pool/admission actions deterministic and non-LLM by default: rebalance a
  query class, review pool sizing, or collect a bounded workload window before
  claiming SQL-shape or stats work is the next action.
- Do not infer admission pressure from duration alone. Cluster pool saturation,
  runtime metrics, events, concurrent workload signals, and Prometheus gauges
  remain supporting context unless query-specific analyzer facts prove the
  bottleneck. A stats or SQL-shape finding may still coexist with
  `runtime_admission` and must not be suppressed.

Provider-neutral runtime context cleanup:

- Treat source identity as data, not analyzer schema. Canonical
  `Runtime Metrics Facts`, `Runtime Metrics Correlation`, and
  `Cluster Runtime Context` headings are the current analyzer/report contract;
  keep legacy `CM Metrics` heading load fallbacks for old artifacts. Analyzer
  runtime readers now use canonical `query_context`, `metrics_context`,
  `metrics_facts`, and `metrics_correlation` keys with `cm_*` fallbacks.
  Report contract digests also expose `metrics_facts` and
  `metrics_correlation` aliases while preserving legacy digest keys. Runtime
  metric collectors now write abstract catalog `signal_id`s next to
  provider-specific `id`s, and analyzer metric facts read via `signal_id` with
  legacy ID fallbacks. Source Provenance uses explicit Cloudera Manager and
  Prometheus labels for collected runtime metrics and generic `Runtime metrics`
  wording when metrics are absent or unknown. Report guardrails read
  provider-neutral Runtime Metrics Correlation headings when checking
  context-only signals. Web Details facts loaders expose provider-neutral
  runtime-metrics aliases and state builders consume them, while preserving
  existing `cm_*` keys, wrapper names, and artifacts as legacy load fallbacks.
  Web Details source limitations and trusted Python report evidence bullets now
  consume analyzer Source Provenance facts for explicit `none`, `unavailable`,
  and partial coverage wording on direct Impala cases. The remaining migration
  is compatibility-safe UI/presenter naming cleanup.
- Keep report-validator heading allowlists and snapshot tests in sync with any
  heading or alias change, so the trusted report contract cannot drift silently.
- Keep any later metric-catalog expansion source-backed: new metrics must write
  a catalog `signal_id` plus a provider-specific `id`, and analyzer reads must
  preserve legacy fallback for old corpora.
- Use analyzer source provenance for raw-free UI/report coverage wording and
  explicit `none`, `unavailable`, or partial-coverage limitations. Keep any
  later persistence or snapshot contract changes narrow and compatibility-safe.
- Introduce source-family interfaces only when they wrap real current paths:
  `ProfileSource`, `QueryDiscoverySource`, `MetricsSource`, and `EventSource`,
  with Cloudera Manager wrappers over existing helpers. Avoid one broad
  provider object, fake implementations, or placeholder packages.

Profile dialect and counter evidence acceptance details:

Implemented baseline:

- profile dialect detection and primary-bottleneck policy are in place;
- incomplete/cancelled exec-node guardrails are in place for mapped profile
  signals;
- client-fetch-tail facts and primary routing are in place for mapped
  `ClientFetchWait*` counters, with Query Timeline fetch and
  `GetInFlightProfileTimeStats` kept as context unless corroborated;
- runtime-admission facts and primary routing are evidence-tiered: selected
  query admission result/wait and profile/query-timeline admission facts can
  promote `runtime_admission`, materially conflicting waits stay context-only,
  and pool, cluster, metric, event, or duration-only signals cannot promote it.
- memory-pressure facts are evidence-tiered: selected-query non-zero
  spill/scratch counters are the current strong evidence path, while memory
  estimates, reservations, peak-memory footprints, daemon metrics, and runtime
  context remain context-only.
- scan-skew facts are evidence-tiered: per-instance scan bytes, bytes-read,
  rows, or mapped equivalent spread are the current finding path; primary
  `runtime_skew` promotion requires a long-running phase with material Max/Avg
  execution imbalance when timing is available. Backend data-skew summaries
  without those mapped fields remain context-only.
- exchange/data-movement promotion requires mapped `EXCHANGE` operator context
  and large `TotalBytesSent`; storage/HDFS promotion requires mapped
  scan/storage operator context and large `TotalBytesRead`.

Open P0 analyzer status:

- No separate next P0 analyzer slice is currently queued. The remaining
  profile work is validation and P1 diagnostic-quality depth unless a new
  trust-boundary risk appears.
- Keep the rules below as acceptance criteria for future profile-derived
  evidence work.

1. Dialect and safety:
   - profiles must be classified as `classic_text_profile`,
     `classic_json_profile`, `classic_thrift_profile`,
     `experimental_profile_v2`, or `unknown` before profile-derived analyzer
     work begins;
   - unknown or partially mapped dialects need a safe limitation;
   - raw profile payload, local paths, hostnames, users, and artifact names must
     stay out of browser-visible text and trusted reports.
2. Evidence tiers:
   - `strong`: query-specific profile evidence from a mapped dialect section,
     with required corroborating fields for that finding family;
   - `medium`: query-specific evidence that is mapped but missing one
     corroborating dimension, suitable for follow-up direction but not a root
     cause claim;
   - `context_only`: runtime, estimate, or aggregate context that can support a
     recommendation only when stronger analyzer facts exist;
   - `unsupported`: missing, unknown, or unmapped evidence that must not
     influence primary bottleneck classification.
3. Bottleneck promotion rules:
   - admission wait is strong only from query timeline, admission wait, or
     admission result facts for the selected query;
   - memory pressure is strong with explicit non-zero spill or scratch counters,
     while estimates and reservations alone stay context-only;
   - scan skew can become primary only with per-instance scan bytes,
     bytes-read, rows, or a mapped equivalent spread section plus a
     long-running imbalanced phase when timing is available;
   - exchange wait, network, and inactive timers require mapped exchange context
     before they can exceed medium evidence;
   - disk I/O wait requires bytes and scan/storage operator context before
     promotion;
   - mapped `ClientFetchWait*` counters can strongly support a client-fetch-tail
     finding only when they are a large share of selected-query duration;
     primary bottleneck routing requires that finding to be the top elapsed
     runtime finding. They are not a Hue, network, BI tool, or client root-cause
     claim by themselves.
4. Incomplete/cancelled node guardrail:
   - baseline implemented: mapped profile-wide and per-node incomplete or
     cancelled signals now emit raw-free Exec Node Completeness facts and block
     affected row/cardinality promotion;
   - detect mapped profile signals that an exec node may be incomplete,
     closed-early, or cancelled;
   - downgrade cardinality, row-count, scan-selectivity, and
     runtime-filter-effectiveness conclusions for affected nodes;
   - never infer an empty table, meaningful zero-row selectivity, or runtime
     filters filtering everything from incomplete node evidence.
5. Runtime filter and storage limitations:
   - treat HDFS runtime-filter counters as interpretable only when the scan
     section and node-completion state are mapped;
   - keep Kudu runtime-filter effectiveness `unknown` or `unsupported` until a
     Kudu-specific counter contract and fixtures exist;
   - downgrade raw throughput interpretation for mixed data-cache and
     remote/object-store I/O, and do not claim remote storage slowness without
     source-specific evidence.
6. Heuristic-only backlog:
   - keep exchange-partition skew as heuristic-only until receiver distribution
     counters or equivalent aggregate evidence exists;
   - treat `GetInFlightProfileTimeStats`, huge profiles, and high `mt_dop`
     profile overhead as profile-serialization context, not client-fetch root
     cause proof;
   - keep planner mode / Calcite estimate-drift awareness as P2 context until
     mapped fixtures can compare plan mode, estimates, and runtime rows.

Workload stabilization acceptance details:

1. Real-batch workload validation:
   - compare repeated, frequent-short, and regressed grouping against sanitized
     real batches;
   - record false positives and false negatives in raw-free terms;
   - keep singleton or noisy groups out of the primary table unless there is a
     regression, status, spill, stats, or runtime signal.
2. Safe query-type grouping:
   - derive type labels from deterministic classifier facts only;
   - keep unknown or unsupported types explicit;
   - do not leak raw SQL, identifiers, predicates, or literals.
3. Action outcome learning:
   - keep recorded/applied/outcome labels whitelisted and local;
   - use outcomes to validate recommendation families rather than to inflate
     confidence.

### 4. Query Optimizer Usefulness

Keep optimizer trust strict while making useful outcomes more common.

- Keep expensive production queries as the primary optimizer target. Do not
  optimize for easy low-value rewrites just because they are easier to draft.
  The product value comes from diagnosing and improving costly workload shapes.
- Treat trusted SQL drafts as one optimizer outcome, not the only successful
  outcome for expensive queries. A useful optimizer result may be a validated
  SQL rewrite, a stats action, a query-shape recommendation, a data/layout
  recommendation, or a clear "needs deeper facts" limitation.
- Separate "expensive" from "rewriteable": duration, exchange volume, scan
  volume, skew, and cardinality anomalies identify potential value, but they do
  not prove a safe automatic SQL rewrite exists.
- Add a rewriteability taxonomy alongside optimization score: expensive query,
  likely stats problem, likely query-shape problem, currently Python-draftable,
  promising new recipe family, and human-review-only.
- Do not use random production-query hunting as the main demo strategy. A
  credible demo should use a reproducible expensive workload shape with
  analyzer evidence, deterministic recipe coverage, and production evidence
  that the shape occurs.
- Develop optimizer work as facts-first recipes: analyzer-owned facts should
  first prove predicate origin, projection mapping, CTE boundaries,
  stats-vs-query context, and competing bottlenecks; only then should a narrow
  Python-owned recipe produce a trusted SQL draft.
- Treat Optimization Candidate as a funnel, not a promise of SQL rewrite:
  candidate detected, rewrite recipe detected, SQL draft safe to attempt,
  trusted SQL draft produced.
- Make user-visible optimizer states explain why a SQL draft was not produced:
  no deterministic recipe, SQL shape over safety thresholds, CTE body validation
  not proven, set-operation boundary, no material change, or trusted
  recommendations-only.
- Recent real-case batch testing showed that stats-available optimization
  candidates can still produce zero trusted SQL drafts. The bottleneck was not
  validation failure or missing table stats; it was insufficient Python-owned
  proof for a safe rewrite, high-risk SQL shape, or no material LLM change.
- A 1,000-case Recent smoke audit showed that the largest current optimizer
  gap is no-recipe coverage rather than the old `not_candidate` bucket. After
  recomputation with current rewrite-support classification, broad cases split
  roughly into `not_candidate` 496, `guidance_only` 492,
  `source_unavailable` 11, and `draft_disabled` 1. The no-recipe layer was
  dominated by plain SQL workload groups, not by current CTE/derived
  predicate-pushdown surfaces.
- The current no-recipe product path is to make unsupported outcomes useful
  before adding more SQL drafts: trusted `no_rewrite` and
  recommendations-only output should distinguish aggregate/distinct,
  set-operation, nested-query, join, single-relation filter, CTE, and
  derived-table review directions without exposing SQL or implying automatic
  rewrite support. Browser Details should use these tracks only through
  allowlisted labels, review areas, first-change directions, and verification
  steps, and Workload Action Queue should roll repeated groups up to those same
  safe review anchors and comparison metrics instead of generic SQL-shape
  wording.
- Add anonymized real fixtures for long `WITH`, CTE-heavy,
  join/filter/projection-preservation, and model-discipline failure cases.
- Add Python-owned recipes only where analyzer facts and validation can prove
  the boundary; avoid prompt-only rewrite freedom when the analyzer cannot
  explain the transformation.
- Add CTE simplification recipes as separate proven transforms: inline only
  single-use CTEs, remove pass-through CTE layers, use expanded CTE trees for
  internal analysis when helpful, and treat multi-use CTE inlining as a
  recommendations-only materialization or pre-aggregation hint unless Python
  can prove duplication is safe and useful.
- Prefer trusted `no_rewrite` or recommendations-only outcomes over
  speculative SQL drafts.
- Use `scripts/compare_optimizer_models.py --fixture-corpus` before changing
  prompt strategy or model defaults.
- Recognize the current strategic limit: LLM-as-SQL-writer is not reliable for
  expensive production queries under strict validation. Use the LLM primarily
  for wording, review, and candidate recipe design unless Python-owned facts and
  validation already define the rewrite boundary.
- For recipe-supported cases, Python should produce the trusted SQL draft.
  Calling the LLM as a second SQL writer on that path should be treated as
  latency/cost and bug surface unless it is explicitly limited to wording or
  human-readable explanation.
- Current external review diagnosis: the bottleneck is Python-rule coverage and
  candidate selection, not model quality, prompts, the validator, or table
  stats. Real cases are selected for cost, while supported recipes still assume
  textbook projection, predicate, qualifier, and CTE/UNION shapes.
- The shared predicate-pushdown helper contract is now a completed baseline for
  existing predicate-pushdown recipes: top-level and whole-group parenthesized
  `AND` conjuncts are evaluated independently, safe conjuncts can be copied
  without copying unsafe siblings, and tests cover mixed aliases, joins,
  `BETWEEN`, `IN`, `IS NULL`, grouped CTEs, derived tables, existing inner
  `WHERE` append, and no-copy cases. Keep hardening this contract when new
  predicate forms are admitted, but do not treat it as a new recipe family.
- Keep the validator strict. Improving SQL draft yield should come from richer
  analyzer facts, per-shape deterministic executors, and recipe-aware selection,
  not from loosening validation.

The completed optimizer recipe baseline is tracked in
[changelog.md](changelog.md). This roadmap lists remaining direction, not
completed implementation inventory.

Remaining near-term optimizer work:

Current calibration note as of 2026-05-29:

- `scripts/audit_optimizer_funnel.py` now prints a compact candidate
  calibration headline and raw-free medium/high candidate counters. Existing
  local broad summaries showed medium/high candidates ending as guidance-only
  rather than trusted-draft supported: 62/1000 on a 24h summary, 211/952 on a
  6h safe summary, and 17/80 on a metadata-enabled summary. Treat that as an
  optimizer usefulness and recipe-coverage signal. Do not change scoring
  thresholds or validator strictness just to increase draft count.
- A rerun over the 6h safe summary after family-specific no-recipe guidance
  still produced `draft-supported=0`, while review reasons split into plain
  aggregate/distinct, set-operation, single-relation filter, nested-query,
  unfiltered join, plus existing CTE/derived categories. Treat this as the
  current review-guidance baseline, not proof that a broad plain-SQL recipe is
  safe.

1. Run `scripts/audit_optimizer_funnel.py`,
   `scripts/audit_optimizer_plain_shapes.py`, and
   `scripts/audit_optimizer_representative_shapes.py` on the latest broad
   Recent smoke and use repeated workload groups plus no-recipe shape-family
   counts as the recipe-candidate or no-draft-guidance backlog. The first
   target should be the largest repeated family where analyzer facts and
   validation can prove a safe transform. If no transform boundary is provable,
   improve review guidance and fixtures instead of producing SQL. Structural
   and representative audit blockers should stay aligned with the raw-free
   no-recipe review tracks used in product guidance, and broad batch summaries
   should retain `no_recipe_review_track_counts` as the quick backlog mix
   signal. Use `--fail-on-repeated-no-recipe-readiness-gaps` when repeated
   guidance-only groups must be blocked until they have one specific safe
   review track and complete allowlisted guidance with compare/rerun
   verification.
2. Re-run the real optimizer benchmark after the prompt-route split, model
   default split, rewriteability taxonomy, recipe-aware ranking, and
   per-conjunct predicate-pushdown baseline. Compare trusted SQL drafts,
   deterministic no-recipe outcomes, recommendations-only outcomes, and
   validation failures before adding another recipe.
3. Target new recipes at expensive ETL patterns rather than low-value small
   queries: partition-limited `INSERT OVERWRITE ... anti-join staging UNION ALL
   staging`, large-fact joins to small distinct key sets, wider post-UNION
   rollups, pre-aggregation before exchange, and repeated-scan / redundant CTE
   shapes.
4. Add narrow expression-projection predicate pushdown. The first version
   should allow only deterministic scalar projection expressions with no
   aggregate/window/subquery inputs and should substitute output aliases back to
   exact source expressions under validation.
5. Extend UNION ALL branch predicate pushdown after branch lineage facts can
   prove which branch owns the filtered output column. Validation must preserve
   branch count/order/schema and keep untouched branches byte-equivalent.
6. Treat `pre_aggregate_join_input` as a larger follow-up project, not the next
   quick recipe: additive measure proof, join-key/group-key containment, outer
   joins, `AVG`, and `COUNT(DISTINCT ...)` make this high value but high risk.
7. Turn any repeated successful generic rewrite into an analyzer-owned fact plus
   Python-owned recipe only when validation can prove the boundary.
8. Validate the expanded CTE facts against sanitized real fixtures and add only
   missing analyzer-owned categories that block proof of specific future
   recipes.
9. Add more focused deterministic recipes for CTE simplification only after
   recipe-specific validation exists, especially single-use CTE inlining and
   wider pass-through variants with aliases or downstream CTE consumers.
10. Validate analyzer-owned stats-evidence facts with real sanitized fixtures,
   especially stats-present-but-not-primary cases and mixed stats/runtime
   bottleneck signals.
11. Use repeated real-case batches to measure the full optimizer funnel after
   each facts or recipe change: optimization candidate, stats/query context,
   recipe detected, safe to attempt, trusted draft, and no-draft reason.
12. Automate the optimizer funnel against fixture and sanitized real corpora
   outside the normal fast CI path. Each run should produce a raw-free
   `funnel.json` with candidate, recipe-detected, draft-ready, trusted-draft,
   no-rewrite, recommendations-only, and failure counts, and alert on material
   regressions.
13. Keep LLM prompts constrained to applying analyzer-proven rewrite tasks with
   minimal diffs.

Stop condition for the trusted SQL-draft direction:

- After expression-projection pushdown and UNION ALL branch pushdown are
  implemented and tested, a fresh 200+ case medium/high batch still has a
  `safe_to_attempt` rate below roughly 5%.
- Recipe-adjacent backlog is not materially larger than safe-draft cases,
  meaning real workloads are structurally outside the conservative recipe
  surface rather than merely under-implemented.
- `deterministic_draft_unavailable` does not drop materially after the new
  facts/recipes, with unsupported reasons still dominated by aggregate,
  window, join, or set-operation boundaries the product should not cross.
- Blind review or re-collection cannot show a meaningful difference between
  trusted drafts and originals.

If these hold, the optimizer should retreat from trusted SQL draft production
as a central product promise and focus on evidence-backed recommendations,
stats/query-shape classification, and a DBA-facing recipe feasibility funnel.

### 5. Metadata Selection Policy

Make default metadata collection policy explicit and bounded.

- Treat stats/metadata diagnosis as a first-class optimizer routing input, not
  just report context. Expensive cases should be separated into stats-primary,
  SQL-shape-primary, runtime-primary, mixed, and unknown before choosing a SQL
  rewrite, stats action, or runtime follow-up path.
- Prefer structured analyzer facts over rendered `analysis_facts.md` wording in
  scoring. Markdown text remains a compatibility fallback, not the source of
  truth for stats/query-shape classification.
- Do not claim stale stats without direct structured evidence. Until freshness
  or partition-divergence facts exist, "stats present but estimates still wrong"
  should be phrased as an unknown or non-primary stats signal, not as staleness.
- Prioritize high-severity analyzed cases.
- Include top medium/high Optimization candidates and suspicious cases where
  cardinality, memory, stats, or query-shape candidates need metadata to avoid
  misleading conclusions.
- Avoid default metadata collection for clean/short queries, admin statements,
  failed/cancelled cases without useful execution evidence, and cases that
  exceed bounds.
- Show `not_requested`, `partial`, `failed`, and `insufficient_metadata` as
  distinct user-facing states.

Near-term metadata/stats work:

1. Finish replacing report-side stats/query-shape extractors with structured
   analyzer facts where available. Recent stats and query optimization scorers
   already prefer `analysis.json`, and report stats-maintenance routing now
   prefers rendered `Stats Metadata Quality` facts while exchange/data-movement
   recommendations prefer rendered `Data Movement Evidence` facts and
   spill/scratch gates prefer rendered `Memory Pressure Evidence` facts.
   Admission/pool next checks prefer rendered `Runtime Admission Evidence` and
   correlated `Runtime Metrics Correlation` facts, while backend/per-host next
   checks require supported backend follow-up evidence before legacy text
   fallbacks. Remaining query-shape/report extractors keep rendered markdown
   parsing as a fallback until structured facts cover those paths.
2. Continue treating stats freshness as unknown unless a future direct
   staleness or metadata-divergence fact exists. Recent scoring no longer uses
   `stats_possibly_stale` rendered text as positive evidence.
3. Continue calibrating `case_primary_bottleneck` presentation. Recent batch
   summaries and Details pages already include safe label, confidence and
   reason-category presentation when `analysis.json` is available.
4. Continue calibrating high-confidence `case_primary_bottleneck` caps.
   Non-primary stats/query action candidate tiers are already capped to `low`
   for high-confidence primary bottlenecks.
5. Use the batch-level `case_primary_bottleneck_distribution` in
   `batch_summary.json`/Markdown to track unknown, mixed, not-classified, and
   medium-or-better confidence rates across real-case batches.
6. Improve partition and column stats detail from already-collected metadata:
   partition row-count coverage counts are parsed from `SHOW TABLE STATS`, and
   join/filter column stats coverage is derived from already-collected SQL
   context plus per-column stats status without exposing raw partition values
   or raw metadata output. Continue calibrating these facts against
   representative sanitized batches before treating stats as primary.

Stop condition for stats diagnosis without EXPLAIN or reruns:

- After structured scoring, stale-signal gating, `case_primary_bottleneck`,
  partition coverage counts, and join/filter column stats coverage land,
  `case_primary_bottleneck = unknown` remains above roughly 30% on a sanitized
  100+ real-case batch.
- Mixed stats and SQL-shape/runtime rate remains above roughly 40%, meaning the
  analyzer cannot order likely causes from direct profile and bounded metadata
  facts alone.
- Blind reviewer agreement with `case_primary_bottleneck` is no better than
  chance on a held-out reviewer-labelled set of at least 30 cases.
- Re-collection after a user-applied stats action does not materially change
  cardinality anomalies, top estimated-row gaps, or
  `stats_metadata_quality.stats_primary_bottleneck`.

If two or more of these hold, stats diagnosis should retreat to follow-up-check
wording: no high-confidence stats primary label and no high-tier stats action
without EXPLAIN, rerun, or stronger direct metadata evidence.

### 6. Agent-Friendly Documentation

Keep active docs short enough to be read before implementation.

- `docs/codex-handoff.md`: current engineering baseline.
- `docs/code-audit.md`: active risks only.
- `docs/query-optimizer-contract.md`: optimizer trust contract.
- `docs/architecture.md`: current boundaries plus future seams.
- `docs/roadmap.md`: this active plan.
- `docs/changelog.md`: significant behavior, safety, workflow, and baseline
  changes only.
- `docs/README.md`: document status index. Every listed document should be
  marked `active` or `reference`.
- `docs/agent-playbook.md`: change-type routing for required reading, focused
  tests, and documentation updates.

Historical planning detail should stay out of active docs unless it changes a
current decision.

Documentation cleanup priorities:

1. Remove historical release, collector, audit, and prototype notes from the
   current tree unless they remain useful as explicitly listed `reference`
   documents in `docs/README.md`.
2. Keep `docs/code-audit.md` and `docs/analyzer-audit.md` as the only active
   audit files. Older audit snapshots should stay out of the current tree
   unless they are explicitly listed reference documents.
3. Keep `docs/safety-contract.md` as the canonical safety document. Security
   overview and roadmap safety sections should link to it instead of redefining
   the contract.
4. Keep `docs/changelog.md` as completed-work history. Do not add completed
   implementation inventories to the roadmap; when a roadmap item is closed,
   move the significant result to the changelog.
5. After several real agent tasks use generated `SUMMARY.md`, `--changed`,
   `--explain <path>`, `--merge-risk`, compact output, validation hints, or
   aggregate `--usage-summary` data from `scripts/agent_code_graph.py`, review
   whether it actually reduced repeated broad file reads, orientation cost, and
   late merge surprises. Tune only the heuristics with clear evidence from
   those tasks, such as noisy top-node rankings, missing ownership edges, weak
   test-target inference, stale validation hints, noisy merge-risk area
   overlaps, or sections agents keep ignoring.

## Medium-Term Work

- Evidence Quality Score expansion: reuse analyzer-owned confidence in reports
  and add more structured limitations for incomplete metadata/runtime evidence.
- Host Tail Diagnostics: correlate profile backend-tail evidence with bounded
  host/daemon metrics and repeated-host patterns.
- Admission And Pool Context: add safe facts for queue wait, pool saturation,
  concurrent load, and admission pressure.
- Action Catalog: map deterministic recommendation types to local runbooks,
  owner hints, and expected validation signals.
- Evidence-grade reporting: classify report confidence as bronze, silver, or
  gold based on deterministic facts coverage, metadata coverage, workload
  baseline availability, and action outcome history.
- Safer local job history/status persistence for UI sessions.
- Focused package splits where feature work touches mixed responsibilities.

## Deferred

These are not current support. Revisit them only when the listed signal is met.

### Shared Deploy And Enterprise

- Multi-tenant web service, OIDC/SAML auth, RBAC, identity-bound job store,
  shared artifact storage, centralized audit export, centralized LLM quota and
  cost attribution, and a production multi-tenant server runtime are deferred
  until there is an explicit shared-deploy product decision and a real design
  partner. Container, Kubernetes, and Helm packaging already exist for
  public-demo and configured private deployments; they do not provide native
  authentication or general multi-tenancy. Use the deployment READMEs and
  [kubernetes-auth-front-door.md](kubernetes-auth-front-door.md) for their
  current external-auth and isolation requirements.
- A reference corporate deployment guide can be documented earlier as an
  "if you must" path, but it should state the required external controls:
  corporate TLS, trusted reverse proxy, authentication, single-tenant or
  team-scoped instances, safe credential ownership, and operational support.
  The owner-raw D3 contract is the current narrow version of that guide for one
  isolated source surface; real use still requires a live front-door validation
  gate before enabling shared/non-local raw source reveal.
- Do not sell or separate safety, validation, browser redaction, or diagnostic
  quality into an enterprise-only tier. Commercial-only boundaries may cover
  shared-deploy operations and support, not the safety contract.

### Source Provider Expansion

- Current source support is described in Current Scope. The items below are
  follow-up expansion work, not permission to claim broader provider support.
- Cloudera Manager remains the reference implementation for source-provider
  boundaries. Prefer thin wrappers around existing `query_doctor/cm` helpers
  over rewriting working collectors for tidiness.
- Cloudera Manager version adapter: revisit when real deployments expose newer
  response shapes or metric catalogs that current collectors cannot parse.
- Direct Impala daemon provider: bounded Recent, Running, and Known Query ID
  collection, source provenance, provider-neutral profile context labels,
  profile resource facts, and profile timing facts are implemented. Follow-up
  work should add real fixture coverage, profile action cards, and a normalized
  engine fact contract before broadening provider behavior.
- Prometheus-style metrics provider: implemented as optional direct Impala
  runtime context with allowlisted PromQL, fixed windows, response-size limits,
  and normalized facts only. Follow-up work should add sanitized Ambari/Hadoop
  fixtures and broaden metric profiles only with tests.
- Prepared event/log provider: revisit when event or log sources can provide
  structured cluster events, health alerts, or summarized indexes without raw
  log exposure.
- Hive Metastore metadata backend: revisit only through allowlisted
  parameterized read-only queries over known schema tables and strict output
  bounds.

### Diagnostic Products

- Cluster Doctor as a separate explicit user-run cluster/service/workload window
  diagnostic product. Revisit when per-query diagnosis needs cluster-level
  normalized context often enough that a separate product mode is clearer than
  adding more Details sections. Query Doctor may consume only normalized
  Python-owned context from it.
- Broad workload-level views by query fingerprint, pool, user, table set, and
  time window. Raw-free fingerprint aggregation, baselines, regression signals,
  and minimal outcome tracking are near-term work; broad exploratory workload
  views wait until those safe aggregates exist locally.
- Broad pool/admission operations belong with the deferred Cluster Doctor
  product. Per-query pool/admission diagnosis is near-term only as
  analyzer-owned facts for pool pressure, queue wait, and concurrent load,
  separate from per-query SQL-shape analysis.

### Engines And Storage

Future Big Data SQL/lakehouse live engine candidates include expanded Trino,
Spark SQL, StarRocks, Apache Doris, ClickHouse, and Dremio. They require
engine-specific collectors, parsers, metadata allowlists, validators, browser
safety tests, and report coverage before being documented as live supported.

Do not claim a second live supported engine until Impala diagnosis is useful on
real workloads or a design-partner workload proves cross-engine urgency. A
practical live-support readiness bar remains `case_primary_bottleneck =
unknown` below roughly 20% on a representative real-case batch, plus the
support gates above. Bounded offline import work can start earlier when it is
used to shape the engine fact contract and stays out of live product surfaces.

Spark SQL remains deferred for product support. Its useful diagnostic surface
is a different model from Impala: applications, SQL executions, jobs, stages,
tasks, executor behavior, event history, and logs. A research-only Spark
architecture spike may define the fact model and compact fixture contract, but
it must not add collection, UI, reports, optimizer behavior, or a public support
claim before the same gates apply.

Recommended expansion order is documented in
[engine-expansion-plan.md](engine-expansion-plan.md):

1. Harden the current direct Impala daemon source and Prometheus metrics source
   with sanitized real fixture coverage, profile action cards, and normalized
   engine fact contracts.
2. Engine fact contract refactor so analyzer services consume normalized
   parser outputs rather than raw Impala profile internals.
3. Fixture-only second-engine discovery for one named candidate when it answers
   an engine fact-contract question. Trino is the default candidate to validate
   because of migration-path fit, but it is not a public commitment. See
   [engines/trino-diagnostic-contract.md](engines/trino-diagnostic-contract.md)
   for Trino source and evidence rules. Spark architecture research is tracked
   separately in
   [engines/spark-architecture-spike.md](engines/spark-architecture-spike.md)
   and remains below product support.
4. Supported second engine only after real design partner demand, collection
   contracts, parser/fact fixtures, metadata allowlists, browser/report safety
   tests, and a support gap matrix.
5. Broaden Prometheus-style metrics profiles only after the first direct Impala
   metrics contract is stable.
6. Storage/table-format facts after provider and engine boundaries stabilize.

Storage and table-format context is a separate axis from query engines. Future
context may cover HDFS, object storage, Apache Kudu, Apache Iceberg, Apache
Hudi, Delta Lake, and engine-internal analytical storage, but only as bounded
read-only normalized facts.

## Repository Direction

The package refactor is in stabilization mode.

- Keep `query_doctor/` under the repository root for now; do not mix a `src/`
  layout migration with feature work.
- Do not add placeholder packages or fake adapters.
- Do not split files only because they are long.
- Split when a feature, safety review, repeated edit conflict, or mixed
  responsibility needs a clearer boundary.
- Keep safety-critical boundaries explicit: raw collection, normalized facts,
  LLM prompt/report writing, deterministic validation, and browser rendering.
- Keep shared browser/report safety helpers under `query_doctor/safety/`.
- Treat `query_doctor/web/trusted_artifacts.py` as a browser trust boundary and
  split it only with dedicated trust-boundary tests.

Good future split boundaries:

- web routes, job orchestration, command builders, presenters, and trusted
  artifact loading;
- report prompt contract, sanitizer, validation, recommendation candidates, and
  LLM client code;
- optimizer parsing, risk classification, validation, recipes, fallback, and
  trusted outcome contracts;
- Cloudera Manager client/config, query discovery, profile collection,
  time-series collection, events collection, and source-provider adapters;
- Impala metadata allowlist, Kerberos/cache handling, shell/protocol execution,
  workflow orchestration, digesting, and analyzer integration.

## Out Of Scope And Anti-Features

These are not deferred backlog items. They require a product-direction change
or a new safety contract before they return.

- AI Copilot UX. The product favors validated facts and explicit rejection over
  speculative suggestions; a Copilot flow would invert that trust contract.
- Auto-fix mode. Query Doctor must not execute user SQL or apply generated SQL.
- Generic SQL execution or OLTP database support.
- Execution-engine selection, session routing, live availability policy, and
  deterministic fallback. Query Doctor may produce diagnostic evidence but
  never selects the final route.
- Claiming support for engines without tests.
- Plugin framework before the Impala product is useful end to end.
- Broad package reorganization mixed with feature work.
- Confidence-score inflation. High-certainty wording without high-confidence
  evidence is a silent trust failure.
- Exposing raw SQL, raw profiles, raw metadata, local paths, secrets, model
  names, runtime internals, or raw artifact filenames in browser-visible output
  or trusted reports.

## Known Backlog

- Optimizer trusted SQL draft coverage remains narrow for complex CTE-heavy
  cases.
- The #88 optimizer recipe slice added narrow deterministic recipes for one
  single-CTE `UNION ALL` branch-filter shape and one derived-table
  projection-alias filter shape. More Python-owned optimizer recipes and
  fixtures are still needed before loosening prompts or treating broader
  set-operation or nested-query families as draft-ready.
- Browser model-name redaction must be updated when new provider/model names
  are introduced.
- Batch and pipeline subprocess timeouts may need configuration if real cluster
  runs show current defaults are too strict or too loose.
- More sanitized analyzer fixtures are needed for profile counter edge cases and
  runtime context correlation.
- Documentation drift remains a product risk; active docs should be updated as
  part of safety-sensitive feature work.
