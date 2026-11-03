# Query Doctor Code Audit

Last reviewed: 2026-11-03

This public audit tracks current engineering and safety risk areas at a level
that is useful to contributors without publishing local calibration history.
Detailed branch notes, real-batch measurements, private fixture identifiers,
generated paths, and per-run investigation logs belong in local exclude-only notes.
Resolved implementation history is retained in
[archive/code-audit-resolved-guards.md](archive/code-audit-resolved-guards.md);
this document remains the source for active risks and maintenance boundaries.

## Summary

Query Doctor has a deterministic diagnostic core: collection is bounded,
Python extracts facts, browser presenters sanitize display data, and LLM output
is hidden unless validation accepts it. The largest remaining public risks are
release-readiness and maintenance risks: product usefulness gaps, review
complexity, rendered-Markdown internal coupling, fixture depth, lower-priority
redaction bypass variants, regex resource-bound regression drift, and
review-branch history shape before public sharing.

## Current Strengths

- Recent queries, Running now, and Known Query ID workflows do not auto-run LLM
  reports or optimizer jobs.
- Cloudera Manager and direct Impala collection paths are bounded and
  read-only for the supported workflow.
- Full production triage remains Impala-only. Trino is limited to the
  matrix-listed bounded raw-free import and local production lanes, while Spark
  remains bounded compact-only. Do not combine them into an Impala-parity or
  generic multi-engine support claim.
- The normalized engine-fact projection is not the product engine registry or a
  replacement for current Impala analyzer facts; existing Impala recommendations
  still come from the production analyzer, presenter, and report contracts until
  an explicit migration proves parity.
- Metadata collection is read-only, allowlisted, bounded, explicit, and
  redacted.
- Redaction is layered: explicit metadata keys, host fields, host assignments,
  URLs, IP addresses, auth headers, and configured secret values are scrubbed
  before local artifacts, logs, or browser fallback text can reuse them.
- Browser display sanitization is centralized and covered by safety tests.
- Private deployments may explicitly opt into diagnostic host-name display;
  default aliases, credential masking, and storage/lease identity sanitization
  remain independent of that display policy.
- Web subprocess helpers bound captured child stdout/stderr per stream on the
  parent side, including real subprocess calls and defensive custom-runner
  returns; browser failure messages still do not display captured output.
- Trusted report and optimizer outputs require deterministic validation before
  browser rendering.
- Optimizer fallback can produce trusted non-SQL outcomes instead of exposing
  partial or untrusted drafts.
- Query Optimizer recommendations prompts keep raw source SQL out of the prompt
  and rely on Python-owned candidates, SQL-shape digests, and fact digests;
  rewrite drafts are accepted only after deterministic SQL validation.
- User-controlled web and optimizer text inputs are byte-bounded before
  regex-heavy parsing or validation paths.
- Synthetic demo data and public README screenshots are kept raw-free.
- Base Python and web runtime packaging has no new third-party dependencies;
  optional developer and E2E tooling stays outside the default install path.

## Open Risk Areas

Finding numbers are stable audit identifiers. Closed findings and detailed
implementation histories move to the resolved-guard archive instead of being
renumbered here.

### 1. Optimizer usefulness remains narrow

Severity: medium for product value, low for trust.

The optimizer intentionally accepts only Python-owned, strictly validated SQL
drafts plus trusted no-rewrite or recommendations-only outcomes. That keeps the
workflow safe, but many real expensive queries still need manual guidance
because the supported recipe set is narrow.

Keep future optimizer work focused:

- add recipes only when detection, deterministic draft generation, validation,
  and regression fixtures are all available;
- keep no-recipe guidance raw-free and explicit that it is manual review, not a
  trusted SQL draft;
- use raw-free funnel and shape audits to choose recipe families, but do not
  publish local batch identifiers or private result tables.

### 2. Large modules make safety review expensive

Severity: medium.

Several web, batch, optimizer, and analyzer modules carry broad responsibilities
because they grew around safety-sensitive flows. Large files are not a bug by
themselves, but they make trust-boundary review harder.

When touching these areas:

- keep behavior slices small;
- extract presenters, parsers, validators, command builders, or render helpers
  only when a real boundary becomes clearer;
- avoid formatting-only churn mixed with behavior changes;
- add focused tests around the boundary being changed.

