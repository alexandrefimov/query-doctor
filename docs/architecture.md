# Query Doctor Architecture

Last reviewed: 2026-07-29

Query Doctor keeps fact extraction and assessment deterministic. LLMs may write
report wording only from facts that deterministic analyzers have extracted and
Query Doctor has validated. The global
`language` config controls Help, Details static UI copy, and newly generated
trusted reports; English is the default and Russian uses the same
language-specific prompt, normalizer, and validator boundary.

The product architecture is a local-first Big Data query diagnostic tool
focused on Apache Impala production triage, with bounded local Trino production
lanes: Recent Scan ranks many suspicious queries first, Query ID diagnosis
handles one known query as a secondary mode, and Query Optimizer remains a
separate read-only SQL analysis workflow. Native Impala AI profile-analysis work
tracked upstream should be treated as a compatibility direction and a signal to
prepare real cross-engine diagnostic contracts, not a reason to duplicate a
one-profile AI tab.

The standalone production-triage target is a Recent-first Query Inbox over
materialized raw-free cases. Operators should be able to open the UI, choose a
configured source and time range, filter the ranked cases, and then drill into
Details without manually starting a one-query workflow first. That inbox target
still uses the same explicit bounded collection, deterministic analyzer, and
trusted-output boundaries described below; it is not a broad query-history
crawler, shared service, SQL executor, or automatic report/optimizer runner.

## Current Architecture

```mermaid
flowchart TD
    subgraph External["External read-only sources"]
        CM[Cloudera Manager summaries and profiles]
        ImpalaDaemon[Direct Impala daemon query-list and profile endpoints]
        TrinoCoordinator[Bounded Trino coordinator query-list and QueryInfo reads]
        ImpalaMeta[Allowlisted Impala metadata]
        CMMetrics[Bounded Cloudera Manager time-series summaries]
        PromMetrics[Bounded Prometheus runtime summaries]
        CMEvents[Bounded Cloudera Manager events]
    end

    subgraph Local["Local Query Doctor runtime boundary"]
        Collector[Explicit bounded collectors]
        CaseStore[Ignored local case output and materialized cases]
        Analyzer[Deterministic analyzer]
        Facts[Analyzer-owned facts]
        Ranking[Ranking, inbox index, and action candidates]
        Details[Safe Details view models]
        WebUI[Local web UI]
    end

    subgraph Trust["Explicit trusted-output boundary"]
        ReportAction[Explicit report action]
        ReportWriter[LLM wording from analyzer facts only]
        ReportValidator[Sanitizer and fail-closed report validator]
        TrustedReport[Trusted report]
        OptimizerAction[Explicit optimizer action]
        OptimizerDraft[Details-page optimizer draft or no-draft guidance]
        OptimizerValidator[Deterministic optimizer validator]
        TrustedOptimizer[Trusted optimizer outcome]
    end

    subgraph Pasted["Separate pasted-query workflow boundary"]
        PastedInput[Submitted query text]
        QueryParser[Read-only parser and analyzer]
        QueryFindings[Safe findings and limitations]
    end

    CM --> Collector
    ImpalaDaemon --> Collector
    TrinoCoordinator --> Collector
    ImpalaMeta --> Collector
    CMMetrics --> Collector
    PromMetrics --> Collector
    CMEvents --> Collector
    Collector --> CaseStore
    CaseStore --> Analyzer
    Analyzer --> Facts
    Facts --> Ranking
    Ranking --> WebUI
    Facts --> Details
    Details --> WebUI
    WebUI --> ReportAction
    ReportAction --> ReportWriter
    Facts --> ReportWriter
    ReportWriter --> ReportValidator
    ReportValidator --> TrustedReport
    TrustedReport --> WebUI
    WebUI --> OptimizerAction
    OptimizerAction --> OptimizerDraft
    Details --> OptimizerDraft
    Facts --> OptimizerDraft
    OptimizerDraft --> OptimizerValidator
    OptimizerValidator --> TrustedOptimizer
    TrustedOptimizer --> WebUI
    PastedInput --> QueryParser
    QueryParser --> QueryFindings
    QueryFindings --> WebUI
```

