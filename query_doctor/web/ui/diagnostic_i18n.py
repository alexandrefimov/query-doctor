"""Russian UI localization for deterministic diagnostic display strings."""

from __future__ import annotations

import re
from typing import Any

from query_doctor.web.ui.i18n import normalize_ui_language


_EXACT_RU = {
    "Action outcomes": "Результаты действий",
    "Additional deterministic signal": "Дополнительный детерминированный сигнал",
    "Admission/runtime follow-up": "Проверка admission/runtime",
    "Admission/runtime": "Admission/runtime",
    "Admission or queueing signals need runtime follow-up": "Сигналы admission или очереди требуют runtime-проверки",
    "Admission wait helps separate queueing from execution time.": "Admission wait помогает отделить очередь от времени выполнения.",
    "All analyzed": "Все проанализированные",
    "Analyzed": "Проанализировано",
    "Available": "Есть",
    "Candidate": "Кандидат",
    "Candidate details": "Детали кандидата",
    "Clean": "Без сигнала",
    "Cluster context is supporting evidence, not standalone proof.": "Контекст кластера является поддерживающим сигналом, а не самостоятельным доказательством.",
    "Collection, deterministic analysis, metadata, or report processing did not complete cleanly, so this row needs attention before diagnostic conclusions are trusted.": "Сбор, детерминированный анализ, метаданные или отчет не завершились корректно; сначала разберите этот шаг, и только потом доверяйте диагностическим выводам.",
    "Confidence": "Уверенность",
    "Coverage": "Покрытие",
    "Coverage, scan notes, and compact follow-up links for this result set.": "Покрытие, заметки скана и компактные follow-up ссылки для этого набора результатов.",
    "Data movement follow-up": "Проверка data movement",
    "Data movement may be inflating runtime": "Data movement может увеличивать runtime",
    "Details": "Детали",
    "Deterministic score severity for triage.": "Детерминированная severity для triage.",
    "Duration": "Длительность",
    "Elapsed query duration with baseline context when available.": "Длительность запроса с baseline-контекстом, если он доступен.",
    "Evidence behind this recommendation": "Доказательства для этой рекомендации",
    "Failed": "Ошибка",
    "Finding": "Сигнал",
    "Frequent short": "Частые короткие",
    "Frequent short limitations:": "Ограничения frequent short:",
    "High": "Высокий",
    "High priority": "Высокий приоритет",
    "High: analyzer evidence quality": "Высокая: качество доказательств анализа",
    "How long did it run?": "Сколько выполнялся запрос?",
    "How much work did it do?": "Какой объем работы выполнен?",
    "How strong is the evidence?": "Насколько сильны доказательства?",
    "How urgent is it?": "Насколько срочно?",
    "Impact": "Влияние",
    "Is this normal for similar work?": "Это нормально для похожей нагрузки?",
    "Is this normal in this scan?": "Это нормально для текущего скана?",
    "Low": "Низкий",
    "Low: analyzer evidence quality": "Низкая: качество доказательств анализа",
    "Low: no positive deterministic findings": "Низкая: нет положительных детерминированных находок",
    "Main signal": "Основной сигнал",
    "Medium": "Средний",
    "Medium priority": "Средний приоритет",
    "Medium: analyzer evidence quality": "Средняя: качество доказательств анализа",
    "Metadata": "Метаданные",
    "Metadata contexts": "Контексты метаданных",
    "Memory estimate evidence follow-up": "Проверка доказательств по оценке memory",
    "Memory pressure needs spill/scratch follow-up": "Memory pressure требует проверки spill/scratch",
    "Missing": "Нет",
    "Mixed-signal follow-up": "Проверка смешанных сигналов",
    "Multiple follow-ups": "Несколько направлений проверки",
    "Multiple supported signals need review": "Несколько поддержанных сигналов требуют проверки",
    "N/A": "Н/Д",
    "Need": "Что проверить",
    "Needs attention": "Требует внимания",
    "No positive deterministic score reasons": "Нет положительных детерминированных причин оценки",
    "No single supported bottleneck is classified yet": "Единый поддержанный bottleneck пока не классифицирован",
    "No supported change direction": "Нет поддержанного направления изменения",
    "No supported problem signal is classified yet": "Поддержанный проблемный сигнал пока не классифицирован",
    "Not applicable": "Не применимо",
    "Not checked": "Не проверено",
    "Not classified": "Не классифицировано",
    "Open Workload Details": "Откройте Workload Details",
    "Priority": "Приоритет",
    "Primary": "Основной",
    "Processing did not finish - diagnosis is not trustworthy yet": "Обработка не завершилась: диагностике пока нельзя доверять",
    "Processing failure follow-up": "Проверка ошибки обработки",
    "Query ID": "Query ID",
    "Query shape is worth a rewrite review": "Форму запроса стоит проверить на rewrite",
    "Query shape is worth a rewrite review: query-shape evidence needs manual review": "Форму запроса стоит проверить на rewrite: сигналы формы запроса требуют ручной проверки",
    "Query-shape follow-up": "Проверка формы запроса",
    "Query-shape follow-up": "Проверка формы запроса",
    "Query-shape recommendation": "Рекомендация по форме запроса",
    "Query-shape follow-up": "Проверка формы запроса",
    "Repeated workloads": "Повторяющиеся workloads",
    "Regression": "Регрессия",
    "Regressed workloads": "Регрессии workloads",
    "Rank": "Ранг",
    "Rewrite opportunities": "Возможности rewrite",
    "Rewrite signal": "Сигнал rewrite",
    "Rewrite support": "Поддержка rewrite",
    "Runtime profile contains operators where estimated rows diverge strongly from actual rows. This may affect planning, memory sizing, and join decisions; it is not a root-cause claim.": "Runtime profile содержит операторы, где estimated rows сильно расходятся с actual rows. Это может влиять на planning, memory sizing и join decisions; это не root-cause claim.",
    "Runtime skew follow-up": "Проверка runtime skew",
    "Runtime skew may be stretching execution": "Runtime skew может растягивать выполнение",
    "Runs": "Запуски",
    "SQL shape follow-up": "Проверка SQL shape",
    "Safe structural location from deterministic facts.": "Безопасная структурная точка проверки из детерминированных фактов.",
    "Score": "Оценка",
    "Scan context": "Контекст скана",
    "Scan notes": "Заметки скана",
    "Selected Query details": "Детали выбранного запроса",
    "Similar queries": "Похожие запросы",
    "Spill filter": "Фильтр spill",
    "Skipped": "Пропущено",
    "Source": "Источник",
    "Speed benefit": "Потенциал ускорения",
    "Stats candidate": "Кандидат stats",
    "Stats evidence follow-up": "Проверка доказательств по stats",
    "Stats gaps are worth checking before a rewrite": "Пробелы в статистике стоит проверить перед rewrite",
    "Stats gaps may be misleading the planner": "Пробелы в статистике могут вводить planner в заблуждение",
    "Stats maintenance recommendation": "Рекомендация по stats maintenance",
    "Stats signal": "Сигнал stats",
    "Stats context limited by metadata coverage": "Stats context ограничен metadata coverage",
    "Stats to check": "Проверить stats",
    "Storage or HDFS signals need follow-up": "Сигналы Storage/HDFS требуют проверки",
    "Storage/HDFS follow-up": "Проверка Storage/HDFS",
    "Strong: analyzer findings plus correlated runtime context": "Сильная: находки анализа плюс коррелированный runtime-контекст",
    "Strong: analyzer findings with metadata context": "Сильная: находки анализа с metadata-контекстом",
    "Supported analyzer signal needs review": "Поддержанный сигнал анализа требует проверки",
    "Supported analyzer signals need review": "Поддержанные сигналы анализа требуют проверки",
    "Table key": "Легенда таблицы",
    "Table stats": "Table stats",
    "The scan summary contains deterministic analyzer signals even though explicit score-reason bullets were unavailable.": "Сводка скана содержит детерминированные сигналы анализа, даже если отдельные score-reason bullets недоступны.",
    "The scan summary carries a positive deterministic score. Treat this as a prompt to inspect the supporting Details evidence.": "Сводка скана содержит положительную детерминированную оценку. Используйте это как повод проверить подтверждающие доказательства в Details.",
    "Unknown": "Неизвестно",
    "Unknown priority": "Приоритет неизвестен",
    "Unknown: evidence is insufficient for a stronger verdict": "Неизвестная: доказательств недостаточно для более сильного вердикта",
    "Use the listed review location as the safe anchor for the first manual check.": "Используйте указанное место проверки как безопасную точку для первой ручной проверки.",
    "User": "Пользователь",
    "Verdict": "Вердикт",
    "View": "Вид",
    "When did it run?": "Когда выполнялся запрос?",
    "Where did it run?": "Где выполнялся запрос?",
    "Who ran it?": "Кто запускал?",
    "Workload impact": "Влияние workload",
    "Workload follow-up": "Проверки workloads",
    "Workload p95": "Workload p95",
    "Workload signal": "Сигнал workload",
    "Worth reviewing": "Стоит проверить",
    "Only queries with spills": "Только запросы со spill",
    "analysis failed": "analysis failed",
    "collection failed": "collection failed",
    "column stats": "column stats",
    "confidence": "уверенность",
    "duration": "длительность",
    "enabled": "включена",
    "high": "высокая",
    "insufficient metadata": "недостаточно метаданных",
    "low": "низкая",
    "main signal": "основной сигнал",
    "medium": "средняя",
    "metadata failed": "metadata failed",
    "no high-signal repeated workloads": "нет повторяющихся workloads с сильным сигналом",
    "no positive analyzer signals": "нет положительных сигналов анализа",
    "pool": "pool",
    "positive score from detailed analyzer reasons": "положительная оценка по детальным причинам анализа",
    "priority": "приоритет",
    "query type": "тип запроса",
    "query window": "окно запроса",
    "resource footprint": "ресурсный footprint",
    "runs": "запуски",
    "signals": "сигналы",
    "skew observed": "skew обнаружен",
    "stats freshness unknown": "свежесть stats неизвестна",
    "table/partition stats": "table/partition stats",
    "table/partition stats first, then column stats": "сначала table/partition stats, затем column stats",
    "unknown": "неизвестно",
    "user": "пользователь",
    "workload baseline": "workload baseline",
    "workload group": "workload group",
}

