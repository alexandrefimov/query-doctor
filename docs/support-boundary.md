# Support Boundary

Last reviewed: 2026-08-11

What Query Doctor supports today, what it deliberately does not, and the
deployment shape it is supported in. The README keeps a three-line summary; this
is the full contract. For the engine-by-engine gap detail see
[engine-support-gap-matrix.md](engine-support-gap-matrix.md).

## What It Is

- a local-first Impala production triage workbench with official bounded local
  Trino production lanes;
- a deterministic evidence extractor;
- a Recent-query ranking workflow for operators and administrators;
- a safe report generator using validated facts;
- a practical tool for deciding what to inspect, change, and verify next;
- a containerized web application that can run as a read-only public demo or a
  configured private operator service behind a trusted ingress/auth proxy;
- a Big Data SQL/lakehouse diagnostics wedge whose full production triage engine
  is Apache Impala, with bounded raw-free local Trino production lanes and
  future-engine preview seams.

## What It Is Not

- a generic AI chatbot over raw profiles;
- a replacement for the Impala Web UI;
- a tool that executes user SQL or optimizer draft SQL;
- a tool that sends raw SQL/profile data to remote services by default;
- a root-cause oracle;
- a broad live multi-engine query-history collector.

## Current Surfaces

| Surface | Current status |
| --- | --- |
| Query engine | Apache Impala is the full production triage engine. Trino has bounded local production support only for the raw-free lanes named below. |
| First-value intake | One local exported Impala text profile can be uploaded from a local/private web session or staged from CLI/manual inbox, redacted, analyzed, and opened from Known Query ID. |
| Recent scan | Cloudera Manager is the full Recent discovery/profile/metrics/events provider for Impala workflows. |
| Direct Impala | Bounded Recent scans, Running scans, and one Known Query ID through impalad daemon endpoints; no Cloudera Manager events and no SQL execution. |
| Runtime metrics | Optional bounded Prometheus summaries for configured direct Impala workflows; no arbitrary PromQL from users. |
| Metadata | Read-only allowlisted Impala metadata statements over HiveServer2, or the equivalent read-only reads from the Hive Metastore's PostgreSQL database; no user SQL execution or unbounded metadata crawl. |
| Reports and optimizer | Python-owned facts and validation. Known Query ID prepares the deterministic Python report in its explicit submit job; LLM narratives remain explicit selected-case actions, and optimizer actions are shown only for cases with safe-to-attempt rewrite support. |
| Trusted SSO/auth proxy deployment | Query Doctor supports deployment behind a trusted SSO/auth proxy via `viewer_identity_header` for shared/non-local `owner_raw` access only after the raw-free D3 support-readiness gate passes. The proxy or ingress owns authentication, MFA, session lifecycle, token handling, and inbound-header stripping; Query Doctor only enforces the normalized viewer owner header against `query.user`. |
| Container/Kubernetes web deployment | Supported starting point through the official container image, `/healthz` and `/readyz` probes, raw-free deployment readiness summary, a read-only `public-demo` manifest, a configured private web manifest, a synthetic self-test Job, and the `deploy/helm/query-doctor` chart with a `helm test` hook. Kubernetes support does not add native auth, RBAC, sessions, multi-tenant isolation, an operator/CRD, arbitrary command running, SQL execution, or broader engine support. |
| Trino local | Local web Trino mode can read one bounded retained pruned coordinator query list for Recent diagnosis, then bounded pruned coordinator QueryInfo payloads for selected rows or one explicit Query ID, render deterministic compact diagnosis, materialize server-owned raw-free case artifacts, open a raw-free Details view, and generate deterministic Python Report plus optimizer guidance from those materialized case facts. No Running scans, query-history crawling, metadata collection, LLM report output, Query Optimizer jobs, generated Trino SQL, SQL execution, or broader/shared Trino production triage support. |
| Spark | Bounded compact support surfaces only. Not production engine support, live Recent scans, Details/trusted report output, optimizer behavior, raw event-log handling, Spark job execution, or Query Doctor-generated SQL. |

## Trino Detail

Trino compact/dev surfaces include offline or compact raw-free imports and
checks: sanitized evidence packages, bounded local compact imports, explicit
source-contract checks, a contract-gated local metadata CLI summary builder and
dev-only round-trip smoke gate that emit aggregate metadata coverage only, and
bounded pruned QueryInfo paths documented in the engine docs.