This diagram is the current standalone/local product boundary. Future
source-provider and engine
work remains roadmap-only until contracts, fixtures, safety tests, and public
docs exist; the diagram should not be read as a generic provider plugin system
or automatic LLM execution path.

Current support is intentionally narrow:

- Apache Impala is the full production triage query engine. Trino has bounded
  local production support only for the raw-free retained-list Recent, one
  explicit Query ID, materialized Details, deterministic Python Report, and
  optimizer guidance lanes listed in the support matrix.
- Cloudera Manager summaries, profiles, metrics, and events are the full
  implemented Recent queries source.
- Direct Impala daemon query-list and profile endpoints support bounded Recent
  scans, Running scans, and one explicit Known Query ID. They do not provide
  Cloudera Manager events; optional Prometheus runtime metrics can be collected
  only when explicitly configured.
- Direct Impala Recent and Running scans inherit upstream coordinator query-log
  retention. Impala defaults that log to `--query_log_size=200` entries plus a
  byte cap, so deeper direct history is an Impala daemon configuration choice on
  every coordinator, not a Query Doctor discovery guarantee. Very large
  coordinator query logs can make the daemon Web UI query-list endpoint slower
  to render or serialize.
- Deeper direct Impala history has three possible source families with separate
  contracts: larger coordinator query-log retention through the existing daemon
  endpoints, future operator-managed profile-log directory ingestion, and
  future bounded external history indexes such as Loki or OpenSearch. Only the
  first family is current product behavior.
- Direct Impala profile collection keeps text profile endpoints as the default
  compatibility path. Optional JSON profile probing, `/profile_docs` counter
  stability probing, and `/admission?json` aggregate context degrade safely to
  unavailable/unknown on older Impala or Cloudera distributions.
- Direct Impala profile analysis publishes raw-free Profile Format, Source
  Provenance, Profile Resource Facts, Profile Timing Facts, scan I/O and tuple
  cache facts (`profile_scan_io`), Impala-reported skew and statistics counts
  (`profile_impala_signals`), Runtime Diagnosis resource/timing signals, and
  safe capability/limitation summaries.
- Cloudera Manager (CM) and Prometheus time-series support is bounded and
  summarized before becoming facts.
- Cloudera Manager events support is bounded and summarized before becoming
  Cluster Event Context.
- Impala metadata collection is explicit, read-only, and allowlisted.
- Reports and details-page optimizer drafts run only after an explicit
  selected-case action.
- The pasted-query optimizer is read-only, does not execute input, and does not
  echo submitted text after submit.
- Future upstream Impala profile JSON/parser/redactor compatibility must map
  into the same Python-owned fact and raw-free browser/report boundary before it
  becomes product behavior.
- Future direct Impala profile-log or external-history ingestion, if added,
  must be an explicit operator-managed source with allowlisted local/mounted
  roots or source-contract labels, bounded file counts, record counts, byte
  reads, and time windows, safe freshness controls, raw-free indexing, and no
  browser-triggered remote shell, pod exec, pod copy, arbitrary path traversal,
  arbitrary log-search DSL, or credential entry.
- Future live multi-engine support must enter through an engine fact contract
  with real fixtures and safety tests, not through placeholder adapters.
- The engine fact contract currently supports an Impala analyzer projection,
  sanitized Trino offline evidence package import, bounded local Trino
  event-store import, bounded HTTP Trino event archive import, bounded HTTP
  Trino query-detail archive import, bounded local Trino query-detail import,
  bounded local Trino query-list aggregate import, bounded local Trino
  statement-stats import, local pruned QueryInfo import, bounded local metadata
  summary import, event-source contract checks, dry-run coordinator query-info
  target checks, bounded pruned coordinator query-info probes, one-query pruned
  coordinator fact import, local compact diagnosis over raw-free direct
  boundary JSON excluding metadata summary boundaries or selected package sample
  boundaries, isolated local `/trino/compact-diagnosis` rendering for the same
  already raw-free inputs, and the local production Trino retained-list Recent
  and One Query ID lanes. Only server-owned materialized cases from those Trino
  lanes may open raw-free Details, deterministic Python Report, and optimizer
  guidance. Broader live Trino coordinator collection, Running scans,
  query-history crawling, product metadata collection, LLM reports, Query
  Optimizer jobs, generated Trino SQL, and SQL execution remain unsupported.

