# Query Doctor UI/UX Audit Notes

Last updated: 2026-09-09

This note records the accepted product takeaways from the May 2026 UI/UX audit.
The goal is to make Query Doctor usable by an analyst who needs to understand
what is wrong with a slow query, where to inspect it, and what to try next,
without weakening the safety contract or exposing raw artifacts.

## Accepted For Public README Entry Point Slice

- Keep the root README as a demo-first public entry point, not a complete
  command reference or implementation guide.
- Preserve the first user path: install, generate the synthetic demo pack,
  launch the local web UI, inspect Scan context workload follow-up links, then
  follow Details for why/where/change/verify guidance.
- Keep support boundaries short and unambiguous in root README. Apache Impala
  is the full production triage engine; Trino has bounded local production
  lanes only for retained-list Recent, One Query ID, raw-free materialized
  Details, Python Report, and optimizer guidance; Spark compact intake stays
  below public support. Link to the engine support matrix for the detailed
  contract.
- Remove the full console-script catalog from root README. Keep only the
  commands needed for the demo and a few high-signal `--help` entry points; the
  full command surface belongs in package metadata, local smoke docs, and
  focused runbooks.
- Keep contributor validation in root README lightweight. Normal changes should
  point to focused validation and `git diff --check`; broad gates such as
  `pre-commit run --all-files`, `scripts/local_gate.sh`, and
  `query-doctor-demo-preflight --public-release` belong to release or
  public-sharing work.

## Closed In Web UI Architecture Audit Slice

- Keep the synthetic demo pack as a first-class web UI validation path. Its
  trusted report fixtures now use the current Python Report artifact and marker
  contract instead of the legacy generic report marker, so demo Details pages
  exercise the same selected-case report baseline as production web flows.
- Make Details read as one continuous case page by removing the visual frame
  from the outer Details container. The verdict, Recommended change,
  Diagnostics, and action controls remain sibling sections with stable anchors
  and safe server-rendered content.
- Keep repeat scan entry points visible. Results pages now render an open
  `New scan` form instead of hiding the scan setup behind a disclosure button,
  and Details links land on that visible form.
- Keep owner-gated scan readiness compact. When no owner can be configured, the
  Username control remains a disabled dropdown with a short unavailable option
  and disabled submit button. When a local owner is configured, the dropdown is
  active and preselects the verified owner in Basic scan.
- Keep Basic scan visually flat inside the main form panel. The primary scan
  fields should not look like a nested technical fieldset; secondary filters may
  remain framed as a secondary group.

## Accepted For Current Quick-Win Slice

- Make the results table easier to scan: replace `Score`, `STATS`, and `META`
  shorthand with visible priority, table-stat, and metadata wording.
- Add a permanent results legend so color and small badges are not the only
  explanation of severity and status.
- Rename result groups from engine-centric labels to task-centric labels.
- Open Details in the current tab. The Details pages already have breadcrumbs
  back to results, and same-tab navigation avoids tab sprawl.
- Remove the production design-style toggle from the header; keep the light/dark
  theme toggle.
- Use Search depth as the visible Finished-query lookback control across
  sources, with a warning when large windows may increase collection-source
  load.
- Add a short first-run hint above the scan form that tells analysts the basic
  workflow.
- Split Recent scan setup into a default Basic scan layer with scan target,
  Search depth, and Run, plus collapsed Source and Advanced settings for source
  selection, filters, and collection limits. Owner-gated username selection
  stays visible in Basic scan when required by source visibility.
- Make the Recent results table read from the analyst signal first: `Finding`
  is the first content column after rank, while Query ID, user, priority, and
  collection statuses stay available as context.
- Replace the dense Results metric grid with one visible result-view toolbar
  plus a compact `Scanned N` note. Keep `Needs attention`, `Worth reviewing`,
  repeated workloads, rewrite opportunities, and stats candidates in the same
  toolbar instead of hiding secondary views behind another disclosure.
- Move secondary counters such as result rows, metadata contexts, and rewrite
  funnel counts into visible post-table `Scan context` so the first row list is
  not duplicated by top cards.
- Combine Results notices such as rewrite guidance, action-outcome count,
  empty-state notes, and scan warnings into one compact notes block so they do
  not compete as separate first-screen cards.

