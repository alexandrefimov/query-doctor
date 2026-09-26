# Query Doctor Safety Contract

Last reviewed: 2026-07-29

Language: English | [Russian](i18n/ru/safety-contract.md)

This file contains mandatory safety rules. Exact phrases such as `Do not weaken
validators` are intentionally kept precise because they define review and
implementation boundaries.

## Fact Boundary

- Query Doctor's deterministic Python analyzers own diagnostic facts, evidence
  merging, assessment findings, and causal promotion.
- The LLM owns wording only.
- Every diagnostic claim must map to `supported`, `not_observed`, or `unknown`
  evidence. Current reports render that evidence through `analysis_facts.md`.
- Do not state root cause unless Query Doctor's accepted evidence directly
  supports that cause.
- The report writer must not infer facts from raw profile or EXPLAIN text, SQL,
  Cloudera Manager JSON, local config, or external knowledge.
- Raw EXPLAIN text is a local evidence artifact. Persisted plan facts may contain
  only bounded allowlisted structural enums, coverage states, counts, and finite
  nonnegative estimates. They must not retain raw lines, relations, predicates,
  literals, paths, engine-local identities, or a raw-plan fingerprint, and
  optimizer intent alone must not support a causal claim.
- An accepted external EXPLAIN artifact remains unbound to the runtime profile.
  Case co-location, adjacent SQL or query metadata, and structural operator
  overlap do not establish statement or execution identity. Both identities
  must remain `unknown` until a separately verified same-snapshot provenance
  source exists.

## Engine Fact Boundary

- `engine_fact_boundary_v1` is a raw-free normalized fact seam. It is not the
  product engine registry, not a public support claim, and not a replacement for
  existing Impala analyzer facts until an explicit migration proves parity.
- Engine fact bundles must use registered fact identifiers with explicit scope
  and allowed engines. Shared or distributed-SQL-family facts require an
  explicit namespace definition; engine-specific facts must stay
  engine-prefixed or otherwise allowlisted by the registry.
- Boundary payloads must be generated only from Python-owned parsed or compact
  facts and must pass raw-free validation before any browser/report consumer or
  consumer probe sees them.
- Boundary payloads and public engine fact text must not include raw SQL, raw
  profile text, raw metadata, raw event logs, raw query details, source
  endpoints, IDs, hostnames, user names, object names, local paths, runtime
  internals, parser-local identifiers, stack traces, exception messages, or raw
  artifact filenames.
- Unsupported, missing, partial, unstable, or source-version-mismatched engine
  facts must degrade to `unknown`, `not_observed`, or an explicit safe
  limitation. Do not backfill fake metrics, counters, lifecycle evidence, or
  events across engines.
- Trino compact/dev facts outside the local production web cases and Spark
  compact facts remain below production support.
  The Trino local production exceptions are local web retained-list Recent over one
  bounded retained pruned coordinator query-list read plus selected pruned
  coordinator QueryInfo reads, and one explicit local web Query ID over bounded
  pruned coordinator QueryInfo. Both render raw-free compact diagnosis. They do
  not enable Running, query-history crawling, product metadata collection, LLM reports,
  Query Optimizer jobs, Query Doctor-generated Trino SQL, user SQL execution, or
  broader/shared Trino production triage support. They may open the raw-free Trino Details view,
  deterministic Python Report, and optimizer guidance only from server-owned materialized case
  artifacts. See
  [engine-support-gap-matrix.md](engine-support-gap-matrix.md) for current
  status.

## Collection Boundary

- Broad cluster, profile, or table scanning is disabled by default.
- External collection must be explicit, bounded, read-only, redacted, and safe
  by default.
- Outbound HTTP collection must validate the target, keep response reads bounded,
  and reject unsafe redirect targets. Metadata, link-local, reserved, multicast,
  or otherwise unsafe destinations must not be reachable through browser forms or
  optional URL overrides.
- Private-network and loopback targets are allowed only as explicit configured
  diagnostic endpoints for the relevant component, not as arbitrary egress.
- Dry-run and preflight paths must not collect profile text.
- Real profile collection must not print raw profiles, SQL, raw Cloudera
  Manager (CM) JSON, or credentials.
