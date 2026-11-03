# Engine Expansion Plan

Last reviewed: 2026-11-03

This document records the transition plan for future source-provider and engine
work. Full production triage remains Apache Impala. Trino has bounded local
production web Recent and One Query ID lanes, with raw-free materialized
Details, deterministic Python Report, and optimizer guidance. Its supporting
import, compact diagnosis, and dev-only audit lanes remain bounded by
[engine-support-gap-matrix.md](engine-support-gap-matrix.md), which owns the
current status. Running, broad history crawling, product metadata collection,
LLM reports, Query Optimizer jobs, generated SQL, SQL execution, and broader
shared Trino production support remain unsupported. Spark remains compact-only
and below production support.

Cloudera Manager is the full query/profile/metrics/events source for Impala.
Direct Impala daemon collection and optional Prometheus runtime metrics are
implemented only for the bounded workflows described below. The phases record
implementation boundaries and remaining work; they do not reset implemented
Trino lanes to an offline-only status.

The goal is to avoid doing provider decoupling, engine abstraction, new metrics
sources, and a second engine in one step. Apache Impala upstream work around
native AI profile analysis makes cross-engine production triage more important
as a long-term differentiator, but it does not relax the readiness gates below.
Each phase has its own stop conditions. Early second-engine exploration may
start before live-support gates are complete, but only as a bounded
contract-shaping path with explicit limitations and no default live workflow
impact.

## Current Position

- `query_doctor/engines/` is a thin metadata seam, not a full parser or analyzer
  abstraction.
- Current full collection is coupled to Cloudera Manager for query discovery,
  profile collection, metrics, and events. Direct Impala daemon collection now
  supports bounded Recent, Running, and Known Query ID profile workflows without
  Cloudera Manager events.
- Current profile parsing and many analyzer facts are Impala-profile specific.
- Impala metadata collection is already mostly separate from Cloudera Manager,
  but it remains Impala-specific.

Impala remains the only full production triage engine. The bounded Trino
lanes and compact Spark adapter do not imply analyzer parity. Extract behavior
only when there is implemented behavior behind the boundary.

The strategic direction is a Big Data SQL/lakehouse diagnostic workbench, not an
Impala-only clone of native Web UI analysis. The implementation path still
starts from Impala facts because that is the full production triage baseline.
The implemented Trino lanes have their own narrower collection, fact, and
browser/report safety contracts.

## Gate Types

Separate two decisions:

- **Exploration gate:** a small second-engine spike may start when it has a
  named engine, real or synthetic-safe artifacts, a specific contract question
  to answer, and no path to browser/report output without safety tests.
- **Support claim gate:** README, package metadata, UI copy, and support
  matrices must distinguish offline evidence import from live engine support.
  They must not claim live support for a second engine until real collection or
  fixture coverage, parser/fact mapping, metadata allowlists, browser/report
  safety tests, and a support gap matrix exist.

Do not start broad provider or product engine expansion until all of these are
true:

- core Impala diagnosis is useful on representative real workloads;
- `case_primary_bottleneck = unknown` is below roughly 30% for normal Impala
  diagnosis work;
- optimizer funnel metrics show non-zero trusted SQL draft production for
  recipe-supported cases;
- design partners confirm that the current product is useful enough to justify
  expanding its deployment surface.

Do not claim live supported second-engine behavior until every support gate in
[roadmap.md](roadmap.md) is true. A bounded offline or local import path can
happen earlier if it has deterministic parsers, raw-free outputs, explicit
limitations, and no live/query-execution behavior.

## Pre-Parallel Trino/Spark Backlog

The current pre-start guardrails are the shared `redaction_note_v1` validator,
the engine capability manifest, and the isolated preview web-surface registry.
They let Impala work continue while Trino/Spark feature branches stay bounded
to registered preview surfaces.

Before broad active parallel Trino/Spark feature work resumes, keep these as
separate synchronization slices rather than engine-specific feature work:

- the source-contract registry for source kinds, raw policy, bounds, and
  promotion gates is implemented in
  `query_doctor/trino/source_contract_registry.py`;
- the shared/distributed-SQL-family/source-boundary/support-boundary fact
  promotion policy is implemented in
  `query_doctor/analyzer/engine_fact_promotion_policy.py` with explicit
  `allowed_engines` and consumer tests;
