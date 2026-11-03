# Codex Handoff

Last reviewed: 2026-11-03

This is the public-safe agent baseline for Query Doctor. It records durable
product, safety, and engineering context only. Transient continuation notes,
current branch plans, workstation-specific smoke details, private cluster IDs,
temporary output paths, and chat-local reminders belong in ignored
local exclude-only note files, not in committed documentation.

## Baseline

- Query Doctor is a local-first Big Data query diagnostic tool focused on
  Apache Impala production triage, with bounded local Trino production lanes.
- Treat it as an engineering diagnostic product, not a chat wrapper.
- The full production triage engine is Impala. Trino production support is
  bounded to the local raw-free lanes listed in the support matrix. Keep the
  minimal future engine/provider seams that already exist, but do not add fake
  support for other engines or managers.
- Current engine support, fixture-only, and research statuses are tracked in
  [engine-support-gap-matrix.md](engine-support-gap-matrix.md). Use that matrix
  before changing support wording or second-engine wiring.
- Trino is implemented only for the matrix-listed bounded raw-free imports,
  compact tools, and local production web lanes. Those lanes may materialize
  raw-free Details, deterministic Python Report, and optimizer guidance; the
  aggregate-only metadata CLI summary remains outside product metadata
  collection. Do not expand Trino into Running, broad query-history crawling,
  product metadata collection, LLM reports, Query Optimizer jobs, generated
  SQL, user SQL execution, or broader/shared production support without a
  separately implemented and validated promotion slice.
- Recent scan is the primary workflow.
- Query ID diagnosis is secondary for one known query.
- Query Optimizer is separate for pasted SQL analysis and deterministic
  candidate guidance.
- Known Query ID analysis may generate the deterministic Python Report as part
  of its explicit submit job. LLM reports and Query Optimizer outcomes remain
  explicit selected-case actions.

## Safety Baseline

- Never execute user SQL or optimizer draft SQL.
- Never echo pasted Query Optimizer SQL back into the browser after submit.
- Trusted browser/report surfaces must not expose raw SQL, raw profile text,
  raw metadata, local paths, `case_dir`, command output, secrets, model names,
  runtime internals, or raw artifact filenames. The isolated owner-only
  selected-case source surface is the narrow raw-SQL browser exception and must
  follow `docs/safety-contract.md`.
- Shared or non-local `owner_raw` must gate raw source access on authenticated
  per-request viewer identity from `viewer_identity_header` behind a trusted
  auth front door that strips inbound copies and sets exactly one normalized
  simple owner value. Query Doctor must not grow native OIDC, SAML, SPNEGO,
  Kerberos, LDAP, password, MFA, session, group, RBAC, or token auth variants
  for owner-raw access. Do not gate raw reveal on the collection credential or
  keytab owner set. Use
  [owner-raw-d3-deployment.md](owner-raw-d3-deployment.md) as the D3 deployment
  checklist before changing shared owner-raw behavior.
- Keep the isolated owner-raw source surface behind its kill switch and
  raw-free reason-coded audit line; never audit raw SQL, query ids, case ids,
  users, paths, header values, or secrets.
- Never state root causes without direct support in deterministic analysis
  facts.
- Treat raw LLM output as untrusted until deterministic validation accepts it.
- Trusted SQL drafts require a Python-owned recipe, deterministic execution, and
  strict validation.
- Metadata collection must stay read-only, allowlisted, bounded, explicit, and
  redacted.
- Web Recent scan and Running scan workflows must not auto-run LLM reports or
  optimizer jobs.

See [safety-contract.md](safety-contract.md) for the full trust and redaction
contract.

## Current Impala Support

- Cloudera Manager remains the full Recent discovery/profile/metrics/events
  provider for Impala workflows.
- Direct Impala daemon collection supports bounded Recent scans, Running scans,
  and one explicit Known Query ID.
- Direct Impala has no Cloudera Manager events.
- Direct Impala can optionally collect bounded Prometheus runtime metrics when
  explicitly configured.
- Direct JSON profile, `/profile_docs`, and `/admission?json` probes are
  optional compatibility surfaces. Missing old-cluster endpoints must degrade to
  unknown or not-configured unless the user explicitly requires that source.
