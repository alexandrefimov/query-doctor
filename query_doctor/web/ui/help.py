"""Curated static Help page for the local Query Doctor web UI."""

from __future__ import annotations

from typing import Any

from query_doctor.web.ui.i18n import normalize_ui_language
from query_doctor.web.ui.pages import render_page


def render_help_page(settings: Any) -> str:
    llm_enabled = not getattr(settings, "no_llm", False)
    language = normalize_ui_language(getattr(settings, "language", "en"))
    return render_page(
        settings,
        active_nav="help",
        show_run_panel=False,
        extra_sections=[render_help_content(llm_enabled=llm_enabled, language=language)],
    )


def render_demo_guide_page(settings: Any) -> str:
    """Legacy /demo alias.

    Demo talk-track content lives in repository docs. The product UI keeps one
    maintained static guide so navigation does not drift from Details behavior.
    """

    return render_help_page(settings)


def render_help_content(*, llm_enabled: bool = True, language: str = "en") -> str:
    if normalize_ui_language(language) == "ru":
        return render_help_content_ru(llm_enabled=llm_enabled)
    actions_label = "Reports and optimizer"
    optimizer_label = "Query LLM optimizer" if llm_enabled else "Query optimizer"
    selected_action_line = (
        "Run <strong>Python Report</strong>, optional <strong>LLM narrative</strong>, or <strong>Query LLM optimizer</strong> only from a selected Details page."
        if llm_enabled
        else "Run <strong>Python Report</strong> or <strong>Query optimizer</strong> only from a selected Details page."
    )
    web_scan_boundary = (
        "Recent and Running scans do not auto-run reports, LLM narratives, or optimizer drafts."
        if llm_enabled
        else "Recent and Running scans do not auto-run reports or optimizer jobs."
    )
    known_query_boundary = (
        "It collects and analyzes one query, prepares the deterministic Python report in the same submit job, clears the input after submit, and appends the result to the Known Query ID analysis table. LLM narrative and optimizer actions remain explicit."
        if llm_enabled
        else "It collects and analyzes one query, prepares the deterministic Python report in the same submit job, clears the input after submit, and appends the result to the Known Query ID analysis table. Optimizer actions remain explicit."
    )
    action_copy = (
        "<p><strong>Reports and optimizer</strong> contains explicit selected-case buttons for Python Report, optional LLM narrative, Query LLM optimizer, and combined report + optimizer execution. Outputs appear only after deterministic validation; rejected partial content stays hidden.</p>"
        if llm_enabled
        else "<p><strong>Reports and optimizer</strong> contains explicit selected-case buttons for Python Report, Query optimizer, and combined report + optimizer execution. Outputs appear only after deterministic validation; rejected partial content stays hidden.</p>"
    )
    report_copy = (
        "<p>Use <strong>Python Report</strong> as the deterministic baseline. Optional LLM narrative can improve wording after validation, but analyzer facts remain the source of truth.</p>"
        if llm_enabled
        else "<p>In Python-only mode, reports are generated without LLM calls. Analyzer facts remain the source of truth, and raw profile text or SQL stays outside trusted output.</p>"
    )
    optimizer_copy = (
        "Use the optimizer only after opening a selected Details page. It can return a trusted draft or recommendation-only guidance after validation, but Query Doctor never executes generated SQL."
        if llm_enabled
        else "Use the optimizer only after opening a selected Details page. It can return trusted recommendation-only guidance without LLM calls, but Query Doctor never executes generated SQL."
    )
    auto_report_answer = (
        "To avoid mass LLM execution and trusted-looking output without a selected case. Report generation remains an explicit user action."
        if llm_enabled
        else "To avoid trusted-looking output without a selected case. Report generation remains an explicit user action."
    )
    return f"""
<section class="panel docs-panel help-panel" aria-label="Query Doctor help">
<h1>Help</h1>
<div class="report-body help-body">
<p class="help-lede">Query Doctor is a local-first Big Data query diagnostics tool focused on Apache Impala production triage, with bounded local Trino production lanes for operators. It ranks suspicious Recent queries, collects bounded profile context, derives deterministic evidence, optionally enriches it with safe metadata and runtime context, and generates validated raw-free reports. The full production triage engine is Apache Impala. Local Trino production support covers configured retained-list Recent diagnosis, one configured Query ID, raw-free materialized Details, deterministic Python Report, and optimizer guidance; <code>trino_support_mode=beta</code> keeps the beta label and production mode uses the same bounded local surface without expanding broader Trino production triage support.</p>

<nav class="help-card-grid" aria-label="Help shortcuts">
<a class="help-card" href="/"><span>Query Inbox</span><strong>Triage materialized Recent cases or inspect one Known Query ID.</strong></a>
<a class="help-card" href="/#recent-results"><span>Results</span><strong>Read priority filters, findings, metadata, and stats signals.</strong></a>
<a class="help-card" href="#workload-patterns"><span>Workloads</span><strong>Find repeated patterns, open the right details, and verify reruns.</strong></a>
<a class="help-card" href="#details-actions"><span>Details</span><strong>Start with the recommendation, then expand evidence.</strong></a>
<a class="help-card" href="#safety"><span>Safety</span><strong>See what browser output intentionally hides.</strong></a>
</nav>

<h2>On this page</h2>
<ul class="help-toc">
<li><a href="#quick-start">Quick start</a></li>
<li><a href="#workflows">Workflows</a></li>
<li><a href="#results-table">Results table</a></li>
<li><a href="#workload-patterns">Workload patterns</a></li>
<li><a href="#details-actions">{actions_label}</a></li>
<li><a href="#metadata">Metadata</a></li>
<li><a href="#safety">Safety boundary</a></li>
<li><a href="#github-docs">GitHub documentation</a></li>
<li><a href="#common-questions">Common questions</a></li>
</ul>

<section id="quick-start" class="help-section-block">
<h2>Quick start</h2>
<ol class="help-step-list">
<li><a href="/">Open Query Inbox</a>. If materialized cases are already available, start from the status strip, result views, and owner/pool tag or value, lifecycle/readiness/action filters; use <strong>New scan</strong> only to refresh a stale inbox or change source, time range, workflow, or query type.</li>
<li>Choose <strong>Finished queries</strong>, <strong>Running now</strong>, or <strong>One Query ID</strong> in the first control when starting or changing a scan.</li>
<li>Use <strong>Engine</strong> to keep production triage on <strong>Impala</strong>. <strong>Trino</strong> becomes selectable only after local Trino config is present, for retained-list <strong>Finished queries</strong> or <strong>One Query ID</strong>.</li>
<li>Use <strong>Finished queries</strong> for normal batch triage. Use <strong>Running now</strong> only when you need a lower-confidence live snapshot.</li>
<li>Switch to <strong>Known Query ID</strong> when you already have one query ID. Recent-query filters are intentionally hidden in that mode.</li>
<li>Open a result row in the same tab and start with <strong>Recommended change</strong>: what to try next, how to verify it, where to inspect, and why it matters. Expand <strong>Diagnostics and evidence</strong> when you need pipeline state or analyzer basis.</li>
<li>For repeated patterns, open <strong>Workload patterns</strong>, then the top workload Details page, then the best representative query Details page.</li>
<li>{selected_action_line}</li>
</ol>
</section>

<div class="help-topic-stack">
<details id="workflows" class="help-topic">
<summary><span>Workflows</span><small>Recent queries, Running now, Known Query ID, Trino</small></summary>
<div class="help-topic-body">
<p>Most demos and investigations start from <strong>Recent queries</strong>, the flagship production triage workflow. Query Inbox shows safe materialized results first when they exist, marks stale summaries only from safe freshness fields, and keeps <strong>New scan</strong> as the control for refreshing or changing source, time range, workflow, or query type. Use <strong>Known Query ID</strong> only when you already have one query ID and want to skip batch discovery.</p>

<h3>Recent queries</h3>
<p>Recent queries is the primary operator workflow. It reads query summaries from the selected source, applies filters, collects bounded profiles for selected queries, runs deterministic analysis, and shows action-oriented result filters. {web_scan_boundary}</p>
<ul>
<li>Verify <strong>Source cluster</strong> first. Credentials and endpoints stay in local config; the browser only selects among configured sources.</li>
<li>The first workflow control selects <strong>Finished queries</strong>, <strong>Running now</strong>, or <strong>One Query ID</strong>. Finished queries remain the default triage path.</li>
<li>For <strong>Finished queries</strong>, set <strong>Search depth</strong> or an exact UTC range to choose the bounded window for the selected source. Large windows can increase load on Cloudera Manager, direct Impala UI endpoints, and optional Prometheus collection, so use owner, resource-pool, query-type, or duration filters when possible.</li>
<li>Leave <strong>Minimum duration</strong> empty when you want long-running queries and repeated short workload patterns in the same triage pass.</li>
<li>Owner-gated sources keep the required <strong>Username</strong> visible in Basic scan. Optional user, resource-pool, and query-type filters stay config-owned unless <code>web_advanced_settings_enabled</code> makes them editable in Advanced settings.</li>
<li>Runtime context is collected automatically when the selected source supports it. Cloudera Manager clusters add bounded event and metric summaries; direct Impala Recent and Running scans use profile evidence and skip Cloudera Manager-only context.</li>
<li>The Results filter shows all available views in one toolbar: <strong>Needs attention</strong>, <strong>Worth reviewing</strong>, repeated workloads, rewrite opportunities, and stats candidates.</li>
<li><strong>Owner tagged</strong> and <strong>Pool tagged</strong> filter rows that already carry safe owner or pool tags. Concrete <strong>Owner: ...</strong> and <strong>Pool: ...</strong> chips use opaque URL tokens derived from the current materialized rows. They do not submit raw owner or pool values and do not change scan parameters.</li>
<li><strong>Clean analysis</strong>, <strong>Status follow-up</strong>, and <strong>Metadata available</strong> are view-only lifecycle filters over already analyzed results. They do not change scan parameters.</li>
<li><strong>Validated reports</strong>, <strong>Optimizer guidance</strong>, and <strong>Outcomes recorded</strong> are view-only filters over already analyzed results. They do not change scan parameters or run reports, optimizer jobs, generated SQL, or SQL execution.</li>
<li><strong>Rewrite opportunities</strong> are deterministic query-shape review opportunities. They do not promise speedup and do not execute SQL.</li>
<li><strong>Stats to check</strong> rows require metadata evidence, estimate mismatch, and planning-sensitive runtime symptoms. They still require EXPLAIN comparison and a comparable rerun.</li>
<li><strong>Only queries with spills</strong> is a display filter over analyzed results; it does not change scan parameters.</li>
</ul>

<h3>Running now</h3>
<p>Running now uses the same result and details shape as Finished queries, but scans only queries that are running at scan time. It has no Scan date or Scan Hour filter. Profiles can be incomplete until a query finishes, so findings can be lower-confidence than completed-query analysis. Runtime context is collected automatically when the selected source supports it.</p>

<h3>Known Query ID</h3>
<p>Known Query ID is for one explicit query ID. Use the shared Cluster selector above What to analyze, then enter the Query ID and run analysis. {known_query_boundary}</p>
<p>If you have one exported Impala text profile instead of live access, use the local/private <strong>Exported profile</strong> upload in One Query ID mode, or configure <code>manual_profile_dir</code> as a local profile inbox. For inbox files, name the file with the Query ID slug by replacing <code>:</code> with <code>_</code>, for example <code>aaaaaaaaaaaaaaaa_0000000000000001.txt</code>. Both paths stage exactly one text profile through the bounded redacted analyzer path; the browser never renders the uploaded profile content, filename, file-system location, or temporary upload artifact. Public demo mode hides uploads.</p>
<h3>Trino Recent and One Query ID</h3>
<p>When <strong>Trino</strong> is configured, <strong>Finished queries</strong> reads one bounded retained pruned coordinator query list, then bounded pruned coordinator QueryInfo payloads for selected rows; <strong>One Query ID</strong> performs the same bounded QueryInfo read for one explicit ID. Both render deterministic compact diagnosis from raw-free boundaries and can open a raw-free Details view plus deterministic Python Report and optimizer guidance after server-owned case materialization. Configured beta sources keep the Trino Beta labels in the Source cluster selector; configured production-mode sources use the same labels without Beta. The Engine control narrows that selector to Impala-capable sources or Trino-ready sources before workflow selection. Forged or stale Trino submits still fail closed before analysis or async job creation. It does not support Trino Running scans, query-history crawling, metadata collection, LLM reports, Query Optimizer jobs, generated Trino SQL, or SQL execution.</p>
</div>
</details>

<details id="results-table" class="help-topic">
<summary><span>Results table</span><small>Filters, columns, and triage wording</small></summary>
<div class="help-topic-body">
<p>The table is a triage surface. Open a row for Details before making production changes.</p>

<h3>Columns and filters</h3>
<ul>
<li>The filter toolbar shows counts for available result views, and a compact scanned-volume note shows how many summaries were inspected.</li>
<li><strong>Scan context</strong> after the table keeps coverage, compact notes, and top workload follow-up links for the result set. Critical scan warnings still open above the table.</li>
<li><strong>Rank</strong> is ordering within the current group, not a root-cause verdict.</li>
<li><strong>Query ID</strong> opens the details page for the selected case.</li>
<li><strong>User</strong> shows the sanitized query user when available.</li>
<li><strong>Priority</strong> combines a word label with the deterministic analyzer score.</li>
<li><strong>Duration</strong> comes from query summary data when available.</li>
<li><strong>Next</strong> opens selected-case Details for the supported inspection, change, and verification path.</li>
<li><strong>Rewrite opportunities</strong> use Finding, Candidate, Impact, Confidence, and Rewrite support columns.</li>
<li><strong>Stats to check</strong> uses Finding, Candidate, Need, Speed benefit, and Confidence columns.</li>
<li>Cases without triage severity and without Medium/High optimization or statistics update candidacy are intentionally hidden from separate result filters.</li>
<li><strong>Finding</strong> explains deterministic signals without raw evidence.</li>
</ul>
</div>
</details>

<details id="workload-patterns" class="help-topic">
<summary><span>Workload patterns</span><small>Repeated patterns, next checks, and verification</small></summary>
<div class="help-topic-body">
<p>Workload patterns group similar safe fingerprints from the current scan. They help distinguish one-off expensive queries from repeated behavior, but a repeated fingerprint is not a root-cause claim by itself.</p>
<h3>What should I open first?</h3>
<ul>
<li>Use <strong>Workload follow-up</strong> in Scan context for the highest-priority repeated patterns.</li>
<li>Open the top <strong>Workload details</strong> page to see why the pattern matters, where to inspect, what to try, and how to verify.</li>
<li>Inside workload details, open the <strong>Best Details case</strong> first. Use other representative queries to confirm whether the same signal repeats.</li>
</ul>
<h3>How should I read workload labels?</h3>
<ul>
<li><strong>Baseline slowdown</strong> means current workload p95 is above the local baseline under comparable scan history.</li>
<li><strong>Admission/runtime review</strong>, <strong>Stats review</strong>, and <strong>Query-shape review</strong> are next-check directions backed by selected-case facts.</li>
<li><strong>Low-value repeat</strong> usually means do not change SQL, stats, or runtime settings unless pool or owner review raises priority.</li>
</ul>
<h3>How do I verify?</h3>
<p>Change one supported thing, rerun under comparable scope, then compare workload p95, signal count, and recorded outcome. Use <strong>Record rerun outcome</strong> on a selected representative Details page so future workload confidence has feedback.</p>
</div>
</details>

<details id="details-actions" class="help-topic">
<summary><span>{actions_label}</span><small>Recommendation, diagnostics, reports, optimizer</small></summary>
<div class="help-topic-body">
<p>Details shows a browser-safe summary for one analyzed query. <strong>Recommended change</strong> leads with one supported next step and its success check, then shows where to inspect and why the query matters. Extra supported actions and <strong>Diagnostics and evidence</strong> stay available without turning the first screen into a low-level evidence dump.</p>
{action_copy}

<h3>Validated reports</h3>
{report_copy}

<h3>Details-page {optimizer_label}</h3>
<p>{optimizer_copy}</p>
<p>If validation fails, Query Doctor can show trusted recommendations-only or no-rewrite guidance instead of an unsafe draft.</p>
</div>
</details>

<details id="metadata" class="help-topic">
<summary><span>Metadata</span><small>Bounded read-only table facts</small></summary>
<div class="help-topic-body">
<p>Metadata collection is explicit, bounded, read-only, and allowlisted. Metadata can be unavailable or partial; that is a normal degraded state.</p>
<h3>Allowed metadata summaries</h3>
<ul>
<li>Table definition summary</li>
<li>Table statistics summary</li>
<li>Column statistics summary</li>
</ul>
<p>Query Doctor does not run user queries, maintenance statements, metadata cache updates, schema changes, or data-changing statements for metadata collection.</p>
</div>
</details>

<details id="safety" class="help-topic">
<summary><span>Safety boundary</span><small>What browser output shows and hides</small></summary>
<div class="help-topic-body">
<p>Browser UI intentionally hides raw query text, raw profile text, raw metadata output, filesystem locations, case directory details, process output, secrets, environment secret values, runtime internals, and raw evidence links. This is a product boundary, not a missing feature.</p>
<p>Safe browser output means summarized deterministic facts, statuses, validated reports, trusted optimizer outcomes, and bounded limitations.</p>
</div>
</details>

<details id="github-docs" class="help-topic">
<summary><span>GitHub documentation</span><small>Maintained external docs</small></summary>
<div class="help-topic-body">
<p>This page stays short and task-oriented. Full documentation lives in the repository on GitHub:</p>
<ul class="help-link-list">
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/README.md" target="_blank" rel="noopener noreferrer">Project README</a> - install, commands, workflow overview, and current public status.</li>
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/README.md" target="_blank" rel="noopener noreferrer">Documentation index</a> - the maintained map of current docs.</li>
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/security-model.md" target="_blank" rel="noopener noreferrer">Security model</a> - public security, privacy, and demo-sharing overview.</li>
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/DEMO.md" target="_blank" rel="noopener noreferrer">Synthetic demo docs</a> - maintained demo talk track outside the product UI.</li>
</ul>
</div>
</details>

<details id="common-questions" class="help-topic">
<summary><span>Common questions</span><small>Safety, metadata, reports, and future scope</small></summary>
<div class="help-topic-body">
<h3>Why can I not see the query SQL?</h3>
<p>Raw query text can contain sensitive business logic, table names, and literals. Query Doctor shows safe summaries and deterministic findings instead.</p>
<h3>Why can I not see the full profile?</h3>
<p>Full profiles can be large and sensitive. The UI shows analyzer-owned facts and bounded status summaries.</p>
<h3>Why is metadata partial or skipped?</h3>
<p>Metadata collection is bounded. It can be disabled, unavailable, limited to top cases, or stopped by safety limits. Profile-based findings still remain usable.</p>
<h3>Why does Recent queries not generate reports automatically?</h3>
<p>{auto_report_answer}</p>
<h3>Can Query Doctor execute optimized SQL?</h3>
<p>No. Details-page optimizer actions can produce trusted drafts or recommendations, but Query Doctor never executes generated SQL. Benchmarks must be separate explicit read-only checks outside the UI workflow.</p>
<h3>Does a stats gap mean stats caused the slowdown?</h3>
<p>No. Treat it as a statistics update candidate only when metadata gaps, estimate mismatch, and planning-sensitive runtime symptoms line up. Confirmation requires EXPLAIN comparison and a comparable rerun.</p>
<h3>Does runtime metrics context prove root cause?</h3>
<p>Usually no. Runtime metrics are bounded context. They become stronger only when correlated with deterministic profile evidence.</p>
<h3>Where is future engine scope documented?</h3>
<p>Help covers current product workflows. Future engine and storage scope lives in the public roadmap and support matrix, while Apache Impala remains the full production triage engine. Trino is intentionally limited to configured retained-list Recent diagnosis, One Query ID, raw-free materialized Details, deterministic Python Report, and optimizer guidance.</p>
</div>
</details>
</div>
</section>
""".strip()


