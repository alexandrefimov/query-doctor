# Development Practices

Last reviewed: 2026-11-03

This document records engineering heuristics for keeping Query Doctor
maintainable. Mandatory trust rules live in
[safety-contract.md](safety-contract.md), behavior ownership in
[code-map.md](code-map.md), and focused validation routing in
[test-matrix.md](test-matrix.md) plus `python3 scripts/agent_preflight.py`.

## Design By Owner

Identify the behavior owner before editing:

- collectors own explicit bounded external reads;
- analyzers own deterministic facts and limitation states;
- report code owns LLM wording, sanitization, and validation;
- optimizer code owns read-only SQL analysis, Python recipes, and trust checks;
- web code owns routes, jobs, presenters, and browser-safe rendering.

Keep a change inside its owner unless a contract genuinely crosses boundaries.
When one outcome spans collection, facts, validation, and rendering, keep the
file groups or commits reviewable and validate each boundary explicitly.

Do not add speculative engines, placeholder adapters, generic service layers,
or framework seams. Apache Impala is the full production triage engine; Trino
and Spark are limited to the status in
[engine-support-gap-matrix.md](engine-support-gap-matrix.md). That matrix, not a
copied lane inventory here, owns future support changes.

## Module Discipline

Prefer focused modules over files that mix orchestration, parsing, validation,
and presentation. Useful extraction boundaries include:

- parser or tokenization helpers;
- deterministic analyzer facts;
- validation rules;
- prompt or contract rendering;
- browser presenters and view models;
- subprocess command builders;
- provider-specific adapters;
- pure formatting helpers.

Split by responsibility and reviewability, not an arbitrary line target. Large
fixture or scenario files can be legitimate; tiny one-use wrappers and broad
helper buckets often make safety review harder. Do not pause requested product
work for cosmetic file movement.

## Change Shape

- Keep every changed line tied to the requested behavior, regression coverage,
  or required documentation drift.
- Preserve public interfaces, routes, flags, config semantics, and trust
  markers unless the task explicitly changes them.
- Prefer boring explicit code over generic machinery whose safety properties
  are difficult to inspect.
- Separate behavior changes from formatting-only churn and unrelated cleanup.
- For a bug fix, capture the failure in a focused test when practical.
- Treat a new shared helper as a contract change: identify all consumers and
  broaden validation accordingly.

## Parallel Work

Use separate task worktrees so unrelated changes and agents do not collide.
The exact create, validate, commit, local-merge, and cleanup sequence lives in
[agent-quickstart.md](agent-quickstart.md). Inventory worktrees with
`python3 scripts/worktree_status.py`; never force-clean dirty, unknown, or
another agent's work.

## Test Strategy

Choose evidence by risk:

- focused unit tests for the changed owner;
- a regression test for each bug fix;
- safe-accepted and unsafe-rejected cases for validators and redactors;
- route or presenter tests for browser-visible dynamic text;
- CLI tests for flags, bounds, commands, and generated artifact contracts;
- broader tests for shared helpers, trust boundaries, schemas, and
  cross-workflow behavior.

Use `python3 scripts/agent_preflight.py` and the relevant row in
[test-matrix.md](test-matrix.md) instead of maintaining another command list
here. Always run `git diff --check` before committing.

## Local Automation

Install development dependencies once with `python3 -m pip install -e
".[dev]"` inside the chosen virtual environment. Default local workflows should
remain offline after installation.

Use `scripts/local_gate.sh` for broad release or public-sharing validation, and
`pre-commit run --all-files` when the full hook set is required. Ordinary
changes should start with the focused preflight route. The scripts themselves
own their current check inventories; do not duplicate those inventories in
documentation.

Public-safety scanners are guardrails, not substitutes for reviewing a diff.
Use the changed-worktree scan when public docs, configs, generated-artifact
boundaries, or release material could contain local state.

## Dependency Policy

Keep dependency additions exceptional. A new dependency needs a clear owner
and a correctness or maintainability benefit that the standard library and
existing dependencies cannot provide safely. Do not add dependencies for small
formatting helpers, generic abstractions, optional decoration, or functionality
that weakens local-first behavior.

## Error And Logging Rules

Terminal output may include actionable implementation detail after redaction.
Browser-visible output and trusted artifacts must remain product-level and
safe. Prefer stable error categories over raw exceptions, and add a regression
test whenever a new error can reach the web UI or a trusted artifact.

Never expose raw provider output, SQL, paths, subprocess streams, model names,
artifact filenames, or secrets in browser-visible error text.

## Documentation Roles

Update docs in the same slice when behavior, safety boundaries, workflows,
public commands, or trust contracts change. Keep the roles distinct:

- `AGENTS.md`: hard rules and source routing;
- `docs/agent-quickstart.md`: operational sequence;
- `docs/agent-playbook.md`: change-type deltas;
- `docs/test-matrix.md`: focused validation selection and commands;
- `docs/codex-handoff.md`: durable product and safety baseline;
- this file: engineering rationale and heuristics;
- `docs/changelog.md`: significant product, safety, workflow, or baseline
  changes.

Treat committed Markdown as public. Keep branch state, workstation setup,
private smoke evidence, and continuation notes in ignored local notes as
defined by [public-documentation-boundary.md](public-documentation-boundary.md).
