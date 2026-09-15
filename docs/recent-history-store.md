# Recent History Store

Last reviewed: 2026-09-01

This page defines the durable storage boundary for Recent summary history and
profile-budget work. It is a storage contract, not an engine support claim.
Only sources already supported by the current Impala and bounded Trino product
lanes may write through this boundary.

## Current Implementation

`query-doctor-batch-recent` can opt in to raw-free summary history with
`--recent-history-backend`. The supported backends are:

- `sqlite`, selected automatically when `--recent-history-db` or
  `recent_history_db` is set. SQLite is intended for local runs, development,
  smoke tests, and single-pod deployments that need a dependency-free durable
  summary index.
- `postgres`, selected explicitly with `--recent-history-backend postgres` or
  `recent_history_backend=postgres`. It reads its DSN from the environment
  variable named by `--recent-history-postgres-dsn-env` or
  `recent_history_postgres_dsn_env`, defaulting to
  `QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN`. The DSN value itself must stay out
  of Query Doctor JSON config.

The Postgres adapter requires installing the optional `postgres` extra, for
example `pip install 'query-doctor[postgres]'`, or building the container image
with `QUERY_DOCTOR_INSTALL_EXTRAS=postgres`.
Run `query-doctor-recent-history-postgres-readiness --json` in the target
runtime environment to check that the DSN env is configured and the schema can
initialize. The readiness output is raw-free: it reports only status, check
ids, issue codes, and aggregate booleans, not the DSN value, hostnames,
credentials, local paths, Query IDs, or raw payloads.
Run `query-doctor-recent-history-operator-readiness` over retained raw-free
Postgres readiness and profile-worker summary JSON files, plus the optional
collector, retention, and profile-remediation summaries, before an operator
handoff. This audit validates only those already written summaries, rejects
unsafe retained fields or values, and emits one path-free readiness summary
with accepted raw-free operation counters for schema readiness, collector
producer status/freshness, profile-worker jobs and materialized records,
profile backlog health, optional retention deletes, and optional remediation
dry-run/apply counts. It does not contact Postgres, Kubernetes, query engines,
profile collectors, or remediation actions.
`--max-evidence-age-minutes` turns the collector and profile-worker producer
timestamps from reported fields into gates: without it, a producer that stops
writing keeps its last acceptable summary on disk and the audit keeps reading
the retained contents. The audit also blocks when profile-backlog health shows
stale leases or terminal failed jobs. Pending and retry-pending work remains an
operational workload signal rather than a readiness failure by itself.
In Helm configured mode, `recentHistory.operatorReadiness.enabled=true` renders
that audit as a separate CronJob after Postgres history, Postgres readiness, and
the Recent profile worker are enabled. The chart has the Postgres readiness
initContainer, profile-worker CronJob, and optional retention CronJob write
their raw-free summaries to the case PVC. When
`recentHistory.operatorReadiness.collectorSummaryJson` is set, the audit can
also include the raw-free scheduled collector producer summary from the PVC.
The optional
`recentHistory.postgres.profileRemediation` CronJob can also write a
dry-run-only raw-free remediation summary to the same PVC for that audit; the
chart does not render an apply/requeue remediation job. The operator-readiness
CronJob mounts only that PVC and writes the final handoff summary for the web
`recent_history_operator_readiness_summary_json` projection.

The store records only normalized summary signals:

- source labels, Query ID, timestamps, duration, status, query state, user,
  pool, query type, and SQL verb;
- summary metrics such as admission wait, rows, bytes, and memory when the
  source provides them;
- selected/suspicion reason codes and profile collection status.

It does not retain raw SQL, raw profile text, raw metadata, subprocess output,
local case paths, or the SQLite path in trusted summaries. Batch summaries
expose only aggregate history-store status, recorded-summary count, and planned
profile-job count.

When `query-doctor-batch-recent --discover-only` runs with a configured history
backend, it also plans pending `recent_profile_job` rows for suspicious or
selected summaries under the existing Recent profile budget. Full batch runs do
not enqueue duplicate background jobs because they already collect and analyze
their selected cases synchronously.

