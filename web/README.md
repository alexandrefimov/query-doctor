# Browser analyzer (prototype)

A static page that runs the Query Doctor deterministic analyzer entirely in the
visitor's browser through Pyodide. Drop an exported Impala text profile, get the
same `analysis_facts` output `query-doctor-analyze` writes locally.

The point is not convenience. The most common reason an operator cannot try
Query Doctor is that the profile must not leave their machine. On this page it
does not: there is no server, no upload, and no request to any host after the
page loads. That is checkable in DevTools in ten seconds, which the safety
documentation is not.

Deployed to https://alexandrefimov.github.io/query-doctor/ by
`.github/workflows/pages.yml` on pushes to `main` that touch `web/` or the
analyzer, and linked from the README. The workflow builds the site, serves it,
and runs `bench/check_page.py` against it; that check fails the build if the
page reaches any external host, raises a JS error, or produces no output.

## Build and run

```bash
web/build.sh
python3 -m http.server 8799 --directory web/dist
```

`build.sh` builds the wheel, fetches the Pyodide runtime from npm, generates the
synthetic sample profile, and writes everything into `web/dist`. The runtime is
fetched at build time rather than vendored so the repository does not carry
~13 MB of binaries; the built site still serves every asset from its own origin.

## Why the analyzer runs unmodified

The deterministic core imports no third-party package and no networking or
process stdlib. Measured on `origin/main` at 0.11.0:

```
query_doctor.analyzer.service + action_cards + facts_renderer +
runtime_diagnosis + scan_skew + case_bottleneck + data_movement +
memory_pressure + query_doctor.report
  -> 90 modules, 0 third-party packages
  -> no socket, ssl, subprocess, urllib, sqlite3, asyncio
```

Only the `query_doctor.cli` wrapper pulls sockets and subprocesses, because the
collectors live behind the same entry point. The page calls
`analyzer.service.analyze` directly with a `SimpleNamespace` of the seven
threshold arguments the analyzer reads, so no argparse and no CLI import.

Calling the analyzer directly skips the CLI's case staging, which is where
`query-doctor-analyze` applies redaction. The page therefore calls
`safety.redaction.redact_profile_text` itself with the same defaults
(`redact_hosts=True`, `redact_identifiers=False`) before analyzing, so users,
pools, emails, secrets, local paths, and host identifiers do not reach the
rendered facts. Nothing is transmitted either way, but the rendered output is a
browser surface, and screenshots of it travel.

## Measurements

Analyzer throughput, best of three runs, synthetic profiles shaped like real
Impala text exports (`bench/make_profile.py`):

| profile | CPython 3.13 | Pyodide | ratio |
| --- | --- | --- | --- |
| 103 KiB | 52 ms | 113 ms | 2.2x |
| 1013 KiB | 568 ms | 1101 ms | 1.9x |
| 4154 KiB | 2297 ms | 4263 ms | 1.9x |

Roughly 1.1 s per megabyte, linear, with identical output to native (operator
counts and fact-group counts match exactly).

Full page in headless Chromium (`bench/check_page.py`):

```
boot: 1196 ms      (runtime + wheel + analyzer import)
run:  127 ms       (103 KiB sample profile)
network: 8 requests, external hosts: none  (the check fails on either)
JS errors: none
```

First-visit transfer, gzipped: Pyodide wasm 3.4 MB, Python stdlib 2.4 MB,
runtime glue 0.2 MB, query-doctor wheel 1.19 MB, page under 15 KB. About 7.2 MB,
cached afterwards. 5.8 MB of that is Python itself and cannot be reduced.

The wheel could be reduced: the analyzer core is 587 KiB of source out of
5559 KiB in the package. It is deliberately not trimmed yet. An import trace
from one sample profile is not proof that other profile shapes take the same
path, and shipping a wheel that fails on an unusual export would be a worse
outcome than a megabyte of transfer. Trim only against a corpus that covers the
profile dialects in `analyzer/profile_counter_registry.py`.

## Bench harness

- `bench/make_profile.py <bytes> <out>` — synthesize an Impala-shaped profile.
  Synthetic only; no real cluster data, hosts, users, tables, or SQL.
- `bench/bench.py <profile>` — time `analyze` on CPython.
- `bench/harness.mjs <scratch-dir> <profile...>` — the same timing under Pyodide
  in Node. Needs `npm install pyodide` in its working directory.
- `bench/check_page.py [out.png]` — drive the served page in Chromium and fail
  if it reaches an external host, raises a JS error, or renders nothing. Set
  `QUERY_DOCTOR_PAGE_URL` to check the deployed site instead of a local build.
  Needs `pip install playwright` and `playwright install chromium`.

## Open decisions

- ~~**Hosting.**~~ Settled: `actions/deploy-pages` from `web/dist`. A `gh-pages`
  branch was rejected because it would commit ~13 MB of Pyodide binaries per
  rebuild and let the deployed site drift from source.
- **Output language.** Settled: English. Page chrome and `render_md` output now
  match. A Russian layer would follow the `docs/i18n/ru/` convention, not a
  second page.
- **Sample profile.** Currently synthetic and generated at build time. A
  hand-written sample with a real pathology would demo better than uniform
  generated counters.