- safe handoff artifact path/output helpers are implemented in
  `query_doctor/safety/handoff_artifacts.py`;
- continue extracting shared readiness/handoff script orchestration into
  focused dev-tool helpers when those scripts are next modified.

## Phase 1: Direct Impala Profile Source And Metrics Source

First expand away from Cloudera Manager, not away from Impala.

The first Direct Impala slice is intentionally narrow and implemented:

- support bounded Recent and Running query-list/profile collection from Impala
  daemon debug endpoints;
- support one explicit known query/profile fetch from an Impala daemon
  debug/profile endpoint;
- support optional explicit Prometheus runtime metrics for configured direct
  Impala workflow windows;
- stay read-only, bounded, redacted, and explicit;
- keep browser and trusted-report output raw-free;
- support enterprise auth requirements, including Kerberos, before claiming the
  provider is useful for real deployments;
- document that Prometheus metrics are not discovery or event support and are
  context-only unless correlated with deterministic profile evidence.

Use small source interfaces instead of one large provider object:

- `ProfileSource`: fetch one explicit profile and safe query context.
- `QueryDiscoverySource`: list bounded candidate queries.
- `MetricsSource`: publish normalized bounded metric facts.
- `EventSource`: publish normalized bounded event facts.

Cloudera Manager can implement all four over time. Direct Impala currently
implements bounded query discovery/profile collection and can use an optional
Prometheus `MetricsSource`; it still does not implement events.

Do not add event/log providers or a second engine in this phase.
Do not rewrite `query_doctor/cm/` for neatness; wrap existing behavior only as
far as needed to create a tested boundary.

Done for the current baseline means at least one real non-Cloudera-Manager
Impala deployment can diagnose bounded Recent/Running queries and one known
query safely, with optional bounded Prometheus runtime metrics, fixtures, and
browser/report safety tests. Follow-up hardening should add more sanitized
real fixtures and profile action cards before broader provider claims.

## Phase 2: Engine Fact Contract

Refactor Impala parsing and analysis behind an engine-owned parser output. The
contract should normalize parser outputs, not profile inputs.

The first contract-shaping slice now exists as
`query_doctor/analyzer/engine_facts.py`, an Impala projection module, a Trino
mapper, a packaged Trino offline evidence import path, a bounded local Trino
event-store import path, a bounded HTTP Trino event archive import path, a
bounded local Trino query-detail import path, and a bounded local Trino
query-list aggregate import path, plus a raw-free Trino event-source contract
check. These contracts now also feed the bounded local production Trino
Recent and One Query ID lanes. Only their server-owned materialized raw-free
cases may feed Trino Details, deterministic Python Report, and optimizer
guidance. Product metadata collection, LLM reports, Query Optimizer jobs, and
broader/shared production support remain blocked. Track current gaps in
[engine-support-gap-matrix.md](engine-support-gap-matrix.md).

Target shape:

- engine-specific parser input: raw profile payload plus bounded provider
  context;
- engine-specific parser output: typed engine profile facts with explicit
  support and null semantics;
- analyzer input: normalized facts, not raw Impala profile text;
- finding IDs and fact fields carry enough engine context to avoid pretending
  every engine has the same counters.

This phase should keep Impala behavior stable. Existing Impala tests should pass
through the new contract before any second engine is supported.

Do not add live second-engine behavior during this refactor. A bounded offline
or local import path for one named candidate engine is allowed when it answers a
contract question, stays isolated from normal workflows, and cannot render
browser/report output without safety tests. The goal is to prevent the first
non-Impala engine from becoming an Impala-shaped copy.

Done means the analyzer service no longer depends on Impala-specific profile
parsing internals, Impala fixtures still pass, and the fact contract documents
field semantics and unsupported-field behavior.

## Phase 3A: Experimental Second-Engine Offline And Local Import

Choose the second engine from design partner demand, not from a static wishlist.
The first step is discovery and offline import, not live support.

Trino is the default candidate to validate because it is a common migration
destination from legacy Hadoop and Cloudera environments, and it supports a
local-first diagnostic model better than closed platforms. Its initial spike
has progressed to the bounded local production lanes in the support matrix;
this does not meet the broader support gate in Phase 3B. The initial spike is
documented in
[trino-discovery-spike.md](trino-discovery-spike.md), and the future evidence
contract is documented in
[engines/trino-diagnostic-contract.md](engines/trino-diagnostic-contract.md).