When the local web UI is configured with the same history backend, Query Inbox
can load retained summary payloads into a read-only Online History surface.
Postgres-backed web read does not create or update schema objects; run the
Postgres readiness command first, or use the Helm initContainer that runs it.
If the schema is not ready, the page degrades to its safe unavailable state.
The default `Details ready` view selects the newest retained summaries that
have both compatible analysis-cache data and profile-artifact metadata, then
applies the bounded page limit. This keeps openable analyst cases visible even
when a larger set of newer summaries is still waiting for profile analysis.
The Postgres read chooses the latest available artifact key for each retained
query once before joining its exact ready cache row; it remains DDL-free.
`All recent` remains available as a separately bounded newest-summary view. It
labels rows as queued, analyzing, failed, unselected, or Details unavailable
when they cannot open a safe snapshot. Both views use only raw-free summary
fields and do not expose raw SQL, profile text, local store paths, or raw
artifact names.
The view also overlays the current Recent profile worker lifecycle state from
the history store, so pending, processing, retry-pending, analyzed, and failed
states can appear in raw-free row status, Query Inbox profile-loop metrics,
separate profile-state counts, normalized profile-worker error-code rollups,
Details-ready counts, and coverage counts. When history retention exceeds the
first rendered projection, the status banner distinguishes retained rows from
either Details-ready rows shown or newest rows shown. The same banner shows
safe Recent summary collector freshness from the latest retained planning
timestamp in `All recent`. The filtered `Details ready` view does not infer
collector freshness from older ready cases, so it cannot falsely label a healthy
producer as stale. This keeps discover-only producer health separate from
profile-worker backlog health without exposing Query IDs, source keys, local
paths, or raw collector payloads.
When the web config points `recent_history_collector_summary_json` at an
already retained `query_doctor_recent_history_collector_v1` summary, the banner
also projects only allowlisted producer run status, age, recorded-row, and
planned-job counters. Invalid, wrong-kind, or unsafe summaries degrade to safe
blocked/unavailable status without exposing the configured path, raw JSON,
Query IDs, source keys, local paths, raw errors, or retained free-form text.
Safe next-step labels are derived from those retained states and aggregate
counters, not from retained free-form text, so the browser can guide retry,
failed, waiting, materialized backlog, and stale producer handling without
exposing raw errors.
When a retained operator readiness summary is configured, the banner
also shows the allowlisted collector producer state when present, aggregate
profile-backlog health for pending, retry-pending, leased, stale leased, and
terminal failed jobs, plus counter-derived collector and backlog next steps.
Rediscovered summary rows do not reset a progressed worker state back to
not-collected.
An analyzed row becomes an Online History Details link only when a ready
`recent_analysis_cache` payload is linked through available `fingerprint_only`
profile-artifact metadata for that retained summary. The Details snapshot is
built from retained raw-free summary fields plus allowlisted score,
recommendation, aggregate metadata coverage, and processing timing fields from
that cache payload. It does not expose raw profile bytes, profile fingerprints,
storage keys, local paths, LLM reports, optimizer jobs, generated SQL, or SQL
execution.
History Details links use an opaque source-and-query identity rather than a
row position. New results and view switches do not change the selected query
or the target of rerun feedback. If the selected result leaves the displayed
history window, its link fails closed instead of opening a replacement row;
select it again from the current History view.
Refreshing that history-backed inbox from the web path uses a discover-only
Recent scan so it updates retained summaries and profile-job planning without
running LLM reports, optimizer jobs, generated SQL, SQL execution, metadata SQL
collection, or synchronous profile analysis.