- The first supported real Impala metadata connection path is Kerberos over
  HiveServer2 with an already available TGT from `kinit`.
- The metadata collector does not call `kinit`, does not prompt for passwords,
  and does not accept AD/LDAP passwords. It opens one read-only HiveServer2
  connection per run through impyla and reuses it for every planned statement.
- The metadata collector accepts only bounded table references from explicit
  CLI input or Python-owned selected-case extraction. It runs only read-only
  statements: `SHOW CREATE TABLE`, `SHOW TABLE STATS`, and
  `SHOW COLUMN STATS`.
- With `--source hms-postgres`, the collector does not contact Impala. It opens
  one read-only PostgreSQL session to the Hive Metastore database per run, with
  the DSN taken from a named environment variable (never from argv or config),
  and runs only fixed parameterized SELECT statements over the metastore schema
  tables for the same bounded table references. Driver errors are replaced by
  fixed messages that carry neither the DSN nor the host.
- Raw coordinator rows and driver error text must not be printed to the
  terminal. Collected output is bounded, redacted, and written only under
  explicit `--out`.
- Generated `impala_context.md` and `impala_context.json` are local outputs and
  must not be committed.

## Manual Profile Intake Boundary

- Manual profile intake accepts only one exported Apache Impala text profile for
  one Query ID, either from CLI/local inbox staging or from the explicit
  local/private web upload form. The CLI may derive that Query ID from an
  embedded profile header, from a strict Impala Web UI
  `profile_<query-id-high>_<query-id-low>` download filename, or accept an
  explicit `--query-id` when the profile header is missing. JSON, Thrift,
  profile-v2 payloads, broad profile directories, and network collection are
  outside this boundary.
- The browser must not render the raw profile, uploaded filename, local path, or
  temporary upload artifact. The only browser upload entry point is
  `POST /profile/upload`: it must require multipart form data, accept exactly one
  profile file, stay bounded by `max_profile_bytes` plus small multipart
  overhead, force the Impala Known Query path, stage through the same redacted
  analyzer path, and remove the temporary upload file after staging. Public demo
  mode must hide the upload form and block uploads before reading the request
  body. Web `manual_profile_dir` remains a server-side local inbox; users place
  files on disk, then enter the original Query ID in Known Query ID mode. Web
  `--corpus-dir` may also render already complete manual-profile cases staged by
  `query-doctor-analyze --profile-text` as a local read-only results table
  without requiring live CM settings or default local config discovery when no
  explicit `--config` is provided.
- Manual profile staging must run the same redaction and bounded analyzer path
  used for collector-shaped cases before any Details page or trusted report can
  consume the case.
- If the profile text contains an embedded Query ID and an explicit Query ID is
  supplied, both values must match before the staged case can be written or
  replace an existing case. Missing Query ID evidence, missing profile files, or
  malformed profile files must fail closed with safe remediation text.
- Browser-visible manual-intake errors must not expose raw profile text, local
  paths, raw filenames, subprocess output, credentials, or mismatched raw Query
  IDs. Terminal diagnostics may be technical but still must avoid raw profile
  dumps and secrets.

## Git Boundary

Generated, sensitive, or local outputs must not be committed:

- `cases/cm-corpus/`
- `cases/cm-corpus-hostalias/`
- `analysis_facts.md`
- generated `report*.md` / `diagnosis*.md`
- `*.partial`
- local Cloudera Manager (CM) config
- real CM profile material
- `query_metadata.json`
- `impala_context.md` / `impala_context.json`

Never commit raw hostnames, IP addresses, users, emails, tokens, cookies,
passwords, Authorization headers, embedded URL credentials, local config
contents, or real production profile text.

## Report Validation

- Validators are fail-closed.
- Do not weaken validators to make reports pass.
- If a report is rejected, improve deterministic facts, prompt wording,
  sanitizer behavior, or tests.
- New validator rules must include unsafe-rejected and safe-allowed tests.
- Every supported report language must have explicit overclaim-detection
  coverage. Adding a report language requires validator coverage and parity
  tests before it can be used for trusted reports.
- Validators must reject unsupported claims even when phrased indirectly or
  softly, including causal/responsibility wording, diagnostic recommendations,
  and row/cardinality or memory estimate direction wording.
