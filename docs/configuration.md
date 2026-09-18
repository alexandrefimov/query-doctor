# Configuration Reference

Last reviewed: 2026-06-22

Language: English | [Russian](i18n/ru/configuration.md)

Query Doctor reads non-secret local settings from a JSON config file. Keep
passwords, tokens, keytabs, ticket contents, Authorization headers, and API keys
out of this file. Put those values in environment variables or local env files
described in [credentials.md](credentials.md).

The committed
[query-doctor-config.minimal.example.json](../query-doctor-config.minimal.example.json)
is the first-copy Cloudera Manager starter. The fuller
[query-doctor-config.example.json](../query-doctor-config.example.json) shows
CM, direct Impala, Prometheus, metadata, and LLM routing fields in one
advanced template. Add optional fields from the reference below only when you
need to override a built-in default.

A third template,
[query-doctor-config.direct-impala.example.json](../query-doctor-config.direct-impala.example.json),
drops Cloudera Manager entirely: one direct Impala cluster, SQLite Recent
history with a 30-day retention, running queries included, and no LLM. The
end-to-end install and restart check that uses it is
[Door 4](first-path.md#door-4-connect-directly-to-kubernetes-impala).

## Config Location

Preferred local path:

```bash
mkdir -p ~/.qdcreds
cp query-doctor-config.minimal.example.json ~/.qdcreds/query-doctor-config.json
chmod 600 ~/.qdcreds/query-doctor-config.json
```

The file is non-secret, but it often contains hostnames, usernames, local paths,
and cluster names, so treat it as private workstation state.

Most packaged commands that support automatic local config discovery use this
order when `--config` is omitted:

1. `query-doctor-config.json` in the current working directory.
2. `~/.qdcreds/query-doctor-config.json`.
3. `query-doctor-config.json` in the repository root, when the command allows
   the repository default.
4. Legacy `.query-doctor-cm.local.json` in the current working directory.
5. Legacy `.query-doctor-cm.local.json` in the repository root, when allowed.

The local web bootstrap wrapper has a workstation-oriented order:

1. `QD_CONFIG`, when set.
2. `$QD_CREDS_DIR/query-doctor-config.json`, defaulting to
   `~/.qdcreds/query-doctor-config.json`.
3. Repository-local `query-doctor-config.json`.
4. Legacy repository-local `.query-doctor-cm.local.json`.

Explicit `--config PATH` always wins. CLI flags then override environment
variables, config values, and code defaults for the settings they control.
Credential environment variables such as `CM_PASSWORD`, `CM_TOKEN`, and
`KRB5CCNAME` remain outside the JSON config; `KRB5CCNAME` overrides configured
`krb5ccname`.

## Minimal Cloudera Manager Config

Use this shape, also available in
[`query-doctor-config.minimal.example.json`](../query-doctor-config.minimal.example.json),
for the normal Cloudera Manager workflow:

```json
{
  "clusters": [
    {
      "id": "cm-impala",
      "label": "Impala via Cloudera Manager",
      "cluster_type": "cm",
      "cm_url": "https://cm.example.com:7183/",
      "cluster": "example_cluster",
      "service": "impala"
    }
  ],
  "language": "en",
  "recent_scan_timezone": "UTC"
}
```

Provide `CM_USERNAME` plus `CM_PASSWORD` or `CM_TOKEN` through the shell
environment, for example from `~/.qdcreds/cm-ro.env`. The `username` config
field remains supported as a non-secret fallback, but keeping the CM auth user
with the CM auth secret avoids drift between files.

Add `ca_bundle` only when your Cloudera Manager endpoint uses a private CA,
and add metadata or direct-Impala fields only after the basic Recent scan path
is working.

Direct `query_doctor.cli.batch_recent` runs also read the local Cloudera
Manager env file before preflight. Discovery order is:

1. `QD_CM_ENV`, when set.
2. `$QD_CREDS_DIR/cm-ro.env`, when `QD_CREDS_DIR` is set.
3. `~/.qdcreds/cm-ro.env`.

The file is parsed as simple `KEY=value` or `export KEY=value` assignments
without shell evaluation. Only `CM_USERNAME`, `CM_USER`, `CM_PASSWORD`,
and `CM_TOKEN` are accepted from this file. Already-exported environment
variables win over file values. Kerberos cache and principal settings should
come from the shell environment, wrapper defaults, keytab inference, or JSON
config where appropriate, not from `cm-ro.env`.

## Minimal Direct Impala Config

Use this shape when Cloudera Manager is not the profile source:

```json
{
  "cluster_type": "impala",
  "impala_profile_hosts": [
    "impalad-worker-1.example.com",
    "impalad-worker-2.example.com"
  ],
  "impala_kerberos_service_name": "hive",
  "metadata_coordinator": "impala-coordinator.example.com:21050",
  "metadata_kerberos_service_name": "hive",
  "metadata_kerberos_host_fqdn": "impala-coordinator.example.com"
}
```

Direct Impala collection reads only bounded daemon debug web endpoints for
Recent, Running, or one Known Query ID workflow. It does not execute SQL.
For Recent and Running scans, history depth is limited by what the configured
coordinator query-list endpoints still expose. Upstream Impala defaults the
coordinator query log to `--query_log_size=200` entries, with an additional
`--query_log_size_in_bytes` cap. To make more completed direct-Impala queries
available to Query Doctor, increase those Impala daemon settings on each
coordinator that can accept client queries. Avoid unbounded settings in
production; larger query logs can increase coordinator memory use and make the
Impala Web UI `/queries` JSON page slower to render or serialize before Query
Doctor can apply its own candidate limits.

Direct Impala history can grow through several distinct options:

- **Coordinator query-log retention** is the current supported path. Increase
  `--query_log_size` and, if needed, `--query_log_size_in_bytes` in Impala daemon
  configuration on every coordinator. This keeps Query Doctor on the existing
  daemon `/queries` and `/profile` endpoints, but the history is still
  coordinator-local and can increase Web UI response cost. Raise
  `impala_query_list_max_bytes` to match: `/queries?json` grows with the retained
  log, and a response over that ceiling is refused rather than truncated, which
  leaves Recent and Running scans with nothing to read.
- **Profile-log directory ingestion** is a future source option, not current
  support. Impala can write completed profile logs separately from the online
  query log. If Query Doctor adds this source, it must read an
  operator-configured read-only local or mounted directory, not arbitrary node
  or pod paths.
- **External history indexes** such as Loki or OpenSearch are also future
  source options. They must use operator-owned query templates or source
  contracts with explicit cluster labels, time windows, byte and record bounds,
  and redaction guarantees. Browser users must not submit arbitrary log search
  DSL, credentials, node paths, or raw query/profile payloads through Query
  Doctor.

For load-balanced metadata connections, keep `metadata_coordinator` as the
reachable `HOST:PORT` endpoint and set `metadata_kerberos_host_fqdn` when the
Kerberos server principal uses a different DNS hostname.
Metadata collection runs read-only `SHOW` statements over HiveServer2 and stays
bounded by the metadata limits below. `metadata_coordinator` must name the
coordinator's HiveServer2 port, not its Beeswax port.
For Cloudera Manager Recent batches, the metadata refresh may use table
references extracted from discovery statements before profile identifier
redaction. Those identifiers are passed only to the bounded metadata subprocess;
progress, summaries, trusted reports, and pipeline plan output remain raw-free.

## Multiple Clusters

Prefer `clusters[]` when one workstation talks to more than one target. Keep
cluster-specific settings inside each cluster object. Avoid top-level
`cm_url`, `cluster`, or `service` when mixing Cloudera Manager and direct
Impala clusters; otherwise direct clusters inherit irrelevant CM defaults.

```json
{
  "clusters": [
    {
      "id": "cm-prod",
      "label": "Cloudera Manager production",
      "cluster_type": "cm",
      "cm_url": "https://cm-prod.example.com:7183/",
      "cluster": "prod_cluster",
      "service": "impala",
      "ca_bundle": "~/.qdcreds/cm-chain.pem",
      "metadata_coordinator": "impala-prod-coordinator.example.com:21050"
    },
    {
      "id": "direct-impala",
      "label": "Direct Impala",
      "cluster_type": "impala",
      "impala_profile_hosts": ["impalad-worker-1.example.com"],
      "metadata_coordinator": "impala-coordinator.example.com:21050"
    }
  ]
}
```

Cluster entries may define target, TLS, direct Impala, Prometheus, metadata,
privacy, redaction, and `recent_scan_timezone` fields. `language`, `no_llm`, web
`host`/`port`, Recent scan limits, and output paths are global. Cluster `id`
values must use only letters, digits, `.`, `_`, or `-`.

Direct `query_doctor.cli.batch_recent` runs can select one of these entries
with `--config-cluster <id>`. The selected cluster supplies source, metadata,
runtime-metrics, redaction, and `source_visibility` settings; explicit CLI
flags still override the selected cluster. When no explicit source flags are
provided, batch maintenance CLIs use `active_cluster_key` or the only
`clusters[]` entry as the default source; multi-cluster configs without either
must pass `--config-cluster`. For local web runs, omit
`source_owner_user` unless you intentionally need a fixed owner that differs
from the keytab-derived user.

## LLM Routes

Reports and Query LLM optimizer can use separate routes. For local Ollama, the
provider and models are enough; the default base URL is
`http://localhost:11434`.

```json
{
  "report_llm_provider": "ollama",
  "report_llm_model": "qwen3-coder:30b-a3b-q8_0",
  "optimizer_llm_provider": "ollama",
  "optimizer_llm_model": "deepseek-coder-v2:16b"
}
```

To switch either route to an external OpenAI-compatible API, change the provider
and add the non-secret base URL. Keep the token in `~/.qdcreds/llm-api.env`.

```json
{
  "report_llm_provider": "openai_compatible",
  "report_llm_model": "report-route-model",
  "report_llm_base_url": "https://llm-gateway.example.com",
  "optimizer_llm_provider": "openai_compatible",
  "optimizer_llm_model": "optimizer-route-model",
  "optimizer_llm_base_url": "https://llm-gateway.example.com"
}
```

## Safety And Privacy

| Field | Type | Scope | Notes |
| --- | --- | --- | --- |
| `privacy_mode` | boolean | global or cluster | Default on. Makes identifier, hostname, and metadata redaction the default behavior. |
| `no_llm` | boolean | global only | Disables LLM-backed report and optimizer generation. Reports and optimizer outcomes stay Python-owned. |
| `redact` | boolean | global | Redacts sensitive profile content for collection. Real profile collection requires redaction. |
| `redact_identifiers` | boolean | global or cluster | Redacts database, table, and SQL-like identifiers in collected/displayed safe artifacts. |
| `redact_hosts` | boolean | global or cluster | Replaces infrastructure hostnames/IPs with stable aliases. |
| `metadata_redact` | boolean | global or cluster | Redacts collected metadata context. Leave enabled unless inspecting private local artifacts only. |
| `source_visibility` | string | global or cluster | `safe` or `owner_raw`. Default `safe`. `owner_raw` enables fail-closed owner gating for Recent and Running scans; it does not expose raw browser/report fields by itself. |
| `source_owner_user` | string | global or cluster | Optional query owner user for `owner_raw`. Prefer omitting this for local web runs: Query Doctor derives a simple user from `QD_SOURCE_OWNER_USER`, `QD_KRB5_PRINCIPAL`, `KRB5_PRINCIPAL`, or simple principals in `QD_KEYTAB`. Service principals with `/` are not accepted for inference. Keytab-derived users are sorted alphabetically, and the first user becomes the default Username selection. |
| `viewer_identity_header` | string | global | Optional HTTP header name to trust as the authenticated viewer user for shared/D3 web deployments. This is the only Query Doctor D3 application contract: a trusted auth front door authenticates the request, strips inbound copies of that header, and sets exactly one already normalized simple owner value, such as an Active Directory `sAMAccountName` or Kerberos primary. OIDC/SSO, SAML, SPNEGO/Kerberos, LDAP, MFA, session, logout, and token lifecycle stay at the front door, not inside Query Doctor. Missing, duplicate, UPN/email-style, distinguished-name, group/role-like, opaque-subject, whitespace/display-name, comma-separated, service-principal, or host-principal values are unauthenticated and fail closed for raw source access. |
| `owner_raw_source_enabled` | boolean | global | Default `true`. Set `false`, or pass `--disable-owner-raw-source`, to disable only the isolated owner-only original source page and Details link. Recent/Running collection owner filters and validated optimizer draft policy keep their existing `source_visibility` behavior. |
| `language` | string | global | Global language mode shown in the web header. It controls Help, deterministic Recent Finding body copy, deterministic Details recommendation/explanation body copy, and newly generated trusted report language. Details headings, compact Recent labels, table headers, badges, and technical terms remain English. Supported values: `en`, `ru`. Default: `en`. Existing reports are not regenerated automatically after changing this field. The web header points to this config key without rendering the absolute config path. |
| `report_llm_provider` | string | global | Report LLM provider: `ollama` or `openai_compatible`. |
| `report_llm_model` | string | global | Report model route name. |
| `report_llm_base_url` | string URL | global | Non-secret report provider base URL. For external providers, keep tokens in `~/.qdcreds/llm-api.env`, not JSON. |
| `report_llm_chat_path` | string | global | Optional OpenAI-compatible report chat path override. Omit unless your gateway needs a non-standard path. |
| `optimizer_llm_provider` | string | global | Query LLM optimizer provider: `ollama` or `openai_compatible`. |
| `optimizer_llm_model` | string | global | Query LLM optimizer model route name. `optimizer_model` remains a legacy alias. |
| `optimizer_llm_base_url` | string URL | global | Non-secret optimizer provider base URL. For external providers, keep tokens in `~/.qdcreds/llm-api.env`, not JSON. |
| `optimizer_llm_chat_path` | string | global | Optional OpenAI-compatible optimizer chat path override. Omit unless your gateway needs a non-standard path. |
| `out` | string path | global | Local generated output directory for collector workflows. Do not commit generated output. |

Browser-visible UI and trusted reports must remain raw-free regardless of these
settings. Disabling privacy controls is for private local artifacts only.
`source_visibility=owner_raw` is not an "unhide everything" switch. It only
narrows Cloudera Manager or direct Impala Recent and Running scans to owner
users and permits the isolated owner-only selected-case source view after a
viewer identity check. On localhost, Query Doctor intentionally treats the
collectable owner users as the local viewer's raw subjects. On non-local binds,
`owner_raw` requires authenticated viewer identity from `viewer_identity_header`
behind a trusted proxy/ingress. The header controls only viewer authorization
(C2), not which users the collector may gather as the collection credential
(C1). Query Doctor does not implement native OIDC, SAML, SPNEGO, Kerberos,
LDAP, password, MFA, or session authentication for this path; those mechanisms
must terminate at the front door and produce the same normalized viewer header.
Every isolated owner-raw source page attempt emits a safe server-side audit line
with request id, route source, HTTP status, reason code, viewer mode/source, and
switch state. The audit line must not include SQL, query ids, case ids, query
users, local paths, header values, or secrets.
For shared/non-local deployments, use
[owner-raw-d3-deployment.md](owner-raw-d3-deployment.md) as the required
deployment checklist before enabling `owner_raw`; do not expose
`viewer_identity_header` without a trusted proxy or ingress that strips inbound
copies before setting it.
When the local web wrapper exposes `QD_KEYTAB`, Query Doctor reads simple
account names from that keytab, sorts them alphabetically, and uses the first
account as the default Username for `owner_raw`. The keytab path and full
Kerberos principals remain local process data and are not rendered in the
browser.

## Web Server

| Field | Type | Notes |
| --- | --- | --- |
| `host` | string | Web bind host. Use `127.0.0.1` for normal local operation. |
| `port` | positive integer | Web bind port. |
| `web_advanced_settings_enabled` | boolean | Shows the Diagnose Advanced settings disclosure when set to `true`. Default is hidden. |
| `web_advanced_filters` | string list | Optional editable filters inside Diagnose Advanced settings. Supported values: `user`, `pool`, `query_type`. When omitted and advanced settings are enabled, `user` and `pool` are shown. |

Non-local binds require the explicit web CLI risk flag. They are supported for
shared `owner_raw` only behind the D3 front-door contract described in
[owner-raw-d3-deployment.md](owner-raw-d3-deployment.md); they are not a
general multi-tenant service mode.

## Trino Local Recent And One Query ID

Trino local web mode is a bounded local production lane for retained-list Recent diagnosis and
one explicit Query ID. `trino_support_mode=beta` keeps the existing Trino Beta
label; `trino_support_mode=production` removes the beta label for the same
bounded raw-free local production surfaces. Recent reads one bounded pruned coordinator
query list after an accepted local query-list source contract, then reads
bounded pruned coordinator QueryInfo payloads through the QueryInfo source
contract for the selected retained rows. One Query ID uses the same bounded
QueryInfo path for one explicit ID. Both paths build raw-free boundaries in
memory and render deterministic compact diagnosis. They do not enable Trino
Running scans, query-history crawling, metadata collection, LLM reports,
Query Optimizer jobs, SQL execution, or generated Trino SQL. They may open the
raw-free Trino Details view, deterministic Python Report, and optimizer guidance
only after the web lane materializes server-owned case artifacts.

Minimal local config shape:

```json
{
  "engine": "trino",
  "trino_support_mode": "beta",
  "trino_coordinator_url": "https://trino-coordinator.example.com",
  "trino_query_info_source_contract": "./trino-query-info-contract.json",
  "trino_query_list_source_contract": "./trino-query-list-contract.json",
  "trino_kerberos_principal": "sa@EXAMPLE.COM",
  "trino_krb5_ccname": "FILE:/tmp/krb5cc_query_doctor_trino"
}
```

| Field | Type | Scope | Notes |
| --- | --- | --- | --- |
| `engine` | string | global | Optional default UI engine selection: `impala` or `trino`. Browser submits the selected engine explicitly; invalid values are rejected by config validation. |
| `trino_support_mode` | string | global or cluster | Explicit Trino local mode: `off`, `beta`, or `production`. `production` uses the same bounded raw-free Recent, One Query ID, materialized Details, Python Report, and optimizer guidance surfaces as local production support and does not enable Running, LLM reports, Query Optimizer jobs, generated SQL, SQL execution, or broader Trino production triage. Public demo mode rejects this setting. |
| `trino_beta_enabled` | boolean | global or cluster | Legacy beta-only switch for existing local configs. It maps to `trino_support_mode=beta` when no explicit mode is set and must not be combined with `trino_support_mode=production`. Public demo mode rejects this setting. |
| `trino_coordinator_url` | string URL | global or cluster | Trino coordinator base URL used by the bounded pruned QueryInfo and retained query-list readers. Keep it in local config; the browser form does not ask for or render it. |
| `trino_query_info_source_contract` | string path | global or cluster | Local path to an accepted `coordinator_query_info` source contract. Required for One Query ID and for fetching facts for selected Trino Recent rows. Relative values resolve from the JSON config file. |
| `trino_query_list_source_contract` | string path | global or cluster | Local path to an accepted retained coordinator query-list source contract. Required only for Trino Recent. Relative values resolve from the JSON config file. |
| `trino_auth_header_file` | string path | global or cluster | Optional local file containing one operator-managed `Authorization:` header line. Store the secret value only in this local file, not in JSON. Relative values resolve from the JSON config file. |
| `trino_kerberos_principal` | string | global or cluster | Optional local Kerberos/SPNEGO client principal for Trino GET collectors. Do not combine with `trino_auth_header_file`. |
| `trino_kerberos_service_name` | string | global or cluster | Optional Kerberos service name for SPNEGO. Defaults to `HTTP`. |
| `trino_krb5_ccname` | string | global or cluster | Optional local ticket-cache reference, for example `FILE:/tmp/krb5cc_query_doctor_trino`. Keep ticket contents out of JSON. |
| `trino_krb5_config` | string path | global or cluster | Optional local `krb5.conf` path for the Trino SPNEGO fetcher. Relative values resolve from the JSON config file. |
| `trino_kerberos_ca_cert` | string path | global or cluster | Optional CA certificate path for the Trino SPNEGO fetcher. Relative values resolve from the JSON config file. |
| `trino_kerberos_insecure_tls` | boolean | global or cluster | Optional local test-cluster override for the Trino SPNEGO fetcher. Prefer `trino_kerberos_ca_cert` when possible. |

Cluster entries may carry the same Trino keys when different Trino targets need
separate contracts. The web UI marks configured beta sources as
`Trino Beta Recent + One Query ID`, `Trino Beta Recent`, or `Trino Beta One
Query ID`; configured production-mode sources use the configured source label
without capability suffixes. The Diagnose Engine control narrows the Source
cluster selector to Impala-capable sources or Trino-ready sources before
workflow selection. Forged or stale Trino submits still fail closed before
analysis or async job creation.
The web UI must not render coordinator URLs, auth reference paths/values, raw
QueryInfo, raw query-list payloads, local source-contract paths, or raw SQL.

The standalone `query-doctor-trino-metadata-cli-summary` command does not add
web config fields. It takes `--source-contract`, `--trino-cli`, `--server`, and
`--connector-family` explicitly for one local operator run, validates the
metadata allowlist contract, and emits only a sanitized aggregate metadata
summary. It is not wired into Recent, Details, reports, optimizer actions, or
web metadata collection.

## Cloudera Manager

| Field | Type | Notes |
| --- | --- | --- |
| `cm_url` | string URL | Cloudera Manager base URL. Credentials stay in environment variables. |
| `cluster` | string | Cloudera Manager cluster name. |
| `service` | string | Impala service name. |
| `username` | string | Cloudera Manager username. `cm_user` is accepted as a legacy alias. |
| `ca_bundle` | string path | PEM CA bundle for verified TLS, often `~/.qdcreds/cm-chain.pem`. |
| `insecure_skip_verify` | boolean | Disables CM TLS verification. Use only for private local diagnostics. |
| `krb5ccname` | string | Kerberos cache path, for example `FILE:/tmp/krb5cc_query_doctor`. `metadata_krb5ccname` is a legacy alias. |
| `cm_metrics_profile` | string | Cloudera Manager metric compatibility profile, such as `cm6` or `cm7`. |

## Single Query Collection

| Field | Type | Notes |
| --- | --- | --- |
| `since_hours` | positive integer | Lookback window for collector workflows. |
| `limit` | positive integer | Maximum profile count for collector workflows. |
| `min_duration_sec` | non-negative integer | Minimum duration filter. |
| `max_profile_bytes` | positive integer | Profile text byte cap. |
| `pool` | string | Optional admission pool filter. |
| `user` | string | Optional query user filter. |
| `status` | string | One of `succeeded`, `failed`, `cancelled`, or `all`. |
| `query_type` | string | Query type filter, normally `QUERY`. |
| `collect_cm_timeseries` | boolean | Collect bounded allowlisted CM time-series summaries. |
| `cm_timeseries_padding_sec` | non-negative integer | Seconds to pad before and after query time windows. |
| `max_timeseries_bytes` | positive integer | Maximum bytes per CM time-series response. |
| `max_timeseries_points` | positive integer | Maximum numeric points to summarize per query. |

## Recent And Running Scans

| Field | Type | Notes |
| --- | --- | --- |
| `recent_limit` | positive integer | Maximum query summaries to inspect. |
| `recent_select` | positive integer | Maximum candidates selected for deeper analysis. |
| `recent_window_minutes` | positive integer | Lookback window for CLI Recent scans and web Finished-query scans. Large windows can increase load on Cloudera Manager, direct Impala UI endpoints, and optional Prometheus collection; use filters where possible. |
| `recent_min_duration_sec` | non-negative number | Lower duration bound. |
| `recent_max_duration_sec` | non-negative number | Upper duration bound. |
| `recent_order` | string | One of `recent`, `duration-desc`, `duration-asc`, `recent-duration-desc`, or `status-priority`. |
| `recent_output_json` | string path | Optional sanitized recent candidate JSON output path. |
| `recent_include_failed` | boolean | Include failed queries in candidate selection. |
| `recent_include_running` | boolean | Include running queries in candidate selection. |
| `recent_user` | string | Optional recent-query user filter. |
| `recent_pool` | string | Optional recent-query pool filter. |
| `recent_scan_timezone` | string | IANA timezone retained for explicit date/hour helper paths. Web Finished-query scans use `recent_window_minutes` by default. This field can also be set per cluster. |
| `recent_parallelism` | positive integer | Overall Recent scan worker limit. |
| `recent_cm_jobs` | positive integer | CM profile/context worker limit. Repeated CM profile HTTP 5xx responses stop new profile submissions and produce a safe summary warning. |
| `recent_cm_summary_limit` | positive integer | CM summary scan cap. |
| `recent_profile_analysis_limit` | positive integer | Profile analysis cap. |
| `recent_metadata_jobs` | positive integer | Metadata refresh worker limit. |
| `recent_metadata_top_limit` | non-negative integer | Maximum number of top collectable cases eligible for metadata refresh. |
| `recent_collect_cm_events` | boolean | Collect bounded CM event context when supported. |
| `recent_cm_events_max_events` | positive integer | Maximum CM events to summarize. |
| `recent_collect_cm_timeseries` | boolean | Collect bounded CM runtime metrics when supported. |
| `recent_cm_timeseries_top_limit` | non-negative integer | Number of top cases eligible for CM time-series refresh. |
| `collect_workload_history` / `recent_collect_workload_history` | boolean | Opt in to local workload fingerprint baseline history and regression labels. |
| `workload_history_path` / `recent_workload_history_path` | string path | Optional local JSONL path for workload history. Defaults to `~/.query-doctor/workload_history.jsonl`. |
| `workload_history_max_bytes` / `recent_workload_history_max_bytes` | positive integer | Rotate the local workload history file before appending when it exceeds this size. |
| `recent_history_backend` | string | Optional Recent summary history backend: `disabled`, `sqlite`, or `postgres`. Defaults to `sqlite` when `recent_history_db` is set, otherwise `disabled`. |
| `recent_history_db` | string path | Optional local SQLite store for raw-free Recent summary history. It stores bounded summary signals, selected/suspicion reason codes, profile collection status, and discover-only planned profile jobs, not raw SQL or profile text. See [Recent History Store](recent-history-store.md) for the SQLite-local and CNPG/Postgres boundary. |
| `recent_history_postgres_dsn_env` | string | Environment variable name containing the Postgres DSN for `recent_history_backend=postgres`. Defaults to `QUERY_DOCTOR_RECENT_HISTORY_POSTGRES_DSN`. Store the DSN value only in the environment or Secret, not in JSON config. |
| `recent_history_collector_summary_json` | string path | Optional configured web pointer to an already retained `query_doctor_recent_history_collector_v1` producer summary. Query Inbox projects only allowlisted raw-free status, age, recorded-row, and planned-job counters and never renders the configured path, raw JSON, raw SQL, Query IDs, source keys, local paths, raw errors, or retained free-form text. |
| `recent_history_operator_readiness_summary_json` | string path | Optional configured web pointer to an already retained `query_doctor_recent_history_operator_readiness_v1` summary. Query Inbox projects only allowlisted raw-free readiness/evidence/reason-code/schema/worker/retention counters and never renders the configured path, raw JSON, raw SQL, profile text, local store paths, or raw artifact names. |
| `recent_history_summary_retention_days` | positive integer | Optional explicit retention cutoff for raw-free Recent summary rows. The batch CLI prunes rows older than this many days after recording history and reports aggregate delete counts only. |
| `recent_history_profile_job_retention_days` | positive integer | Optional explicit retention cutoff for terminal raw-free profile job rows. Pending and leased jobs are not pruned. |
| `recent_history_analysis_cache_retention_days` | positive integer | Optional explicit retention cutoff for raw-free analysis-cache rows. |
| `recent_history_profile_artifact_retention_days` | positive integer | Optional explicit retention cutoff for raw-free profile-artifact metadata rows. Query Doctor does not store raw profile bytes in the history database. |

In the web Diagnose flow, `Minimum duration (sec)` is intentionally blank by
default even when `recent_min_duration_sec` exists in local config. An empty web
field sends no lower-duration filter so long-query and repeated-short workload
patterns remain available; enter a value in the field when you want the web scan
to narrow to longer-running queries.

Recent and Running web workflows do not auto-run LLM reports or optimizer jobs.
The default Diagnose form keeps source, scan target, time window, duration, and
required owner filters in the browser. Optional `recent_user`, `recent_pool`,
and `query_type` filters can remain config defaults, or can be made editable by
setting `web_advanced_settings_enabled=true` and `web_advanced_filters`.
Worker-count settings such as `recent_parallelism` and `recent_metadata_jobs`
are intended as local config defaults or explicit request overrides, not normal
per-scan browser choices.
For non-local configured web binds, Query Doctor uses conservative defaults
when these fields are absent: `recent_profile_analysis_limit=50`,
`recent_parallelism=4`, `recent_metadata_jobs=2`, and
`recent_metadata_top_limit=10`. Local/localhost web and CLI defaults remain
unchanged; raise the non-local defaults only with matching pod resources and a
focused smoke.

## Manual Profile Inbox

| Field | Type | Scope | Notes |
| --- | --- | --- | --- |
| `corpus_dir` | string path | global | Optional output directory for web-collected or web-staged Query ID cases. `query-doctor-web --corpus-dir` overrides this config value. Relative config values resolve from the config file; relative CLI values resolve from the current directory. Defaults to `cases/cm-corpus` under the directory where `query-doctor-web` starts. When it already contains complete `query-doctor-analyze --profile-text` cases, the web UI can start without CM settings or default local config discovery and render those staged exported profiles automatically. |
| `manual_profile_dir` | string path | global or cluster | Optional local directory of exported Apache Impala text profiles for Known Query ID analysis. Name each file with the Query ID slug, for example `<query-id-slug>.txt` after replacing the Query ID separator with `_`. The web UI stages matching files through the existing manual-profile analyzer path. If the file contains an embedded Query ID for a different query, staging fails closed before replacing any existing case. |

`manual_profile_dir` is a local-first fallback for one exported profile. It
does not enable Recent scans, Running scans, Cloudera Manager metrics/events,
direct impalad collection, metadata collection, report generation, or optimizer
actions by itself. When it is the only configured source, Known Query ID fails
closed if the matching profile file is absent instead of falling back to live
collection.

For local/private sessions without a prepared inbox, the Diagnose page also
offers an explicit one-profile upload under `One Query ID`. That upload accepts
one exported Impala text profile, stages it through the same analyzer path under
`corpus_dir`, and does not render the uploaded profile, filename, local path, or
temporary upload artifact back into trusted browser surfaces. Public demo mode
hides the upload form and blocks uploads before reading the request body.

Minimal manual-only web config:

```json
{
  "manual_profile_dir": "./profile-inbox",
  "corpus_dir": "./query-doctor-cases",
  "no_llm": true
}
```

## Direct Impala Profiles

| Field | Type | Scope | Notes |
| --- | --- | --- | --- |
| `cluster_type` / `query_profile_source` | string | global or cluster | `cm` or `impala`. Prefer `cluster_type` in user configs; `query_profile_source` remains the internal/legacy name. Use `impala` for direct daemon endpoints. |
| `impala_profile_hosts` | string list | global or cluster | One or more impalad debug web hosts or host:port values. |
| `impala_profile_port` | positive integer | global or cluster | Default daemon web port for hosts without a port. |
| `impala_profile_scheme` | string | global or cluster | `http` or `https`. |
| `impala_profile_timeout_sec` | positive integer | global or cluster | Per-request timeout. |
| `impala_query_list_max_bytes` | positive integer | global or cluster | Byte ceiling for one `/queries?json` response, default 5242880, hard cap 268435456. A coordinator with a large `--query_log_size` can answer with far more than the default allows, and an over-sized response is refused rather than truncated, so Recent and Running scans on that cluster return nothing until this is raised to match. Raising it also raises the memory one scan holds. |
| `impala_profile_prefer_json` | boolean | global or cluster | Optional compatibility probe. When true, direct Impala collection tries a JSON profile endpoint first and falls back to text endpoints for older Impala versions. Default is false. |
| `impala_profile_collect_docs` | boolean | global or cluster | Optional `/profile_docs/?json` counter-stability probe with `/profile_docs` HTML fallback. When true, direct Impala collection writes a safe allowlisted counter registry context when the endpoint is available as JSON-style docs or a Web UI HTML table. Missing endpoints are non-fatal. Default is false. |
| `impala_collect_admission_context` | boolean | global or cluster | Optional `/admission?json` aggregate pool-context probe. When true, direct Impala collection writes only bounded safe summaries such as queue presence, queue-time bucket, pool pressure, and freshness. Missing endpoints are non-fatal. Default is false. |
| `impala_kerberos_service_name` | string | global or cluster | Kerberos service token, such as `impala` or `hive`. |

Impala completed profile log files are separate from the online coordinator
query log used by `/queries` and `/profile`. Query Doctor does not currently
read `profile_log_dir`, pod filesystems, node filesystems, Loki, OpenSearch, or
other external history indexes for direct Recent scans. A future profile-log or
external-history ingestion path must be operator-configured, read-only,
allowlisted, byte-bounded, time-window-bounded, and raw-free at the
browser/report boundary. It must not let the web UI run `ssh`, `kubectl exec`,
`kubectl cp`, arbitrary filesystem reads, arbitrary log-index queries, or
credential entry against Impala nodes, pods, or external stores.

## Prometheus Runtime Metrics

| Field | Type | Scope | Notes |
| --- | --- | --- | --- |
| `collect_prometheus_timeseries` | boolean | global or cluster | Enables bounded Prometheus runtime metric summaries for direct Impala workflows. |
| `prometheus_url` | string URL | global or cluster | Prometheus base URL. Do not include credentials, query parameters, or fragments. |
| `prometheus_metrics_profile` | string | global or cluster | Metrics compatibility profile, such as `ambari-hadoop`. |
| `prometheus_step_sec` | positive integer | global or cluster | Query step in seconds. |
| `prometheus_timeseries_padding_sec` | non-negative integer | global or cluster | Seconds to pad around query windows. |

Prometheus collection uses allowlisted PromQL and writes summarized facts only.
It does not write raw time-series responses or labels. Treat Prometheus
host-level metrics as shared runtime context when multiple Impala deployments
can run on the same hosts; Query Doctor only strengthens runtime hypotheses
when selected-query profile evidence correlates the signal.

## Metadata Collection

| Field | Type | Scope | Notes |
| --- | --- | --- | --- |
| `metadata_coordinator` | string | global or cluster | Impala coordinator `HOST:PORT` on its HiveServer2 port. |
| `metadata_auth` | string | global or cluster | Only `kerberos` is supported. |
| `metadata_protocol` | string | global or cluster | `hs2` or `hs2-http`. |
| `metadata_ssl` | boolean | global or cluster | Enables TLS for the metadata connection. |
| `metadata_ca_cert` | string path | global or cluster | CA certificate for metadata TLS. |
| `metadata_kerberos_service_name` | string | global or cluster | Kerberos service token for metadata, such as `impala` or `hive`. |
| `metadata_kerberos_host_fqdn` | string | global or cluster | Optional Kerberos host override for load-balanced metadata coordinators. Becomes the host half of the service principal. |
| `metadata_timeout_sec` | positive integer | global or cluster | Optional per-command timeout override. Omit to use the built-in default. |
| `metadata_max_tables` | positive integer | global or cluster | Optional maximum tables override for each metadata run. Omit to use the workflow default. |
| `metadata_max_output_bytes` | positive integer | global or cluster | Optional metadata output byte limit override. Omit to use the built-in default. |
| `metadata_redact` | boolean | global or cluster | Redacts metadata output before artifacts are written. |
| `metadata_source` | string | global or cluster | `impala` (default) runs the SHOW statements on the coordinator. `hms-postgres` reads the same facts from the Hive Metastore's PostgreSQL database. |
| `metadata_hms_postgres_dsn_env` | string | global or cluster | Name of the environment variable that holds the metastore database DSN for `hms-postgres`. Default: `QUERY_DOCTOR_HMS_POSTGRES_DSN`. The DSN itself never goes into config. |

A SHOW statement on a table that catalogd has not loaded yet makes it load the
table, and loading an HDFS table lists all of its partition directories. With
`metadata_source: hms-postgres`, metadata never reaches Impala: the collector
opens one read-only PostgreSQL session per run
(`default_transaction_read_only=on`, statement timeout from
`metadata_timeout_sec`) and runs fixed SELECT statements over the metastore
tables `TBLS`, `DBS`, `SDS`, `TABLE_PARAMS`, `PARTITION_KEYS`,
`PARTITIONS`, `PARTITION_PARAMS`, `COLUMNS_V2`, and `TAB_COL_STATS`. It needs the
`postgres` extra. Row counts and column statistics are the values the last
`COMPUTE STATS` or write recorded, not a live listing; when the metastore has
`impala.lastComputeStatsTime`, the facts show it as `table stats last computed`.
Table size is the sum of partition `totalSize` values and is shown only when
every partition has one.

Metadata collection is read-only, allowlisted, bounded, explicit, and redacted.
Default metadata limits are intentionally omitted from the example config; add
these fields only when a local environment needs different bounds.

## Validation Rules

The config loader rejects:

- unknown fields;
- duplicate canonical aliases, such as both `username` and `cm_user`;
- secret-looking keys such as `password`, `token`, `secret`, or
  `authorization`;
- Prometheus URLs that include credentials, query parameters, or fragments;
- invalid enum values and invalid integer/boolean types.

This fail-closed behavior is intentional. A typo in local config should stop
startup instead of silently changing collection scope.