`query-doctor-recent-profile-worker` is the bounded shared worker for those
jobs. It claims only jobs matching the configured engine/source/source key,
orders equal-priority claims from freshest to oldest so profiles are collected
before daemon retention can evict them, renews the lease before processing,
collects profiles through the existing Impala Recent collector path, runs
deterministic analysis with the configured bounded metadata mode, and writes
only raw-free `recent_analysis_cache` and
`recent_profile_artifact` metadata. A claimed job is marked completed, and the
retained summary is marked analyzed, only after both the raw-free analysis cache
record and `fingerprint_only` profile-artifact metadata are accepted. Incomplete
or unsafe materialization outcomes fail with a normalized worker error code
instead of creating an analyzed row without a Details-ready snapshot. When a
retry budget is exhausted, the terminal code preserves the normalized root
category and adds a retry-exhausted suffix so remediation can distinguish
collection, timeout, and materialization failures. Canonical raw-free HTTP
collection failures retain `profile_fetch_http_<status>` codes without keeping
the reason text. Direct Impala collection preserves the last attempted
endpoint's bounded HTTP status or typed timeout through the CLI and batch
collector; canonical endpoint timeouts retain `profile_fetch_timeout`.
Transport failures, invalid responses, and HTTP failures are not reported as
proof that a query profile is missing. Missing or noncanonical reasons keep the
generic code. Existing collector retry rules now see direct HTTP statuses:
4xx does not restart the collector, while 5xx and timeouts remain retryable.
Endpoint fallback order, timeout values, retry ceilings, worker retry policy,
and readiness gates are unchanged. After
each processed job, the worker removes only the worker-owned temporary
`profile-worker-cases/job-*` directory it created for that job. The worker does
not run LLM reports, Query Optimizer jobs, generated SQL, metadata SQL
collection, Running scans, browser raw-source rendering, or raw profile storage
in the history database. Its raw-free summary JSON includes a bounded
counter-derived next step for idle, retrying, failed, lease-lost, or newly
materialized runs; the value is selected from safe constants and does not echo
profile errors, query identifiers, local paths, or retained free-form text.
The worker replaces this summary atomically, so overlapping worker Pods cannot
leave operator readiness with a partial JSON document.
The same summary includes aggregate profile-backlog health counts for pending,
retry-pending, leased, stale leased, and terminal failed jobs in the configured
source scope, plus a counter-derived backlog next step. Those counts do not
include Query IDs, lease owners, source filter values, retained error values,
local paths, or raw profile artifacts.
Direct Impala summary discovery also promotes completed-query-log continuity
from free-form warning text into a raw-free collector contract. When a daemon
log is full, the collector compares the oldest retained completion time with
the previous valid collector observation. An overlap records `confirmed` and
remains ready; a forward gap records `impala_query_log_gap_detected`, while a
missing or unreadable boundary records
`impala_query_log_continuity_unproven`. The latter two states use a warning
collector summary, so normal capacity use stays operational while a possible
history hole cannot appear fully ready. Previous-summary reads are byte-bounded
and accept only the expected raw-free summary kind.
`query-doctor-recent-profile-remediation` is the bounded maintenance command
for failed backlog recovery. It defaults to dry-run, requires explicit
`--apply` before mutating storage, selects only terminal failed profile jobs,
resets selected jobs back to pending with a fresh attempt budget, and uses
optional engine/source filters plus a per-run limit. It does not contact query
engines, collect profiles, run metadata SQL, run LLM reports, run optimizer
jobs, or expose Query IDs, source filter values, retained error codes, DSNs,
local paths, raw SQL, profile text, or raw artifact names in its terminal or
summary JSON output.

For the Postgres backend, both dry-run and apply use the existing history
schema without creating or updating schema objects. Prepare the schema through
the explicit Postgres readiness command before maintenance. A missing or
inaccessible schema blocks remediation with raw-free failure output; it does
not trigger schema initialization. Direct library callers retain the schema
preparation default and can use `prepare_schema=False` for DDL-free requeue.

## Backend Contract

`query_doctor.recent.history_store` owns the backend-neutral contract:

- `RecentSummaryHistoryRecord` is the raw-free storage record.
- `RecentHistoryStoreBackend` is the minimal backend protocol.
- `persist_recent_history_with_store` converts discovered candidates into
  records and writes them through the backend.
- `query_doctor.recent.profile_budget` owns `RecentProfileJobRecord`, the
  profile-budget store protocol, the deterministic planner that ranks already
  retained summaries into pending jobs, and the aggregate failed-job requeue
  result contract.
- `query_doctor.recent.sqlite_history_store` is the SQLite adapter.
- `query_doctor.recent.postgres_history_store` is the optional Postgres adapter.

Future backends should implement the same record contract instead of changing
Recent discovery or batch summary ownership. Backend failures must stay
path-free in batch summaries and browser-visible output.

## CNPG/Postgres Target

Configured Kubernetes production should use a CNPG/Postgres backend before
Query Doctor starts online profile budgeting or shared background workers.
SQLite should not become the multi-worker production database.

