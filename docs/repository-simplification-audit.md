# Repository Simplification Audit

Last reviewed: 2026-09-14

This audit classifies documentation, scripts, and tests before any pruning.
It is intentionally conservative: do not delete safety gates, compatibility
tests, raw-free fixtures, or support-boundary docs only because they are
verbose.

## Documentation Classes

| Class | Meaning | Current examples |
| --- | --- | --- |
| Active | Entry path, product contract, safety contract, or required agent guidance. | README, docs index, safety contract, architecture, support boundary, engine expansion, engine support matrix, roadmap, code audit, test matrix. |
| Reference | Supporting design, runbook, audit, or evidence-contract material. | configuration, credentials, security model, UI/UX audit, release checklists, Trino/Spark contracts, Cluster Doctor contract. |
| Archived | Historical detail useful for maintainers but not a current contract. | older release notes, resolved code-audit guards, superseded discovery spikes. |
| Local-only | Private targets, generated outputs, smoke selectors, temporary paths, and one-run validation evidence. | ignored local notes, generated demo packs, retained private smoke artifacts. |

## Changelog Rule

Keep `docs/changelog.md` readable as a release narrative. Add only significant
user-facing workflow, safety, trust-boundary, collector/analyzer, or major
documentation baseline changes. When historical detail makes the file hard to
scan, move old detail into curated release notes or an archive in a dedicated
cleanup slice rather than continuing to append every internal change.

## Russian Documentation Rule

English remains canonical. Maintain Russian docs only for README-level and
practical user/operator paths: demo, configuration, credentials, local smoke,
security, and safety overview. Internal, agent, release, research, roadmap,
audit, and engine deep-dive docs stay English-only unless a future explicit
user-facing requirement changes that boundary.

## Script Roles

| Role | Meaning | Current examples |
| --- | --- | --- |
| Product CLI | Installed command exposed through package metadata. | `query-doctor-web`, `query-doctor-batch-recent`, `query-doctor-demo`, Impala collectors, report and optimizer commands. |
| Preview/bounded CLI | Installed command for bounded non-Impala intake or compact diagnosis; not production support. | Trino import/check commands, Spark compact package/diagnosis commands. |
| Dev gate | Maintainer validation script that audits readiness or safety. | `audit_*`, `check_*`, `agent_preflight.py`, `web_static_smoke.py`. |
| Release gate | Script used before public release, demo, or handoff. | `local_gate.sh`, public-safety checks, release-history checks, demo preflight. |
| Fixture or handoff builder | Script that creates sanitized fixtures or retained raw-free manifests. | `build_*`, `export_*`, evidence-package builders. |
| Legacy compatibility helper | Compatibility path retained for old environments or package workflows. | legacy config/artifact fallback tests. |

## Test Roles

- Keep safety and browser-display tests until an equivalent stronger guard
  exists.
- Keep compatibility tests while old public artifacts, config aliases, or
  generated fixtures are still accepted.
- Mark research/preview tests by supported boundary, not by deletion priority.
- Before deleting a test, identify the product, safety, compatibility, or
  fixture contract it protects and move that coverage if the contract remains.

## First Cleanup Candidates

- Summarize or archive older changelog detail after the next curated release
  notes update.
- Shorten external-review or demo-only docs that are not part of the
  first-run path.
- De-emphasize future Cluster Doctor and second-engine references in entry
  paths while keeping their safety contracts indexed as reference material.
- Keep the Russian layer narrow and update it only when README-level or
  practical user/operator instructions change.
