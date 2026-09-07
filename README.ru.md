# Query Doctor

Last reviewed: 2026-08-11

Язык: [English](README.md) | Русский

[![Safety CI](https://github.com/alexandrefimov/Query-Doctor/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/alexandrefimov/Query-Doctor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/alexandrefimov/Query-Doctor/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/alexandrefimov/Query-Doctor/actions/workflows/codeql.yml)
[![PyPI](https://img.shields.io/pypi/v/query-doctor.svg?cacheSeconds=300)](https://pypi.org/project/query-doctor/)

Находит запросы Apache Impala, которые стоит разобрать, и даёт
детерминированный ответ, куда смотреть и что менять — так, что SQL и профили
не покидают машину.

Query Doctor ранжирует подозрительные Recent-запросы, собирает ограниченный
контекст профиля, извлекает факты обычными правилами на Python и генерирует
проверенные отчёты. Ни raw SQL, ни текст профиля не попадают в браузер, в
отчёты и ни в один внешний сервис.

```text
Python owns facts. LLM owns wording only.
```

## Попробовать

**[Разобрать профиль прямо в браузере](https://alexandrefimov.github.io/query-doctor/)**
— перетащите экспортированный текстовый профиль Impala и получите разбор.
Ставить ничего не нужно, и никуда ничего не уходит: анализатор выполняется
у вас в браузере через WebAssembly, а страница после загрузки не делает ни
одного запроса.

Локально, без кластера, конфигурации и доступов — только синтетические данные:

```bash
python -m pip install query-doctor
query-doctor-web --public-demo
```

Установка не тянет ни одной сторонней зависимости и занимает несколько секунд.
Демо детерминированное, локальное, read-only и блокирует любые действия записи.

![Synthetic Query Doctor Query Inbox status](docs/assets/demo_search.png)

![Synthetic Query Doctor finished queries results](docs/assets/demo_finished_queries.png)

## Разобрать настоящий запрос

Если можно выгрузить один текстовый профиль Impala из Impala Web UI — это вся
подготовка. Cloudera Manager, Kerberos, metadata, Prometheus и LLM не нужны:

```bash
query-doctor-analyze --profile-text ./your-profile.txt --out cases/cm-corpus
query-doctor-web --corpus-dir cases/cm-corpus
```

Скачанный из Impala Web UI файл с именем вида
`profile_<query-id-high>_<query-id-low>` подходит как есть. В локальной и
приватной веб-сессии профиль можно ещё и загрузить со страницы Query Inbox.

Четыре входа, по уровню доступа:

| Вход | Когда подходит |
| --- | --- |
| Один выгруженный профиль | Профиль достать можно, живой доступ пока не дадут. |
| Синтетическое демо | Хочется покликать read-only без реальных данных. |
| Минимальный CM-скан | Есть read-only доступ к Cloudera Manager для сервиса Impala. |
| Прямой скан Impala | Из рантайма достижимы debug Web UI координаторов Impala в Kubernetes. |

Полная настройка, опции и разбор ошибок для каждого входа:
[docs/first-path.md](docs/first-path.md).

## Установка

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install query-doctor
query-doctor-self-test
```

`query-doctor-self-test` — проверка установленного пакета. Прогоняет
консольные скрипты, анализ одного профиля, локальный рендер веб-интерфейса,
детерминированные отчёты и corpus smoke на синтетических данных, не обращаясь
к Cloudera Manager, impalad, Spark, Trino, Prometheus, Ollama и внешним
LLM-сервисам.

Локальная JSON-конфигурация описана в
[docs/i18n/ru/configuration.md](docs/i18n/ru/configuration.md). Рекомендуемый
путь на рабочей станции — `~/.qdcreds/query-doctor-config.json`, секреты в
переменных окружения или локальных env-файлах. Для Cloudera Manager начинайте с
`query-doctor-config.minimal.example.json`; в
`query-doctor-config.example.json` лежат продвинутые поля для прямого доступа к
Impala, Prometheus, metadata и LLM.

Для разработки из чекаута:

```bash
python -m pip install -e ".[dev]"
pre-commit install
```

## Основные команды

| Команда | Что делает |
| --- | --- |
| `query-doctor-web` | Локальный веб-интерфейс: Recent scan, Running now, один известный Query ID, Details, явные действия для отчётов и оптимизатора |
| `query-doctor-analyze` | Детерминированный анализ одного выгруженного профиля или собранных файлов кейса |
| `query-doctor-batch-recent` | Headless-скан Recent с ограничениями и ранжированием |
| `query-doctor-report` | Генерация проверенного отчёта из фактов, которыми владеет Python |
| `query-doctor-optimize-query` | Read-only разбор вставленного SQL |
| `query-doctor-self-test` | Проверка установленного пакета на синтетических данных |

Каждый упакованный консольный скрипт принимает `--help`. Из неустановленного
чекаута — `python -m query_doctor.cli.<command_module>`.

## Контейнер и Kubernetes

```bash
docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/alexandrefimov/query-doctor:0.11.0
```

Образ по умолчанию поднимает безопасное синтетическое публичное демо. Работает
на Python 3.13 и несёт клиентские инструменты Kerberos, нужные при настроенном
сборе metadata. Драйвер HiveServer2 добавляется сборкой с
`QUERY_DOCTOR_INSTALL_EXTRAS=impala`.

Kubernetes-манифесты, пробы, базовые ресурсы, хранение истории Recent и Helm
chart описаны в [deploy/kubernetes/README.md](deploy/kubernetes/README.md),
[deploy/helm/query-doctor/README.md](deploy/helm/query-doctor/README.md) и
[docs/recent-history-store.md](docs/recent-history-store.md). Общие развёртывания
требуют доверенного ingress/auth-прокси; Kubernetes не добавляет внутрь Query
Doctor ни аутентификацию, ни RBAC, ни сессии, ни изоляцию арендаторов.

## Безопасность

- Детерминированный анализ на Python — единственный доверенный источник
  диагностических фактов. Вывод LLM недоверенный, пока не нормализован,
  не санитизирован и не провалидирован.
- Доверенные поверхности браузера и отчётов никогда не показывают raw SQL,
  raw-профили, raw metadata, локальные пути, секреты, вывод подпроцессов и
  имена артефактов. Изолированный owner-only просмотр исходника — единственное
  узкое исключение под явным гейтом.
- Внешний сбор всегда явный, ограниченный, read-only и с редактированием по
  умолчанию. Query Doctor не выполняет ни пользовательский SQL, ни черновики
  оптимизатора.

`privacy_mode` по умолчанию `true`; `no_llm=true` оставляет отчёты и
оптимизатор на детерминированных фактах Python. Полный контракт:
[docs/i18n/ru/safety-contract.md](docs/i18n/ru/safety-contract.md). Обзор для
ревьюера: [docs/i18n/ru/security-model.md](docs/i18n/ru/security-model.md).

## Границы поддержки

Apache Impala — полноценный production-движок для triage. У Trino есть
bounded local production support для retained-list Recent, одного Query ID,
raw-free Details, детерминированных отчётов и подсказок оптимизатора. У Spark
только compact-поверхности, это не поддержка движка в production.

Полный контракт по каждой поверхности, включая то, что сознательно вне
области, — в [docs/support-boundary.md](docs/support-boundary.md).

Query Doctor поддерживается как однопользовательский локальный инструмент. Не
разворачивайте обычный локальный режим как общий сервис без отдельного
проектирования, описанного в том же документе.

## Документация

Начинайте с [docs/i18n/ru/README.md](docs/i18n/ru/README.md). Дальше по
пользе: [docs/first-path.md](docs/first-path.md),
[docs/i18n/ru/demo-mode.md](docs/i18n/ru/demo-mode.md),
[docs/i18n/ru/configuration.md](docs/i18n/ru/configuration.md),
[docs/i18n/ru/credentials.md](docs/i18n/ru/credentials.md),
[docs/i18n/ru/roadmap.md](docs/i18n/ru/roadmap.md).

Канонический язык документации — английский. Русский слой ограничен этим файлом
и практическими инструкциями для пользователей и операторов в
[docs/i18n/ru/](docs/i18n/ru/).

## Разработка

Для обычных изменений прогоняйте точечные тесты по затронутой области и всегда
`git diff --check`. Выбрать проверки помогают
[docs/agent-quickstart.md](docs/agent-quickstart.md) и
[docs/test-matrix.md](docs/test-matrix.md). Перед релизом или публикацией:

```bash
pre-commit run --all-files
scripts/local_gate.sh
query-doctor-demo-preflight --public-release
```

Добавляйте в коммит только явные файлы. Не коммитьте сгенерированные кейсы,
отчёты, локальные конфиги, учётные данные, raw-профили, raw metadata и
временные выводы. См. [CONTRIBUTING.md](CONTRIBUTING.md).

## Лицензия и статус

Apache-2.0, см. [LICENSE](LICENSE). Публичные релизы исходного кода начинаются
с `v0.4.2`; `v0.11.0` продолжает эту линию. Публикация на PyPI идёт через
GitHub OIDC Trusted Publishing с окружениями, требующими подтверждения
мейнтейнера, без хранимых API-токенов. Образы веб-контейнера публикуются в
GitHub Container Registry как
`ghcr.io/alexandrefimov/query-doctor:<version>` из GitHub Releases.

Apache, Apache Impala и Impala — товарные знаки The Apache Software Foundation.
Query Doctor — независимый проект, не одобренный The Apache Software Foundation
или проектом Apache Impala.