## Query Inbox Target Boundary

The Query Inbox direction changes the default product emphasis, not the trust
contract. The intended loop is:

```text
bounded source read
  -> local materialized case
  -> deterministic analyzer facts
  -> raw-free summary index and workload digest
  -> ranked/filterable inbox
  -> Details and explicit selected-case actions
```

Online materialization is allowed only as an operator-configured local loop or
explicit scan job that runs the same bounded source contracts as the current
workflows. It may refresh safe indexes, last-run status, freshness, coverage,
limitation, and failure-category summaries. It must not automatically run LLM
reports, Query Optimizer jobs, generated SQL, SQL execution, metadata crawls,
broad query-history crawling, raw source reveal, or shared-deployment identity
logic.

The browser contract for the inbox is a safe materialized summary, not the raw
collector output. Case references, source provenance, freshness states, workload
fingerprints, ranking fields, and action-readiness fields must remain raw-free
and path-free. Details, deterministic Python Report, and optimizer guidance may
consume materialized facts only through their existing validation boundaries.
The current browser implementation exposes explicit empty, ready, running,
partial, and stale Inbox states from safe summary counts, server-owned job
status, and parseable safe freshness fields such as `to_time` and
`recent_window_minutes`. Missing or unparseable freshness remains unknown; stale
must not be inferred from local paths, raw artifacts, or artifact mtimes.

## Source-Provider Architecture

This diagram shows current and roadmap seams. Not every provider implements
every source family; future providers and workflows must first add contracts,
fixtures, safety tests, and public docs before they become product behavior.

```mermaid
flowchart TD
    subgraph Providers["Roadmap source-provider seams"]
        CMProvider[Cloudera Manager profiles, metrics and events]
        ImpalaDaemon[Direct Impala query-list/profile endpoints]
        PromProvider[Prometheus metrics for direct Impala workflows]
        EventProvider[Prepared log/event summaries]
        LakehouseProvider[Future Big Data SQL/lakehouse providers]
        StorageProvider[Future storage and table-format context]
    end

    subgraph Normalized["Python-owned normalized facts"]
        ProfileFacts[Profile facts]
        MetadataFacts[Metadata facts]
        StorageFacts[Storage and table-format facts]
        MetricFacts[Metric facts]
        EventFacts[Event facts]
        Correlation[Deterministic correlation]
    end

    subgraph Products["Product surfaces"]
        QueryDoctor[Query Doctor query workflows]
        ClusterDoctor[Cluster Doctor window workflow]
        OptimizerSurface[Read-only optimizer workflows]
    end

    subgraph TrustBoundary["Shared trust boundary"]
        ClaimRegistry[Claim and evidence policy]
        Validators[Report and optimizer validators]
        Reports[Trusted reports]
        Browser[Safe browser summaries]
    end

    CMProvider --> ProfileFacts
    CMProvider --> MetricFacts
    ImpalaDaemon --> ProfileFacts
    PromProvider --> MetricFacts
    EventProvider --> EventFacts
    LakehouseProvider --> ProfileFacts
    LakehouseProvider --> MetricFacts
    StorageProvider --> StorageFacts
    ProfileFacts --> Correlation
    MetadataFacts --> Correlation
    StorageFacts --> Correlation
    MetricFacts --> Correlation
    EventFacts --> Correlation
    Correlation --> QueryDoctor
    Correlation --> ClusterDoctor
    MetadataFacts --> OptimizerSurface
    QueryDoctor --> ClaimRegistry
    ClusterDoctor --> ClaimRegistry
    OptimizerSurface --> ClaimRegistry
    ClaimRegistry --> Validators
    Validators --> Reports
    Validators --> Browser
```

Future direction:

- keep engine and provider adapters thin until there is implemented behavior;
- keep future engine work focused on Big Data SQL/lakehouse runtimes rather
  than generic OLTP database support;
- follow [engine-expansion-plan.md](engine-expansion-plan.md) for the transition
  order: Direct Impala profile source first, engine fact contract second, and a
  second engine only after demand and Impala readiness signals;