- Deterministic normalization must not silently hide unsupported claims.
- Safe replacements must be explicit, narrow, and tested.
- Raw LLM output is buffered and must not stream to stdout/stderr or
  user-facing UI.
- Final report files are written only after normalization, sanitization,
  validation, deterministic appendix append, and final validation.
- Validation failure writes only a sanitized/normalized `.partial` and preserves
  the existing final report.
- Trusted final reports must not contain raw SQL-like text, SQL fenced code
  blocks, pasted query fragments, or raw metadata command snippets such as
  table-specific `SHOW CREATE TABLE`, `SHOW TABLE STATS`, or
  `SHOW COLUMN STATS`.
- Partial or invalid report output is untrusted and must not be displayed as
  the final diagnosis.
- CLI validation bypass modes are manual escape hatches. Browser and trusted
  artifact consumers must accept only current strict validation markers with
  every field required by that marker contract and matching artifact and facts
  hashes. Marker schemas should bind a schema version before validation rules
  change materially.
- Defensive UI failure handlers must fail closed with redacted safe messages
  and must not expose raw SQL, subprocess output, local paths, or artifact
  names when unexpected exceptions occur.

## Query Optimizer Trust Boundary

- Query Optimizer may send raw source SQL to an LLM only inside a delimited
  `INPUT SQL` block for an explicit selected-case rewrite attempt.
- Prompt wording must frame `INPUT SQL` as untrusted data. Instructions inside
  SQL comments, string literals, identifiers, or pasted query text must not
  override Python-owned rules, recipes, or validation requirements.
- Recommendations-only prompts must stay raw-free and use only Python-owned
  recommendation candidates, SQL-shape digests, and optimizer fact digests.
- Raw LLM optimizer output is untrusted until deterministic validation accepts
  it. Unsafe, non-read-only, multi-statement, incomplete, or unsupported-shape
  output must remain untrusted or become a trusted no-rewrite/recommendations
  outcome, never a browser-visible trusted SQL draft.

## Redaction And Resource Boundary

- Redaction is defense in depth. It must not replace raw-free fact extraction,
  deterministic validation, or browser/trusted-report raw-content exclusions.
- Local artifacts, logs, warnings, and defensive UI fallback text that may carry
  operational strings must use the shared redaction policy before display or
  persistence.
- Redaction changes for hosts, users, URLs, credentials, auth headers, cookies,
  metadata keys, and local paths must include adversarial unsafe-rejected tests
  and safe false-positive checks.
- Identifier redaction names tables `<db>.<table_N>` by their position in the
  table list extracted from the statement, the same numbers the metadata
  collector uses, so redacted SQL and metadata can be matched. The label shows
  how many tables a query reads and which references share one, never a name.
  Metadata planning must reject `db.table_N` so a label is never collected as
  a real table.
- User-controlled text must be byte-bounded before regex-heavy parsing,
  validation, prompt assembly, sanitizer, or browser-rendering paths.
- New or expanded safety regexes must avoid nested unbounded quantifiers and
  must include a pathological-within-cap regression test when they sit on a
  trust boundary.

## Browser Display Boundary

- Trusted browser/report surfaces must not render raw SQL, raw profiles, raw
  metadata, stdout/stderr, local paths, `case_dir`, credentials, secret values,
  Kerberos ticket contents, metadata connection details, model names, or Ollama
  internals. The isolated owner-only selected-case source surface is the narrow
  raw-SQL browser exception and must follow the `owner_raw` rules below. Raw
  profiles, raw metadata, stdout/stderr, local paths, credentials, secret
  values, Kerberos material, model/runtime internals, and raw artifact filenames
  remain forbidden there too.
- Dynamic browser-visible text should use the shared browser display redaction
  policy before rendering.
- Web Recent and Running scans must not auto-run LLM reports or optimizer jobs.
  Known Query ID may generate the deterministic Python report as part of its
  explicit analysis submit job. LLM report and Query LLM optimizer generation
  remain explicit for one selected case.