## Accepted For Recent Scan Cleanup Slice

- Remove the `More scan options` disclosure from New scan. The everyday path is
  Source cluster, workflow, finished-query date/hour, optional Minimum duration,
  and Run.
- Treat an empty `Minimum duration (sec)` field as an explicit request to keep
  all default Recent patterns available, including long queries and repeated
  short workload patterns. A user-entered duration narrows the scan to
  longer-running queries.
- Keep all available Results views in one toolbar. There are not enough result
  groups to justify a separate `More filters` disclosure.
- Render `Scan context` below the result rows as a visible explanation of
  coverage, scan notes, and compact workload follow-up links. Do not make
  analysts expand several similar blocks just to understand what context
  exists.
- On Help, collapse only large topic sections. Small two-line explanations
  should stay visible inside their parent topic.

## Accepted For Workload And Help Slice

- Results should show only compact repeated-workload follow-up links after the
  table, with the full repeated-pattern decision path on Workload Details.
- Keep result filters for repeated workloads, regressions, and frequent-short
  patterns visible in the toolbar instead of exposing a second workload
  analytics panel below the table.
- Make Workload Details an analyst decision page for one repeated pattern:
  `Why this pattern matters`, `Where to inspect`, `What to try next`, and
  `How to verify` come before representative queries, the snapshot, collapsed
  additional checks, coverage/shape, all selected cases, and limitations.
- Simplify Help around current product workflows. The main Help shortcuts
  should include the repeated workload path and omit bounded future-engine
  compact pages; future engine scope belongs in roadmap/support-matrix docs.

## Accepted For Details Decision-Flow Slice

- Keep the primary customer path focused on Recent scan -> Results -> Details
  -> recommendation -> verification. Secondary diagnostics, source coverage,
  and pipeline mechanics should remain below the decision path or inside
  collapsed sections.
- Details recommendation cards should read in analyst decision order:
  `Why this query matters`, `Where to inspect`, `What to try`, and
  `How to verify`. The primary recommendation stays visible; additional
  supported actions, candidate details, and diagnostics stay collapsed below
  that first decision path.
- Results should route the analyst with explicit next-action labels. Repeated
  workload surfaces should use `Workload p95`, `Workload impact`, `Next`, and
  `Open Details` instead of generic group/open wording.
- Known Query ID Details can share the same decision-page intro copy, but it
  remains a secondary one-query workflow. Do not make it look broader than the
  primary Recent scan path.

## UX Regression Checklist

- No visible UI copy should reintroduce raw/internal terms such as generic
  group wording, pipeline mechanics, result context, result notes, or mark
  result labels when an analyst-facing label exists.
- Results should make the next action clear without requiring docs: what needs
  attention, why it ranked high enough, which Details page to open, and whether
  a repeated workload pattern exists.
- Details first screen should preserve verdict, recommended change,
  verification path, and safe inspection location before expanded diagnostics.
- Important scan warnings must stay visible; secondary scan notes, source
  coverage, and workload context can sit below the table in `Scan context`
  without competing with the main result rows.
- Feedback recording should explain outcome and comparable-rerun intent, not
  expose implementation storage details.
- Outcome controls should ask whether the recommendation was applied and what
  happened on a comparable rerun. Local feedback summaries can mention workload
  confidence and next checks, but should not expose storage paths or raw
  outcome records.

## Accepted For Scan Context Trim Slice

- Keep `Scan context` as a light post-table strip: compact coverage, important
  scan warnings or notes, optional action outcome count, table key, and top
  workload follow-up links.
- Do not render the full workload digest, action queue, pool/owner breakdown,
  repeated workload group table, or rewrite funnel metrics in Results context.
  Those details belong in result filters and Workload Details.
- Preserve important scan trust signals such as safety-cap hit, running-only
  scope, and safe cluster event context without returning to the old
  duration/user/pool/parallelism checklist.

## Accepted For Diagnose Form Follow-Up Slice

- Keep `Recent queries` as the primary mode and `One Query ID` as the secondary
  mode, but make the first-run hint mode-specific so Query ID mode does not
  instruct users to choose a finished-query hour.