_PHRASE_RU = (
    (
        "Confirm the failed processing step completes successfully; Details should then show typed score evidence or a specific supported action candidate.",
        "Подтвердите, что сбойный шаг обработки завершился успешно; после этого Details должен показать типизированные score evidence или конкретного поддержанного кандидата действия.",
    ),
    (
        "Confirm and refresh table/partition row-count stats for the referenced physical tables first; add column stats only if the plan still shows weak estimates after that.",
        "Сначала подтвердите и обновите table/partition row-count stats для задействованных физических таблиц; добавляйте column stats только если после этого план все еще показывает слабые estimates.",
    ),
    (
        "Confirm and refresh table/partition row-count stats for the referenced physical tables first",
        "Сначала подтвердите и обновите table/partition row-count stats для задействованных физических таблиц",
    ),
    (
        "add column stats only if the plan still shows weak estimates after that.",
        "добавляйте column stats только если после этого план все еще показывает слабые estimates.",
    ),
    (
        "Do not treat this as a SQL rewrite, stats, or admission recommendation until the failed processing step completes successfully.",
        "Не считайте это SQL rewrite, stats или admission рекомендацией, пока сбойный шаг обработки не завершится успешно.",
    ),
    (
        "Use the diagnostics evidence as the review anchor and make only changes that can be confirmed by EXPLAIN and a comparable rerun.",
        "Используйте diagnostics evidence как anchor проверки и делайте только изменения, которые можно подтвердить через EXPLAIN и сопоставимый повторный запуск.",
    ),
    (
        "No Medium/High rewrite or stats candidate was selected, but deterministic scoring still marked this case as",
        "Кандидат Medium/High для rewrite или stats не выбран, но детерминированная оценка пометила этот кейс как",
    ),
    (
        "The row is marked failed by selected-case status, not by a root-cause diagnosis.",
        "Строка помечена failed по статусу выбранного кейса, а не по root-cause diagnosis.",
    ),
    (
        "Regenerate deterministic facts before relying on report or optimizer actions.",
        "Пересоберите детерминированные факты перед тем, как полагаться на report или optimizer actions.",
    ),
    (
        "Explicit query-specific admission evidence made runtime admission primary bottleneck.",
        "Явный admission evidence по выбранному запросу сделал runtime admission основным bottleneck.",
    ),
    (
        "Runtime admission is the primary follow-up.",
        "Runtime admission является основной проверкой.",
    ),
    (
        "Profile collection command failed before a profile digest was produced.",
        "Команда сбора профиля завершилась ошибкой до создания profile digest.",
    ),
    (
        "Keep changes limited to directions supported by analyzer facts or by a later trusted optimizer outcome.",
        "Ограничивайте изменения направлениями, которые поддержаны analyzer facts или последующим trusted optimizer outcome.",
    ),
    (
        "Fix the failed processing step for this case",
        "Исправьте сбойный шаг обработки для этого кейса",
    ),
    (
        "then rerun or regenerate the selected action",
        "затем повторно запустите или пересоберите выбранное действие",
    ),
    (
        "so this case needs attention before diagnostic conclusions are trusted.",
        "поэтому кейс требует внимания до того, как диагностическим выводам можно доверять.",
    ),
    (
        "then rerun under comparable load and confirm",
        "затем выполните сопоставимый повторный запуск и подтвердите, что",
    ),
    (
        "Rerun under comparable load and confirm",
        "Выполните сопоставимый повторный запуск и подтвердите, что",
    ),
    (
        "and confirm admission wait no longer dominates",
        "и подтвердите, что admission wait больше не доминирует",
    ),
    (
        "and confirm backend row/time spread, runtime metrics, and elapsed time improve without new spill or admission pressure.",
        "и подтвердите, что backend row/time spread, runtime metrics и elapsed time улучшаются без нового spill или admission pressure.",
    ),
    (
        "and confirm scan, storage, or HDFS signals improve before attributing the case to SQL shape.",
        "и подтвердите, что scan, storage или HDFS signals улучшаются, прежде чем относить кейс к SQL shape.",
    ),
    (
        "and confirm the flagged score signals, exchange volume, memory pressure, skew, or runtime metrics improve.",
        "и подтвердите, что отмеченные score signals, exchange volume, memory pressure, skew или runtime metrics улучшаются.",
    ),
    (
        "Confirm and refresh the referenced table/partition row-count gaps through the approved stats-maintenance process first; add column stats only if the plan still shows weak estimates after that.",
        "Сначала подтвердите и обновите gaps по table/partition row-count через утвержденный stats-maintenance process; добавляйте column stats только если после этого план все еще показывает слабые estimates.",
    ),
    (
        "Confirm and refresh the referenced table/partition row-count gaps through the approved stats-maintenance process first",
        "Сначала подтвердите и обновите gaps по table/partition row-count через утвержденный stats-maintenance process",
    ),
    (
        "Confirm and refresh the partition row-count gaps for referenced physical tables, then recheck the plan before collecting broader column stats.",
        "Подтвердите и обновите partition row-count gaps для задействованных физических таблиц, затем перепроверьте план перед более широким сбором column stats.",
    ),
    (
        "Confirm and refresh table/partition row-count stats for the referenced physical tables, then recheck the plan before collecting broader column stats.",
        "Подтвердите и обновите table/partition row-count stats для задействованных физических таблиц, затем перепроверьте план перед более широким сбором column stats.",
    ),
    (
        "Confirm and refresh the join/filter column stats gaps through the approved stats-maintenance process only after table/partition row-count stats are available.",
        "Подтвердите и обновите gaps по join/filter column stats через утвержденный stats-maintenance process только после доступности table/partition row-count stats.",
    ),
    (
        "Confirm and refresh column stats for join and filter columns only after table/partition row-count stats are available.",
        "Подтвердите и обновите column stats для join и filter columns только после доступности table/partition row-count stats.",
    ),
    (
        "Inspect the join, aggregation, filter, and exchange shape behind the score signals.",
        "Проверьте join, aggregation, filter и exchange shape за score signals.",
    ),
    (
        "Prefer reducing rows before joins, exchanges, or memory-heavy operators; keep result columns and join semantics unchanged.",
        "Предпочитайте сокращать строки до joins, exchanges или memory-heavy operators; сохраняйте result columns и join semantics без изменений.",
    ),
    (
        "Prefer reducing rows before joins, exchanges, or memory-heavy operators",
        "Предпочитайте сокращать строки до joins, exchanges или memory-heavy operators",
    ),
    (
        "keep result columns and join semantics unchanged.",
        "сохраняйте result columns и join semantics без изменений.",
    ),
    (
        "Inspect data distribution and hot-key behavior before changing SQL.",
        "Проверьте data distribution и hot-key behavior перед изменением SQL.",
    ),
    (
        "If a query-shape change is attempted, it should reduce skewed rows or improve join distribution.",
        "Если пробуется query-shape изменение, оно должно уменьшать skewed rows или улучшать join distribution.",
    ),
    (
        "Inspect exchange and intermediate-volume evidence.",
        "Проверьте exchange и intermediate-volume evidence.",
    ),
    (
        "Prefer pre-filtering or pre-aggregation that reduces rows before data movement while preserving output shape.",
        "Предпочитайте pre-filtering или pre-aggregation, которые уменьшают строки до data movement и сохраняют output shape.",
    ),
    (
        "Inspect storage and scan-footprint evidence first.",
        "Сначала проверьте storage и scan-footprint evidence.",
    ),
    (
        "Separate HDFS or storage pressure from SQL-shape changes before tuning joins or admission settings.",
        "Отделите HDFS или storage pressure от SQL-shape изменений перед настройкой joins или admission settings.",
    ),
    (
        "Split the review into small checks: estimates and query shape first, then runtime skew or data movement if the plan remains suspicious.",
        "Разделите проверку на небольшие шаги: сначала estimates и query shape, затем runtime skew или data movement, если план остается подозрительным.",
    ),
    (
        "No supported change is recommended for this selected case.",
        "Для этого выбранного кейса не рекомендовано поддержанное изменение.",
    ),
    (
        "If the workload still matters operationally, compare it with similar queries or wait for a stronger deterministic signal before choosing a change.",
        "Если workload все еще операционно важен, сравните его с похожими запросами или дождитесь более сильного детерминированного сигнала перед выбором изменения.",
    ),
    (
        "On the next comparable scan or rerun, confirm the case remains low priority and that duration, workload baseline, admission wait, spill, and stats signals do not cross the suspicious thresholds.",
        "На следующем сопоставимом скане или повторном запуске подтвердите, что кейс остается низкоприоритетным, а duration, workload baseline, admission wait, spill и stats signals не пересекают подозрительные пороги.",
    ),
    (
        "Review ",
        "Проверьте ",
    ),
    (
        "; make only row-reduction or shape changes that can be explained by the listed deterministic facts or by a trusted optimizer outcome.",
        "; делайте только row-reduction или shape изменения, которые объясняются перечисленными deterministic facts или trusted optimizer outcome.",
    ),
    (
        "make only row-reduction or shape changes that can be explained by the listed deterministic facts or by a trusted optimizer outcome.",
        "делайте только row-reduction или shape изменения, которые объясняются перечисленными deterministic facts или trusted optimizer outcome.",
    ),
    (
        "Confirm the failed processing step completes successfully",
        "Подтвердите, что сбойный шаг обработки завершился успешно",
    ),
    (
        "Details should then show typed score evidence or a specific supported action candidate.",
        "после этого Details должен показать типизированные score evidence или конкретного поддержанного кандидата действия.",
    ),
    (
        "worth checking before deeper SQL rewrites, because missing or incomplete stats can leave the planner choosing from weak row estimates.",
        "стоит проверить перед более глубокими SQL rewrites, потому что отсутствующие или неполные stats могут оставить planner со слабыми row estimates.",
    ),
    (
        "table/partition stats first, then column stats",
        "сначала table/partition stats, затем column stats",
    ),
    ("join order and pre-aggregation", "join order и pre-aggregation"),
    (
        "Deterministic analysis found positive score from detailed analyzer reasons.",
        "Детерминированный анализ нашел положительную оценку из детальных analyzer reasons.",
    ),
    (
        "Explicit query-specific admission evidence made runtime admission the primary bottleneck.",
        "Явный admission evidence по выбранному запросу сделал runtime admission основным bottleneck.",
    ),
    ("admission wait came from query context", "admission wait пришел из query context"),
    (
        "estimate mismatch with missing stats evidence",
        "estimate mismatch с недостающими stats evidence",
    ),
    ("join and exchange shape evidence", "evidence формы join/exchange"),
    ("Suspicious.", "Средний."),
    (
        "positive score from detailed analyzer reasons",
        "положительная оценка по детальным причинам анализа",
    ),
    (
        "then rerun under comparable load",
        "затем выполните сопоставимый повторный запуск",
    ),
    (
        "stats signals, and runtime behavior",
        "stats signals и runtime behavior",
    ),
    (
        "estimates, exchange, memory, spill, or runtime behavior",
        "estimates, exchange, memory, spill или runtime behavior",
    ),
    ("stats, or runtime settings", "stats или runtime settings"),
    ("high.", "высокая."),
    ("Unknown.", "неизвестная."),
    ("Action outcomes recorded:", "Записанные результаты действий:"),
    ("Additional supported actions", "Дополнительные поддержанные действия"),
    ("Admission wait came from query context", "Admission wait пришел из query context"),
    (
        "Admission wait came from profile resource facts",
        "Admission wait пришел из profile resource facts",
    ),
    (
        "Admission wait came from profile timing facts",
        "Admission wait пришел из profile timing facts",
    ),
    ("Admission wait dominates runtime", "Admission wait доминирует в runtime"),
    ("Analyzed ", "Проанализировано "),
    ("Analyzed cases", "Проанализированные кейсы"),
    ("Analyzed queries", "Проанализированные запросы"),
    (
        "Batch score does not contain a suspicious analyzer signal for this case.",
        "Batch score не содержит подозрительного analyzer-сигнала для этого кейса.",
    ),
    ("Candidate strength:", "Сила кандидата:"),
    (
        "Check pool saturation and admission wait around the case window before changing SQL.",
        "Проверьте насыщение pool и admission wait вокруг окна кейса перед изменением SQL.",
    ),
    (
        "Collection, deterministic analysis, metadata, or report processing did not complete cleanly",
        "Сбор, детерминированный анализ, метаданные или отчет не завершились корректно",
    ),
    (
        "Compare EXPLAIN before and after stats collection",
        "Сравните EXPLAIN до и после сбора stats",
    ),
    ("Compare EXPLAIN before and after the change", "Сравните EXPLAIN до и после изменения"),
    (
        "Compare EXPLAIN before and after the change, then rerun under comparable load and confirm estimates, exchange, memory, spill, or runtime behavior before accepting the change.",
        "Сравните EXPLAIN до и после изменения, затем выполните сопоставимый повторный запуск и подтвердите estimates, exchange, memory, spill или runtime behavior перед принятием изменения.",
    ),
    (
        "Compare the CTE dependency path, output columns, and rows around the candidate layer before and after one simplification; keep output shape stable and rerun the repeated group.",
        "Сравните CTE dependency path, output columns и rows вокруг candidate layer до и после одного simplification; сохраните стабильный output shape и повторите repeated group.",
    ),
    (
        "Compare the CTE dependency path, output columns, and rows around the candidate layer before and after one simplification",
        "Сравните CTE dependency path, output columns и rows вокруг candidate layer до и после одного simplification",
    ),
    (
        "Compare downstream filter placement, CTE output-column mapping, and rows around the CTE boundary before and after one bounded filter-placement change; then rerun the repeated group.",
        "Сравните downstream filter placement, CTE output-column mapping и rows вокруг CTE boundary до и после одного bounded filter-placement изменения; затем повторите repeated group.",
    ),
    (
        "Compare downstream filter placement, CTE output-column mapping, and rows around the CTE boundary before and after one bounded filter-placement change",
        "Сравните downstream filter placement, CTE output-column mapping и rows вокруг CTE boundary до и после одного bounded filter-placement изменения",
    ),
    (
        "Compare CTE output columns, dependency path, filter scope, and rows around one boundary before and after a bounded manual change; then rerun the repeated group.",
        "Сравните CTE output columns, dependency path, filter scope и rows вокруг одной boundary до и после bounded manual change; затем повторите repeated group.",
    ),
    (
        "Compare derived-table output shape, row-reduction hypothesis, and rows entering and leaving the boundary before rerunning one bounded manual change.",
        "Сравните derived-table output shape, row-reduction hypothesis и rows entering/leaving boundary перед повторным запуском одного bounded manual change.",
    ),
    (
        "Compare outer-filter mapping through derived output columns and rows around the derived boundary; keep the outer filter in place, then rerun the repeated group.",
        "Сравните outer-filter mapping через derived output columns и rows вокруг derived boundary; оставьте outer filter на месте, затем повторите repeated group.",
    ),
    (
        "Compare outer-filter mapping through derived output columns and rows around the derived boundary",
        "Сравните outer-filter mapping через derived output columns и rows вокруг derived boundary",
    ),
    (
        "Compare rows entering and leaving the nested-query boundary in EXPLAIN before and after one bounded change; keep output shape stable, then rerun the repeated group.",
        "Сравните rows entering/leaving nested-query boundary в EXPLAIN до и после одного bounded change; сохраните output shape стабильным, затем повторите repeated group.",
    ),
    (
        "Compare set-operation branch projection symmetry, branch-local rows, and duplicate semantics before and after one branch-local change; then rerun the repeated group.",
        "Сравните set-operation branch projection symmetry, branch-local rows и duplicate semantics до и после одного branch-local change; затем повторите repeated group.",
    ),
    (
        "Compare set-operation branch projection symmetry, branch-local rows, and duplicate semantics before and after one branch-local change",
        "Сравните set-operation branch projection symmetry, branch-local rows и duplicate semantics до и после одного branch-local change",
    ),
    (
        "Compare UNION ALL branch filter selectivity, projection width, and branch output rows before and after one branch-local change; then rerun the repeated group.",
        "Сравните UNION ALL branch filter selectivity, projection width и branch output rows до и после одного branch-local change; затем повторите repeated group.",
    ),
    (
        "Compare UNION ALL branch filter selectivity, projection width, and branch output rows before and after one branch-local change",
        "Сравните UNION ALL branch filter selectivity, projection width и branch output rows до и после одного branch-local change",
    ),
    ("Confidence:", "Уверенность:"),
    ("Current latency", "Текущая задержка"),
    (
        "Deterministic analysis did not select a Medium/High query-shape, stats, runtime admission, or processing follow-up for this case.",
        "Детерминированный анализ не выбрал для этого кейса Medium/High проверку query shape, stats, runtime admission или processing.",
    ),
    ("Deterministic analysis found", "Детерминированный анализ нашел"),
    ("Draft or review", "Draft или review"),
    ("Evidence:", "Доказательства:"),
    ("Facts:", "Факты:"),
    ("Guardrails:", "Ограничения:"),
    ("High.", "Высокое."),
    ("Impact:", "Влияние:"),
    ("Keep in mind:", "Учтите:"),
    ("Low.", "Низкое."),
    ("Metadata contexts:", "Контексты метаданных:"),
    ("Medium.", "Средняя."),
    ("Need:", "Что проверить:"),
    (
        "No trusted SQL draft will be generated for this case by the current deterministic optimizer",
        "Текущий детерминированный optimizer не будет генерировать trusted SQL draft для этого кейса",
    ),
    (
        "No trusted SQL draft is implied by this recommendation.",
        "Эта рекомендация не подразумевает trusted SQL draft.",
    ),
    (
        "Open Details for the supported next step, verification anchor, and rewrite scope.",
        "Откройте Details, чтобы увидеть поддержанный следующий шаг, точку проверки и rewrite scope.",
    ),
    (
        "Open repeated patterns when one query row is not enough.",
        "Открывайте повторяющиеся patterns, когда одной строки запроса недостаточно.",
    ),
    ("Plan: data movement operator", "Plan: оператор data movement"),
    (
        "Primary bottleneck classification selected the safest review direction from deterministic facts. It is not a root-cause claim.",
        "Классификация primary bottleneck выбрала самое безопасное направление проверки из детерминированных фактов. Это не root-cause claim.",
    ),
    ("Primary signal:", "Основной сигнал:"),
    ("Primary:", "Основной сигнал:"),
    ("Query optimization candidate:", "Кандидат query optimization:"),
    ("Query summaries inspected:", "Проверено query summaries:"),
    ("Rank:", "Ранг:"),
    ("Ranked opportunity", "Ранжированная возможность"),
    ("Reason:", "Причина:"),
    (
        "Recorded whether this recommendation was applied",
        "Записано, применялась ли эта рекомендация",
    ),
    ("Regressed workload:", "Регрессировавший workload:"),
    ("Result rows:", "Строки результата:"),
    ("Review:", "Проверить:"),
    (
        "Review query shape; make only row-reduction or shape changes that can be explained by the listed deterministic facts or by a trusted optimizer outcome.",
        "Проверьте query shape; делайте только row-reduction или shape изменения, которые объясняются перечисленными deterministic facts или trusted optimizer outcome.",
    ),
    (
        "Review one CTE simplification at a time: remove or merge only a proven pass-through or single-use layer and compare output shape.",
        "Проверяйте по одному CTE simplification за раз: удаляйте или объединяйте только доказанный pass-through или single-use layer и сравнивайте output shape.",
    ),
    (
        "Review the CTE filter boundary first: move only filters tied to CTE output columns and preserve projection and dependency shape.",
        "Сначала проверьте CTE filter boundary: переносите только filters, привязанные к CTE output columns, и сохраняйте projection/dependency shape.",
    ),
    (
        "Review the CTE boundary first: keep output columns, dependency path, and filter scope stable while testing one bounded change.",
        "Сначала проверьте CTE boundary: сохраняйте output columns, dependency path и filter scope стабильными при проверке одного bounded change.",
    ),
    (
        "Review the derived-table filter boundary first: move only filters that map through simple derived output columns and keep the outer filter in place.",
        "Сначала проверьте derived-table filter boundary: переносите только filters, которые проходят через simple derived output columns, и оставляйте outer filter на месте.",
    ),
    (
        "Review the derived-table boundary first: keep output shape stable and verify one bounded row-reduction hypothesis at a time.",
        "Сначала проверьте derived-table boundary: сохраняйте output shape стабильным и проверяйте по одной bounded row-reduction hypothesis.",
    ),
    (
        "Review the nested-query boundary first: reduce rows before the nested result is joined, aggregated, or redistributed without changing output shape.",
        "Сначала проверьте nested-query boundary: уменьшайте rows до join, aggregation или redistribution nested result без изменения output shape.",
    ),
    (
        "Review selected cases by their listed query-shape tracks first; do not apply one SQL rewrite pattern across the whole group until each boundary has a bounded manual hypothesis.",
        "Сначала проверьте selected cases по указанным query-shape tracks; не применяйте один SQL rewrite pattern ко всей группе, пока у каждой boundary нет bounded manual hypothesis.",
    ),
    (
        "Review set-operation branches first: keep branch columns and semantics stable while checking branch-local filters, pre-aggregation, or projection pruning.",
        "Сначала проверьте set-operation branches: сохраняйте branch columns и semantics стабильными, пока проверяете branch-local filters, pre-aggregation или projection pruning.",
    ),
    ("Rewrite support:", "Поддержка rewrite:"),
    ("Rewriteability:", "Rewriteability:"),
    ("Rerun under comparable load", "Повторите запуск под сопоставимой нагрузкой"),
    ("Score:", "Оценка:"),
    ("Scanned summaries", "Проверенные summaries"),
    ("Scanned", "Проверено"),
    ("Similar queries", "Похожие запросы"),
    ("Speed benefit:", "Потенциал ускорения:"),
    ("Stats candidate:", "Кандидат stats:"),
    ("Stats context limited by metadata coverage", "Stats context ограничен metadata coverage"),
    ("Status:", "Статус:"),
    (
        "SQL draft is disabled by guardrails; use manual optimizer guidance.",
        "SQL draft отключен guardrails; используйте ручные optimizer-рекомендации.",
    ),
    ("SQL: join/filter placement", "SQL: размещение join/filter"),
    ("Structured metadata detail:", "Структурные детали метаданных:"),
    (
        "That makes this query worth a focused rewrite review, not a proven root-cause claim.",
        "Поэтому запрос стоит точечно проверить на rewrite; это не доказанная root cause.",
    ),
    ("That makes", "Поэтому"),
    ("then rerun the repeated group.", "затем повторите repeated group."),
    (
        "This is a deterministic runtime signal, not proof of the slow query cause.",
        "Это детерминированный runtime-сигнал, а не доказательство причины медленного запроса.",
    ),
    (
        "This is a planning/estimate signal, but not a root-cause claim.",
        "Это planning/estimate сигнал, но не root-cause claim.",
    ),
    (
        "This is a review direction, not a root-cause claim.",
        "Это направление проверки, а не root-cause claim.",
    ),
    (
        "This is a strong estimate-quality signal, but not a root-cause claim.",
        "Это сильный сигнал качества estimates, но не root-cause claim.",
    ),
    (
        "keep output shape stable and rerun the repeated group.",
        "сохраните стабильный output shape и повторите repeated group.",
    ),
    (
        "keep the outer filter in place, then rerun the repeated group.",
        "оставьте outer filter на месте, затем повторите repeated group.",
    ),
    (
        "keep output shape stable, then rerun the repeated group.",
        "сохраните output shape стабильным, затем повторите repeated group.",
    ),
    (
        "Use the flagged estimate-mismatch plan operator as the before/after comparison point.",
        "Используйте отмеченный plan operator с estimate mismatch как точку сравнения до/после.",
    ),
    (
        "Use the flagged estimate-mismatch operator as the plan comparison anchor; after the change, check whether fewer rows or better estimates feed that operator.",
        "Используйте отмеченный estimate-mismatch operator как plan comparison anchor; после изменения проверьте, подаются ли в этот operator меньше строк или лучшие estimates.",
    ),
    (
        "Use the flagged estimate-mismatch operator as the plan comparison anchor",
        "Используйте отмеченный estimate-mismatch operator как plan comparison anchor",
    ),
    (
        "after the change, check whether fewer rows or better estimates feed that operator.",
        "после изменения проверьте, подаются ли в этот operator меньше строк или лучшие estimates.",
    ),
    (
        "Use the marked memory-pressure operator as the plan comparison anchor; prefer reducing rows before it over runtime tuning.",
        "Используйте отмеченный memory-pressure operator как plan comparison anchor; предпочитайте уменьшение количества строк перед ним вместо runtime tuning.",
    ),
    (
        "Use the marked data-movement operator as the plan comparison anchor; prefer reducing unnecessary rows before exchange or large intermediate movement.",
        "Используйте отмеченный оператор data movement как plan anchor для сравнения; предпочитайте сокращать лишние строки до exchange или большого intermediate movement.",
    ),
    (
        "Use the marked data-movement operator as the plan comparison anchor",
        "Используйте отмеченный оператор data movement как plan anchor для сравнения",
    ),
    (
        "Start with the data-movement plan operator and look for a query-shape change that reduces rows before exchange.",
        "Начните с plan operator data movement и проверьте query-shape изменение, которое уменьшает строки до exchange.",
    ),
    (
        "Try to reduce rows earlier: move the final SELECT filter closer to the data-producing CTE or subquery only when the result columns, join semantics, and filters stay the same.",
        "Попробуйте уменьшить количество строк раньше: перенесите final SELECT filter ближе к data-producing CTE или subquery только если result columns, join semantics и filters остаются теми же.",
    ),
    (
        "Try to reduce rows inside the relevant CTE path: move only the downstream filters that belong to the CTE output, and keep projection plus dependency shape intact.",
        "Попробуйте уменьшить количество строк внутри relevant CTE path: переносите только downstream filters, относящиеся к CTE output, и сохраняйте projection/dependency shape.",
    ),
    (
        "Try to reduce rows inside the derived table: move the outer filter inward only if it does not cross projection, aggregate, set-operation, or join boundaries.",
        "Попробуйте уменьшить количество строк внутри derived table: переносите outer filter внутрь только если он не пересекает projection, aggregate, set-operation или join boundaries.",
    ),
    (
        "Try to reduce rows per UNION branch: apply equivalent branch-local filters or pre-aggregation while preserving every branch output column.",
        "Попробуйте уменьшить количество строк по каждой UNION branch: применяйте эквивалентные branch-local filters или pre-aggregation, сохраняя каждый branch output column.",
    ),
    (
        "Try to simplify the CTE path only where facts support it: review filter placement and pass-through layers, but avoid CTE inlining or reordering without trusted validation.",
        "Пробуйте упростить CTE path только там, где это поддержано facts: проверьте filter placement и pass-through layers, но избегайте CTE inlining или reordering без trusted validation.",
    ),
    (
        "Use the listed metadata or plan location as the safe anchor for the stats check.",
        "Используйте указанную metadata или plan-точку как безопасный anchor для проверки stats.",
    ),
    (
        "Use the listed review location as the safe anchor for the first manual check.",
        "Используйте указанное место проверки как безопасный anchor для первой ручной проверки.",
    ),
    ("Why:", "Почему:"),
    ("Workload group fingerprint", "Fingerprint workload group"),
    ("Workload history:", "История workload:"),
    ("Workload impact", "Влияние workload"),
    ("Workload p95", "Workload p95"),
    ("Worth checking", "Стоит проверить"),
    (" appended ", " добавлено "),
    ("appended ", "добавлено "),
    ("admission wait", "admission wait"),
    ("before accepting the change", "перед принятием изменения"),
    ("before changing SQL", "перед изменением SQL"),
    ("cardinality estimate anomalies", "аномалии оценки строк"),
    ("cardinality anomalies", "аномалии cardinality"),
    ("column stats completeness", "полнота column stats"),
    ("compare EXPLAIN estimates", "сравните EXPLAIN estimates"),
    ("current scan impact about", "влияние в текущем скане около"),
    ("deterministic attention score", "детерминированная attention score"),
    (" enabled", " включена"),
    (
        "exchange or intermediate data movement is the top finding",
        "exchange или intermediate data movement является главным сигналом",
    ),
    ("explicit admission wait was observed", "обнаружен явный admission wait"),
    ("backend data skew detected", "обнаружен backend data skew"),
    (
        "exchange-heavy runtime context needs review",
        "exchange-heavy runtime context требует проверки",
    ),
    (" cases", " кейсов"),
    (" rows", " строк"),
    ("history samples", "history samples"),
    ("host-tail candidates", "host-tail candidates"),
    (
        "join/filter preservation must be checked before rewrite",
        "сохранение join/filter нужно проверить перед rewrite",
    ),
    (
        "large exchange volume before downstream processing",
        "большой exchange volume перед downstream processing",
    ),
    (" loaded ", " загружено "),
    ("loaded ", "загружено "),
    ("memory estimate anomalies", "аномалии оценки памяти"),
    ("metadata collection failed", "сбор метаданных завершился ошибкой"),
    ("no query-shape opportunity evidence", "нет evidence для query-shape opportunity"),
    ("processing failure category recorded", "записана категория ошибки обработки"),
    (
        "prefer reducing unnecessary rows before exchange or large intermediate movement.",
        "предпочитайте сокращать лишние строки до exchange или большого intermediate movement.",
    ),
    ("query-shape evidence", "сигналы формы запроса"),
    ("query shape is worth review", "форму запроса стоит проверить"),
    ("runtime behavior", "runtime behavior"),
    ("similar queries", "похожих запросов"),
    ("spill/scratch evidence", "spill/scratch evidence"),
    ("stats and estimate-mismatch evidence", "сигналы stats и estimate mismatch"),
    ("stats-planning evidence", "сигналы stats/planning"),
    ("table stats available", "table stats доступны"),
    ("table stats not checked", "table stats не проверены"),
    ("table stats not applicable", "table stats не применимы"),
    ("table stats", "table stats"),
    ("the primary bottleneck", "primary bottleneck"),
    ("trusted", "trusted"),
    ("zero/unknown memory estimate gaps", "пробелы zero/unknown memory estimates"),
    ("zero/unknown row estimate gaps", "пробелы zero/unknown row estimates"),
    ("workload p95", "workload p95"),
)