- use stable upstream Impala profile JSON, parser, and redactor contracts as
  compatibility targets when they exist, with fixtures and redaction tests
  before any adapter is trusted;
- treat query engine and storage/table-format context as orthogonal axes:
  engine adapters parse how a query ran, while storage context adapters publish
  bounded facts about HDFS, object storage, table formats or internal analytical
  storage;
- add direct Impala, Prometheus-style metrics, and prepared event providers only
  behind explicit bounded read-only contracts;
- keep Cluster Doctor as a separate user-run cluster/window diagnostic product,
  not an implicit query root-cause engine;
- let Python publish facts, statuses, confidence, limitations, and claim scope
  before any LLM wording is allowed;
- keep browser and report output raw-free across every future source.

## Current Pipeline

```text
Cloudera Manager profile summary
  -> query-doctor-collect-cm-profiles
  -> ignored local case directory
  -> query-doctor-analyze
  -> analyzer-owned facts artifact
  -> action cards and deterministic evidence
  -> optional bounded metadata collection and analyzer rerun
  -> query-doctor-report
  -> sanitizer and fail-closed validator
  -> deterministic analyzer facts appendix
  -> trusted LLM report
  -> local UI
```

The Cloudera Manager collection path is currently validated against the local
Cloudera Manager 6.2.1 environment. Direct Impala daemon collection is also
implemented for bounded Recent, Running, and Known Query ID workflows when
configured. Optional JSON profile, `/profile_docs`, `/admission?json`, and
Prometheus surfaces are compatibility inputs, not required dependencies. Treat
newer Cloudera Manager versions, broader non-Cloudera provider behavior, and
prepared event/log sources as future source-provider work, not as automatic
support.

The same boundary applies to every current workflow: collectors and parsers
prepare bounded inputs, Python-owned analyzers create diagnostic facts, LLMs
phrase those facts only after an explicit action, and validators decide what
can be rendered or stored as trusted output.

## Analyzer Flow

This is the contract-level analyzer flow. Individual Impala profile parsers,
metric adapters, event summarizers, and presenter helpers stay implementation
details so the diagram remains reviewable.

```mermaid
flowchart LR
    subgraph Inputs["Bounded local inputs"]
        Profile[Redacted profile digest]
        QueryMeta[Safe query metadata]
        TableMeta[Allowlisted table metadata]
        MetricInput[Bounded metric summaries]
        EventInput[Bounded event summaries]
    end

    subgraph AnalyzerFlow["Python-owned analyzer"]
        Parsers[Profile and context parsers]
        Facts[Raw-free analyzer facts]
        Runtime[Runtime Diagnosis signals]
        Actions[Action candidates and limitations]
    end

    subgraph SafeOutputs["Safe downstream outputs"]
        Details[Details view models]
        ReportFacts[Trusted report fact appendix]
        Recent[Recent scan ranking]
        OptimizerSignals[Optimizer candidate signals]
        SafeBoundary[Browser and trusted report boundary]
    end

    Blocked[Raw SQL, raw profiles, raw metadata, local paths]:::blocked

    Profile --> Parsers
    QueryMeta --> Parsers
    TableMeta --> Parsers
    MetricInput --> Parsers
    EventInput --> Parsers
    Parsers --> Facts
    Facts --> Runtime
    Runtime --> Actions
    Facts --> Details
    Facts --> ReportFacts
    Actions --> Recent
    Actions --> OptimizerSignals
    Details --> SafeBoundary
    ReportFacts --> SafeBoundary
    Blocked -. blocked .-> SafeBoundary

    classDef blocked fill:#fbeaea,stroke:#9a2a2a,color:#3a1010;
```

## Components

### Collector

The collector:

- performs explicit, bounded, read-only profile collection from Cloudera
  Manager or configured direct Impala daemon endpoints;
- requires redaction for real collection;
- keeps analyzer-useful counters and stable safe host aliases;
- writes generated local cases only under ignored corpus/output paths;
- does not run the analyzer or report writer.

