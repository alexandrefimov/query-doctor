# Synthetic Demo Mode

Last reviewed: 2026-06-19

Language: English | [Russian](i18n/ru/demo-mode.md)

Query Doctor can generate a local synthetic demo pack that works without
Cloudera Manager, Impala, network access, or LLM calls.

For the public-style read-only demo, run one command:

```bash
query-doctor-web --public-demo
```

`--public-demo` generates a fresh synthetic pack under the system temp
directory, points the web UI at its `batch_summary.json`, synthetic
`action_outcomes.jsonl`, and static `trino_demo.json`, forces Python-only mode,
ignores default local config discovery and owner-source environment hints,
rejects explicitly loaded CM, direct Impala, Prometheus, metadata, Trino local
source, or owner-source settings, and blocks every POST route with a safe
read-only response.

If you need to inspect or reuse the generated pack manually, generate it under
a dedicated `query-doctor-*` temp directory. The generator refuses repository
paths, generic temp roots, and unsafe shallow output paths:

```bash
DEMO_PACK="${TMPDIR:-/tmp}/query-doctor-demo-pack"
query-doctor-demo --out "$DEMO_PACK" --overwrite
```

Then launch the web UI with the generated batch summary and local synthetic
action outcomes:

```bash
QUERY_DOCTOR_ACTION_OUTCOMES_PATH="$DEMO_PACK/action_outcomes.jsonl" \
  query-doctor-web --host 127.0.0.1 --port 8766 --batch-summary "$DEMO_PACK/batch_summary.json"
```

Open:

```text
http://127.0.0.1:8766/?query_group=workloads#scan-context
http://127.0.0.1:8766/?query_group=workloads#recent-results
http://127.0.0.1:8766/?query_group=optimization#recent-results
http://127.0.0.1:8766/?query_group=stats#recent-results
http://127.0.0.1:8766/?query_group=frequent_short#recent-results
```

The generated pack includes synthetic Recent queries / Finished queries cases
for:

- optimizer recommendation outcomes
- stats maintenance candidate evidence
- rejected/untrusted optimizer draft behavior
- admission/runtime workload regression
- Storage/HDFS runtime follow-up
- frequent-short workload handling
- mixed stats/query-shape/runtime signals without false certainty
- an unknown-but-useful bounded follow-up case
- direct Impala profile compatibility with missing optional endpoints treated
  as non-fatal
- local synthetic action outcomes for recommendation follow-up history

The generated pack also includes two read-only Trino Beta demo cases in
`trino_demo.json`. The UI renders them from static raw-free compact diagnosis
facts, without contacting a Trino coordinator, without metadata collection, and
without enabling Details pages, trusted reports, optimizer behavior, generated
SQL, or SQL execution.

The generated local synthetic action outcomes include enough comparable rerun
records with measured results to satisfy the default synthetic outcome gate for
the admission/runtime workload aggregate. The gate commits only the safe
aggregate summary, not the generated local outcome records.

The pack is intentionally local generated data, not committed fixtures. It is
safe for demos because it does not contain real profiles, real metadata, real
query text, local corpus paths from another environment, model names, or raw
runtime output. It is still demo data only and must not be used as performance
evidence.

## Read-Only Public Demo

The published GET UI remains the same synthetic demo, but external users cannot
start collection, generate reports, run optimizer actions, submit uploads,
cancel jobs, or write action-outcome feedback.

The default bind remains `127.0.0.1:8765`. Do not use ordinary local mode with
real local config, Kerberos material, cluster credentials, or generated cases
from real environments for public demos.

## README Screenshot Refresh

The README screenshots at `docs/assets/demo_search.png` and
`docs/assets/demo_finished_queries.png` should be refreshed after material web
UI or workflow layout changes. Use only the synthetic demo pack, and keep
generated demo output outside the repository. Keep
`docs/assets/readme-screenshot-provenance.json` in sync with any screenshot
path, route, viewport, or demo-pack version change:

```bash
DEMO_PACK="${TMPDIR:-/tmp}/query-doctor-demo-pack"
query-doctor-demo --out "$DEMO_PACK" --overwrite
QUERY_DOCTOR_ACTION_OUTCOMES_PATH="$DEMO_PACK/action_outcomes.jsonl" \
  query-doctor-web --host 127.0.0.1 --port 8766 --batch-summary "$DEMO_PACK/batch_summary.json"
```

Open the printed localhost URL and capture:

- the Query Inbox status, safe source/window/time-range/query-type scope,
  compact Scan scope and saved views disclosure, and materialized results for
  `docs/assets/demo_search.png`;
- the Finished Queries results view for `docs/assets/demo_finished_queries.png`.

For the results view, Scan context is the useful default because its compact
workload follow-up links dispatch the analyst to the repeated pattern that
should be opened next. Workload Details then shows the decision path: why the
pattern deserves attention, where to inspect representative cases, what
supported change direction to try, how to verify a comparable rerun, and local
synthetic outcome history.

```text
http://127.0.0.1:8766/?query_group=workloads#scan-context
```

Capture browser viewports and replace only the public synthetic screenshots.
Do not commit the generated demo pack, local config, local browser output, raw
profiles, raw SQL, local paths, or screenshots from real Cloudera Manager or
Impala data.
