# Customer Readiness Priorities

Last reviewed: 2026-07-03

This note records the near-term product-readiness backlog for making Query
Doctor easier to show to external reviewers and design partners. It is public
planning material, not a private handoff, outreach script, or support promise.

## Decision

Impala customer readiness comes before broadening Trino or Spark product
surfaces.

The working adoption gate is five external or design-partner Impala diagnostic
runs with useful feedback. Those runs may start from one manually exported
profile or from a bounded Recent scan; both paths must stay safe, raw-free on
public surfaces, and no-LLM-capable.

The main operator path should evolve into a Recent-first Query Inbox over
already materialized safe cases: open the UI, pick the configured source and
time range, use first-screen result presets for the ranked bad queries, and
drill into Details. The one-profile path remains the shortest first-value demo
and restricted-access fallback, not the long-term center of production triage.

Trino and Spark work remains useful for bounded raw-free contracts, fixture
shape, and future engine seams. It should not distract from the current
customer path: Apache Impala Recent scans, Details, safe recommendations,
configuration, demo flow, and a UI that an operator can understand without
knowing the internal analyzer pipeline.

## Near-Term Product Slice

1. Query Inbox direction: make bounded Recent scans and materialized safe cases
   the primary operator path for production triage. Prioritize a clean results
   table, time-range and source context, first-screen high-signal filters,
   workload grouping, freshness/coverage status, and direct Details navigation
   over collector internals.
2. One-profile first value: keep the shortest entry path focused on one
   exported Impala profile to one useful diagnosis without requiring Cloudera
   Manager discovery, Kerberos setup, metadata collection, Prometheus, or an
   LLM provider.
3. Demo site: use the existing `query-doctor-web --public-demo` mode as the
   first read-only click-through demo surface. Do not build a separate demo app
   until the public-demo path proves insufficient.
4. Real-cluster validation: seek read-only Cloudera Manager plus Impala access
   for bounded Recent-scan validation. Keep outreach emails, contacts,
   endpoints, and partner-specific details outside the public repository.
5. Minimal config: keep a copy-pasteable Cloudera Manager starter config
   separate from advanced direct-Impala, Prometheus, metadata, and LLM settings.
6. UI/UX polish: prioritize Recent results and Details. Browser labels should
   use analyst workflow language such as `Scan context`, `Scan notes`,
   `Scan warnings`, `Workload follow-up`, `Workload p95`, `Open Details`, and
   `Record rerun outcome` instead of
   internal analyzer concepts. Results should keep available views in one
   visible toolbar, and Details should lead with why the query matters, where
   to inspect, what to try, and how to verify before collapsed evidence.
7. Documentation hygiene: keep README, demo, config, safety, roadmap, and
   support boundaries easy to find. Move deep contracts and historical material
   behind the documentation index, and avoid sending new users through the full
   knowledge base.

## Backlog Decisions To Review

| Item | Proposed action | Rationale |
| --- | --- | --- |
| Query Inbox operator path | Make Recent/materialized safe cases the main production triage path before adding broader providers | Operators get value when the bad queries are already ranked and filterable; this also creates the right vendor-neutral source contract without weakening support boundaries. |
| One-profile first value | Keep a one-profile diagnosis path visible before full Recent setup | It is the lowest-friction way for a design partner to see value without giving broad cluster access. |
| Demo site | Build from `--public-demo` first | Existing synthetic demo is read-only, local-safe, and already blocks writes. |
| Read-only Impala validation access | Keep partner-specific outreach outside public docs; public docs should record only the generic validation need and safety boundaries | Real Impala/CM validation is the highest-leverage way to improve the primary product, but outreach copy is not product documentation. |
| Minimal config | Keep `query-doctor-config.minimal.example.json` as the first-copy example | The current full example is useful, but too broad for first launch. |
| Documentation size | Use [repository-simplification-audit.md](repository-simplification-audit.md) before pruning | The docs are now a knowledge base; the entry path needs curation. |
| Russian docs | Treat English as canonical and keep only README-level and practical Russian user/operator paths fresh | Full mirrored deep contracts can drift faster than they help. |
| UI/UX | Keep refining Recent-results and Details polish | The UI should answer analyst questions before exposing diagnostics mechanics. |
| Demo brief | Keep or shorten as external-review demo reference | Useful for demos, but should not be required reading. |
| Synthetic Demo Mode | Keep | It is a real public-safe product/demo workflow. |
| Demo Preflight | Keep | It protects public sharing and release safety boundaries. |
| Cluster Doctor Contract | Keep as reference, de-emphasize in entry paths | It is a future seam, not a current product. |
| Changelog | Keep readable; archive or summarize old detail | Changelog should stay a release narrative, not a working log. |
| Brand Voice And Humor Policy | Shorten or rename to product voice guidance | Humor is allowed only on safe outer surfaces and should not dominate docs. |
| README screenshots | Keep only synthetic screenshots with provenance | The images are documentation; never replace them with real-cluster captures. |
| Scripts and tests | Classify roles before deletion | Many are safety/readiness gates; classify before pruning. |

## UI Label Decisions

- `Record rerun outcome` records whether a recommendation was applied and
  whether a comparable rerun improved, regressed, or stayed unchanged.
  Details shows the latest saved feedback for that case and recommendation,
  with a link to recorded outcomes. This confirmation is not calibrated
  confidence; comparable-rerun status is reported by the user.
- Results should expose repeated patterns as compact `Workload follow-up`
  links inside `Scan context`, not as a second analytics dashboard. Full
  repeated-pattern decisions belong on Workload Details and in the existing
  result filters.
- Results pages should route operators to the next object to inspect. Details
  and Workload Details should carry the deeper decision story.
- Workload Details should read as a repeated-pattern decision page: why it
  matters, where to inspect, what to try next, and how to verify come before
  the snapshot, coverage, selected-case lists, and limitations.
- Recommendation cards should lead with `Why this query matters`,
  `Where to inspect`, `What to try`, and `How to verify`. Additional supported
  actions and technical diagnostics remain available below the primary path.
- `Scan context` is the visible analyst-facing context block after result rows.
  Critical warnings stay visible above the table as `Scan warnings`; secondary
  notes, coverage, and compact workload follow-up links live below the table
  without competing with the result rows.

## Non-Goals For This Cleanup

- Do not remove safety contracts, raw-free gates, or validation scripts just
  because they are verbose.
- Do not collapse Trino or Spark boundaries into product support wording.
- Do not move private smoke targets, generated outputs, or branch-local notes
  into committed docs.
- Do not rewrite UI copy in a way that weakens diagnostic certainty, support
  boundaries, or raw-data redaction rules.
