# Trino Shared Deployment Hardening

Last reviewed: 2026-11-03

This is the shared/non-local Trino deployment hardening contract, not a support
claim. It describes the extra boundary that must hold before an operator can
put the existing local Trino web lanes behind a shared internal front door.
It does not promote Trino into broader/shared Trino production support.

The current source of truth for implemented Trino support remains
[engine-support-gap-matrix.md](engine-support-gap-matrix.md). This document
only adds the shared deployment guardrail for those already implemented local
lanes.

## Scope

`trino_support_mode=production` remains local production support only for the
bounded raw-free retained-list Recent lane, One Query ID lane, raw-free
materialized Details, deterministic Python Report, and optimizer guidance.
Those surfaces may use one bounded retained pruned coordinator query-list read,
bounded selected pruned QueryInfo reads, or one bounded pruned QueryInfo read
according to the configured local source contracts.

Shared deployment hardening must not add:

- Running scans
- query-history crawling
- product metadata collection
- LLM reports
- Query Optimizer jobs
- generated SQL or Query Doctor-generated Trino SQL
- SQL execution
- broader/shared Trino production support

## Identity Boundary

Shared or non-local Trino web deployment requires trusted front-door viewer
identity. Query Doctor may only rely on the normalized owner value supplied by
`viewer_identity_header` after a trusted auth front door strips inbound copies
of that header and sets exactly one simple owner value for each request.
Because Query Doctor cannot prove that front-door behavior from its local
config, the shared hardening gate also requires the operator to pass
`--trusted-front-door-reviewed` for shared/non-local configs after verifying
the front door strips inbound copies of `viewer_identity_header`, authenticates
the request before it reaches Query Doctor, and injects exactly one normalized
simple viewer value.

Query Doctor must not add native OIDC, SAML, SPNEGO, Kerberos, LDAP, password,
MFA, session, group, RBAC, or token authentication variants for this boundary.
It must not gate raw source reveal on the Trino collection credential, keytab
owner, service account, or operator identity.

Use [owner-raw-d3-deployment.md](owner-raw-d3-deployment.md) for the generic
`owner_raw` front-door checklist. The Trino-specific rule is stricter for
shared deployment: raw Trino source reveal stays isolated and disabled.

## Source Isolation

The preferred shared Trino web configuration keeps `source_visibility=safe`.
If an operator has any `owner_raw` configuration in the same shared deployment,
Trino shared hardening requires `owner_raw_source_enabled=false` for that
deployment before the Trino gate can pass.

Shared Trino must not reveal raw source data, raw QueryInfo or query-list
payloads, Query IDs, source-contract paths, auth-reference paths, coordinator
URLs, local paths, users, header values, object identifiers, metadata values,
CLI stdout/stderr, or raw payloads in browser, report, optimizer, audit, or
summary output.

Trino Details, Python Report, and optimizer guidance remain raw-free
materialized Details, deterministic Python Report, and optimizer guidance over
server-owned materialized web cases. They must not reopen raw QueryInfo,
query-list, metadata, CLI, or source-contract inputs.

## Metadata Boundary

The metadata CLI summary smoke is dev-only. It may run only through explicit
operator metadata inputs, an accepted allowlist, an operator-installed Trino
CLI, and the existing Python-owned read-only metadata statement builder. It
may write raw-free smoke and aggregate summary artifacts only after explicit
redaction review.

The metadata CLI summary smoke is not product metadata collection. It must not
feed Recent, Running, Details, Python Report, optimizer guidance, LLM reports,
Query Optimizer jobs, browser output, generated SQL, or SQL execution.

## Audit Path

Run the shared deployment preflight for every shared/non-local Trino hardening
review:

```bash
python3 scripts/audit_trino_shared_deployment_preflight.py
python3 scripts/audit_trino_shared_deployment_preflight.py --config <ignored-local-web-config.json>
python3 scripts/audit_trino_shared_deployment_preflight.py --config <ignored-local-web-config.json> --trusted-front-door-reviewed
python3 scripts/audit_trino_shared_deployment_preflight.py --config <ignored-local-web-config.json> --front-door-review-summary <raw-free-front-door-review.json>
```