The only local production Trino product surfaces are local web retained-list
Recent diagnosis, One Query ID diagnosis, the raw-free Details view,
deterministic Python Report, and optimizer guidance for server-owned
materialized cases from those lanes.

Those lanes require `trino_support_mode=beta` or `trino_support_mode=production`,
`trino_coordinator_url`, and `trino_query_info_source_contract` in local config;
Recent also requires `trino_query_list_source_contract`. The legacy
`trino_beta_enabled=true` key remains beta-only for existing local setups and
must not be combined with `trino_support_mode=production`. Startup validation
checks local source contracts, safe coordinator URL shape, and optional auth
reference (`trino_auth_header_file` or local Kerberos/SPNEGO settings) before
the lane is marked configured.

Configured beta sources are marked as `Trino Beta Recent + One Query ID` or
`Trino Beta One Query ID`; configured production-mode sources are marked without
the beta label. The Diagnose Engine control narrows the Source cluster selector
to Impala-capable or Trino-ready sources before workflow selection, and stale or
forged Trino submits still fail closed before analysis or async job creation.

Coordinator URL, auth header references, raw QueryInfo, raw SQL, and local paths
stay out of the browser. Trino web case artifacts contain only the normalized
boundary, compact diagnosis, metadata-not-collected summary, typed analysis, and
safe analyzer facts view; Details opens only after those artifacts exist. Python
Report and optimizer guidance use the same raw-free facts and hide raw payloads,
query IDs, paths, LLM report output, Query Optimizer jobs, and generated SQL.

Broader/shared Trino live collection and broader Trino production triage remain
unsupported.

Spark compact support surfaces are limited to bounded compact History Server
intake, compact evidence-package build/validation, and compact diagnosis; there
is no public Spark engine support.

Future Big Data SQL/lakehouse live collectors, broader providers, prepared
event/log sources, and Cluster Doctor workflows remain roadmap seams, not
current support. For the preview command catalog see
[engines/README.md](engines/README.md).

## Direct Impala History Depth

Direct Impala Recent and Running scans currently see only the query history
exposed by the configured coordinator daemon query-list endpoints. Upstream
Impala keeps the coordinator query log at `--query_log_size=200` entries by
default, further bounded by `--query_log_size_in_bytes`. Operators who need
deeper direct history can increase those Impala daemon settings on each
coordinator, while watching coordinator Web UI memory and `/queries` response
latency.

Future deeper-history options are deliberately separate sources:
operator-managed read-only profile-log directory ingestion, or bounded external
history sources such as Loki or OpenSearch. They require explicit source
contracts, allowlists, byte/window bounds, and raw-free browser/report output;
the current product does not read coordinator filesystems, pod filesystems, or
external log indexes for direct Recent scans.

Apache Impala also has upstream work around native AI query profile analysis.
Query Doctor aligns with that direction by staying focused on local-first
production triage across many queries, deterministic evidence, safe enrichment,
and validated raw-free reports. See
[upstream-impala-ai-analyzer.md](upstream-impala-ai-analyzer.md).

## Supported Deployment Shape

Query Doctor is supported as a single-user, local-first tool run by an operator
with their own local Cloudera Manager, Kerberos, Impala, Prometheus, and LLM
credentials. Use localhost or a tightly controlled local bind for the web UI.

Do not deploy ordinary local mode as a shared service without a separate design
for authentication, authorization, tenant/job isolation, audit logging,
TLS/reverse-proxy trust, and resource limits. Shared public demos should use the
read-only `query-doctor-web --public-demo` mode.

Shared `owner_raw` source access requires authenticated per-request viewer
identity through an explicit `viewer_identity_header` supplied only by a trusted
SSO/auth proxy or ingress that strips inbound copies of the same header and sets
exactly one normalized simple owner value. Query Doctor supports that deployment
pattern after the raw-free support-readiness gate in
[owner-raw-d3-deployment.md](owner-raw-d3-deployment.md).

A dev-only Keycloak/oauth2-proxy smoke is available in
[dev-sso-keycloak.md](dev-sso-keycloak.md) to test the front-door viewer header
contract locally; `scripts/dev_sso_keycloak_smoke.py` verifies the running local
compose path with raw-free output. The dev smoke is not production SSO support
evidence and does not add native SSO to Query Doctor.