_CONFIDENCE_RE = re.compile(
    r"^(?P<label>.+) \((?P<confidence>High|Medium|Low|Unknown) confidence\)$"
)
_SEVERITY_SCORE_RE = re.compile(
    r"^(?P<label>Failed|High priority|Medium priority|Clean|Unknown priority) · (?P<score>.+)$"
)
_FOLLOWUP_SCORE_RE = re.compile(
    r"^(?P<label>Multiple follow-ups|Query-shape follow-up|Stats follow-up) · score (?P<score>.+)$"
)
_COUNT_REPLACEMENTS = (
    (re.compile(r"^(\d+) cardinality anomalies$"), r"\1 аномалий cardinality"),
    (re.compile(r"^(\d+) tail candidates$"), r"\1 tail-кандидатов"),
    (re.compile(r"^(\d+) spill/scratch counters$"), r"\1 spill/scratch counters"),
    (re.compile(r"^admission wait share (\d+)%$"), r"доля admission wait \1%"),
    (re.compile(r"^client fetch wait share (\d+)%$"), r"доля client fetch wait \1%"),
    (re.compile(r"^cardinality (\d+)$"), r"cardinality \1"),
    (re.compile(r"^memory (\d+)$"), r"memory \1"),
    (re.compile(r"^host-tail (\d+)$"), r"host-tail \1"),
)