Profile acquisition should stay behind small source interfaces rather than one
broad provider object: fetch explicit profiles and safe query context, discover
bounded query summaries, fetch bounded runtime metrics when available, and fetch
bounded event context when available.

Current provider support:

- Cloudera Manager API, tested against CM 6.2.1 behavior.
- Direct Impala daemon query-list/profile endpoints for bounded Recent,
  Running, and one explicit Known Query ID, with source provenance, resource
  facts, timing facts, and optional explicit Prometheus runtime metrics.

Provider seams:

- CM-version seam: isolate endpoint paths, response parsing, query-state
  normalization, and time-series tsquery allowlists so newer CM versions can be
  added with fixtures and safety tests instead of changing analyzer/UI
  contracts.
- Non-CM Impala seam: direct Impala daemon debug query-list/profile collection
  exists for bounded Recent, Running, and one explicit Known Query ID. It must
  stay explicit, bounded, read-only, redacted, and source-limited; follow-up
  work should improve fixtures, profile action cards, and normalized engine
  facts before broadening provider behavior.
- Metrics seam: keep metrics source separate from profile source. Cloudera
  Manager time-series is the full Cloudera Manager Recent scan implementation.
  Prometheus is an implemented optional metrics provider for configured direct
  Impala workflows. It uses a bounded query allowlist, fixed time windows,
  response-size limits, and summarized facts only.
- Events seam: keep Cloudera Manager events as the current event source for
  bounded cluster context. Future prepared log/event providers must publish
  normalized counts, categories, affected safe scopes and limitations, not raw
  log lines or raw provider payloads.

### Diagnostic Signal Seam

Profiles, metadata, metrics, events, and future logs are separate diagnostic
signal families. Each family can have its own source providers and
deterministic analyzer before facts enter the shared report contract.

- Profile analyzer: implemented today for Impala runtime profiles.
- Metadata analyzer: implemented through bounded Impala metadata context.
- Metrics analyzer: implemented through bounded CM time-series summaries;
  future providers may read pre-aggregated metrics from CM/Prometheus or compute
  safe aggregates locally from bounded raw responses.
- Event analyzer: started through bounded Cloudera Manager Events context.
  Future log analyzers should prefer prepared log indexes or structured log
  stores when available and fall back to bounded local parsing only with
  explicit allowlists, time windows, redaction, and tests.

Cross-signal correlation belongs in Python-owned facts, not in LLM inference.
The LLM may phrase a complex report only after profile, metadata, metrics,
events, and future log analyzers publish normalized facts with confidence/status
fields.

Future Cluster Doctor work should follow
[cluster-doctor-contract.md](cluster-doctor-contract.md): keep it as a separate
explicit user-run read-only cluster/service/workload-window diagnostic seam, and
let Query Doctor consume only normalized Python-owned context or deterministic
correlation facts.

### Analyzer

The analyzer:

- reads the local collected profile digest;
- extracts deterministic facts into the analyzer facts artifact;
- writes operator summaries, anomaly counts, action cards, backend/host
  evidence, referenced tables, and optional table metadata facts when present;
- reads local metadata context when present and adds
  `## Table Metadata Context`;
- may add safe metrics, event, or cluster facts only through bounded collection
  contracts and normalized analyzer facts;
- does not call Cloudera Manager, the LLM runtime, or the report writer.

### Report Writer

The report writer:

- reads only the analyzer facts artifact;
- uses an LLM for narrative wording, not fact discovery;
- must not infer facts from raw profile text, SQL, local config, or external
  context;
- may render a multi-signal diagnosis, but only from normalized Python-owned
  facts produced by profile, metadata, metrics, event, and future log analyzers;
- generates trusted LLM reports within one fact boundary;
- writes localized user-facing narrative sections for summary,
  recommendations, detailed findings, and follow-up checks;
- deterministically appends a localized analyzer facts appendix from the facts
  artifact;
- excludes raw metadata, raw time-series, and raw event context from the LLM
  prompt;
- passes only curated metadata digest, normalized `## Runtime Metrics Facts`, and
  bounded Cluster Event Context to the LLM/report boundary;
- buffers raw LLM output and writes final reports only after normalization,
  sanitization, narrative validation, appendix append, and final validation.

### Sanitizer And Validator

