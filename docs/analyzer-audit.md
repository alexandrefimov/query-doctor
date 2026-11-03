# Analyzer Audit

Last reviewed: 2026-11-03

This public audit summarizes deterministic analyzer risks for Impala profile
diagnostics. It avoids local case identifiers, private smoke history, generated
paths, and per-run calibration details. Keep that evidence in local exclude-only notes.

## Scope

- `query_doctor.analyzer.*`: profile parsing, findings, backend-tail analysis,
  runtime metrics correlation, and deterministic facts rendering.
- `query_doctor.cli.batch_recent`: deterministic recent and known-query
  scoring.
- `query_doctor.cli.report`: facts passed to the LLM report writer and trusted
  report validation.
- Browser presenters that render analyzer-owned facts.

The analyzer contract is stable: Python owns facts, score reasons, evidence
limits, and confidence. The LLM can only phrase validated facts.

## Current Strengths

- Analyzer facts separate deterministic evidence from LLM wording.
- Runtime metrics are normalized into Python-owned statuses; raw time-series
  points are excluded from prompt input.
- Direct Impala profile facts publish provenance, profile format, resource
  facts, and timing facts without raw profile text.
- Already-provided Impala EXPLAIN artifacts now use a separate bounded parser
  that publishes only allowlisted raw-free optimizer-intent and estimate facts
  in the analyzer result (`analysis.json` when JSON output is requested). It
  recognizes bounded classic, legacy, boxed, and selected modern fragment
  layouts, including current `HDFS partitions` scan detail and structural stored
  statistics availability. It closes typed detail extraction after opaque
  expression sections, malformed tuple detail, or unexpected stored-statistics
  content, and degrades to partial on malformed fragment or attribute
  boundaries. It does not retain stored relation/column names, reuse runtime
  `OperatorFact`, create findings, or promote structural overlap into execution
  identity or causality.
- EXPLAIN correlation records its identity basis explicitly. An accepted
  external artifact remains `unbound_external_artifact`, while a missing or
  rejected plan source is `no_accepted_plan_source`; statement and execution
  identity remain `unknown` regardless of structural overlap.
- Primary-bottleneck routing keeps aggregate-only memory-estimate top findings
  in the SQL-shape lane. It does not treat memory estimates as runtime memory
  pressure without selected-query spill/scratch evidence, and it does not treat
  low-byte exchange context as runtime data movement.
- Recent batch scoring now uses typed `analysis.json` for core scoring
  components when available and records any markdown fallback source and reason
  in batch summaries.
- Query and stats optimization candidate scoring now use typed analyzer
  evidence for the full analyzer JSON path and record safe fallback labels when
  they remain on legacy rendered facts.
- Report validation rejects unsupported skew, host-tail, runtime metric, and
  root-cause claims.
- Browser presenters render analyzer facts through allowlisted, raw-free
  summaries.

## Open Risk Areas

### 1. Evidence source confidence must remain explicit

Missing metadata, missing metrics, unsupported profile dialects, or low-signal
profiles must degrade to `unknown`, `not_configured`, or explicit limitation
wording. They must not become failed diagnosis or unsupported root cause claims
unless the user explicitly required that source.

### 2. Scoring must stay typed or section-scoped and fact-owned

Recent-scan scoring should read typed analyzer facts where the typed contract
exists. Markdown fallback must read only the facts owned by each deterministic
section, and any fallback should remain visible in batch summaries. New labels
or debug appendices must not accidentally influence score, severity, candidate
selection, or root-cause wording.

### 3. Profile counter interpretation must stay evidence-tiered

Impala profile counters vary across versions and profile formats. Analyzer code
must distinguish elapsed/operator timing, cumulative thread counters, wait/CPU
context, memory pressure evidence, and unknown counters instead of treating all
similarly named counters as equivalent proof.

### 4. Runtime and metadata context are supporting evidence

Metrics, events, table stats, and metadata facts can strengthen diagnosis, but
they should not override missing primary profile evidence or turn correlation
into causation. Browser wording should keep verification steps explicit.

### 5. Analyzer facts rendered in UI or reports need raw-free tests

Any new analyzer fact that reaches Details, trusted reports, exported Markdown,
or action candidates needs tests proving it does not leak raw SQL, raw profiles,
hostnames, IP addresses, local paths, model names, process logs, or raw
artifact filenames.

### 6. EXPLAIN intent must remain separate from runtime evidence

Broadcast or partitioned joins, missing-statistics warnings, estimates, and
structural plan/profile overlap describe planner intent or correlation. They
must not feed scoring, recommendations, primary-bottleneck promotion, trusted
reports, or browser claims until same-revision provenance, representative
fixtures, direct runtime evidence, and focused causal-rule validation exist.
Co-location, adjacent query metadata, and operator-shape overlap do not provide
that provenance.

## Update Rule

Update this audit when analyzer fact ownership, scoring, confidence, or
browser/report rendering changes. Close a risk only with code, tests, and
documentation drift review; do not close it by wording alone.