def localize_diagnostic_text(value: Any, language: object = "en") -> str:
    text = "" if value is None else str(value)
    if normalize_ui_language(language) != "ru" or not text:
        return text
    return _localize_ru(text)


def localize_candidate_label(value: Any, language: object = "en") -> str:
    text = str(value or "not_likely").replace("_", " ").strip()
    label = text.title() if text else "Unknown"
    return localize_diagnostic_text(label, language)


def localize_table_header(value: str, language: object = "en") -> str:
    return localize_diagnostic_text(value, language)


def localize_stats_need(value: Any, language: object = "en") -> str:
    labels = {
        "table_stats": "table/partition stats",
        "column_stats": "column stats",
        "table_and_column_stats": "table/partition stats first, then column stats",
        "stats_possibly_stale": "stats freshness unknown",
        "insufficient_metadata": "insufficient metadata",
        "not_likely_stats_issue": "not likely a stats issue",
    }
    return localize_diagnostic_text(labels.get(str(value), str(value)), language)


def _localize_ru(text: str) -> str:
    stripped = text.strip()
    if stripped in _EXACT_RU:
        return _restore_outer_space(text, _EXACT_RU[stripped])

    confidence_match = _CONFIDENCE_RE.fullmatch(stripped)
    if confidence_match:
        label = _localize_ru(confidence_match.group("label"))
        confidence = {
            "High": "высокая",
            "Medium": "средняя",
            "Low": "низкая",
            "Unknown": "неизвестная",
        }.get(confidence_match.group("confidence"), "неизвестная")
        return _restore_outer_space(text, f"{label} ({confidence} уверенность)")

    severity_match = _SEVERITY_SCORE_RE.fullmatch(stripped)
    if severity_match:
        label = _localize_ru(severity_match.group("label"))
        return _restore_outer_space(text, f"{label} · {severity_match.group('score')}")

    followup_match = _FOLLOWUP_SCORE_RE.fullmatch(stripped)
    if followup_match:
        label = _localize_ru(followup_match.group("label"))
        return _restore_outer_space(text, f"{label} · score {followup_match.group('score')}")

    for pattern, replacement in _COUNT_REPLACEMENTS:
        if pattern.fullmatch(stripped):
            return _restore_outer_space(text, pattern.sub(replacement, stripped))

    if "; " in stripped:
        parts = [_localize_ru(part) for part in stripped.split("; ")]
        return _restore_outer_space(text, "; ".join(parts))

    translated = stripped
    for source, target in sorted(_PHRASE_RU, key=lambda item: len(item[0]), reverse=True):
        translated = translated.replace(source, target)
    if translated in _EXACT_RU:
        translated = _EXACT_RU[translated]
    return _restore_outer_space(text, translated)


def _restore_outer_space(original: str, translated: str) -> str:
    prefix_len = len(original) - len(original.lstrip())
    suffix_len = len(original) - len(original.rstrip())
    prefix = original[:prefix_len]
    suffix = original[len(original) - suffix_len :] if suffix_len else ""
    return f"{prefix}{translated}{suffix}"