- Trino Recent uses one bounded retained pruned coordinator query-list
  read plus bounded selected pruned coordinator QueryInfo reads from local
  config; Trino One Query ID uses one bounded pruned coordinator
  QueryInfo read from local config. Both render only raw-free compact diagnosis.
  They must not render coordinator URLs, auth header paths or values, raw
  QueryInfo, raw query-list payloads, raw SQL, local source-contract paths, LLM
  reports, Query Optimizer jobs, or generated Trino SQL. Trino Details, Trino
  Python Report, and Trino optimizer guidance may render only the server-owned
  raw-free materialized case facts and must not reveal query IDs, local paths,
  raw artifact names, or raw source payloads.
- Details-page Query LLM optimizer may render a validated read-only SQL draft
  only for an explicit selected-case optimizer action when the current web
  source policy is `source_visibility=owner_raw`. The default
  `source_visibility=safe` policy must degrade to trusted
  recommendations/no-rewrite guidance even if a validated SQL draft artifact is
  present. Partial drafts, raw source SQL, externally pasted SQL, and optimizer
  validation failures stay hidden.
- `source_visibility=owner_raw` is an owner-gating mode, not a blanket display
  bypass. In the current implementation it narrows Cloudera Manager or direct
  Impala Recent and Running scans to verified owner users and is the only web
  policy that can display a validated optimizer SQL draft. It also permits the
  separate isolated owner-only selected-case source surface to render the
  original read-only SQL source for a query whose `query.user` is authorized by
  the authenticated viewer identity. That source surface is not a trusted
  report, Details, Recent table, optimizer, handoff, or download surface; it
  must be source-allowlisted, fail closed on ownership mismatch, use
  `Cache-Control: no-store`, send nothing to the LLM, and still exclude raw
  profile dumps, raw metadata, local paths, subprocess output, secrets, model
  names, runtime internals, and raw artifact filenames. Local-first owner raw
  visibility must not start on a non-local web bind; shared web access requires
  authenticated viewer identity before owner raw visibility can be enabled.
- The isolated owner-raw source surface must remain behind a global kill switch.
  `owner_raw_source_enabled=false` and `--disable-owner-raw-source` disable only
  the original source page/link; they must not silently rewrite collection owner
  filters or optimizer policy.
- Every isolated owner-raw source page attempt must emit a server-side audit
  line with request id, route source, HTTP status, reason code, viewer
  mode/source, and switch state. Audit lines must not contain raw SQL, query
  ids, case ids, query users, local paths, header values, secrets, raw artifact
  filenames, model names, or runtime internals.
- Shared/D3 web deployments may provide the authenticated viewer identity
  through `viewer_identity_header` only when Query Doctor is behind a trusted
  auth proxy or ingress that authenticates the request and strips inbound
  copies of that header before setting exactly one already normalized simple
  owner value, such as an Active Directory `sAMAccountName` or Kerberos primary.
  This is the only Query Doctor D3 application contract. OIDC/SSO, SAML,
  SPNEGO/Kerberos, LDAP, MFA, session, logout, token, group, and RBAC handling
  must stay at the trusted front door; Query Doctor must not implement native
  owner-raw authentication variants or accept raw identity-provider tokens.
  Missing, duplicate, UPN/email-style, distinguished-name, group/role-like,
  opaque-subject, whitespace/display-name, comma-separated, service-principal,
  or host-principal values are unauthenticated and must fail closed for raw
  source access. The header is C2 viewer identity only; it must not widen C1
  collection credentials or owner-user collection scope. The required
  deployment checklist is
  [owner-raw-d3-deployment.md](owner-raw-d3-deployment.md).
- Keytab-derived Username dropdowns may display simple account names only.
  Keytab paths, full Kerberos principals, keytab contents, ticket contents, and
  `klist` subprocess output must not be rendered in browser-visible UI or
  trusted reports.

## Future Cluster Doctor

- Cluster Doctor is a future explicit user-run cluster/service/workload
  diagnostic seam, not current product support.
- Query Doctor may consume future Cluster Doctor output only as normalized
  Python-owned facts with status, scope, coverage, confidence, limitations, and
  deterministic correlation.
- Cluster Doctor must stay read-only: it may recommend checks or operational
  follow-up, but must not execute service control, configuration changes, data
  changes, or remediation automation.
