# Границы поддержки

Last reviewed: 2026-08-11

Русская версия контракта поддержки. Английский оригинал и полный текст,
включая supported deployment shape, — в
[../../support-boundary.md](../../support-boundary.md). README держит короткую
сводку из трёх строк, здесь — поверхности целиком.

## Текущие поверхности


| Surface | Current status |
| --- | --- |
| Query engine | Apache Impala - full production triage engine. Trino имеет bounded local production support только для raw-free lanes ниже. |
| First-value intake | Один локальный exported Impala text profile можно загрузить из local/private web session или staged через CLI/manual inbox, затем redacted/analyzed и открыть через Known Query ID. |
| Recent scan | Cloudera Manager - полный Recent discovery/profile/metrics/events provider для Impala workflows. |
| Direct Impala | Bounded Recent scans, Running scans и один Known Query ID через impalad daemon endpoints; без Cloudera Manager events и без SQL execution. |
| Runtime metrics | Optional bounded Prometheus summaries для configured direct Impala workflows; без arbitrary PromQL from users. |
| Metadata | Read-only allowlisted Impala metadata statements поверх HiveServer2 или эквивалентное read-only чтение из PostgreSQL-базы Hive Metastore; без user SQL execution и unbounded metadata crawl. |
| Reports and optimizer | Python-owned facts и validation. Known Query ID готовит deterministic Python report в explicit submit-job; LLM narratives и optimizer actions остаются explicit selected-case actions. |
| Container/Kubernetes web deployment | Supported starting point через official container image, `/healthz` и `/readyz` probes, raw-free deployment readiness summary, read-only `public-demo` manifest, configured private web manifest, synthetic self-test Job и `deploy/helm/query-doctor` chart с `helm test` hook. Kubernetes support не добавляет native auth, RBAC, sessions, multi-tenant isolation, operator/CRD, arbitrary command running, SQL execution или broader engine support. Shared configured deployments все равно требуют trusted ingress/auth proxy и те же safety gates, что любой shared/non-local web bind. |
| Trino local | Local web Trino mode может прочитать один bounded retained pruned coordinator query list для Recent diagnosis, затем bounded pruned coordinator QueryInfo payloads для выбранных rows или одного explicit Query ID, показать deterministic compact diagnosis, materialize server-owned raw-free case artifacts, открыть raw-free Details view и создать deterministic Python Report плюс optimizer guidance для этих materialized cases. `trino_support_mode=beta` сохраняет legacy beta label; `trino_support_mode=production` помечает те же bounded raw-free local lanes как local production support и убирает этот label. Без Running scans, query-history crawling, metadata collection, LLM report output, Query Optimizer jobs, generated Trino SQL, SQL execution и broader/shared Trino production triage support. |
| Spark | Только bounded compact support surfaces. Spark не является production engine support, live Recent scans, Details/trusted report output, optimizer behavior, raw event-log handling, Spark job execution или Query Doctor-generated SQL. |

Публичный GHCR release содержит Query Doctor web image.

Trino compact/dev surfaces включают offline/local raw-free imports and checks:
bounded local pruned QueryInfo import принимает one explicit compact sanitized
local pruned QueryInfo JSON через `query-doctor-trino-query-info-pruned-import`
после source-contract checks. `query-doctor-trino-coordinator-query-info-pruned-probe`
и `query-doctor-trino-coordinator-query-info-pruned-import` могут использовать
`--auth-header-file`, но safe output не печатает auth header paths или values.
Local production Trino product surfaces - local web retained-list Recent diagnosis, One
Query ID diagnosis, raw-free Details view, deterministic Python Report и optimizer guidance для
server-owned materialized cases из этих lanes. Diagnosis lanes требуют
`trino_support_mode=beta` или
`trino_support_mode=production`, `trino_coordinator_url` и
`trino_query_info_source_contract` в local config; Recent дополнительно требует
`trino_query_list_source_contract`. Legacy `trino_beta_enabled=true` остается
beta-only switch для existing local setups и не должен комбинироваться с
`trino_support_mode=production`. Startup validation проверяет local source
contracts, safe coordinator URL shape и optional auth reference
(`trino_auth_header_file` или local Kerberos/SPNEGO settings) до того, как lane
считается configured. Configured beta sources помечаются в source selector как
`Trino Beta Recent + One Query ID` или `Trino Beta One Query ID`; configured
production-mode sources используют labels без `Beta`. Diagnose Engine control
сужает Source cluster selector до Impala-capable sources или Trino-ready sources
до выбора workflow, а stale или forged Trino submits все равно fail closed до
analysis или async job creation. Этот lane не делает network read вне explicit
bounded probe/import, reject-ит raw QueryInfo fields вроде query text, session
fields, endpoint URLs, object names и stage/task detail. Details открывается
только после materialized artifacts. Python Report и optimizer guidance используют те же raw-free
facts и не показывают raw payloads, query IDs, paths, LLM report output,
Query Optimizer jobs или generated SQL; Running scans, Query Optimizer jobs и
metadata collection остаются unavailable.
Broader/shared Trino live collection и broader Trino production triage остаются unsupported.

Spark compact support surfaces остаются только compact History Server intake,
compact evidence-package build/validation и compact diagnosis; no public Spark
engine support, без Recent scans, Details/trusted report output, optimizer
behavior, raw event logs или Spark job execution.

Будущие Big Data SQL/lakehouse live collectors, более широкие providers,
подготовленные event/log sources и Cluster Doctor workflows остаются roadmap
seams, а не текущей поддержкой. Detailed Trino/Spark preview command catalog:
[engines/README.md](../../engines/README.md). Текущий support/research
boundary: [engine-support-gap-matrix.md](../../engine-support-gap-matrix.md).
