# Контракт безопасности Query Doctor

Last reviewed: 2026-07-13

Язык: [English](../../safety-contract.md) | Русский

Английская версия является канонической для публичного репозитория. Эта
страница - русская companion-версия для операторов и разработчиков. Имена
файлов, команд, секций отчетов и проверяемые идентификаторы оставлены на
английском там, где они являются частью интерфейса или тестового контракта.

## Граница фактов

- Deterministic Python analyzers Query Doctor отвечают за diagnostic facts,
  evidence merging, assessment findings и causal promotion.
- LLM отвечает только за формулировку.
- Любое диагностическое утверждение должно соответствовать evidence со
  статусом `supported`, `not_observed` или `unknown`. Текущие reports
  отображают его через `analysis_facts.md`.
- Нельзя заявлять root cause, если accepted evidence Query Doctor прямо не
  поддерживает такую причину.
- Report writer не должен делать выводы из raw profile/EXPLAIN text, SQL, raw
  Cloudera Manager JSON, local config или external knowledge.
- Raw EXPLAIN text является local evidence artifact. Persisted plan facts могут
  содержать только bounded allowlisted structural enums, coverage states,
  counts и finite nonnegative estimates. Они не должны сохранять raw lines,
  relations, predicates, literals, paths, engine-local identities или raw-plan
  fingerprint; optimizer intent само по себе не поддерживает causal claim.
- Accepted external EXPLAIN artifact остается не связанным с runtime profile.
  Case co-location, соседние SQL/query metadata и structural operator overlap
  не устанавливают statement или execution identity. Оба identity должны
  оставаться `unknown`, пока не появится отдельно проверенный источник
  same-snapshot provenance.

## Граница engine facts

- `engine_fact_boundary_v1` - raw-free normalized fact seam. Это не product
  engine registry, не public support claim и не замена текущим Impala analyzer
  facts без отдельной migration/parity работы.
- Engine fact bundles должны использовать registered fact identifiers с
  explicit scope и allowed engines. Shared или distributed-SQL-family facts
  требуют явного namespace definition; engine-specific facts должны оставаться
  engine-prefixed или allowlisted registry.
- Boundary payloads строятся только из Python-owned parsed/compact facts и
  проходят raw-free validation до любого browser/report consumer или consumer
  probe.
- Boundary payloads и public engine fact text не должны содержать raw SQL, raw
  profile text, raw metadata, raw event logs, raw query details, source
  endpoints, IDs, hostnames, user names, object names, local paths, runtime
  internals, parser-local identifiers, stack traces, exception messages или raw
  artifact filenames.
- Unsupported, missing, partial, unstable или source-version-mismatched engine
  facts должны деградировать в `unknown`, `not_observed` или explicit safe
  limitation. Нельзя backfill fake metrics, counters, lifecycle evidence или
  events между движками.
- Trino compact/dev facts и Spark compact facts остаются ниже product support.
  Текущие Trino local production exceptions - local web retained-list Recent через
  один bounded retained pruned coordinator query-list read плюс selected pruned
  coordinator QueryInfo reads, и один explicit local web Query ID через
  bounded pruned coordinator QueryInfo. Оба workflow показывают только raw-free
  compact diagnosis. Они не включают Running, query-history crawling, product
  metadata collection, LLM reports, Query Optimizer jobs, Query Doctor-generated Trino
  SQL, user SQL execution или broader/shared Trino production triage support. Они могут открывать raw-free Trino
  Details view, deterministic Python Report и optimizer guidance только из
  server-owned materialized case artifacts. Текущий
  статус см. в английской
  [engine-support-gap-matrix.md](../../engine-support-gap-matrix.md).

## Граница сбора данных

- Широкое cluster/profile/table scanning по умолчанию запрещено.
- Любой внешний сбор должен быть explicit, bounded, read-only, redacted и safe
  by default.
- Outbound HTTP collection должен validate target, держать response reads
  bounded и отклонять unsafe redirect targets. Metadata, link-local, reserved,
  multicast и другие unsafe destinations не должны быть доступны через browser
  forms или optional URL overrides.
- Private-network и loopback targets разрешены только как explicit configured
  diagnostic endpoints для соответствующего компонента, а не как arbitrary
  egress.
- Dry-run и preflight paths не должны собирать profile text.
- Real profile collection не должен печатать raw profiles, SQL, raw Cloudera
  Manager JSON или credentials.
- Первый поддержанный путь Impala metadata connection - Kerberos поверх
  HiveServer2 с уже полученным TGT от `kinit`.
