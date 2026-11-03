# Agent Playbook

Last reviewed: 2026-11-03

Use this file as a human-readable change router after the hard rules in
[../AGENTS.md](../AGENTS.md) and the operational sequence in
[agent-quickstart.md](agent-quickstart.md). The executable router is
`python3 scripts/agent_preflight.py --paths <planned-paths>`; it and
[test-matrix.md](test-matrix.md) own focused validation selection and commands.
Feature contracts own long operator, live-system, and retained-evidence
sequences.

Do not read every row. Match the requested change, read the named contract and
touched implementation, then run the focused route.

## Change Routes

| Change | Read first | Primary evidence | Documentation drift |
| --- | --- | --- | --- |
| Docs or agent guidance | Target doc and [README.md](README.md); add [public-documentation-boundary.md](public-documentation-boundary.md) for agent, runbook, validation, or release material | Active-doc, public-doc, link, agent-tooling, and whitespace checks selected by preflight | Public README only when product workflow/positioning changed; changelog only for a significant baseline change |
| Browser route, Details, Help, or presenter | [safety-contract.md](safety-contract.md), [code-audit.md](code-audit.md), touched route/presenter/UI | Focused route/presenter tests plus browser-safety rejection; rendered UI checks when layout changed | README/screenshots for material first-screen changes; changelog for workflow or trust-boundary changes |
| Report writer, sanitizer, or validator | [safety-contract.md](safety-contract.md), [code-audit.md](code-audit.md), touched report modules | Safe-accepted and unsafe-rejected validator tests, trusted-marker tests, browser safety when rendered | Safety/report contracts and changelog when trust behavior changed |
| Query Optimizer, recipe, or SQL validator | [query-optimizer-contract.md](query-optimizer-contract.md), [code-audit.md](code-audit.md), touched optimizer modules | Detection, deterministic draft, accepted/rejected validation, stale-marker, and no-echo tests | Optimizer contract for recipe/trust changes; changelog for behavior or safety changes |
| Cloudera Manager, direct Impala, or metadata collection | [safety-contract.md](safety-contract.md), [codex-handoff.md](codex-handoff.md), touched provider modules | Bounds, read-only allowlist, config, redaction, degradation, and safe-error tests | Collector/config docs and changelog when supported behavior changed |
| Analyzer fact, scoring, or recommendation | [analyzer-audit.md](analyzer-audit.md), [code-audit.md](code-audit.md), touched analyzer modules | Fixture fact states, confidence/limitation, scoring, candidate, and downstream claim tests | Analyzer audit and changelog for fact-contract or user-visible diagnosis changes |
| Batch, Recent, Known Query ID, or worker flow | [codex-handoff.md](codex-handoff.md), [code-audit.md](code-audit.md), touched orchestration/job modules | Candidate, timeout/failure, progress, no-auto-LLM/optimizer, and browser-safe state tests | Workflow docs, README, and changelog when the user path changes |
| Trino support, imports, compact diagnosis, or local production web lanes | [engine-support-gap-matrix.md](engine-support-gap-matrix.md), [safety-contract.md](safety-contract.md), relevant Trino contract and touched modules | Preflight-selected fact/source/capability/product-surface/support-gap tests | Matrix, capability/engine docs, README, and changelog only when a promotion gate actually changes status |
| Spark compact intake, evidence package, or diagnosis | [engine-support-gap-matrix.md](engine-support-gap-matrix.md), [engine-redaction-note-v1.md](engine-redaction-note-v1.md), relevant Spark contract and touched modules | Compact schema/intake, raw-free boundary, capability, handoff/readiness, and no-support-claim tests | Matrix and Spark docs when the bounded compact contract changes; never imply production support |
| Config, packaging, deployment, or dependency | [configuration.md](configuration.md) or the relevant deployment doc, [development-practices.md](development-practices.md), touched schema/build files | Config compatibility, render/build, packaging, or deployment checks selected by preflight | Public README and changelog when install/runtime behavior changes; explain any dependency addition |
| Agent tooling or validation routing | [test-matrix.md](test-matrix.md), [code-map.md](code-map.md), touched script and its focused tests | Agent-tooling tests plus active/public docs and whitespace checks | Update this router or matrix only when ownership or commands changed |

## Cross-Cutting Decisions

- Use [code-map.md](code-map.md) or one focused code-graph query to find an
  unfamiliar owner. Verify the result against current code and tests.
- The engine support matrix owns mutable support status. Do not describe Trino
  as fixture/private-preview-only: it has the bounded raw-free local production
  lanes recorded there. Do not generalize those lanes into Running, broad
  history, product metadata collection, LLM reports, Query Optimizer jobs,
  generated SQL, SQL execution, or broader/shared support.
- Spark remains compact-only and below production support. Cross-engine helper,
  schema, manifest-reference, or capability changes are explicit shared slices,
  not incidental edits inside one engine feature.
- Browser and trusted-artifact changes require both a safe allowed case and an
  unsafe rejected case. Raw provider payloads, SQL, paths, filenames, secrets,
  model names, runtime internals, and exception text do not become UI copy.
- Representative real-source audits are promotion evidence, not ordinary unit
  validation. Keep their inputs and retained outputs local and raw-free; commit
  only stable contracts and sanitized aggregate guidance.
- Start focused. Broaden only when the change moves a shared helper, trust
  boundary, cross-workflow contract, or when focused failures expose wider risk.