- Move the `Finished queries` / `Running now` scan target into Basic scan.
  This is a workflow choice, not an advanced filter. Advanced settings should
  keep only filters, scope notes, and collection limits.
- Keep `Source` shared by Recent and Known Query ID modes, and keep the selected
  source cluster always visible near the top. Source cluster is one of the first
  choices an analyst must verify before running diagnosis, so it should not be
  hidden inside Advanced settings.
- Put the primary Run action close to the Basic scan inputs on desktop, while
  preserving the full-width mobile action.
- Replace native select arrows with a consistent right-side affordance that
  gives the chevron breathing room and separates it from the selected value.
- Add visible keyboard focus treatment to segmented controls.
- When the user switches the scan target to `Running now` before submitting,
  label already visible finished-query results as previous results so stale
  output is not confused with the pending running scan.
- When the user switches to `One Query ID`, collapse already visible Recent
  results behind a previous-results disclosure so the one-query form remains the
  primary task.
- Compact the mobile header and Recent results pre-table area: keep navigation
  on one short row, keep the result-view toolbar usable, and move the table
  legend after the table.

## Kept As Follow-Up

- Mobile card layout for result rows. This is a larger responsive redesign.
- Unified Report/Optimizer naming and copy/print export. This touches action
  semantics and validation affordances, so it should be handled separately.
- Further Details safe fact expansion. The current Details view model now carries
  query-context timestamp, admission wait, and resource footprint when the
  analyzer provides them. Compile/admission/execution time splits and worst
  estimate-to-actual operator pairs still need explicit browser-safe analyzer
  facts before they can be displayed.

## Accepted For Source And Advanced Polish Slice

- Make the selected source cluster a persistent top-level control. It should be
  easy to verify and change before choosing Recent queries versus One Query ID,
  while credentials and endpoints remain local-config only.
- Move `Minimum duration (sec)` back into Basic scan. It is an everyday scan
  narrowing control, not an advanced collection detail.
- Treat collection concurrency as configuration-owned by default. `Parallelism`
  and `Metadata parallelism` already have bounded server defaults and local
  config keys; the default browser flow should not ask analysts to tune worker
  counts before a normal scan.
- Keep any collection override behind a clearly secondary disclosure only if it
  remains needed for local troubleshooting. Do not remove the server-side caps
  or local config support.
- Reconsider `Resource pool` as a default visible filter now that Username can
  be inferred and preselected for owner-gated local web runs. Pool remains
  useful on multi-tenant clusters and for admission/pool investigations, but it
  should not compete with source, scan target, time window, duration, username,
  and Run in the primary form.
- Keep Diagnose `Advanced settings` hidden by default. Show it only when local
  config explicitly enables editable advanced filters such as `user` or `pool`;
  otherwise those filters remain config-owned defaults and do not add another
  disclosure to the everyday scan path.

## Accepted For Desktop Web Audit Follow-Up Slice

- Move instructional Diagnose copy off the main form surface and into the
  nearest `i` help controls. Source-locality, Recent scan start guidance,
  Running caveats, and One Query ID scope are useful context, but they should
  not consume first-screen space when the user already knows the workflow.
- Keep the Diagnose page title compact. The mode explanation belongs in the
  `What to analyze` help popover, not as a permanent subtitle above the form.
- Keep the Running now layout tight after the user switches scan target: the
  Run button should stay next to the visible Basic scan fields, not parked at
  the far edge of a Finished-query grid with hidden date/hour controls.
- Keep the Results table key terse. It should explain column shorthand, not
  repeat the Details call to action or read like a second help paragraph.
- Keep the separate `/running` page consistent with the main Diagnose running
  mode. It should not reintroduce Resource pool, worker-count controls, or
  dense scope cards by default after those controls moved to config-owned
  behavior.
- Simplify the `One Query ID` mode by replacing the heavy scope card with a
  short helper sentence near the Query ID input. The task is a single explicit
  id, so the form should stay lighter than the Recent scan setup.
- Record broader desktop audit follow-ups before starting a larger visual
  redesign: Details can read as nested cards inside a large panel.
  This is not a safety blocker, but it remains a desktop polish target.