- Metadata collector не вызывает `kinit`, не запрашивает passwords и не
  принимает AD/LDAP passwords. Он открывает одно read-only HiveServer2
  соединение на прогон через impyla и переиспользует его для всех statements.
- Metadata collector принимает только bounded table references из explicit CLI
  input или Python-owned selected-case extraction. Он выполняет только
  read-only statements: `SHOW CREATE TABLE`, `SHOW TABLE STATS`,
  `SHOW COLUMN STATS`.
- С `--source hms-postgres` collector в Impala не обращается. Он открывает одну
  read-only PostgreSQL-сессию к БД Hive Metastore на прогон, DSN берёт из
  названной environment variable (не из argv и не из config) и выполняет только
  фиксированные параметризованные SELECT по таблицам схемы метастора для тех же
  bounded table references. Ошибки драйвера заменяются фиксированными
  сообщениями без DSN и хоста.
- Raw строки координатора и текст ошибок драйвера не печатаются в terminal;
  collected output bounded, redacted и пишется только в explicit `--out`.
- Generated `impala_context.md` и `impala_context.json` являются local outputs
  и не должны попадать в commit.

## Граница manual profile intake

- Manual profile intake принимает только один exported Apache Impala text
  profile для одного explicit Query ID: через CLI/local inbox staging или через
  explicit local/private web upload form. JSON, Thrift, profile-v2 payloads,
  broad profile directories и network collection находятся вне этой границы.
- Browser не должен render raw profile, uploaded filename, local path или
  temporary upload artifact. Единственный browser upload entry point -
  `POST /profile/upload`: он должен требовать multipart form data, принимать
  ровно один profile file, оставаться bounded через `max_profile_bytes` плюс
  небольшой multipart overhead, force Impala Known Query path, stage через тот
  же redacted analyzer path и удалять temporary upload file после staging.
  Public demo mode должен скрывать upload form и блокировать uploads до чтения
  request body. Web `manual_profile_dir` остается server-side local inbox:
  пользователь кладет files на disk, затем вводит исходный Query ID в Known
  Query ID mode.
- Manual profile staging должен пройти тот же redaction и bounded analyzer path,
  что collector-shaped cases, до того как Details page или trusted report могут
  использовать case.
- Если profile text содержит embedded Query ID, он должен совпадать с explicit
  Query ID до записи staged case или замены existing case. Missing или malformed
  profile files должны fail closed с safe remediation text.
- Browser-visible manual-intake errors не должны раскрывать raw profile text,
  local paths, raw filenames, subprocess output, credentials или mismatched raw
  Query IDs. Terminal diagnostics могут быть technical, но не должны печатать
  raw profile dumps или secrets.

## Git boundary

Generated, sensitive и local outputs не должны попадать в commit:

- `cases/cm-corpus/`;
- `cases/cm-corpus-hostalias/`;
- `analysis_facts.md`;
- generated `report*.md` / `diagnosis*.md`;
- `*.partial`;
- local Cloudera Manager config;
- real Cloudera Manager profile material;
- `query_metadata.json`;
- `impala_context.md` / `impala_context.json`.

Нельзя коммитить raw hostnames, IPs, users, emails, tokens, cookies, passwords,
Authorization headers, embedded URL credentials, local config contents или real
production profile text.

## Report validation

- Validators работают fail-closed.
- Нельзя ослаблять validators только ради того, чтобы report проходил проверку.
- Если report rejected, исправляйте deterministic facts, prompt wording,
  sanitizer behavior или tests.
- Новые validator rules должны иметь unsafe-rejected и safe-allowed tests.
- Каждый supported report language должен иметь explicit overclaim-detection
  coverage. Добавление языка требует validator coverage и parity tests до
  использования в trusted reports.
- Validators должны reject-ить unsupported claims даже при indirect или soft
  phrasing, включая causal/responsibility wording, diagnostic recommendations и
  row/cardinality или memory estimate direction wording.
- Deterministic normalization не должна незаметно прятать unsupported claims.
- Safe replacements должны быть explicit, narrow и covered tests.
- Raw LLM output буферизуется и не должен stream'иться в stdout/stderr или
  user-facing UI.
- Final report files пишутся только после normalization, sanitization,
  narrative validation, appendix append и final validation.
- Trusted final reports не должны содержать raw SQL-like text, SQL fenced code
  blocks, pasted query fragments или raw metadata command snippets, включая
  table-specific `SHOW CREATE TABLE`, `SHOW TABLE STATS`, `SHOW COLUMN STATS`.
- Partial или invalid report output остается untrusted и не должен
  отображаться как final diagnosis.