The preflight wraps the shared deployment boundary audit, product-surface
boundary audit, support-gap audit, and active-docs check. When
`--front-door-review-summary` is supplied, it first
validates that raw-free operator review summary with
`scripts/audit_owner_raw_live_front_door_review.py --require-trino-shared-hardening`
and then passes the trusted front-door review confirmation to the shared
boundary audit. It captures child stdout/stderr and emits only raw-free gate
names, counts, and failure categories. It performs no coordinator network read,
metadata collection, SQL execution, live smoke, or UI smoke.

The front-door review summary is the retained evidence form for a real
Kubernetes/proxy deployment review. The private commands that prove TLS/auth,
direct-upstream blocking, inbound header stripping, exactly-one normalized
viewer injection, raw identity token dropping, and negative request cases stay
in ignored local notes. Start from the fail-closed raw-free template:

```bash
python3 scripts/audit_owner_raw_live_front_door_review.py --template-json <raw-free-front-door-review.json> --require-trino-shared-hardening
```

The template uses `review_status=unreviewed` and false proof fields, so it must
not pass until the operator edits only the supported boolean and enum review
fields after the live proxy checks complete. The retained JSON must not contain
real URLs, paths, users, header names or values, query ids, case ids, auth
subjects, tokens, cookies, tickets, assertions, screenshots, SQL, proxy logs,
or source text.

Use the narrower shared deployment boundary audit when only the boundary
config shape needs direct inspection:

```bash
python3 scripts/audit_trino_shared_deployment_boundary.py
python3 scripts/audit_trino_shared_deployment_boundary.py --config <ignored-local-web-config.json>
python3 scripts/audit_trino_shared_deployment_boundary.py --config <ignored-local-web-config.json> --trusted-front-door-reviewed
```

The static boundary invocation checks capability, release-gate, and
documentation drift. The config invocation additionally checks the ignored
local web configuration shape. Both forms emit only raw-free counts and issue
categories. For local-only binds the review flag is not required; for
shared/non-local Trino configs it is required in addition to
`viewer_identity_header`.

The boundary audit summary records raw-free
`shared_deployment_requirement_tracking` entries and
`shared_deployment_requirement_tracking_counts` for accepted, missing,
invalid, and not-required deployment-config, product-boundary, capability,
release-gate, and documentation requirements. It also records the
`production_review_shared_deployment_v1` profile with
`production_review_tracking_counts` for review-family, deployment-config,
product-boundary, capability, release, documentation, and unsupported-surface
block coverage. The CLI prints the matching path-free
`shared_deployment_requirements` and production-review counts without printing
config paths, header names, users, Query IDs, coordinator URLs, auth
references, source-contract paths, raw payloads, or child command output.

For local release handoff, keep the release-readiness bundle as the preferred
one-command gate:

```bash
python3 scripts/audit_trino_beta_release_readiness.py --config <ignored-local-web-config.json> --selected-query-limit 1
python3 scripts/audit_trino_beta_release_readiness.py --config <ignored-local-web-config.json> --selected-query-limit 1 --trusted-front-door-reviewed
```

Use the `--trusted-front-door-reviewed` release-readiness form only for
operator-reviewed shared/non-local configs. Do not use it as a workaround for a
missing or untrusted front door.

When no intentional local Trino source is available, the bundle's
`--static-only` mode is acceptable for developer drift checks only. It is not a
demo or release substitute.

If operator metadata inputs are available, the bundle may include the optional
metadata CLI summary smoke only with `--metadata-smoke-redaction-reviewed` and
the explicit `--metadata-smoke-*` inputs. Do not commit the local config,
metadata source contract, output summaries, paths, hostnames, credentials,
Query IDs, object identifiers, or raw smoke output.

## Evidence Boundary

Retained evidence from this gate must be limited to raw-free summary JSON and
path-free validation notes, including only the raw-free requirement-tracking
counts, production-review counts, and per-requirement status entries described
above. Do not retain or publish local config, generated cases, raw QueryInfo
payloads, raw query-list payloads, raw metadata values, CLI stdout/stderr,
local paths, URLs, auth references, users, header names, Query IDs, object
identifiers, or credentials.

README changes are not required for this hardening layer unless the user-facing
workflow, first-screen UI, CLI quickstart, or support claim changes. The shared
deployment audit and this contract do not change those surfaces by themselves.