The sanitizer and validator:

- normalize a narrow set of unsafe generated wording into explicit safe wording;
- reject reports with unsupported claims;
- fail closed: a rejected report is safer than accepted invented evidence;
- write only sanitized/normalized `.partial` output on validation failure and
  preserve the existing final report.

### Optimizer Outcome Generator

The details-page optimizer:

- reads only server-owned analyzed case inputs;
- may use a read-only SELECT/WITH source or a SELECT/WITH payload extracted from
  supported INSERT/CTAS sources;
- uses Python-owned deterministic recipe executors for trusted SQL drafts;
- may use the LLM for explanation and recommendation wording, but not as the
  source of a trusted SQL draft;
- never executes SQL;
- writes a validated draft only after a supported recipe executor and
  read-only SQL validation prove result-shape checks over physical tables,
  filter scope, projection, DISTINCT, top-level GROUP/ORDER/set operations,
  CTE names, and top-level join shape;
- classifies rewrite risk as `rewrite_allowed`, `conservative_rewrite`, or
  `recommendations_only`;
- may emit trusted non-SQL outcomes such as deterministic recommendations-only
  or `no_rewrite` when Python cannot trust a SQL draft or the draft has no
  material change;
- may expose external rewrite validation only after an LLM optimizer validation
  failure, and only as bounded in-memory validation against the server-owned
  source;
- keeps partial drafts untrusted and hidden from browser-visible details.

### Local UI

The local UI:

- exposes Query Inbox, details pages, Help, and explicit selected-case LLM action
  workflows;
- is implemented as server-rendered Python HTML with shared CSS and small
  vanilla JavaScript helpers; there is no JavaScript build pipeline or SPA
  framework in the current baseline;
- uses Recent Scan as the flagship production triage workflow, with Recent
  queries as the default Query Inbox mode; it discovers Cloudera Manager summaries
  for Finished queries by default or direct Impala daemon query lists when
  configured, then collects bounded selected profiles, ranks deterministically,
  and leaves report/optimizer generation explicit per case;
- can collect bounded Cloudera Manager metrics/events or configured Prometheus
  runtime metrics as runtime context for selected cases;
- uses the same result shape for Running now scans, with lower-confidence live
  evidence;
- analyzes one known Query ID in the Known Query ID Query Inbox mode, prepares the
  deterministic Python report in that explicit submit job, and appends results
  to its table. It does not auto-run LLM reports or optimizer jobs. This path
  can collect via Cloudera Manager or direct Impala daemon profile endpoints,
  depending on local config;
- keeps the direct Query Optimizer route read-only for compatibility and safety
  testing; it parses one safe SELECT/WITH statement locally, does not execute
  pasted SQL, and does not render it back after submit;
- is not a source of facts;
- does not include broad unsafe collection or automatic web LLM batch reports.

Keep the server-rendered UI as the default architecture. It matches the
local-first workflow and keeps Python presenters close to the browser safety
boundary. A React or other client-app migration is not a goal by itself. Treat it
as future work only when a specific surface needs substantial client-side state,
such as complex in-browser filtering, multi-job live progress, graph/timeline
visualization, comparison workflows, or a richer optimizer editor. Any such
migration must first define narrow safe JSON/view-model contracts, preserve the
raw-free browser boundary, add focused browser-safety tests, and justify the new
Node/build dependency in the dependency policy.

See [roadmap.md](roadmap.md) for planned UI cleanup and architecture direction.
The current implementation remains Impala-only.

## Real-Case Coverage

Ignored local corpus and recent real-case checks cover important classes:

- `host-skew-corpus` alias: host/backend data-skew evidence without proven
  execution-tail host.
- `cardinality-memory-corpus` alias: bad-query case with supported
  row/cardinality and memory estimate anomalies.
- Details-page optimizer smoke covers read-only SELECT/WITH sources,
  SELECT/WITH payload extraction from supported DML/CTAS, conservative rewrite
  mode, and validation rejection for unsafe result-shape changes.

Keep alias-to-case mappings local. Do not add raw SQL, query IDs, raw
hostnames, raw IP addresses, raw profiles, local config, or credentials to
committed docs.