- Collapse standalone Query Optimizer scope strips into one secondary
  disclosure and move input rules into the SQL field help. The trust boundary
  must stay explicit, but it should not compete with the pasted-SQL task before
  the user asks for more context.
- Rework Help from one long document into a compact task surface: shortcut
  cards, a short quick-start list, and collapsed topic sections keep safety and
  workflow explanations available without making the page feel like a manual.

## Accepted For Secondary Desktop Pages Slice

- Keep the standalone Running Queries page aligned with Diagnose: source,
  minimum duration, and Run are the visible task controls, while live-snapshot
  caveats move into nearby field help instead of occupying form space or page
  header space.
- Keep Query Optimizer focused on paste-and-analyze. The primary Analyze action
  should sit directly after the SQL input, and Scope and safety should remain a
  secondary disclosure below the action rather than a wide peer control.
- Make Action outcomes useful when empty. A compact empty state should explain
  the next action and avoid rendering two blank tables before any feedback has
  been recorded.

## Accepted For Results And Details Control Slice

- Keep the Results table key visible, but style it as a quiet reference strip
  rather than separate chip-like controls.
- Keep available Details actions as explicit buttons. When report or optimizer
  work is not available for a case, render the reason as compact status text
  instead of a disabled action card.
- Do not remove the safety reason for optimizer unavailability; keep the same
  safe source-scope message visible without exposing raw SQL or local paths.
- In Details, a row with a clean analyzer score but a Medium/High action
  candidate should read as a follow-up candidate, not as a clean verdict.
  Candidate strength remains in the recommendation card so case priority and
  candidate strength do not collapse into one label.
- Reduce Details first-screen weight by splitting long verdict summaries into
  a short headline plus supporting signal, and replacing the large verdict KPI
  cards with a compact meta strip for Query ID, duration, and confidence.
  Priority stays in the severity badge alone; carrying it in both places
  printed the same string twice on every high-priority case.

## Accepted For Details Quick-Win Slice

- Merge the old Case overview and Analysis summary into one verdict block. The
  verdict title owns the main signal, the severity badge owns priority, and the
  meta strip adds supporting context such as duration/baseline and confidence.
- Make the verdict title read as a supported analyst review signal rather than
  an engine label: query-shape rewrite review, stats gaps, runtime queueing,
  skew, data movement, storage follow-up, or competing signals.
- Keep "what to do next" only in Recommended change so the page does not repeat
  action guidance in multiple formats.
- Promote existing baseline/regression, cluster-runtime, spill, table-stat, and
  review-anchor facts as secondary chips rather than equal-weight overview
  cards.
- When primary bottleneck classification is unavailable but a High/Medium
  action candidate exists, make the verdict say that a query-shape or stats
  review candidate was found instead of showing `Not classified`.
- Keep the low-level Diagnostics and evidence block collapsed by default.
- Use one page `h1`; make Recommended change and Details action controls
  section headings instead of nested page titles.
- Collapse action-outcome buttons behind one "Record rerun outcome" disclosure
  so the recommendation text remains the primary reading path.
- Rename Query Doctor pipeline timings so they are not confused with query
  runtime timings.
- Keep generated report and optimizer outputs available on Details, but collapse
  the bulky trusted result bodies by default so action controls do not dominate
  the page after a report or optimizer run.
- Make Details read as one continuous case page instead of nested cards inside
  a large card. The verdict, Recommended change, Diagnostics, and action
  controls should be sibling sections; repeated recommendation/evidence items
  can remain card-like for scanning.

## Accepted For Visual Quick-Win Slice

- Keep monospace typography for code, SQL, query ids, and compact technical
  values, but use the main sans-serif face for UI chrome such as inputs,
  badges, segmented controls, navigation, and outcome controls.
- Raise the default reading size to 14px and make page/section headings more
  visible without changing the existing page architecture.
- Normalize visible UI font weights to the 400/600/700 range.
- Darken muted color tokens so secondary text remains legible at the sizes used
  by the UI.
- Increase desktop control heights and provide 44px touch targets on mobile for
  common buttons, inputs, segmented controls, and the theme toggle.
- Use `not-allowed` for disabled controls instead of the wait cursor.

## Accepted For Diagnostics Flattening Slice