def render_help_content_ru(*, llm_enabled: bool = True) -> str:
    actions_label = "Отчеты и оптимизатор"
    optimizer_label = "Query LLM optimizer" if llm_enabled else "Query optimizer"
    selected_action_line = (
        "Запускайте <strong>Python-отчет</strong>, optional <strong>LLM narrative</strong> или <strong>Query LLM optimizer</strong> только со страницы Details выбранного кейса."
        if llm_enabled
        else "Запускайте <strong>Python-отчет</strong> или <strong>Query optimizer</strong> только со страницы Details выбранного кейса."
    )
    web_scan_boundary = (
        "Recent и Running scans не запускают отчеты, LLM narrative или optimizer drafts автоматически."
        if llm_enabled
        else "Recent и Running scans не запускают отчеты или optimizer jobs автоматически."
    )
    known_query_boundary = (
        "Он собирает и анализирует один query ID, готовит deterministic Python report в том же submit-job, очищает ввод после submit и добавляет результат в таблицу Known Query ID. LLM narrative и optimizer actions остаются явными."
        if llm_enabled
        else "Он собирает и анализирует один query ID, готовит deterministic Python report в том же submit-job, очищает ввод после submit и добавляет результат в таблицу Known Query ID. Optimizer actions остаются явными."
    )
    action_copy = (
        "<p><strong>Отчеты и оптимизатор</strong> содержит явные действия для выбранного кейса: Python-отчет, optional LLM narrative, Query LLM optimizer и combined report + optimizer execution. Outputs появляются только после deterministic validation; rejected partial content остается hidden.</p>"
        if llm_enabled
        else "<p><strong>Отчеты и оптимизатор</strong> содержит явные действия для выбранного кейса: Python-отчет, Query optimizer и combined report + optimizer execution. Outputs появляются только после deterministic validation; rejected partial content остается hidden.</p>"
    )
    report_copy = (
        "<p><strong>Python-отчет</strong> - deterministic baseline. Optional LLM narrative может улучшить wording после validation, но source of truth остаются analyzer facts.</p>"
        if llm_enabled
        else "<p>В Python-only режиме reports генерируются без LLM calls. Source of truth остаются analyzer facts; raw profile text и SQL не попадают в trusted output.</p>"
    )
    optimizer_copy = (
        "Optimizer используйте только после открытия Details выбранного кейса. Он может вернуть trusted draft или recommendation-only guidance после validation, но Query Doctor никогда не выполняет generated SQL."
        if llm_enabled
        else "Optimizer используйте только после открытия Details выбранного кейса. Он может вернуть trusted recommendation-only guidance без LLM calls, но Query Doctor никогда не выполняет generated SQL."
    )
    auto_report_answer = (
        "Чтобы не запускать массовую LLM-генерацию и не создавать trusted-looking output без выбранного кейса. Генерация отчета остается явным действием пользователя."
        if llm_enabled
        else "Чтобы не создавать trusted-looking output без выбранного кейса. Генерация отчета остается явным действием пользователя."
    )
    return f"""
<section class="panel docs-panel help-panel" aria-label="Query Doctor help">
<h1>Справка</h1>
<div class="report-body help-body">
<p class="help-lede">Query Doctor - local-first Big Data query diagnostics tool, сфокусированный на Apache Impala production triage и bounded local Trino production lanes для operators. Он ранжирует подозрительные Recent queries, собирает bounded profile context, выводит deterministic evidence, опционально обогащает его safe metadata и runtime context и генерирует validated raw-free reports. Полный production triage engine сейчас Apache Impala. Local Trino production support покрывает configured retained-list Recent diagnosis, один configured Query ID, raw-free materialized Details, deterministic Python Report и optimizer guidance; <code>trino_support_mode=beta</code> сохраняет beta label, а production mode использует тот же bounded local surface без расширения broader Trino production triage support.</p>

<nav class="help-card-grid" aria-label="Help shortcuts">
<a class="help-card" href="/"><span>Query Inbox</span><strong>Разобрать materialized Recent cases или один Known Query ID.</strong></a>
<a class="help-card" href="/#recent-results"><span>Результаты</span><strong>Смотреть priority filters, findings, metadata и stats signals.</strong></a>
<a class="help-card" href="#workload-patterns"><span>Workloads</span><strong>Найти repeated patterns, открыть правильные Details и проверить rerun.</strong></a>
<a class="help-card" href="#details-actions"><span>Детали</span><strong>Начать с рекомендации, затем раскрывать evidence.</strong></a>
<a class="help-card" href="#safety"><span>Безопасность</span><strong>Что browser output намеренно скрывает.</strong></a>
</nav>

<h2>На этой странице</h2>
<ul class="help-toc">
<li><a href="#quick-start">Быстрый старт</a></li>
<li><a href="#workflows">Рабочие режимы</a></li>
<li><a href="#results-table">Таблица результатов</a></li>
<li><a href="#workload-patterns">Workload patterns</a></li>
<li><a href="#details-actions">{actions_label}</a></li>
<li><a href="#metadata">Метаданные</a></li>
<li><a href="#safety">Граница безопасности</a></li>
<li><a href="#github-docs">Документация GitHub</a></li>
<li><a href="#common-questions">Частые вопросы</a></li>
</ul>

<section id="quick-start" class="help-section-block">
<h2>Быстрый старт</h2>
<ol class="help-step-list">
<li><a href="/">Откройте Query Inbox</a>. Если materialized cases уже есть, начинайте со status strip, result views и owner/pool tag или value filters, lifecycle/readiness/action filters; <strong>New scan</strong> используйте только для refresh stale inbox или смены source, time range, workflow или query type.</li>
<li>При запуске или изменении scan выберите <strong>Finished queries</strong>, <strong>Running now</strong> или <strong>One Query ID</strong> в первом переключателе.</li>
<li><strong>Engine</strong> оставляет production triage на <strong>Impala</strong>. <strong>Trino</strong> становится selectable только после local Trino config, для retained-list <strong>Finished queries</strong> или <strong>One Query ID</strong>.</li>
<li>Для обычного batch triage используйте <strong>Finished queries</strong>. <strong>Running now</strong> оставляйте для live snapshot с меньшей уверенностью.</li>
<li>Переключитесь на <strong>Known Query ID</strong>, если у вас уже есть один query ID. Фильтры Recent-query в этом режиме скрыты.</li>
<li>Откройте строку результата в той же вкладке и начните с <strong>Рекомендуемое изменение</strong>: почему запрос важен, где проверить, что попробовать и как проверить rerun. <strong>Diagnostics and evidence</strong> раскрывайте, когда нужна техническая база.</li>
<li>Для repeated patterns откройте <strong>Workload patterns</strong>, затем workload Details, затем лучший representative query Details.</li>
<li>{selected_action_line}</li>
</ol>
</section>

<div class="help-topic-stack">
<details id="workflows" class="help-topic">
<summary><span>Рабочие режимы</span><small>Recent queries, Running now, Known Query ID, Trino</small></summary>
<div class="help-topic-body">
<p>Flagship workflow для operator triage - <strong>Recent queries</strong>. Query Inbox сначала показывает safe materialized results, если они есть, помечает stale summaries только по safe freshness fields, а <strong>New scan</strong> остается control для refresh или смены source, time range, workflow или query type. <strong>Known Query ID</strong> нужен, когда уже известен один query ID и batch discovery не требуется.</p>
<h3>Recent queries</h3>
<p>Recent queries читает summaries из выбранного источника, применяет фильтры, собирает bounded profiles, запускает детерминированный analysis и показывает action-oriented result filters. {web_scan_boundary}</p>
<ul>
<li>Сначала проверьте <strong>Source cluster</strong>. Credentials и endpoints остаются в local config.</li>
<li>Первый переключатель выбирает <strong>Finished queries</strong>, <strong>Running now</strong> или <strong>One Query ID</strong>. Finished queries остается основным triage path.</li>
<li>Для <strong>Finished queries</strong> задайте <strong>Search depth</strong> или exact UTC range, чтобы выбрать bounded window для выбранного источника. Большие окна могут увеличивать нагрузку на Cloudera Manager, direct Impala UI endpoints и optional Prometheus collection, поэтому по возможности используйте owner, resource-pool, query-type или duration filters.</li>
<li>Оставляйте <strong>Minimum duration</strong> пустым, если в одном triage pass нужны long-running queries и repeated short workload patterns.</li>
<li>Runtime context собирается автоматически, когда выбранный source это поддерживает.</li>
<li>Фильтр Results показывает все доступные срезы в одной панели: <strong>Needs attention</strong>, <strong>Worth reviewing</strong>, repeated workloads, rewrite opportunities и stats candidates.</li>
<li><strong>Owner tagged</strong> и <strong>Pool tagged</strong> фильтруют строки, где уже есть safe owner или pool tags. Конкретные chips <strong>Owner: ...</strong> и <strong>Pool: ...</strong> используют opaque URL tokens, рассчитанные по текущим materialized rows. Они не submit raw owner или pool values и не меняют scan parameters.</li>
<li><strong>Clean analysis</strong>, <strong>Status follow-up</strong> и <strong>Metadata available</strong> - view-only lifecycle filters по уже analyzed results. Они не меняют scan parameters.</li>
<li><strong>Validated reports</strong>, <strong>Optimizer guidance</strong> и <strong>Outcomes recorded</strong> - view-only filters по уже analyzed results. Они не меняют scan parameters и не запускают reports, optimizer jobs, generated SQL или SQL execution.</li>
<li><strong>Only queries with spills</strong> - display filter по уже проанализированным результатам.</li>
</ul>
<h3>Running now</h3>
<p>Running now использует такую же форму результатов и Details, но сканирует только running queries на момент запуска. Профили могут быть неполными, поэтому уверенность ниже, чем для completed-query analysis.</p>
<h3>Known Query ID</h3>
<p>Known Query ID анализирует один явный query ID. Используйте общий Cluster selector, введите Query ID и запустите analysis. {known_query_boundary}</p>
<p>Если вместо live access есть один exported Impala text profile, используйте local/private upload <strong>Exported profile</strong> в режиме One Query ID или настройте <code>manual_profile_dir</code> как local profile inbox. Для inbox files назовите файл slug-версией Query ID: замените <code>:</code> на <code>_</code>, например <code>aaaaaaaaaaaaaaaa_0000000000000001.txt</code>. Оба пути stage ровно один text profile через bounded redacted analyzer path; browser никогда не render uploaded profile content, filename, file-system location или temporary upload artifact. Public demo mode скрывает uploads.</p>
<h3>Trino Recent и One Query ID</h3>
<p>Когда <strong>Trino</strong> настроен, <strong>Finished queries</strong> читает один bounded retained pruned coordinator query list, затем bounded pruned coordinator QueryInfo payloads для выбранных rows; <strong>One Query ID</strong> выполняет тот же bounded QueryInfo read для одного explicit ID. Оба пути показывают deterministic compact diagnosis из raw-free boundaries и могут открыть raw-free Details view плюс deterministic Python Report и optimizer guidance после server-owned case materialization. Configured beta sources сохраняют Trino Beta labels в Source cluster selector; configured production-mode sources используют такие же labels без Beta. Engine control сужает selector до Impala-capable sources или Trino-ready sources до выбора workflow. Forged или stale Trino submits все равно fail closed до analysis или async job creation. Trino не поддерживает Running scans, query-history crawling, metadata collection, LLM reports, Query Optimizer jobs, generated Trino SQL или SQL execution.</p>
</div>
</details>

<details id="results-table" class="help-topic">
<summary><span>Таблица результатов</span><small>Фильтры, колонки и triage wording</small></summary>
<div class="help-topic-body">
<p>Таблица - triage surface. Перед production changes открывайте строку в Details.</p>
<h3>Колонки и фильтры</h3>
<ul>
<li>Панель фильтров показывает counts для доступных result views, а компактная пометка <strong>Scanned</strong> показывает объем просмотренных summaries.</li>
<li><strong>Scan context</strong> после таблицы держит coverage, compact notes и top workload follow-up links для текущего result set. Critical scan warnings остаются видимыми над таблицей.</li>
<li><strong>Rank</strong> - порядок внутри текущей группы, не root-cause verdict.</li>
<li><strong>Query ID</strong> открывает details page выбранного кейса.</li>
<li><strong>Priority</strong> объединяет word label и deterministic analyzer score.</li>
<li><strong>Duration</strong> показывает duration из query summary, когда оно доступно.</li>
<li><strong>Next</strong> открывает Details выбранного кейса для supported inspection, change и verification path.</li>
<li><strong>Rewrite opportunities</strong> и <strong>Stats to check</strong> показывают только поддержанные кандидаты для проверки.</li>
</ul>
</div>
</details>

<details id="workload-patterns" class="help-topic">
<summary><span>Workload patterns</span><small>Repeated patterns, next checks и verification</small></summary>
<div class="help-topic-body">
<p>Workload patterns группируют похожие safe fingerprints из текущего scan. Они помогают отличить one-off expensive query от repeated behavior, но fingerprint сам по себе не является root-cause claim.</p>
<h3>Что открыть первым?</h3>
<ul>
<li>Используйте <strong>Workload follow-up</strong> в Scan context для highest-priority repeated patterns.</li>
<li>Откройте верхний <strong>Workload details</strong>, чтобы увидеть why, where, what to try и how to verify.</li>
<li>В workload details сначала открывайте <strong>Best Details case</strong>. Остальные representative queries нужны для подтверждения, что сигнал повторяется.</li>
</ul>
<h3>Как читать workload labels?</h3>
<ul>
<li><strong>Baseline slowdown</strong> означает, что текущий workload p95 выше local baseline при comparable scan history.</li>
<li><strong>Admission/runtime review</strong>, <strong>Stats review</strong> и <strong>Query-shape review</strong> - направления next check, поддержанные selected-case facts.</li>
<li><strong>Low-value repeat</strong> обычно означает: не менять SQL, stats или runtime settings, пока pool/owner review не поднимет priority.</li>
</ul>
<h3>Как проверить?</h3>
<p>Меняйте одну поддержанную вещь, rerun под comparable scope, затем сравнивайте workload p95, signal count и recorded outcome. Используйте <strong>Record rerun outcome</strong> на representative Details page.</p>
</div>
</details>

<details id="details-actions" class="help-topic">
<summary><span>{actions_label}</span><small>Рекомендации, диагностика, отчеты, optimizer</small></summary>
<div class="help-topic-body">
<p>Details - browser-safe summary для одного analyzed query. <strong>Рекомендуемое изменение</strong> сначала показывает, почему запрос важен, где проверить, что попробовать и как проверить comparable rerun. Extra supported actions и <strong>Diagnostics and evidence</strong> остаются доступны, но не перегружают первый экран.</p>
{action_copy}
<h3>Validated reports</h3>
{report_copy}
<h3>Details-page {optimizer_label}</h3>
<p>{optimizer_copy}</p><p>Если validation fails, Query Doctor может показать trusted recommendations-only или no-rewrite guidance вместо unsafe draft.</p>
</div>
</details>

<details id="metadata" class="help-topic">
<summary><span>Метаданные</span><small>Bounded read-only table facts</small></summary>
<div class="help-topic-body">
<p>Metadata collection явная, bounded, read-only и allowlisted. Partial или unavailable metadata - нормальное degraded state.</p>
<h3>Allowed metadata summaries</h3>
<ul><li>Table definition summary</li><li>Table statistics summary</li><li>Column statistics summary</li></ul>
<p>Query Doctor не запускает user queries, maintenance statements, metadata cache updates, schema changes или data-changing statements для metadata collection.</p>
</div>
</details>

<details id="safety" class="help-topic">
<summary><span>Граница безопасности</span><small>Что browser output показывает и скрывает</small></summary>
<div class="help-topic-body">
<p>Browser UI намеренно скрывает raw query text, raw profile text, raw metadata output, filesystem locations, case directory details, process output, secrets, environment secret values, runtime internals и raw evidence links.</p>
<p>Safe browser output - это summarized deterministic facts, statuses, validated reports, trusted optimizer outcomes и bounded limitations.</p>
</div>
</details>

<details id="github-docs" class="help-topic">
<summary><span>Документация GitHub</span><small>Поддерживаемые внешние docs</small></summary>
<div class="help-topic-body">
<p>Эта страница остается короткой и task-oriented. Полная documentation живет в repository на GitHub:</p>
<ul class="help-link-list">
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/README.md" target="_blank" rel="noopener noreferrer">Project README</a></li>
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/README.md" target="_blank" rel="noopener noreferrer">Documentation index</a></li>
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/security-model.md" target="_blank" rel="noopener noreferrer">Security model</a></li>
<li><a href="https://github.com/alexandrefimov/Query-Doctor/blob/main/docs/DEMO.md" target="_blank" rel="noopener noreferrer">Synthetic demo docs</a></li>
</ul>
</div>
</details>

<details id="common-questions" class="help-topic">
<summary><span>Частые вопросы</span><small>Безопасность, metadata, reports и scope</small></summary>
<div class="help-topic-body">
<h3>Почему не видно query SQL?</h3><p>Raw query text может содержать sensitive business logic, table names и literals. Query Doctor показывает safe summaries и deterministic findings.</p>
<h3>Почему не видно полный profile?</h3><p>Full profiles могут быть большими и sensitive. UI показывает analyzer-owned facts и bounded status summaries.</p>
<h3>Почему metadata partial или skipped?</h3><p>Metadata collection bounded. Ее можно отключить, источник может быть недоступен, сбор может ограничиться top cases или остановиться по safety limits. Profile-based findings остаются usable.</p>
<h3>Почему Recent queries не генерирует reports автоматически?</h3><p>{auto_report_answer}</p>
<h3>Может ли Query Doctor выполнить optimized SQL?</h3><p>Нет. Optimizer actions могут создать trusted drafts или recommendations, но Query Doctor никогда не выполняет generated SQL.</p>
<h3>Означает ли stats gap, что stats вызвали slowdown?</h3><p>Нет. Это statistics update candidate только когда metadata gaps, estimate mismatch и planning-sensitive runtime symptoms сходятся. Подтверждение требует EXPLAIN comparison и comparable rerun.</p>
<h3>Доказывает ли runtime metrics context root cause?</h3><p>Обычно нет. Runtime metrics - bounded context. Они становятся сильнее только при correlation с deterministic profile evidence.</p>
<h3>Где описан future engine scope?</h3>
<p>Help описывает текущие product workflows. Future engine и storage scope живут в roadmap и support matrix; full production triage engine сейчас Apache Impala. Trino намеренно ограничен configured retained-list Recent diagnosis, One Query ID, raw-free materialized Details, deterministic Python Report и optimizer guidance.</p>
</div>
</details>
</div>
</section>
""".strip()
