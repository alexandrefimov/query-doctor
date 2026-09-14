# Engine Redaction Note v1

Last reviewed: 2026-09-14

This document is the canonical schema reference for raw-free engine evidence
package `redaction_note` payloads. It applies to bounded, operator-reviewed
offline or compact evidence packages such as current Trino and Spark package
intake. It is not an Impala production triage contract and does not add support
claims, live collection, Details output, trusted reports, optimizer behavior, or
SQL execution for any second engine.

## Scope

`redaction_note_v1` is a package-level safety gate for already-sanitized
evidence packages that carry a `manifest`, `redaction_note`, and `samples`
wrapper. The validator is shared in
`query_doctor/analyzer/engine_redaction_note.py`; engine-specific package
validators pass their own required redaction classes, sentinel tests,
rejection reasons, and boundary assertions.

The contract is intentionally raw-free:

- no raw SQL, plans, profiles, event logs, stack traces, warnings, endpoint
  URLs, hostnames, identifiers, local paths, artifact filenames, credentials,
  headers, secrets, or raw companion archives;
- no browser/report exposure;
- no root-cause or support claim;
- no SQL execution or Spark job execution.

## Required Fields

Every `redaction_note_v1` object must contain:

| Field | Required value or shape |
| --- | --- |
| `package_id` | Safe package label matching the package manifest. |
| `redaction_note_version` | String `"1"`. |
| `prepared_by_role` | Safe role label. |
| `prepared_date_utc` | `YYYY-MM-DD` UTC date. |
| `manual_reviewer_role` | Safe role label. |
| `redaction_status` | String `"checked"`. |
| `removed_field_classes` | Non-empty list of safe labels containing every engine-required class. |
| `rejected_record_counts_by_reason` | Mapping from safe reason label to non-negative integer, containing every engine-required reason. |
| `synthetic_sentinel_tests` | Mapping from safe test name to string `"yes"` for every listed test. |
| `boundary_assertions` | Mapping from safe assertion name to boolean `true` for every listed assertion. |
| `raw_companion_archive` | String `"none"`. |

Engines may require additional safe labels within the same mapping/list fields,
but they must not replace the v1 field names or downgrade mappings back to
lists. Extra sentinel tests and boundary assertions must also pass (`"yes"` or
`true` respectively); unsafe names or false values fail closed.

## Engine Parameters

Current engine package validators provide these engine-owned requirement sets:

- Trino: `TRINO_EVIDENCE_REQUIRED_REDACTION_CLASSES`,
  `TRINO_EVIDENCE_REQUIRED_REJECTION_REASONS`,
  `TRINO_EVIDENCE_REQUIRED_SENTINEL_TESTS`, and
  `TRINO_EVIDENCE_REQUIRED_BOUNDARY_ASSERTIONS`.
- Spark: `SPARK_EVIDENCE_REQUIRED_REDACTION_CLASSES`,
  `SPARK_EVIDENCE_REQUIRED_REJECTION_REASONS`,
  `SPARK_EVIDENCE_REQUIRED_SENTINEL_TESTS`, and
  `SPARK_EVIDENCE_REQUIRED_BOUNDARY_ASSERTIONS`.

Future package-style engine intake should conform to this v1 shape by default
and add only engine-specific required labels, not a second redaction-note
schema.

## Parallel Development Guard

Trino and Spark package-style branches must treat this document as the durable
schema source. Before resuming or closing a stale branch, merge current local
`main` and check for legacy note forms:

```bash
rg -n 'manual_review_status|"sentinel_tests_passed"|"boundary_assertions": \[' tests query_doctor scripts
```

`sentinel_tests_passed` may remain a CLI or builder confirmation flag, but it
must not appear as a JSON `redaction_note` field. Do not reintroduce
`manual_review_status`, list-style sentinel tests, list-style boundary
assertions, or per-engine local copies of the v1 schema. Every grep hit should
be an intentional negative/regression test or CLI confirmation flag, not
accepted package data or a live validator path.

Shared package-style changes should keep using
`engine_redaction_note.py`, `engine_intake_primitives.py`, and
`manifest_references.py`; engine-specific branches should only add their own
required safe labels or bounded behavior.