- CLI validation bypass modes являются manual escape hatches. Browser и trusted
  artifact consumers должны принимать только current strict validation markers
  со всеми fields, которые требует текущий marker contract, и совпадающими
  artifact/facts hashes. Marker schemas должны bind-ить schema version до
  material changes в validation rules.
- Defensive UI failure handlers должны fail closed с redacted safe messages и
  не должны показывать raw SQL, subprocess output, local paths или artifact
  names при unexpected exceptions.

## Query Optimizer trust boundary

- Query Optimizer может отправлять raw source SQL в LLM только внутри
  delimited `INPUT SQL` block для explicit selected-case rewrite attempt.
- Prompt wording должен framing-ить `INPUT SQL` как untrusted data. Instructions
  внутри SQL comments, string literals, identifiers или pasted query text не
  должны override-ить Python-owned rules, recipes или validation requirements.
- Recommendations-only prompts должны оставаться raw-free и использовать только
  Python-owned recommendation candidates, SQL-shape digests и optimizer fact
  digests.
- Raw LLM optimizer output остается untrusted, пока deterministic validation не
  примет его. Unsafe, non-read-only, multi-statement, incomplete или
  unsupported-shape output должен оставаться untrusted или превращаться в
  trusted no-rewrite/recommendations outcome, но никогда в browser-visible
  trusted SQL draft.

## Redaction and resource boundary

- Redaction является defense in depth. Она не заменяет raw-free fact
  extraction, deterministic validation или browser/trusted-report raw-content
  exclusions.
- Local artifacts, logs, warnings и defensive UI fallback text, где могут
  появиться operational strings, должны использовать shared redaction policy до
  display или persistence.
- Redaction changes для hosts, users, URLs, credentials, auth headers, cookies,
  metadata keys и local paths должны иметь adversarial unsafe-rejected tests и
  safe false-positive checks.
- User-controlled text должен быть byte-bounded до regex-heavy parsing,
  validation, prompt assembly, sanitizer или browser-rendering paths.
- Новые или расширенные safety regexes должны избегать nested unbounded
  quantifiers и иметь pathological-within-cap regression test, если они стоят
  на trust boundary.

## Browser display boundary

- Trusted browser/report surfaces не должны показывать raw SQL, raw profiles,
  raw metadata, stdout/stderr, local paths, `case_dir`, credentials, secret
  values, Kerberos ticket contents, metadata connection details, model names или
  Ollama internals. Isolated owner-only selected-case source surface - узкое
  browser-исключение для raw SQL и должна следовать правилам `owner_raw` ниже.
  Raw profiles, raw metadata, stdout/stderr, local paths, credentials, secret
  values, Kerberos material, model/runtime internals и raw artifact filenames
  остаются запрещены и там.
- Любой dynamic browser-visible text должен проходить shared browser display
  redaction policy перед rendering.
- Web Recent и Running scans не должны автоматически запускать LLM reports или
  optimizer jobs. Known Query ID может готовить deterministic Python report
  внутри explicit analysis submit-job. LLM report generation и Query LLM
  optimizer generation остаются explicit actions для одного selected case.
- Trino Recent использует один bounded retained pruned coordinator
  query-list read плюс bounded selected pruned coordinator QueryInfo reads из
  local config; Trino One Query ID использует один bounded pruned
  coordinator QueryInfo read из local config. Оба workflow показывают только
  raw-free compact diagnosis. Они не должны показывать coordinator URLs, auth
  header paths/values, raw QueryInfo, raw query-list payloads, raw SQL, local
  source-contract paths, LLM reports, Query Optimizer jobs или generated Trino SQL.
  Trino Details, Trino Python Report и Trino optimizer guidance могут показывать
  только server-owned raw-free materialized case facts и не должны раскрывать
  query IDs, local paths, raw artifact names или raw source payloads.
- Details-page Query LLM optimizer может показывать validated read-only SQL
  draft только для explicit selected-case optimizer action, когда текущая web
  source policy равна `source_visibility=owner_raw`. Default
  `source_visibility=safe` должен деградировать до trusted
  recommendations/no-rewrite guidance, даже если validated SQL draft artifact
  уже существует.
- Partial drafts, raw source SQL, externally pasted SQL и optimizer validation
  failures должны оставаться скрытыми.