The current Postgres slice covers the raw-free `recent_query_summary` table,
Secret/env based connection settings, Helm Secret-key wiring for configured
mode, dependency policy, container optional-extra build support, unit coverage,
the raw-free `recent_profile_job`, `recent_analysis_cache`, and
`recent_profile_artifact` schemas, and a deterministic profile-budget planner
over already retained summary records. It also includes backend claim semantics
for atomically leasing pending or expired profile jobs, optional source-filtered
claiming, owner-guarded lease renewal, completion, retryable failure, and
terminal failure transitions. The analysis cache has a backend API for
upserting and loading raw-free analyzer payloads by Query ID, profile
fingerprint, and analyzer contract; dangerous raw-key payload fields are
scrubbed before storage. The profile artifact table stores only compatibility
keys, status, size, and an opaque storage key; it does not store raw profile
bytes, local paths, or artifact filenames. The Helm chart can optionally render
a CloudNativePG `Cluster` resource for configured mode. It can reference the
standard CNPG-generated application Secret and its `uri` key directly, while
still supporting external owner credentials plus a separate DSN Secret. The
CNPG operator remains external and the chart does not render Secret objects.
The chart can also render an optional configured-mode `recentProfileWorker`
CronJob after Postgres history is enabled. That CronJob uses the same config,
credential Secret, Kerberos cache settings, case PVC, and Postgres DSN Secret
as the web pod; it runs `query-doctor-recent-profile-worker` with metadata
collection controlled by the configured bounded mode,
top reports disabled, and raw-free JSON output. Backends also expose explicit
retention pruning for old summaries, terminal profile jobs, analysis-cache
records, and profile-artifact metadata through batch config/CLI retention-day
settings or the standalone `query-doctor-recent-history-retention` maintenance
CLI; output returns aggregate delete counts only and does not delete pending or
leased jobs. The Helm chart can render an optional configured-mode Postgres
retention CronJob that uses only the DSN Secret environment variable and does
not mount Query Doctor config, collection credentials, Kerberos material, or
case PVCs. The Postgres readiness CLI verifies Secret/env handoff and
schema initialization without printing sensitive connection details, and the
Helm chart runs it as a web pod initContainer by default when configured-mode
Postgres history is enabled. The Helm chart can also render a configured-mode
`recentSummaryCollector` CronJob that runs
`query-doctor-batch-recent --discover-only` to write retained raw-free summary
rows and planned profile jobs to Postgres, plus a raw-free
`query_doctor_recent_history_collector_v1` producer summary and progress JSONL
stream to the case PVC, without collecting profiles, running metadata SQL, LLM
reports, optimizer jobs, or readiness audits. The shared
profile worker can process claimed jobs from SQLite or Postgres/CNPG, cleans
its own temporary local
`profile-worker-cases/job-*` directories after processing, and currently emits
only `fingerprint_only` profile-artifact metadata. Storage kinds that would
retain profile bytes, local paths, object names, or external artifact
references are rejected until a bounded delete implementation exists. The
retained operator readiness audit can combine
the raw-free Postgres readiness, profile-worker, optional collector, optional
retention, and optional profile-remediation summaries into one path-free
handoff summary with accepted operations counters, including collector
producer state and profile-backlog health from the worker summary, without
re-opening the raw artifacts, contacting live services, or running remediation.
The Helm chart can render a
Postgres-only dry-run `recentHistory.postgres.profileRemediation` summary
CronJob that writes the optional remediation evidence to the case PVC, but it
does not render an apply/requeue remediation job. The Helm chart can render the
same audit as an optional configured-mode `recentHistory.operatorReadiness`
CronJob that reads only those retained raw-free summaries from the case PVC and
does not mount Query Doctor config, collection credentials, the Postgres DSN
Secret, Kerberos material, or source endpoint configuration. Configured web
installs can set
`recent_history_operator_readiness_summary_json` to that already retained
`query_doctor_recent_history_operator_readiness_v1` summary so Online History
shows only allowlisted readiness, evidence, reason-code labels, schema,
worker, profile-backlog, retention, and profile-remediation counters in the
Query Inbox status banner. Invalid, wrong-kind, or unsafe summaries degrade to
a safe blocked/unavailable status without exposing the configured path, raw
JSON, raw SQL, profile text, local store paths, retained free-form remediation
text, or raw artifact names.

For an intentional configured staging release, the bounded
`scripts/kubernetes-online-history-smoke.sh` gate creates one temporary Job
from each installed collector, worker, and operator-readiness CronJob, waits in
that order, and verifies the raw-free Online History projection through a
local port-forward. It prints no Job logs or page contents and removes only its
own temporary Jobs. Keep all three schedules suspended during this isolated
cycle and enable them only after it passes. This is a pre-release environment
gate, not a local unit test and not authorization to run collection against an
unapproved cluster.

The first Postgres-backed feature should be profile-budgeted local retention:
store all summaries, schedule only suspicious or representative profile-fetch
jobs within the configured budget, and reuse already analyzed profiles by Query
ID and compatibility contract. The current planner, discover-only enqueue path,
claim path, worker lifecycle, and analysis cache now cover the raw-free job
queue and analyzed-result metadata. The profile-artifact table can retain and
prune raw-free metadata for selected profile artifacts by compatibility key.
These pieces still do not store raw profile bytes in the history database,
retain local/object artifact references, or run LLM/optimizer work from Recent
jobs.
