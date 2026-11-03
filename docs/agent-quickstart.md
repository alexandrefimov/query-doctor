# Agent Quickstart

Last reviewed: 2026-11-03

This is the canonical operational sequence for an authorized code or
documentation change. Repository hard rules remain in [../AGENTS.md](../AGENTS.md).
For review, explanation, or diagnosis, use the read-only parts of this sequence
and do not create changes, commits, or merges unless the user asks for them.

## 1. Orient

From the repository root:

```bash
git status --short --branch
python3 scripts/worktree_status.py
```

Preserve unrelated changes and treat unknown or dirty worktrees as owned by
someone else. If the requested outcome or safety boundary is ambiguous, resolve
that before editing.

## 2. Isolate The Change

Use a fresh task worktree from local `main` unless the user explicitly requests
the current worktree:

```bash
git worktree add "$HOME/query-doctor-worktrees/<task>" -b "codex/<task>" main
```

Do not reuse another agent's branch or worktree. If a required branch is already
checked out, create a separate branch or arrange an explicit handoff.

## 3. Scope Before Broad Reading

When planned paths are known, route the task before loading large documents:

```bash
python3 scripts/agent_preflight.py --paths <planned-paths>
```

Follow the matched read path and focused checks. Use
[agent-playbook.md](agent-playbook.md) only when a human-readable change-type
route helps, and [test-matrix.md](test-matrix.md) when focused test selection is
needed. Feature runbooks own long live and retained-evidence command sequences.
Do not read every agent document by default.

For unfamiliar or cross-module work, use one focused graph query:

```bash
python3 scripts/agent_code_graph.py --explain <path> --compact
```

Use `--changed --compact` after edits when several areas are involved. Graph
output is orientation, not authority; verify it against current code and tests.

## 4. Implement A Traceable Slice

Keep the diff limited to the requested behavior, its regression coverage, and
required documentation drift. Prefer a focused failing test before a bug fix.
Do not mix formatting cleanup, speculative refactors, or unrelated findings
into the slice.

For engine status or second-engine work, use
[engine-support-gap-matrix.md](engine-support-gap-matrix.md) rather than copied
capability lists. For public/local boundaries, use
[public-documentation-boundary.md](public-documentation-boundary.md).

## 5. Validate By Risk

After edits, let the router inspect the actual diff:

```bash
python3 scripts/agent_preflight.py
python3 scripts/agent_code_graph.py --changed --compact
```

Run the focused checks it selects. Always run:

```bash
git diff --check
```

For committed Markdown or other public-facing files, also run the applicable
public documentation checks selected by preflight. Use
`python3 scripts/check_staged_public_safety.py --changed` before merge-ready
cleanup when docs, configs, generated-artifact boundaries, or public files are
in scope.

Broaden beyond focused tests only for shared helpers, trust boundaries,
cross-workflow contracts, release/public-sharing work, or focused failures.
Use `scripts/local_gate.sh` for a broad release or public-sharing gate and
`pre-commit run --all-files` when the full hook set is required.

## 6. Commit Explicitly

Review the diff, stage intended paths one by one, and commit the verified slice:

```bash
git status --short --branch
git diff -- <paths>
git add <path> [<path> ...]
git commit -m "<summary>"
```

Never use `git add .` or `git add -A`. Do not commit local configs, generated
cases or outputs, caches, virtual environments, secrets, or ignored local notes.

## 7. Integrate And Clean Up

Before merging:

```bash
python3 scripts/worktree_status.py
python3 scripts/agent_code_graph.py --merge-risk --base main --compact
git rev-list --left-right --count main...<branch>
```

If local `main` advanced, merge `main` into the task branch and rerun focused
validation there. When the task branch is committed, clean, validated, and a
direct descendant of current `main`, fast-forward it from the main worktree:

```bash
git -C "<main-worktree>" status --short --branch
git -C "<main-worktree>" merge --ff-only "<branch>"
```

Then remove only the clean completed task worktree and delete its merged local
branch. Do not force cleanup or discard dirty, unmerged, unknown, or another
agent's work. Remote pushes, PR finalization, rebases, amends, force-pushes, and
destructive Git operations require the explicit authorization defined in
[../AGENTS.md](../AGENTS.md).