- Current and future providers, including Cloudera Manager, direct Impala,
  Prometheus, prepared metric stores, or log/event stores, must be explicit,
  bounded, read-only, allowlisted where applicable, redacted, and tested before
  their facts enter reports or browser UI.
- Future log/event support must consume prepared event summaries only. Raw log
  lines, stack traces, raw alert text, principals, usernames, query text, and
  raw parser payloads must not enter browser-visible UI, trusted reports, or LLM
  prompts.
- The CM Events MVP CLI is read-only and bounded. It may print normalized event
  counts, severities, and signal ids, but must not print raw CM event payloads,
  raw log lines, event ids, hostnames, principals, paths, query text, or raw
  provider JSON.
- `cluster_event_context.json` is a schema-versioned internal Cluster Doctor
  seam artifact built only from normalized CM event summaries. It must whitelist
  exported fields and omit raw provider payloads, raw log lines, event ids,
  hostnames, principals, paths, query text, URLs, local paths, secrets, command
  output, model/runtime names, and raw artifact filenames.
- `cluster_context.json` is a schema-versioned aggregate Cluster Doctor seam
  artifact built only from safe context artifacts. It may include source status,
  product status, normalized signal counts, limitations, and next checks, but
  it must not include raw provider payloads or browser-forbidden details.
- Raw metric series, raw logs, raw provider JSON, raw alert text, raw
  timestamps, hostnames, entity IDs, URLs, paths, credentials, artifact names,
  command-stream details, model names, and runtime internals must not be
  rendered in trusted reports or browser-visible UI.
- Cluster-wide root-cause or incident claims require their own deterministic
  claim registry, fixtures, report validation, and browser safety tests.

## Report Structure

The LLM writes localized user-facing narrative sections for summary, practical
recommendations, detailed findings, and follow-up checks.

Python appends a localized analyzer facts appendix.

The analyzer facts appendix is built deterministically from `analysis_facts.md`.
The LLM must not write or reinterpret that section.

`## Table Metadata Context` is currently excluded from the LLM prompt and
appears only in the Python-generated appendix.

## Query LLM Optimizer

- Pasted-SQL Query Optimizer accepts only one safe SELECT/WITH statement and
  must not execute or echo pasted SQL after submit.
- Details-page Query LLM optimizer may use only server-owned analyzed case
  sources.
- Details-page external rewrite validation is shown only after an LLM optimizer
  validation failure and accepts pasted SQL only for bounded in-memory
  validation against the server-owned source. It must not execute the pasted
  SQL, persist it as a raw artifact, or echo it back into browser output.
- Supported details-page source scopes are read-only SELECT/WITH and SELECT/WITH
  payloads extracted from supported INSERT/CTAS statements.
- Generated optimizer SQL output must still be a read-only SELECT/WITH
  statement. If no useful rewrite is validated, the trusted optimizer output may
  be a safe recommendations-only/no-rewrite outcome instead of SQL.
- Python validation owns trust: physical tables, filters, projection, DISTINCT,
  top-level GROUP/ORDER/set operations, CTE shape, and top-level JOIN shape must
  remain within validated scope.
- Prompt constraints are not enough for safety. High-risk cases should fall back
  to safe recommendations instead of accepting an unsafe SQL draft, and
  no-benefit drafts should not be presented as optimized SQL.

## Claim Discipline

Keep these categories separate:

- backend data skew
- execution skew
- cardinality / row-estimate anomaly
- memory estimate anomaly
- write-path anomaly
- diagnostic recommendation
- proven cause

Rules:

- Backend data skew means parsed backend rows/records are unevenly distributed.
  It does not prove stale stats, cardinality underestimation, hot keys, or one
  slow host by itself.
- Execution skew requires parsed evidence that a backend or host is slower than
  peers.
- Write-path anomaly can be checked when it is `unknown`, but must not be stated
  as a proven cause.
- Row/cardinality underestimation requires actual rows greater than estimated
  rows or a ratio above `1`.
- Memory underestimation requires actual/peak memory greater than estimated
  memory or a ratio above `1`.
- Operator/profile counter time is not query wall-clock duration unless
  `analysis_facts.md` explicitly contains wall-clock evidence.

When in doubt, say evidence is missing.
