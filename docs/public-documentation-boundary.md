# Public Documentation Boundary

Last reviewed: 2026-11-03

This document defines what belongs in committed Query Doctor documentation and
what must stay local. The goal is to keep the public repository useful to
contributors without publishing private workstation state or agent continuation
notes.

## Rule

Committed documentation is public documentation. If a note only helps the next
local agent session, a private workstation, or a private smoke run, keep it in
local exclude-only notes instead of root, `docs/`, `deploy/`, or `.github/`
Markdown. Configure those notes in `.git/info/exclude` or a personal global
Git exclude, not in the tracked repository ignore file.

The committed agent docs may describe durable safety rules, validation paths,
review expectations, and sanitized runbooks. They must not carry current branch
state, chat-specific instructions, private cluster selectors, local output
paths, generated case locations, or raw validation evidence.

## Public Documents

| Area | Public content allowed | Keep local |
| --- | --- | --- |
| Root docs | Product overview, contribution rules, security policy, durable agent hard rules. | Private handoff notes, branch queues, local hostnames, local config details. |
| `docs/` contracts | Safety, architecture, optimizer, analyzer, engine, config, demo, release, and validation contracts. | Raw profiles, raw metadata, raw SQL, private evidence packages, generated artifact paths. |
| `deploy/` docs | Public deployment contracts, generic commands with placeholders, and sanitized readiness guidance. | Private endpoints, cluster selectors, credentials, live output, and workstation-specific commands. |
| Agent docs | Stable read path, safety constraints, worktree and validation rules, public-safe product baseline. | Per-chat continuation plans, active branch state, local smoke target names, workstation-specific setup. |
| Runbooks | Generic commands with placeholders or synthetic examples. | Real endpoints, real cluster IDs, real query IDs, private output directories. |
| Validation summaries | Path-free aggregate results and tested command lists. | Raw batch-summary paths, case directories, raw command output, private logs. |
| Model evaluation | Route scoring protocol, strict validation requirements, placeholder commands. | Local model rankings, latency/pass-rate tables, provider setup, private prompts/completions. |
| Changelog and release notes | Significant product, safety, workflow, and public baseline changes. | Local agent hygiene, private branch cleanup, per-run scratch details. |

## Local Notes

Use local exclude-only notes for ignored working notes. The path is a
workstation choice and should not be standardized in public docs. Keep it in
`.git/info/exclude` or a personal global Git exclude.

Local notes may mention private cluster selectors, temporary directories, and
per-run setup only when they remain ignored and are not copied into public
handoff text.

## Audit Checks

Route documentation changes through the repository preflight:

```bash
python3 scripts/agent_preflight.py --paths <changed-docs>
```

Follow its active-doc, public-doc, public-distribution-boundary, link, focused
test, and whitespace checks.
The public-doc and local-link checks include committed deployment Markdown under
`deploy/`.
Before staging or merge-ready cleanup, use the changed-worktree public-safety
scan when docs, configs, generated-artifact boundaries, or public files are in
scope; run the staged form after explicit staging. Review the diff manually
because scanners cannot determine whether every otherwise-safe detail is
durable.

When local context is needed for reproducibility, commit a placeholder or
sanitized aggregate contract and keep the full private evidence in local
exclude-only notes.