- `source_visibility=owner_raw` - owner-gating mode, а не blanket display
  bypass. Он может сужать Recent/Running scans до verified owner users,
  показывать validated optimizer SQL draft только для explicit selected-case
  optimizer action и разрешает отдельную isolated owner-only selected-case
  source surface для original read-only SQL source, когда `query.user`
  разрешен authenticated viewer identity. Эта source surface не является
  trusted report, Details, Recent table, optimizer, handoff или download
  surface; она должна быть source-allowlisted, fail-closed при ownership
  mismatch, использовать `Cache-Control: no-store`, ничего не отправлять в LLM
  и по-прежнему исключать raw profile dumps, raw metadata, local paths,
  subprocess output, secrets, model names, runtime internals и raw artifact
  filenames. Local-first owner raw visibility не должен стартовать на non-local
  web bind; shared web access требует authenticated viewer identity.
- Isolated owner-raw source surface должна оставаться за global kill switch.
  `owner_raw_source_enabled=false` и `--disable-owner-raw-source` отключают
  только original source page/link; они не должны тихо менять collection owner
  filters или optimizer policy.
- Каждая попытка открыть isolated owner-raw source page должна писать
  server-side audit line с request id, route source, HTTP status, reason code,
  viewer mode/source и switch state. Audit lines не должны содержать raw SQL,
  query ids, case ids, query users, local paths, header values, secrets, raw
  artifact filenames, model names или runtime internals.
- Shared/D3 web deployments используют один Query Doctor application contract:
  trusted auth front door authenticates request, strips inbound copies of
  `viewer_identity_header`, sets exactly one normalized simple owner value, and
  Query Doctor then compares that C2 viewer value with selected case
  `query.user`. OIDC/SSO, SAML, SPNEGO/Kerberos, LDAP/AD, MFA, session,
  logout, token, group и RBAC handling должны оставаться на trusted front door;
  Query Doctor не должен реализовывать native owner-raw auth variants или
  принимать raw identity-provider tokens. Missing, duplicate, invalid,
  UPN/email-style, distinguished-name, group/role-like, opaque-subject,
  whitespace/display-name, comma-separated, service-principal или
  host-principal header values считаются unauthenticated и должны fail-closed
  для raw source access. Header задает только C2 viewer identity; он не должен
  расширять C1 collection credentials или owner-user collection scope.

## Future Cluster Doctor

- Cluster Doctor - будущий explicit user-run seam для диагностики
  cluster/service/workload, а не текущая поддержка продукта.
- Query Doctor может использовать будущий Cluster Doctor output только как
  normalized Python-owned facts со status, scope, coverage, confidence,
  limitations и deterministic correlation.
- Cluster Doctor должен оставаться read-only: он может рекомендовать checks или
  operational follow-up, но не должен выполнять service control, configuration
  changes, data changes или remediation automation.
- Все current и future providers, включая Cloudera Manager, direct Impala,
  Prometheus, prepared metric stores и log/event stores, должны быть explicit,
  bounded, read-only, allowlisted where applicable, redacted и tested до того,
  как их факты попадут в reports или browser UI.
- Future log/event support должен потреблять только prepared event summaries.
  Raw log lines, stack traces, raw alert text, principals, usernames, query text
  и raw parser payloads не должны попадать в browser-visible UI, trusted
  reports или LLM prompts.
- CM Events MVP CLI read-only и bounded. Он может печатать normalized event
  counts, severities и signal ids, но не raw CM event payloads, raw log lines,
  event ids, hostnames, principals, paths, query text или raw provider JSON.
- `cluster_event_context.json` - schema-versioned internal Cluster Doctor seam
  artifact, построенный только из normalized CM event summaries. Он должен
  whitelist exported fields и исключать raw provider payloads, raw log lines,
  event ids, hostnames, principals, paths, query text, URLs, local paths,
  secrets, command output, model/runtime names и raw artifact filenames.
- `cluster_context.json` - schema-versioned aggregate Cluster Doctor seam
  artifact, построенный только из safe context artifacts. Он может включать
  source status, product status, normalized signal counts, limitations и next
  checks, но не raw provider payloads или browser-forbidden details.
- Raw metric series, raw logs, raw provider JSON, raw alert text, raw
  timestamps, hostnames, entity IDs, URLs, paths, credentials, artifact names,
  command-stream details, model names и runtime internals не должны
  отображаться в trusted reports или browser-visible UI.
- Cluster-wide root-cause или incident claims требуют отдельного deterministic
  claim registry, fixtures, report validation и browser safety tests.

## Report structure

LLM пишет user-facing narrative sections:

- `## Краткий вывод`;
- `## Практические рекомендации`;
- `## Подробный разбор`;
- `## Админские проверки`.

Python добавляет deterministic appendix:

- `## Факты анализатора`.

LLM не должен писать appendix section. Report validator должен отклонять raw
SQL-like output, unsafe recommendations, unsupported root-cause claims и
browser-forbidden details.