Machine-checkable capability, source-contract, collector, production-review,
representative-evidence, and fact-promotion registries now pin the bounded
Trino/Spark and cross-engine seams. Capability or support changes must update
the relevant registry, focused tests, and support matrix before product wording
or routing changes. The detailed implementation record is in the
[resolved-guard archive](archive/code-audit-resolved-guards.md#architecture-and-engine-boundary-drift-guards).

Shared handoff-artifact helpers own safe path comparison, output-overlap
detection, and deterministic JSON writing. Trino and Spark handoff scripts
still own engine-specific parsing, redaction, readiness gates, and safe error
wording; keep moving repeated orchestration behind focused helpers when those
scripts are next touched.

Remaining architecture backlog before broad parallel Trino/Spark feature work:

- continue moving thick readiness/handoff script orchestration into focused
  dev-tool helpers when those scripts are next touched.

High-priority Impala architecture backlog:

- Rendered `analysis_facts.md` still acts as a load-bearing interchange format
  for some analyzer, report, web, and optimization-candidate consumers. Label
  or heading changes can therefore degrade a consumer by parsing less evidence
  rather than by failing at a typed boundary.
- Move those consumers toward `analysis.json` or a typed loader over the same
  deterministic facts, keeping Markdown as a render-only view.
- The Recent batch scoring path now prefers typed `analysis.json` for core
  scoring components and score reasons, records markdown fallback source and
  reason in batch summaries, and keeps renderer-to-parser characterization
  coverage so the fallback remains deliberate. Query and stats optimization
  candidate scoring now also prefer typed analyzer evidence for impact,
  planning/opportunity, runtime counter-signal, and metadata-gap inputs when
  the full analyzer JSON contract is present; their candidate JSON records safe
  evidence-source and fallback-reason labels for legacy rendered-facts fallback.
  Report prompt assembly, browser Details/presenters, and any remaining
  analyzer consumers still need equivalent typed migration or contract guards
  before the Markdown coupling is closed.
- Add characterization over representative fixtures before replacing any
  remaining Markdown parser or score source, so behavior changes are deliberate
  and reviewable.
- Keep the full-pipeline leak-canary regression baseline passing while this
  migration proceeds. It exercises manual-profile intake, analysis, scoring,
  report prompt assembly, trusted report output, and browser rendering; any new
  generated sink in that path must be classified before marker checks run.

### 3. Optimizer prompt-injection guard coverage must stay explicit

Severity: low for current behavior, medium if new prompt slots regress.

The Query Optimizer rewrite prompt must include raw source SQL inside a
delimited `INPUT SQL` block because the model needs the statement to rewrite.
Current trust is correctly placed on deterministic output validation: only one
read-only `SELECT` or `WITH` draft with preserved shape and recipe constraints
can become trusted.

The rewrite prompts now explicitly frame `INPUT SQL` as untrusted data and tell
the model to ignore instructions inside SQL comments, literals, identifiers,
aliases, table names, and column names. Regression coverage includes a malicious
source SQL comment that asks for unsafe SQL; when the model returns that unsafe
draft, deterministic validation downgrades the result to a trusted no-rewrite
recommendation outcome. Recommendations-only prompts also keep raw source SQL
out of the prompt, mark source-derived digests and fact text as untrusted data,
and omit instruction-like or unsafe digest values.

Maintenance guard:

- keep the untrusted-data wording and downgrade tests with any future rewrite
  prompt changes;
- add equivalent raw-free prompt-path tests whenever a new digest section or
  source-derived prompt slot is introduced.

### 4. Sanitized fixture depth is still the limiting factor

Severity: medium.

The project has useful synthetic and sanitized fixtures, but some analyzer and
optimizer failure modes still need more representative raw-free coverage before
the product can claim broader support.

Needed fixture work:

- small golden synthetic/sanitized cases for stats, skew, data movement,
  metadata-missing, optimizer-candidate, and mixed-signal paths;
- browser/report regression fixtures for raw-leak prevention;
- installed-wheel CLI checks for every public console script;
- explicit unsupported-shape fixtures for optimizer no-draft outcomes.

### 5. Impala normalized fact projection can drift from production facts

Severity: medium.

`query_doctor/analyzer/impala_engine_facts.py` projects existing Impala analyzer
output into the normalized engine-fact contract for fixture calibration. It is
not currently consumed by Impala scoring, Details, reports, optimizer guidance,
or browser rendering. That keeps production behavior stable, but it also means
the projection can drift from the analyzer facts that actually support user
recommendations.

Keep future Impala projection work constrained:

- expand representative Impala projection fixtures before adding new projected
  fact families;
- do not wire product consumers to the projection until parity is proven against
  existing analyzer facts and representative sanitized batches;
- keep the guard test that prevents accidental product imports unless a real
  migration slice updates this audit, docs, and consumer tests.

### 6. Outbound HTTP clients must keep one shared egress policy

Severity: low for current behavior, high if regressed.

Collection and LLM HTTP clients now share `query_doctor.safety.http_egress` for
default outbound requests. The shared helper validates DNS-resolved destination
classes, blocks metadata, link-local, reserved, documentation, multicast, and
unspecified targets, and builds a no-redirect opener so a redirect cannot bypass
the same target policy. CM, Prometheus, direct Impala daemon endpoints, and LLM
clients use the configured-diagnostic policy because real deployments commonly
use private or loopback endpoints. Spark History Server compact intake keeps
the stricter public-target policy by default and switches to the configured
policy only when local/private targets are explicitly opted in.
Trino HTTP event archive, HTTP query-detail archive, and one-query pruned
coordinator QueryInfo readers also use the configured-diagnostic policy and
therefore share the same target validation and no-redirect behavior. Their
distinct support statuses and product boundaries remain those in the engine
support gap matrix.

Response reads are parent-side bounded on these paths: Spark, Prometheus, direct
Impala daemon endpoints, CM text/JSON, Trino HTTP archive/query-detail and
coordinator QueryInfo reads, and LLM JSON/streaming responses. Guard tests cover
unsafe literal destinations, DNS-resolved unsafe destinations, integer-IP
loopback aliases, no-redirect handlers, safe default opener wiring, and default
CM response caps.

Spark-specific coverage also keeps shared egress target violations fail-closed
at the Spark collector boundary instead of downgrading them to optional endpoint
warnings, with generic browser-safe wording for DNS failures.

Maintenance guard:

- do not introduce a new outbound HTTP client that defaults to raw
  `urllib.request.urlopen`;
- keep private-network and loopback destinations as explicit configured
  diagnostic targets, not arbitrary browser-form egress;
- keep response byte caps mandatory for every fetched response path;
- add a focused egress test whenever a new collector or LLM provider adds an
  HTTP surface.

Do not fix this by blindly blocking all private RFC1918 addresses: real CM,
Impala, and Prometheus deployments commonly live on private networks. The
policy must distinguish explicit configured diagnostic targets from arbitrary
egress initiated through browser forms or optional URL overrides.

### 7. Report validators need adversarial wording coverage

Severity: high for trusted report semantics, medium for future language drift.

English and Russian validators compare report claims with deterministic facts,
normalize supported language keys through one registry, fail closed on unknown
languages, and reject SQL-like trusted output at the trust gate. The resolved
semantic-claim coverage record is archived. Keep the adversarial validator
corpus in focused validation whenever report wording, trusted markers, language
support, or report safety gates change.

### 8. Fail-closed trusted-output paths must keep defensive coverage

Severity: low for current web exposure, medium if regressed.

Report finalization validates generated narrative text, appends deterministic
facts, validates the final report again, and writes the final artifact only
after both checks pass. The web command builders force strict report validation,
and trusted marker loaders require the current strict validation mode and hash
bindings before browser display. Direct trusted-marker tests reject non-strict
report validation modes and non-`strict_v2` optimizer validation modes, so a
local/manual `validation_mode=off` artifact cannot become trusted browser
output. Report and optimizer trusted markers also bind current marker schema
versions before browser display, so artifacts validated by an older weaker
marker contract do not silently survive an upgrade.

The remaining risk is coverage drift. Recent scan batch-job defensive fallback
coverage now verifies that unexpected exceptions become a generic failed job
message, leave the job flow terminal, and do not expose raw subprocess output,
paths, SQL, model names, or artifact names in browser job status JSON.

Keep future changes guarded:

- CLI `validation_mode=off` is an escape hatch for local/manual use and must
  not make an artifact trusted for browser display;
- any new defensive web catch-all handler must have focused tests proving
  unexpected exceptions return redacted safe fallback messages, do not crash the
  job flow, and do not expose raw subprocess output, paths, SQL, model names, or
  artifact names.

### 9. Redaction adversarial coverage should include free-text host and secret variants

Severity: low for trusted browser/report surfaces, medium for local artifacts
and logs.

The primary browser and trusted-report safety guarantee is raw-free fact
construction from Python-owned facts. Redaction is still an important
defense-in-depth layer for local profile/metadata artifacts, terminal logs,
warning text, and defensive browser fallbacks.

Current redaction covers explicit host fields, host assignments, URLs, bare
FQDNs, host-like single-label names, IP addresses, auth headers, cookies,
common and less-common secret assignment names, configured secret values,
metadata key parts, and stable host aliases. A compact adversarial corpus now
covers local/log/browser fallback text for these cases, including credential,
passphrase, private-key, and auth assignments. The same coverage also pins
false-positive boundaries: SQL-style table identifiers, pool names, synthetic
source-version labels, and safe local filenames must not be misclassified as
hosts by local/log redaction. Browser display redaction separately hides
model-name variants including `gpt-4`, `gpt-4o`, `gpt_4_1`, and `gpt-lst`.

The remaining gaps are lower-priority bypass variants:

- IDN or Unicode host spellings are not normalized into ASCII before redaction;
- non-dotted hostnames without host-like tokens remain intentionally keyword
  gated unless they appear in explicit host fields or assignments;
- alternate IP encodings beyond dotted decimal and standard IPv6 remain
  low-priority bypass classes unless a future raw-ish local artifact surface
  depends on free-text redaction alone.

Required guard work:

- keep the adversarial corpus updated whenever browser fallback, local log, or
  report display redaction accepts a new raw-ish text source;
- keep paired false-positive tests for curated SQL/table/pool/file identifiers
  whenever host heuristics are expanded;
- add Unicode/IDN-like host text coverage before depending on free-text
  redaction for such inputs;
- keep intentionally keyword-gated host matching documented as a redaction
  limitation, and keep raw-free fact construction as the trusted-report and
  browser primary safety layer.

### 10. Regex resource-bound guards should stay explicit

Severity: low for current behavior, medium if regressed.

Current user-controlled inputs are capped before expensive processing: web POST
bodies, pasted optimizer SQL, optimizer recommendations, Spark compact JSON,
Trino fixture packages, fetched profile/metrics/event responses, and metadata
outputs all have byte limits. The safety and validator regexes mostly use
bounded repeats and simple scans rather than nested unbounded quantifiers.

The remaining risk is regression drift. A future validator or sanitizer regex
could add catastrophic backtracking without violating the existing functional
tests. Current focused guard coverage includes bounded pathological inputs for
shared log redaction, browser error sanitization, trusted report SQL-like text
validation, and optimizer recommendation validation; those tests assert safe
redaction or safe rejection without depending on exact wall-clock timings.

Required guard work:

- keep bounded pathological-input coverage for shared
  redaction/browser/report/optimizer validation paths whenever those regex
  surfaces expand, focused on completion and safe rejection rather than exact
  wall-clock performance;
- keep input byte caps before regex-heavy parsing, validation, or sanitizer
  paths;
- treat unclosed fenced-SQL extraction and similar lazy DOTALL scans as bounded
  by input caps, and add a focused guard before expanding those patterns.

### 11. Pre-push history must be cleaned into reviewable semantic commits

Severity: high for public review readiness, low for content safety when public
release scans pass.

Older local integration history may contain frequent merge commits. The
current worktree workflow in [agent-quickstart.md](agent-quickstart.md) prefers
fast-forward integration into local `main`. A release or review branch must
not publish a large merge-heavy or draft-shaped history as-is.

The public release gate uses `scripts/check_release_history_shape.py` to reject
a merge-heavy or draft-shaped review branch before public handoff. This does not
replace the required content and history review.

Before any requested push or public-sharing branch:

- scan the full diff and history for secrets, raw production data, generated
  artifacts, local paths, hostnames, and real configuration;
- if the history gate finds merge-heavy or draft-shaped history, prepare
  reviewable semantic commits that group behavior, tests, and docs by actual
  change; rewriting or squashing history still requires the explicit approval
  defined in `AGENTS.md`;
- do not rewrite an already reviewable fast-forward history just to satisfy
  an assumed cleanup step;
- do not push local `main` directly to remote `main`; push a prepared review
  branch and keep protected-main behavior intact.

### 15. Browser and trusted-artifact boundaries must stay conservative

Severity: high if regressed.

Trusted browser/report surfaces must not expose raw SQL, raw profile text, raw
metadata, local paths, `case_dir`, process logs, secrets, model names, runtime
internals, or raw artifact filenames. The isolated owner-only selected-case
source surface is the narrow raw-SQL browser exception and must keep the
stricter rules in `docs/safety-contract.md`. Shared or non-local owner-raw
deployments must resolve per-request viewer identity through
`viewer_identity_header` behind a trusted auth front door that strips inbound
copies and sets exactly one normalized simple owner value; keytab or collection
owner sets must not authorize raw reveal by themselves. The isolated owner-raw
source surface must keep its global kill switch and emit only reason-coded
raw-free audit lines.

Any change that renders analyzer facts, report content, optimizer content,
collector errors, or generated artifacts must include focused tests proving the
new trusted/report surface remains raw-free, or that an isolated owner-raw
surface stays policy-gated and non-persisted.

Current batch and Specific Query report exports have traversal, symlink,
fixed-download-name, trusted-marker, and raw-free rendering coverage. The
resolved route-audit details are archived. Any new browser artifact route must
add equivalent coverage before it becomes trusted output.

### 16. Trino naming and promotion debt must stay closed

Severity: medium.

The Trino-specific naming contract permits `trino_*`, `query_detail_*`,
`query_list_*`, and neutral `no_*` prefixes for Trino-specific facts.
`planning_time_ms` remains an explicit distributed-SQL-family fact with
`allowed_engines={"impala", "trino"}`. The resolved namespace-cleanup history
is archived; broader promotion and aggregate-bucket debt remain active.

Before broader/shared Trino support, LLM report or Query Optimizer job
promotion, or any Details surface beyond raw-free materialized local cases:

- keep Trino-only facts behind `trino_*`/source-shape prefixes unless a
  separate contract change promotes a specific fact to a family/shared scope
  with explicit `allowed_engines`;
- keep `no_*` as the only limitation-fact idiom for unsupported Trino coverage;
- keep `query_list_*` bucket growth behind an explicit contract/test update, or
  replace the bucket fan-out with a structured aggregate fact in a dedicated
  contract migration;
- keep strict one-query readiness gates rejecting both `query_list_*`
  aggregate facts and local `trino_metadata_*` metadata-summary facts before
  any boundary can count as one-query Trino diagnosis evidence;
- keep retained handoff-suite manifests confined to safe relative `*.json`
  artifact references and keep duplicate boundary/diagnosis references rejected
  so suite-width gates cannot count one artifact more than once;
- keep local compact diagnosis rejecting local metadata-summary boundaries so
  aggregate metadata-coverage facts cannot be rendered as diagnosis;
- run `scripts/audit_trino_product_surface_boundary.py` on retained compact
  boundary/diagnosis artifacts or a retained handoff-suite manifest before any
  product-surface promotion decision so
  `live_known_query_diagnosis=one_query_pruned_query_info_local_production`,
  `live_recent_scan=retained_query_list_local_production`, the bounded local
  production support claim, and the compact-preview plus Recent, One Query ID,
  materialized Details, deterministic Python Report, and optimizer-guidance
  registry limits stay pinned;
  manifest mode must require diagnosis artifacts for every entry, and this
  audit must reject metadata-summary boundaries as aggregate coverage evidence,
  not product-surface diagnosis artifacts; keep its static source-import guard
  enabled so only the explicit local materialized lanes may consume Trino
  diagnosis facts while LLM report jobs, Query Optimizer jobs, raw-source
  rendering, and other unsupported product modules remain blocked;
- run `scripts/audit_trino_support_gap_matrix.py` before broader support-surface
  decisions so registered Trino fact-family coverage, source-type registry
  coverage, neutral `no_*` gaps, blocked product adapter flags, and
  `trino_support_gap_matrix_audit_v1` evidence stay aligned with the
  support-gap matrix;
- keep the engine adapter and console-script registry language precise: Trino is
  registered only for the matrix-listed bounded raw-free import and compact
  lanes plus local production retained-list Recent, One Query ID, materialized
  Details, deterministic Python Report, and optimizer guidance. It is not
  registered for Running scans, broad query-history crawling, product metadata
  collection, LLM reports, Query Optimizer jobs, generated SQL, SQL execution,
  or broader/shared production support.

### 17. Public documentation must not become a local run journal

Severity: medium.

Committed docs are public docs. The repository should keep durable contracts,
runbooks, and sanitized summaries, while local continuation notes and raw
validation evidence stay ignored.

Before committing documentation changes, run:

```bash
python3 scripts/check_staged_public_safety.py --changed
python3 scripts/audit_public_docs.py
python3 scripts/check_active_docs.py
python3 scripts/check_markdown_links.py
git diff --check
```

## Update Rule

Update this audit when a public risk changes materially, a trust boundary moves,
or a previously open risk is resolved by code and tests. Keep detailed local
evidence in local exclude-only notes; commit only the durable public conclusion.