Spark SQL remains outside product support under the current product state. Its
useful diagnostic surface depends on applications, SQL executions, jobs, stages,
tasks, executor behavior, event history, and logs rather than the Impala-style
runtime profile model. Bounded compact Spark architecture work now defines the
source contract, compact fixture schema, fact envelope, and compact-only adapter
described in
[engines/spark-architecture-spike.md](engines/spark-architecture-spike.md).
The current Spark slice includes an experimental bounded History Server
compact-intake CLI, a compact-only adapter, plus an isolated direct
compact-diagnosis page for one explicit History Server application or already
accepted raw-free JSON. A dev-only one-application handoff wrapper composes the
same bounded compact collection, raw-free diagnosis, optional boundary export,
readiness audit, and optional product-surface summary audit without becoming a
product CLI. Retained one-application
compact/diagnosis/boundary artifacts can be grouped and re-audited through a
dev-only local manifest without reopening Spark, and optional retained
product-surface summaries can be cross-checked over that same manifest. The
collector reads only summary `/api/v1` JSON and maps it to raw-free normalized
facts. The page renders only endpoint counts, warning IDs, deterministic
attention areas, limitations, and verification direction, without echoing
request selectors or submitted JSON. This remains below product support. Spark
registration must stay limited to the compact-only adapter and must not expand
into Recent workflows, Details/trusted report surfaces, optimizer behavior, raw
event-log downloads, raw SQL/plan/log or environment collection, Spark job
execution, or a support claim.

An experimental offline import path must:

- use sanitized or synthetic-safe artifacts or operator-reviewed local packages,
  not live cluster reads by default;
- for package-style intake, conform to
  [engine-redaction-note-v1.md](engine-redaction-note-v1.md) instead of adding
  engine-specific redaction-note schemas;
- map only a small set of parser outputs into the engine fact contract;
- preserve explicit `supported`, `not_observed`, and `unknown` semantics;
- avoid adding live runtime engine selectors, placeholder packages, or default
  product routes;
- include redaction and raw-free contract tests before any output reaches
  report or browser code;
- document what the spike proves and what remains unsupported.

Done means the path teaches the engine fact contract something concrete and
does not change current Impala behavior or live support claims.

## Phase 3B: Supported Second Engine

A supported second engine requires:

- engine-specific source and profile/parser fixtures;
- engine-specific metadata allowlists;
- explicit bounded read-only collection contracts before any live collection;
- normalized facts mapped into the engine fact contract;
- finding coverage that distinguishes shared findings from engine-specific
  findings;
- browser/report safety tests;
- a documented support gap matrix.

Do not update public README language to imply multi-engine support until the
engine has real fixtures, safety coverage, and working diagnosis paths.

## Phase 4: Prometheus Metrics Source Expansion

Prometheus-style metrics are a metrics-source expansion, not an engine
expansion. The first slice is implemented for configured direct Impala
workflows. Broader expansion, such as additional metric profiles or non-Impala
providers, can run in parallel with second-engine work only if the provider
interfaces from Phase 1 are stable.

Requirements:

- allowlisted PromQL only;
- fixed query windows;
- response-size limits;
- normalized facts only;
- no raw time-series, labels, provider JSON, or query text in browser-visible
  output or trusted reports.

## Phase 5: Storage And Table-Format Facts

Storage and table-format context is a third axis, separate from query engine and
source provider. Future facts may cover HDFS, object storage, Apache Kudu,
Apache Iceberg, Apache Hudi, Delta Lake, and engine-internal analytical storage.

Defer this axis until provider and engine boundaries have stabilized. When it
starts, publish bounded normalized facts such as file-count, file-size, pruning,
manifest, clustering, compaction, or layout signals. Do not pass raw storage
metadata to LLM prompts or browser UI.

## Non-Goals

- No placeholder engine packages.
- No fake adapters that claim support without parser, analyzer, fixture, and
  safety coverage.
- No broad runtime engine selector before engine-specific collectors, parsers,
  metadata, and tests exist.
- No final execution-engine selection, session routing, availability policy, or
  fallback logic in Query Doctor. Those responsibilities remain outside this
  repository.
- No public "coming soon" promises for Trino or any other engine.
- No storage/table-format root-cause claims without deterministic facts.