- Analyzer-owned direct/profile facts include Profile Format, Source
  Provenance, Profile Resource Facts, Profile Timing Facts, Client Fetch Tail
  Facts, Memory Pressure Evidence, safe profile capability/counter-stability
  context, and Runtime Diagnosis resource/timing signals. Keep them raw-free and
  do not backfill fake metrics or events.

Current-upstream Impala compatibility work must stay direct-Impala-only and
bounded through ignored local config and daemon endpoints. Public docs may
describe the generic smoke workflow, but local cluster selectors, port-forward
endpoints, generated cases, query IDs, output directories, and per-chat
continuation plans belong in local exclude-only notes.

## Engine Expansion Boundary

- Apache Impala remains the only full production triage engine. Trino has the
  bounded local production lanes listed in the support matrix.
- The normalized engine-fact projection is a raw-free contract seam, not the
  product engine registry and not a support claim.
- Use [engine-support-gap-matrix.md](engine-support-gap-matrix.md) for the
  current support status and [code-map.md](code-map.md) for exact command,
  script, registry, and route ownership. Do not copy those inventories into
  this handoff.
- Trino is bounded to the raw-free import, compact diagnosis, source-contract,
  retained-handoff, and local production web surfaces listed in the support
  matrix. It is not broad production Trino triage, live query-history collection,
  LLM report output, Query Optimizer jobs, product SQL execution, or Query
  Doctor-generated Trino SQL. Trino Details, Trino Python Report, and Trino
  optimizer guidance are limited to the raw-free materialized local case facts.
- Spark is bounded to compact History Server intake for one explicit
  application, compact evidence-package build/validation/fixture export,
  compact diagnosis, and retained raw-free readiness/product-surface/support
  audits listed in the support matrix. The Spark adapter remains compact-only:
  no Recent workflow, Details/trusted report surface, optimizer behavior, broad
  live collector, raw event-log path, Spark job-execution path, or production
  Spark support claim.
- [engines/spark-test-cluster-evidence-checklist.md](engines/spark-test-cluster-evidence-checklist.md)
  records the durable Spark readiness boundary: bounded one-application intake
  can stay raw-free for compact summaries, and application-only
  `same_application` evidence can summarize readable application-level jobs,
  stages, scheduler delay, spill, and task-duration context without selected SQL
  execution linkage. SQL-execution-specific timing/failure facts still require
  accepted SQL execution evidence. Live validation notes and one-run checkpoints
  stay out of committed docs.
- Do not add broad public production support claims, broad live collection, engine
  registration beyond adapters explicitly listed in the support matrix, browser
  workflows beyond isolated compact pages and the Trino Recent/One Query ID
  local production lanes plus the raw-free materialized Trino Details view and
  Python Report plus optimizer guidance, LLM report output, Query Optimizer
  jobs, product metadata collection, query-history crawling, user SQL
  execution, or Query Doctor-generated SQL for a second engine without explicit
  implementation and validation.

## Second-Engine Change Gate

The shared `redaction_note_v1` contract is the baseline for package-style Trino
and Spark intake. Reuse the shared validators, JSON primitives, safe manifest
references, and handoff artifact helpers named in [code-map.md](code-map.md)
instead of copying schema or output logic.

Keep the machine-checkable capability manifest, source registries, fact
promotion policy, route registry, adapters, and support matrix aligned. Their
current owners live in [code-map.md](code-map.md); do not reproduce the command
or registry inventory here.

Trino and Spark changes remain independently owned. A feature slice for one
engine must not silently change the other's schema or support status. Shared
helper, schema, manifest-reference, capability, or cross-engine safety changes
are explicit synchronization slices with focused tests and documentation drift
checks.

Before editing either engine, run
`python3 scripts/agent_preflight.py --paths <planned-paths>` and follow the
matrix, redaction-contract, capability, and support-boundary routes it selects.

## Working Rules

Follow [agent-quickstart.md](agent-quickstart.md) as the canonical operational
contract for worktrees, staging, validation, commits, local `main` merges, and
completed-worktree cleanup.

The durable Git, validation, scope, and documentation invariants remain in
[../AGENTS.md](../AGENTS.md). This handoff does not duplicate their command
sequence.

## Documentation Boundary

Committed docs contain stable public contracts and sanitized aggregate
guidance. Current branches, workstation setup, private targets, raw evidence,
temporary paths, generated outputs, and continuation notes stay in ignored
local notes. Follow
[public-documentation-boundary.md](public-documentation-boundary.md) for the
policy and audit route.
