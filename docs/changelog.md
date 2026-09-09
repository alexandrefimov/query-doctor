# Changelog

Last updated: 2026-09-09

This changelog records significant product, safety, workflow, and trust-boundary
changes only. It is not a commit-by-commit history.

For current behavior, prefer [../README.md](../README.md),
[docs/README.md](README.md), [roadmap.md](roadmap.md),
[codex-handoff.md](codex-handoff.md), and [code-audit.md](code-audit.md).

For 0.11.0 release notes suitable for GitHub Release, package-index, and
container-image handoff, see [release-notes-0.11.0.md](release-notes-0.11.0.md).
Historical 0.10.0, 0.9.0, 0.8.0, 0.7.0, 0.6.0, 0.5.0, 0.4.3, 0.4.2, and 0.4.1
release notes remain in [release-notes-0.10.0.md](release-notes-0.10.0.md),
[release-notes-0.9.0.md](release-notes-0.9.0.md),
[release-notes-0.8.0.md](release-notes-0.8.0.md),
[release-notes-0.7.0.md](release-notes-0.7.0.md),
[release-notes-0.6.0.md](release-notes-0.6.0.md),
[release-notes-0.5.0.md](release-notes-0.5.0.md),
[release-notes-0.4.3.md](release-notes-0.4.3.md),
[release-notes-0.4.2.md](release-notes-0.4.2.md), and
[release-notes-0.4.1.md](release-notes-0.4.1.md).

## Unreleased

- The web UI is one application surface instead of a stack of framed cards on a
  grey page. The bar at the top is sticky and spans the window, sections are
  separated by a hairline rather than by a border and a shadow, the results
  table runs edge to edge with a header that sticks under the bar, and content
  is capped at 1560px so a wide screen gains table width without gaining line
  length. Palette and radii moved with it: light neutrals instead of blue-grey,
  no shadows, one 6px radius instead of 6/8/12.

  Two things that were already broken came out with the frames. The unreachable
  `data-design=command` skin - 23 rule blocks, 153 lines, selected by nothing
  since `theme-bootstrap.js` always wrote `serious` - is gone, and so is the
  attribute itself. `batch-summary-card` and `split-grid` on Deployment
  Readiness had no rules at all, so the readiness summary printed
  `Modepublic_demo` and its two columns stacked; both have rules now.

- Headings, disclosure controls and keyboard focus now follow one set of rules.
  Across the demo pages `h1` rendered at 16px, 20px and 26px and `h2` at 14px,
  16px, 20px, 21px and 22px, so Help showed a 20px section heading under a 16px
  page title and Deployment Readiness had `h2` elements with no rule at all,
  left at the browser default. Page titles are 20px now, section titles 15px,
  sub-headings 13px, and bare `h1`/`h2`/`h3` carry that scale so nothing falls
  back to browser defaults again. Every disclosure chevron was a 30px bordered
  button with its own shadow; it is a 22px mark that tints on hover, and the
  whole summary row stays the click target it already was. Keyboard focus came
  back to the top navigation, the footer links and the result rows: all three
  set `outline:none` on `:focus` and offered only a colour, underline or
  background change in its place.

- The results table now shows the query id it is supposed to hand to the
  coordinator. The column was capped at 190px while the id and the workload
  fingerprint beside it needed 240px, so every grouped row ended in an ellipsis
  and the fingerprint was never readable; the cap is 264px now and the id wins
  the space. Rows are 31px instead of 37px, headers stop shouting in bold
  12px, and the row action stops being rendered in monospace. On the synthetic
  demo at 1280x900 the `All analyzed` view fits all 11 rows in the first
  screen, against 2 before this and the first-screen change together.

- Details now reads as one document instead of a stack of boxes. The
  recommendation card kept its sections in bordered panels with green, teal and
  amber left rules, so `Why this query matters`, `What to try` and `How to
  verify` looked like three severities rather than three steps of one path;
  they are labelled text now, and the card is the only container. The label
  rule also reached every nested span, which printed safe review locations
  uppercase, muted and bold - `SQL: final SELECT filter (line 9)` was shouting
  at the reader. Supporting evidence lost its per-fact boxes, the paragraph
  listing the four section names below it is gone, and the verdict prints
  priority once: the severity badge carries the full value, including the score
  it used to trim, and the meta strip keeps Query ID, duration and confidence.
  Runtime facts under the verdict read as one line instead of six pills. The
  synthetic demo case page drops from 2634px to 2436px.

- The Query Inbox first screen now leads with the query table. Result group and
  result filter controls were rendered twice: 19 of them repeated the same label
  and the same link in the status panel and again above the table. The status
  panel keeps scan scope and saved views, the working copy stays next to the
  table it acts on, and result filters plus the view-state line moved behind one
  `Filters` disclosure that carries an active-filter count and opens by itself
  when a filter is on. Two status messages that only restated the state badge
  are gone, and a lone scan warning renders as one line instead of its own
  disclosure box. On the synthetic demo at 1280x900 the first table row moves
  from 781px to 470px, so the first screen carries six ranked rows instead of
  two.

- The README link to the browser analyzer now points at the deployed site.
  GitHub publishes the Pages site at the lower-cased `.../query-doctor/`, and
  the advertised `.../Query-Doctor/` returned 404, so the first thing the
  landing page offers a visitor did not open. No check saw it: the Pages
  workflow served the freshly built site over localhost and never requested
  the deployment. It now runs the same page check against the deployed URL
  and fails when a README advertises a different one.
- Browser page rendering now reuses identical redaction results only within one
  GET request. The bounded cache keeps the existing browser-safety projection,
  does not cache explicit credential environments, and is discarded before the
  next request.

- Online History now reuses its already browser-safe in-memory case projection
  instead of redacting all 500 bounded rows a second time during rendering.
  Materialized indexes loaded from JSON remain untrusted and still pass through
  the full browser-display projection.

- Postgres-backed `Details ready` reads now choose the latest available profile
  artifact for each retained query once, then join those keys to compatible
  ready analysis-cache rows. This removes two correlated artifact lookups from
  the retained-summary scan while preserving the same bounded, raw-free,
  DDL-free Online History result.

- Postgres-backed Online History reads are now DDL-free. Schema creation stays
  with the explicit Postgres readiness command and the configured Helm
  initContainer, while a missing schema makes the browser show its existing
  safe unavailable state. The web request now opens only the two read
  connections for materialized rows/count and fail-soft backlog health.

- Online History now opens on a bounded `Details ready` view that selects the
  newest compatible analyzed cases across retained history before rendering.
  This keeps actionable cases visible even when newer unprocessed summaries
  would otherwise fill the bounded page. A separate `All recent` view keeps the
  newest-summary workflow and shows explicit queued, analyzing, failed,
  unselected, or Details-unavailable actions instead of empty cells. Both views
  stay bounded in the database, preserve raw-free projection, and use distinct
  opaque case references so their Details links resolve against the same view
  that produced them.

- Direct-Impala query summaries now keep the metrics the coordinator already
  serves. `/queries?json` carries `rows_fetched`, `bytes_read`, `bytes_sent`,
  `mem_usage` and `queued_duration`, and the parser read none of them, so
  `rows_produced`, both byte counts, the memory peak and the admission wait were
  empty on every row of a production install -- and the suspicion score, which
  reads exactly those fields, could never rise above what duration and status
  alone contribute. The pool was missed too: the coordinator calls it
  `resource_pool` while the parser looked only for `pool` and `request_pool`.
  Sizes arrive as text like `11.43 GB` and are read as binary units, which is
  what Impala means by them; reading them as decimal would understate every
  threshold. `mem_usage` maps to the aggregate peak rather than the per-node
  one, because it is the sum of the per-node peaks: on a nine-node query it
  reported 989.45 MB against a per-node maximum of 138.85 MB.

- The Recent profile worker now collects Impala metadata when the deployment
  asks for it. It forced `--metadata-mode off` on every analysis pass whatever
  the config said, and it is the only component that analyzes a case: the
  collector runs discover-only, so the separate top-N metadata refresh is never
  reached either. Between the two, a deployment could set every metadata option
  correctly and still have `metadata_status` read `skipped` on every case
  forever, which is what a production install did across 957 analyses. The
  worker also hands the analysis the table names extracted from the statement
  during collection; without them the pipeline reads only the redacted facts,
  where every identifier is a placeholder and nothing is collectable.

- A table whose name starts with a digit no longer loses its metadata. The
  identifier policy demanded a leading letter or underscore, which is Impala's
  rule for *unquoted* names, while everything the collector emits is
  backtick-quoted and may start with a digit. `2014_2018_facts` is a real
  production table, and it was refused before a connection was even opened, so
  no metadata would ever be collected for it. The allowed character set is
  unchanged, so a backtick still cannot appear inside a name and break out of
  the quoting; a test pins that.

- Impala metadata collection now speaks HiveServer2 in process through impyla
  instead of shelling out to `impala-shell`, and the collector holds one
  coordinator connection for a whole run rather than starting a process and a
  Kerberos handshake per statement. The policy is unchanged: the same three
  allowlisted `SHOW` statements, the same bounded and redacted artifacts, the
  same fail-closed statuses. What changes is the transport, and with it two
  things that were forced by the shell. Beeswax is gone, because impyla speaks
  HiveServer2 only, so `metadata_protocol` accepts `hs2` and `hs2-http` and a
  coordinator must be named on its HiveServer2 port. And the image no longer
  carries a second Python: `impala_shell` 4.3.0 imports the C `sasl` module
  unconditionally, `sasl` 0.3.1 is the last release and needs `longintrepr.h`,
  which CPython dropped in 3.12 — that is what pinned the old runtime to
  Python 3.10, nine weeks from end of life. impyla reaches GSSAPI through
  puresasl and pykerberos, so the whole thing builds on the same interpreter as
  the rest of the package, and the container image moves to Python 3.13.
  The driver lives behind a new `impala` extra; without it metadata collection
  reports `impala.metadata_driver_unavailable` and everything else runs as
  before. `metadata_impala_shell` is still accepted in config and ignored, so a
  deployment can drop the setting after the image, not before it.

- Three places that needed one field from the Online History view no longer
  build every presented row to get it. The scope parts and the warning
  messages are derived from the summary alone, and the empty message needs
  only how many cases there are, but each call presented all of them first —
  500 rows on the deployment measured, 2.85 of the page's profiled seconds
  spent in `present_recent_scan_case_row`. The values are unchanged, which a
  test now pins against the full view.
- One Online History render now reads the retained history once instead of
  three times. The case detail context, the scan results and the inbox status
  each asked for the same summary, and each read loaded and sanitized every
  materialized record again: against a day of production history the page
  spent 30.7 of its 40 profiled seconds inside three identical loads. The
  memo is scoped to the render rather than to a span of wall-clock time, so
  it cannot serve a stale answer to a later request, and a caller that has
  not opted into the scope loads exactly as before.
- Sanitizing a string for a log or a retained record is now remembered.
  Retained history is overwhelmingly repetition — reason codes, severities,
  field names, statuses — and every page that reads a batch of records
  re-derives the same answers. One production Online History render passed
  93698 strings through the sanitizer drawn from 701 distinct values, and
  spent 14.7 of its 40 profiled seconds inside the host redactor. The result
  is a pure function of the input, so it is cached, bounded, and skipped
  entirely when a caller supplies secrets, since those change the answer
  without changing the text. On a corpus shaped like that page the sanitizer
  runs 33 times faster from cold.
- Recent history reads an Impala `exception` as the failure it is. Impala
  reports a failed query with that status, and the Recent path knew only
  `failed` and `error`, so such queries scored nothing for having failed and
  slipped past the filter that keeps failures out of the selected set — two
  bugs that hid each other, since the query was still collected, just for the
  wrong reason and with the wrong priority. One failure vocabulary now backs
  the summary scorer, the failed-query filter and the status ordering, so a
  new engine spelling has one place to be added. On the deployment where this
  surfaced, 286 of about 68000 collected summaries carry that status, dozens
  of them failing after fifteen minutes of work.
- `query-doctor-recent-history-operator-readiness` can now age the evidence it
  reads. Every other check asks whether a retained payload's contents are
  acceptable, which a producer that stopped writing keeps satisfying: its last
  good summary stays on disk and the audit keeps reporting `ready`.
  `--max-evidence-age-minutes` blocks when the collector summary was observed
  longer ago than that, when its observation time is unreadable, and when no
  collector summary was supplied at all. Without the option the audit behaves
  exactly as before. Only the collector summary carries an observation time, so
  the Postgres readiness and profile-worker summaries are still judged on
  contents alone.
- Recent history keeps the query id the engine assigned instead of running
  it through host redaction. The host pass reads the colon in an Impala id
  as a host/port separator, so an id whose low half starts with a digit and
  whose high half starts with a short role prefix was stored as
  `host_NN:<low half>`. Nothing can be fetched under that id, and the
  profile worker retried it until its attempts ran out.
  `sanitize_identifier_for_log` is the same sanitizer without the host pass;
  secret, credential, and header redaction still apply, and free-form text is
  unchanged. The summary row, the profile job, the analysis cache, and the
  artifact row now agree on one id.
- The installed self-test now walks the direct Impala route as well as the
  Cloudera Manager one. It parses a synthetic query list holding one finished
  and one in-flight query, checks the in-flight entry is normalized rather than
  passed through as `RUNNING`, writes both as raw-free summaries into SQLite,
  and reopens the store through a fresh instance, so a Recent history that does
  not survive a restart fails on the workstation instead of on a cluster. A
  public-safe `query-doctor-config.direct-impala.example.json` joins the minimal
  and full templates: one direct Impala cluster, SQLite history, no Cloudera
  Manager, no LLM. Tests drive it through the real batch config builder and
  preflight rather than only reading its JSON. `first-path.md` gains a fourth
  door covering that route from install check through one bounded
  discovery-only pass to a restart of the local UI. No new collector was added
  and the direct-source support claim did not change.

- The README is now a landing page rather than the full contract. Its first
  command runs without any input the reader has to obtain, the synthetic demo
  leads instead of sitting several hundred lines down, and the one-profile path
  follows it. The support contract, the What Is / Is Not scope lists, the Trino
  and Spark detail, the direct-Impala history depth note, and the supported
  deployment shape moved to `support-boundary.md` with a Russian companion under
  `i18n/ru/`; the three entry paths moved to `first-path.md` with their setup
  options and troubleshooting unchanged. No support claim changed. Tests that
  pinned individual sentences to `README.md` now assert against the document
  that carries the claim, so a landing page can stay short without dropping a
  guarantee.
- Added a browser analyzer prototype under `web/`. It runs the deterministic
  analyzer in the visitor's browser through Pyodide, so an exported Impala text
  profile can be diagnosed without a server, an upload, or any request after the
  page loads. The analyzer core needed no changes, and the page applies the same
  default redaction `query-doctor-analyze` applies during case staging before
  rendering facts. The prototype is not deployed, not linked from the README,
  and not part of the published distribution.
  metadata across public documentation, source, deployment assets, packaging,
  tests, and release tooling. Its public-release mode scans every commit's
  metadata and UTF-8 text blobs after the configured public base using a
  required private fingerprint file outside the checkout; no marker-derived
  hashes or lengths are committed publicly. The upstream Helm chart remains
  platform-neutral and keeps typed generic user-provided pod labels and
  annotations while rejecting selector-owned label overrides; downstream
  platform overlays own controller labels, rollout metadata, and runtime-bundle
  wiring.
- Reworked the pasted-SQL Query Optimizer first screen so its one-statement
  scope, bounded local parsing and referenced-table metadata behavior, and
  no-execution/no-echo safety boundary are visible before input. The larger
  monospaced editor and adjacent action guidance remain legible from mobile
  through desktop without changing POST behavior, trust validation, or engine
  support.
- Agent code-graph explanations can now emit graph-ranked repository context at
  `fold`, `preview`, or `full` detail under one explicit source-line budget. An
  optional local JSONL ledger stores only file hashes and emitted line ranges,
  skips unchanged ranges on later calls, and invalidates them after edits. The
  graph can also build an on-demand Python symbol index for `--symbol` queries,
  rank exact classes, functions, methods, and failing tests first, and start
  preview output at the matched symbol instead of the beginning of its file.
  Ordinary file-level calls do not build the symbol index, unsupported languages
  keep the existing file fallback, and the explain, changed-scope, merge-risk,
  and summary interfaces remain compatible.
- Query Inbox results are now centered on row choice. Needs-attention,
  worth-reviewing, and all-analyzed rows show one short deterministic
  classification instead of inline primary and score-reason evidence. Repeated
  workloads now use seven columns for workload summary, priority, p95, total
  impact, top owner, and the Details path; runs remain in the summary while
  p50, pool, bottleneck, and supporting evidence stay in Workload Details. The
  change preserves URL filters, sorting, row navigation, empty-state recovery,
  raw-free rendering, and selected-case safety boundaries.
- Helm-managed CloudNativePG history can now use the controller-generated
  application Secret directly. Leaving `cnpg.existingOwnerSecret` empty lets
  CNPG generate `<cluster-name>-app`; Query Doctor references its `uri` key
  without duplicating the password into a separate DSN Secret. External owner
  and DSN Secrets remain supported, and the chart still renders no Secret
  objects or inline database values.
- Helm configured deployments can now keep the long-lived web Kerberos cache
  current with a bounded `kerberos-ticket-renewer` sidecar after the existing
  initContainer obtains the initial ticket. The sidecar reacquires credentials
  from the referenced external Secret, shares only the cache with the web
  container, suppresses raw Kerberos command output, and emits generic refresh
  status. Finite Recent summary collector and profile worker CronJobs retain
  their one-ticket-per-Job init flow so they can terminate normally. This does
  not add browser authentication, render Secret values, or expose the keytab to
  the web container.
- Kubernetes release validation now includes opt-in staging gates for one
  Kerberos cache refresh and one installed Online History
  collector/worker/operator-readiness cycle. They require the installed
  Deployment and CronJobs to use the same candidate image, compare only cache
  timestamps and raw-free UI markers, print no ticket contents, Job logs, SQL,
  profiles, or retained page contents, and remove only their temporary Jobs.
- Query Doctor now parses one already-provided Impala EXPLAIN artifact through a
  case-contained, byte/line/node/fragment-bounded loader and a pure parser. The
  analyzer adds only allowlisted raw-free optimizer-intent, estimate, coverage,
  limitation, and conservative structural-link facts to its result; existing
  JSON-output workflows serialize them in `analysis.json`. Raw plan text,
  relation/predicate/literal content, paths, and engine-local identities are not
  retained. Missing, ambiguous, unsafe, oversized, empty, or partially mapped
  inputs degrade without failing profile analysis. The facts do not affect
  scoring, recommendations, primary diagnosis, reports, browser surfaces,
  `engine_fact_boundary_v1` or engine selection, and Query
  Doctor still does not execute SQL or generate EXPLAIN. The parser now also
  accepts bounded modern fragment host/instance syntax, `PLAN-ROOT SINK`, a
  leading UTF-8 BOM, operator-specific bare partition attributes, current
  `HDFS partitions` scan detail, and the structural portion of modern stored
  statistics blocks. Synthetic coverage now spans minimal-, standard-,
  extended-, and verbose-like plan detail plus current fragment resource
  headers. Stored relation/column names and non-projected resource or pipeline
  detail are discarded; only existing aggregate scan estimates and
  table/column statistics availability states are retained. Opaque expression
  sections, malformed tuple detail, unexpected stored-statistics content, and
  malformed or overlong structural boundaries fail closed instead of injecting
  later typed estimates or reusing stale fragment context. Correlation now
  distinguishes an unbound external artifact from no accepted plan source while
  keeping statement and execution identity unknown.
 - Agent documentation now keeps focused test selection in `test-matrix.md` and
  routes long live-system and retained-evidence sequences to their canonical
  Kubernetes, owner-raw, Trino, Spark, and Impala runbooks. Deployment Markdown
  under `deploy/` is now included in the public-doc and local-link audits, and
  local Markdown fragments are checked against generated or explicit anchors.
  Resolved code-audit guard history moved to a public archived snapshot while
  active risks and current Trino promotion boundaries remain in the active
  audit. These documentation and guardrail changes do not broaden engine
  support, owner-raw access, SQL execution, or raw-data exposure.
- Agent guidance now follows a focused instruction chain: root `AGENTS.md`
  keeps non-negotiable safety/support/Git invariants and source routing,
  `agent-quickstart.md` owns the operational sequence, and the compact
  `agent-playbook.md` describes only change-type deltas. Documentation preflight
  no longer loads the full Codex handoff for every Markdown edit or unmatched
  path; config, packaging, and deployment paths now have an explicit focused
  route before the guarded unmatched fallback. Active-doc, public-doc, link,
  focused agent-tooling, and whitespace checks remain routed explicitly.
  Mutable Trino/Spark capability inventories stay in the engine support matrix
  and code map, and the durable handoff no longer carries worktree restart or
  backlog continuation notes. This is an agent-context and semantic-drift
  cleanup; it does not relax SQL, raw-data, owner-raw, engine-support,
  validation, or Git safety boundaries.
- Cloudera Manager, direct Impala daemon, and optional Prometheus HTTP readers
  now treat incomplete response reads as sanitized transport failures. Direct
  Impala optional `/profile_docs` and `/admission?json` probes, daemon identity
  lookup, query-list/profile fallback paths, and Prometheus runtime metric
  summaries degrade through existing unavailable or retry states instead of
  surfacing raw tracebacks; this does not change support scope, SQL execution,
  or raw-output behavior.
- Query Inbox can now read configured Recent history storage as a bounded
  read-only Online History view. It renders retained raw-free summary rows
  without raw SQL, profile text, local store paths, or raw artifact names, keeps
  rows without materialized case artifacts read-only, and uses discover-only web
  refreshes for history-backed configured scans so the path updates retained
  summaries and profile-job planning without synchronous profile analysis,
  metadata SQL collection, LLM reports, optimizer jobs, generated SQL, or SQL
  execution.
- Online History now projects current Recent profile worker lifecycle state
  from the history store into raw-free row status and coverage counts. Pending,
  processing, retry-pending, analyzed, and failed worker states remain visible
  in the Query Inbox status banner as aggregate profile-loop, separated
  profile-state, normalized error-code, retained-row, and Details-ready metrics
  without exposing raw profile bytes or enabling Details before the analysis
  cache is materialized. Retry-pending and failed rows also surface the
  normalized profile-worker error code when one is retained, without exposing
  raw transport errors, subprocess output, profile bytes, local paths, or raw
  artifact names. The status banner now also derives safe next-step labels
  from retained states and aggregate counters for retry, failed, waiting, and
  materialized backlog states without trusting retained free-form text.
  Rediscovered summaries do not reset a progressed worker state back to
  not-collected.
- Online History now distinguishes retained summary count, displayed row cap,
  and real analyzed-profile count in the Recent results coverage line. The
  bounded 500-row inbox projection is labeled as shown rows instead of analyzed
  cases, Details-ready counts can use aggregate materialization evidence beyond
  the displayed page, and direct history-store backlog health can surface
  raw-free pending/retry/leased/failed profile-worker counts even when the
  operator-readiness CronJob summary is not configured.
- Batch maintenance CLIs, including `query-doctor-recent-profile-worker`, can
  now use `active_cluster_key` or the only `clusters[]` entry as the default
  source when no explicit source flags are provided. Multi-cluster configs
  without a default still fail with an explicit safe configuration error instead
  of falling through to missing Cloudera Manager settings.
- `query-doctor-recent-profile-worker` no longer applies the Recent scan
  `--triage-profile-limit <= --cm-inspect-limit` validation to maintenance
  runs, so retained profile jobs can be processed from configs whose analysis
  budget is higher than the summary discovery page size.
- Online History now shows Recent summary collector freshness from the latest
  retained planning timestamp. The Query Inbox banner can flag a stale
  discover-only producer separately from profile-worker backlog health and
  derives a safe collector next step without exposing Query IDs, source keys,
  local paths, raw collector payloads, or retained free-form text. Configured
  web installs can now also point `recent_history_collector_summary_json` at a
  retained `query_doctor_recent_history_collector_v1` summary so the same
  banner shows the producer's last allowlisted status, age, recorded-row, and
  planned-job counters; invalid or unsafe summaries degrade to
  blocked/unavailable status without rendering paths, raw JSON, raw errors, or
  retained free-form text. Batch Recent also mirrors the same allowlisted
  collector summary fields into the optional `--progress-jsonl` stream for
  recorded, disabled, warning, and failed producer states without emitting raw
  SQL, Query IDs, local paths, raw errors, or retained free-form text. Helm
  configured installs now wire the scheduled `recentSummaryCollector` CronJob
  to retain that progress JSONL stream on the case PVC under
  `recentSummaryCollector.progressJsonl`; chart validation keeps the progress
  path under `persistence.mountPath` and separate from `summaryJson`.
- Online History can now open a raw-free Details snapshot for analyzed rows
  that have a ready `recent_analysis_cache` payload linked through available
  `fingerprint_only` profile-artifact metadata. Rows without that materialized
  cache remain read-only. The snapshot includes only allowlisted score,
  recommendation, aggregate metadata coverage, and processing timing fields;
  it does not expose profile bytes, profile fingerprints, storage keys, local
  paths, LLM reports, optimizer jobs, generated SQL, or SQL execution.
- Recent profile worker completion now requires both an accepted raw-free
  analysis cache record and accepted `fingerprint_only` profile-artifact
  metadata before the retained summary is marked analyzed. Incomplete or unsafe
  materialization outcomes fail the worker job with a normalized error code
  instead of making Online History show an analyzed row without a Details-ready
  snapshot.
- Added `query-doctor-recent-profile-remediation` as a dry-run-first
  maintenance CLI for bounded recovery of terminal failed profile jobs. The
  command can explicitly `--apply` a limited failed-job requeue after an
  operator fixes collection or materialization settings, resets selected jobs
  to pending with a fresh attempt budget, and emits only aggregate counts,
  issue codes, mode, and next-step text without exposing Query IDs, retained
  error values, source filter values, DSNs, local paths, raw SQL, profile text,
  or raw artifact names.
- `query-doctor-recent-history-operator-readiness` can now accept an optional
  retained `query_doctor_recent_profile_remediation_v1` summary and project its
  dry-run/apply counts into the raw-free operator handoff. Query Inbox Online
  History shows only allowlisted remediation mode/counts and a recomputed safe
  next step; it does not trust retained free-form remediation text and does not
  run remediation from the browser or readiness audit.
- `query-doctor-recent-history-operator-readiness` can now accept an optional
  retained `query_doctor_recent_history_collector_v1` summary and include the
  scheduled collector producer status, freshness, and aggregate counters in
  the raw-free operator handoff. Not-ready collector states block the handoff
  while still projecting safe producer state; missing optional collector
  summaries remain non-blocking.
- Helm configured installs can now render a disabled-by-default
  `recentHistory.postgres.profileRemediation` CronJob that writes a raw-free
  dry-run-only `query-doctor-recent-profile-remediation` summary to the case
  PVC for operator readiness. The chart passes `--dry-run`, does not render an
  apply/requeue remediation job, and mounts only the Postgres DSN Secret and
  case PVC for that producer.
- Helm configured installs can now render a disabled-by-default
  `recentSummaryCollector` CronJob that runs
  `query-doctor-batch-recent --discover-only` against Postgres Recent history.
  It plans retained summary rows and profile jobs and writes a raw-free
  collector run summary to the case PVC without collecting profiles, running
  metadata SQL, LLM reports, optimizer jobs, or operator-readiness audits.
- Query Inbox Online History can now project a configured retained
  `query_doctor_recent_history_operator_readiness_v1` summary into the status
  banner. The web path shows only allowlisted readiness, evidence, reason-code
  labels, schema, worker, worker-materialization, worker next-step,
  profile-backlog, retention, and profile-remediation counters, while invalid,
  wrong-kind, or unsafe summaries degrade to safe blocked/unavailable status
  without exposing the configured path, raw JSON, raw SQL, profile text, local
  store paths, retained free-form remediation text, or raw artifact names. The
  worker and remediation next-step values are derived from safe counters
  instead of retained free-form text.
- Recent profile worker summaries now include aggregate profile-backlog health
  counts for pending, retry-pending, leased, stale leased, and terminal failed
  jobs in the configured source scope. Operator readiness requires that
  raw-free health block before accepting a worker summary, and Online History
  projects only those allowlisted counts plus a recomputed backlog next step
  without exposing Query IDs, lease owners, source filter values, retained
  error values, local paths, raw SQL, or profile artifacts.
- Helm configured example values now enable the default Online history lane
  with PVC-backed SQLite storage, 5000-row Recent profile planning, and bounded
  raw-free retention defaults so configured Kubernetes installs do not fall
  back to the old 50-query history-refresh view or unbounded local history
  growth.
- Recent profile-artifact metadata now has an explicit `fingerprint_only`
  storage lifecycle contract. Legacy `local` metadata labels normalize to that
  contract, path-like keys are rejected, and storage kinds that would retain
  profile bytes, local paths, object names, or external artifact references are
  rejected until a bounded delete implementation exists.
- Added `query-doctor-recent-history-operator-readiness` for retained raw-free
  Recent history operator evidence. The command validates already written
  Postgres readiness and profile-worker summary JSON files, plus an optional
  retention summary, rejects unsafe retained fields or values, emits one
  path-free handoff summary with accepted raw-free operation counters for
  schema readiness, profile-worker jobs and materialized records, and retention
  deletes, and does not contact Postgres, Kubernetes, query engines, or profile
  collectors.
- The Helm chart can now render an optional configured-mode
  `recentHistory.operatorReadiness` CronJob after Recent history Postgres,
  Postgres readiness, and the Recent profile worker are enabled. The chart
  retains the upstream Postgres readiness, profile-worker, and optional
  retention summaries on the case PVC, then runs the operator-readiness audit
  over only those raw-free summaries; the audit CronJob does not mount Query
  Doctor config, collection credentials, the Postgres DSN Secret, Kerberos
  material, or source endpoint configuration.
- `query-doctor-recent-profile-worker` now removes its worker-owned temporary
  `profile-worker-cases/job-*` directory after each processed job while keeping
  Recent history storage raw-free. It still does not store raw profile bytes in
  the history database.
- Added `query-doctor-recent-history-retention` and optional Helm
  `recentHistory.postgres.retention` wiring for scheduled raw-free Recent
  history pruning. The maintenance command prunes only configured old summary,
  terminal profile-job, analysis-cache, and profile-artifact metadata rows,
  returns aggregate delete counts, and does not contact query engines, discover
  queries, collect profiles, run metadata SQL, run LLM reports, run optimizer
  jobs, mount collection credentials, or delete external artifact objects.
- Helm configured deployments that enable Recent history Postgres now run the
  raw-free `query-doctor-recent-history-postgres-readiness --json
  --fail-on-warning` check as a web pod initContainer by default. The check
  verifies only Secret/env DSN handoff and schema initialization, does not
  print DSNs, hostnames, credentials, paths, Query IDs, or raw payloads, and
  does not contact query engines or run profile collection. Operators can
  disable it with `recentHistory.postgres.readiness.enabled=false` only when an
  equivalent platform gate is managed outside the chart.
- The Helm chart can now render an optional configured-mode
  `recentProfileWorker` CronJob after Recent history Postgres is enabled. The
  CronJob runs `query-doctor-recent-profile-worker` with raw-free JSON output,
  metadata collection off, top reports disabled, source-filtered Postgres job
  claims, and the same external Secret/config/PVC/Kerberos wiring as the web
  pod. It does not run LLM reports, optimizer jobs, generated SQL, SQL
  execution, metadata SQL collection, browser raw-source rendering, or raw
  profile storage in the database.
- Agent code graph tooling now records safe aggregate usage by default in
  shared local state outside task worktrees, so adoption metrics survive
  worktree cleanup. Agents can review aggregate counts with `--usage-summary`
  or opt out for one run with `--no-record-usage`; the JSONL records still
  contain only counts and runtime, not changed file paths or graph output.
- Agent code graph usage summaries now include daily activity and a local
  `main` reflog proxy for merge-risk runs before merge events, giving agents a
  safer adoption signal without exposing branch names, changed paths, or raw
  graph output.
- Agent code graph tooling now includes `--merge-risk` to show pre-merge exact
  file overlaps and area overlaps against `main` and sibling worktrees before
  agents try to merge task branches.
- Web UI now treats Query Inbox as the default first screen. When a safe
  `batch_summary` or materialized case set is already available, the results
  table renders before the collapsed New scan controls; empty sessions still
  open directly to the bounded scan form. The first screen now also shows a
  raw-free Inbox status strip for empty, ready, running, partial, and stale
  summary states. Stale is computed only from parseable safe summary freshness
  fields, not local paths or artifact mtimes. When materialized results exist,
  the status strip includes safe source/window/time-range/query-type scope chips
  from the materialized summary and allowlisted result presets for the existing URL
  filters, including attention, workload, stats, rewrite-opportunity, all-cases,
  and spill-evidence views. Query Inbox also exposes allowlisted URL scope
  filters for source, workflow, window, UTC time range, and query type; if those
  filters do not match the current materialized snapshot, the page shows a safe
  filtered state and New scan controls instead of showing mismatched rows. That
  New scan form is prefilled from selected safe
  source/window/time-range/workflow/query-type URL filters when they map to
  supported scan controls, so the selected safe source, running/finished
  workflow, numeric lookback, exact finished-query range, and short query type
  identifier can be materialized without manual re-entry. Result presets,
  result toolbar links, spill filtering, and pagination preserve those safe
  scope filters without echoing arbitrary query
  parameters. The collapsed New scan form also inherits safe
  source/window/time-range/workflow/query-type refresh defaults from the same
  materialized scope so operators can rerun the shown snapshot shape without
  restoring those parameters by hand. New scan submit and job redirects now
  keep only the same allowlisted safe scope filters, so a running job page does
  not fall back to a stale mismatched snapshot while the new scope materializes.
  Window, UTC time-range, and query type scope filters now also include inline
  controls that update only the allowlisted `inbox_window`, `inbox_from`,
  `inbox_to`, and `inbox_query_type` URL filters. Result toolbars and Query
  Inbox presets also include allowlisted view-only owner/pool tag,
  opaque owner/pool value, lifecycle/readiness/action filters for owner-tagged
  rows, pool-tagged rows, concrete safe owner/pool values, clean analysis,
  status follow-up, metadata availability, validated reports, optimizer
  guidance readiness, and recorded action outcomes. Concrete owner/pool value
  filters use server-generated opaque URL tokens recomputed from the current
  materialized rows rather than raw owner or pool values. Owner/pool value
  chips now show safe row counts, and empty result tables name the matching
  active value chip when the token is present in the current snapshot. Active
  result filters now also expose explicit Clear filters links in Query Inbox,
  the Results toolbar, and matching empty result tables; those links preserve
  safe materialized scope and spill state while removing only view-only result
  filters. Empty result tables now also offer safe one-click actions to remove
  spill filtering or switch to All analyzed while preserving the same allowlisted
  scope/result filters. Query Inbox result group presets now show safe row or
  workload-group counts recalculated from the current active result filters and
  spill state, matching the Results toolbar counts before the operator opens a
  group. The Results toolbar now also exposes allowlisted URL-driven sorting for
  default group rank, priority, duration, safe start time, and impact; sorting
  reorders only already materialized raw-free rows or workload groups and is
  preserved by result links and pagination. Query Inbox now adds shareable view
  presets for common analyst paths such as needs-attention-by-duration,
  spill-impact, rewrite-priority, and owner/pool-priority; those links compose
  only existing allowlisted scope, result-filter, spill, and sort URL state and
  do not start scans, reports, optimizer jobs, generated SQL, or SQL execution.
  Zero-count presets are visually muted while remaining available, and the
  Results toolbar now shows a short active-filter summary for spill, owner/pool,
  lifecycle/readiness, report, optimizer, and outcome filters. Query Inbox also
  surfaces active safe scope/result filters as status-strip chips, and the
  Results toolbar shows a compact filtered row/group count when result filters
  or spill filtering narrow the selected view. The Results toolbar also shows
  a compact view-state summary and shareable URL label built only from the same
  allowlisted group, scope, filter, spill, and sort state. Those
  filters narrow only already materialized raw-free rows and workload groups,
  preserve state through result links, spill filtering, and pagination, and
  are not submitted as New scan scope. Running scans drop exact time-range
  scope before job redirect because Running remains a live snapshot.
  Scope chips, refresh defaults, and job scope redirects never include raw user
  or pool names, source hostnames, local paths, raw artifact names, or query IDs.
  This does not auto-run collection, LLM reports, optimizer jobs, generated
  SQL, or SQL execution.
- Query Inbox now keeps the top status strip focused on inbox state, safe
  counts, and current materialized scope while moving its safe scope filters,
  view presets, result filters, and New scan link into a compact Filters and
  views disclosure. The Results toolbar remains visible by default, so
  materialized rows appear earlier in the first viewport without changing raw
  data boundaries, URL allowlists, scan behavior, report generation, optimizer
  jobs, generated SQL, or SQL execution. Public README screenshots were
  refreshed from the synthetic demo pack for the updated first-screen layout.
- 0.11.0 adds a supported containerized web deployment path. The new Docker
  image runs `query-doctor-web` as a fixed non-root UID/GID, defaults to the
  safe read-only `--public-demo` mode, exposes port 8765, and carries a
  liveness healthcheck against `/healthz`.
- Kubernetes deployment assets now live under `deploy/kubernetes/`. The
  `public-demo.yaml` manifest runs the synthetic demo without credentials and
  denies pod egress; `configured-web.yaml` is a private operator template with
  mounted config, externally created credentials Secret reference, PVC-backed
  case storage, security contexts, and liveness/readiness probes.
- The upstream Helm chart now lives under `deploy/helm/query-doctor`. It
  renders safe public-demo and configured private web modes, ships a values
  schema, supports generic user-provided pod labels and annotations, and is
  covered by a render/audit/kubeconform smoke script.
- Configured Kubernetes web deployments now use and audit a Recent-ready web
  pod resource baseline of `250m`/`512Mi` requests and `2`/`2Gi` limits, so
  the supported configured path no longer inherits the smaller public-demo
  memory profile.
- Non-local configured web binds now default to a conservative Recent scan
  shape unless private config overrides it: profile analysis limit `50`,
  overall Recent parallelism `4`, metadata parallelism `2`, and metadata top
  limit `10`, reducing Kubernetes OOM risk for shared configured installs.
- Finished-query web Recent scans now reuse already analyzed profiles from
  prior local web batch outputs when the Query ID matches, the previous case
  completed successfully, and the scan remains in the same safe redacted shape.
  The reuse path copies artifacts into the new batch output, records aggregate
  reuse counters in `batch_summary.json`, skips duplicate collection/analysis,
  and requires an explicit profile reuse contract in the prior summary before
  reusing artifacts.
- Configured Kubernetes and Helm installs can now persist the web Recent
  analyzed-profile reuse cache across pod restarts by setting
  `recent_batch_root` to the dedicated temp-backed case-PVC cache mount. The
  web startup, chart validation, and batch CLI keep the batch root temp-backed
  and dedicated to `query-doctor-*` outputs.
- Web Results coverage now shows the aggregate reused-profile count for Recent
  runs that reused analyzed artifacts. Re-submitting the same finished Recent
  scan while it is still running now redirects to the existing job instead of
  launching duplicate profile collection and analysis.
- Recent batch runs can now opt in to a local SQLite `recent_history_db` that
  stores raw-free summary history for every discovered candidate, including
  bounded summary signals, selected/suspicion reason codes, and profile status
  without retaining raw SQL or profile text. Discover-only history runs also
  plan pending profile jobs for suspicious or selected summaries under the
  existing Recent profile budget. Batch summaries record only aggregate
  history-store status, summary count, and planned-job count, not the SQLite
  path.
- Recent history storage now has a backend-neutral contract with SQLite as the
  dependency-free local adapter and optional Postgres as the configured
  production storage target for raw-free `recent_query_summary`. Postgres mode
  reads its DSN from a Secret/env-provided variable, requires the optional
  `postgres` package extra or image build arg, and keeps batch summaries
  path-free and aggregate-only. The Helm chart can now wire that DSN from an
  existing Secret key in configured mode without rendering Secret objects or
  inline values.
- Recent history storage now includes raw-free `recent_profile_job`,
  `recent_analysis_cache`, and `recent_profile_artifact` metadata schemas plus
  a deterministic profile-budget planner that ranks already retained summary
  records into pending profile jobs by suspicion score, selected-candidate
  status, and duration. Discover-only Recent runs now enqueue those jobs for
  the shared worker.
- Profile-budget storage now supports atomic claim semantics for pending or
  expired `recent_profile_job` rows. SQLite uses a local transaction lock for
  the dependency-free adapter, and Postgres uses `FOR UPDATE SKIP LOCKED` for
  CNPG workers. Claims can be filtered by engine/source/source key, normalize
  the lease owner, increment attempts, and return only raw-free job records.
- Profile-budget storage now supports owner-guarded lease renewal, completion,
  retryable failure, and terminal failure transitions for leased profile jobs.
  Retryable failures clear the lease and return the job to `pending`; terminal
  failures move it to `failed`; completion clears lease and error state. Stored
  failure reasons are safe error codes only, not raw exception messages.
- `query-doctor-recent-profile-worker` now processes queued Recent profile jobs
  from SQLite or Postgres/CNPG. It claims only jobs for the configured source,
  collects bounded profiles through existing Impala Recent collectors, runs
  deterministic analysis with metadata mode off, and writes raw-free
  analysis-cache plus profile-artifact metadata. It does not run LLM reports,
  Query Optimizer jobs, generated SQL, metadata SQL collection, browser raw
  source rendering, or raw profile storage in the history database.
- Recent analysis cache storage now has SQLite and Postgres upsert/load APIs
  keyed by engine/source, Query ID, profile fingerprint, and analyzer contract.
  Cached payloads are sanitized as raw-free JSON and scrub dangerous raw-key
  fields such as raw SQL, profile text, metadata, paths, artifact names, model
  names, and secrets before storage.
- Recent history storage now includes a raw-free `recent_profile_artifact`
  metadata table for selected profile artifacts. It stores compatibility keys,
  status, size, and an opaque storage key only; it rejects path-like storage
  keys and does not store raw profile bytes, local paths, or artifact
  filenames.
- Recent history storage now has an explicit retention-pruning API for SQLite
  and Postgres. It can delete old summary rows, terminal profile jobs,
  analysis-cache records, and profile-artifact metadata by cutoff timestamp,
  returns aggregate counts only, and intentionally leaves pending or leased
  profile jobs untouched. Batch Recent can opt in through positive
  `recent_history_*_retention_days` config fields or matching CLI flags;
  retention output remains aggregate-only.
- The Helm chart can now optionally render a configured-mode CloudNativePG
  `Cluster` for the Recent history Postgres backend. It remains disabled by
  default, requires configured mode plus `recentHistory.postgres.enabled`, and
  keeps CNPG owner credentials and the Query Doctor DSN in existing Secrets
  instead of rendering Secret objects or inline database values.
- `query-doctor-recent-history-postgres-readiness` now checks the configured
  Recent history Postgres DSN env and schema initialization from the target
  runtime environment. Its JSON/text output is raw-free and reports only status,
  check ids, issue codes, and aggregate booleans.
- Container images now include Kerberos client tools and the isolated
  `impala-shell` runtime used by configured Impala metadata refresh, while
  keeping credentials, keytabs, ticket caches, and metadata settings outside
  the image.
- The image smoke now imports the native `sasl` module from the isolated
  `impala-shell` runtime, catching missing runtime shared libraries before a
  Kubernetes metadata refresh reaches production.
- Added a bounded configured Kubernetes metadata smoke that verifies the live
  web pod's Kerberos cache and metadata runtime, submits one Recent scan with
  `metadata_top_limit=1`, requires collected or partial metadata with table
  context, and reports only aggregate metadata status and table-context
  counters. The smoke marks its service scan as non-publishing so release gates
  do not replace the latest analyst-visible UI batch result.
- The Helm chart now has an optional configured-mode Kerberos initContainer
  flow that references an existing Secret, refreshes a ticket cache under
  `/tmp/query-doctor-krb5/`, and mounts only the cache plus optional
  `krb5.conf` into the web container.
- The configured Helm example now leaves chart-owned ingress disabled so shared
  deployments do not accidentally publish the configured web Service directly;
  platform-owned SSO/auth front doors such as oauth2-proxy/Keycloak should route
  to the chart Service instead.
- Added a Kubernetes auth-front-door runbook and
  `scripts/audit_kubernetes_auth_front_door.py` so configured deployments can
  check oauth2-proxy/Keycloak-style Ingress, expected issuer/client, expected
  PKCE method, compact cookie-session hardening for large AD group claims,
  upstream, Secret reference, token/basic-auth-forwarding, NetworkPolicy
  front-door isolation, and deployment-readiness wiring without printing
  hostnames, issuer URLs, redirect URLs, client IDs, Secret names, paths, users,
  label values, or case identifiers.
- Added `scripts/kubernetes_auth_front_door_smoke.py` for a raw-free live
  unauthenticated ingress smoke. It follows the external redirect chain, checks
  the OIDC authorization request, `/oauth2/callback` redirect URI, PKCE `S256`,
  optional expected issuer/client values, and token-like response header/cookie
  names without printing hostnames, redirect URLs, client IDs, state, code
  challenge, cookies, tokens, or users.
- Added `scripts/kubernetes-configured-release-gate.sh` as the configured
  Kubernetes handoff wrapper. It composes the metadata smoke, external auth
  redirect smoke, and raw-free auth-front-door resource audit without embedding
  deployment-specific URLs, client IDs, labels, retained resource dumps, or
  readiness payloads in public source.
- Kubernetes packaging now includes a synthetic self-test Job and Helm test
  hook that run only `query-doctor-self-test` without config, credentials,
  PVCs, live engine access, optimizer jobs, metadata collection, or SQL. It is
  an install confidence check, not an arbitrary command runner.
- Web deployments now expose raw-free deployment readiness at `/deployment`
  and `/deployment/readiness.json`, with a matching
  `query-doctor-deployment-readiness` CLI for pre-start checks. The summary
  reports only safe mode, bind-scope, source-type counts, probe, and boundary
  status labels; it does not print config paths, endpoints, users, header
  names, model names, or runtime internals.
- The web server now exposes raw-free JSON `/healthz` and `/readyz` endpoints
  for container and Kubernetes probes. The payload intentionally reports only
  service/probe status plus raw-output and SQL-execution false flags.
- Local/private web sessions can now upload one exported Impala text profile
  from `One Query ID`. The new `/profile/upload` route is multipart-only,
  bounded by `max_profile_bytes`, accepts exactly one file, rejects unsupported
  profile payload shapes through the same manual-profile analyzer path, removes
  the temporary upload file after staging, generates the deterministic Python
  report, and keeps raw profile text, filenames, paths, and temporary artifacts
  out of trusted browser surfaces. Public demo mode hides the form and blocks
  uploads before reading the request body.
- Added `scripts/audit_kubernetes_deployment.py` for raw-free static manifest
  checks, `scripts/kubernetes-public-demo-smoke.sh` for intentional disposable
  live-cluster public-demo smoke, and `scripts/kubernetes-self-test-smoke.sh`
  for disposable Helm self-test validation with separate Job log capture.
- Container CI now builds and smokes the image on pull requests and publishes
  `ghcr.io/alexandrefimov/query-doctor:<version>` plus `latest` from published
  GitHub Releases. Release docs now require kubeconform and container image
  smoke alongside the Python package release gates.
- Public README, Russian README, docs index, public-release readiness, release
  checklist, test matrix, package metadata, and release notes now describe
  Kubernetes support as a containerized web deployment starting point only. It
  does not add native auth, RBAC, sessions, multi-tenant isolation, an
  operator/CRD, SQL execution, or broader engine support.
- Recent scan summary presentation now consumes a materialized case index
  projection before falling back to legacy `cases`, giving the Query Inbox path
  a path-free, allowlisted row contract without changing `batch_summary.json`
  storage or source support claims.
- Roadmap, architecture, and customer-readiness docs now define the
  Recent-first Query Inbox direction: materialized raw-free cases are the
  target main production-triage path, while one-profile intake remains the
  shortest first-value path and online materialization remains bounded,
  read-only, local, and unable to auto-run LLM reports, optimizer jobs,
  generated SQL, SQL execution, broad query-history crawling, or
  shared-deployment identity logic.
- Recent scan Results now reuses the already-built safe summary view while
  rendering notices and context, avoiding repeated full presenter passes over
  large scan batches and reducing page-switch latency for broad Recent scans.
- Recent scan Results tables now paginate large result groups server-side,
  keeping broad `All analyzed`, `Worth reviewing`, and `Rewrite opportunities`
  views responsive without hiding the remaining rows.
- Recent scan Repeated workloads now renders one row per workload fingerprint
  group instead of one row per member query. The table links directly to
  Workload Details and introduced group-level runs, p50, p95, total impact,
  owner, pool, bottleneck, and severity aggregates for follow-up presentation.
- Public and agent documentation positioning now consistently describes Query
  Doctor as Impala full production triage with bounded local Trino production
  lanes. Active architecture, roadmap, release-readiness, engine-reference,
  demo, and Help docs no longer describe Trino as only beta/private-preview
  support, while still keeping broader/shared Trino expansion, Running,
  query-history crawling, product metadata collection, LLM reports, Query
  Optimizer jobs, generated SQL, SQL execution, and Spark production support
  out of scope.
- 0.10.0 release notes now summarize the bounded local Trino production
  claim, Impala-first support boundary, public demo safety boundary, and
  release validation focus without changing historical 0.9.0 notes.
- The Diagnose page now preserves the configured Trino display label when
  browser-side engine switching updates the One Query ID submit button. Legacy
  beta sources keep `Run Trino Beta`, while production-mode sources use
  `Run Trino`.
- Trino retained-list Recent now accepts coordinator query-list rows containing
  `errorCode` and `errorType` as scrub-only input fields, keeping them out of
  normalized facts, browser output, Details, reports, and optimizer guidance.
- The Diagnose Engine control no longer shows secondary support subtitles under
  the Impala and Trino choices. Unconfigured or production-mode Trino now uses
  the plain `Trino` label, while legacy beta sources still show `Trino Beta`.
- Production-mode Trino source options now use the configured Source cluster
  label without beta or capability suffixes; legacy beta sources keep the
  explicit beta capability suffix.
- Trino Recent no longer inherits Impala-only `recent_user`, `recent_pool`, or
  `query_type` defaults from local config. Those fields stay hidden in the
  Trino Recent form, while explicitly submitted unsupported filters still fail
  closed before job creation.
- Trino bounded local production support claim is now release-aligned. The
  support-gap audit reports `bounded_production_claim_pinned` and
  `broader_production_closure_status=bounded_production_claim_ready`, the
  production-closure audit reports `bounded_production_claim_ready` only when
  every retained raw-free tracking summary is accepted and collector evidence is
  linked to representative evidence, and source/product-surface machine
  statuses now use local-production labels instead of beta labels. The claim
  remains limited to retained-list Recent, one explicit Query ID, raw-free
  materialized Details, deterministic Python Report, and optimizer guidance;
  Running scans, query-history crawling, product metadata collection, LLM
  reports, Query Optimizer jobs, generated Trino SQL, SQL execution, and
  broader/shared Trino expansion remain unsupported.
- Trino browser/report regression now records a raw-free
  `production_review_browser_report_v1` profile. The audit requires regression
  families, test files, materialized route capabilities, raw-output blocks,
  unsupported-surface blocks, download regressions, and public-claim
  regressions, emits path-free production-review counts, and makes the broader
  production closure gate reject browser/report profile drift while page
  rendering, case artifact loading, Trino collection, SQL generation, SQL
  execution, and broader/shared Trino expansion remain blocked.
- Trino shared/non-local deployment hardening now records a raw-free
  `production_review_shared_deployment_v1` profile. The audit requires review
  families, deployment-config requirements, product-boundary requirements,
  capability requirements, release requirements, documentation requirements, and
  unsupported-surface blocks, emits path-free production-review counts, and
  makes the broader production closure gate reject shared-deployment profile
  drift while raw source reveal and broader/shared Trino production support
  remain blocked.
- Trino report and optimizer safety now records a raw-free
  `production_review_report_optimizer_v1` profile. The audit requires
  report/guidance families, materialized Python Report and optimizer guidance
  capabilities, raw-source policy fields, validator sentinel matrix, and
  blocked product-surface requirements, emits path-free production-review
  counts, and makes the broader production closure gate reject report/optimizer
  profile drift while LLM reports, Query Optimizer jobs, generated SQL, and
  Trino SQL execution remain blocked.
- Trino product metadata collection now records a raw-free
  `production_review_metadata_v1` profile. The audit requires the
  allowlist/source lanes, aggregate-only metadata boundary, metadata fact
  namespace, bounded sources, redaction blocks, explicit metadata SQL policy,
  product-surface blocks, and the retained product-metadata open blocker, emits
  path-free production-review counts, and makes the broader production closure
  gate reject metadata profile drift while product metadata collection remains
  unsupported.
- Trino query-linked fact coverage now records
  `operator_connector_telemetry_decision_v1`. The audit keeps connector metric
  signal as the only current bounded-supported operator/connector/telemetry
  decision, records operator-level, split-detail, and
  JMX/OpenMetrics/OpenTelemetry linkage as deliberate unsupported gaps until
  raw-free source contracts exist, emits path-free decision counts, and makes
  the broader production closure gate reject decision-profile drift.
- Trino query-linked fact coverage now records a raw-free
  `production_review_query_linked_v1` profile. The audit requires
  resource-group, stage, task, and split core query-linked families, their
  linkage scopes, one-query source granularities, and retained
  operator/split-detail/telemetry blockers, emits coverage-profile tracking
  counts, and the broader production closure gate rejects profile drift while
  keeping broad Trino support not closed.
- Owner-raw D3 now has a release-facing raw-free SSO/auth proxy support
  readiness gate at `scripts/audit_owner_raw_sso_proxy_support_readiness.py`.
  It accepts only an already passing `owner_raw_d3_deployment_bundle_v1`
  summary from the live-front-door, disabled-source rehearsal,
  source-enable canary, post-enable, and launch-closure chain before the
  project claims support for deployment behind a trusted SSO/auth proxy via
  `viewer_identity_header`. It writes an
  `owner_raw_sso_proxy_support_readiness_v1` summary with safe support status,
  final source-state enum, gate counts, and issue categories, rejects dev-only
  rehearsal evidence as insufficient, can optionally require
  `final_source_state=leave_enabled`, and does not add native SSO handling,
  contact a proxy, perform authentication, start Query Doctor, open cases, read
  source text, print artifact paths or filenames, URLs, users, header names or
  values, query ids, auth material, credentials, or raw source.
- Owner-raw D3 now has a local raw-free artifact workspace helper at
  `scripts/prepare_owner_raw_d3_artifacts.py`. It creates or preserves ignored
  disabled-source and source-enabled config templates, fail-closed front-door
  and post-enable review templates, a local operator checklist, and a deployment
  bundle summary through the `owner_raw_d3_artifact_workspace_v1` scaffold
  path, preserving existing review evidence unless
  `--replace-templates` is explicit. It can run the deployment bundle over the
  workspace, then runs the release-facing support-readiness gate by default and
  writes a local `support-readiness.summary.json`; `--skip-support-readiness`
  is explicit, and `--require-source-left-enabled` makes rollback-completed
  closure insufficient for the final support gate. The helper prints only safe
  gate status and issue categories. It does not add native SSO handling, start
  Query Doctor, open cases, read source text, decide user access, print artifact
  paths or filenames, URLs, users, header names or values, query ids, auth
  material, credentials, or raw source, and requires explicit confirmation that
  the artifact directory is local and ignored.
- Owner-raw D3 now has a raw-free deployment bundle gate at
  `scripts/audit_owner_raw_d3_deployment_bundle.py`. It orchestrates the
  existing front-door review audit, readiness gate, dev SSO rehearsal,
  source-enable canary gate, post-enable gate, launch-closure manifest builder,
  and launch-closure gate into one `owner_raw_d3_deployment_bundle_v1` summary
  with only safe gate status, counts, failed-gate names, issue categories, a
  `ready` or `blocked` deployment verdict, and final source-state enum. It can
  additionally audit a retained launch-closure manifest, suppresses child gate
  output, does not add native SSO handling, start Query Doctor, open cases,
  read source text, change source state, or decide user access, and does not
  print config paths, review paths, retained manifest paths, URLs, users,
  header names or values, query ids, auth material, credentials, or raw source.
- Owner-raw D3 launch closure now has a raw-free manifest path. The new
  `scripts/build_owner_raw_d3_launch_closure_manifest.py` writes an
  `owner_raw_d3_launch_closure_manifest_v1` manifest with one redaction-reviewed
  entry, safe relative `*.json` references to the retained front-door,
  readiness, rehearsal, source-enable, and post-enable summaries, and fixed
  limitation labels. `scripts/audit_owner_raw_d3_launch_closure.py` can now
  consume that manifest with `--launch-closure-manifest` while rejecting unsafe
  references, duplicate references, missing redaction review, mixed direct and
  manifest inputs, and summary-output overlap. The builder and manifest audit
  do not contact a proxy, perform authentication, open cases, read source text,
  read config, print paths, URLs, header names or values, users, query ids,
  auth material, credentials, or raw source, and do not add native SSO handling
  to Query Doctor.
- Owner-raw D3 now has a raw-free launch closure gate at
  `scripts/audit_owner_raw_d3_launch_closure.py`. It validates already retained
  raw-free front-door review, readiness, rehearsal, source-enable, and
  post-enable summaries as one closure chain, checks disabled rehearsal to
  operator-planned enablement to reviewed post-enable final state, and can write
  an `owner_raw_d3_launch_closure_v1` summary with only safe gate status,
  counts, failed-gate names, issue categories, a `closed` or `blocked` verdict,
  and final source-state enum. It does not contact a proxy, perform
  authentication, open cases, read source text, read config, change source
  state, print paths, URLs, header names or values, users, query ids, auth
  material, credentials, or raw source, and does not add native SSO handling to
  Query Doctor.
- Owner-raw D3 now has a raw-free post-enable canary gate at
  `scripts/audit_owner_raw_d3_post_enable.py`. It validates an already passing
  `owner_raw_d3_source_enable_canary_v1` summary plus a retained post-enable
  review checklist over boolean/enum evidence for runtime allow/deny behavior,
  raw-free denied pages and audit lines, rollback verification, monitoring, and
  final source-state closure. It can write an
  `owner_raw_d3_post_enable_canary_v1` summary with only safe gate status,
  counts, failed-gate names, issue categories, and final source-state enum. It
  does not contact a proxy, open cases, read source text, perform
  authentication, change source state, print paths, URLs, header names or
  values, users, query ids, auth material, credentials, or raw source, and does
  not add native SSO handling to Query Doctor.
- Owner-raw D3 now has a raw-free source-enable canary gate at
  `scripts/audit_owner_raw_d3_source_enable.py`. It checks an already passing
  `owner_raw_d3_rehearsal_v1` summary against a separate planned
  source-enabled config, requires explicit operator confirmations for the
  canary, unchanged front door/header mapping, absence of the CLI kill switch,
  and a rollback plan, and can write an
  `owner_raw_d3_source_enable_canary_v1` summary with only safe gate status,
  counts, failed-gate names, and issue categories. It does not start Query
  Doctor, perform authentication, enable raw source, print paths, URLs, header
  names or values, users, query ids, auth material, credentials, or raw source,
  and does not add native SSO handling to Query Doctor.
- Owner-raw D3 now has a raw-free rehearsal runner at
  `scripts/audit_owner_raw_d3_rehearsal.py`. It runs the dev SSO smoke, the
  live front-door review summary audit, the staging config preflight, and the
  aggregate readiness gate in one operator command, fails closed when any gate
  is missing or unsafe, and can write an `owner_raw_d3_rehearsal_v1` summary
  with only safe gate status, counts, failed-gate names, and issue categories.
  It does not perform authentication, enable raw source, print paths, URLs,
  header names or values, users, login secrets, query ids, auth material,
  credentials, or raw source, and does not add native SSO handling to Query
  Doctor.
- Owner-raw D3 now has an aggregate raw-free readiness gate at
  `scripts/audit_owner_raw_d3_readiness.py`. It combines the ignored staging
  config preflight with the raw-free live front-door review summary, fails
  closed when live review evidence is missing or invalid, and can write an
  `owner_raw_d3_readiness_v1` summary with only safe gate status, counts, and
  issue categories. It does not perform authentication, enable raw source,
  print paths, header names or values, users, URLs, query ids, auth material,
  credentials, or raw source, and does not add native SSO handling to Query
  Doctor.
- Owner-raw D3 now has a raw-free staging config preflight at
  `scripts/audit_owner_raw_staging_preflight.py`. It validates planned
  pre-proxy config shape for at least one `owner_raw` source, a valid trusted
  viewer header contract, disabled raw source reveal through the config or CLI
  kill switch, explicit non-local bind review, and unweakened privacy/redaction
  controls, and can write an `owner_raw_staging_preflight_v1` summary without
  printing paths, header names or values, users, URLs, query ids, credentials,
  or raw source. This preflight does not replace the live front-door review gate
  and does not add native SSO handling to Query Doctor.
- Trino production-collector closure now records retained representative
  evidence handoff status. `scripts/audit_trino_production_collector_contracts.py`
  accepts `--representative-evidence-summary-json` plus
  `--require-representative-evidence-summary` for promotion-review checks over
  already raw-free `trino_representative_evidence_audit_v1` summaries. That
  handoff now requires the `production_review_breadth_v1` profile with
  `breadth_profile_status=ready`, keeps the gate `not_closed`, and does not
  reopen retained artifacts, collect from Trino, execute SQL, or promote
  broader production support.
- Trino representative-evidence production-review tracking now requires a
  retained summary-kind mix for handoff-suite, compact-readiness,
  product-surface, and support-gap summaries, plus accepted `ok` input
  statuses. The audit still consumes only already raw-free summary payloads,
  rejects support-gap or product-surface boundary drift that would imply SQL
  execution or broader closure, and keeps the representative evidence gate
  `not_closed`.
- Trino browser/report regression now has a dev-only raw-free audit at
  `scripts/audit_trino_browser_report_regression.py`. It tracks required
  Details, Python Report, optimizer guidance, error, unsupported-workflow,
  product-surface static-boundary, and public-claim regression tests plus
  current materialized route capabilities, raw-free
  `browser_report_requirement_tracking` entries and
  `browser_report_requirement_tracking_counts` for accepted, missing, and
  invalid test/route/product-surface requirements, and prints the same
  path-free CLI counts. It keeps `trino_browser_report_regression` not closed
  and does not render pages, load case artifacts, collect from Trino, run
  report or optimizer jobs, generate SQL, execute SQL, or promote broader
  production support.
- Trino report and optimizer safety now has a dev-only raw-free audit at
  `scripts/audit_trino_report_optimizer_safety.py`. It tracks materialized
  Python Report and optimizer guidance capability contracts, raw-source policy,
  validator sentinel rejection, blocked Trino adapter validated reports,
  raw-free `report_optimizer_requirement_tracking` entries and
  `report_optimizer_requirement_tracking_counts` for accepted, missing, and
  invalid capability/policy/validator/product-surface requirements, and prints
  the same path-free CLI counts. It keeps `trino_report_optimizer_safety` not
  closed and does not run LLM reports, create Query Optimizer jobs, generate
  SQL, execute SQL, load case artifacts, or promote broader production support.
- Owner-raw D3 now has a dev-only Keycloak/oauth2-proxy compose smoke under
  `dev/sso/`. It can exercise a local OIDC login and inject the normalized
  viewer header into Query Doctor while keeping the upstream web process inside
  the compose network, disabling the original source page by default, and not
  forwarding OIDC tokens or Authorization material upstream.
  `scripts/dev_sso_keycloak_smoke.py` verifies the running local compose path
  with raw-free pass/fail output. The harness is a local unblocker only and
  does not replace the live front-door validation gate or add native SSO
  handling to Query Doctor.
- Trino product metadata collection now has a dev-only raw-free audit at
  `scripts/audit_trino_product_metadata_collection.py`. It tracks the metadata
  allowlist contract, dev-only aggregate metadata CLI summary, local aggregate
  metadata summary import, metadata fact namespace, adapter/product-surface
  blocks, raw metadata and identifier-output redaction, keeps
  `trino_product_metadata_collection` not closed, and does not read metadata,
  execute user SQL, add browser/report/optimizer metadata output, or promote
  broader production support.
- Trino broader production closure now has a consolidated dev-only raw-free
  audit at `scripts/audit_trino_production_closure_gates.py`. It pins the
  support-gap gate list, checks Trino dev-gate capability wiring, validates
  retained collector, representative-evidence, query-linked, product-metadata,
  report-optimizer, browser-report, shared-deployment, and support-gap summary
  inputs when required, checks that collector and representative-evidence
  summaries link the same retained handoff when both are present, records the
  raw-free `current_tracking_summary_status`,
  `invalid_current_tracking_summary_count`, and
  `representative_evidence_linkage_status` in machine summaries, records
  `representative_evidence_linkage_invalid_summary_count` and
  `representative_evidence_linkage_missing_summary_count`, treats
  present-but-invalid retained tracking or linkage summaries as failed
  statuses, records per-gate `gate_tracking` entries and
  `gate_tracking_counts` for accepted, missing, invalid, and not-required
  tracking inputs, prints the same path-free tracking, linkage, and
  `gate_tracking` status labels in CLI output,
  keeps broader production closure `not_closed`, and does not collect from
  Trino, execute SQL, add product surfaces, or promote broader production
  support.
- Trino broader production promotion now has an explicit twelve-slice release
  plan in the support-gap matrix, with the live-collection design pointing back
  to it. The plan defines broad Trino production support as a release claim
  rather than automatic Impala parity, keeps Running, query-history crawling,
  LLM reports, Query Optimizer jobs, generated Trino SQL, and SQL execution
  unsupported unless promoted by separate gates, and orders the remaining work
  through collector contracts/readers, representative evidence, query-linked
  facts, product metadata, report/optimizer boundary decisions, shared
  deployment readiness, browser/report regression, and final support-claim
  updates.
- Trino query-linked fact coverage now has a dev-only raw-free audit at
  `scripts/audit_trino_query_linked_fact_coverage.py`. It checks registered
  bounded compact resource-group, stage, task, split, and connector fact/source
  coverage, records operator/split-detail/JMX/OpenMetrics/OpenTelemetry gaps as
  open blockers, records raw-free `query_linked_requirement_tracking` entries
  and `query_linked_requirement_tracking_counts` for accepted, missing, and
  invalid fact/source requirements, prints the same path-free CLI counts, keeps
  `trino_query_linked_fact_coverage` not closed, and does not collect from
  Trino, execute SQL, add product surfaces, or promote broader production
  support.
- Trino product metadata collection now records raw-free
  `product_metadata_requirement_tracking` entries and
  `product_metadata_requirement_tracking_counts` for accepted, missing, and
  invalid fact/source/product-surface requirements in
  `scripts/audit_trino_product_metadata_collection.py`, prints the same
  path-free CLI counts, keeps `trino_product_metadata_collection` not closed,
  and does not read metadata, run Trino CLI, execute user SQL, add product
  metadata surfaces, or promote broader production support.
- Trino representative real-cluster evidence now has a dev-only raw-free audit
  at `scripts/audit_trino_representative_evidence.py`. It aggregates already
  retained handoff/readiness/product-surface/support-gap summary payloads for
  safe version-family, source-schema, connector-family, lifecycle,
  source-granularity, verification-scope, and retained summary-kind breadth,
  and adds the
  `production_review_breadth_v1` profile through
  `--require-breadth-profile production_review_breadth_v1` for required
  summary-kind mix, accepted input statuses, retained summary/evidence-unit,
  source-contract, source-schema, lifecycle, connector-family,
  source-granularity, verification-scope, and support-status counter breadth.
  It now records raw-free `breadth_requirement_tracking` entries and
  `breadth_requirement_tracking_counts` for accepted, insufficient, and
  not-required breadth requirements, prints the same path-free CLI counts,
  rejects raw-like retained summary content, keeps
  `trino_representative_real_cluster_evidence` not closed, and does not reopen
  packages, collect from Trino, execute SQL, or promote broader production
  support.
- Trino production-collector closure now has a dev-only raw-free audit at
  `scripts/audit_trino_production_collector_contracts.py`. It records the
  `trino_production_collector_contracts` gate as not closed, tracks existing
  local lanes, preview readers, contract-only sources, metadata boundary
  separation, open blockers, and raw-free `source_requirement_tracking` counts
  for accepted, missing, and invalid collector source requirements, prints the
  same path-free source-requirement tracking counts in CLI output, and rejects
  accidental Running, query-history, broad QueryInfo, `POST /v1/statement`, or
  `EXPLAIN ANALYZE` collector source registration.
- Trino source-contract and production-collector audits now pin
  auth-reference policy, source-schema gate, retry-policy, and fail-closed
  policy fields in the source registry, support-gap summary, collector
  requirements, per-source tracking, summary JSON, and path-free CLI output.
  Network-capable Trino source registry entries now fail audit without a safe
  auth reference and bounded retry policy, while broader/shared expansion
  remains not closed.
- Trino production-collector closure now tracks bounded reader implementations
  as explicit raw-free summary evidence. The collector audit records
  reader-status, reader-scope, CLI-role, capability, and forbidden-reader
  counters, checks reader modules, command roles, and capability surfaces
  against the code registries, rejects accidental query-history or Running-style
  reader roles, and still keeps broader/shared Trino expansion not closed.
- Trino bounded production promotion now has an explicit closure plan in the
  support-gap matrix and live-collection design. The support-gap audit records
  `broader_production_closure_status=bounded_production_claim_ready` plus the
  required closure gate list, without expanding Trino beyond the bounded local
  Recent, One Query ID, raw-free Details, deterministic Python Report, and
  optimizer guidance lanes.
- Owner-raw D3 and Trino shared hardening now have a dev-only raw-free live
  front-door review summary audit at
  `scripts/audit_owner_raw_live_front_door_review.py`. It lets operators retain
  boolean/enum evidence from real Kubernetes/proxy reviews without committing
  private URLs, paths, users, header names or values, auth subjects, logs, SQL,
  query ids, or source text, can write a fail-closed raw-free template through
  `--template-json`, and Trino shared preflight can require that summary through
  `--front-door-review-summary`.
- Trino shared/non-local deployment hardening now requires an explicit
  `--trusted-front-door-reviewed` confirmation for shared Trino configs after
  the operator verifies trusted front-door header stripping and exactly-one
  normalized viewer identity injection. The flag is propagated through the
  shared preflight and release-readiness bundle without adding Query Doctor
  native auth, raw-source reveal, or broader/shared Trino production support.
- Trino shared/internal deployment hardening now has a dev-only static preflight
  wrapper at `scripts/audit_trino_shared_deployment_preflight.py`. It runs the
  shared boundary, product-surface, support-gap, and active-docs gates, captures
  child stdout/stderr, emits only raw-free gate counts and failure categories,
  and performs no network read, live smoke, UI smoke, metadata collection, SQL
  execution, or broader/shared Trino support promotion.
- Trino shared/non-local deployment hardening now has an active public-safe
  contract at `docs/trino-shared-deployment-hardening.md`, and the dev-only
  shared deployment boundary audit checks that contract plus release/readiness
  docs for trusted front-door identity, raw-source isolation, dev-only metadata
  smoke, blocked unsupported surfaces, and no broader/shared Trino production
  support.
- Trino release hardening now includes a dev-only shared deployment boundary
  audit at `scripts/audit_trino_shared_deployment_boundary.py`. It checks
  shared/non-local Trino config shape for trusted front-door viewer identity
  and raw-source isolation, keeps Details, Python Report, and optimizer
  guidance raw-free/materialized only, records raw-free
  `shared_deployment_requirement_tracking` entries and
  `shared_deployment_requirement_tracking_counts`, and prints only raw-free
  counts and issue categories without paths, header names, users, Query IDs,
  URLs, or raw payloads.
- Trino Beta release-readiness can now include the dev-only metadata CLI
  summary smoke when explicit `--metadata-smoke-*` inputs and
  `--metadata-smoke-redaction-reviewed` are supplied. The bundle summary records
  only raw-free gate counts/classes and does not print paths, URLs, users,
  object identifiers, metadata values, CLI stdout/stderr, or raw payloads; this
  does not add product metadata collection or browser/report/optimizer
  surfaces.
- Trino now has a dev-only metadata CLI summary smoke gate at
  `scripts/trino_metadata_cli_summary_smoke.py`. It validates the safe dry-run
  plan, executes only the existing Python-owned aggregate metadata summary
  builder, round-trips the sanitized summary through the local metadata-summary
  importer, and writes only raw-free smoke or aggregate summary artifacts.
  It does not add product metadata collection, Recent, Details, trusted report,
  optimizer, Running, query-history, generated SQL, or user SQL execution
  surfaces.
- Trino now has a contract-gated local metadata CLI summary builder through
  `query-doctor-trino-metadata-cli-summary`. It validates one
  `metadata_allowlist` source contract, uses only Python-owned read-only
  metadata statements for Hive or Iceberg connector-family gates through an
  operator-installed Trino CLI, passes statement text on stdin rather than argv,
  and emits only a sanitized aggregate metadata summary or path-free safe
  summary. It does not add Recent, Details, trusted report, optimizer, Running,
  query-history, product metadata collection, generated SQL, or user SQL
  execution surfaces.
- Trino local web Recent, One Query ID, raw-free materialized Details,
  deterministic Python Report, and guidance-only optimizer output are now
  classified as local production support when `trino_support_mode=production`
  is configured. The legacy `trino_beta_enabled` path remains beta-only, and
  Running scans, query-history crawling, metadata collection, LLM reports, Query
  Optimizer jobs, generated Trino SQL, SQL execution, and broader/shared Trino
  production triage remain blocked.
- Local web Trino Details can now open deterministic optimizer guidance for
  server-owned raw-free materialized cases. The guidance is built only from
  typed normalized Trino facts and compact diagnosis attention areas, rejects
  raw SQL-like text, Query IDs, URLs, paths, auth material, connector internals,
  Impala-only wording, root-cause overclaims, and generated-query wording, and
  keeps Query Optimizer jobs, SQL drafts, LLM reports, metadata collection, and
  SQL execution blocked.
- Local web Trino Details can now open a deterministic Python Report for
  server-owned raw-free materialized cases. The report is built only from typed
  normalized Trino facts, rejects raw SQL-like text, Query IDs, URLs, paths,
  auth material, connector internals, Impala-only wording, root-cause
  overclaims, and generated-SQL wording, and keeps LLM reports and optimizer
  output blocked.
- Local web Trino Query ID and Recent-selected rows now link materialized
  server-owned raw-free case artifacts to a Trino Details page. The page reads
  only typed normalized facts, compact diagnosis, and the metadata-not-collected
  summary, and it still withholds query IDs, raw payloads, paths, LLM reports,
  optimizer actions, generated SQL, metadata collection, and SQL
  execution.
- Local web Trino Query ID and Recent-selected rows now materialize
  server-owned raw-free case artifacts: normalized boundary JSON, compact
  diagnosis JSON, metadata-not-collected summary, typed `analysis.json`, and
  `analysis_facts.md`. The artifacts are not linked as LLM report, optimizer,
  generated SQL, metadata collection, or SQL execution surfaces.
- Trino local web config now has an explicit `trino_support_mode` enum
  (`off`, `beta`, `production`). The legacy `trino_beta_enabled` key remains
  beta-only for existing local configs and is rejected when combined with
  production mode. Production mode marks the same bounded raw-free Recent, One
  Query ID, materialized Details, Python Report, and optimizer guidance lanes as
  local production support without enabling Running, metadata, LLM reports,
  Query Optimizer jobs, generated SQL, or SQL execution.
- Retained Impala North Star aggregate output now keeps raw-like
  unknown-primary reason buckets in a dedicated safe closure category instead
  of grouping them with unmapped diagnostic evidence gaps.
- Synthetic Impala primary coverage and North Star readiness now reject
  aggregate unknown-primary reason buckets that contain raw-like text; their
  committed aggregate outputs now carry safe unknown-primary category counts
  and closure labels while keeping gate fixtures raw-free.
- Retained Impala North Star readiness now rejects loop-summary coverage
  aggregates whose unknown-primary reason buckets contain raw-like text, while
  keeping the suite output aggregate and raw-free.
- Strict Impala diagnostic coverage readiness now rejects unknown-primary
  reason buckets that contain raw-like text, while retained coverage summaries
  now carry safe unknown-primary category counts and closure labels as
  aggregate raw-free evidence.
- Strict optimizer funnel readiness now rejects repeated no-recipe workload
  groups whose retained candidate reasons contain raw-like text, while keeping
  funnel output aggregate and raw-free.
- Strict stats diagnostics readiness now rejects actionable stats candidates
  whose retained candidate text contains raw-like SQL, metadata identifiers,
  paths, URLs, or secrets, while keeping audit output raw-free.
- Direct Impala Details now labels source context as `Source coverage and
  limitations` and uses analyst-facing wording for unavailable Cloudera Manager
  events, optional Prometheus runtime metrics, and bounded metadata coverage.
- The standalone Query Optimizer page now keeps the first screen focused on the
  pasted SQL field and `Analyze` action, with detailed input rules and safety
  boundaries folded into one compact disclosure. The no-execution and no-echo
  optimizer contract is unchanged.
- Recent results default rows now route analysts with an explicit `Open Details`
  next action and keep per-row table-stats and metadata status out of the
  default triage table. Aggregate coverage remains in `Scan context`, while
  stats, optimizer, and workload-specific result views keep their focused
  columns.
- Documentation now distinguishes active contracts, reference material, and
  archived snapshots in the main docs index. Historical release notes and the
  original Trino discovery spike are marked as non-current guidance, and
  research pages now carry explicit lifecycle notes.
- Russian documentation is now limited to README plus practical user/operator
  instructions for demo, setup, credentials, local smoke checks, and safety.
  Internal, agent, contributor, release, research, audit, roadmap, and
  Trino/Spark engine deep-dive Russian companion pages were removed; English
  remains canonical for those docs.
- Agent-facing documentation is slimmer and role-separated: `AGENTS.md`
  records document ownership, `agent-quickstart.md` keeps the operational path,
  `agent-playbook.md` keeps change routing, `test-matrix.md` remains the exact
  validation-command catalog, and `codex-handoff.md` keeps durable product and
  safety baseline instead of detailed command inventories. Russian
  agent/internal companion pages were removed to avoid drifting duplicates.

## 0.9.0 - 2026-06-19

- Russian web UI mode now localizes deterministic Recent Finding body copy and
  Details recommendation/explanation body copy while keeping Details headings,
  compact Recent labels, table headers, badges, raw-free analyzer artifacts,
  and technical terms in English.
- Details-page Query LLM optimizer actions now use deterministic
  `optimizer_rewrite_support` eligibility before showing or accepting optimizer
  launches. Cases classified as guidance-only, draft-disabled, not-applicable,
  or source-unavailable now show a browser-safe unavailable reason instead of a
  runnable action that later fails generically.
- Fixed Recent scan progress rendering for streaming profile collection and
  analyzer scoring: Profile collection now stays in progress until collection
  actually finishes or all selected cases report collection completion/failure,
  instead of turning green as soon as analyzer scoring starts.
- Diagnose now asks for Engine before Source cluster so Trino Beta can switch
  the source selector to a Trino-ready local source before workflow selection.
  The Source cluster selector now shows only Impala-capable sources for Impala
  and only Trino Beta-ready sources for Trino Beta. The synthetic demo pack also
  writes read-only raw-free Trino Beta demo cases in `trino_demo.json` and
  renders them without live coordinator collection, Details/trusted reports,
  optimizer behavior, generated SQL, or SQL execution.
  Manual `--batch-summary` demo startup without an explicit `--config` now
  ignores default local config discovery so synthetic screenshots and demo
  sessions cannot accidentally inherit workstation source settings.
- Unexpected web job failures now keep the browser-safe redaction boundary while
  returning workflow-specific reason codes, current progress stage, details,
  and next-step guidance for Impala Recent/Running scans, Specific Query
  analysis, Trino Beta Query ID/Recent, report generation, optimizer
  generation, and combined report+optimizer actions.
- Recent-results filters, spill toggles, and workload follow-up links now use
  root-relative targets so they keep working from job/error pages as well as
  the home and batch-result pages.
- Details-page manual optimizer rewrite validation failures now render the same
  structured safe error body with reason code, validation stage, next-step
  guidance, and raw-SQL-hidden footer while preserving deterministic validator
  categories.
- Browser-side job polling failures now render a structured safe error card
  with reason code, polling stage, and next-step guidance, including the legacy
  async job fallback path when a status payload lacks server-rendered
  `error_html`.
- Selected-case report and optimizer action failures now reuse the async job
  safe error envelope in their Details cards, including reason code, workflow
  stage, and next-step guidance while keeping subprocess output, raw SQL, local
  paths, and runtime internals hidden.
- Fixed Details report+optimizer progress polling so browser job links are built
  only from internal 32-hex job ids. Display-safe host aliases can no longer
  become `/jobs/.../status` URLs, avoiding a stalled combined action page while
  keeping malformed ids out of cancel links and status polling attributes.
- Diagnose, Running, Query Optimizer, Specific Query Details/report, job lookup,
  and isolated Spark/Trino compact web form failures now have route-smoke
  coverage for structured browser-safe error cards. Missing inputs, unavailable
  Specific Query artifacts, unknown jobs, and compact-intake validation errors
  surface stable reason codes, workflow stages, and next-step guidance without
  echoing raw SQL, local paths, selectors, subprocess output, or model/debug
  details.
- General web request, form validation, source selection, startup config,
  public-demo, manual-profile, case-artifact, report, optimizer, and outcome
  failures now carry browser-safe reason codes, workflow stages, and next-step
  guidance. Public demo generation and local config/path failures avoid
  exposing exception text or local filesystem paths in the browser.
- Impala web collection, metadata preflight, CM/direct profile lookup, report,
  optimizer, and startup TLS failures now use classified browser-safe errors
  with stable reason codes, workflow stages, and next-step guidance. Subprocess
  output, raw SQL/profile/JSON, coordinator/CM URLs, Query IDs, credentials,
  and local paths remain hidden from the browser error panel and async job
  status JSON.
- Browser-visible web failures now render a structured safe error contract with
  a product title, stable reason code, workflow stage, safe detail bullets, and
  next-step guidance. Async job status JSON carries the same raw-free envelope
  for pollers, Trino Beta Recent row failures show safe reason/next-step
  context, and Trino Beta coordinator/auth/config failures are classified
  without exposing Query IDs, coordinator URLs, auth references, local paths,
  raw payloads, or subprocess output.
- Fixed Trino Beta Recent selection for retained coordinator query lists whose
  timestamps are nested under `queryStats` and use nanosecond ISO fractions.
  The Recent window now excludes rows whose timestamps cannot be verified, and
  selected Query IDs open the existing Trino Beta One Query ID diagnosis flow
  instead of appearing as inert table text.
- `scripts/query-doctor-web-local` now refreshes configured Trino Beta
  Kerberos/SPNEGO ticket caches before starting the web UI, using the selected
  local config and existing keytab without printing principals, cache paths,
  keytab paths, coordinator URLs, or auth material.
- Fixed the local web Engine switch so Trino Beta remains selectable when the
  current Source cluster is Impala-only but another configured source is
  Trino-ready. Selecting Trino Beta now switches Source cluster to a
  Trino-ready local source, keeps Running disabled for Trino, and aligns the
  Engine segmented control spacing/style with What to analyze.
- CM Known Query ID collection now preserves the validated Impala Query ID
  separator expected by Cloudera Manager profile/detail routes while still
  rejecting unsafe Query ID path shapes, and browser job errors now classify CM
  404/profile-lookup failures with a safe operator hint instead of only showing
  a generic subprocess failure.
- Added `scripts/audit_trino_beta_release_readiness.py`, a dev-only
  one-command Trino Beta demo/release readiness bundle. The bundle runs the
  existing static boundary audits, focused tests, local-config readiness, and
  optional bounded live/UI smokes, plus the optional metadata CLI summary smoke
  when explicitly configured, without printing local config values, Query IDs,
  coordinator URLs, auth references, local paths, object identifiers, metadata
  values, CLI stdout/stderr, or raw payloads, and it does not add user SQL
  execution, product metadata collection, trusted reports, optimizer behavior,
  or production Trino support.
- Trino Beta Recent and One Query ID result pages now render an explicit
  blocked-surface status strip for Running, query-history crawling, metadata,
  Details/reports, optimizer behavior, generated SQL, and SQL execution. This
  is UI boundary polish only; it does not add metadata collection, trusted
  reports, optimizer behavior, SQL execution, or production Trino support.
- Added `scripts/query-doctor-web-trino-beta-smoke`, a dev-only local web UI
  gate for Trino Beta demo/release handoff. The smoke starts the local web
  server, submits Trino Beta Recent, validates the beta result HTML, then
  submits One Query ID using a selected retained Query ID without printing it.
  It rejects Details/report/optimizer action links in Trino Beta results, keeps
  terminal output free of Query IDs, coordinator URLs, auth references, local
  paths, and raw payloads, and does not add SQL execution, metadata collection,
  trusted reports, optimizer behavior, or production Trino support.
- Added an installed-package Trino Beta web E2E smoke with a local fake
  coordinator. The installed user-paths release gate now verifies that the
  packaged `query-doctor-web` can run Trino Beta Recent and One Query ID form
  flows through bounded pruned coordinator-shaped responses while keeping
  output raw-free and without contacting external services or executing SQL.
- Aligned the safety contract with the current Trino Beta product boundary:
  retained-list Recent plus One Query ID are the only local web beta
  exceptions, both remain raw-free compact diagnosis, and Running,
  query-history crawling, metadata collection, Details/trusted reports,
  optimizer behavior, generated Trino SQL, SQL execution, and production
  support stay blocked. The Trino product-surface audit now includes the
  English and Russian safety contracts and rejects the stale
  Query-ID-only wording that denied the Trino Beta Recent lane.
- Added a dev-only `audit_trino_web_beta_live_smoke.py` gate for Trino Beta web
  demo/release handoff after local-config readiness passes. The smoke uses the
  local web backend to perform one bounded retained query-list read plus bounded
  selected QueryInfo diagnosis, emits only raw-free counts and issue IDs, and
  does not output raw payloads, coordinator URLs, Query IDs, auth references,
  or local paths. It does not add SQL execution, metadata collection,
  Details/trusted reports, optimizer behavior, or production Trino support.
- Trino Beta retained-list Recent now accepts the real pruned coordinator
  query-list response shape while scrubbing raw SQL, session, self-link, and
  other unused response fields before building normalized rows. The lane still
  performs one bounded read, emits only raw-free compact diagnosis, and does
  not add Trino Running scans, query-history crawling, Details/trusted reports,
  optimizer behavior, generated SQL, SQL execution, or production support.
- Trino Beta web Recent and One Query ID collectors can now use local
  Kerberos/SPNEGO settings for bounded coordinator GETs as an alternative to an
  operator-managed `Authorization` header file. The settings are local
  references only, are mutually exclusive with `trino_auth_header_file`, and
  do not add SQL submission, metadata collection, reports, optimizer behavior,
  or production Trino support.
- Added a dev-only `audit_trino_web_beta_readiness.py` gate for Trino Beta web
  demo/release handoff. The audit validates only local config and Trino source
  contract files, emits raw-free counts and issue IDs, and performs no
  coordinator network read or SQL execution.
- Local config validation now accepts `trino_query_list_source_contract` at the
  global and cluster levels, matching the Trino Beta Recent documentation and
  web startup path.
- Local web Diagnose now exposes a bounded Trino Beta Recent lane when the
  selected local source has `trino_beta_enabled`, `trino_coordinator_url`,
  `trino_query_info_source_contract`, and `trino_query_list_source_contract`.
  The lane reads one retained pruned coordinator query list, diagnoses only a
  bounded selected set through pruned QueryInfo, renders raw-free compact
  results, and keeps Trino Running scans, query-history crawling, metadata,
  Details/trusted reports, optimizer behavior, generated SQL, SQL execution,
  and production support claims blocked.
- Local web Diagnose now exposes a Trino Beta engine lane for one explicit
  Query ID when local `trino_beta_enabled`,
  `trino_coordinator_url`, and `trino_query_info_source_contract` settings are
  configured. The lane performs one bounded pruned coordinator QueryInfo read,
  renders deterministic raw-free compact diagnosis, rejects Trino Running scans
  server-side, and still blocks Trino metadata collection,
  Details/trusted reports, optimizer behavior, SQL execution, query-history
  crawling, and production support claims.
- The Diagnose page now marks Trino Beta as selectable only when the selected
  local source has the required beta config, keeps unconfigured sessions on
  Impala, and the Help page documents the Trino Beta Recent and One Query ID boundary
  without exposing local coordinator, source-contract, or auth-reference
  values.
- Trino Beta One Query ID async jobs now use beta-specific progress wording
  for the bounded QueryInfo read, raw-free boundary validation, compact
  diagnosis, and beta result preparation instead of Impala profile/report
  stages.
- Trino Beta web startup now validates the configured local QueryInfo source
  contract, coordinator URL shape, and optional auth-header reference before
  presenting the beta lane as configured, while keeping rejected URL, path, and
  secret details out of startup errors.
- Trino Beta One Query ID jobs now honor web cancellation checks between
  bounded stages, including before the coordinator QueryInfo read starts, while
  keeping the same one-query beta boundary.
- The Diagnose Known Query ID form now switches to Trino-specific label,
  placeholder, button, and beta-boundary help text when Trino Beta is selected,
  avoiding Impala profile, metadata, report, optimizer, or SQL-execution
  promises in that lane.
- Trino Beta Query ID results now include an explicit beta-boundary note stating
  that the result is the complete beta product output and does not create
  Details pages, trusted reports, optimizer recommendations, generated SQL,
  query-history crawling, metadata collection, or SQL execution.
- The Diagnose page now keeps the Known Query ID label, placeholder, help text,
  and submit button synchronized with the selected engine, so switching between
  Impala and Trino Beta no longer leaves stale Impala or Trino copy in the
  visible form.
- The Diagnose page now initializes engine-specific Known Query ID copy before
  source-cluster synchronization runs, preserving selected-source submits and
  async job polling after the Trino Beta engine switcher loads.
- Source cluster options now mark locally configured Trino Beta sources as
  `Trino Beta Recent + One Query ID` or `Trino Beta One Query ID` without exposing coordinator URLs, source-contract
  paths, or auth-reference paths in the browser.
- Trino Beta Query ID submits now fail closed before analysis or async job
  creation when the selected source lacks the required local beta configuration,
  returning a browser-safe product-level error instead of starting a doomed
  Trino job.
- README, Help, and configuration docs now describe the Trino Beta source
  selector marker and fail-closed submit guard while preserving the Recent and
  One Query ID beta-only support claim.
- Trino Beta Query ID job panels now use Trino-specific running, complete,
  stopped, and failed titles instead of generic analysis titles, keeping async
  progress copy aligned with the beta lane.
- Trino Beta async polling now preserves those beta-specific terminal titles and
  restores the `Run Trino Beta` submit label after terminal job states.
- New Trino Beta Query ID jobs now start with an empty result slot instead of
  inheriting the prior Impala Known Query ID result table while the beta job is
  still running.
- The Diagnose Engine help popover now uses the complete Trino Beta boundary,
  including blocked Running scans, query-history crawling, metadata collection,
  Details/trusted reports, optimizer behavior, generated SQL, and SQL execution.
- The Diagnose engine-switching script now keeps the dynamic Trino Beta Query
  ID help text aligned with the same complete boundary when users switch
  engines without reloading the page.
- The server-rendered Trino Beta Query ID field help now also uses the complete
  beta boundary, so first-load HTML and engine-switching copy stay consistent.
- The Trino Beta Query ID result header and boundary note now repeat the full
  beta boundary, including blocked Running scans, query-history
  crawling, metadata collection, Details/trusted reports, optimizer behavior,
  generated SQL, and SQL execution.
- Trino Beta async job pages now have regression coverage that the running job
  route preserves the beta Query ID form and never falls back to Impala
  profile/report wording while the job is in progress.
- The Diagnose UI now disables unsupported Trino workflows on first render and
  during engine/workflow switching, preventing stale form state from showing
  Trino Beta as a Running scan path or a Recent path without the query-list
  source contract.
- Source cluster options now carry raw-free Trino Beta readiness markers, and
  the Diagnose UI disables or clears Trino Beta when users switch to a source
  without the local Recent/One Query ID beta config before submit.
- README, Help, and configuration docs now describe that same Trino Beta
  source-switch guard while preserving the forged/stale submit fail-closed
  server boundary.
- The Trino product-surface boundary audit now reports how many allowlisted
  Trino preview imports it inspected in product modules, making the Recent/One
  Query ID beta and compact-preview import boundary visible in retained audit
  summaries.
- The Trino product-surface boundary audit now also checks curated public
  README, support-matrix, and web UI claim surfaces for the beta-only Recent and
  One Query ID boundary and forbidden production-support wording.
- Added a Trino Beta UI readiness checklist that defines the showable local
  Recent and One Query ID beta surfaces, required UI behavior, blocked claims, release
  gates, and screenshot-refresh boundary without broadening Trino support.
- Trino Beta UI readiness now includes a route-level E2E smoke covering
  configured `/analyze` submit, async job status polling, and the final raw-free
  beta result page without contacting a real coordinator.
- Trino private-preview release docs now link the local Trino Beta UI readiness
  checklist so release-facing wording, support-matrix boundaries, and UI beta
  gates stay aligned.
- Trino Beta Query ID errors now validate before async job creation and clear
  rejected Trino Query ID input from browser error renders while successful beta
  results still show the selected valid Query ID.
- The Trino product-surface boundary audit now treats `docs/configuration.md`
  as a curated public claim surface so local Trino Beta config wording stays
  pinned to the Recent and One Query ID beta boundary.
- The Russian configuration reference now documents the same local Trino Beta
  Recent and One Query ID config boundary and is included in the Trino product-surface
  claim audit without broadening Trino support.
- The Trino product-surface claim audit now rejects Russian forbidden
  production-support wording for the beta-only Recent and One Query ID boundary.
- Configuration documentation tests now pin the English and Russian Trino Beta
  Recent and One Query ID config references to the same required local keys, raw-free UI
  limits, and blocked support surfaces.
- Public release readiness docs now describe the local Trino Beta Recent and
  One Query ID lanes explicitly while continuing to block Running, query-history,
  metadata, Details/report, optimizer, generated SQL, SQL execution, and
  production Trino support claims.
- The Trino product-surface claim audit now also checks English and Russian
  public-release readiness docs for the beta-only Recent and One Query ID boundary and
  forbidden support overclaims.
- Release checklists now pin the Trino Beta Recent and One Query ID boundary and are
  covered by the Trino product-surface claim audit, keeping release gates
  aligned with the beta-only support claim.
- Documentation indexes now include the local Trino Beta Recent and One Query ID boundary
  in the product-surface claim audit, including the Russian docs index.
- The engine preview index is now covered by the Trino product-surface claim
  audit, so second-engine boundary wording stays pinned to the beta-only lane.
- The changelog itself is now covered by the Trino product-surface claim audit
  so release-history wording cannot accidentally broaden the beta support
  boundary.
- The Russian support-gap matrix is now covered by the Trino product-surface
  claim audit, keeping localized engine-support wording aligned with the
  beta-only Recent and One Query ID boundary.
- The Trino product-surface audit now checks preview web registry metadata, so
  Trino preview routes must stay isolated compact pages and cannot be marked
  as product surfaces.
- The Trino product-surface audit now inspects the Trino Beta Query ID module
  for forbidden report, optimizer, CLI, subprocess, or product action imports,
  keeping the beta lane bounded to QueryInfo import and compact diagnosis.
- The Trino product-surface audit now also inspects the Trino Beta UI renderer
  for forbidden Details, report, optimizer, or product action rendering
  imports.
- The Trino product-surface audit now also rejects direct Details, report,
  optimizer, or selected-case action markers inside the Trino Beta UI renderer,
  keeping the beta output from becoming a trusted report or optimizer surface.
- Trino Beta result rendering, including async job result JSON, now redacts
  dynamic diagnosis text through the browser-display safety boundary while
  still showing the explicit Trino Query ID.
- Trino Beta local config validation now still runs when CM startup checks are
  disabled, so partial beta auth/header settings fail closed without echoing
  local paths or secrets.
- Read-only batch summary and staged corpus startup now skip CM credential
  checks again when CM validation is disabled, while still running Trino Beta
  local config validation for partial beta settings.
- The Trino support-gap matrix audit now checks the single product-beta
  capability shape, so retained support-gap evidence rejects accidental CLI,
  route, script, dev-only, or production-surface drift for One Query ID beta.
- Trino retained one-query handoff-suite manifests now reject smoke summary
  artifacts that overlap boundary, diagnosis, readiness-summary,
  handoff-summary, or product-surface summary artifacts, while still allowing
  one shared smoke summary across entries.
- Trino compact readiness now requires retained Kerberos/SPNEGO smoke summaries
  to keep explicit not-written redaction assertions and dev-only/no-product
  limitations before they can contribute to one-query handoff readiness.
- Trino compact readiness now validates retained Kerberos/SPNEGO smoke-check
  counter fields, requiring planned checks to stay not-run and executed checks
  to report non-negative row/field/response counters with a positive page count.
- Trino compact readiness now validates retained Kerberos/SPNEGO smoke-check
  `safe_error_category` values against the known safe smoke categories and
  Trino error types emitted by the smoke generator, while still rejecting
  successful or planned checks that carry failure categories.
- Trino compact readiness now rejects retained Kerberos/SPNEGO smoke summaries
  whose bounded statement count does not match the retained smoke checks, so an
  all-ok executed smoke artifact must stay internally consistent before it can
  contribute to one-query handoff readiness.
- Trino pruned QueryInfo fixture coverage now includes committed raw-free
  zero/absence and invalid-value compact fixtures in the shared engine-fact
  golden harness, preserving the boundary that missing or invalid coordinator
  fields degrade to `not_observed` or `unknown` instead of fake zeros.
- Trino retained one-query handoff-suite readiness can now require per-entry
  `trino_product_surface_boundary_audit_v1` summaries with
  `--require-product-surface-summary-json`. The compact readiness audit checks
  those retained summaries for the no-product-surface boundary and deterministic
  diagnostic-lane/count consistency, records path-free checked counts, and still
  keeps Trino below Details, trusted reports, optimizer behavior, Recent, live
  Query ID diagnosis, and support claims.
- Cloudera Manager Recent profile collection now stops submitting new profile
  jobs when repeated HTTP 5xx responses indicate Service Monitor or the CM
  profile source is unhealthy. The batch keeps already completed/in-flight case
  results, marks unsubmitted cases as skipped with a safe reason, emits a
  progress stop event, and records a raw-free summary warning instead of
  continuing to hammer the monitoring service.
- Recent batch case processing now streams analyzer work as soon as each
  parallel profile collection succeeds, reducing large-batch wall-clock time by
  overlapping analyzer execution with slow collection tails while preserving
  ordered summaries and safe failure categories.
- Large analyzer-only Recent batches now collect a small reserve candidate pool
  and retain up to the requested number of successfully analyzed cases in the
  final summary, reducing missing-analysis noise from isolated collection
  failures during calibration runs.
- Analyzer primary-bottleneck classification now runs after deterministic
  runtime diagnosis is built, so safe storage/runtime follow-up evidence can
  participate in the same selected-query primary routing pass.
- Impala profile-format detection now treats classic text profiles with plan
  fragment sections or resource/timing section markers as limited mapped
  profiles instead of failing primary bottleneck routing closed as an unknown
  dialect, while preserving per-section promotion guardrails.
- Recent batch profile collection now retries one bounded transient profile
  collection failure before marking a case failed, reducing missing-analysis
  gaps from short-lived Cloudera Manager or profile endpoint hiccups while
  preserving safe failure categories for persistent collection failures.
- Documented the direct Impala history-depth boundary: direct Recent and
  Running scans only see the coordinator query log exposed by daemon query-list
  endpoints, upstream Impala defaults that log to `--query_log_size=200`, and
  deeper direct history requires deliberate Impala daemon configuration, future
  bounded profile-log directory ingestion, or future bounded external history
  sources such as Loki/OpenSearch rather than arbitrary node/pod file reads or
  browser-submitted log searches.
- Web Recent scans now continue with bounded partial analysis when a broad CM
  discovery hits the raw query-summary cap, while clearly marking the result as
  partial and showing guidance to reduce Search depth or add user, pool, or
  query-type filters for complete coverage instead of looking like an ordinary
  zero-query result.
- Web Recent scan polling now surfaces a safe "Scan status unavailable" state
  when the local Query Doctor web server stops responding, so a lost local
  connection no longer looks like a scan that is still running.
- Safety CI now bounds pull-request public-release history scanning to the
  event base/head range, matching the local release gate and avoiding
  full-repository history scans on release PRs.
- Added `scripts/query-doctor-web-recent-to-known-smoke` to run the local web
  Recent-to-Known Query ID smoke chain in one command. It keeps the selected
  Query ID in a temporary local file, accepts metadata-backed Recent cases with
  collected table context, runs the Known Query ID smoke with metadata
  validation, accepts collected or partial Known Query ID metadata state, and
  redacts Query IDs and local paths from child output. Known Query smoke errors
  now preserve recognized safe subprocess hints while still hiding raw
  subprocess output.
- Added `scripts/query-doctor-known-query-input-from-summary` to select one
  successful metadata-backed Recent case from a retained `batch_summary.json`
  and write a local Known Query ID smoke input file without printing the Query
  ID, summary path, output path, raw SQL, profiles, or metadata.
- Added a generic local `scripts/query-doctor-web-recent-smoke` wrapper for
  configured Cloudera Manager or direct-Impala clusters. It starts the local web
  UI, submits a bounded Finished-query scan with optional duration and query
  type filters, checks Details, and prints only raw-free aggregate selection,
  collection, analysis, and metadata counters.
- Local direct-Impala CLI and web smoke wrappers now print safe aggregate
  candidate-selection diagnostics from retained Recent summaries, including
  normalized exclusion reason and SQL-verb counters, so "selected no cases"
  failures are actionable without exposing query text, profiles, metadata,
  endpoints, local paths, or subprocess output.
- Added a local `scripts/query-doctor-web-direct-impala-smoke` wrapper that
  starts the local web UI, submits a bounded direct-Impala Finished-query scan,
  verifies aggregate metadata collection through the web job path, and checks a
  Details page without printing raw profiles, SQL, metadata, endpoints, local
  paths, or subprocess output.
- Added a local `scripts/query-doctor-web-known-query-smoke` wrapper that
  starts the local web UI, submits one explicit Known Query ID from a local file
  or argument, waits for the web job, and checks Details plus the deterministic
  Python report route without printing the Query ID, raw profiles, SQL,
  metadata, endpoints, local paths, or subprocess output.
- Added `scripts/query-doctor-table-backed-smoke-query` to generate a local
  read-only table-backed `SELECT` from existing metadata context for Known
  Query ID metadata smokes, rejecting placeholder table references, optionally
  validating candidates with bounded read-only metadata statements, and avoiding
  table-name output on stdout/stderr.
- Owner-raw D3 deployment guidance now separates application-side pre-proxy
  readiness from the required live front-door validation gate, and the roadmap
  tracks the remaining real-proxy proof point without promoting general shared
  deployment support.
- The test matrix now has an explicit owner-raw D3 validation path covering the
  front-door smoke harness, policy simulator, viewer-identity normalization,
  owner-raw source policy, web route checks, UI/error wording, and public-safety
  documentation gates.
- Owner-raw D3 startup and denied-source-page diagnostics now explain the
  trusted front-door viewer-header requirement with safe reason-coded
  remediation, while continuing to fail closed without echoing viewer values,
  query users, header values, paths, SQL, or raw artifact names.
- Added a dev-only owner-raw D3 front-door smoke harness that exercises the
  normalized viewer-header contract with synthetic inputs, including stripped
  spoofed inbound headers, Kerberos human-principal mapping, missing/mismatched
  viewers, service-principal rejection, and duplicate-upstream-header
  fail-closed behavior without contacting an IdP, proxy, LDAP, Kerberos, or web
  server.
- Owner-raw D3 deployment guidance now includes public-safe front-door review
  snippets for common header handling, OIDC/SSO claim mapping, and
  SPNEGO/Kerberos principal mapping, plus an explicit warning that direct
  browser access to the Query Doctor web process must be blocked to prevent
  viewer-header spoofing.
- Russian documentation guidance now defines `docs/i18n/ru/` as a selective
  user/operator companion layer rather than a full mirror of every internal
  document, and the Russian security/configuration companions now pin the same
  owner-raw D3 front-door viewer-header contract as the English docs.
- Owner-raw D3 deployment guidance now standardizes on one application-level
  contract: a trusted auth front door authenticates the request, strips inbound
  viewer headers, and sets exactly one normalized viewer header for Query
  Doctor's owner check. OIDC/SSO, SAML, SPNEGO/Kerberos, LDAP, MFA, sessions,
  logout, tokens, groups, and RBAC remain front-door responsibilities rather
  than native Query Doctor auth modes.
- Web Finished-query scans now use the configured `recent_window_minutes`
  Search depth lookback for both Cloudera Manager and direct Impala sources
  instead of the previous one-hour Scan date/hour bucket. Large browser-selected
  windows show a load warning for Cloudera Manager, direct Impala UI endpoints,
  and optional Prometheus collection.
- Direct Impala query-list discovery now ignores daemon `query_locations`
  bookkeeping entries when building Recent candidates, and plain non-CTAS DDL
  statements are reported as `excluded: DDL statement` instead of the generic
  not-analyzable query-text reason.
- Direct Impala query-list discovery now emits safe warnings when the daemon
  completed-query list is already at its retained log size, or when a single
  configured profile host appears to front multiple daemon query-location
  hints. The warnings explain why broad Recent windows or load-balanced Known
  Query ID profile collection may miss the intended user query without exposing
  endpoints, Query IDs, SQL, users, profiles, or metadata.
- Direct Impala Recent and Running metadata refresh now also consumes
  collector-extracted source-table references from the selected profile, so
  metadata can run when daemon query-list statements are redacted or incomplete.
- Recent source locators now carry typed raw-free `line_span` payloads and
  allowlisted `line_span_source` provenance alongside their legacy display
  coordinates, so owner-raw source highlighting can prefer structured line
  ranges without exposing SQL text or weakening older summaries.
- Added a dev-only owner-raw policy simulator that evaluates the raw source
  allow/deny matrix over sanitized inputs and emits raw-free reason-code JSON,
  while the web owner-raw source route and non-local startup guard now share the
  same policy helper.
- Direct-Impala Running scans now treat daemon query-list entries from
  in-flight/running collections, or entries with `executing=true`, as running
  even when the daemon also reports a stale terminal `state`.

## 0.8.0 - 2026-06-14

- Release smoke scripts now fail early on non-empty explicit `--work-dir`
  values and offer guarded `--replace-work-dir` cleanup for `query-doctor-*`
  workspaces. Package/release/publish workflows also expose the README
  Quickstart installed-wheel smoke as a standalone CI step, and release
  operators can use `scripts/index_install_quickstart_smoke.py` to verify an
  exact PyPI/TestPyPI install in a clean venv before or after publication.
- D3 `viewer_identity_header` values now require an already normalized simple
  owner token, such as an Active Directory `sAMAccountName` or Kerberos primary.
  UPN/email-style values, distinguished names, group/role-like values, opaque
  subjects, whitespace/display names, comma-separated subjects, and service or
  host principals fail closed for owner-raw source access.
- Browser-visible subprocess failures now include allowlisted safe reason hints
  for direct-Impala Known Query ID profile collection failures, including
  missing retained daemon profiles, unavailable profile endpoints, endpoint
  request failures, and profile byte-limit failures, while still hiding raw
  child output.
- Web Recent and Running scans now honor the local `query_type` filter and can
  expose it as a config-enabled Advanced settings field via
  `web_advanced_filters=["query_type"]`; invalid browser values fail before a
  subprocess is started.
- Recent triage scoring now suppresses stats-hygiene-only attention for
  sub-30-second queries, reducing noise from very short queries with missing
  table or column stats while leaving longer stats candidates and runtime
  anomaly scoring unchanged.
- `viewer_identity_header` now fails closed when a request exposes duplicate
  viewer header values. Shared/D3 `owner_raw` source access still requires a
  trusted proxy or ingress to strip inbound copies and set exactly one
  authenticated viewer value.
- Added a README Quickstart installed-wheel smoke and a clean-wheel release
  rehearsal. Package/release/publish user-path smoke now verifies
  `query-doctor-self-test`, `query-doctor-analyze --profile-text
  ./exported-impala-profile.txt --out cases/cm-corpus`, and
  `query-doctor-web --corpus-dir cases/cm-corpus` from installed console
  scripts, with relative paths, a real local web server, and no external
  services or LLM calls.
- Added [owner-raw-d3-deployment.md](owner-raw-d3-deployment.md), an active
  shared/non-local `owner_raw` deployment contract covering trusted ingress
  header stripping, C1/C2 identity separation, kill-switch behavior, raw-free
  audit checks, and the absence of admin, group, role, or delegation bypasses.
- Owner-raw source access now has a global kill switch
  (`owner_raw_source_enabled=false` or `--disable-owner-raw-source`) and emits a
  request-id correlated safe audit line for every isolated raw source page
  attempt. The audit line is reason-coded and omits raw SQL, query ids,
  case ids, query users, paths, header values, and secrets.
- Browser-visible subprocess failures now include allowlisted safe reason hints
  for common Recent scan setup failures, including metadata Kerberos, metadata
  shell, CM credentials, missing direct-Impala hosts, cluster selection, and
  output-directory validation, while still hiding raw stdout/stderr.
- `query-doctor-web` can now derive per-request authenticated viewer identity
  from an explicitly configured `viewer_identity_header`, intended for D3-style
  deployments behind a trusted auth proxy or ingress that strips inbound copies
  of that header. Non-local `owner_raw` startup now accepts this configured
  auth front door, while missing, invalid, or service/host-principal viewer
  header values fail closed for raw source access.
- Added a local `scripts/query-doctor-direct-impala-smoke` wrapper for bounded
  direct-Impala Recent smoke runs. It auto-selects a single direct cluster from
  local config, prepares Kerberos metadata auth from local keytab state when
  available, and keeps local cluster ids and endpoints out of committed checks.
- `owner_raw` now has an isolated owner-only selected-case source surface for
  authorized Recent/Running cases, with no-store responses, source allowlisting,
  safe reason-code denials, local-path/secret masking, and no trusted-report,
  handoff, download, or LLM path.
- Safe Recent summaries now carry raw-free SQL line coordinates and Details
  renders a redacted source map for locator-backed findings without exposing
  source SQL text.
- Local `owner_raw` Recent scans can now collect across all configured/keytab
  owner users in one bounded scan, while still excluding service/host
  principals and keeping raw reveal governed by viewer identity rather than the
  collection credential.
- Installed-wheel user-path smoke now includes a real local web-server E2E for
  the one-profile Quickstart corpus path and manual-profile Known Query ID
  path. The smoke starts packaged `query-doctor-analyze`, starts packaged
  `query-doctor-web`, fetches Diagnose, Details, Known Query ID Details, static
  assets, and a generated Python report over HTTP, and checks that raw profile
  text and local paths stay out of browser-visible pages.
- Installed-wheel user-path smoke now also replays a sanitized Impala Web UI
  export corpus through packaged `query-doctor-analyze`,
  `query-doctor-corpus-smoke`, and a real packaged `query-doctor-web` server.
  The corpus covers embedded Query ID intake, strict Web UI filename-derived
  Query IDs, and accepted zero-operator profile exports.
- Recent Details and rewrite-opportunity rows now render raw-free source
  location chips from owner-gated source locators when line coordinates are
  already present, without exposing SQL text or changing the safe-mode summary.
- Direct Impala Recent discovery now ignores inconsistent daemon query-list
  `end_time` values that precede `start_time`, using the sane start timestamp
  for window filtering so fresh SELECT queries are not dropped when the daemon
  snapshot carries a stale completion timestamp.
- Web startup now refuses `source_visibility=owner_raw` on non-local binds
  unless authenticated viewer identity is configured. This blocks the unsafe
  shared local-first mode without silently downgrading owner raw visibility.
- Web settings now model viewer raw-subject identity separately from source
  owner/keytab collection choices. The current local-first mode still maps raw
  viewer subjects to collectable owners, while public demo mode remains
  unauthenticated and fail-closed for raw viewer subjects.
- Added `query-doctor-self-test`, an installed-package confidence check that
  exercises packaged console scripts, synthetic demo generation, one-profile
  analysis, Impala Web UI filename fallback, local web rendering,
  deterministic report generation, and corpus smoke using synthetic local data
  without external services or LLM calls.
- Package, release-gate, and publish workflows now run an installed-wheel
  user-path smoke matrix after wheel installation. The matrix starts from the
  installed console scripts, clears local source-path and CM secret
  assumptions, and exercises Quickstart/manual profile intake, corpus web
  rendering, deterministic report/pipeline/optimizer paths, demo generation,
  CM and Impala dry-run collectors, direct Impala discover/profile smokes
  against a local fake daemon, and bounded Trino/Spark compact fixture paths.
- `query-doctor-web --corpus-dir` now starts without Cloudera Manager settings
  when the corpus already contains complete `query-doctor-analyze
  --profile-text` manual-profile cases, and the Diagnose page renders those
  staged exported profiles automatically as a read-only local results table.
  When no explicit `--config` is provided, this Quickstart corpus path also
  tolerates stale default local config files that are not needed for the local
  read-only view.
- `query-doctor-analyze --profile-text` now accepts direct Impala Web UI text
  profile downloads whose filenames use the strict
  `profile_<query-id-high>_<query-id-low>` shape when the profile body lacks a
  readable Query ID header. Embedded profile IDs, explicit `--query-id` values,
  and filename-derived IDs must still agree before any local case is written.
- Metadata analysis now records raw-free statement status and issue counters
  for explicit Impala metadata collection, distinguishing bounded causes such
  as `too_large`, Kerberos host-FQDN failures, authorization, parse,
  object-resolution, connection, and timeout issues without exposing raw
  metadata output or subprocess text.
- Recent batch summaries now distinguish raw-free referenced-table counts from
  collectable metadata table counts, so redacted placeholder references explain
  why a case can be `metadata=not_requested` without indicating a metadata
  transport or authorization failure.
- Details metadata facts now show collectable metadata table counts alongside
  referenced and collected table counts, making the same distinction visible in
  selected Recent cases without exposing table names or raw metadata.
- Details metadata loading now has a separate bounded read limit for generated
  `impala_context.json` artifacts, so larger collected metadata contexts can
  still render safe table-stat facts and statement status counts instead of
  falling back to aggregate-only metadata.
- Metadata collection now backtick-quotes validated database and table
  identifiers in generated `SHOW CREATE TABLE`, `SHOW TABLE STATS`, and
  `SHOW COLUMN STATS` statements so reserved-word table names do not trip the
  Impala parser while the collector remains read-only and allowlisted.
- Metadata collection now accepts `metadata_kerberos_host_fqdn` /
  `--metadata-kerberos-host-fqdn`, passed through to
  `impala-shell --kerberos_host_fqdn`, so load-balanced Impala metadata
  endpoints can keep a reachable coordinator address while authenticating
  against the Kerberos host principal.
- Details now folds validated selected-case optimizer recommendations, manual
  optimizer guidance, or a safe link to a validated SQL draft into the same
  Recommended change area as deterministic analyzer action cards. Report and
  optimizer generation/validation pipelines remain separate, and SQL drafts are
  not copied into the summary action surface.
- Known Query ID analysis now generates and validates the deterministic Python
  report as part of the explicit analysis submit job. LLM narratives and Query
  Optimizer actions remain explicit selected-case actions, and Recent/Running
  scans still do not auto-run report or optimizer work.
- The root README and demo-mode docs now present `query-doctor-web
  --public-demo` as the primary synthetic demo startup, with the lower-level
  manual demo-pack commands kept as an advanced reuse/inspection path.

## 0.7.0 - 2026-06-12

### Web UI And Documentation

- Package and release-gate workflows now run an installed-wheel one-profile
  smoke that stages one exported Impala text profile through the installed CLI,
  opens the manual-profile web inbox path from a temporary launch directory, and
  renders Details plus a validated Python report without CM, Kerberos, network
  collectors, Prometheus, or LLM.
- Manual-profile intake is now documented in the safety contract as a text-only,
  no-browser-upload, fail-closed local boundary. Web startup errors now point
  one-profile users to `manual_profile_dir` when CM settings are absent, and the
  configuration guide includes a minimal manual-only web config example.
- `query-doctor-web` now accepts `--corpus-dir`, and local config accepts
  `corpus_dir`, so one-profile web inbox runs can keep generated Query ID cases
  in a user-chosen workspace instead of relying on the default
  `cases/cm-corpus` location. Relative config values resolve from the config
  file; relative CLI values resolve from the current directory. When unset, the
  web default now resolves under the launch directory instead of the installed
  package or source-tree root.
- Details-page optimizer SQL drafts are now explicitly gated by
  `source_visibility=owner_raw`. The default `source_visibility=safe` policy
  produces trusted recommendations/no-rewrite guidance instead and hides any
  existing validated SQL draft artifact from browser rendering.
- Added a full-pipeline leak-canary regression baseline for manual-profile
  intake, deterministic analysis, scoring, report prompt assembly, trusted
  Python report output, and browser Details/report rendering. The test uses
  salted synthetic markers, classifies generated case sinks fail-closed, and
  includes negative controls for unclassified sinks and disabled host redaction.
  It also covers the configured `manual_profile_dir` web inbox path through
  Known Query ID job status, route-level Details, and validated report
  rendering, with inbox precedence, symlink-containment, and deterministic
  suffix-order guards.
- Added a renderer-to-parser characterization guard for Recent batch scoring:
  structured analyzer facts are rendered through the production markdown
  renderer, then parsed and scored to pin the current markdown contract before
  the scoring path moves to typed analyzer facts.
- Recent batch scoring now prefers typed `analysis.json` analyzer facts for
  core scoring components and score reasons, with a recorded markdown fallback
  source and fallback reason in batch JSON and Markdown summaries. Query and
  stats optimization candidate scoring still read the existing rendered facts
  while their typed migrations are staged separately.
- Query and stats optimization candidate scoring now prefer typed analyzer
  evidence from `analysis.json` for impact, planning/opportunity, runtime
  counter-signal, and metadata-gap inputs when the full analyzer contract is
  present. Candidate JSON records safe evidence-source and fallback-reason
  labels when incomplete analyzer JSON falls back to the legacy rendered-facts
  path.
- The public README now starts new users with three explicit first paths:
  one exported Impala text profile, the synthetic public demo, or a minimal
  read-only Cloudera Manager Recent scan. Detailed Trino and Spark preview
  command catalogs moved out of the root README entry path behind the new
  [engines README](engines/README.md) and existing engine docs, while the root
  README keeps only the production-support boundary, explicit Trino offline and
  Spark no-public-support guard wording, and links to the engine support
  matrix.
- `query-doctor-analyze` now has an explicit one-profile entry path for local
  exported Apache Impala text profiles: `--profile-text` and `--out` stage a
  redacted collector-shaped case from the Query ID embedded in the profile,
  write `analysis_facts.md` plus `analysis.json`, and print the case directory
  without any network collection. `--query-id` remains available for profile
  exports without a readable Query ID header. Known Query ID analysis now reuses
  complete manual-profile staged cases in the web corpus instead of forcing
  recollection, so those staged cases can be opened from the local UI by
  entering the same Query ID. Browser profile upload remains out of scope for
  this trust-boundary slice.
- Manual-profile staging now verifies an embedded profile Query ID when the
  exported text profile includes one, covering both `Query ID:` and
  `Query (id=...)` forms. Mismatched profiles fail closed before writing or
  replacing a case, and staged metadata records whether the embedded Query ID
  was verified.
- Manual-profile inbox docs and recovery messages now explain the Query ID slug
  recipe for web use, add the inbox path to the in-app Help and Russian README,
  and give incomplete manual-profile cases a non-circular recovery message.
- Known Query ID analysis can now use a configured local `manual_profile_dir`
  as a web profile inbox: when a matching exported text profile file exists,
  the web workflow stages and analyzes it through the same bounded redacted
  manual-profile path without running CM or direct-Impala collection. Manual-only
  configurations fail closed when the matching file is absent instead of
  silently falling back to live collection.
- Roadmap and customer-readiness docs now make the near-term adoption gate
  explicit: five external or design-partner Impala diagnostic runs with useful
  feedback, a visible one-profile first-value path before full Recent setup,
  and a trust-architecture backlog for leak-canary coverage plus migrating
  load-bearing consumers away from rendered `analysis_facts.md`.
- Results `Scan context` now stays compact: coverage, important scan notes,
  table key, action outcome count, and top workload follow-up links remain
  visible, while the full workload digest, pool/owner breakdown, repeated group
  table, and rewrite funnel metrics no longer compete with the result rows.
  Demo links and README screenshot provenance now target `#scan-context`
  instead of the retired `#workload-digest` entry point.
- Workload triage now reads as an analyst flow. Results point to repeated
  patterns through compact workload follow-up links, while Workload Details
  starts with why the repeated pattern matters, where to inspect, what to try,
  and how to verify before representative queries, collapsed additional checks,
  coverage, and limitations. The retired full workload digest, action queue,
  pool/owner breakdown, and repeated-group dashboard renderers and CSS were
  removed after the Results page moved to the compact follow-up path.
- Details now leads the first recommendation card in analyst decision order:
  `Why this query matters`, `Where to inspect`, `What to try`, and
  `How to verify`. Additional supported actions are collapsed below the primary
  recommendation, and `Record rerun outcome` now explains comparable-rerun
  feedback and local workload-confidence use before showing the outcome
  buttons. Help documents the current Details and workload path instead of
  exposing compact future-engine surfaces in the main shortcut set.
- Recent scan and Results now keep the default path flatter: an empty
  `Minimum duration` includes long-query and repeated-short workload patterns,
  setting a duration narrows to longer-running queries, secondary result views
  stay in one visible toolbar instead of `More filters`, post-table context is
  visible as `Scan context` with `Coverage`, and Help collapses only large
  topics. README screenshots were refreshed from the synthetic demo pack for
  the same UI wording.
- Recorded the need for read-only Impala/Cloudera Manager validation access and
  added an initial repository simplification audit for docs, scripts, tests,
  changelog readability, and Russian-doc maintenance. Partner-specific outreach
  copy stays outside public documentation.
- Spark readiness docs now record durable compact-intake boundaries instead of
  one-run live checkpoint details; private endpoints, selectors, output paths,
  and validation notes stay in local exclude-only notes.
- Added an Impala-first customer-readiness priority note and a minimal
  Cloudera Manager config example so first-run setup can stay separate from
  advanced direct-Impala, Prometheus, metadata, and LLM routing options.
- `query-doctor-web --public-demo` now supports a one-command read-only public
  synthetic demo mode. It generates the synthetic pack itself, forces
  Python-only mode, ignores default local config discovery and owner-source
  environment hints, rejects explicitly loaded external source settings, and
  blocks every POST route so public browsers cannot start scans, reports,
  optimizer actions, uploads, cancellations, or feedback writes.
- The local web UI now includes a restrained project footer linking to GitHub,
  PyPI, documentation, and the public security model without exposing local
  state.
- The public security model now states public demo constraints:
  synthetic data only, read-only demo surfaces, no arbitrary uploads, no
  collector actions, no SQL execution, no raw artifact rendering, and
  compatibility feedback limited to redacted aggregate behavior.

## 0.6.0 - 2026-06-06

### Safety

- Trino retained package-level handoff suites can now require selected
  source contracts through `--require-source-contract`, diagnostic-lane source
  granularities through `--require-source-granularity`, and verification scopes
  through `--require-verification-scope`. The suite audit accepts only safe
  labels, rejects missing retained evidence without printing paths or
  user-supplied rejected values, and keeps retained package evidence below
  Trino product support.
- Spark support-boundary audit can now write an optional raw-free
  `spark_support_boundary_audit_v1` summary through `--summary-json`. The
  retained summary records only no-support boundary labels, check statuses,
  safe counts, and safe issue categories/messages without printing paths or
  broadening Spark beyond compact support surfaces.
- Spark retained package handoff-suite audits can now require explicit
  diagnostic-lane source-granularity labels with
  `--require-source-granularity`. The suite summary JSON records selected
  source-granularity requirements, and missing requested labels fail as
  path-free readiness gaps without reopening packages or claiming Spark
  support.
- Spark compact-readiness suite audits can now require explicit diagnostic-lane
  source-granularity labels with `--require-source-granularity` across direct
  compact inputs, fixture-export manifests, and retained one-application
  handoff-suite manifests. Summary JSON records the selected source-granularity
  requirements, and missing requested labels fail as path-free readiness gaps.
- Spark product-surface audit summaries now retain diagnostic-lane readiness,
  source-granularity, verification-scope, and fact-state counters. Retained
  one-application suite audits recompute those counters to catch
  no-product-surface summary drift without reopening Spark or broadening Spark
  beyond compact preview lanes.
- Spark compact-readiness summaries now retain diagnostic-lane
  source-granularity and verification-scope counters alongside readiness and
  fact-state counters, so retained one-application suite evidence can prove
  both the lane readiness and the comparable verification scope without
  reopening Spark.
- Spark compact-readiness suite audits can now require explicit diagnostic-lane
  verification-scope labels with `--require-verification-scope` across direct
  compact inputs, fixture-export manifests, and retained one-application
  handoff-suite manifests. Summary JSON records the selected scope
  requirements, and missing requested scopes fail as path-free readiness gaps.
- Spark package handoff summaries now retain diagnostic-lane checked,
  readiness, source-granularity, verification-scope, and fact-state counters.
  Retained handoff suite audits reject summaries that lose required
  `compact_attention_ready` evidence, accepted source-granularity counters, or
  accepted verification-scope counters, keeping package handoff evidence
  path-free and below Spark product support.
- Spark retained package handoff suite audits can now require explicit
  diagnostic-lane verification-scope labels with `--require-verification-scope`.
  Suite summary JSON records the selected scope requirements, and missing
  requested scopes fail as path-free readiness gaps without reopening Spark or
  broadening Spark beyond compact preview lanes.
- Spark one-application handoff-suite manifests can now retain optional
  `spark_product_surface_boundary_audit_v1` summary references through
  `--product-surface-summary-json`. The readiness gate treats them as safe
  retained artifacts and protects summary outputs from overwriting them, while
  the Spark product-surface audit recomputes each per-entry summary to catch
  no-product-surface evidence drift without printing paths or raw payloads.
- Impala primary-bottleneck coverage now has a committed synthetic
  representative gate fixture derived from the local synthetic demo pack. The
  fixture stores portable raw-free `analysis.json` inputs plus a sanitized
  aggregate showing full-batch unknown rate, medium-or-better rate, unknown
  reason categories, unknown resolution categories, and a short trend. The
  synthetic gate now protects the next coverage step by failing if the
  aggregate drifts or if unknown primary coverage is not below 20% while
  medium-or-better coverage stays below 70%; the current trend point closes the
  mapped-operator unknown gap through deterministic client-fetch-tail evidence,
  while the remaining short clean unknown cases are counted as a no-action
  boundary rather than promoted to a fake bottleneck.
- Spark one-application handoff can now write an optional raw-free
  `spark_product_surface_boundary_audit_v1` summary through
  `--product-surface-summary-out`. The dev-only wrapper runs the
  product-surface boundary audit over the compact and diagnosis artifacts it
  just wrote, keeps the output path-free, and still returns failed when strict
  compact readiness fails.
- Spark compact diagnosis now has a dev-only product-surface boundary audit
  over retained compact/diagnosis artifacts or retained one-application handoff
  manifests. The audit reuses compact readiness validation, checks stored
  diagnosis artifacts for no-support/lane drift, reruns static Spark support
  boundary and isolated preview route checks, and can write a path-free raw-free
  summary while keeping Spark below Recent, Details, trusted reports, optimizer
  behavior, live Query ID diagnosis, Spark job execution, and support claims.
- Trino compact diagnosis now emits a raw-free `diagnostic_lane` contract that
  records source granularity, evidence readiness, verification scope,
  supported-attention counts, fact-state counts, and required readiness/surface
  audit gates. The compact-readiness and product-surface audits recompute and
  validate that lane contract while keeping Trino below Recent, Details,
  trusted reports, optimizer behavior, live Query ID diagnosis, SQL execution,
  and support claims.
- Trino product-surface manifest audits now treat per-entry readiness-summary
  references as protected input artifacts, so a product-surface summary cannot
  overwrite retained readiness evidence while checking no-product-surface
  boundaries.
- Trino one-query handoff suite manifests can now retain per-entry
  `trino_product_surface_boundary_audit_v1` summaries. The product-surface
  manifest audit treats those summaries as protected input artifacts and
  rejects stored summary drift against deterministic boundary/diagnosis output
  before accepting no-product-surface retained evidence.
- Trino one-query handoff wrappers can now write optional
  `trino_one_query_handoff_summary_v1` raw-free machine evidence. Retained
  handoff-suite manifests can reference those summaries, and the compact
  readiness gate rejects drifted pipeline, artifact-boundary, or embedded
  readiness evidence while keeping the path-free no-product-support boundary.
- Trino compact diagnosis documentation and regression tests now explicitly pin
  task retry/failure and connector-metric attention lanes to supported
  one-query facts only, with absent connector metrics staying
  no-supported-attention and no root-cause claims.
- Trino product-surface audit summaries now include raw-free diagnostic-lane
  fact-state counters, and the one-query handoff wrapper tests require retained
  product-surface summaries to prove source granularity, readiness,
  verification scope, supported-attention, and fact-state coverage.
- Trino compact-readiness summaries now include a raw-free `diagnostic_lane`
  block with source granularity, evidence readiness, verification scope, and
  fact-state counters so retained one-query handoff evidence can prove lane
  readiness, comparable-rerun verification, and fact support in one structured
  machine contract.
- Trino one-query handoff suite audits now explicitly validate retained
  `trino_compact_readiness_summary_v1` `diagnostic_lane` blocks and reject
  missing or drifted source-granularity, readiness, verification-scope, or
  fact-state counters with dedicated safe issue categories before accepting
  retained readiness evidence.
- Trino evidence handoff summaries now include raw-free diagnostic-lane source,
  readiness, verification-scope, and fact-state counters, and retained handoff
  suite audits require those counters and reject source-granularity or
  fact-state drift between `diagnostic_lane` and the retained top-level summary
  counters before treating package-to-boundary evidence as ready. The retained
  handoff-summary manifest builder and suite audit also reject duplicate
  artifact references, including path aliases, so one summary cannot satisfy
  suite-width counts more than once.
- Action-outcome feedback now records explicit comparable-rerun verification
  for applied outcomes. New local records use `schema_version=2` with safe
  `verification_status` values, legacy records remain readable as
  `legacy_unverified`, and strict workload/action-outcome audits count only
  comparable-rerun verified records toward the sample threshold while still
  reporting aggregate unverified-feedback counters without paths, case IDs,
  workload fingerprints, SQL, or raw notes.
- Workload/action-outcome audits now retain explicit raw-free feedback
  requirements for tracked recommendation families. The component summary
  lists required, missing, below-threshold, and satisfied group counts plus the
  comparable-rerun threshold and schema version, while the aggregate Impala loop
  carries the same safe family-requirement counters without fingerprints, case
  IDs, SQL, local paths, or raw outcome records.
- Workload/action-outcome summary JSON now also carries an explicit raw-free
  `action_outcome_gate` block and aggregate gate counters. The gate records the
  comparable-rerun sample threshold, accepted verification status, whether local
  outcome feedback was supplied and raw-free, required family-group coverage,
  open missing or below-threshold groups, and pass/fail booleans without
  exposing workload fingerprints, case IDs, SQL, local paths, or raw notes.
- Workload/action-outcome gates now distinguish comparable-rerun sample volume
  from measured result evidence. Strict outcome calibration requires at least
  one measured result (`improved`, `no_change`, or `worsened`) for each required
  tracked recommendation family; all-`unsure` comparable reruns remain visible
  as raw-free aggregate counters but no longer pass the outcome gate.
- The synthetic demo pack now includes enough measured comparable-rerun action
  outcomes for the admission/runtime workload family to pass the default
  synthetic outcome gate. `scripts/audit_impala_synthetic_outcome_gate.py`
  regenerates the pack locally, audits the generated outcome records, and
  compares only the committed raw-free aggregate and short trend without storing
  workload fingerprints, case IDs, SQL, local paths, or raw outcome records.
- `scripts/audit_impala_synthetic_north_star_gate.py` now joins the synthetic
  primary coverage gate and synthetic measured-outcome gate into one raw-free
  aggregate artifact. The combined gate protects the current synthetic
  north-star baseline by failing when either primary coverage or measured
  outcome feedback regresses.
- `scripts/audit_impala_north_star_gate.py` now gates retained raw-free
  `impala_diagnostic_loop_audit_v1` summaries against the representative
  Impala north-star thresholds: unknown primary below 30%, medium-or-better
  primary coverage at or above 70%, and measured action-outcome feedback
  passing. Its optional summary JSON keeps only aggregate counters and safe
  trend fields for local or CI calibration.
- `scripts/build_impala_north_star_suite_manifest.py` now builds local
  redaction-reviewed retained-suite manifests for raw-free Impala loop
  summaries, and `scripts/audit_impala_north_star_gate.py --suite-manifest`
  can enforce minimum retained-batch breadth while keeping safe per-entry trend
  output without storing paths, artifact filenames, raw cases, SQL, profiles,
  workload fingerprints, or action-outcome records.
- Retained Impala north-star summaries now include safe
  `unknown_primary_category_counts` and `top_unknown_primary_categories`
  closure-track labels, so follow-up deterministic evidence work can target the
  largest unknown contributors without reopening raw cases or strengthening
  diagnosis wording.
- Impala diagnostic-loop retained summaries now carry sanitized
  `unknown_primary_resolution_counts`, and retained north-star summaries roll
  them up into resolution classes such as deterministic evidence gaps,
  collector gaps, and no-action/out-of-scope boundaries. This keeps unknown
  backlog prioritization focused on evidence gaps instead of treating clean or
  short-query boundaries as analyzer coverage work.
- The committed synthetic Impala north-star aggregate now includes the same
  unknown-primary resolution-class split, including evidence-gap, boundary,
  collector-gap, and unclassified counters. This lets CI protect the current
  synthetic baseline as "remaining unknowns are clean boundaries," not just as
  a passing unknown-rate percentage.
- Impala diagnostic coverage and aggregate loop audits can now use
  `--use-current-classifier-primary` for retained-summary calibration. The
  default remains persisted-summary coverage, while the explicit mode
  recomputes primary bottlenecks from current deterministic `analysis.json`
  facts and reports safe persisted-label drift counters without rewriting old
  batch artifacts or printing paths, case IDs, raw SQL, or raw profile text.
- Impala unknown-primary coverage audits now distinguish context-only memory
  estimate evidence from data-movement context. Memory estimate gaps remain
  below primary routing unless selected-query spill/scratch, metadata, or other
  validated evidence supports promotion; the audit only reports safe aggregate
  reason and follow-up counters.
- Impala diagnostic coverage audits now report strict-only unknown-primary
  reason breakdowns separately from aggregate unknown reasons. The printed and
  machine-readable counters stay raw-free, preserve safe composite reason keys,
  and keep out-of-scope short-query or unsupported-profile unknowns from
  diluting representative gate calibration.
- Impala diagnostic coverage summary JSON now carries an explicit raw-free
  `primary_gate` block with configured thresholds, full-batch primary rates,
  strict eligible-case rates, out-of-scope case counts, and pass/fail booleans.
  This lets retained calibration evidence prove the 70/30 primary-bottleneck
  gate without promoting clean, very short, unsupported-profile, or
  context-only cases into stronger primary labels.
- Impala primary-bottleneck classification now treats a selected-query
  aggregate-only `memory_estimate_errors` top finding as a medium-confidence
  SQL-shape follow-up. The route remains distinct from runtime memory pressure
  and data movement: memory estimates still do not promote to
  `runtime_memory` without selected-query spill/scratch evidence, and low-byte
  exchange context still does not promote to `runtime_data_movement`.
- Recent Details now gives `memory_estimate_context_only` cases a dedicated
  memory-estimate evidence follow-up card. The card keeps the primary
  bottleneck unknown, blocks SQL/runtime changes from estimate gaps alone, and
  asks analysts to verify table/column stats, spill/scratch counters, EXPLAIN
  estimates, and comparable rerun behavior before accepting one bounded change.
- Trino and Spark package-style evidence intake now share one
  `redaction_note_v1` safety gate. The shared validator requires versioned
  provenance, checked redaction status, required redaction classes, rejection
  reason counters, mapping-style sentinel tests, mapping-style boundary
  assertions, and `raw_companion_archive: none`; Spark package fixtures now use
  the same note shape as Trino while preserving Spark-specific required labels
  and the compact-only no-support boundary.
- The Trino/Spark parallel development restart gate is now documented in the
  agent quickstart, public-safe handoff baseline, and `redaction_note_v1`
  schema reference. Future package-style Trino/Spark branches must merge the
  aligned baseline, grep for stale note fields, and keep shared helper/schema
  changes in explicit synchronization slices instead of engine-specific feature
  branches.
- Trino/Spark architecture hardening now has a machine-checkable engine
  capability manifest for adapter flags, second-engine CLI roles, isolated
  compact web routes, and dev-only script taxonomy. Static Trino/Spark boundary
  audits and agent preflight rules use that capability graph, and Spark
  engine-specific normalized facts now have the same prefix guard discipline as
  Trino facts.
- Trino/Spark isolated compact browser routes now use a checked preview
  web-surface registry aligned with the engine capability manifest. Future
  preview web route additions must update that registry and focused tests
  instead of adding direct Trino/Spark route branches to the shared router.
- Trino preview source types now have a checked source-contract registry for
  accepted bounded source kinds, raw-storage policy, required bounds, network
  access classes, and promotion gates. The support-gap audit fails if a source
  type is missing from the registry or if any registry entry enables product
  surfaces, Details/trusted reports, Recent scans, optimizer behavior, SQL
  execution, raw storage, browser/report output, or metadata identifier output.
- Cross-engine normalized facts now have a checked promotion-policy registry
  for shared, distributed-SQL-family, source-boundary, and support-boundary fact
  IDs visible to Trino preview lanes. The support-gap audit fails if a
  Trino-visible promoted fact lacks policy coverage, mismatches its
  allowed-engine or scope contract, enables product surfaces, weakens the
  raw-free policy, or omits the explicit promotion gate.
- Trino and Spark dev-only handoff scripts now share handoff artifact helpers
  for path-overlap checks and ASCII/sorted JSON writes. Engine-specific
  redaction guards, readiness gates, and below-support wording stay in the
  owning scripts, while repeated output safety logic is covered by focused
  tests.
- Direct Impala Prometheus runtime metric limitations now explicitly frame
  Prometheus host-level metrics as shared runtime context when multiple Impala
  deployments can run on the same hosts. Correlation and scoring stay tied to
  selected-query profile evidence instead of treating Prometheus metrics as
  deployment-specific root-cause proof.
- Trino retained one-query handoff suites can now carry optional per-entry
  `trino_compact_readiness_summary_v1` references. The manifest builder keeps
  those references safe and relative, the compact-readiness suite gate can
  require them with `--require-readiness-summary-json`, and the audit
  cross-checks each retained summary against the deterministic one-query
  boundary/diagnosis/smoke readiness result without printing artifact paths,
  filenames, coordinator URLs, Query IDs, auth material, raw QueryInfo, SQL, or
  support claims.
- Trino one-query compact readiness now carries a safe `trino_version_family`
  fact from accepted coordinator QueryInfo source contracts into raw-free
  boundary payloads. The dev-only one-query handoff requires at least one
  non-unknown version family, and the retained handoff-suite audit can require
  minimum version-family breadth or specific safe version-family labels without
  printing coordinator URLs, Query IDs, auth material, raw QueryInfo, artifact
  paths, or raw version strings.
- Spark History Server application-only collection now treats an unavailable
  SQL execution-list endpoint as a safe `sql_execution_endpoint` compatibility
  limitation instead of a source-coverage warning, and can still summarize
  readable application-level jobs, stages, scheduler delay, spill, and task
  duration buckets as raw-free `same_application` context. SQL execution
  timing, failure category, and exact query linkage remain `unknown` unless an
  accepted SQL execution summary directly supports them. Exact SQL execution
  selectors remain strict: if that explicit endpoint is unavailable or not
  found, the collector still records safe source warning IDs and keeps query
  linkage at `same_application`.
- Spark compact evidence-package validation now requires the
  `application_only_same_application` promotion sample to be warning-free
  History Server compact evidence with `same_application` provenance,
  supported application-level job/stage/task and task-duration context, and no
  claimed SQL execution timing or failure facts. This keeps the package
  promotion gate aligned with the application-only preview lane without
  creating a Spark support claim.
- Spark one-application-suite-to-package bridging now rejects SQL-specific
  sample-case labels unless the retained compact History Server payload has
  accepted `exact_query` SQL execution evidence. This prevents
  `same_application` application-level handoffs from being relabeled as exact
  SQL, long-elapsed, failure-category, or adaptive-execution samples while
  preserving application-level compact evidence below Spark support.
- Spark compact diagnosis now emits a raw-free
  `spark_compact_diagnostic_lane_v1` contract with source granularity,
  evidence readiness, verification scope, fact-state counts, and required
  readiness/surface gates. The compact-readiness audit recomputes and validates
  that lane contract so retained handoffs fail closed on missing or drifted lane
  evidence without wiring Spark into Details, reports, optimizer, Recent, or
  support claims.
- Spark compact evidence-package validation now validates the same
  `spark_compact_diagnostic_lane_v1` contract for every accepted sample and
  includes safe diagnostic-lane readiness/source-granularity counters in package
  summaries and readiness JSON. The package promotion gate now requires at
  least one `compact_attention_ready` lane while staying preview-only and below
  Spark support.
- The isolated Spark compact-diagnosis page now renders the raw-free diagnostic
  lane as a first-class preview block: evidence readiness, source granularity,
  verification scope, supported-attention count, and source-warning count are
  visible without echoing submitted compact JSON, History Server selectors, lane
  schema internals, Details/trusted report wiring, optimizer behavior, Recent
  workflow behavior, or a Spark support claim.
- Spark History Server compact collection now treats unavailable per-stage
  `taskSummary` enrichment reads as safe `task_summary_endpoint` compatibility
  context instead of a source-coverage warning. Stage skew and task-duration
  signals remain `unknown` unless accepted stage summaries or task-summary
  quantiles provide enough raw-free evidence.
- Spark compact diagnosis now maps supported over-one-minute task duration
  bucket evidence to a `spark_task_duration_tail` attention area. The signal is
  value-gated, remains raw-free, and still does not claim a Spark root cause,
  Details/trusted-report wiring, optimizer behavior, Spark job execution, or
  product support.
- Trino coordinator QueryInfo source contracts now require a safe
  `trino_version_family` label alongside the source-contract and QueryInfo
  schema versions. The target check, pruned probe, pruned import, and dev-only
  one-query handoff summaries expose only that broad safe label, reject
  URL/path-like version values, and still emit no coordinator URLs, Query IDs,
  auth material, raw QueryInfo, or support claim.
- Spark compact diagnosis now uses supported application lifecycle as a safe
  fallback when SQL execution lifecycle is unavailable in application-only
  History Server evidence. The fallback improves Spark 2.4-style compact
  diagnosis context while keeping SQL failure, failure-category, root-cause,
  Details/trusted-report, optimizer, Spark job execution, and support claims
  unclaimed unless SQL/failure facts directly support them.
- Spark History Server compact collection now skips per-stage `taskSummary`
  reads when the stage summary already includes task runtime quantiles, and
  skips task-summary reads for zero-task stages. This keeps optional task
  enrichment bounded and reduces false source-coverage warnings without
  collecting task lists, raw stage identifiers, task details, URLs, logs, SQL,
  or changing the Spark no-support boundary.
- Trino one-query live handoff can now read the explicit Query ID from a local
  `--query-id-file` instead of requiring `--query-id` in shell history or
  process arguments. The dev-only wrapper requires exactly one of the two
  sources, validates that the file contains one supported Query ID, rejects
  output overlap with the file, and still never prints the Query ID or file
  path. Real-cluster handoff notes now also call out that finished QueryInfo can
  be evicted quickly, so operators should select a current or very recent Query
  ID for the one bounded read; both the standard coordinator fetch path and the
  Kerberos fetch path now map HTTP 404/410 responses to the same redacted
  stale-QueryInfo operator hint instead of falling through to a raw JSON parse
  failure or a generic network-read failure. The same one-query paths also map
  HTTP 401/403 to a redacted auth-rejected operator hint so ticket or
  operator-managed auth-reference refreshes are actionable without exposing auth
  material.
- Impala stats guidance now keeps generic column-stats gaps below the
  actionable `medium` tier unless the metadata/analyzer evidence ties the gap
  to join or filter columns. Generic column-only evidence still appears as a
  caveated low-confidence signal with comparable-rerun verification, but it no
  longer produces a stronger stats recommendation without deterministic
  join/filter relevance.
- Impala coverage audits now treat metadata context as not applicable when
  analyzer facts explicitly show no referenced tables. Referenced-table cases
  still keep the strict `metadata_context_not_collected` follow-up when bounded
  metadata is unavailable, failed, or not collected.
- Impala profile-doc counter registry context now records raw-free match state
  for Query Doctor's allowlisted counter families and exposes only safe
  allowlisted `missing_counter_names` in analyzer summaries and coverage
  audits. The retained context still omits unrelated profile-doc counters,
  descriptions, paths, hosts, SQL, and raw profile text.
- Impala coverage audits now separate missing profile-doc labels for all
  allowlisted counter families from missing labels that are actually observed
  in selected analyzer facts. Legacy profile-doc summaries without safe missing
  names still fail closed, while current raw-free summaries avoid P1 gaps for
  unobserved counter families.
- Impala coverage audits now keep clean and very-short unknown-primary cases
  in the raw-free reason counters and strict out-of-scope totals without
  counting them as `unknown_primary_bottleneck` follow-up gaps. Non-clean
  strict-applicable unknown primaries still remain P1 diagnostic coverage gaps.
- Direct Impala Recent discovery now keeps running query summaries when
  `--include-running` is set instead of dropping them during client-side window
  filtering. Completed daemon summaries remain bounded by the selected scan
  window, while running summaries continue through the existing explicit
  candidate-selection and profile-collection gates.
- Direct Impala `--only-running` discovery now applies the same running-query
  predicate before candidate selection, so completed daemon-query-list entries
  no longer inflate `summaries_inspected` or running-only exclusion counters.
  This keeps running probes aligned with the existing safe candidate gates and
  still executes no SQL.
- Workload action-outcome readiness now checks local rerun feedback against the
  recommendation family shown by each workload Action Queue entry and Details
  action hint. A workload with enough feedback for one family no longer passes
  strict calibration for a different stats, query-shape, or runtime action
  surface; retained audit counters remain raw-free and path-free.
- Strict workload action-outcome readiness now requires supplied comparable
  rerun feedback only for action surfaces with tracked recommendation IDs.
  Low-value or no-change repeated-group guidance without a tracked stats,
  query-shape, or runtime action still reports missing feedback buckets, but it
  no longer fails strict outcome calibration just because no change was
  proposed.
- Workload action-outcome audits now report raw-free recommendation-family
  requirement counters for tracked action surfaces. Strict retained summaries
  can show which safe family needs comparable rerun feedback, such as required
  and missing query-shape review outcomes, without printing workload
  fingerprints, case IDs, SQL, local paths, or raw outcome notes.
- Spark evidence handoff auditing now has an explicit `--partial-ok` dry-run
  mode for sanitized packages that intentionally have incomplete sample/case
  coverage. The mode validates the package with the same partial-evidence
  contract as the standalone package validator, writes a rejected raw-free
  blocker summary when requested, and still does not run fixture export, print
  package paths, raw values, request selectors, or create a Spark support
  claim.
- Spark compact readiness suites can now require raw-free Spark version-family
  breadth with `--require-min-spark-version-families` and repeated
  `--require-spark-version-family` flags. The audit counts only safe
  `spark_*` labels from accepted compact provenance, writes those aggregate
  counters into `spark_compact_readiness_summary_v1`, and keeps retained
  one-application summaries backward compatible without exposing raw Spark
  version strings, request selectors, paths, or a Spark support claim.
- Spark support-boundary auditing now requires the engine support matrix to
  describe retained `spark_one_application_handoff_summary_v1` artifacts as
  optional raw-free one-application evidence below production support. The
  guard keeps retained summary suites path-free, offline, and explicitly below
  Recent, Details/trusted reports, optimizer behavior, raw event-log access,
  Spark job execution, or a Spark support claim.
- Spark one-application handoff now preserves the collector's default safe
  History Server opener for ordinary CLI runs while still allowing explicit
  test opener injection. This fixes the dev-only wrapper path found during
  live Spark History Server validation without printing URLs, application
  selectors, artifact paths, raw SQL, plans, logs, or a Spark support claim.
- Trino one-query live handoff can now use an explicit dev-only
  Kerberos/SPNEGO curl fetch mode for the single bounded
  `GET /v1/query/{queryId}?pruned=true` read when an operator has already
  prepared a local ticket cache. The mode is mutually exclusive with
  `--auth-header-file`, keeps the same source-contract/readiness gates, rejects
  output overlap with Kerberos local inputs, and still prints no coordinator
  URLs, Query IDs, principals, ticket-cache paths, auth material, raw QueryInfo,
  local paths, filenames, or Trino support claim.
- Trino one-query live handoff can now write an optional raw-free
  `trino_compact_readiness_summary_v1` artifact through
  `--readiness-summary-out` in the same dev-only run that writes boundary and
  compact diagnosis JSON. The wrapper reuses the strict one-query/source-version
  readiness gate, rejects summary output overlap with every input and output
  artifact, and still prints no coordinator URLs, Query IDs, auth headers, raw
  QueryInfo, local paths, filenames, or Trino support claim.
- Trino evidence-package requirements can now be printed from the Python
  contract through the dev-only
  `scripts/trino_evidence_package_requirements.py` helper. It emits only safe
  package/source-type, fixture contract/version, redaction, rejection,
  sentinel-test, boundary-assertion, and size-limit labels, reads no Trino
  endpoint, is not an installed product CLI, and does not create a Trino
  support claim.
- Trino retained evidence-package handoff summaries can now be grouped with
  `scripts/build_trino_evidence_handoff_suite_manifest.py` and audited through
  `scripts/audit_trino_evidence_handoff.py --handoff-suite-manifest`. The
  suite path accepts only raw-free `trino_evidence_handoff_summary_v1`
  artifacts with an accepted package/boundary/readiness pipeline, writes an
  optional raw-free machine summary, rejects output/input overlap, and prints
  no paths, filenames, package payloads, SQL, URLs, Query IDs, or support
  claim.
- Spark evidence-package requirements can now be printed from the Python
  contract through the dev-only
  `scripts/spark_evidence_package_requirements.py` helper. It emits only safe
  case, source-contract, diagnostic signal-group, redaction, sentinel-test, and
  boundary-assertion labels, reads no Spark endpoint, is not an installed
  product CLI, and does not create a Spark support claim.
- Spark History Server compact intake now accepts an explicit
  `--application-attempt-id` selector in the installed collector, the isolated
  compact web page, and the dev-only one-application handoff wrapper. The
  selector is used only in bounded History Server request paths and is not
  written to compact output, diagnosis output, boundary facts, terminal output,
  or browser results.
- Spark one-application handoff can now write optional raw-free
  `spark_one_application_handoff_summary_v1` JSON through `--summary-json`.
  The summary records only collection counters, safe warning IDs, no-support
  boundary labels, artifact-write states, and the nested compact readiness
  summary, and it rejects output overlap with compact, diagnosis, or boundary
  artifacts.
- Spark one-application handoff now has a dev-only retained-artifact suite
  manifest builder and compact readiness gate. The new path groups raw-free
  compact, deterministic diagnosis, and engine fact boundary JSON triples from
  operator-reviewed History Server handoffs, verifies diagnosis/boundary
  consistency without reopening Spark, and prints only safe aggregate counters
  without paths, filenames, raw payload values, or a Spark support claim.
  Retained suite manifests can now also carry matching
  `spark_one_application_handoff_summary_v1` references; the readiness gate
  checks those summaries for raw-free/path-free status-ok evidence, matching
  strict requirements, source-coverage counters, warning IDs, and no-support
  boundary labels.
- Spark compact readiness audits can now write optional raw-free
  `spark_compact_readiness_summary_v1` JSON through `--summary-json`,
  including one-application handoff-suite mode. The summary records only
  schema/mode/status labels, selected strictness requirements, no-support
  boundary labels, aggregate counters, source-contract counts, and safe issue
  categories/messages, and rejects output paths that overlap input artifacts.
- Spark retained one-application History Server handoff suites can now feed a
  dev-only sanitized evidence-package builder through
  `scripts/build_spark_evidence_package_from_one_application_suite.py`. The
  bridge re-runs compact/diagnosis/boundary suite validation before building
  the package wrapper, requires explicit sample-case labels, rejects drift and
  output/input overlap, and does not print artifact paths, filenames, raw
  payload values, or Spark support claims.
- Recent workload presenters now derive bounded repeated workload groups from
  already sanitized row-level workload fingerprints when the batch summary has
  no materialized group payload. Derived groups intentionally have no local
  baseline or regression claim, but they unlock workload Details, action queue
  hints, and aggregate readiness auditing for repeated workloads that were
  previously hidden behind `workload_groups=0`. Workload readiness audits count
  only eligible safe row fingerprints, report incomplete fingerprints as an
  aggregate readiness blocker when no derived or materialized groups exist, and
  now retain safe `workload_fingerprint_incomplete_fields` buckets for new
  summaries while recomputing summary-only missing-field buckets for older
  summaries without field details. Cases are bucketed as `unspecified` only
  when even structured summary fields are unavailable. Raw fingerprints, case
  paths, and query text stay out of retained evidence.
- Recent batch summaries now retain each case's raw-free `workload_shape`
  beside the workload fingerprint. Retained summaries can therefore recompute
  and audit workload grouping inputs without reopening case directories,
  profiles, SQL, metadata, or other raw artifacts. Summary-only recomputation
  also preserves stored incomplete-field buckets so defaulted shape values
  cannot be mistaken for complete workload evidence.
- Recent workload fingerprints now derive missing join and set-operation
  counts from already structured profile operator names when explicit
  SQL-shape facts are unavailable. This keeps Impala Recent workload grouping
  raw-free while preventing complete profile-backed rows from being excluded
  only because `query_shape`/`sql_shape` fields were absent.
- Workload representative audits now have an explicit
  `--require-workload-groups` gate, and strict action-outcome calibration now
  fails when no repeated workload groups are present. This prevents a
  representative workload/action-outcome run from passing only because the
  grouping layer is absent while preserving the default non-strict behavior for
  ordinary summaries with no repeated workloads.
- Impala diagnostic-loop `--summary-json` now retains raw-free diagnostic
  coverage breakdowns for primary labels, source compatibility, optional source
  availability, source provenance status, evidence quality, and safe gap or
  calibration-limitation buckets. This makes representative batch evidence show
  why profile/runtime/metadata/direct-source coverage is or is not ready without
  exposing case paths, SQL, hostnames, raw metadata, or profile text.
- Direct Impala failed-discovery summaries now retain raw-free discovery
  counters in both the coverage-gap audit and diagnostic-loop summary JSON.
  Empty direct batches can distinguish failed discovery, inspected/selected
  count buckets, and allowlisted warning categories such as unreadable query
  lists without exposing endpoint names, SQL, case paths, or raw warning text.
- Trino preview now has a dry-run metadata source-contract gate through
  `query-doctor-trino-metadata-source-contract-check`. It validates one
  redaction-reviewed explicit relation/column allowlist contract, safe source
  reference labels, bounds, and redaction rules, then emits only path-free and
  identifier-free summaries. It does not contact Trino, read metadata, execute
  metadata SQL, collect metadata facts, add browser/report output, or create a
  Trino support claim.
- Trino preview now has a bounded local metadata summary import through
  `query-doctor-trino-metadata-summary-import`. It validates one compact
  sanitized aggregate summary after an accepted `metadata_allowlist` source
  contract, maps only relation/column coverage and stats-completeness counts
  into raw-free normalized facts, and does not contact Trino, execute metadata
  SQL, print object identifiers or metadata values, add compact diagnosis,
  browser/report output, or create a Trino support claim.
- Trino compact readiness now treats local metadata summary boundaries as
  aggregate metadata-coverage evidence. The strict
  `--require-one-query-boundary` gate rejects `trino_metadata_*` aggregate facts
  the same way it rejects `query_list_*` aggregates, so metadata summaries
  cannot count as one-query Trino diagnosis readiness.
- Trino compact diagnosis now also rejects local metadata summary boundaries in
  the shared CLI/web builder. Local `trino_metadata_*` coverage facts can still
  be exported as raw-free boundaries for contract auditing, but they cannot be
  rendered as compact diagnosis or leak object identifiers through rejection
  messages.
- Trino product-surface boundary auditing now gives local metadata summary
  boundaries a dedicated safe issue category. Aggregate `trino_metadata_*`
  coverage boundaries remain useful for contract/readiness auditing, but cannot
  be discussed as retained compact diagnosis artifacts for product-surface
  readiness.
- The dev-only Trino one-query handoff wrapper can now write a
  `trino_product_surface_boundary_audit_v1` summary from the retained
  boundary/diagnosis artifacts. This keeps real-cluster handoff evidence tied
  to the no-product-surface gate without adding live Query ID diagnosis,
  Details/trusted reports, optimizer behavior, or a support claim.
- Trino product-surface boundary auditing can now read a
  `trino_one_query_handoff_suite_v1` manifest directly. Retained one-query
  suites now use the same manifest for strict readiness and no-product-surface
  evidence while requiring compact diagnosis artifacts for every entry and
  keeping manifest paths, artifact paths, URLs, Query IDs, and support claims
  out of output.
- Trino handoff-suite manifests now accept only safe relative `*.json` artifact
  references with no absolute paths, parent traversal, current-directory
  segments, or backslashes. The builder and readiness audit also reject
  duplicate boundary, diagnosis, or readiness-summary artifact references,
  including path aliases, so a strict retained suite cannot satisfy minimum
  input counts by reusing one artifact; a shared smoke summary remains allowed.
- Added `scripts/audit_trino_support_gap_matrix.py`, a dev-only static gate that
  checks Trino fact-family coverage against the registered engine-fact namespace
  and engine adapter flags. The audit writes optional
  `trino_support_gap_matrix_audit_v1` raw-free machine evidence, keeps product
  surfaces blocked, and catches accidental promotion of Trino to Recent, live
  Query ID diagnosis, Details/trusted reports, metadata collection, or a support
  claim before broader support work.
- Trino product-surface boundary auditing now also statically scans
  product-surface web, report, and optimizer Python imports. The guard permits
  only the isolated compact-diagnosis route/page imports and fails path-free if
  Details, trusted reports, optimizer, Recent, or other product modules import
  Trino preview diagnosis code.
- Browser/log host redaction now uses a linear token scanner for free-text
  FQDNs, host-like single-label names, and bare `host:port` values instead of
  backtracking-prone regex substitutions. This preserves stable host aliases,
  keeps safe filenames and version-like tokens visible, and removes the CodeQL
  polynomial-regex risk from the redaction path.
- Spark compact diagnosis now includes safe task-duration bucket counts in
  `runtime_context` when accepted compact facts provide them. These are
  aggregate context values only; they do not create Spark root-cause claims,
  shared facts, Details/trusted-report output, optimizer behavior, or Spark
  product support.
- Spark compact evidence-package validation now emits a package-level readiness
  verdict. The safe summary, optional `--summary-json` output, and
  `--require-promotion-candidate` gate distinguish `partial_evidence`,
  `minimum_case_set_ready`, and `promotion_candidate` without echoing package
  paths, sample paths, or payload values. The package builder can now apply the
  same promotion-candidate gate before writing output, fixture-ready compact
  sample export requires the same gate before writing deterministic safe files
  plus a safe export manifest, and the Spark compact readiness audit can now
  consume that manifest while checking safe filenames, sample count, and source
  contract alignment before auditing the listed compact JSON files. A local
  Spark evidence handoff audit now composes package validation, temporary
  fixture export, manifest-driven readiness audit, and temporary-output cleanup
  into one path-free strict gate, with optional raw-free `--summary-json` output
  for machine-readable handoff readiness evidence. Package validation also
  rejects per-sample compact diagnosis boundary drift, keeping Spark's
  experimental/no-support and no-root-cause boundary intact.
- Spark evidence handoff now has a dev-only retained-suite gate. The new
  `scripts/build_spark_handoff_suite_manifest.py` helper builds local
  `spark_evidence_handoff_suite_v1` manifests over already raw-free handoff
  summary JSON artifacts, and `scripts/audit_spark_evidence_handoff.py
  --handoff-suite-manifest` audits the retained summaries with optional
  raw-free suite summary JSON. The suite path does not read Spark, re-open
  packages, print artifact paths, create product surfaces, or broaden Spark
  support beyond the compact-only adapter.
- Spark live evidence handoff now has a dev-only one-application wrapper.
  `scripts/spark_one_application_handoff.py` composes bounded History Server
  summary collection, raw-free compact diagnosis, optional raw-free boundary
  export, and Spark compact readiness auditing for one explicit application
  without installing a product CLI, crawling applications, reading raw event
  logs or environment dumps, printing selectors or artifact paths, or creating
  a Spark support claim.
- Spark evidence-package `promotion_candidate` readiness now also requires
  diagnostic signal-group breadth across data movement, failure, runtime
  context, and adaptive plan context. Complete case labels alone no longer
  make a package promotion-ready, and raw-free summaries expose missing signal
  groups only as safe blocker IDs without changing Spark's no-support boundary.
- Spark evidence-package summaries, readiness JSON, compact readiness suite
  output, and strict handoff summary JSON now include safe
  `source_warning_counts` by allowlisted Spark warning ID in addition to the
  aggregate warning count. This keeps warning-driven promotion blockers
  actionable without exposing History Server endpoints, file paths, raw logs,
  SQL text, or changing Spark's no-support boundary.
- Spark now has a registered bounded compact engine adapter for the Spark
  History Server compact-intake CLI, compact evidence-package
  validation/export, and raw-free compact diagnosis. The adapter keeps Impala
  as the default production triage engine and does not enable Spark Recent
  scans, Query ID product diagnosis, metadata collection, Details/trusted
  reports, optimizer behavior, raw event-log handling, raw SQL/plan display,
  environment/log dumps, or Spark job execution.
- Added `scripts/audit_spark_support_boundary.py`, a static Spark
  support-boundary audit that keeps the Spark adapter compact-only, Spark CLI
  roles aligned with compact/evidence-package surfaces, README/support-matrix
  wording below production support, and Details/report/optimizer/recent imports
  out of Spark before any Spark product exposure can broaden.
- Spark History Server compact executor summaries now mark the executor section
  as `supported` when a non-empty executor summary list is accepted. Individual
  executor-loss, memory, churn, and dynamic-allocation substates remain
  independently `supported`, `not_observed`, or `unknown`, so partial executor
  evidence still cannot backfill fake memory or loss signals.
- Agent-facing release instructions now pin the Spark compact boundary as
  experimental research only: no public Spark support, Recent scans,
  Details/trusted-report output, optimizer behavior, engine registration, raw
  event-log handling, raw SQL/plan display, environment/log dumps, or Spark job
  execution.
- Public release gate now checks branch history shape when `PUBLIC_RELEASE=1`
  is set. `scripts/check_release_history_shape.py` rejects missing public base
  refs, non-ancestor release heads, excessive commit counts, merge commits, and
  WIP/fixup/draft subjects before a public handoff can rely on local gate
  results.
- Public-release preflight history scanning now uses the same configured
  release range as the history-shape guard when local gate runs with
  `PUBLIC_RELEASE=1`. This keeps unrelated local refs from creating release
  warnings for a clean semantic release branch.
- README screenshot provenance is now machine-checkable. The new
  `docs/assets/readme-screenshot-provenance.json` manifest ties each public
  README screenshot to the synthetic demo pack, documented capture route,
  viewport dimensions, README usage, and actual PNG dimensions.
- Committed text fixtures under `tests/fixtures/` now have a dedicated
  public-release provenance guard. The pytest scan applies the same
  public-release marker detector to every fixture text file so future fixture
  families must stay synthetic, example-only, or explicitly redacted.
- Batch and Specific Query report export routes now have route-level traversal
  and symlink regression guards. Encoded path-shaped IDs do not select cases,
  symlinked report files outside the case directory remain hidden even with
  marker-like metadata, and fixed markdown download filenames stay pinned.
- Generated case staging directories now have explicit defense-in-depth
  coverage. `.replace-*`, `.query-refresh-*`, and
  `.cm-timeseries-refresh-*` directories are ignored at any tree depth,
  including non-default local corpus roots, and staged public-safety checks
  reject those paths even if they are force-added.
- Outbound HTTP clients now share a fail-closed no-redirect egress policy for
  configured diagnostic targets and strict public targets. The shared policy
  validates DNS-resolved destination address classes, blocks metadata,
  link-local, reserved, documentation, multicast, and unspecified targets, and
  keeps private/loopback use behind explicit configured-target policy. CM JSON
  and LLM response paths now also use parent-side byte caps instead of
  unbounded reads.
- Trino network-backed private-preview readers now use the same configured
  diagnostic HTTP egress helper instead of raw `urllib` openers. The HTTP event
  archive reader, HTTP query-detail archive reader, and one-query pruned
  coordinator QueryInfo reader inherit the shared target validation and
  no-redirect behavior while keeping output URL-free, Query-ID-free, and
  auth-header-free.
- Trino normalized fact IDs no longer reserve bare metric names during preview:
  Trino-only timing, resource, stage, task, spill, blocked, connector, and
  statement-execution facts now use `trino_*` IDs while `planning_time_ms`
  remains the explicit distributed-SQL-family fact. Contract tests reject new
  bare Trino engine-specific IDs; `query_list_*` bucket IDs remain behind
  snapshot review.
- Added `scripts/audit_trino_compact_readiness.py`, a local raw-free gate for
  accepted Trino `engine_fact_boundary_v1` JSON. It verifies compact diagnosis
  keeps `root_cause=not_claimed`, `trino_sql_execution=not_performed`,
  `live_recent_scan=not_wired`, `live_known_query_diagnosis=not_wired`, and no
  browser/report or optimizer wiring; checks boundary and diagnosis output for
  raw-like content; and supports suite mode across multiple boundary JSON
  inputs without printing paths or raw filenames. Its strict
  `--require-one-query-boundary` mode rejects aggregate
  `query_list_*` boundaries so query-list source-shape evidence cannot count
  as one-query Trino diagnosis readiness. Strict handoffs can also pass
  `--require-source-version <version>` to require an accepted boundary
  `identity.source_version` without printing the actual value.
- The Trino compact readiness audit can now also check a `--diagnosis-json`
  artifact written from the same boundary. The gate compares it with the
  deterministic compact diagnosis built from the boundary, rejects raw-like
  diagnosis text, and keeps local artifact paths and filenames out of output.
- The same audit can now check the dev-only Kerberos/SPNEGO
  `trino_smoke_summary.json` through `--smoke-summary`; strict release-facing
  dry runs can add `--require-executed-smoke` so a dry-run plan cannot count as
  an executed test-cluster smoke. Strict executed-smoke mode now also requires
  every smoke check to finish with the known `ok` status; planned, failed, or
  unknown statuses do not satisfy the evidence gate.
- Trino pruned coordinator QueryInfo probe/import commands can now use one
  local `--auth-header-file` containing an operator-managed `Authorization`
  header for the single bounded `GET /v1/query/{queryId}?pruned=true` request.
  Header paths and values remain outside summaries, boundary JSON, compact
  diagnosis, and error output; unsupported header names fail closed before the
  request. The pruned import command also rejects a `--diagnosis-out` path that
  would overwrite the auth-header file.
- The Trino pruned coordinator QueryInfo import command now supports
  `--boundary-out <raw-free-trino-boundary.json>`, writing the direct
  `engine_fact_boundary_v1` payload for local readiness audits without printing
  the output path. The command rejects boundary output paths that overlap the
  source contract, auth-header file, or compact diagnosis output.
- Trino pruned coordinator QueryInfo probe/import reads now disable HTTP
  redirect following for the single bounded
  `GET /v1/query/{queryId}?pruned=true` request, keeping the explicit
  coordinator target from expanding into a redirected egress path.
- Added `scripts/trino_one_query_live_handoff.py`, a dev-only one-query Trino
  handoff wrapper for real-cluster readiness work. It runs the existing pruned
  coordinator QueryInfo fact import, writes raw-free boundary and compact
  diagnosis JSON, and immediately applies the strict one-query/source-version/
  diagnosis readiness audit, with optional executed-smoke checking, without
  printing coordinator URLs, Query IDs, auth headers, raw QueryInfo, output
  paths, or filenames. It is not an installed product CLI, live Query ID
  workflow, Details/trusted-report surface, optimizer workflow, or support
  claim.
- The Trino compact readiness audit now accepts a
  `--handoff-suite-manifest <manifest.json>` for a set of dev-only one-query
  handoff results. The manifest references raw-free boundary JSON plus optional
  compact diagnosis and smoke-summary artifacts per entry; strict gates can
  require every entry to include a matching diagnosis artifact, an executed
  all-`ok` smoke summary, one-query granularity, known source version, supported
  attention, and supported parser coverage. Suite output stays path-free and
  filename-free and reports only aggregate counts plus safe issue categories.
  The same audit now supports `--require-min-inputs <n>` for representative
  handoff width and `--summary-json <summary.json>` for a raw-free machine
  summary whose source-version requirements are recorded only as counts/flags,
  not as operator-provided values.
- Added `scripts/build_trino_handoff_suite_manifest.py`, a dev-only local
  manifest builder for retained one-query Trino handoff artifacts. It writes
  `trino_one_query_handoff_suite_v1` manifests with relative artifact
  references after explicit redaction-review confirmation, supports one shared
  smoke summary or one per boundary, rejects output/input overlap, and prints
  only path-free aggregate counts.
- Added `scripts/audit_trino_evidence_handoff.py`, a dev-only package-to-boundary
  readiness audit for sanitized Trino evidence packages. It validates the
  package, converts accepted samples to raw-free boundary payloads in memory,
  runs the compact readiness suite, can write
  `trino_evidence_handoff_summary_v1`, prints no paths, raw payloads, SQL, or
  Trino identifiers, and makes no support claim.
- Added `scripts/audit_trino_product_surface_boundary.py`, a dev-only gate for
  retained Trino compact boundary/diagnosis artifacts before any product-surface
  promotion decision. It checks deterministic diagnosis artifacts, pins
  `live_known_query_diagnosis=not_wired`, verifies the allowed Trino web/CLI
  registry stays limited to compact preview surfaces, can write
  `trino_product_surface_boundary_audit_v1`, and keeps output path-free and
  support-claim-free.
- Report validators now include an adversarial EN/RU corpus for indirect
  unsupported stale-statistics root-cause wording, soft `COMPUTE STATS`
  recommendation wording, English stats-maintenance fix/explanation overclaims,
  row/cardinality estimate-direction wording, and integrated parity coverage
  for memory estimate direction, backend data skew, primary bottleneck, CM
  context-only metrics, and CM event context. The trust gate rejects those
  unsupported claims before a report can receive a trusted marker, while nearby
  neutral investigation and conditional maintenance wording remains allowed.
- Report language handling now uses the shared report-language registry for
  config, web settings, and report/pipeline CLI boundaries. Case-insensitive
  public keys such as `RU` normalize to `ru`, while unknown languages fail
  closed before report generation instead of drifting into fallback wording.
- Trusted report SQL-like text validation now rejects inline prose that embeds
  `SELECT`, `WITH`, DML/DDL, or metadata `SHOW` statements, closing the gap
  where raw SQL-like text could be caught in fenced snippets or list items but
  not in a surrounding sentence.
- Trusted report markers now bind the current report-marker schema version in
  addition to strict validation mode and report/facts hashes, so older weaker
  marker contracts stay untrusted in browser report surfaces after upgrades.
- Browser and log fallback redaction now includes an adversarial corpus for
  bare FQDNs, host-like single-label names, URL/field hosts, IPs, and uncommon
  secret assignment names such as credentials, passphrases, private keys, and
  auth values.
- Browser/log redaction now keeps curated SQL-style table identifiers, pool
  names, synthetic source-version labels, and safe local filenames from being
  misclassified as hosts, while still redacting explicit host fields, URL
  hosts, infrastructure-looking free-text domains, IPs, and host-like daemon
  names. Browser model-name redaction now also covers `gpt-4`, `gpt-4o`,
  `gpt_4_1`, and `gpt-lst` variants.
- Resource-trace facts now fall back to allowlisted aggregate host-counter
  parsing when no profile-format mapping is available, but explicit
  unsupported mappings remain unavailable. The facts stay context-only and do
  not promote a primary bottleneck without selected-query corroboration.
- Recent scan batch-job defensive fallback coverage now verifies unexpected
  exceptions produce a generic failed job message and keep raw subprocess text,
  paths, SQL, model names, and artifact names out of browser job status JSON.
- Web subprocess helpers now bound captured child stdout/stderr per stream on
  the parent side for real subprocess calls and defensive custom-runner
  returns, while browser failure messages continue to hide captured output.
- Trusted report and optimizer artifact tests now directly reject non-strict
  report validation modes and non-`strict_v2` optimizer validation modes, so
  local/manual validation bypass artifacts stay partial-untrusted in the web UI.
- Query Optimizer rewrite prompts now frame `INPUT SQL` as untrusted data and
  ignore instructions inside comments, literals, identifiers, aliases, or
  object names; regression coverage verifies unsafe prompt-injection drafts are
  downgraded to trusted no-rewrite recommendations, and recommendations-only
  prompts omit instruction-like unsafe digest values.
- Standalone stats diagnostics audits can now write optional raw-free
  `stats_diagnostics_audit_v1` summary JSON, recording stats tier, need-type,
  metadata status, evidence-detail, review-area, confirmation, and readiness-gap
  counters without paths, case IDs, query IDs, raw metadata, SQL, or free-form
  evidence text.
- Impala coverage-gap audits can now write optional raw-free
  `impala_coverage_audit_v1` summary JSON, recording diagnostic coverage,
  direct-source readiness, source compatibility, optional-source availability,
  and calibration counters without paths, case IDs, query IDs, raw source text,
  SQL, or follow-up prose.
- Recent action cards now prioritize the generic column-stats relevance caveat
  when a Medium stats recommendation is not tied to specific join/filter
  columns, so the visible guidance keeps the limitation before comparable-rerun
  verification.
- Recent stats scoring now treats structured join/filter column stats status
  counts as important-column evidence, matching the existing gap detector and
  preventing count-backed join/filter gaps from being downgraded to generic
  column-stats caveats. Evidence details normalize contradictory count-backed
  `covered` inputs to `partial` wording.
- Query-shape action cards now prioritize stats-vs-query-shape uncertainty and
  possible stats-refresh caveats before generic runtime counters, so Medium
  guidance keeps the evidence limitation visible to analysts.
- Optimizer funnel strict audits now require repeated no-recipe review guidance
  to keep an explicit no-trusted-SQL-draft and manual-review contract before it
  is counted as guidance-ready.
- Optimizer funnel audits can now write optional
  `optimizer_funnel_audit_v1` summaries through `--summary-json`, and the
  standalone CLI accepts the diagnostic-loop-compatible
  `--use-stored-optimizer-support` alias. The machine summary records only
  aggregate counters, safe issue categories, and masked workload labels, and
  reason-like fields now collapse raw-like SQL, paths, URLs, hostnames, IPs,
  emails, and secrets to `unsafe_reason` before stdout or JSON output.
- Direct Impala source-provenance facts now keep metadata read errors generic,
  and strict direct-source readiness fails representative summaries whose source
  provenance contains raw-like SQL, paths, hosts, URLs, emails, IPs, or secrets.
- Direct Impala Details source limitations now consume analyzer Source
  Provenance facts from `analysis_facts.md`, using allowlisted wording for
  explicit `none`, `unavailable`, and partial coverage states while ignoring
  arbitrary limitation text that could contain raw-like details.
- Trusted Python reports now consume analyzer Source Provenance facts through a
  Python-owned evidence bullet, summarizing only allowlisted source `kind` and
  `status` values while ignoring arbitrary limitation text from
  `analysis_facts.md`.
- Report recommendation candidates now prefer structured `Stats Metadata
  Quality` facts for stats-maintenance routing, using legacy table metadata
  wording only as a fallback and avoiding stats actions for non-physical
  `not_applicable` metadata.
- Report exchange/data-movement recommendations now prefer structured `Data
  Movement Evidence` facts, require `finding_supported: yes`, and use legacy
  `Findings` text only as a fallback.
- Report and Recent spill/scratch gates now prefer structured `Memory Pressure
  Evidence` facts. Explicit `finding_supported: no`, context-only, or
  unsupported memory-pressure facts block legacy spill wording from promoting a
  spill recommendation or scoring reason.
- Trusted report admission/pool follow-up checks now prefer structured
  `Runtime Admission Evidence` and correlated `Runtime Metrics Correlation`
  facts. Negative, context-only, or unsupported admission facts block aggregate
  pool context wording from inserting an admission-pool next check.
- Trusted report backend/per-host follow-up checks now use a narrower
  backend-follow-up gate. Context-only `Scan Skew Evidence` blocks legacy
  `Backend / Host Tail Evidence` wording from inserting platform handoff,
  per-host, or Backend priority checks unless supported data skew, execution
  skew/tail, write-path evidence, or a legacy supported finding exists.
- Workload action-outcome strict audits now reject supplied local outcome JSONL
  files that contain raw-like SQL, paths, hosts, URLs, emails, IPs, or secrets
  before counting them as representative feedback evidence.
- Browser SQL-snippet redaction now treats allowlisted metadata statements as
  metadata statements, hiding the full statement and object identifier without
  downgrading them to generic SQL redaction or leaving dotted-name tails.
- Added `scripts/audit_spark_compact_readiness.py`, a local raw-free gate for
  accepted Spark compact JSON. It verifies the Spark compact diagnosis keeps
  `root_cause=not_claimed`, `support_status=experimental_compact_intake`, and
  no Spark job execution; checks the engine fact boundary for raw-like content;
  and guards Spark fact naming so Spark-specific facts stay `spark_*` and out
  of shared scopes before any future support-surface expansion.
- Added a Spark test-cluster evidence checklist for the next promotion-readiness
  step. It defines representative operator-reviewed compact History
  Server/event-log evidence, sanitization, and readiness-audit requirements
  without requiring live query execution or creating a Spark support claim.
- Added `query-doctor-validate-spark-evidence-package` and the compatible local
  `scripts/validate_spark_evidence_package.py` wrapper for operator-reviewed
  Spark compact evidence package validation. The command accepts only safe
  manifest/redaction-note metadata and already compact Spark samples, reuses the
  raw-free Spark compact schema/fact/diagnosis checks, and prints only a
  path-free safe summary for readiness work.
- Added `query-doctor-build-spark-evidence-package` and a compatible local
  script wrapper to assemble sanitized Spark compact evidence packages from
  already compact sample JSON files. The builder requires explicit redaction
  review and sentinel-test confirmations, validates the package before writing,
  rejects output/sample path overlap, and prints only a path-free safe summary.
- The Spark compact readiness audit now supports suite mode for multiple
  accepted compact JSON inputs. It aggregates only safe counts and issue
  categories, keeps input paths and raw filenames out of output, and lets future
  Spark support gates validate several fail-closed scenarios in one run.
- Spark compact readiness suite mode now supports strict breadth requirements:
  minimum compact input count and required source-contract coverage. The same
  test coverage also guards against wiring Spark compact modules into Details,
  trusted report, Recent, or optimizer surfaces before a separate support
  promotion.
- Spark compact readiness now has a committed
  `spark_history_server_compact_source_warning.json` fixture, so suite breadth
  validation covers both the synthetic compact event-log contract and the
  History Server compact contract with safe source-warning aggregation.
- Spark History Server compact intake now treats shared egress target-policy
  violations as fail-closed collection errors instead of optional endpoint
  warnings. DNS failures use a generic safe error without echoing hostnames or
  resolved addresses.
- Spark compact diagnosis now maps supported aggregate executor memory
  used/capacity facts into a raw-free executor-memory-pressure attention area
  when utilization is high, without making root-cause or Spark support claims.
- Spark compact diagnosis now maps supported SQL elapsed time of at least two
  minutes into a raw-free long-elapsed-time attention area, treating duration as
  triage context rather than a root-cause or Spark support claim.
- Spark compact lifecycle facts now accept only allowlisted safe failure
  categories such as `resource_limit` and map them into raw-free compact
  diagnosis attention areas without reading raw exception text or making
  root-cause/support claims.
- Spark compact stage facts now include Spark-specific aggregate input/output
  row counts when every selected stage summary provides explicit safe row
  values. Partial or missing row aggregates stay `unknown`, and the values are
  not promoted into shared input/output facts.
- Spark compact diagnosis and the isolated Spark compact web page now show
  supported runtime context such as Spark version family, query linkage,
  application lifecycle/attempt state, adaptive execution enabled, dynamic
  allocation observed, input/output rows, bytes, stages, tasks, shuffle, spill,
  and elapsed time with formatted browser-safe labels. These values remain
  context only, not attention signals, root causes, shared facts,
  Details/trusted report output, or Spark support claims.
- Spark History Server compact collection now disables HTTP redirects by
  default, blocks metadata, link-local, reserved, documentation, multicast, and
  unspecified literal targets, and requires explicit CLI/web opt-in before
  loopback, RFC1918, carrier-grade NAT, or unique-local targets can be used.
  This is a Spark-local hardening step; the broader shared outbound policy
  across all HTTP clients remains tracked separately.
- Spark History Server compact provenance now records the per-endpoint
  `maxResponseBytes` cap for live History Server intake, validates it against a
  compact contract ceiling, and rejects over-wide response bounds before
  collection. The field is raw-free provenance only and does not add Spark
  product support, broad live collection, Details/trusted report output, or
  optimizer behavior.
- Spark History Server compact stage parsing now accepts additional safe
  aggregate task summary quantile shapes such as runtime or task-time lists
  from summary distributions. These feed only bounded skew context; raw stage,
  job, task, SQL, or plan details still remain outside compact output.
- Spark History Server compact collection can now inspect a bounded number of
  official per-stage `taskSummary` endpoints when selected stage summaries
  provide safe stage-attempt selectors. The collector still never calls
  `taskList`, stores no stage/application identifiers in compact output, and
  uses the summaries only as supplemental skew context without Spark support,
  Details/trusted report output, or optimizer behavior.
- Spark stage skew summaries now stay `unknown` rather than `not_observed`
  when only part of the selected stage set has runtime quantiles and no skew
  candidate was found. Positive skew evidence is still surfaced when present,
  but partial runtime coverage no longer claims absence of skew.
- The isolated Spark compact web page now exposes the same bounded
  `max_task_summaries` control as the CLI, so browser-triggered History Server
  compact collection can explicitly cap supplemental `taskSummary` probes while
  keeping request selectors and compact JSON out of browser output.
- The same isolated Spark compact web page now exposes `max_response_bytes`
  for History Server endpoints, preserving the default 2 MiB cap while letting
  local users explicitly lower or bound per-endpoint JSON reads under the
  compact contract ceiling.
- Spark compact CLI help now consistently states that the History Server
  collector and offline compact diagnosis do not claim Spark product support,
  and focused command plus installed-wheel contract tests guard the no-support
  wording plus the absence of SQL-execution flags on these experimental entry
  points.
- Validated report download and inline rendering now apply the shared browser
  redaction boundary to trusted report text, hiding model settings, raw
  artifact names, subprocess markers, SQL snippets, metadata statements, and
  internal field names in addition to local paths.
- Browser display redaction and the Recent Details audit now treat raw
  table/column stats and unsupported metadata statement labels as forbidden
  browser fragments, strengthening metadata/stats safety checks for
  representative batch validation.
- The Recent Details audit now counts stats action cards with or without
  structured raw-free metadata detail and supports
  `--fail-on-stats-detail-gaps` for strict representative-batch validation.
- The Recent Details audit now counts actionable recommendation cards whose
  verification text includes comparable rerun or comparable scan guidance and
  supports `--fail-on-comparable-rerun-gaps` for strict calibration batches.
- Recent Details query-shape and stats action cards now normalize older or
  incomplete verification text to include comparable rerun guidance, so Medium
  or High recommendations cannot render as EXPLAIN-only follow-ups.
- The Recent Details audit now fails browser-visible action cards that use
  positive root-cause/proven/confirmed wording, while allowing explicit
  negated guardrails such as "not a proven root-cause claim."
- Stats diagnostics now have a raw-free representative audit,
  `scripts/audit_stats_diagnostics.py`, with
  `--fail-on-stats-readiness-gaps` for strict calibration of score/tier
  strength, structured metadata detail, usable metadata status, safe review
  areas, and comparable rerun confirmation on Medium/High stats candidates.
- Stats optimization scoring now requires a complete evidence chain before a
  candidate can become Medium or High. Partial metadata plus estimate mismatch
  is no longer actionable unless there is supported missing or incomplete stats
  evidence.
- The Impala coverage-gap audit now supports
  `--fail-on-diagnostic-coverage-gaps`, which keeps representative calibration
  from passing when selected cases lack analyzer output, primary labels, or the
  aggregate unknown/medium-confidence primary-bottleneck coverage targets.
- The Impala coverage-gap audit now supports
  `--fail-on-direct-source-readiness-gaps` for direct Impala representative
  summaries. The gate blocks unknown source provenance, profile capability, and
  optional-source limitation states while accepting explicit unavailable,
  not-configured, and not-collected limitations as raw-free coverage.
- The workload diagnostics audit now accepts `--action-outcomes` plus
  `--fail-on-action-outcome-readiness-gaps`, allowing representative workload
  calibration to verify action queue/detail feedback summaries from local
  outcome records without printing local paths, fingerprints, or raw notes.
- The workload diagnostics readiness audit now requires verification guidance
  to include both a comparison anchor and rerun, comparable-load, or next-scan
  context before action hints and queue entries count as comparable-rerun ready.
- The optimizer funnel audit now uses
  `--fail-on-repeated-no-recipe-readiness-gaps` to require repeated no-recipe
  workloads to have a safe review track plus allowlisted review area, change
  direction, workload metric, and compare/rerun verification before they count
  as representative guidance-ready.
- Added `scripts/audit_impala_diagnostic_loop.py`, an aggregate raw-free strict
  gate for representative Recent summaries that runs Details, profile evidence,
  diagnostic coverage, workload, stats, and optimizer readiness checks and
  prints only component status plus issue categories.
- The Impala diagnostic-loop audit now includes passive optimizer artifact
  checks. Existing trusted SQL-draft, recommendations-only, and no-rewrite
  outputs are counted through the web trusted-artifact loader, while partial or
  untrusted optimizer output fails the representative gate without generating
  optimizer work or printing draft SQL, artifact filenames, case IDs, or local
  paths.
- The Impala diagnostic-loop audit now includes passive trusted-report artifact
  checks. Existing Python/LLM report artifacts are counted through the web
  trusted-artifact loader, current strict report validation is replayed against
  any trusted artifact, and partial, untrusted, or stale-invalid report output
  fails the representative gate without generating reports, running LLM work,
  or printing case paths, report filenames, raw SQL, or local artifact details.
- The direct/profile evidence-gate audit now treats selected cases without
  readable deterministic analyzer output as gate issues, so
  `--fail-on-issues` no longer passes a representative batch whose profile
  evidence was not actually checked.
- The direct/profile evidence-gate audit now compares profile-derived primary
  labels and confidence against the deterministic case-primary classifier,
  blocking representative batches where a profile primary overstates the
  analyzer-owned label or confidence.
- The direct/profile evidence-gate audit now prints only the batch-summary
  basename in normal output, keeping local representative-batch paths out of
  raw-free audit logs while preserving issue categories and aggregate counts.
- Direct Impala profile analysis now publishes raw-free section-mapping states
  and fails closed for unknown, unsupported, or partially mapped profile
  dialect sections. Experimental profile-v2 and unmapped classic JSON/Thrift
  sections stay unknown, allowlisted classic JSON counters remain limited
  context, and client-fetch or memory-pressure evidence no longer promotes
  report/Details spill or fetch findings unless the mapped section supports it.
- Public-tree safety checks now include a repository-wide guard against
  non-synthetic examples, real-looking local query IDs, and unsafe placeholder
  patterns. Public fixtures and tests use synthetic schemas, columns, Query
  IDs, Kerberos cache names, and host markers.
- Public release preparation now verifies an explicit source candidate and
  release tree before tagging the public source release line.
- CI and release-gate public-safety coverage now run public documentation
  audits and the full public-release preflight with git-history scanning
  instead of a current-tree shortcut.
- The aggregate Impala diagnostic-loop audit can now write a raw-free
  `--summary-json` machine summary with only component status, aggregate
  metrics, and safe issue counts. The retained JSON omits batch paths,
  action-outcome filenames, case identifiers, workload fingerprints, SQL, and
  profile content so representative readiness evidence can be saved and
  compared without expanding the public/raw boundary.
- The standalone workload diagnostics audit can now write a raw-free
  `workload_diagnostics_audit_v1` summary through `--summary-json`. The summary
  records workload grouping, repeated-row, baseline, action-queue,
  action-outcome, incomplete-fingerprint, and readiness counters without batch
  paths, action-outcome filenames, case identifiers, workload fingerprints,
  SQL, or raw outcome records, and refuses to overwrite input artifacts.
- Strict workload readiness now treats missing workload history as a blocker
  only when baseline or regression claims are present. Current-scan-only
  repeated workload groups with unknown regression and no baseline remain
  auditable for inspect, action hints, and comparable-rerun verification while
  still reporting the missing history counter without inventing baseline
  evidence.
- For direct Impala representative summaries, that aggregate
  `--summary-json` output now also carries safe direct-source readiness
  breakdowns from the coverage audit. The breakdown keys are normalized
  counters only, with unsafe-looking URL, path, host, SQL-like, and secret
  fragments collapsed before they can enter retained machine evidence.
- The same aggregate Impala loop summary JSON now retains safe workload
  breakdown counters for workload history, regression and baseline coverage,
  Details representatives, action-queue verification, and action-outcome
  feedback. This lets representative repeated-workload calibration compare
  aggregate states without storing workload fingerprints, case IDs, raw SQL, or
  local action-outcome paths.
- Aggregate Impala loop summary JSON now also retains safe stats and optimizer
  funnel breakdown counters. Stats readiness evidence includes tier, need type,
  metadata status, evidence-detail, confirmation, and review-area counters;
  optimizer evidence includes support/status, no-recipe, and repeated
  no-recipe readiness counters without SQL drafts or raw query text.
- The same retained summary JSON now carries safe profile-evidence breakdown
  counters, including profile dialect/policy, counter-registry state, evidence
  quality, profile-derived gate states, resource-trace state, and primary
  classifier drift. This keeps direct/profile evidence calibration comparable
  without case-level profile text, paths, or raw counter payloads.
- Aggregate Impala loop summary JSON now retains safe Details and trusted
  artifact breakdown counters. Details evidence covers severity, metadata,
  verdict/action, stats-detail, verification, optimizer, and report status
  counters; trusted-report and optimizer-artifact evidence covers state,
  trusted variant, revalidation, output-kind, and readability counters without
  raw artifact filenames or report/SQL text.

### Product

- Recent workload presenters now resolve stale row-fingerprint incomplete flags
  when the stored raw-free `workload_shape` already proves the named missing
  fields. This restores repeated-workload grouping and action-queue calibration
  for retained summaries with explicit zero join or set-operation counts while
  keeping unresolved fields, including missing referenced tables, excluded from
  grouping.
- Workload Details now opens for repeated groups derived from safe row-level
  fingerprints even when the summary has no materialized group payload or
  per-row workload member counts. Representatives, limitations, and action
  hints are built only from sanitized rendered rows and safe group membership,
  preserving the raw-free Details boundary while restoring the inspect step for
  retained repeated-workload calibration.
- Impala `sql_shape` primary-bottleneck confidence now reaches `medium` for
  top join, sort, or analytic query-shape findings even when metadata was not
  collected, as long as the classifier is not making a stats-maintenance claim.
  Estimate-only shape cases without a top query-shape finding still remain
  low-confidence until metadata or stronger deterministic evidence is present.
- Impala primary-bottleneck routing now emits a conservative `runtime_memory`
  label when analyzer-owned Memory Pressure Evidence has strong selected-query
  non-zero spill/scratch support. Memory estimates, reservations, profile
  resource memory, daemon metrics, runtime context, unsupported dialects, and
  context-only spill evidence still stay below primary routing; mixed
  stats-plus-memory cases remain `mixed` and keep comparable-rerun
  verification.
- Repeated workload query-shape guidance now uses an explicit mixed
  query-shape review track when the same workload fingerprint has multiple
  safe no-recipe optimizer review tracks. The workload action queue no longer
  picks one arbitrary specific track for the whole group; it directs users to
  review selected cases by their listed tracks and verify bounded changes with
  comparable repeated-group reruns.
- Help shortcuts now link to the isolated local Trino compact-diagnosis page so
  maintainers can reach the raw-free boundary JSON renderer without treating
  Trino as Recent, Details, trusted-report, optimizer, or live Query ID support.
- Added an isolated local `/trino/compact-diagnosis` web page for already
  raw-free Trino `engine_fact_boundary_v1` payloads. It renders deterministic
  compact-diagnosis attention areas, limitations, and boundary status without
  echoing submitted JSON, source schema, fact groups, Query IDs, URLs, paths,
  raw SQL, or source-contract fields; Details, trusted reports, Recent
  workflows, optimizer behavior, live Query ID diagnosis, metadata collection,
  and SQL execution remain unsupported.
- The internal command-spec registry now covers every published Trino console
  script, so module and installed-console backends stay consistent for Trino
  offline/import, coordinator-check, and compact-diagnosis commands.
- The Trino engine adapter now reports the full bounded raw-free support matrix:
  offline evidence package import, local and HTTP archive imports, local/pruned
  QueryInfo imports, source-contract checks, coordinator target/probe/import
  gates, and compact diagnosis, while keeping live Recent, product Query ID
  diagnosis, metadata, trusted reports, optimizer behavior, and SQL execution
  unsupported.
- Trino engine-specific fact naming now has an explicit guardrail: Trino-only
  fact IDs must use `trino_*`, `query_detail_*`, `query_list_*`, or neutral
  `no_*` naming. Existing Trino metric IDs have been moved behind the
  `trino_*` prefix unless they already live in an explicit non-engine-specific
  scope.
- Trino query-list aggregate bucket facts are now snapshot-tested, so adding
  another `query_list_*` fact requires an explicit contract/test update instead
  of incidental namespace growth.
- Trino limitation fact IDs now use neutral `no_*` naming for current unsupported
  boundary coverage. `no_admission_model`, `no_profile_counters`, and
  `no_fragment_lifecycle` replace Impala-named limitation IDs in Trino fact
  bundles and compact diagnosis summaries, with regression coverage that old
  Trino boundary IDs stay absent.
- Trino now has a bounded local pruned QueryInfo import for operator-prepared
  compact JSON. `query-doctor-trino-query-info-pruned-import` validates one
  already-sanitized local pruned QueryInfo object after an accepted
  `coordinator_query_info` source contract, accepts only allowlisted `state`
  and `queryStats` fields, and emits a safe summary or raw-free normalized fact
  boundary. It performs no network read, rejects raw QueryInfo fields, does not
  echo paths or Query IDs, and does not add live Query ID diagnosis,
  browser/report output, optimizer behavior, or SQL execution.
- Trino compact diagnosis now surfaces high peak memory as a raw-free
  attention area when accepted one-query resource facts cross the conservative
  100 GiB threshold. The guidance remains deterministic and bounded: it does
  not claim a root cause, ingest raw Trino payloads, submit SQL, add live
  collection, or expose Trino facts in browser/report surfaces.
- Trino compact diagnosis now surfaces planning-heavy timing as a raw-free
  attention area when accepted timing facts show planning time is both long and
  a large share of elapsed time. The finding remains deterministic guidance
  only: it does not claim a root cause, ingest raw Trino payloads, submit SQL,
  add metadata collection, or expose Trino facts in browser/report surfaces.
- Single-boundary Trino import commands can now write compact diagnosis directly.
  `query-doctor-trino-query-detail-import`,
  `query-doctor-trino-query-list-import`,
  `query-doctor-trino-statement-stats-import`,
  `query-doctor-trino-http-query-detail-archive-import`, and
  `query-doctor-trino-coordinator-query-info-pruned-import` accept
  `--diagnosis-out <path>` and build the diagnosis only from the accepted
  raw-free normalized fact boundary. They reject output paths that would
  overwrite the input/source-contract file, keep stdout path-free, and do not
  ingest raw payloads, submit SQL, add browser/report output, or become live
  Trino diagnosis.
- Trino now has deterministic local compact diagnosis over normalized raw-free
  boundary JSON. The `query-doctor-diagnose-trino-compact` command accepts one
  already raw-free `engine_fact_boundary_v1` payload, rejects non-Trino
  boundaries, and writes raw-free attention areas, supported change directions,
  verification prompts, limitations, parser coverage, lifecycle, and fact-state
  counts. It does not ingest raw Trino payloads, copy input summaries or string
  values, claim root causes, submit SQL, add browser/report output, add
  optimizer behavior, run live Recent scans, or become live Query ID diagnosis.
- `query-doctor-diagnose-trino-compact` can now diagnose one selected sample
  boundary from a Trino package boundary export produced by
  `query-doctor-trino-import --format boundary-json`. Multi-sample package
  exports require `--sample-index <zero-based-index>`, direct boundary JSON
  continues to work without an index, and the isolated
  `/trino/compact-diagnosis` page accepts the same direct boundary or selected
  package sample without echoing submitted JSON. Both paths still reject raw
  payloads, non-Trino boundaries, SQL execution, browser/report output, and
  live Query ID diagnosis.
- Trino now has a bounded pruned coordinator query-info fact import. The
  `query-doctor-trino-coordinator-query-info-pruned-import` command validates
  one compact `coordinator_query_info` source contract with an operator-managed
  auth reference, issues exactly one bounded
  `GET /v1/query/{queryId}?pruned=true` request, maps only allowlisted
  lifecycle and `queryStats` fields into a raw-free normalized fact boundary,
  and emits only safe summaries or boundary JSON. It does not print or store
  the URL, Query ID, raw QueryInfo, query text, session fields, endpoint URLs,
  object names, stage/task identifiers, worker identifiers, raw failures, or
  connector internals, does not crawl query history or submit SQL, and does not
  expose browser/report/optimizer output or live Query ID diagnosis.
- Trino now has a bounded pruned coordinator query-info probe. The
  `query-doctor-trino-coordinator-query-info-pruned-probe` command validates
  one compact `coordinator_query_info` source contract with an operator-managed
  auth reference, issues exactly one bounded
  `GET /v1/query/{queryId}?pruned=true` request, validates the response as a
  bounded JSON object, and emits only a safe summary. It does not print or
  store the URL, Query ID, raw QueryInfo, query text, session fields, endpoint
  URLs, or object names, does not map QueryInfo to facts, does not crawl query
  history or submit SQL, and does not expose browser/report/optimizer output or
  live Query ID diagnosis.
- Trino now has a dry-run coordinator query-info target gate. The
  `query-doctor-trino-coordinator-query-info-target-check` command validates
  one compact future `coordinator_query_info` source contract plus one explicit
  coordinator base URL and Query ID shape, requires redaction-review
  confirmation, and emits only a URL-free and Query-ID-free safe summary. It
  does not contact Trino, issue `/v1/query`, fetch query-info JSON, crawl query
  history, submit SQL, collect live Query ID diagnosis, or expose
  browser/report/optimizer output.
- Trino now has a bounded HTTP query-detail archive import path. The
  `query-doctor-trino-http-query-detail-archive-import` command validates one
  explicit `http_query_detail_archive` source contract, fetches one explicit
  operator-controlled HTTP(S) archive URL, enforces contract byte/depth/timeout
  bounds, and emits only a safe summary or raw-free normalized fact boundary
  JSON for one compact sanitized query-detail record. It does not contact the
  Trino coordinator, fetch query-info by Query ID, submit SQL, perform default
  discovery, echo URLs, accept credentials in URLs, collect live Query ID
  diagnosis, or expose browser/report/optimizer output.
- Optimizer funnel strict repeated no-recipe readiness now requires the
  workload metric to name a comparable repeated-group, group p95, next-scan, or
  rerun signal, so manual guidance cannot pass representative calibration with
  only a generic metric label.
- Workload diagnostics strict action-outcome calibration now fails groups whose
  local feedback exists but has not met the configured applied-sample threshold,
  keeping thin rerun feedback separate from representative outcome evidence.
- Optimizer funnel audits now classify repeated no-recipe workload groups by
  review-track readiness, separating specific allowlisted tracks from
  unknown, missing, mixed, or source-unavailable tracks so recipe/guidance
  follow-up can stay tied to deterministic safe facts.
- Trino now has a bounded HTTP event archive import path. The
  `query-doctor-trino-http-event-archive-import` command validates one explicit
  `http_event_listener_archive` source contract, fetches one explicit
  operator-controlled HTTP(S) archive URL, enforces contract record/byte/depth
  bounds, and emits only safe summaries or raw-free normalized fact boundary
  JSON. It does not contact the Trino coordinator, submit SQL, perform default
  discovery, echo URLs, accept credentials in URLs, collect live Recent scans,
  or expose browser/report/optimizer output.
- Trino now has a raw-free event-source contract check for future event-store
  reader work. The `query-doctor-trino-event-source-contract-check` command
  validates one explicit compact local source-contract JSON for source type,
  safe auth-reference label, accepted event schema, bounds, and redaction
  storage policy. It emits only a safe summary and does not contact Trino, read
  event records, collect query history, submit SQL, or expose browser/report
  output.
- Optimizer funnel audits can now fail strict representative calibration when
  repeated no-recipe workload groups do not have one specific safe review track,
  keeping recipe follow-up behind raw-free review-readiness evidence.
- Workload diagnostics now have a raw-free representative audit,
  `scripts/audit_workload_diagnostics.py`, with
  `--fail-on-workload-readiness-gaps` for strict calibration of repeated group
  detail pages, representatives, regression baselines, workload-history status,
  and comparable verification guidance.
- Workload action-outcome summaries now label each recommendation-family signal
  with the applied-feedback sample threshold, so Action Queue and Workload
  Details distinguish thin local rerun feedback from enough comparable applied
  records without exposing raw case data.
- Stats refresh candidate scoring now consumes the structured analyzer stats
  quality vocabulary consistently, including aggregate `incomplete_or_unknown`
  statuses. Partition row-count gaps and join/filter column stats gaps now
  produce specific raw-free reasons instead of being downgraded to generic or
  unsupported stats evidence.
- Recent Details stats-maintenance action cards now carry the same structured
  raw-free metadata detail into the analyst path, distinguishing partition
  row-count coverage gaps and join/filter column stats coverage gaps before
  proposing an approved stats-maintenance check and comparable rerun.
- Trino now has a bounded local statement-stats import path. The
  `query-doctor-trino-statement-stats-import` command reads one explicit
  compact sanitized local `QueryResults.statementStats` / `rootStage` JSON
  object, requires redaction-review confirmation, enforces file/payload/depth
  bounds, and emits only safe summaries or raw-free normalized fact boundary
  JSON. It does not contact Trino, call `/v1/statement`, submit SQL, crawl
  query history, fetch query-details, or expose browser/report/optimizer
  output.
- Trino now has a bounded local query-list aggregate import path. The
  `query-doctor-trino-query-list-import` command reads one explicit compact
  sanitized local query-list aggregate JSON object, requires redaction-review
  confirmation, enforces file/payload/depth bounds, and emits only safe
  summaries or raw-free normalized fact boundary JSON. The aggregate remains a
  source-shape contract probe, not one-query diagnosis, live Recent scan, live
  query-list crawl, query-detail fetch, SQL execution, or browser/report output.
- Trino now has a bounded local query-detail import path. The
  `query-doctor-trino-query-detail-import` command reads one explicit compact
  sanitized local query-detail JSON object, requires redaction-review
  confirmation, enforces file/payload/depth bounds, and emits only safe
  summaries or raw-free normalized fact boundary JSON. It does not contact
  Trino, fetch query-info by Query ID, submit SQL, collect live query history,
  or expose browser/report/optimizer output.
- Trino now has a bounded local event-store import path. The
  `query-doctor-trino-event-store-import` command reads one explicit
  already-sanitized JSON, JSON-array, or NDJSON file of compact Trino
  event-listener records, requires redaction-review confirmation, enforces
  byte/record/depth bounds, and emits only safe summaries or raw-free normalized
  fact boundary JSON. It does not contact Trino, submit SQL, collect live query
  history, or expose browser/report/optimizer output.
- Trino now has a packaged sanitized offline evidence import path. The
  `query-doctor-trino-import` command validates already-sanitized compact
  evidence packages and can emit raw-free normalized fact boundary JSON, while
  the Trino adapter remains disabled for live Recent scans, Query ID fetch,
  metadata collection, browser/report output, optimizer behavior, and
  Query Doctor-generated Trino SQL.
- Workload Action Queue and Workload Details now share one workload action
  contract: the queue dispatches the analyst to the repeated group to open,
  while Details renders the matching why/where/change/verify action plan and
  the local rerun outcome summary for that workload. Workload Details also
  links representative cases directly to their case Action cards for recording
  rerun feedback, and outcome summaries distinguish the last applied
  action/result from skipped latest records while naming the recommendation
  family signal to compare on the next comparable scan.
- Details Recommended changes now always renders an analyst decision path,
  including clean/no-candidate cases. When deterministic facts do not support a
  query-shape, stats, runtime, or processing follow-up, Details says no
  supported change is recommended, points Diagnostics at coverage/limitations,
  and tells the analyst what to confirm on the next comparable scan or rerun.
- Spark research now has an experimental bounded Spark History Server compact
  intake. The new CLI reads only explicit-application summary `/api/v1` JSON,
  requests SQL summaries with details and plan descriptions disabled, skips raw
  event-log and environment endpoints, validates a
  `spark_history_server_compact_v1` payload, and can write raw-free normalized
  engine fact boundary JSON. At that stage it still added no Spark engine
  registration, Recent workflow, Details/trusted report output, optimizer
  behavior, Spark job execution, raw SQL/plan/log collection, or public Spark
  support claim.
- Spark compact intake can now write a deterministic local compact-diagnosis
  JSON. The summary turns accepted Spark compact facts into raw-free attention
  areas, change directions, verification prompts, and explicit support
  limitations without making root-cause claims or wiring Spark into Details,
  trusted reports, optimizer behavior, Recent scans, or engine registration.
- Spark compact CLI entry points now have explicit internal command-spec roles
  for both module and installed console-script invocation. This keeps packaged
  CLI coverage aligned without wiring Spark into Recent workflows, Details,
  trusted reports, optimizer behavior, or engine registration.
- Spark History Server compact collector CLI now rejects overlapping output
  paths and reports filesystem write failures with a fixed safe message. It no
  longer echoes local output paths, request selectors, or endpoint values on
  these error paths.
- `query-doctor-diagnose-spark-compact` can now diagnose an existing accepted
  Spark compact JSON file offline and write deterministic raw-free compact
  diagnosis plus optional engine fact boundary JSON. It rejects invalid JSON
  safely without printing local paths or raw payload fragments.
- The direct local `/spark/compact-diagnosis` web page can now validate one
  accepted compact Spark JSON summary and render raw-free deterministic
  attention areas, limitations, and verification direction. It does not display
  submitted JSON back after validation and remains outside primary navigation,
  Details, trusted reports, Recent workflows, optimizer behavior, engine
  registration, and Spark support claims.
- The same isolated Spark compact web page can now collect bounded summary-only
  Spark History Server JSON for one explicit application, immediately validate
  and diagnose the compact facts, and render only endpoint counts, warning IDs,
  attention areas, limitations, and verification direction. It does not display
  request selectors or compact JSON back and still skips raw event logs,
  environment/log dumps, SQL text, plan descriptions, Spark job execution,
  Recent workflows, Details, trusted reports, optimizer behavior, engine
  registration, and Spark support claims.
- Spark History Server web collection errors now show only allowlisted safe
  messages or a fixed safe collection-failure state. Unexpected lower-layer
  error text cannot echo History Server URLs, request selectors, or SQL-like
  fragments into the isolated Spark compact page.
- Spark History Server compact intake now also reads the optional explicit
  application summary endpoint for bounded application lifecycle and attempt
  count facts. Missing application endpoints stay warning/unknown, attempt
  counts are bounded by `maxApplicationAttempts`, and raw application IDs,
  attempt IDs, users, names, host fields, SQL text, plans, and logs remain
  unwritten.
- Spark compact facts now expose a raw-free `spark_version_family` boundary
  fact when the compact source safely provides a family such as `spark_4_1`.
  Raw Spark version strings stay outside normalized facts, and unsupported or
  missing version-family labels fail closed to `unknown`.
- Spark compact facts now map accepted application lifecycle and application
  attempt state into Spark-specific normalized facts. Missing application
  summaries, bounded-out attempts, and `unknown` lifecycle labels stay
  `unknown` instead of backfilling supported lifecycle evidence.
- Spark compact facts now map bounded aggregate running, skipped, and unknown
  job-state counts. Zero counts are `not_observed`, positive counts are
  `supported`, and missing job summaries stay `unknown` instead of emitting
  fake job-state evidence.
- Spark History Server compact intake can now use SQL-linked job IDs to keep
  stage, spill, skew, and task aggregates supported when the jobs endpoint is
  unavailable. Job-state facts still stay `unknown` until the jobs endpoint
  provides supported evidence.
- Spark History Server compact intake can now filter stage summaries by
  job-linked stage IDs when stage summaries omit job IDs. The IDs remain
  parser-local and are not written to compact payloads or diagnosis output.
- Spark compact facts now include Spark-specific aggregate input/output byte
  facts from accepted stage summaries when every selected stage has an explicit
  safe value. Partial or missing stage byte summaries stay `unknown`; the
  values are not promoted to shared input/output facts and no raw stage IDs are
  written.
- Spark History Server compact intake now separates task evidence states, so
  supported aggregate task and failed-task counts from stage summaries can feed
  raw-free facts while unavailable retry counts and duration buckets stay
  `unknown` instead of becoming fake zeros.
- Spark History Server compact intake can now map explicit aggregate retried
  task counts from accepted stage summaries. Missing, partial, or inconsistent
  retry aggregates still stay `unknown`, and no task IDs or task details are
  written.
- Spark compact facts now include a Spark-specific aggregate
  `spark_scheduler_delay_ms` signal when every selected stage summary provides
  an explicit safe scheduler-delay value. Partial or missing scheduler-delay
  summaries stay `unknown`, and compact diagnosis treats supported scheduler
  delay as runtime context rather than an admission/root-cause claim.
- Spark History Server compact intake can now map explicit aggregate task
  duration buckets from accepted stage summaries. Missing, partial,
  over-bound, or inconsistent bucket aggregates still stay `unknown`, and no
  task IDs, task details, raw bucket source field names, or task payloads are
  written.
- Spark History Server compact intake now records executor churn as its own
  checked aggregate state from the bounded executor summary endpoint. Inactive
  executors set both executor-loss and executor-churn evidence, no inactive
  executors become `not_observed`, and dynamic allocation remains `unknown`
  unless a compact source explicitly supports it.
- Spark executor compact facts now separate dynamic-allocation evidence from
  the broader executor summary state. History Server intake can map explicit
  dynamic-allocation markers from executor summaries, while absent markers stay
  `unknown` instead of becoming fake disabled evidence.
- Spark executor compact facts now include aggregate executor memory
  used/capacity when bounded executor summaries provide complete safe values.
  Missing, partial, or internally inconsistent values stay `unknown`, raw
  executor IDs/hosts/logs/source field names are not written, and the facts are
  not modeled as peak memory or Spark support claims.
- Spark History Server compact payloads now carry a raw-free source coverage
  summary with allowlisted warning IDs. Offline Spark compact diagnosis can
  retain missing-endpoint context without endpoint URLs, request selectors, raw
  errors, or raw response payloads.
- Spark compact diagnosis now surfaces incomplete Spark History Server source
  coverage as its own raw-free local attention area. The area references only
  the safe coverage fact and allowlisted warning IDs, and still makes no
  root-cause or Spark support claim.
- Spark History Server compact collection now records a safe
  `spark_history_sql_execution_not_found` warning when an explicit SQL
  execution selector has no accepted summary. The selector itself is still not
  written to compact payloads, browser output, or diagnosis artifacts.
- Spark History Server compact provenance now records `exact_query` only when
  an explicit SQL execution selector finds an accepted summary. Application-only
  compact collection stays at `same_application` even when the collector uses a
  bounded SQL summary to filter safe aggregate facts.
- Spark History Server compact intake can now map explicit checked adaptive
  execution booleans from SQL summaries. Raw plan text is still not collected
  or written, and unchecked or partial adaptive markers remain `unknown`.
- Spark compact diagnosis now surfaces supported executor-churn observations as
  a separate raw-free attention area with verification direction. It remains
  local compact output only and still makes no root-cause claim or Spark support
  claim.
- Spark compact diagnosis now also surfaces supported failed-job and
  failed-stage counts as separate raw-free attention areas with verification
  direction. These areas are context only and still do not create root-cause or
  Spark support claims.
- Spark adaptive execution facts now honor the compact source's checked marker.
  Unchecked History Server adaptive fields stay `unknown`, while checked
  adaptive plan changes can surface as a raw-free local compact-diagnosis
  attention area without a root-cause claim.
- Normalized engine facts now have an explicit fact namespace registry that
  records shared, distributed-SQL-family, source-boundary, support-boundary, and
  engine-specific fact IDs with allowed engines. Engine fact bundles and
  boundary consumers now reject unregistered or wrong-engine fact IDs, while
  cross-engine attention signals are registry aliases over engine-specific
  facts rather than reused counters. Spark fixture facts remain `spark_*` or
  Spark support-boundary facts and still add no Spark product support.
- Spark research now has a fixture-only compact fact-envelope mapper for the
  synthetic `spark_history_eventlog_compact_v1` fixture. It maps only raw-free
  application, SQL execution, job, stage, task, executor, data-movement, and
  limitation summaries into the normalized boundary/consumer probe tests. At
  that stage it still added no Spark engine registration, live collector,
  Details/trusted report output, optimizer behavior, or public support claim.
- Trino fixture-only query-list package coverage now includes a second
  sanitized aggregate summary with non-zero long-duration, queue-delay,
  high-memory, unknown-input, and blocked-reason buckets. The evidence-package
  demo now validates two query-list summary samples while keeping the facts
  aggregate-only and unwired from live collection, browser/report output,
  optimizer behavior, or public Trino support.
- Trino fixture-only query-list summary mapping now exposes aggregate bucket
  facts for sanitized elapsed-duration, queued-duration, peak-memory,
  processed-input, and blocked-reason summaries. The validator rejects bucket
  counts that exceed summarized records or their corresponding field-presence
  counts, and the facts remain aggregate-only: no live Trino collection,
  engine registration, browser/report output, optimizer behavior, or public
  Trino support claim is added.
- Direct Impala admission/runtime Details now point action candidates to
  profile resource facts and profile timing facts when selected-query admission
  evidence came from those analyzer-owned profile facts, while keeping the
  visible anchors raw-free.
- Direct Impala Details now include raw-free source limitations when
  Cloudera Manager-only event context, optional Prometheus runtime metrics, or
  bounded Impala metadata are unavailable for a case.
- Owner-gated Recent and Running scan forms now fail closed visibly when the web
  process has no configured owner user: the Username dropdown shows that no
  owner is configured and scan submit is disabled instead of presenting an empty
  required filter. Results and Details also expose a visible open New scan form
  so repeat scans do not depend on finding a collapsed form section.
- Synthetic demo generation now writes validated trusted case reports through
  the current Python Report artifact contract. The demo pack again works as a
  public web smoke path after the Python Report / Query Doctor Report split,
  and Details pages no longer rely on the legacy generic report marker for
  their trusted report state.
- Details pages now visually flatten the outer case container so the verdict,
  recommendations, diagnostics, and selected-case actions read as one case page
  instead of nested panels inside a larger panel. The existing safe anchors and
  server-rendered Details structure remain unchanged.
- Known Query ID Details now reuses the same report and optimizer availability
  gates as Recent Details. Clean or otherwise non-actionable analyzed queries
  keep report/optimizer actions unavailable, hide the Query LLM optimizer and
  combined Python report + optimizer buttons, and reject direct optimizer POSTs
  without starting a job.
- Details pages now make report mode explicit: the recommended Python Report
  baseline and optional LLM narrative are separate selected-case actions with
  separate validated artifacts. Combined report + optimizer execution now uses
  the Python report baseline instead of implicitly inheriting the global LLM
  report mode, while web scans still do not auto-run reports, LLM narratives,
  or optimizer jobs.
- The default `recent_scan_timezone` is now `UTC` in the built-in web defaults
  and canonical example config. Existing configs that set a different IANA
  timezone keep their configured scan-hour behavior.
- Recent scan optimizer Details and Workload Action Queue now use allowlisted
  track-specific verification wording and workload comparison metrics for every
  visible no-recipe review track, including aggregate, set-operation, join,
  scan/projection, CTE, derived-table, and source-unavailable paths. These
  remain guidance-only/no-draft paths, but the browser now points users toward
  the concrete EXPLAIN, shape-stability, source-resolution, and
  comparable-rerun signals to check next.
- Trusted optimizer `no_rewrite` and recommendations-only outcomes now add
  raw-free, family-specific guidance for common no-recipe SQL shapes. Plain
  aggregate/distinct, set-operation, nested-query, join-review,
  single-relation filter, complex CTE graph, CTE no-downstream-filter, and
  derived-table boundary cases now tell users what to inspect and how to verify
  one bounded manual change without producing a SQL draft.
- Recent batch summaries now persist an allowlisted `no_recipe_review_track`
  for no-recipe and source-unavailable optimizer outcomes and aggregate
  `no_recipe_review_track_counts` in the optimizer rewriteability distribution.
  This makes no-draft backlog mix visible without parsing recommendation text or
  re-reading raw SQL.
- Recent scan optimizer cards now surface the allowlisted no-recipe review track
  in browser-safe fact summaries, so Details/action-candidate text can show the
  review family without exposing SQL, identifiers, or artifacts.
- Recent scan action candidates now use the same allowlisted no-recipe review
  tracks to add browser-safe review areas and first-change directions, making
  guidance-only optimizer outcomes more actionable when no trusted SQL draft is
  available.
- Workload Action Queue now rolls repeated query-shape groups up to the dominant
  allowlisted no-recipe review track, so repeated guidance-only workloads show
  a specific review anchor and comparison metric instead of only a generic SQL
  shape follow-up.
- Optimizer no-recipe guidance now distinguishes filtered scalar aggregate
  shapes as their own allowlisted review track. These cases remain
  guidance-only with no SQL draft, but Details and workload rollups can point
  operators toward filter selectivity, partition pruning, stats freshness, and
  aggregate input rows instead of the broader aggregate/distinct bucket.
- Optimizer no-recipe guidance now splits the remaining plain
  aggregate/distinct bucket into grouped aggregate, DISTINCT aggregate, scalar
  multi-aggregate, and scalar aggregate review tracks. These tracks remain
  guidance-only and route operators toward grain, duplicate semantics,
  input-row, stats, pruning, and projection review instead of implying a SQL
  rewrite.
- Optimizer no-recipe guidance now splits plain set-operation cases into
  allowlisted UNION ALL branch review tracks for projection boundaries,
  projection mismatches, nested or aggregate branch boundaries, join-heavy
  branches, filtered/unfiltered/mixed-filter branches, and mixed or distinct
  set-operation boundaries. These tracks remain guidance-only and do not add a
  trusted SQL draft path.
- Trino fixture-only evidence intake now accepts a compact sanitized
  `query_detail_export` sample under an explicit source contract. It maps only
  summary-level timing/resource/stage facts and a checked task summary into the
  raw-free engine fact bundle, rejects raw query-detail identifiers before
  mapping, and still does not add live Trino collection, engine registration,
  browser/report output, optimizer behavior, or public Trino support.
- The normalized engine-fact consumer probe now turns positive Trino
  query-detail task retry/failure counts into state-backed internal attention
  signal IDs. Zero `not_observed` counts and `unknown` task summaries still
  produce no signal, and the probe remains unwired from Recent ranking,
  browser output, reports, live collection, or Trino support claims.
- Trino compact task-count summaries now require non-negative integer count
  values. Fractional `safeTaskSummary` counts and optional `sampledTaskCount`
  values fail closed to `unknown` instead of producing task or stage-skew
  evidence.
- The fixture-only Trino evidence-package demo now includes both compact
  query-detail task-summary variants: one retry-count sample and one
  failure-count sample under the same safe `query_detail_stage_task_summary`
  case. The builder and validator still print only path-free package summaries
  and do not contact Trino, execute SQL, or claim live Trino support.
- Trino query-detail source-contract coverage now includes a committed
  unsupported-contract fixture and package/demo sample. It fails closed to
  `unknown` parser coverage and `unknown` facts even when the compact payload
  contains otherwise numeric timing/resource/stage/task fields.
- Trino query-detail missing-field coverage now includes a committed accepted
  source-contract fixture and package/demo sample. It keeps absent lifecycle,
  timing, resource, stage, and task facts `unknown` instead of converting them
  into zero values or `not_observed`.
- Trino query-detail blocked coverage now includes a committed accepted
  source-contract fixture and package/demo sample. It derives blocked evidence
  only from an explicit compact boolean `fullyBlocked` field and keeps the
  behavior fixture-only, raw-free, and unwired from live collection or product
  surfaces.
- Trino fixture-only `fullyBlocked` handling now accepts only boolean compact
  values across statement-statistics, event-listener, and query-detail paths.
  Non-boolean values fail closed to `unknown` blocked state and
  `unknown` blocked-signal facts instead of becoming truthy blocked evidence.
- Trino event-listener fixture-only resource queue handling now accepts
  `not_observed` queue evidence only from an explicit compact boolean
  `queued: false`. Falsey non-boolean values fail closed to `unknown` queue
  timing instead of becoming absence evidence.
- Trino query-detail failure-category coverage now includes a committed
  accepted source-contract fixture and package/demo sample. It maps only an
  allowlisted checked `resource_limit` category from compact
  `safeFailureSummary` and keeps raw exception text, stack traces, query IDs,
  connector details, live collection, and product surfaces out of scope.
- Trino query-detail spill coverage now includes a committed accepted
  source-contract fixture and package/demo sample. It maps only compact
  `spilledBytes` into raw-free spill evidence and keeps query IDs, task IDs,
  worker details, live collection, and product surfaces out of scope.
- Trino query-detail stage-skew coverage now includes a committed accepted
  source-contract fixture and package/demo sample. It maps only checked
  aggregate candidate/ratio fields into raw-free skew evidence and keeps stage
  IDs, task IDs, worker details, live collection, and product surfaces out of
  scope.
- Trino query-detail queued coverage now includes a committed accepted
  source-contract fixture and package/demo sample. It maps only queued
  lifecycle and queued timing, leaves absent resource/stage/task fields
  `unknown`, and keeps resource-group attribution, live collection, and product
  surfaces out of scope.
- Trino query-detail connector-metric coverage now includes a committed
  accepted source-contract fixture and package/demo sample. It maps only a
  checked/present compact connector metric summary, keeps connector names,
  metric names, endpoints, object context, live collection, and product
  surfaces out of scope, and does not change public Trino support status.
- Trino query-detail connector-metric absent coverage now includes a committed
  accepted source-contract fixture and package/demo sample. It maps the same
  checked/present compact summary shape to `not_observed`, produces no
  connector attention signal, and keeps live collection, UI/report output, and
  public Trino support claims out of scope.
- Query LLM optimizer now has a Python-owned deterministic
  `cte_union_branch_filter_pushdown` recipe for the narrow single-CTE
  `UNION ALL` case where a final `WHERE` predicate can be copied into eligible
  branch `WHERE` clauses through simple branch projection mapping. The final
  filter stays in place, unsupported branch shapes remain untrusted, and the
  trusted draft is written only after recipe-specific validation preserves
  branch count/order, physical tables, literals, original filters, and final
  output shape.
- Query LLM optimizer also has a Python-owned deterministic
  `single_derived_table_projection_alias_predicate_pushdown` recipe for one
  top-level derived table where an outer `WHERE` predicate targets a derived
  output alias that maps to one unqualified source column. The outer filter
  stays in place, expression/function/cast projections remain untrusted, and
  validation preserves the derived-table alias, projections, physical tables,
  literals, original filters, and outer output shape.
- Trusted optimizer `no_rewrite` and recommendations-only outcomes now include
  explicit no-draft review guidance and verification steps. When Query Doctor
  cannot show a trusted SQL draft, the optimizer tells users to treat the
  output as manual review guidance and to compare EXPLAIN plus a comparable
  rerun before claiming benefit.
- Expanded the synthetic demo pack from three to eleven cases. The generated
  demo now leads with Workloads/Action Queue and covers optimizer
  recommendations, stats maintenance, rejected optimizer drafts,
  admission/runtime workload regression, Storage/HDFS runtime follow-up,
  frequent-short workload handling, mixed diagnostic signals, unknown-but-useful
  limited evidence, direct Impala compatibility, and local synthetic action
  outcomes while remaining local, synthetic, raw-free, and independent of LLM,
  network, Cloudera Manager, Impala, or Prometheus access.

### Documentation

- Public code-audit and Codex handoff baselines now record the implemented
  Trino source-contract registry as the owner of accepted preview source kinds,
  raw policy, bounds, network-access classes, and promotion gates, and record
  the engine fact promotion-policy registry as the owner of promoted shared,
  distributed, source-boundary, and support-boundary facts. The remaining broad
  Trino/Spark architecture backlog now excludes both implemented registries and
  keeps only shared readiness/handoff helpers.
- Code audit and safety contract now record two open trust-boundary follow-ups:
  shared outbound HTTP egress policy for CM, Prometheus, Spark History, direct
  Impala, and LLM clients; and report-language validator parity/registry guards
  for future language expansion. These entries document the risk and required
  guard tests; they do not mark the hardening work as implemented.
- Code audit and safety contract now also record two prompt/report safety
  follow-ups: explicit prompt-injection framing and guard tests for Query
  Optimizer rewrite prompts, plus direct regression coverage for fail-closed
  trusted-output paths, validation-mode marker rejection, and defensive web
  fallback handlers. These entries document open hardening work, not completed
  implementation.
- Code audit and safety contract now record two additional defense-in-depth
  follow-ups: an adversarial redaction corpus for free-text host and secret
  variants in local/log/browser fallback surfaces, and explicit
  pathological-within-cap regression coverage for regex resource-bound paths.
  These entries document open guard work; they do not mark redaction or ReDoS
  hardening as implemented.
- Code audit and release-readiness docs now record final release-hygiene
  follow-ups from the pre-push, packaging, and demo-data audits: merge-heavy
  local history must be cleaned into semantic review commits before any public
  branch handoff, package version metadata should move toward one canonical
  source, and committed fixtures plus README screenshots need stronger
  provenance guards.
- Code audit and safety contract now record the round-2 public-safe audit
  follow-ups: report validators need adversarial coverage for indirect
  unsupported claims and soft recommendation wording, trusted report markers
  should bind the current marker schema version, browser display must keep
  model/runtime fingerprints hidden, generated case staging directories need
  explicit ignore coverage, and traversal/symlink artifact guards should be
  pinned by tests. The subprocess output capture follow-up from that audit is
  now implemented above; the remaining entries document open hardening work.
- Public README is now a demo-first entry point instead of a broad CLI/reference
  document. The root English and Russian READMEs keep install, synthetic demo,
  screenshots, support boundaries, safety rules, and high-value next links,
  while full command/reference detail stays in focused docs and command `--help`
  output.
- Agent baseline docs now make `agent-quickstart.md` the canonical operational
  contract for worktree/commit/local-merge cleanup, align
  `codex-handoff.md` with the local `main` merge rule, and wire
  `engine-support-gap-matrix.md` into the agent read path as the source of
  truth for Impala support, Trino fixture/private-preview, Spark compact
  research, and second-engine promotion gates. The safety contract now records
  `engine_fact_boundary_v1` as a raw-free contract seam rather than a support
  claim or product engine registry.
- Roadmap now records the global Impala diagnostic-quality goals: deepen direct
  Impala reliability, expand profile evidence only through explicit
  dialect/section mappings, improve primary-bottleneck coverage through
  deterministic facts rather than stronger wording, keep Details analyst-first,
  grow sanitized fixtures and real-batch audits, and prepare future engine
  contracts without adding new support claims.
- Agent Git instructions now make verified local branch integration the default:
  complete, committed, validated, and clean task branches are merged into local
  `main` and cleaned up in the same turn unless the user explicitly asks to stop
  before merge. Push, rebase, amend, and force-push still require an explicit
  request.
- Roadmap now records a deterministic-first / no-LLM-capable product posture:
  Recent diagnosis, Details, Python reports, trusted optimizer outcomes, demos,
  and validation must remain useful when `no_llm=true`, while LLM-backed
  wording stays an optional selected-case extension. The next UI/help wording
  cleanup should move toward neutral `Report` and `Query optimizer` labels
  without hiding backend status such as `Python-owned`, `LLM-backed`, or
  `no_llm=true`.
- Minimized public documentation surface for validation, model-route,
  engineering-audit, analyzer-audit, repository-hardening, architecture, and
  smoke-run docs. Local run journals, model bake-off tables, real-looking case
  IDs, private connectivity commands, generated output paths, and detailed
  maintainer evidence now stay out of committed public docs and belong under
  local exclude-only notes.
- Split committed public documentation from ignored local agent notes. The
  public agent baseline now stays durable and path-free, local exclude-only
  notes are the home for private continuation notes, and
  `scripts/audit_public_docs.py` plus staged public-safety checks block common
  local handoff markers before commit.
- Updated the optimizer roadmap, validation log, code audit, handoff, and agent
  playbook to record the current candidate-calibration baseline, broad smoke
  audit interpretation, and next-step rule: use raw-free funnel and shape
  audits before adding recipes or changing thresholds.
- Documented the current-upstream Impala smoke baseline with generic direct
  Impala placeholders and follow-up gates for broader current-Impala support
  wording, without committing private hostnames, local config, target selectors,
  query IDs, raw profiles, generated case paths, or smoke artifacts.
- Added a Trino private-preview release path for closed test-cluster work. The
  runbook defines allowed and forbidden release wording, the dev-only
  Kerberos/SPNEGO smoke, sanitized evidence-package intake, and release gates
  while keeping Apache Impala as the only production engine support.
- Updated public package metadata, agent-facing baseline docs, and web UI
  branding so the product is named Query Doctor rather than
  `impala-query-doctor`, while keeping Apache Impala as the current production
  engine support boundary and Trino as fixture-only groundwork. Refreshed the
  public README screenshots from the synthetic demo pack for the updated header.
- Added Trino evidence package templates for the first operator-exported
  sanitized handoff package. The templates pin manifest and redaction-note
  structure, safe package labels, redaction assertions, and fixture-only
  acceptance gates without adding a live Trino collector, engine selector,
  browser/report surface, optimizer behavior, or public support claim.
- Added a Trino test-cluster evidence export checklist. It defines the first
  operator-exported sanitized handoff package for future real-cluster fixtures
  without adding a live Trino collector, engine selector, browser/report
  surface, optimizer behavior, or public support claim.
- Refreshed the active documentation baseline after the post-merge readiness
  pass: release readiness, repository hardening, validation log, architecture,
  code map, security model, release checklist, and Russian companion
  configuration/readiness docs now reflect the 0.4.0 published release state,
  optional direct Impala JSON, `/profile_docs`, `/admission?json` probes, Trino
  private-preview groundwork, and the 2026-05-26 README screenshot drift check.
  The Russian documentation index now includes companion pages for all current
  English Markdown docs, records the translation status, and defines a
  terminology policy to keep prose readable while preserving exact product and
  contract identifiers.

### Engineering

- Impala profile-evidence audit now distinguishes stale persisted
  primary-classifier artifacts from live gate mismatches. If a retained
  `analysis.json` and summary agree with an older primary label but the current
  deterministic classifier now returns a safer mixed label, the audit reports
  artifact drift separately while still failing real summary/current-fact
  mismatches and confidence overclaims.
- Impala diagnostic coverage audit now reports strict primary-coverage rates
  over analyzed non-clean cases only, excluding explicit out-of-scope unknown
  reasons such as very short or unknown wall-clock evidence and unsupported
  profile dialects. The full unknown/gap breakdown is still reported, but
  strict readiness no longer fails just because clean or structurally
  out-of-scope cases correctly stay unknown.
- The legacy `setup.py` editable-install shim now reads the package version
  from `pyproject.toml`, making `[project].version` the single canonical
  version source while keeping console-script parity tests for older tooling.
- Added research-only Spark compact fixture schema validation and focused tests
  for raw-field rejection, unsafe text, non-finite and negative values,
  boolean marker typing, limitation coverage, and the absence of Spark engine
  registration.
- Added presenter regression coverage for complete allowlisted no-recipe
  verification text coverage, workload-level comparison metrics, and
  unknown-token suppression.
- Added a raw-free optimizer shape-guidance helper and regression coverage for
  deterministic no-recipe recommendations, recommendations-only prompt context,
  and Recent rewrite-support reasons. The helper classifies unsupported shape
  families without exposing SQL text, table names, column names, or artifacts.
- Updated optimizer structural and representative audit scripts to group plain
  no-recipe backlog by the same raw-free review tracks used in user-facing
  guidance, so broad smoke output no longer collapses aggregate/distinct,
  set-operation, nested-query, join, and single-relation filter cases into one
  generic plain blocker.
- Added regression coverage for structured no-recipe review-track serialization,
  batch summary aggregation, markdown rendering, source-unavailable fallback,
  and allowlisted audit handling for stored summaries.
- Added presenter regression coverage for browser-safe no-recipe review-track
  labels and unknown-token suppression.
- Added action-candidate regression coverage for browser-safe no-recipe review
  areas/change directions and unknown-token suppression.
- Added Workload Action Queue regression coverage for repeated no-recipe review
  tracks and browser-safe group-level wording.
- Added filtered scalar aggregate review-track regression coverage for
  rewrite-support classification, raw-free optimizer recommendations,
  browser-safe fact summaries/action guidance, and raw-free audit grouping.
- Added aggregate/distinct subtrack regression coverage for grouped,
  DISTINCT, scalar multi-aggregate, and scalar aggregate classification,
  raw-free optimizer recommendations, browser-safe action guidance, and
  plain-shape audit grouping.
- Added set-operation review-track regression coverage for rewrite-support
  classification, raw-free optimizer recommendations, browser-safe fact
  summaries/action guidance, plain-shape audit grouping, and set-operation
  audit filtering.
- Added accepted and rejected optimizer regression coverage for
  `cte_union_branch_filter_pushdown`, including the fixture bake-off corpus,
  deterministic CLI generation without LLM SQL drafting, Recent rewrite-support
  classification, and validator rejection when a branch predicate is not copied
  from the final SELECT.
- Added accepted and rejected optimizer regression coverage for
  `single_derived_table_projection_alias_predicate_pushdown`, including the
  fixture bake-off corpus, deterministic CLI generation without LLM SQL
  drafting, Recent rewrite-support classification, and validator rejection when
  a derived-table predicate is not copied through the projection alias.
- Expanded the raw-free optimizer funnel audit with candidate calibration
  counters and a compact headline for medium/high share, draft-supported,
  guidance-only, and source-unavailable counts. The audit remains offline,
  aggregate-only, and source-SQL-free, and now has regression coverage for
  suppressing SQL-like or path-like reason strings from public output.
- Direct Impala profile-doc collection now probes the intended
  machine-readable `/profile_docs/?json` endpoint before falling back to the
  human `/profile_docs` HTML table. The collector still writes only the safe
  allowlisted counter-stability registry context and treats missing old
  endpoints as non-fatal.
- Added a repeatable fixture-only Trino evidence-package walkthrough for
  committed synthetic fixtures. The local demo command builds and validates the
  package shape, can optionally write a sanitized demo package, and prints only
  the path-free safe summary without live collection, SQL execution, credential
  access, engine registration, UI/report output, optimizer behavior, or support
  claims.
- Added a fixture-only Trino evidence package builder for already-sanitized
  compact sample JSON files. The local script assembles the package wrapper,
  requires explicit redaction-review and sentinel-test confirmations, validates
  before writing output, and prints only path-free safe summaries without adding
  live collection, engine registration, UI/report output, optimizer behavior, or
  support claims.
- Tightened fixture-only Trino evidence package intake. The package wrapper now
  rejects unsupported top-level sections, and the local dry-run validator prints
  a bounded safe manifest source summary alongside parser coverage and case
  counts without echoing input paths, raw payloads, raw values, or rejected
  record contents.
- Added a fixture-only Trino `/v1/query` list-shape contract probe. The mapper
  accepts only a sanitized aggregate summary with bounded record counts,
  field-presence counts, safe state/failure buckets, and explicit redaction
  assertions, then emits raw-free normalized facts without query-detail fetches,
  SQL statement submission, live collection, an engine selector,
  browser/report output, optimizer behavior, or a support claim.
- Added a dev-only Trino Kerberos/SPNEGO smoke script. It uses `curl` with an
  explicit Kerberos service name, executes only built-in read-only smoke
  statement shapes, follows bounded Trino protocol pages, and writes a safe
  summary without statement text, result values, query identifiers, actor
  identity values, coordinator hostnames, object names, or raw failure details.
  It is not wired into Query Doctor product workflows and does not add live
  Trino collection, an engine selector, browser/report output, optimizer
  behavior, or a support claim.
- Added a local Trino evidence package validator script. `python3
  scripts/validate_trino_evidence_package.py <sanitized-package.json>` checks
  the fixture-only package intake gate and prints only a safe package summary
  or safe rejection message, without echoing raw payloads, file paths, raw
  values, SQL text, identifiers, hostnames, object names, connector details, or
  rejected record contents.
- Added fixture-only Trino evidence package intake validation. A local
  sanitized package wrapper with `manifest`, `redaction_note`, and `samples`
  now fail-closed checks package labels, redaction assertions, sentinel-test
  coverage, declared bounds, sample counts, raw-free payloads, and existing
  statement-statistics/event-listener fixture validators without adding live
  Trino collection, an engine selector, browser/report output, optimizer
  behavior, or a support claim.
- Hardened fixture-only Trino compact summary shapes. Connector-metric,
  failure-category, and stage-skew summaries now stay `unknown` when extra
  fields or nested detail objects are present.
- Pinned fixture-only Trino JSON shape guards. Statement-statistics and
  event-listener fixture checks now explicitly cover nested objects, arrays,
  and maximum-depth rejection before mapping.
- Hardened fixture-only Trino numeric intake. Statement-statistics and
  event-listener payloads now reject non-finite numeric values such as `NaN`,
  `Infinity`, and `-Infinity` before mapping.
- Hardened fixture-only Trino numeric fact mapping. Negative timing, resource,
  split, stage-count, queue-time, and ratio values now stay `unknown` instead
  of becoming supported facts or fake zeros.
- Tightened the fixture-only Trino statement-statistics intake boundary. The
  mapper now rejects oversized statement-statistics payloads plus unsafe raw
  field names and text values before mapping, matching the existing
  event-listener fixture safety behavior without adding live Trino support.
- Expanded fixture-only Trino event-listener coverage with a synthetic
  resource-group queue-delay event. The normalized engine-fact contract now
  keeps query-specific queue time state-backed and raw-free without adding a
  live Trino reader, engine adapter, UI/report surface, optimizer behavior, or
  public support claim.
- Added a fixture-only Trino unknown source-contract event gate. Event payloads
  with an unsupported compact source contract now fail closed to unknown parser
  coverage and unknown facts, even when the payload contains otherwise numeric
  timings or resource counters.
- Added context-only Resource Trace Facts for Impala profiles. The analyzer now
  parses allowlisted CPU, disk, and network resource-trace samples into safe
  aggregate facts, treats missing traces as `unknown`, and keeps host-wide
  resource traces out of primary-bottleneck promotion.
- Added a raw-free offline profile evidence-gate audit for existing Recent
  `batch_summary.json` files. The script aggregates profile dialect, evidence
  quality, counter registry, client-fetch, admission, memory, backend
  execution-tail, scan-skew, runtime-filter, and storage-context gates, and can
  fail when a primary bottleneck is not backed by the expected deterministic
  analyzer gate.
- Added a raw-free offline Impala coverage-gap audit for existing Recent batch
  summaries. The script aggregates implemented source coverage, profile
  dialect, profile counter registry, evidence quality, and ranked diagnostic
  follow-up opportunities without printing case IDs, SQL, profiles, hosts,
  paths, or raw artifact names.
- Refined the offline Impala coverage-gap audit to separate missing primary
  labels from analyzed `unknown` primary bottlenecks and to print a safe
  unknown-reason breakdown. Missing analysis is no longer counted as
  deterministic unknown evidence.
- Added supporting-only calibration breakdowns to the offline Impala
  coverage-gap audit. Scan-skew and data-movement follow-up opportunities now
  include safe aggregate reason counts for missing timing, row-only spread,
  bytes below threshold, low exchange share, and similar non-promoting gates.
- Added data-movement calibration signals to the offline Impala coverage-gap
  audit. The script now reports safe bucketed exchange/data-movement status,
  evidence tier, finding/primary support, exchange-count, byte-threshold,
  exchange elapsed, and exchange-share signals without printing raw profile
  counters or changing data-movement promotion rules.
- Added an Impala source-compatibility section to the offline coverage-gap
  audit. It reports safe observed capability buckets for distribution family,
  major version, profile response format, JSON/profile-docs probes, admission
  context, profile counter registry, primary profile routing, and resource trace
  availability without printing endpoint URLs, hostnames, raw profile payloads,
  or full build strings.
- Added runtime-filter calibration signals to the offline Impala coverage-gap
  audit. The script now reports safe aggregate context such as producer/consumer
  mapping, target-scan mapping, routing/final table presence, arrival gaps,
  Bloom counter presence, and exec-node completeness without promoting
  runtime-filter findings or exposing raw filter/profile details.
- Refined scan-skew evidence selection to prefer per-instance evidence with
  known runtime timing over mapped group summaries with unknown timing. This
  removes false `timing_unknown` limitations while preserving the existing
  long-running, imbalanced, corroborated primary gates.
- Refined analyzed `unknown` primary-bottleneck reasons. The classifier still
  keeps these cases at `unknown` / low confidence, but now records safe
  supporting-only reasons such as codegen findings outside primary routing,
  medium scan-skew support, context-only data movement, view-only storage
  context, and wall clock not explained by mapped operator time.
- Added an offline retry-aggregate summary builder for Recent scan calibration.
  The script rebuilds a synthetic `batch_summary.json` from successful original
  and retry case directories, reuses normal scoring/ranking, and materializes
  bounded case artifacts under the output root so existing Details and
  optimizer audits can run on recovered cases.
- Tightened optimizer recipe-adjacent ranking. CTE or nested-query adjacent
  shapes with unproven body-validation boundaries now stay in structural
  review instead of actionable recipe backlog, so Recent prioritization does
  not overstate unsupported SQL rewrite readiness.
- Tightened the offline Recent Details audit for zero-score follow-up cases.
  Clean rows with Medium/High query-shape or stats candidates are now accepted
  only when Details renders them as explicit follow-up recommendations, while
  clean rows without such candidates still must avoid action cards and
  unsupported problem verdicts.
- Narrowed the offline Recent Details optimizer-availability observation to
  query-shape recommendation cards. Runtime, mixed-signal, skew, storage, and
  client-fetch follow-up cards no longer count as optimizer availability
  issues when source SQL is unavailable or the optimizer is not applicable.
- Promoted zero-score Recent cases with a medium/high-confidence primary
  bottleneck to suspicious severity, so runtime, client-fetch, storage, skew,
  stats, or other supported primary signals cannot disappear behind a clean
  verdict. Low-confidence SQL-shape primary labels remain cautious follow-up
  context unless separate scoring or candidate evidence supports escalation.
- Hardened exchange/data-movement and storage/HDFS evidence gates. Network and
  disk runtime context, stats metadata competition, and primary bottleneck
  routing now require mapped exchange or scan/storage profile context plus the
  relevant large byte counter before those profile findings can promote beyond
  context-only evidence. Data movement now has a structured analyzer-owned
  evidence fact with support/primary flags, mapped `EXCHANGE` timing, and safe
  limitations. `runtime_data_movement` primary routing also requires material
  mapped `EXCHANGE` elapsed time, so large sent bytes with only tiny exchange
  time remain a finding/follow-up signal instead of the primary bottleneck.
  Details now renders those analyzer-owned data movement facts as a collapsed
  raw-free evidence section.
- Hardened backend/host-tail analyzer facts to use stable safe host aliases in
  rendered evidence, normalized candidate tables, and finding evidence lines
  instead of raw profile host values. Report prompt facts now also redact
  legacy/raw infrastructure identifiers before LLM wording.
- Hardened Scan Skew Evidence promotion so runtime-skew primary routing and
  related stats/report/scoring paths require per-instance scan bytes,
  bytes-read, rows, or a mapped equivalent spread. Strong scan-skew evidence
  now also requires direct scan/bytes spread or multiple corroborating spread
  metrics; row-only spread remains medium supporting evidence even when the
  phase is long-running and imbalanced. Backend data-skew summaries without
  mapped spread fields stay context-only for promotion.
- Added the first bundled Impala profile counter stability registry. Current
  client-fetch and spill/scratch evidence paths now check counter stability
  before allowing strong profile-derived findings; `UNKNOWN`, `UNSTABLE`, and
  `DEBUG` counters cannot independently promote root-cause or
  primary-bottleneck evidence.
- Added limited classic JSON profile ingestion for allowlisted mapped counters.
  Structured JSON profile payloads can now feed existing client-fetch and
  spill/scratch facts through a safe normalized counter view, while classic JSON
  still cannot promote primary bottleneck routing until fuller mapped-section
  coverage exists.
- Added opt-in direct Impala JSON profile probing. Text endpoints remain the
  default and fallback path, so older Impala and Cloudera deployments without
  JSON profile export support keep the existing collection behavior.
- Added safe profile source capability summaries to analyzer facts for direct
  Impala collection, including selected endpoint format, probe status, and
  observed text/JSON payload status without exposing endpoints or hosts.
- Added opt-in direct Impala `/profile_docs` probing for counter stability.
  Collection writes only a safe allowlisted registry context for interpreted
  counter families; missing old endpoints are non-fatal and leave the bundled
  registry path in use.
- Added `/profile_docs` HTML table fallback for direct Impala counter
  stability collection. The collector now accepts both JSON-style labels and
  Web UI table labels such as `STABLE & HIGH` / `STABLE & LOW` while still
  storing only allowlisted counter names and normalized stability labels.
- Added opt-in direct Impala `/admission?json` aggregate context collection.
  Collection writes only bounded safe pool summaries, treats missing old
  endpoints as non-fatal, and keeps admission context below root-cause
  promotion unless selected-query admission facts support it.
- Added storage-aware scan context for Impala metadata-backed cases. Analyzer
  facts now keep only safe storage scheme/family summaries, distinguish HDFS
  from object-store semantics when metadata is available, and prevent
  object-store remote-read context from promoting HDFS/DataNode locality
  findings.
- Refined storage context for metadata-backed views. `SHOW CREATE` parsing now
  accepts Impala shell pipe-table output, view-only metadata is reported as
  `table_metadata_view_only` without inventing a physical storage family, and
  the coverage-gap audit prints a safe storage-unknown reason breakdown.
- Added context-only Runtime Filter Evidence facts. Analyzer output now records
  safe aggregate counts for classic text profile runtime-filter plan arrows,
  filter IDs, producer/consumer pairing coverage, target-scan mapping coverage,
  arrival gaps/waits, and BloomFilterBytes counter presence without exposing raw
  filter IDs, raw filter columns, raw target names, or promoting missing/late
  filters as a cause.
- Extended runtime-filter target-scan mapping to recognize scan operators under
  classic text plan tree branch markers such as union children. This keeps Kudu
  and HDFS consumer target families as safe aggregate context instead of
  misclassifying those consumers as non-scan targets.
- Added context-only runtime-filter routing/final table aggregates. Classic
  text profiles can now contribute safe counts for routing rows, final rows,
  enabled filters, partition filters, pending filters, observed arrival and
  completion values, and target-type families. Empty zero-filter tables remain
  `not_observed`; these facts do not promote missing/late filter findings or
  primary bottlenecks.
- Refined Scan Skew Evidence quality. Scan-spread findings now include group
  host count, corroborating metric count, phase runtime, and Max/Avg execution
  ratio; primary `runtime_skew` routing requires a long-running imbalanced
  phase when timing is available.
- Hardened stats metadata UNKNOWN normalization. Metadata parser output,
  analyzer rendering, report metadata digests, and web Details metadata views
  now normalize Impala stats placeholders such as `-1`, `NULL`, and `N/A`
  before browser/report surfaces.

## 0.3.0 final candidate updates - 2026-05-23

### Product

- Relicensed the public project from AGPL-3.0-or-later to Apache-2.0 and
  removed the separate commercial licensing path from public docs.
- Recent scan optimizer triage now treats near-threshold query-shape evidence
  as review guidance instead of "not candidate", includes those rows in Rewrite
  opportunities, and reserves "not applicable" wording for cases without
  supported query-shape optimizer evidence.
- Failed Recent Details cases now carry a browser-safe processing failure
  reason when the batch workflow can classify one, show that reason in the
  processing-failure follow-up, and present report/optimizer actions as
  explicitly unavailable until deterministic processing succeeds.
- Repositioned public docs and in-product help around Query Doctor as a
  local-first Big Data query diagnostic tool focused today on Apache Impala
  production triage, with Recent Scan as the flagship workflow, Query ID
  diagnosis as secondary, Query Optimizer as a separate read-only workflow, and
  IMPALA-14953 tracked as both an upstream alignment direction and a signal to
  prepare real cross-engine diagnostic contracts.
- Clarified the second-engine roadmap by separating early fixture-only
  exploration from public support claims: engine discovery can start when it
  shapes the real fact contract, while supported engine status still requires
  collection contracts, parser/fact fixtures, metadata allowlists,
  browser/report safety tests, and a support gap matrix.
- Added a fixture-only Trino discovery spike plan to start validating the
  future engine fact contract without live Trino collection, SQL execution,
  browser/report output, or public second-engine support claims.
- Added profile dialect and evidence-tier planning for Impala classic text,
  classic JSON, classic Thrift, experimental profile-v2, and unknown profiles,
  with fail-closed rules before profile-derived bottleneck claims can be
  promoted.
- Added an upstream watch loop and Trino diagnostic contract to keep future
  engine research tied to primary sources, evidence tiers, safety boundaries,
  connector limitations, and support gates instead of turning research notes
  directly into product claims.
- Expanded the upstream watch loop beyond engines to storage/table-format
  metadata, observability standards, planner architecture, comparative
  profiling UX, execution backends, AI governance, and safe production
  diagnostic-gap logging.

### Engineering

- The Impala coverage-gap audit now prints a compact optional-source
  availability summary for JSON profile, `/profile_docs`, `/admission?json`,
  metadata, runtime metrics, cluster events, and resource trace inputs. The
  summary uses only allowlisted states such as `available`, `unavailable`,
  `not_configured`, `not_collected`, and `unknown`.
- The raw-free optimizer funnel audit now reports no-recipe workload
  concentration: known versus unknown workload fingerprints, repeated versus
  singleton no-recipe groups, top-group share, and repeated-group review tracks
  and shape families. This makes repeated no-draft workload families visible
  before adding recipes or changing guidance.
- Added a raw-free offline optimizer funnel audit for existing
  `batch_summary.json` files. It can recompute rewrite-support classification
  with current code and group no-recipe cases by rewriteability bucket,
  structural shape family, safe SQL feature counts, and workload fingerprint
  without printing source SQL.
- Added a next-agent profile-evidence handoff that records the completed
  Impala dialect, exec-node-completeness, and client-fetch-tail analyzer
  slices, with runtime/admission hardening tracked as the next P0 slice at
  that baseline.
- Added Runtime Admission Evidence facts for selected-query admission result,
  admission wait, and profile/query-timeline admission phases. Primary
  `runtime_admission` routing now consumes that tiered fact, preserves
  materially conflicting wait sources as context-only, and keeps pool,
  cluster, metric, event, and duration-only signals out of primary promotion.
- Added Memory Pressure Evidence facts so selected-query non-zero spill/scratch
  counters are the current strong memory-pressure evidence path, while memory
  estimates, reservations, peak-memory footprints, daemon metrics, and runtime
  context stay context-only unless a strong query-specific memory fact is
  present.
- Added Scan Skew Evidence facts so runtime-skew primary routing and related
  stats/report/scoring paths require per-instance scan bytes, bytes-read, rows,
  or a mapped equivalent spread. Backend data-skew summaries without those
  mapped spread fields now stay context-only for promotion.
- Added Client Fetch Tail facts for Impala profiles: mapped
  `ClientFetchWait*` counters now produce raw-free evidence tiers, a
  fetch-tail finding only when the wait dominates selected-query duration, and
  a conservative `client_fetch_tail` primary bottleneck label only when no
  stronger backend/runtime finding outranks it; Query Timeline fetch and
  `GetInFlightProfileTimeStats` remain context-only.
- Added the first bundled Impala profile counter stability registry. Current
  client-fetch and spill/scratch evidence paths now check counter stability
  before allowing strong profile-derived findings; `UNKNOWN`, `UNSTABLE`, and
  `DEBUG` counters cannot independently promote root-cause or
  primary-bottleneck evidence.
- Added limited classic JSON profile ingestion for allowlisted mapped counters.
  Structured JSON profile payloads can now feed existing client-fetch and
  spill/scratch facts through a safe normalized counter view, while classic JSON
  still cannot promote primary bottleneck routing until fuller mapped-section
  coverage exists.
- Added opt-in direct Impala JSON profile probing. Text endpoints remain the
  default and fallback path, so older Impala and Cloudera deployments without
  JSON profile export support keep the existing collection behavior.
- Added opt-in direct Impala `/profile_docs` probing for counter stability.
  Collection writes only a safe allowlisted registry context for interpreted
  counter families; missing old endpoints are non-fatal and leave the bundled
  registry path in use.
- Trimmed `AGENTS.md` to keep it as a shorter hard-rules entry point and leave
  detailed operational commands in `docs/agent-quickstart.md`.
- Tightened contributor PR guidance with a branch-based contribution flow,
  documentation-drift checks, changed-worktree public-safety scan, focused
  preflight validation, synthetic fixture expectations, and no direct pushes to
  `main`.
- Added `scripts/worktree_status.py`, a read-only parallel-agent worktree audit
  helper that reports each worktree's branch, dirty state, divergence from
  `main`, merged status, and conservative merge/cleanup recommendation.
- Added the Impala incomplete/cancelled exec-node guardrail: mapped
  profile-wide and per-node lifecycle signals now emit raw-free Exec Node
  Completeness facts, limit affected row/cardinality conclusions, block
  stats-primary promotion from unsafe row evidence, and warn that zero rows on
  affected nodes do not prove empty tables, selectivity, or runtime-filter
  effectiveness.
- Added a changed-worktree public-safety scan that checks staged, unstaged, and
  untracked non-ignored files for private paths, secret-like tokens, generated
  artifacts, and unsafe public markers before broad handoff or release cleanup.
- Clarified parallel-agent worktree hygiene: agents should inspect existing
  worktrees before creating or cleaning task branches, avoid reusing another
  active worktree, and state dependencies on unmerged branches.
- Added explicit merge/push guardrails for agents: check branch divergence
  before merging into `main`, refresh task branches when `main` has advanced
  instead of relying on `--ff-only`, and never push directly to remote `main`.
- Strengthened agent instructions so every code change includes a documentation
  drift check, with affected docs updated in the same slice or explicitly
  recorded as still accurate.
- Added the first Impala profile-format analyzer slice: deterministic profile
  dialect detection for classic text, classic JSON, classic Thrift,
  experimental profile-v2, and unknown profiles; structured
  `analysis_support` and primary-bottleneck policy facts; and fail-closed
  primary-bottleneck routing for unknown or unmapped profile-derived evidence.
- Recent batch and single-case pipeline metadata workflows now run a raw-free
  Kerberos ticket preflight before real Impala metadata collection, so expired
  or missing tickets fail early instead of turning broad scans into mass
  metadata failures.
- Reduced agent-documentation duplication by making `agent-quickstart.md` the
  canonical universal preflight/worktree path, keeping `agent-playbook.md`
  focused on change-type routing, and trimming the docs index Start Here list
  to required entry points.
- Added the first engine-fact contract slice with typed lifecycle, timing,
  resource, stage, and limitation facts, plus a synthetic Trino
  statement-statistics fixture mapper that stays outside live collection,
  browser/report output, and supported engine registration.
- Added an Impala analyzer-to-engine-fact projection and support-gap matrix so
  the current implemented engine and the fixture-only Trino shape can be tested
  against the same raw-free contract without changing product workflows.
- Added a golden engine-fact contract harness that compares the Impala
  projection and fixture-only Trino bundle by public shape, diagnostic states,
  required fact states, and raw-free output without treating Trino as supported
  product behavior.
- Expanded the golden engine-fact harness with Impala projection cases for
  clean finished queries, admission queueing, explicit spill/scratch evidence,
  missing profile sections, and failed-query lifecycle semantics.
- Added a normalized engine-fact report/browser boundary payload helper and
  harness tests that fail closed on unsafe fact text and keep internal parser
  source labels out of future trusted surfaces, without wiring the payload into
  current UI or report generation.
- Added a read-only normalized engine-fact consumer probe that derives
  state-count coverage and state-backed attention signal IDs from boundary
  payloads for Impala golden cases and the fixture-only Trino case, without
  wiring normalized facts into current ranking, browser, or report behavior.
- Expanded fixture-only Trino engine-fact coverage with a second synthetic
  failed-statement statistics fixture, covering failed lifecycle semantics,
  unknown output rows, and raw-free boundary/consumer probes without adding
  live collection or Trino product support.
- Added a tested minimum raw-free Trino intake contract that pins fixture-only
  supported / not observed / unknown fact states, minimal boundary identity,
  forbidden surface classes, and non-support wording before any real Trino
  source or product surface can consume Trino-derived facts.
- Added a future-only Trino live-collection design covering source families,
  auth and bounds, redaction, fixture gates, and explicit bans on using
  `/v1/statement` or Query Doctor-generated `EXPLAIN ANALYZE` as collectors.
- Added a fixture-only Trino completed-event contract slice that maps a
  synthetic compacted event-listener payload into raw-free normalized facts,
  including resource-group queue timing and spill evidence, without adding a
  live event-store reader, engine adapter, UI output, or support claim.
- Added fail-closed guards for the fixture-only Trino event mapper so oversized
  payloads, unsafe raw event field names, and unsafe raw text values are
  rejected before normalized facts are built.
- Expanded fixture-only Trino completed-event coverage with a missing-field
  event fixture that omits source-version and optional detail fields, proving
  absent lifecycle, timing, resource, stage, and blocked/failure signals remain
  `unknown` without fake zero values or support claims.
- Expanded fixture-only Trino statement-statistics coverage with a blocked
  query fixture, proving `BLOCKED` lifecycle and `fullyBlocked` signals stay
  state-backed and raw-free without registering Trino as a supported engine.
- Added a fixture-only Trino stage-skew statement-statistics case that maps a
  safe aggregate per-task distribution summary to a supported
  `stage_skew_candidate` fact, without exposing stage IDs, task IDs, workers,
  SQL, connector details, live collection, or product support.
- Added fixture-only Trino connector-metric present/absent
  statement-statistics cases that map only a compact checked/present summary to
  `connector_metric_signal`, without exposing connector names, metric names,
  object names, raw connector payloads, live collection, or product support.
- Added a fixture-only Trino failed-query category case that maps only a
  compact checked/category summary into a redacted safe failure category,
  without exposing raw exception classes, messages, stack traces, live
  collection, or product support.
- Added Impala profile counter caveat guidance covering admission, memory,
  scan-skew, exchange-wait, disk-I/O, and client-fetch-tail promotion rules.
- Expanded the Impala JIRA-derived profile roadmap with incomplete/cancelled
  exec-node guardrails, Kudu runtime-filter limitations, exchange-skew and
  mixed cache/remote I/O caveats, profile-serialization context, and
  planner-mode awareness as evidence-tiered backlog items.
- Tightened the offline Recent Details smoke audit to fail failed cases whose
  report or optimizer state still looks runnable/hidden instead of explicitly
  unavailable, and added fallback review anchors for action cards that lack
  precise source locators.
- Added an offline Recent Details smoke-audit script that renders existing
  `batch_summary.json` cases through the production Details presenters and
  checks problem explanations, report/optimizer action gating, baseline sample
  overlap, and browser-visible safety before broad release handoff.

## 0.3.0 release candidate baseline - 2026-05-22

### Product

- Release-candidate polish tightened the Details analyst path for failed,
  clean, and unknown-bottleneck rows: failed rows now get a processing-failure
  follow-up, clean rows avoid unsupported rewrite/runtime verdicts, unknown
  suspicious rows lead with supported analyzer signals, and report/optimizer
  actions fail closed when the selected case is not server-owned or lacks
  completed deterministic analysis facts.
- Recommended-change cards now consistently follow the decision path
  "why this deserves attention, where to inspect, what supported direction to
  try, and how to verify", with a safe fallback review anchor when precise
  source locators are unavailable.
- Recent scan Results now renders the primary results table as mobile row
  cards with visible field labels, avoiding horizontal clipping on narrow
  screens while keeping the desktop table layout.
- Russian language mode received release polish across Help, Details, and
  details-page optimizer outcomes, reducing mixed English/Russian static labels
  while preserving English as the canonical public documentation language.
- Recent scan Details now hides selected-case optimizer actions for clean rows,
  and the matching optimized-query route refuses to start a job for clean or
  failed selected cases.
- Recent scan Details now shows a deterministic diagnostic follow-up card for
  high/suspicious cases that have analyzer score signals but no Medium/High
  rewrite, stats, or admission action candidate, so attention-worthy rows still
  give a safe review direction and verification step.
- Recent scan Details now derives safe score-reason explanations from
  deterministic status, runtime, bottleneck, signal-summary, and score fields
  when older case summaries lack explicit score-reason bullets.

- Diagnose UI simplification checkpoint: the main screen now starts from one
  workflow selector, keeps secondary scan controls collapsed, collapses the
  launch form after results are available, keeps the result summary focused on
  triage counts, hides zero-count secondary groups, and improves the workflow
  selector plus result-table status readability. The remaining goal is to make
  the default results table answer only which queries need attention, with
  deeper "why" evidence in Details or explicit row/context disclosures.
- Web UI audit follow-up tightened the desktop analyst path across Diagnose,
  Recent results, and Details: Source cluster stays visible, Minimum duration
  returned to Basic scan, Advanced settings are config-enabled instead of
  always visible, long helper copy moved into nearby help controls, dropdown
  and disclosure chevrons gained separated affordances, Details verdicts lost
  duplicated instruction text, and Results status chips plus duration cells are
  easier to scan.
- Added a global `language` config field (`en` or `ru`) that controls Help,
  Details static UI copy, and newly generated trusted reports. The web header
  now shows the active language mode on every page and a safe tooltip pointing
  to the config key without exposing the absolute config path.
- Details product guidance now records the durable analyst-first contract:
  explain why the query deserves attention, where to inspect it, what supported
  change direction to try, and how to verify the change before exposing
  collector-source diagnostics.
- Diagnose now uses one workflow selector for `Finished queries`, `Running
  now`, and `One Query ID`; secondary scan presets moved under `More scan
  options`, and completed-result pages collapse the launch form into `New
  scan`.
- Recent results now keep the summary strip focused on scanned volume plus
  `Needs attention` and `Worth reviewing`; rewrite, stats, workload, and other
  secondary overlays stay under `More groups` and zero-count secondary groups
  are hidden unless active.
- Recent results now keep only the main triage groups, `Needs attention` and
  `Worth reviewing`, visible by default; secondary workload, rewrite, and stats
  overlays remain available under `More groups`.
- Recent results now keep the `More groups` control in the main result filter
  toolbar when collapsed, so secondary overlays stay available without pushing
  the results table down.
- Recent results now include a `Repeated workloads` result group that surfaces
  current-scan workload fingerprint repeats by group impact, so short repeated
  query shapes can be reviewed without exposing raw SQL.
- Recent results now include a `Frequent short` result group and a matching
  finished-query scan preset that removes the minimum-duration default through
  existing bounded scan flags, then ranks repeated short workload fingerprints
  by current-scan impact.
- The Recent scan Basic scan form now places `Scan preset` below the date/hour
  and duration filters next to `Run scan`, keeping the primary filters grouped.
- The `Frequent short` result group now shows raw-free limitations for
  current-scan scope, incomplete workload fingerprints, and missing
  runtime/admission metrics.
- Workload detail pages now include a raw-free triage block with Frequent short
  fit, current-scan impact, top pool/owner, primary-signal mix, and limitations.
- Workload digest now includes an admin-facing pool/owner aggregate for repeated
  workload groups, runs, current-scan impact, top fingerprint, and compact
  signal counts.
- Workload digest now separates repeated workload groups with failed or
  cancelled member status from low-value noise and includes status issue counts
  in admin pool/owner aggregates.
- Workload admin digest now supports raw-free scope and signal filters so
  admins can narrow pool/owner aggregates to regressions, admission/runtime,
  stats, spill, status issue, or low-value repeated workload signals.
- Workload admin digest rows now link to a raw-free focused workload groups
  table for the selected pool or owner, preserving signal filters when active.
- Workload digest now includes a raw-free analyst action queue that selects
  repeated workload groups, next checks, and verification hints from safe group
  and member facts.
- Workload action queue rows now pair each signal with its supporting evidence
  and each next check with the verification target, keeping follow-up work
  easier to scan without adding raw query details.
- Workload action queue entries now carry raw-free review anchors and
  verification metrics per signal type, so analyst follow-up points to the safe
  Details or workload evidence to inspect and the metric to compare after one
  change.
- Repeated workload groups now link to safe workload detail pages with current
  aggregate context, representative cases, and member case links from the
  server-owned scan summary.
- Recent results now surface opt-in workload history status and a `Regressed
  workloads` result group for repeated fingerprints with local baseline
  slowdowns, without exposing the local history path.
- Workload detail pages now include deterministic action hints for baseline
  slowdowns, admission/runtime, stats, query-shape, spill, and status follow-up
  signals from safe group/member facts.
- Recent scan Details action cards now follow that analyst-first order:
  why the query deserves attention, where to look, what to change, and how to
  verify. Candidate score/rank details are collapsed below the recommendation.
- Recent scan Details recommended-change cards now lead with the visible change
  direction and verification step, keep safe review anchors visible, and move
  the longer reason text behind a compact disclosure.
- Details LLM actions now collapse into a compact unavailable-status row when
  neither report nor optimizer action can run for the selected case, while
  keeping actionable buttons and generated results visible when available.
- Running Queries now keeps live-scan guidance inside field help instead of a
  permanent subtitle under the page title.
- Diagnose now keeps the Running now form compact after scan-target switches,
  shortens result-table helper text, and hides the Action outcomes note until
  feedback has been recorded.
- Diagnose mode guidance now lives in the `What to analyze` help popover
  instead of a standalone line under the page title.
- Details action-card wording now turns query-shape and stats candidates into
  analyst-readable explanations: what deterministic analysis found, why the
  named SQL/plan or metadata location matters, and what bounded change to try
  without presenting the candidate as a proven root cause.
- Recent results now include a raw-free workload digest for top regressions,
  admission/runtime groups, stats-gap groups, and spill-heavy repeated
  fingerprints.
- Workload digest now has shortcut links to regressed workloads, repeated
  workloads, workload groups, and each fingerprint detail page.
- Workload digest rows now show compact pool and top-owner aggregates from
  safe Recent scan presenter values.
- Workload digest now marks low-value repeated fingerprints when group/member
  facts show no regression, high-priority signals, spill, or stats/runtime
  follow-up hints.
- Details verdict titles now translate primary bottleneck classifications into
  analyst-readable review signals such as query-shape rewrite review, stats
  gaps, runtime queueing, skew, data movement, or storage follow-up instead of
  leading with internal labels and confidence syntax.
- Workload digest and detail pages now show raw-free action-outcome rollups per
  workload fingerprint, limited to recorded/applied counts, allowed outcome
  labels, and whitelisted recommendation labels.
- Recent scan Details now puts analyst-facing recommended changes above
  pipeline internals, with action cards showing safe review locations, change
  direction, and verification before lower-level evidence blocks.
- Recent scan Details Analysis summary is now a compact decision strip covering
  the supported problem signal, next action, structural review anchor, and
  evidence level instead of listing every collected context source.
- Recent scan Details Pipeline status is now collapsed by default so collection
  and report-state internals remain available without pushing the analyst
  action guidance down the page.
- Recent scan Details action cards now lead with safe review locations, change
  direction, and verification; score/rank metadata is still shown but moved
  below the actionable guidance.
- Recent scan Details now labels the lower diagnostic section as Supporting
  findings, making Recommended changes the primary analyst workflow while
  keeping deterministic evidence visible.
- Details now combines Pipeline status, Supporting findings, and Evidence
  details under one collapsed Diagnostics and evidence block, and removes the
  Details jump list so the page starts directly with the case overview.
- Recent scan Details now merges the old overview and analysis summary into one
  verdict block with four analyst-facing KPIs, leaves next-step guidance only in
  Recommended changes, labels Query Doctor processing timings separately from
  query runtime, and folds action outcome controls behind a compact Mark result
  disclosure.
- Expanded Details diagnostics now uses flat sibling sections for Pipeline,
  Runtime, Metrics, Metadata, and Score evidence, while keeping the full
  Diagnostics and evidence block collapsed by default and removing the nested
  All collected runtime metrics disclosure.
- Details now surfaces safe analyzer-owned query context when available,
  including query window, query type, pool, admission wait, and compact resource
  footprint, while continuing to hide raw metric-window timestamps and provider
  payloads.
- Browser metadata readers now prefer canonical `query_metadata.json` while
  preserving legacy `cm_metadata.json` fallback, and report evidence labels the
  source generically as query metadata.
- Cloudera Manager runtime context collection now writes canonical
  `runtime_metrics_context.json` alongside legacy `cm_timeseries_context.json`,
  and Recent refresh keeps both files while analyzers prefer the canonical
  layout.
- Browser-display redaction, demo preflight, and staged public-safety checks now
  share the current generated-artifact filename denylist, including canonical
  runtime and cluster context artifacts.
- The web language indicator now avoids rendering ignored dotfile local config
  filenames while still pointing users to the `language` config field.
- Details verdict facts now flow through a typed, question-oriented presenter
  model with relevance, severity, interpretation, and safe source anchors, so
  the top verdict can be ranked by diagnostic question rather than by collection
  source.
- Details Recommended changes now show compact supporting facts from the same
  typed diagnostic fact model, linking each suggested action to the safe
  evidence that justifies it.
- Details Diagnostics now starts with a question-oriented evidence layer
  grouped by what looks wrong, when/how much work ran, normality, queue/cluster
  context, and where to act, while preserving the existing Pipeline, Runtime,
  Metrics, Metadata, and Score evidence sections below.
- Details verdicts now fall back to the strongest visible action candidate when
  primary bottleneck classification is unavailable, so analyst-facing pages do
  not lead with `Not classified` when query-shape or stats guidance exists.
- Details action results now keep report and optimizer bodies collapsed by
  default after generation and use section-level headings, reducing page weight
  while preserving the same validated outputs and explicit action buttons.
- Details action controls now show unavailable report or optimizer actions as
  compact status rows instead of disabled action cards, and Results table keys
  read as quiet reference text rather than button-like chips.
- Details priority labels now call out clean-score rows with Medium/High
  action candidates as follow-up candidates instead of showing them as clean
  verdicts.
- Details verdict blocks now split long verdict summaries into a compact
  headline plus supporting signal, and show Query ID, priority, duration, and
  confidence in one inline meta strip instead of large KPI cards.
- The web UI visual system now uses larger readable defaults, keeps monospace
  typography mostly to code/query identifiers, improves muted-text contrast,
  normalizes visible UI font weights, enlarges common controls, and uses a
  not-allowed cursor for disabled actions.
- Results and scan setup now use more analyst-facing wording: result groups are
  task-oriented, Score/STATS/META shorthand is replaced with visible Priority,
  Table stats, and Metadata labels, a table legend explains status badges, the
  fixed Recent scan timezone is visible in the Scan Hour label, and result rows
  open Details in the current tab.
- Recent scan setup now defaults to a Basic scan layer with the Finished queries
  / Running now target selector, the finished query date/hour inputs, and the
  Run action, while source selection, filters, and collection limits sit behind
  collapsed Source and Advanced settings. Owner-gated source visibility still
  keeps the required username visible in Basic scan.
- Diagnose now uses mode-specific start guidance for Recent queries versus One
  Query ID, moves Run scan closer to the scan inputs, and gives dropdown
  chevrons a separated right-side affordance. When the form is switched to
  Running now while old finished-query results are still visible, that result
  block is labeled as previous finished-query output.
- Diagnose now collapses visible recent-scan results behind a previous-results
  disclosure when switching to One Query ID, keeps the mobile header compact,
  shows mobile result metrics as a single horizontal strip, and moves the
  results table legend after the table so the first rows are reached sooner.
- Diagnose now keeps Source cluster visible as a top-level choice, returns
  Minimum duration to Basic scan, hides Advanced settings unless local config
  enables editable advanced filters, and leaves Resource pool plus collection
  worker counts to bounded defaults or local `query-doctor-config.json` keys
  instead of default browser controls.
- The separate Running Queries page now follows the same desktop scan-form
  rules: source and minimum duration stay visible, optional user/pool filters
  are shown only through config-enabled Advanced settings, and worker counts
  remain config-owned. One Query ID mode now uses a short helper sentence
  instead of a full scope card.
- Diagnose now moves long start/source/one-query explanations into nearby
  `i` help controls, leaving the main form focused on source, mode, scan
  target, filters, and the run action.
- Known Query ID analysis jobs now show the same server-owned progress step
  block as other web jobs, so users can see Query ID validation, profile
  collection, deterministic analysis, result preparation, and completion while
  the job is running.
- Completed Known Query ID analysis jobs now mark the final `Done` progress
  step as complete instead of leaving it in the active state.
- Recent scan progress no longer shows summed per-case worker time as step
  elapsed time while parallel collection, analysis, runtime metrics, or
  metadata refresh stages are still running.
- Recent scan date/hour selection now reads the scan timezone from local config
  through `recent_scan_timezone` and shows the current UTC offset in the Scan
  Hour label, such as `UTC+2`, instead of the IANA timezone name. The canonical
  example config now includes the field.
- The standalone Query Optimizer page now collapses repeated scope strips into
  one secondary scope/safety disclosure and moves accepted-input rules into the
  SQL field help, keeping the pasted-SQL task lighter while preserving the
  trust boundary.
- Help now opens as a compact task-oriented page with shortcut cards, a short
  quick-start sequence, and collapsed workflow/safety/reference topics instead
  of one long manual-style document.
- Secondary desktop pages now use lighter task flow: Running Queries hides
  live-snapshot caveats inside field help, Query Optimizer separates Analyze
  from the secondary scope disclosure, and Action outcomes shows a compact
  empty state before feedback exists.
- Details pages now render case-level sections flatter inside the main case
  panel, reducing nested-card chrome around the verdict, Recommended changes,
  Diagnostics, and action controls while leaving repeated recommendation and
  evidence items scannable.
- Details action controls now use the product label `Reports and optimizer`
  instead of mode-specific implementation labels for the section heading, while
  individual buttons still identify LLM or Python behavior.
- Details report and optimizer action cards now include short purpose text, so
  analysts can choose report, optimizer, or combined execution without knowing
  implementation-specific action names first.
- Running Queries now places Source cluster above the Live scan controls,
  matching the Diagnose source-first flow while keeping the same submitted
  cluster field.
- Recent results now collapse secondary Results notes by default while keeping
  scan warnings and scan-stopped empty states open, so routine rewrite/outcome
  guidance no longer competes with the first table rows.
- Recent results now keep the pre-table path focused on result counts, group
  filters, critical warnings, and rows; table-key, scan-detail, notes, and
  workload context live in a collapsed Result context section after the table.
- Details now removes duplicated top-level facts from the analyst path: the
  verdict title owns the main problem statement, verdict chips show only
  triage context, action cards keep review anchors in `Where to look`, and
  optimizer guardrails move behind a collapsed technical disclosure.
- Recent results now put the analyst-facing Finding column immediately after
  rank, keeping Query ID, user, priority, and metadata/status columns as
  supporting context.
- Recent results now show a compact top summary strip for scanned volume and
  action groups, with secondary scan and rewrite-funnel counters collapsed
  under Scan details.
- Recent results now combine rewrite guidance, recorded action outcomes,
  empty-state notes, and scan warnings into one compact Results notes block.
- The local web wrapper now defaults its Kerberos cache to the generic
  `FILE:/tmp/krb5cc_query_doctor` path, matching the config template and
  avoiding account-specific cache names.
- Automatic config discovery now prefers `~/.qdcreds/query-doctor-config.json`
  ahead of repository-local defaults when there is no current-directory config.
  Local web owner gating can derive the owner from a single simple keytab
  principal, with multiple keytab users shown as Username choices instead of a
  fixed `source_owner_user`.
- The config template now leaves CM auth username in `cm-ro.env` by default,
  while keeping the JSON `username` field supported as a non-secret fallback.
- The config template now omits built-in metadata timeout/table/output limits;
  local JSON configs only need these fields when an environment needs custom
  metadata bounds.
- The config template is now a compact starter config: cluster-specific
  Cloudera Manager and direct Impala settings live inside `clusters[]`, direct
  Impala examples no longer inherit top-level CM settings, and default protocol,
  redaction, recent-scan, and Ollama base URL values are omitted.
- Report generation and Query LLM optimizer routes now have separate non-secret
  LLM config fields for provider, model, base URL, and optional chat path. LLM
  API tokens stay in the local `~/.qdcreds/llm-api.env` env file instead of
  JSON config.
- `cm-ro.env` loading now accepts only CM authentication keys, keeping Kerberos
  principals and owner-visibility controls in shell/keytab/JSON config layers.
  The local web wrapper also no longer exports a keytab-inferred principal as a
  fixed owner. User-facing JSON config can use `cluster_type` as the preferred
  alias for the internal `query_profile_source` setting.
- Local config now accepts `source_visibility` and `source_owner_user` for the
  first owner-gated source-visibility slice. `source_visibility=owner_raw` does
  not expose raw browser or trusted-report fields; it fail-closes Cloudera
  Manager and direct Impala Recent/Running scans to a verified owner user before
  any future selected-case source view is considered.
- Recent scan Details action candidates now include raw-free review locations
  for relevant SQL structure, plan operators, metadata stats, or runtime
  admission context, so guidance can point to a concrete safe anchor without
  displaying raw SQL or profile text.
- In owner-gated source visibility mode, Recent scan action-candidate review
  locations can include raw-free SQL line coordinates for SELECT/WITH source
  regions while keeping the source text itself hidden from browser output.
- Recent scan Details action candidates now render structured guidance sections
  for where to look, why it matters, change direction, and verification instead
  of compressing the recommendation into one long paragraph.
- Recent scan Details change-direction guidance now derives from safe locator
  categories, so SQL filter, CTE, derived-table, UNION, plan-estimate,
  memory-pressure, data-movement, and stats-refresh anchors produce more
  specific next-step wording without exposing raw source text.
- Direct `batch_recent` runs can now select a local `clusters[]` entry with
  `--config-cluster`, allowing direct Impala source, metadata, Prometheus,
  redaction, `source_visibility`, and `source_owner_user` settings to come from
  the same cluster config used by the web UI.
- `source_visibility=owner_raw` now applies the same fail-closed owner user
  filter to Cloudera Manager Recent and Running scans instead of only direct
  Impala scans.
- The local web wrapper now exposes the resolved keytab path to the web process,
  and the web UI can populate Username dropdown choices from simple account
  names found in the keytab without rendering keytab paths or full principals.
- Keytab-derived Username choices are now sorted alphabetically, and the first
  choice is selected by default for owner-gated web scans instead of leaving the
  required Username filter empty.

### Documentation

- Pre-release documentation audit refreshed README, release readiness/checklist,
  validation log, and roadmap to match the current config-driven language
  selection, Recent Scan Hour timezone labeling, Known Query ID progress, scan
  progress elapsed wording, and implemented workload diagnostics baseline.
  Remote issue triage now separates active backlog (#47-#49, #67, #68) from
  stale or partially resolved follow-ups (#66, #69, #70).
- Release-prep documentation audit now separates these post-`v0.2.0` local
  changes into `Unreleased`, aligns the public demo runbooks with the current
  synthetic demo pack, removes old prepared-case query IDs from the main demo
  guide, and refreshes README/agent guidance for the current console scripts
  and source-provider baseline.
- Superseded archive notes, old UI prototypes, documentation-audit snapshots,
  and unused legacy demo screenshots were removed from the current documentation
  tree before release cleanup.
- README and demo/release runbooks now use the current synthetic demo pack
  launch path with a dedicated `query-doctor-*` temp output directory and
  `query-doctor-web --batch-summary`.
- Agent-facing operating docs now describe the worktree-first development
  flow, parallel release cleanup, dependency cherry-picks between unmerged
  branches, commit autonomy when the user has already granted it, and safe
  post-merge worktree cleanup. Agent preflight and release/docs gates now also
  treat the active agent docs as a checked routing area.
- Package workflows now run the package metadata contract before builds and
  publishing, and release docs separate pre-release audits from the final
  version bump, tag, TestPyPI, and PyPI publishing steps.
- Public issue, pull request, contribution, and security templates now steer
  reproductions toward synthetic data and explicitly block real-cluster
  screenshots, logs, reports, generated artifacts, and raw operational payloads.
- Release readiness docs now treat screenshot currency, final changelog notes,
  version/tag/publishing, and green release gates as final-candidate
  requirements while keeping pre-release audit cleanup separate from a package
  release.
- Public README, docs indexes, and demo companion pages now expose the
  synthetic demo, screenshot refresh, demo preflight, and final release
  readiness paths as explicit public navigation instead of burying them in the
  reference list.
- README synthetic demo screenshots were refreshed from the current synthetic
  demo pack and current material web UI baseline.
- Code-audit notes now record which compatibility facades, config aliases, CLI
  aliases, and legacy artifact read fallbacks are intentional release-era
  support surfaces rather than stale files to prune.
- Code-audit notes now map the remaining legacy artifact write aliases and the
  required canonical-reader migration order before any post-release write
  cleanup.

## 0.2.0 - 2026-05-19

### Engineering

- Recent batch summaries now attach a raw-free `workload_fingerprint`
  (`wf_<24hex>`) to each case from structured case/analyzer facts only, using a
  pure fingerprint helper with safe defaults and no UI, persistence, baseline,
  SQL, profile, metadata, or optimizer-source reads in this first slice.
- Recent scan summaries now build current-scan workload groups for repeated
  raw-free fingerprints, add safe group context to case rows and Details, and
  keep singleton/incomplete fingerprints out of the visible group panel.
- Recent scan Details action candidates now support local action-outcome
  feedback for raw-free workload fingerprints, storing only recommendation ids,
  fingerprint ids, bounded apply/outcome values, and sanitized notes in a local
  JSONL file, with a read-only `/outcomes` table for recorded feedback.
- Local action outcomes now include recommendation-level metrics on the
  `/outcomes` page and show a Details-card local-history hint only after a
  recommendation has enough applied records for a bounded local signal.
- Recent batch runs can now opt in to local workload fingerprint history and
  regression labels, comparing current in-scan p95 durations against prior
  raw-free fingerprint aggregates before appending the current batch to local
  JSONL history.
- Analyzer runtime metrics state now writes and reads provider-neutral
  `metrics_context`, `metrics_facts`, and `metrics_correlation` keys with
  legacy `cm_*` aliases, keeping current report/analyzer output stable while
  reducing the internal Cloudera Manager-specific data-key dependency. Report
  contract digests now expose matching `metrics_facts` and
  `metrics_correlation` aliases while preserving the legacy digest keys.
- Web Details runtime-metrics facts loaders now expose provider-neutral
  `runtime_metrics` loader/parser names, and Details state builders consume
  those aliases while preserving legacy `cm_metrics` exports as compatibility
  wrappers for existing tests and imports.
- Analyzer query metadata state now writes provider-neutral `query_context`
  while preserving the legacy `cm_query_context` alias, and analyzer admission
  classification, runtime diagnosis, action-card, metrics-correlation, and
  fact-rendering readers consume the canonical key with legacy fallback.
- Runtime metrics collectors now include catalog `signal_id`s alongside
  provider-specific metric ids, and analyzer metric facts can read signal-level
  runtime metrics with legacy id fallback for old collected corpora.
- Recent batch runtime-metrics refresh now uses a dedicated bounded subprocess
  timeout and records a safe timeout reason in progress JSONL when a top-case
  Cloudera Manager or Prometheus metrics refresh stalls.
- Impala metadata refresh now filters generic/redacted referenced-table
  placeholders before building collector commands, so placeholder-only SQL
  context no longer burns the bounded metadata budget or produces avoidable
  `SHOW ...` parser failures.
- Recent batch metadata refresh now fills the remaining
  `metadata_top_limit` budget with collectable cases after priority cases are
  selected, including Cloudera Manager sourced batches.
- Recent CM batch metadata refresh now uses real table references extracted
  from discovery statements before profile identifier redaction, passing them
  only through an internal bounded metadata source and keeping progress,
  summaries, reports, and pipeline plan output raw-free.
- Known Query ID metadata refresh now mirrors the Recent batch metadata-source
  bridge by passing collector-extracted table references through a temporary
  internal file and analyzer environment only, so identifier-redacted single
  query cases can still collect bounded redacted Impala metadata.
- Analyzer referenced-table extraction now ignores comma-separated identifiers
  inside nested expressions after a `FROM` table, avoiding false metadata
  collection attempts for lateral-view/function argument names.
- Source Provenance now labels absent or unknown runtime metric providers as
  generic `Runtime metrics`, while preserving explicit Cloudera Manager and
  Prometheus labels for collected metric contexts.
- `runtime_admission` primary-bottleneck classification now uses explicit
  query-specific admission waits from Cloudera Manager context, safe direct
  profile admission queue facts, or profile Query Timeline admission phases.
  It keeps immediate/trivial admission and pool-only context from becoming a
  primary cause, and adds deterministic admission follow-up action guidance.
- Optimizer deterministic no-draft outcomes now include more specific safe
  reason text and metadata for post-UNION aggregate boundaries, including
  constant-row branch context, `COUNT(*)` rollup context, downstream rollup
  boundaries, and projection/lineage boundaries, without adding a new recipe or
  relaxing validation.
- Recent batch optimizer rewrite-support scoring now safely classifies
  unsupported nested CTE bodies as outside trusted draft scope instead of
  aborting the whole batch during deterministic shape analysis.
- Query LLM optimizer CLI now accepts a Recent batch wrapper directory when it
  contains exactly one analyzed child case, so manual runs copied from
  `batch_summary.json` no longer fail before deterministic no-draft guidance is
  written.
- Web Details now uses Python-only labels for report and optimizer actions,
  progress, navigation, action anchors, action routes, and result sections when
  `--no-llm` mode is active, matching the subprocess commands that already run
  with `--no-llm`.
- Web Details optimizer result links now jump to the trusted optimizer result
  block instead of reloading the Details page, and metadata status table
  headings no longer use SQL-command wording for read-only metadata checks.
- The local web bootstrap now has a dedicated
  `scripts/query-doctor-web-local-no-llm` shortcut, and Help text switches to
  Python-only wording when the web UI is launched with `--no-llm`.
- PyPI publishing now runs only for release tags beginning with `v`, so
  asset-only GitHub releases such as demo videos cannot enter the protected
  PyPI publishing environment.
- Finished/Running Details request rendering now builds a typed
  `RecentScanCaseDetailView` before page rendering, keeping the route path on
  presenter-owned safe fields. Details report-action failure pages now use the
  same typed render path when a complete server-owned case is unavailable.
  Known Query ID Details now follows the same typed view-model path for its
  main request, report-missing, and blocked-action renders. The remaining
  legacy Details dict adapters and facade exports were removed after tests
  moved to typed view-model inputs. Details action handlers now consume typed
  action contexts for report/optimizer eligibility, running-job state, source
  SQL availability, and server-owned case failure state.
- Backend-tail parsing now stops host-instance parsing at Markdown heading and
  code-fence boundaries, preventing following global profile totals from being
  attributed to the last backend host and masking writer-path tail evidence.
- Direct Recent batch CLI runs now load workstation Cloudera Manager auth from
  `QD_CM_ENV` or `~/.qdcreds/cm-ro.env` before preflight using a whitelist-only
  env parser, matching the local web launcher without allowing secrets in JSON
  config files.

### Safety

- Report normalization now emits language-specific safe replacement text for
  context-only runtime metrics, contradicted estimate directions, primary
  bottleneck overclaims, and related evidence contradictions. The analyzer
  facts appendix also escapes redacted angle-bracket placeholders and removes
  raw artifact markers before trusted Markdown validation, so validated English
  reports are no longer rejected by Cyrillic fallback notes or safe placeholders
  such as redacted table labels and no longer expose metadata context artifact
  filenames.
- LLM report generation now replaces shape-only invalid model narratives with a
  Python-owned deterministic report body before strict validation. Factual and
  safety validation failures still fail closed with only a partial report.
- Trusted reports now reuse analyzer-owned Evidence Quality in the Python-owned
  report digest, prompt contract, deterministic fallback report, and normalized
  Supporting Evidence section so evidence coverage and limitations constrain
  report wording instead of living only in Details or the appendix.
- Trusted report loading now applies browser-display local path redaction after
  validated report marker checks, so inline report rendering and Markdown
  downloads hide sibling or stale absolute paths in addition to the current
  case directory.
- Report runtime-metric guardrails now read provider-neutral
  `Runtime Metrics Correlation` headings when blocking context-only metric
  signals from becoming causal claims or optimizer candidates.
- Query LLM optimizer recommendations now normalize matched model bullets back
  to canonical Python-owned English recommendation text and reject trusted
  recommendation artifacts containing Cyrillic text, preventing mixed-language
  recommendations from becoming browser-visible trusted output.
- Public credentials docs now describe generic OpenAI-compatible LLM API
  binding without naming organization-specific endpoints, and the public-release
  guard now blocks production-looking hostnames in addition to private TLDs and
  embedded credentials. The staged public-safety checker also blocks `.DS_Store`
  local artifacts.

### Documentation

- Added a research-only Spark architecture spike contract for Spark History
  Server/event-log fact modeling. The docs now distinguish Spark fact-model
  research from product support: no Spark collector, engine registration, UI
  path, report surface, optimizer behavior, or support claim exists.
- Added a brand voice and humor policy that allows only small dry engineering
  personality on safe outer surfaces while keeping trusted reports, diagnostic
  findings, validation, safety warnings, and root-cause wording strictly
  factual.
- Refreshed agent-facing roadmap, analyzer audit, code audit, and handoff docs
  after Details typed rendering, trusted artifact loading, progress cleanup, and
  `runtime_admission` hardening landed, then updated the product-growth queue
  again after the raw-free workload fingerprint primitive landed. The next-pull
  queue no longer points agents at already-closed work. Provider-neutral runtime
  metrics heading guards now pin the current analyzer/report contract so
  follow-up work can focus on canonical data keys and `signal_id` reads instead
  of redoing heading migration.
- Clarified the product roadmap and engine expansion plan after external
  product audit: Spark SQL is explicitly deferred, second-engine readiness now
  requires full real-workload gates, and near-term product-growth work
  prioritizes workload fingerprinting, pool/admission diagnostics, action
  outcome tracking, direct Impala depth, and stats/metadata depth. The first
  implementation sequence covered the existing `runtime_admission` bottleneck
  path and raw-free workload fingerprint primitive, then moves to in-scan
  workload groups. Analyzer audit guidance records the admission wait source
  precedence, confidence thresholds, parser guardrails, and fixture matrix for
  that slice.
- Added a root-level Russian companion README and linked it from the main
  README and documentation index.
- Added a documentation audit covering current-tree sensitive-information
  checks, git-history blockers, Russian companion coverage, and untranslated
  text priorities.
- Added Russian companion summaries for all current non-archived documentation
  pages and refreshed the highest-priority Russian safety/architecture pages.
- Sanitized demo case notes so public demo docs no longer include prepared-pack
  query IDs, account names, local deep links, or environment-specific case
  references.

## 0.1.2 - 2026-05-13

### Engineering

- Web Recent scan now passes metadata identifier/host redaction flags through
  the batch CLI without triggering `argparse` exit code 2, and browser-visible
  subprocess failures now add a safe category hint for exit code 2 while still
  hiding raw stdout/stderr. Recent scan CM Events collection also now receives
  only CM connection flags, avoiding unsupported profile redaction flags on the
  events collector.
- Recent scan candidate-limit handling now keeps bounded results useful: when
  more queries match than the analyzer profile limit, the scan selects the top
  bounded set by scan order instead of returning zero cases. The web default
  analyzer limit now matches the 5000-case hard cap and can be overridden by
  `recent_profile_analysis_limit`.
- Safety CI now launches the local web UI against the generated synthetic demo
  pack and verifies that demo result rows render through the browser-safe Recent
  scan UI, not only that the demo JSON artifact exists.
- Safety CI now includes a synthetic high-volume Recent scan regression so
  5k+ hourly query windows must keep bounded selected results instead of
  silently degrading to empty scans.
- Package CI now smoke-tests the installed `query-doctor-web` entrypoint against
  a demo pack generated from the installed wheel, covering package data and
  browser-visible Recent scan rendering in the installed-user path.
- Recent scan date changes now refresh the hour selector in the browser, so
  previous-day windows show the full 00:00-24:00 day instead of keeping today's
  truncated hour list.

### Safety

- Browser-visible dynamic error and Recent scan presenter text now redacts
  infrastructure hostnames, IP addresses, email addresses, and inline
  user/host labels, with regression tests covering shared display sanitizers and
  Recent scan warning output.

## 0.1.1 - 2026-05-13

### Engineering

- Added TestPyPI and PyPI Trusted Publishing release paths through GitHub OIDC,
  with maintainer-approved GitHub Environments, no stored PyPI API tokens, and
  installed-wheel smoke checks before package upload.
- Added installed console script contract tests to package and publish
  workflows so release artifacts verify declared entry points, safe help output,
  and the absence of unsafe SQL execution flags after wheel installation.
- Added config matrix regression coverage for `privacy_mode`, `no_llm`, direct
  Impala metadata redaction defaults, Prometheus runtime context settings, and
  cluster-level privacy overrides.
- Stopped `query-doctor-demo --help` from displaying a concrete local temporary
  path in its default output.

## 0.1.0 - 2026-05-13

### Engineering

- CodeQL now runs unconditionally on pull requests, default-branch pushes,
  schedule, and manual dispatch with test fixtures excluded from security alert
  noise, and optimizer/signature helpers avoid regex shapes that CodeQL flags
  as polynomial ReDoS risks, including optimizer SQL comment/string stripping.
  The public-release scanner now validates allowed
  credential-bearing example URLs by parsed hostname instead of URL substring
  matching.
- The isolated `impala-shell` bootstrap now installs the legacy shell package
  without its stale exact dependency set, then installs Query Doctor-owned
  runtime pins with patched `sqlparse` and keeps installed package metadata in
  sync with that patched pin.
- Added a local development gate (`scripts/local_gate.sh`), a staged
  public-safety scanner, and local pre-commit hooks so agents and maintainers
  can catch generated artifacts, local configs, private markers, whitespace
  issues, docs drift, lint/format failures, test failures, and demo regressions
  before committing or preparing a public branch.
- Public, demo, quickstart, security, release, roadmap, architecture, agent, and
  Russian companion docs now share the same 2026-05-13 baseline for direct
  Impala Recent/Running/Known Query ID support, optional bounded Prometheus
  runtime metrics, ruff check/format hooks, CodeQL, and public-release
  validation.
- Repository hardening docs now capture the current branch protection, security
  scanning, CI, release automation, strong-autotest, and maintainer
  time-saving backlog, including the Web E2E, Dependency Review, protected
  tags, CodeQL `security-extended`, workflow-audit, and release-publishing
  follow-ups.
- PyPI release automation now has a dedicated Trusted Publishing workflow that
  validates tag/version alignment, builds and checks distributions, smoke-tests
  the installed wheel, and uploads through GitHub OIDC without stored PyPI API
  tokens.
- Web E2E now reports a stable `Web E2E / Chromium` check on every pull request
  and default-branch push, while skipping browser installation for unrelated
  diffs. `Dependency Review` and `Web E2E / Chromium` are now required checks,
  and release tags matching `v*` are protected from deletion and
  non-fast-forward updates.
- Safety CI now runs the full Python 3.11 pytest suite on pull requests and
  default-branch pushes, making the protected-branch full-suite requirement a
  real merge gate instead of a scheduled-only check.
- The public README now presents the current Cloudera Manager, direct Impala,
  Prometheus, metadata, report, and optimizer scope as the repository front
  page instead of describing the project as Cloudera-Manager-only pre-release
  work.

### Product

- Local config discovery now supports `~/.qdcreds/query-doctor-config.json`.
  The local web bootstrap wrapper prefers that workstation config, then
  repository-local `query-doctor-config.json`, then legacy
  `.query-doctor-cm.local.json`. A new configuration reference documents config
  locations, precedence, field groups, multi-cluster shape, privacy controls,
  and secret-handling rules.
- Local config now supports `privacy_mode` (default on) and `no_llm`.
  Privacy mode makes identifier, hostname, and metadata redaction the default
  for web, Recent, CM, direct Impala, and metadata collection paths while
  preserving the hard rule that browser-visible and trusted report surfaces do
  not show raw SQL/profile/metadata. `no_llm` routes report and optimizer
  actions through deterministic Python-owned output without calling a local
  generation backend.
- Primary bottleneck routing now preserves safe competing-signal reasons for
  mixed stats/query-shape/runtime cases, so Details can distinguish stats plus
  SQL shape, data movement, skew, or storage evidence and recommend the next
  review focus without exposing raw query or metadata content. Mixed cases now
  soft-cap stats candidates so they stay visible without outranking the
  competing analyzer-supported signal. Details wording now explains these
  reasons as user-facing review guidance instead of internal signal names.
- Direct Impala clusters can now run bounded Recent and Running scans from
  impalad debug web query-list endpoints. The workflow reuses the existing
  Recent batch analyzer/scoring path, collects selected profiles through direct
  impalad profile endpoints, and intentionally skips Cloudera Manager metrics
  and events for direct Impala clusters.
- Web Recent and Running forms now hide Cloudera Manager event/metric tuning
  controls and collect bounded runtime context automatically when the selected
  source supports it.
- Web Recent and Running direct Impala scans now forward configured Prometheus
  runtime metrics to profile collection, keep selected-cluster metadata
  collection enabled, and use provider-neutral runtime metrics wording in the
  progress UI.
- Direct Impala Recent metadata refresh now uses the configured bounded
  metadata budget across the ranked analyzed case set after prioritized
  high-signal cases, so Ambari-style clusters can collect Impala metadata even
  when the detected profile signals are only low or clean.
- Web Recent result tables are more compact: Optimization and Stats candidate
  list views no longer show separate Next action columns, and optimization
  review scope is folded into the Summary cell while details pages retain the
  fuller guidance.
- The Diagnose panel now shows one shared Cluster selector above Diagnosis
  target. Recent and Known Query ID submits use that shared selection, and Known
  Query ID preserves it while an analysis job is running.
- Web Recent Scan target is now a segmented Finished queries / Running now
  control, making the different filter sets visible as a mode switch instead
  of a dropdown.
- Diagnose page controls now use consistent compact sizing, and the Finished
  scope note hides when the Recent scan target is switched to Running now.
- Local web config can now define a `clusters` list. The Diagnose, Running,
  and Known Query ID UI show those configured clusters in a Cluster selector
  instead of the former Cluster integration selector, and selected clusters
  drive the CM target, CM metrics compatibility profile, metadata settings, and
  Known Query ID profile source for that run.
- Web Recent scans now pass the selected cluster settings through per-case
  Cloudera Manager profile collection even when the local config uses only a
  `clusters` list, and broad web Recent scans stop at a 100-candidate analyzer
  guardrail before starting profile collection.
- Recent batch runs now accept and forward the selected cluster metadata
  Kerberos service name, covering Ambari-style direct Impala clusters whose
  metadata service principal is `hive`.
- Direct Impala Known Query ID collection can now attach bounded Prometheus
  runtime metrics when `collect_prometheus_timeseries=true` and
  `prometheus_url` are configured. The collector uses allowlisted PromQL,
  fixed query windows, response-size and point limits, and writes only
  summarized `runtime_metrics_context.json` facts; it still does not add
  discovery, events, raw time-series, labels, or provider JSON.
- Impala metadata collection can now pass a configured Kerberos service name
  such as `hive` to `impala-shell`, covering Ambari deployments whose service
  principal is not the upstream `impala` default.
- The local config template and smoke runbook now show Ambari-style direct
  Impala clusters with Prometheus runtime metrics, `hive` Kerberos service
  names, and selected-cluster Known Query ID validation.
- Direct Impala profile collection now rejects non-profile daemon HTML/error
  responses instead of saving them as collected profile text, and profile
  not-found marker matching is case-insensitive.
- Direct Impala profile collection now accepts valid profile pages whose safe
  profile markers appear after a long daemon HTML prefix, while keeping the
  existing bounded profile response size cap.
- Query LLM optimizer and detailed/admin report writing now use separate
  configured model routes, so optimizer recommendation behavior can be tuned
  independently from trusted report writing.
- Model-route documentation now describes the comparison protocol and safety
  gates while keeping local comparison results, rankings, and latency details
  out of committed public docs.
- The validation log now records a fresh 2026-05-12 Cloudera Manager
  `QUERY >=60s` optimizer funnel sample, and the roadmap now points the next
  optimizer measurement at explicit selected-case outcomes for the 11
  draft-ready cases from that sample.
- The validation log now records the explicit optimizer outcome pass for those
  11 draft-ready cases: all produced trusted deterministic SQL drafts, so the
  next optimizer investigation moves to recipe-detected/no-draft and
  recipe-adjacent structural-boundary cases.
- Architecture and Query Optimizer contract docs now include compact Mermaid
  flow diagrams for the analyzer fact pipeline and optimizer trust boundary.
- README synthetic demo screenshot now reflects the current Diagnose workflow,
  and the demo-mode docs describe how to refresh the public screenshot from
  synthetic data only.
- The web design toggle now exposes only the two retained designs: blue
  `serious` and green `command`. Stored classic or pink design values fall back
  to the blue design instead of remaining selectable.
- Roadmap, architecture, handoff, local-smoke, and agent instructions now
  reflect the direct Impala baseline: source provenance, profile
  resource/timing facts, Runtime Diagnosis profile signals, and optional
  Prometheus runtime metrics are current support, while prepared event/log
  sources remain future optional context.
- Query LLM optimizer no-rewrite outcomes now show specific browser-safe
  fallback reasons, such as validation rejection, no material change, no
  supported Python-owned recipe, or output budget, instead of implying that the
  original query needs no optimization.
- Query LLM optimizer now fails closed when a supported recipe is detected but
  Python cannot construct a deterministic draft for the exact query shape: it
  writes a trusted no-rewrite outcome and does not ask the LLM for a SQL draft.
- Optimizer model bake-off summaries now separate product-level trusted outcomes
  from model-comparable runs and include raw-free recommendations-only
  normalization telemetry, so deterministic recipes and canonical fallback
  guidance do not inflate model quality.
- Optimizer model bake-off runs can now filter fixture corpus cases by expected
  output kind, enabling faster repeated recommendations-only model comparisons
  without rerunning deterministic recipe fixtures.
- Finished, Running, and Known Query ID job pages now re-enable their Run
  button after a scan or query analysis reaches a terminal state in the live
  progress view.
- Analyzer facts now include a raw-free Source Provenance section that records
  per-case coverage for engine, profile, metrics, events, and metadata sources.
  Direct Impala daemon cases show profile-source availability while missing
  metrics, events, or metadata are explicit `none` coverage rather than implied
  Cloudera Manager context.
- Runtime Diagnosis now includes Profile resource balance as a deterministic
  follow-up signal from fresh Impala resource facts. Normal balanced resource
  sections remain context-only; queued/rejected admission or large backend
  startup latency become plausible follow-up hypotheses without changing triage
  score or making root-cause claims.
- Runtime Diagnosis now includes Profile timing phases as a deterministic
  follow-up signal from fresh Impala Query Timeline and fragment lifecycle
  facts, keeping timing evidence profile-only and raw-free.
- Analyzer facts now include a raw-free Profile Resource Facts section for
  fresh Impala runtime profiles, summarizing admission result, backend startup
  latency distribution, fragment instance balance, per-node peak memory,
  per-node bytes read, and per-node user/system time balance without exposing
  hostnames or raw profile lines.
- Analyzer facts now include a raw-free Profile Timing Facts section for fresh
  Impala profiles, summarizing Query Timeline phases and fragment lifecycle
  timing aggregates without rendering raw profile events or host-level details.
- Analyzer facts now include a raw-free Profile Format section for Impala
  runtime profiles, recording source, safe Impala version, layout fingerprint,
  and section availability so fresh daemon profile layouts can be audited
  without exposing SQL or raw profile text.
- Direct Impala Known Query ID collection now records safe impalad identity
  metadata from read-only daemon endpoints when available, including product,
  version, server mode, and local-catalog mode.
- Analyzer backend-tail parsing now preserves per-instance metrics after fresh
  Impala `Fragment Instance Lifecycle ...` profile headers, restoring backend
  host tail evidence for direct Impala profiles that use the newer layout.
- New collected and synthetic cases now write provider-neutral
  `query_metadata.json` alongside legacy `cm_metadata.json`; analyzer and Query
  LLM optimizer source extraction prefer the neutral metadata file while keeping
  legacy cases compatible.
- Direct Impala Known Query ID cases now record safe profile-source provenance
  and render analyzer facts under provider-neutral Query Profile Context labels
  instead of Cloudera Manager context headings.
- Known Query ID can now use a direct Impala daemon profile source for one
  explicit query ID when `query_profile_source=impala` and bounded
  `impala_profile_hosts` are configured. The collector reads only impalad
  profile endpoints, keeps redaction mandatory, does not run SQL, and does not
  add discovery, metrics, or events.
- Recent scan optimizer output now labels safety-threshold cases as
  human-review-only and shows raw-free guardrail summaries in batch artifacts
  and the Optimization candidates UI instead of surfacing draft-disabled wording
  or untrusted optimizer reasons.
- Recent scan Optimization candidates now explicitly label non-rewriteable
  cases as review-guidance-only, making clear that no trusted SQL draft shape
  was detected and the review areas are for manual query-shape analysis.
- Recent scan summary metrics now show optimizer funnel counters for
  draft-ready, recipe-backlog, and review-only optimization candidates so broad
  batches do not imply that every candidate can produce a trusted SQL draft.
- Recent scan results now explain the optimizer funnel labels in-place,
  including that review-only candidates have no trusted SQL draft shape and
  should be handled as manual query-shape review.
- Recent scan Details now includes an Analysis summary that brings primary
  bottleneck, optimizer outcome, stats candidate, runtime context, metadata
  coverage, and next action into one raw-free triage block. Review-only
  optimizer candidates now explicitly say that the current deterministic
  optimizer will not generate a trusted SQL draft for that case.
- Details pages now fold the previous visible Evidence guide into Analysis
  summary, keeping evidence quality and fact-strength guidance while removing a
  duplicated first-screen triage block.
- Details-page Query LLM optimizer buttons now say `Run Query LLM optimizer`
  until a validated result exists, avoiding a premature promise that every run
  will produce a SQL draft.
- Details Evidence details now suppresses repeated generic safety prose and
  low-value runtime guardrail rows while preserving concrete coverage,
  correlation, signal, metadata, and limitation facts.
- Details Evidence details no longer renders separate runtime `Limitations`
  sub-blocks for generic Cloudera Manager metric caveats; concrete coverage,
  correlation, signal rollups, and metadata limitations remain visible.
- Finished and Running scan progress bars now use the same batch progress model
  as the detailed progress blocks, so the top stage and fill percent track the
  currently active scan phase.
- Details now shows collected run timing fields as `Pipeline timings` under
  Pipeline status instead of keeping a vague `Technical details` block inside
  Evidence details.
- The `post_union_aggregate_pushdown` deterministic recipe now handles
  constant-row `UNION ALL` branches, `COUNT(...)` rollups, and downstream
  aggregate dimension aliases. Strictly validated deterministic recipe drafts
  can be marked safe even when the generic SQL-size risk gate records
  recommendations-only risk context.
- Fast CI now runs the public-release preflight against the current tracked
  tree, while the manual release gate retains the full git-history scan for
  visibility changes.
- Optimizer no-draft diagnostics now distinguish final SELECT join and final
  CTE reference boundaries for CTE predicate-pushdown recipes, improving
  raw-free backlog analysis without enabling new SQL draft output.
- Optimizer no-draft diagnostics now mark unsupported post-UNION aggregate
  rollups such as `AVG`, `MIN`, `MAX`, and `COUNT(DISTINCT ...)` with
  raw-free aggregate-specific reasons, keeping those shapes deterministic
  no-rewrite unless a Python-owned proof boundary is added.
- Recent batch optimizer rewriteability summaries now include raw-free
  recipe-adjacent CTE and derived-table status/boundary aggregates, making
  non-recipe backlog analysis repeatable without inspecting SQL or case paths.
- Recent optimizer backlog summaries now split recipe-adjacent shapes into
  actionable, structural-boundary, and other buckets, and structural adjacent
  shapes no longer rank ahead of stronger optimizer candidates.
- Recent optimizer backlog summaries now also split recipe-detected no-draft
  cases into actionable, structural-boundary, validation/materiality, and other
  buckets, so bounded recipe-extension work is separated from structural SQL
  boundaries.
- Recent batch metadata refresh now lets high/medium Query Optimization
  candidates use the remaining `metadata_top_limit` budget instead of stopping
  at a fixed query-optimization sub-limit, improving large `>=60s` optimizer
  analysis coverage while preserving the overall metadata hard cap.
- Analyzer primary bottleneck routing now has a conservative
  `runtime_storage` branch for profile-supported storage/HDFS top findings.
  Recent batch summaries also include raw-free aggregate breakdowns for
  remaining `unknown` primary cases so large-batch calibration can continue
  without inspecting raw SQL, profiles, metadata, or case paths.
- Analyzer primary bottleneck routing now treats top join, sort, and analytic
  operator findings as medium-confidence `sql_shape` signals when stats are
  not supported as primary and no higher-precedence runtime branch owns the
  case. Competing storage/HDFS and query-shape signals remain `mixed`.
- Analyzer primary bottleneck routing now treats backend data skew as a
  medium-confidence `runtime_skew` signal only when no top query-shape,
  data-movement, or storage/HDFS branch already owns the case.
- Recent batch discovery now retries Cloudera Manager summary scans with
  bounded one-hour time shards when CM reports its query scan limit, improving
  large-window candidate coverage while preserving raw scan caps and safe
  summary output.
- Report, Details, and Recent scoring parsers now accept provider-neutral
  `Runtime Metrics Facts` and `Runtime Metrics Correlation` headings while
  preserving legacy `CM Metrics` heading fallbacks.
- Analyzer facts now emit provider-neutral `Runtime Metrics Facts` and
  `Runtime Metrics Correlation` headings for derived metric facts, while raw
  Cloudera Manager time-series context remains explicitly labeled as such.
- Cluster Runtime Context, Evidence Quality, and correlated action-card evidence
  now use provider-neutral runtime metrics wording, while raw Cloudera Manager
  collection context remains source-labeled.
- Details, Recent scoring reasons, and trusted report digest/normalization now
  use provider-neutral runtime metrics wording for derived metric evidence,
  while Cloudera Manager collection controls remain source-labeled.
- Cluster Event Context display, report digest/normalization, analyzer
  appendix headings, and Recent scan progress now use event-context wording for
  derived event evidence, while Cloudera Manager event collection controls
  remain source-labeled.
- Local web responses now include a per-request trace ID header, and local
  server logs include the same ID for easier correlation while debugging web
  requests and background actions.
- The in-memory local web job store now prunes old terminal jobs with bounded
  TTL/count cleanup while preserving running jobs.
- Result-table rows now use CSP-safe static JavaScript for Details navigation,
  preserving row click and keyboard access without inline event handlers.
- Details pages now expose an Export-as-Markdown action for trusted Finished,
  Running, and Known Query ID reports. Untrusted, partial, or stale reports
  remain non-exportable, and exported content reuses the same
  `[local case path hidden]` redaction as the in-browser report view.
- Recent optimizer facts now classify simple `UNION ALL` CTE branch-filter
  shapes with raw-free branch counts and safe categories such as all-branch,
  single-branch, ambiguous lineage, or unsupported projection. These categories
  improve rewriteability triage and Details summaries without enabling any new
  SQL draft recipe.
- Query LLM optimizer source discovery now accepts analyzer-owned
  `impala_context/original_query.sql` when a case does not have source SQL in
  the case root. This lets Recent rewrite support and explicit Details
  optimizer actions classify cases already enriched by the analyzer without
  exposing source SQL in browser output.
- Recent batch summaries now include safe no-draft recipe and draft-eligibility
  counts inside the optimizer rewriteability distribution, making recipe
  backlog analysis repeatable without inspecting raw SQL or case paths.
- Recent optimizer rewrite support now records allowlisted no-draft diagnostics
  for deterministic CTE predicate-pushdown attempts, including safe draft
  reason categories and aggregate conjunct decision counts without storing
  predicate text, CTE names, or raw SQL.
- Recent batch summaries now classify deterministic no-draft optimizer backlog
  into a single allowlisted primary class, such as CTE lineage limit,
  downstream CTE filter, missing final filter, shape boundary, predicate not
  copyable, or validation/materiality.
- Recent batch summaries now include a safe no-draft class-by-recipe aggregate
  so optimizer backlog analysis can separate CTE lineage limits from
  downstream-filter gaps without inspecting raw SQL, case paths, or query IDs.
- Deterministic CTE DAG predicate-pushdown diagnostics now add safe lineage
  subreasons, such as unavailable upstream lineage or UNION projection
  mismatches, so no-draft telemetry can explain lineage limits without exposing
  raw SQL or CTE names.
- Recent batch summaries now include a safe no-draft class-by-recipe-by-reason
  aggregate, making optimizer backlog slices such as CTE DAG lineage limits
  repeatable without inspecting raw SQL, query IDs, or case directories.
- Recent batch outputs now include a raw-free optimizer funnel summary in
  `batch_summary.json`, `batch_summary.md`, and `optimizer_funnel.json`,
  counting optimization candidates, recipe detection, draft-ready cases, and
  no-draft backlog without running optimizer jobs automatically.
- Recent metadata refresh now ranks optimizer candidates by safe rewriteability
  before score within the same candidate tier, so bounded metadata collection
  prioritizes draft-ready and recipe-backlog cases over expensive
  human-review-only shapes.
- `scripts/compare_optimizer_models.py` now writes a raw-free
  `optimizer_funnel.json` and Markdown funnel section with trusted draft,
  no-rewrite, recommendations-only, partial-untrusted, expected, and offline
  outcome counts for fixture and model benchmark runs.
- CTE DAG lineage diagnostics now break upstream lineage failures into safer
  subreasons for non-simple projections, qualified physical-table projections,
  and upstream UNION branch mismatches.
- Deterministic CTE DAG predicate pushdown now treats qualified physical-table
  leaf projections such as `e.ds` as simple local columns when the output name
  matches the column and no upstream CTE reference is involved. The trusted
  draft still requires existing downstream predicates, preserved final filters,
  and recipe validation.
- Deterministic linear CTE predicate pushdown can now copy a simple filter from
  a downstream CTE back into the first CTE in the chain when the downstream CTE
  has no top-level join, the predicate targets preserved upstream columns, and
  recipe validation keeps every original downstream filter in place.
- Optimizer no-draft diagnostics now add safe downstream CTE filter subreasons
  for filters that are not on a CTE reference path, plus join, distinct, and
  unsupported-clause boundaries. These categories improve backlog analysis
  without exposing SQL text, CTE names, or predicates.
- Optimizer predicate-pushdown diagnostics now split generic `not_for_target`
  conjunct decisions into safe foreign-qualifier, unavailable-column, and
  malformed-qualified-reference categories, improving no-draft backlog analysis
  without exposing predicate text.
- Predicate-pushdown diagnostics now distinguish foreign-only predicates from
  mixed target-and-foreign predicates, which helps separate ordinary joined-side
  filters from potential future transitive-pushdown recipe candidates without
  exposing qualifier names or predicate text.
- Predicate-pushdown diagnostics now split generic `unsupported_predicate`
  conjunct decisions into safe parse-failed, qualified-reference,
  function-call, unavailable-unqualified-column, unsupported-token,
  unknown-identifier, and no-column-reference categories without exposing
  predicate text.
- CTE DAG lineage diagnostics now split generic upstream `UNION ALL` branch
  mismatches into safe branch lineage mismatch and branch projection failure
  subreasons without exposing CTE names, column names, or SQL text.
- Provider decoupling direction now lives in the active roadmap and engine
  expansion plan; the older provider decoupling audit was removed from the
  active docs index to avoid competing source-of-truth documents.
- Recent optimizer rewrite support now records a safe rewriteability bucket for
  each case: safe material draft, recipe detected with no deterministic draft,
  recipe-adjacent shape, stats-likely, human-review-only, or not rewriteable.
  Batch summaries aggregate those buckets, and Details action cards can show
  the bucket without exposing raw SQL or optimizer internals. Recent
  Optimization candidate ranking now uses the bucket so draft-ready and recipe
  backlog cases sort ahead of expensive-but-undraftable guidance-only cases
  within the same candidate tier.
- Query LLM optimizer now has a narrow Python-owned deterministic recipe for
  single-CTE projection-alias predicate pushdown. A final filter on an output
  alias such as `ds` can be copied into the CTE as the exact source column
  predicate only when the projection maps that alias to one unqualified source
  column, and validation preserves the original final filter and output shape.
- Metadata parsing now counts per-partition row-count coverage from
  already-collected `SHOW TABLE STATS` output. Analyzer facts expose only safe
  counts and coverage categories; totals-only partitioned output remains
  `unknown` partition coverage, and raw partition values are not rendered.
- Metadata parsing now classifies per-column stats coverage from
  already-collected `SHOW COLUMN STATS` output into safe complete, NDV-missing,
  size-missing, and all-missing counts. Join/filter column context and Recent
  metadata summaries consume those structured counts without exposing raw NDV
  values or uncapped column lists.
- Recent stats scoring no longer treats legacy `stats_possibly_stale`,
  "stats possibly stale", or "supported stale" rendered text as positive
  evidence. Old artifacts with that need type are labeled as stats freshness
  unknown rather than as a stale-stats recommendation.
- Predicate-pushdown deterministic rewrites now decompose parenthesized
  `AND` groups when the parentheses enclose the whole group. Safe conjuncts can
  be copied independently while foreign-alias conjuncts stay only in the
  downstream `WHERE`, and validation uses the same per-conjunct helper.
- Roadmap optimizer priorities now treat rewriteability taxonomy,
  recipe-aware ranking, and per-conjunct parenthesized `AND` pushdown as
  completed baseline work. The next optimizer pull starts with a fresh real
  optimizer funnel benchmark before adding another recipe.
- Recent batch summaries now include `case_primary_bottleneck_distribution`
  with label counts, confidence counts, unknown/mixed/not-classified rates, and
  medium-or-better confidence coverage. `batch_summary.md` renders the same
  safe aggregate counts for stop-condition tracking.
- Recent and Details pages now surface safe `case_primary_bottleneck` labels,
  confidence, and reason categories from the presenter layer. The UI shows the
  primary routing signal without exposing raw analysis dictionaries, SQL,
  profile text, paths, model names, or artifact filenames.
- Recent batch scoring now uses high-confidence `case_primary_bottleneck` to
  cap non-primary stats/query action candidate tiers to `low`, while preserving
  raw scores and adding explicit counter-signals. Medium, low, mixed and
  unknown bottlenecks do not cap competing actions.
- Documentation now records the future expansion order explicitly: Direct
  Impala profile source before second-engine work, engine fact contract before
  new engine implementation, Trino as a candidate to validate rather than a
  commitment, Prometheus metrics as a separate source axis, and storage/table
  facts deferred until provider and engine boundaries stabilize.
- Recent stats and query optimization scorers now prefer structured
  `analysis.json` fields when available for stats metadata quality,
  cardinality counts, join/filter column relevance, and metadata-gap signals.
  Rendered `analysis_facts.md` parsing remains as a fallback for older case
  artifacts.
- Analyzer facts now include an informational Python-owned
  `case_primary_bottleneck` with conservative labels for stats, SQL shape,
  runtime admission, runtime skew, runtime data movement, mixed signals, and
  unknown. It is rendered in `analysis_facts.md` as `Primary Bottleneck`; scorer
  caps and Recent/Details presentation now consume the safe structured label.
- Product direction is now explicitly diagnostic-first: Query Doctor is framed
  as an evidence-backed Impala diagnoser whose primary success is
  `case_primary_bottleneck` and evidence-quality coverage; trusted SQL rewrites
  are a narrow validated outcome, not the central promise.
- Optimizer recipe baseline as of 2026-05-08: Recent scan labels separate
  recipe detection, draft eligibility, and trusted draft production; CTE and
  derived-table shape facts expose safe categories only; stats metadata quality
  records row-estimate, partition, join/filter column, and competing-bottleneck
  context; Python-owned deterministic executors exist for
  `post_union_aggregate_pushdown`, `final_union_distinct_rollup`,
  `single_cte_predicate_pushdown`, `linear_cte_predicate_pushdown`,
  `cte_dag_predicate_pushdown`, `single_derived_table_predicate_pushdown`, and
  `pass_through_cte_elimination`; recent real-case batches still produced zero
  trusted SQL drafts outside those narrow proven boundaries, so current
  roadmap work focuses on rewriteability taxonomy, recipe-aware selection, and
  richer analyzer facts rather than prompt-only SQL rewriting.
- Documentation baseline now has clearer agent-facing structure: `AGENTS.md`
  starts with explicit hard rules, `docs/README.md` has a single status index
  for active/reference docs, historical docs moved under `docs/archive/`,
  active docs gained reviewed-date headers where missing,
  `docs/agent-playbook.md` has a change-routing table, and the optimizer
  contract now states that LLMs are not the trusted SQL writer for optimizer
  drafts.
- Roadmap and agent handoff now record the strategic product framing from the
  external review: Query Doctor should lead as an evidence-backed Impala
  diagnostics product, with trusted SQL rewrites as one validated outcome rather
  than the central promise. Near-term roadmap now promotes workload
  fingerprinting, regression baselines, action outcome tracking, optimizer
  funnel automation, and explicit LLM demotion from SQL writer to wording and
  recommendation support.
- Roadmap and analyzer audit now record the stats/metadata diagnosis review:
  scoring should consume structured analyzer facts instead of rendered markdown,
  stale-stats wording must be gated until direct evidence exists, and a
  Python-owned `case_primary_bottleneck` should route expensive cases between
  stats, SQL-shape, runtime, mixed, and unknown action paths.
- Roadmap and optimizer bake-off guidance now state the active optimizer
  strategy explicitly: expensive production queries remain the target, but
  trusted SQL drafts are one validated outcome among stats actions,
  query-shape recommendations, data/layout recommendations, and deeper-facts
  limitations. Model comparisons must separate expensive from rewriteable
  queries before judging SQL rewrite quality.
- Optimizer roadmap now records the external review conclusions: SQL draft
  yield is limited by Python recipe coverage and cost-only candidate selection,
  not by prompt/model quality; near-term work should prioritize rewriteability
  taxonomy, per-conjunct hardening, recipe-aware ranking, expression-projection
  pushdown, and UNION ALL branch pushdown while keeping the validator strict.
- Optimizer roadmap and contract now clarify that per-conjunct predicate
  pushdown is already a shared helper contract for existing predicate-pushdown
  recipes. The next work is hardening and tests, plus parenthesized `AND` group
  handling, not a new recipe ID.
- Recent scan optimizer rewrite support now runs the Python-owned deterministic
  draft feasibility check before labeling a recipe `safe_to_attempt`. Detected
  recipes whose deterministic executor cannot build a material validated draft
  are shown as draft-unavailable instead of being treated as SQL-draft-ready
  hunting targets.
- Query LLM optimizer now has a Python-owned deterministic executor for the
  narrow `linear_cte_predicate_pushdown` recipe. Safe final-filter copies can
  be pushed into the first CTE of a simple linear chain without calling the LLM
  when every CTE preserves the referenced columns.
- Query LLM optimizer now has Python-owned deterministic executors for the
  narrow `post_union_aggregate_pushdown` and `final_union_distinct_rollup`
  recipes. Supported `UNION ALL` rollups can be drafted mechanically from
  branch projections and downstream aggregate expressions, then validated
  before display, without relying on the LLM for SQL construction.
- Query LLM optimizer now has a Python-owned deterministic executor for the
  narrow `cte_dag_predicate_pushdown` recipe when final SELECT predicates can
  be traced through simple CTE projections and UNION branches to one leaf CTE
  output column.
- Query LLM optimizer now has a Python-owned deterministic executor and
  recipe-specific validation for the narrow `pass_through_cte_elimination`
  recipe. A single-use CTE that only selects simple columns from one upstream
  CTE can be removed without calling the LLM, while preserving every remaining
  CTE body and final SELECT contract.
- Query LLM optimizer no longer asks the LLM for SQL drafts when Python has not
  detected a supported rewrite recipe. Unsupported shapes now become trusted
  `no_rewrite` outcomes with deterministic guidance, and the generic SQL draft
  prompt has been reduced to a compact SQL-only contract without runtime/CM
  digest blocks.
- Query LLM optimizer now has analyzer-owned derived-table shape facts and a
  Python-owned deterministic executor for the narrow
  `single_derived_table_predicate_pushdown` recipe. Safe outer-filter copies
  into one simple derived table can produce trusted SQL drafts while preserving
  nested-body validation.
- Query LLM optimizer validation now treats projection alias `AS` spelling and
  qualified-name spacing as non-material, and rejects unbacked nested query
  body rewrites unless a Python-owned recipe proves the transform.
- Recent scan metadata selection now includes top medium/high Optimization
  candidates in the bounded metadata refresh budget, so stats-vs-query evidence
  is collected for optimizer triage instead of only high/suspicious general
  triage cases.
- Query LLM optimizer now has a Python-owned deterministic executor for the
  narrow `single_cte_predicate_pushdown` recipe. Safe single-CTE filter-copy
  rewrites can produce trusted SQL drafts without relying on the LLM for the
  mechanical rewrite, while still passing strict validation before display.
- Single-CTE predicate-pushdown detection now requires a copyable downstream
  predicate that targets the CTE output, so filters on unrelated joined aliases
  are no longer counted as rewrite recipes.
- Optimizer CTE shape facts now include safe predicate-origin, predicate-path,
  projection-contract, and projection-preservation categories for future recipe
  validation without exposing CTE names or SQL fragments.
- Recent scan Optimization candidates now show safe optimizer fact summaries
  and guardrails from rewrite-support classification, including CTE graph,
  predicate path, projection preservation, and boundary categories.
- Details Evidence guide now uses analyzer-owned Evidence Quality from
  `analysis_facts.md` when available, while keeping detailed strengths and
  limitations in collapsed evidence/details surfaces.
- Analyzer facts now include `Stats Metadata Quality`, a raw-free summary of
  table/column stats coverage that Details can display without deriving the
  classification in the UI.
- `Stats Metadata Quality` now records row-estimate evidence and partitioned
  table stats coverage, including the safe case where stats are present but
  row-estimate mismatch still remains.
- Analyzer SQL context now computes raw-free join/filter column stats coverage
  counts so stats quality can distinguish covered, partial, missing, and unknown
  column-stat relevance without exposing SQL or column names in Details.
- Stats Metadata Quality now records safe competing bottleneck categories and a
  stats-primary-bottleneck context so stats-present row-estimate mismatches are
  not overread as stats-refresh evidence when stronger non-stats signals exist.
- Details Evidence guide now separates stats evidence from general metadata
  coverage, showing when a deterministic stats-refresh candidate exists and
  when stats context is merely limited or non-candidate.
- Optimizer rewrite-support classification now records safe CTE shape facts
  such as graph category, downstream-filter eligibility, and boundary categories
  before labeling CTE predicate-pushdown recipes.
- Optimizer CTE shape facts now also classify single-use and pass-through CTE
  simplification candidates as safe categories for future deterministic recipes.
- Recent scan optimizer rewrite support now separates detected rewrite recipes
  from SQL draft eligibility, and explicitly labels cases where a recipe exists
  but a draft is disabled by optimizer guardrails.
- Combined selected-case LLM report + optimizer runs now render one shared stop
  control and show a combined stopped status when cancelled.
- Web analysis, Recent scan, Running scan, and selected-case LLM jobs can now
  be stopped from the browser; cancellation terminates the active subprocess
  and keeps partial/raw output hidden.
- Recent scan now sends Resource pool, Username, Query type, and
  Finished/Running filters to Cloudera Manager discovery before the bounded
  summary limit, while keeping client-side filtering as a backstop.
- Details-page Query LLM optimizer outcomes now show browser-safe guardrail
  explanations for recommendations-only results, including CTE validation and
  safe-draft threshold reasons.
- Query LLM optimizer validation now recognizes linear CTE chains and can trust
  a narrow predicate-pushdown recipe when downstream filters are copied earlier
  without removing the original filters or changing CTE shape.
- Query LLM optimizer validation now recognizes acyclic CTE DAG / UNION
  assembly shapes and can trust narrow predicate pushdown when copied filters
  stay on the same dependency path and the CTE graph is preserved.
- Details-page CM metrics now prioritize only profile-correlated metric signals
  in the main table and keep the full collected metric list in a collapsed
  all-metrics section.
- Details-page job polling now reloads completed LLM results even when the
  browser is already on the target details URL.
- Recent scan removed the `Optimizer-ready` result group and header count.
  Trusted optimizer drafts and recommendations remain visible from the
  `Optimization candidates` group after an explicit details-page optimizer run.
- Details pages now include a compact Evidence guide that summarizes
  deterministic fact strength, runtime context, metadata coverage, and the
  safest next action before the expanded Findings and Evidence details blocks.
- Details-page LLM actions now keep the browser anchored on the LLM actions
  section when report or optimizer jobs start and when progress polling
  reloads completed results.
- Recent scan progress now labels CM Events as scan-window event context and
  shows elapsed timing across discovery, collection, analysis, metrics,
  metadata, summary, and completion steps when timing is available.
- The main Recent scan screen now includes a `Known Query ID` mode beside
  `Recent queries`. The `/query` compatibility route opens that focused mode,
  while top navigation keeps diagnosis as the single primary entry. The top-nav
  entry is now labeled `Diagnose` to match the combined workflow.
- The standalone `Query Optimizer` route remains read-only and directly
  accessible, but is hidden from top navigation and Help so the primary UI
  favors profile-backed diagnosis and details-page optimizer actions.
- Recent scan now supports Finished and Running query workflows with the same
  result/details shape. Finished remains the primary completed-query scan;
  Running is a lower-confidence live snapshot without date/hour filters.
- Details pages now present a compact deterministic findings view, collapsed
  evidence details, explicit LLM actions, runtime verdicts, metadata facts, CM
  metrics, Cluster Runtime Context, and validated report/optimizer outcomes.
- Removed the separate in-product `Demo guide` navigation entry. The legacy
  `/demo` and `/demo-guide` routes now serve the maintained Help page, while
  demo talk-track material stays in repository docs.
- Recent scan can collect bounded Cloudera Manager metrics for the top ranked
  analyzed cases by default. The visible top-case budget defaults to 10 and the
  batch CLI exposes `--cm-timeseries-top-limit`.
- Recent scan can collect bounded Cloudera Manager Events context. Events are
  exposed only as raw-free Cluster Doctor context and follow-up signal counts,
  not as standalone query root-cause proof.
- Analyzer facts now include Python-owned Runtime Diagnosis, Cluster Runtime
  Context, CM Metrics Facts/Correlation, Query Wall Clock, Evidence Quality,
  Runtime Counter Context, CM Query Context, and Backend / Host Tail Evidence
  sections.
- Trusted reports now include bounded CM Events / Cluster Event Context in
  analyzer facts, report evidence, and the deterministic appendix, while
  validators reject event-context root-cause overclaims.
- English is now the default trusted report language, with Russian still
  available through the same language-specific prompt, normalization, and
  validation boundary.
- Synthetic Demo Mode can generate local safe demo packs with trusted demo
  artifacts and optimizer guardrail outcomes without LLMs, Cloudera Manager,
  Impala, or network access.
- The web UI and supported CLI entry points are package-owned. Root-level
  compatibility launchers and legacy executable prototypes were removed from
  current product workflows.

### Safety

- Recent scan Details Analysis summary now renders through a typed raw-free
  presenter view model, with focused browser-safety tests covering dynamic
  evidence, optimizer, stats, runtime, metadata, and next-action fields.
- Known Query ID report sidebar evidence inventory now comes from
  `query_doctor.web.trusted_artifacts`, so report UI rendering receives only
  safe category labels and availability states instead of raw artifact
  filenames.
- Known Query ID details and report actions now check analyzer-facts
  availability through `query_doctor.web.trusted_artifacts` instead of testing
  raw artifact filenames in page/action handlers.
- Details facts loaders now read analyzer facts through
  `query_doctor.web.trusted_artifacts`, keeping raw analyzer-facts filenames
  out of Details parsing entrypoints while preserving size bounds.
- Details metadata-context loaders now read bounded Impala context JSON through
  `query_doctor.web.trusted_artifacts`, keeping context artifact path handling
  out of Details parsing entrypoints.
- Report evidence inventory now uses path-safe trusted artifact predicates for
  category and pipeline availability, ignoring evidence symlinks that resolve
  outside the case directory.
- Trusted report and optimizer artifact loaders now resolve marker-bound files
  through the same case-contained path check before accepting or reading
  validated browser outputs.
- Details-page external optimizer rewrite validation now reads analyzer facts
  through the trusted case-contained loader instead of opening analyzer fact
  files directly.
- Known Query ID case-summary readers now ignore metadata, profile summary, and
  analyzer-output files that resolve outside the case directory before building
  browser-visible summary fields.
- Legacy Query ID analysis output loading now rejects analyzer and report files
  that resolve outside the case directory before returning browser-visible
  report text.
- Selected-case report jobs now require case-contained report output before
  writing the trusted validation marker or showing a generated status.
- Local web POST Origin checks now accept local `X-Forwarded-Host`,
  `X-Forwarded-Port`, and `Forwarded` host ports from localhost proxies while
  still rejecting non-local forwarded hosts and unrelated local Origin ports
  before reading request bodies.
- Local web POST Origin checks now allow browser-produced `Origin: null` only
  when a same-request `Referer` still points back to the local Query Doctor
  origin.
- The local web `Referrer-Policy` is now `same-origin`, so same-origin POSTs can
  prove local source when a browser sends `Origin: null` while still omitting
  referrers on external navigation.
- Local web POST Origin checks now accept same-request local forwarded Host
  ports while still rejecting non-local, malformed, and cross-port Origins
  before reading request bodies.
- Local web POST requests now reject non-local or malformed `Origin` headers
  before reading request bodies, and all web responses include a restrictive
  `Permissions-Policy` header.
- The local web server now rejects non-local `Host` headers by default and adds
  browser hardening headers, including no-sniff, same-origin referrers, frame
  denial, and a local-only Content Security Policy compatible with the existing
  server-rendered UI.
- The web UI now serves its shared CSS and JavaScript from allowlisted package
  static assets, letting the local Content Security Policy remove inline script
  and style allowances while preserving the server-rendered UI.
- Browser-visible UI and trusted reports continue to hide raw SQL, raw profiles,
  raw metadata, local paths, `case_dir`, subprocess output, secrets, model
  names, runtime internals, and raw artifact filenames.
- Browser dynamic text now uses a stricter shared display sanitizer for common
  unsafe snippets, model/runtime names, generated artifact names, field names,
  local paths, and subprocess markers.
- LLM Report validation rejects unsupported primary/root bottleneck claims,
  unsafe operator-time wording, unsupported metadata/stats claims, context-only
  CM metric causal claims, and CM Events causal claims.
- Query Optimizer and Query LLM optimizer keep submitted/source SQL out of
  browser-visible result models except for explicitly trusted validated drafts.
  Pasted SQL on the standalone optimizer page is still not echoed after submit.
- Trusted optimizer markers now bind validation mode, marker schema, draft hash,
  facts hash, source SQL hash, and source scope. Web trust checks reject legacy
  or stale markers and hide unsafe recommendation artifacts.
- External collection remains explicit, bounded, read-only, and allowlisted.
  Metadata collection stays limited to approved Impala `SHOW` statements, and
  CM metrics/events are normalized before entering trusted facts.

### Optimizer

- Query LLM optimizer validation now recognizes a narrow
  `single_cte_predicate_pushdown` recipe that copies final SELECT filters into a
  single CTE while preserving the final filter and output contract.
- Query LLM optimizer is an explicit Details-page action for server-owned
  analyzed cases. It can use safe SELECT/WITH source SQL or supported
  SELECT/WITH payloads extracted from INSERT/CTAS, but trusted drafts must
  remain read-only SELECT/WITH.
- Optimizer validation rejects unsafe SQL and result-shape changes, including
  changed table sets, removed filter scope, projection changes, DISTINCT
  changes, top-level GROUP/ORDER/set-operation changes, CTE shape changes, and
  top-level JOIN or `ON` condition changes.
- Structurally risky cases can produce trusted recommendations-only or no-rewrite
  outcomes instead of unsafe SQL drafts. Output-budget truncation and no-material
  draft changes also become trusted non-SQL outcomes.
- Python-owned optimizer recipes now cover narrow validated rewrite patterns
  such as post-UNION aggregate pushdown and final UNION DISTINCT rollup, with
  recipe-specific validation before any changed CTE body is trusted.
- The committed optimizer fixture corpus and
  `scripts/compare_optimizer_models.py --fixture-corpus` provide repeatable
  local model/prompt bake-offs without raw real query artifacts.

### Documentation

- README, demo, handoff, and local-smoke docs now describe Known Query ID as an
  explicit Impala query-id workflow that can use Cloudera Manager or direct
  Impala daemon profile endpoints depending on local config.
- Local web static checks now have a project-owned smoke script that verifies
  external CSS/JavaScript assets, strict CSP without `unsafe-inline`,
  allowlisted static routes, and common security headers against a running
  localhost UI.
- Agent-facing documentation now has a short `docs/agent-quickstart.md` entry
  point. `docs/codex-handoff.md` and `docs/roadmap.md` now link to canonical
  safety and optimizer contracts instead of duplicating their full rule lists.
- `scripts/agent_preflight.py` now reports validation scope guidance so agents
  can avoid full-suite test runs for docs-only and other narrowly scoped
  changes unless the touched boundary warrants it.
- README now states the supported deployment contract explicitly: Query Doctor
  is a single-user local-first tool, and the current web UI must not be deployed
  as a shared service without a separate auth, isolation, audit, TLS/proxy, and
  resource-limit design.
- Docs tooling now checks that non-archived `docs/**/*.md` pages are listed in
  the documentation status index, and the UI design notes are indexed as
  reference material.
- The Web UI strategy discussion notes were archived after the accepted
  direction was extracted into the roadmap, and the code audit now points
  Details rendering work toward gradual raw-free view models.
- Agent-facing instructions now align with the current optimizer facts-first
  workflow, explicit selected-case optimizer actions, and concrete focused test
  commands that avoid stale recent-scan globs.
- Roadmap and audit docs now track analysis-accuracy gaps outside optimizer
  rewrites: evidence confidence, fingerprint/workload baselines,
  profile-to-plan mapping, and metadata/statistics quality.
- Active roadmap, code-audit, handoff, and optimizer-contract docs now mark the
  CTE optimizer baseline as current: funnel labels, CTE shape facts,
  single-CTE predicate pushdown, pass-through CTE elimination, and the
  remaining inlining/broader-simplification work.
- Roadmap now explicitly tracks CTE simplification as future optimizer work:
  single-use CTE inlining, broader pass-through variants, internal expanded
  CTE analysis, and recommendations-only handling for risky multi-use CTEs.
- Roadmap and audit docs now record the optimizer conversion-funnel finding from
  recent stats-available candidate testing: candidate detection is ahead of
  safe SQL draft production, and the next work should deepen Python-owned CTE
  facts and split recipe detection from draft eligibility.
- README, demo, roadmap, architecture, audit, and optimizer-contract docs now
  describe the current `Diagnose` / `Recent queries` / `Known Query ID`
  workflow and the hidden standalone Query Optimizer route instead of the older
  separate-page UI map.
- Help now mirrors the current Diagnose flow: task-first quick start, `Recent
  queries` / `Known Query ID` wording, no standalone optimizer entry, and
  curated external links to the maintained GitHub documentation.
- Public and contributor docs were consolidated around the package-first
  workflow, current safety contract, active architecture, Query Optimizer
  contract, synthetic demo flow, and release/public-readiness checklists.
- Documentation now distinguishes current Apache Impala-only behavior from
  roadmap seams for future Big Data SQL/lakehouse engines, storage/table-format
  context, source providers, and future Cluster Doctor workflows.
- Active docs now expand Cloudera Manager on first meaningful use while keeping
  `CM` shorthand in dense technical sections.
- `docs/codex-handoff.md`, `docs/code-audit.md`, and
  `docs/development-practices.md` are the current agent-facing engineering
  baseline for larger, safety-sensitive, web, report, optimizer, collector, or
  architecture work.
- Agent-facing baseline docs were refocused: `docs/codex-handoff.md` now
  summarizes the current code map and safety invariants, `docs/code-audit.md`
  tracks only active risks, and `docs/query-optimizer-contract.md` separates the
  optimizer trust contract from historical implementation phases.
- `docs/roadmap.md` and `docs/project-audit.md` were reduced to active product
  direction and product-level risks, while detailed historical triage moved out
  of the active agent reading path.
- Architecture and demo docs now describe Cloudera Manager Events as bounded
  runtime context alongside Cloudera Manager metrics, without treating events as
  standalone root-cause evidence.
- Added agent playbooks, a focused test matrix, and `scripts/agent_preflight.py`
  so coding agents can map changed paths to required reading, validation, and
  changelog expectations without reading the whole repository first.
- Added a practical code map, fixture index, and active-doc drift checker so
  agents can find ownership boundaries, choose fixtures, and catch stale
  guidance before implementing feature work.

## 2026-05-04 - Current baseline

### Product

- Finished Queries is the primary workflow for completed-query triage, with
  ranked deterministic results and explicit details actions.
- Running Queries follows the Finished Queries result/details shape for queries
  running now, without scan date or scan hour filters.
- Specific Query analyzes one explicit Query ID without automatic LLM use,
  clears the input after submit, and appends each run to its result table.
- Details pages show deterministic analysis details plus explicit actions for
  LLM Report and Query LLM optimizer.
- Query Optimizer remains a separate pasted-SQL workflow: read-only parse and
  deterministic guidance only, no SQL execution, and no submitted SQL echo after
  submit.

### Safety

- LLM Report output is trusted only after validation. Partial or rejected reports
  stay hidden from trusted browser rendering.
- LLM Report contract was tightened around Python-owned facts and Python-owned
  recommendation candidates: the model phrases supported findings, while Python
  owns allowed facts and actions.
- Browser-visible UI and trusted reports continue to avoid raw SQL, raw profiles,
  raw metadata, local paths, subprocess output, secrets, runtime internals, and
  raw artifact filenames.
- Spill evidence detection requires explicit non-zero spill/scratch counters;
  general write metrics such as `WriteIoBytes`, `BytesWritten`, and
  `HDFSBytesWritten` are not treated as spill evidence.

### Optimizer

- Query LLM optimizer was added as an explicit details-page action for
  server-owned analyzed cases.
- Recent scan Optimization candidates now include deterministic rewrite-support
  status, separating SQL-draft-supported/attemptable candidates from guidance-only
  follow-up cases.
- Query optimization candidate scoring now requires operator-level JOIN evidence
  for JOIN-expansion reasons, lowers confidence when metadata is missing, and
  carries backend data-skew evidence into distribution/hot-key review areas.
- Optimizer source extraction supports safe SELECT/WITH payloads from supported
  INSERT/CTAS cases while keeping generated drafts read-only.
- Optimizer validation rejects unsafe SQL and result-shape changes such as added
  physical tables, removed filter scope, projection changes, DISTINCT changes,
  top-level GROUP/ORDER/set-operation changes, CTE shape changes, and top-level
  JOIN shape changes.
- Optimizer risk classification distinguishes normal rewrite attempts from
  conservative rewrite mode for structurally risky queries.

### Documentation

- Help and core workflow documentation were refreshed to match the current
  Finished, Running, Specific, Details, LLM Report, and Query Optimizer flows.
- `docs/project-audit.md` and `docs/code-audit.md` were added as current planning
  and engineering-risk references.
