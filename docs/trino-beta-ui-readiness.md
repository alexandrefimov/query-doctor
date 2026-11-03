# Trino Local Web Readiness

Last reviewed: 2026-11-03

This checklist defines what Query Doctor may show as the local production Trino
web surface. Trino Beta remains the legacy label for existing local configs.
It is a product-readiness gate for bounded local support, not broad Trino
production engine support.
The source of truth for broader engine status remains
[engine-support-gap-matrix.md](engine-support-gap-matrix.md).
The same checklist applies when `trino_support_mode=production` is configured:
the web UI may omit the beta label, but the bounded Recent and One Query ID
surface, blocked actions, and raw-free output contract are unchanged.

## Showable Surface

The local web UI may show a `Trino Beta` or `Trino` engine option for bounded
retained-list Recent diagnosis and for one explicit Query ID when the selected
local source is configured with the required settings:

- `trino_support_mode=beta` or `trino_support_mode=production`
- `trino_coordinator_url`
- `trino_query_info_source_contract`
- `trino_query_list_source_contract` for Trino Recent
- optional `trino_auth_header_file`

Legacy `trino_beta_enabled=true` maps only to beta mode for existing local
configs and must not be combined with `trino_support_mode=production`.
When `One Query ID` is selected, the Trino lane may perform one bounded pruned
coordinator QueryInfo read through the configured local source contract, map
only allowlisted facts, and render deterministic raw-free compact diagnosis.
When `Finished queries` is selected and the query-list source contract is also
configured, the Trino lane may perform one bounded retained pruned coordinator
query-list read, select a bounded set of rows, perform bounded pruned QueryInfo
reads for those selected rows, and render deterministic raw-free compact
diagnosis rows. Those results may link to raw-free Trino Details only after
server-owned case materialization. Deterministic Python Report and optimizer
guidance are available from those materialized Details cases.

## Required UI Behavior

- The source selector marks configured beta sources as `Trino Beta Recent + One
  Query ID`, `Trino Beta Recent`, or `Trino Beta One Query ID`. Configured
  production-mode sources use the configured source label without capability
  suffixes. Neither mode exposes coordinator URLs, source-contract paths,
  auth-reference paths, local paths, raw artifact names, or secrets.
- The Engine control narrows the Source cluster selector before workflow
  selection: Impala shows only Impala-capable sources, and Trino shows only
  Trino-ready sources.
- The workflow disables unsupported Trino choices before submit. Running must
  not be shown as a Trino scan path, and Recent must be disabled unless the
  selected source has the query-list source contract.
- The Query ID form uses Trino-specific label, placeholder, help text, and
  `Run Trino Beta` submit copy when Trino Beta is active; production mode uses
  the same copy without `Beta`.
- The Recent form uses Trino-safe defaults when Trino is active: no
  metadata collection, no user/resource-pool/query-type filters, and no
  Running mode fallback.
- Async jobs use Trino-specific stage and terminal wording, and never reuse
  Impala profile/report progress copy.
- Browser-visible Trino Beta failures render a raw-free structured error with a
  stable reason code, workflow stage, safe detail bullets when available, and a
  next step. They must not expose Query IDs, coordinator URLs, auth references,
  source-contract paths, local paths, raw payloads, raw artifact names,
  subprocess output, or secrets.
- Result pages state the full local production boundary, render an explicit status strip
  for Running, query-history crawling, metadata, raw-free Details, Python
  Report, optimizer guidance, LLM reports, Query Optimizer jobs, generated SQL, and SQL execution,
  and link to Trino Details only after server-owned case materialization.
  Python Report and optimizer guidance links appear only on the materialized
  Details page. They do not link to LLM reports, Query Optimizer jobs, generated
  SQL, or SQL execution.
- Forged or stale Trino submits fail closed before creating jobs or running
  subprocesses when the selected source is not configured for the local lane.
- The route-level smoke path covers configured source selection, `/analyze`
  async submit, `/jobs/{job_id}/status` polling, and the final result page
  without a real coordinator network dependency.

## Blocked Claims

The local Trino UI must continue to state or enforce that Trino does not support:

- Running scans
- query-history crawling
- metadata collection
- Details before case materialization
- Python Report before case materialization
- optimizer guidance before case materialization
- LLM reports
- Query Optimizer jobs
- Query Doctor-generated Trino SQL
- SQL execution
- broader/shared production Trino support

## Required Gates

Before showing the Trino local production lane in a release or demo build, run and keep
passing:

- `python3 scripts/audit_trino_beta_release_readiness.py --config <ignored-local-web-config.json> --selected-query-limit 1`
- `python3 scripts/audit_trino_shared_deployment_preflight.py --config <ignored-local-web-config.json>`
- `python3 scripts/audit_trino_shared_deployment_boundary.py --config <ignored-local-web-config.json>`
- `python3 scripts/audit_trino_web_beta_readiness.py --require-query-id --require-recent`
- `python3 scripts/audit_trino_web_beta_live_smoke.py --config <ignored-local-web-config.json> --selected-query-limit 1`
- `scripts/query-doctor-web-trino-beta-smoke --config <ignored-local-web-config.json> --limit 1`
- `python3 -m pytest -q tests/test_web_trino_beta_query.py`, including the
  route-level Trino Beta E2E smoke
- `python3 -m pytest -q tests/test_web_ui_help.py tests/test_web_ui_home.py`
- `python3 scripts/audit_trino_product_surface_boundary.py --registry-only`
- `python3 scripts/audit_trino_support_gap_matrix.py`
- `python3 -m pytest -q tests/*trino*.py tests/test_engine_capabilities.py tests/test_engine_redaction_note.py tests/test_engine_intake_primitives.py tests/test_manifest_references.py`

The shared deployment preflight is a dev-only/static hardening wrapper over the
shared boundary audit, product-surface audit, support-gap audit, and active-docs
check. It performs no coordinator network read, live smoke, UI smoke, metadata
collection, or SQL execution, and it does not make shared Trino production
support available. The underlying shared deployment boundary audit checks local
config shape when a config is supplied, requires trusted front-door viewer
identity for shared/non-local Trino web deployment, requires raw-source reveal
to stay isolated and disabled for shared Trino, and emits only raw-free counts
and issue categories without config paths, header names, users, Query IDs,
coordinator URLs, auth references, source-contract paths, or raw payloads. Use
[trino-shared-deployment-hardening.md](trino-shared-deployment-hardening.md) as
the durable contract for that shared/non-local hardening layer. For
shared/non-local Trino configs, add `--trusted-front-door-reviewed` to the
preflight, boundary audit, or release-readiness bundle only after the operator
has verified that the trusted front door strips inbound viewer headers and sets
exactly one normalized simple viewer value.

The product-surface boundary audit must check curated public claim surfaces,
including this readiness checklist, for the local production Recent and One
Query ID boundary, the legacy Trino Beta label, and forbidden broad
production-support wording.

The release-readiness bundle is the preferred one-command handoff path. Its
`--static-only` mode runs only static audits and focused tests when no
intentional local Trino source is available for developer drift checks.
`--static-only` is not a release or demo substitute; full demo/release mode
requires the local config gate, bounded backend live smoke, and local web UI
smoke.
The bundle can also include the optional `trino_metadata_cli_summary_smoke`
gate only when the operator supplies the metadata smoke flags and
`--metadata-smoke-redaction-reviewed`. That dev-only gate may contact the
coordinator only through the operator-installed Trino CLI, runs only
Python-owned read-only metadata statements, emits raw-free smoke and aggregate
summaries, and must not print paths, URLs, users, object identifiers, metadata
values, CLI stdout/stderr, or raw payloads. It does not add product metadata
collection or any browser/report/optimizer surface.

The web readiness audit is local-config only: it checks the selected
source-contract files and Trino source availability, emits only raw-free counts
and issue IDs, and performs no coordinator network read or SQL execution.
The live smoke runs only after that local-config gate against an intentionally
configured local source. It performs the bounded Trino Recent path and
selected QueryInfo diagnosis through the web backend, emits only raw-free counts
and issue IDs, and performs no SQL execution, metadata collection, trusted
report generation, Query Optimizer jobs, or broader support-claim promotion. Details
links, Python Report links, and optimizer guidance links may appear only for
materialized raw-free Trino web cases.
The web UI smoke starts the local web server, submits the Trino Recent
form, validates the result HTML, then submits One Query ID using one
selected retained Query ID without printing it. It must not print Query IDs,
coordinator URLs, auth references, local paths, or raw payloads, and the result
HTML may link only to materialized Trino Details. It must not expose Impala
Details routes, direct report/optimizer job actions, LLM reports, generated SQL,
or SQL execution. Python Report and optimizer guidance remain available only
from the materialized Trino Details page.

## Screenshot Note

This checklist does not require a README screenshot refresh by itself. Refresh
README screenshots only when the first-screen UI layout or material workflow
presentation changes; the beta readiness gate here documents and tests the
existing UI behavior.