- Keep `Diagnostics and evidence` collapsed by default, but make the expanded
  content flat: `Pipeline`, `Runtime`, `Metrics`, `Metadata`, and `Score` are
  sibling sections instead of `Supporting findings` and `Evidence details`
  wrappers.
- Keep existing runtime, metrics, metadata, and score renderers on the same
  safe typed view-model inputs; this slice changes information architecture, not
  diagnostic claims.
- Remove the nested `All collected runtime metrics` disclosure. Correlated and
  all-metric tables remain in the Runtime metrics block without a fourth
  click-through layer.

## Accepted For Details Safe-Facts Slice

- Surface existing analyzer-owned `CM Query Context` / `Query Profile Context`
  fields in Details without reading raw provider payloads or raw artifacts.
- Promote bounded query context into the verdict chips when available: query
  window, query type, pool, admission wait, and a compact resource footprint.
- Add one `Query context` disclosure under Runtime diagnostics for the same
  allowlisted facts.
- Keep `CM Time-Series Context` metric-window timestamps hidden from browser
  output; only query-level start/end timestamps from the safe query-context
  section are eligible for display.
- Keep the synthetic demo pack aligned with the Details story by including safe
  query-context facts for the demo cases: query window, admission wait, and
  compact resource footprint.

## Accepted For Details Visible-Dedupe Slice

- Details has a durable product contract: the visible page is an analyst
  decision flow before it is an engineering evidence dump. It should answer
  why this query deserves attention, where to inspect the query or plan, what
  supported change direction to try, and how to verify the result.
- Treat the visible Details path as a four-step analyst story: why this query
  deserves attention, where to inspect the query or plan, what supported
  change direction to try next, and how to verify a comparable rerun.
- Keep collector-source organization out of the first screen. Pipeline status,
  profile sections, metric-provider details, and broad fact tables stay in the
  collapsed Diagnostics layer unless they directly support the verdict,
  recommendation, verification step, or an explicit limitation.
- Do not repeat the verdict sentence as a KPI card. The verdict title already
  owns the "what is wrong" answer, and the severity badge owns priority; KPI
  cards should add context such as duration, confidence, or baseline.
- Keep verdict chips for context that helps triage the case, not for action
  facts already shown in Recommended change. Review anchors and candidate ranks
  belong in the recommendation card.
- Keep Recommended change action cards focused on the decision: why the query
  matters, where to inspect, what to try, how to verify, and only
  non-duplicated supporting facts. Candidate score/rank and guardrails should
  stay secondary.
- Render the primary Recommended change card in analyst decision order:
  `Why this query matters`, `Where to inspect`, `What to try`, and
  `How to verify`. Additional supported actions, technical guardrails, and
  candidate score/rank details stay below that flow.
- Make the `Why` and `What to try` copy explain the decision, not the
  internal scoring model. Use language like "deterministic analysis found...",
  "start with this SQL/plan location...", and "try to reduce rows earlier..."
  while preserving guardrails that the recommendation is not a proven root
  cause or guaranteed speedup.
- Keep Diagnostics as the engineer layer. Its question groups should answer
  questions that are not already answered by the visible verdict and action
  card, such as when/how much work ran, baseline normality, and queue or cluster
  context.
- Keep unavailable Details report/optimizer actions compact. When no action can
  run for the selected case, show a single collapsed status row instead of
  giving unavailable notes the same weight as Recommended change.
- Distinguish case priority from candidate strength in labels. A High stats or
  query-shape candidate can still live on a Medium-priority case; the UI should
  not make that look contradictory.

## Rejected Or Not Applicable Now

- Hiding safety language everywhere. Safety copy should not dominate the first
  screen, but safety affordances and Help explanations remain important because
  Query Doctor handles trusted diagnostic output.
- Changing backend scan timezone behavior in this UI slice. The current engine
  still uses the configured Recent scan timezone; this slice only makes that
  visible to the user.
- Reworking the result table into a mobile card layout. That needs a dedicated
  responsive table pass because it changes row scanning and keyboard behavior.
- Compile/admission/execution time breakdowns and worst estimate-to-actual
  operator pairs are still follow-up work until the analyzer exposes them as
  explicit browser-safe facts.
