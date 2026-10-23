"""Assemble the whole guide: overview, stage sections and closing sections."""

from typing import Any

from reportlab.platypus import NextPageTemplate, PageBreak, Spacer

from guide.diagrams import STAGES_DONE, roadmap
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    BLUE,
    BLUE_L,
    CELL,
    GREEN,
    GREEN_L,
    H2,
    INK,
    QUESTION,
    SMALL,
    TEAL,
    TEAL_L,
    VIOLET,
    VIOLET_L,
    W,
    callout,
    cards,
    mono,
    p,
    simple_table,
)
from guide.sources import Facts
from guide.stage01_02 import add_stage01, add_stage02
from guide.stage03 import add_stage03
from guide.stage04 import add_stage04
from guide.stage05 import add_stage05
from guide.stage06 import add_stage06
from guide.stage07 import add_stage07
from guide.stage08 import add_stage08
from guide.toc import section

_REMOTE = {
    "not_verified": ("не подтверждён", "not verified"),
    "passed": ("успешно", "passed"),
    "failed": ("с ошибкой", "failed"),
}


def _overview(story: list[Any]) -> None:
    """Section 1: the scenario, principles and the stage map."""
    section(
        story,
        "Проект за минуту",
        tr(
            "Счёт поставщика сверяется с заказом; решение принимает сотрудник.",
            "A supplier invoice is checked against a purchase order; a person decides.",
        ),
        INK,
    )
    story += [
        p(
            tr(
                "Enterprise Workflow Agent — учебный проект корпоративного процесса. "
                "Счёт поставщика поступает файлом, программа извлекает поля и "
                "запоминает источник каждого значения, проверяет счёт по заказу на "
                "закупку (purchase order, PO) и правилам, показывает расхождения и "
                "передаёт решение сотруднику. Все компании, документы и суммы "
                "вымышлены; платежей и обращений к настоящим ERP нет.",
                "Enterprise Workflow Agent is a learning project for a corporate "
                "process. A supplier invoice arrives as a file; the program extracts "
                "fields and remembers the source of each value, checks the invoice "
                "against a purchase order (PO) and rules, shows discrepancies and "
                "hands the decision to an employee. All companies, documents and "
                "amounts are fictional; there are no payments or real ERP calls.",
            )
        ),
        Spacer(1, 8),
        cards(
            [
                (
                    tr("Код считает", "Code computes"),
                    tr(
                        "Суммы, правила и переходы — обычный детерминированный Python.",
                        "Amounts, rules and transitions are plain deterministic Python.",
                    ),
                    BLUE,
                    BLUE_L,
                ),
                (
                    tr("Модель предлагает", "A model proposes"),
                    tr(
                        "Модель (пока подставная) лишь предлагает поля и черновик; код проверяет.",
                        "A model (a substitute for now) only proposes fields and drafts; code checks.",
                    ),
                    VIOLET,
                    VIOLET_L,
                ),
                (
                    tr("Человек решает", "A person decides"),
                    tr(
                        "Чистая проверка не одобряет: нужна отдельная команда сотрудника.",
                        "A clean check never approves: an employee's command is required.",
                    ),
                    TEAL,
                    TEAL_L,
                ),
            ]
        ),
        p(tr("Дорожная карта", "Roadmap"), H2),
        roadmap(),
        Spacer(1, 6),
        simple_table(
            [tr("Этап", "Stage"), tr("Что уже работает", "What already works")],
            [
                [
                    "01",
                    tr(
                        "FastAPI, /health, настройки, Ruff, mypy, pytest, CI",
                        "FastAPI, /health, settings, Ruff, mypy, pytest, CI",
                    ),
                ],
                [
                    "02",
                    tr(
                        "Деньги Decimal, счёт и заказ, 7 кодов проверки, состояния",
                        "Decimal money, invoice and PO, 7 check codes, states",
                    ),
                ],
                [
                    "03",
                    tr(
                        "PostgreSQL, миграции, версии с хешами, транзакции, аудит, повторы",
                        "PostgreSQL, migrations, hashed versions, transactions, audit, retries",
                    ),
                ],
                [
                    "04",
                    tr(
                        "Текст и PDF, лимиты, поля с цитатами, проверка извлечения",
                        "Text and PDF, limits, fields with citations, extraction assessment",
                    ),
                ],
                [
                    "05",
                    tr(
                        "Сверка строк по правилам с допусками, снимки, устаревание",
                        "Line reconciliation under tolerance rules, snapshots, staleness",
                    ),
                ],
                [
                    "06",
                    tr(
                        "Токены, роли, подразделения, HTTP API, одобрение по свежей сверке, outbox",
                        "Tokens, roles, units, HTTP API, approval on fresh reconciliation, outbox",
                    ),
                ],
                [
                    "07",
                    tr(
                        "Агент с инструментами чтения, лимитами, проверкой черновика и трассировкой",
                        "Agent with read-only tools, limits, draft check and trace",
                    ),
                ],
                [
                    "08",
                    tr(
                        "Страница Streamlit только через HTTP API; повторы не дублируют команды",
                        "Streamlit page over the HTTP API only; repeats never duplicate commands",
                    ),
                ],
            ],
            [50, W - 50],
        ),
        Spacer(1, 6),
        p(
            tr(
                "Подробные руководства: docs/STAGE_01.md – docs/STAGE_"
                f"{STAGES_DONE:02d}.md (русский), короткие инструкции: docs/instructions/ "
                "(английский).",
                "Detailed guides: docs/STAGE_01.md – docs/STAGE_"
                f"{STAGES_DONE:02d}.md (Russian); short instructions: docs/instructions/ "
                "(English).",
            ),
            SMALL,
        ),
    ]


def _cheat_sheet(story: list[Any]) -> None:
    """Closing section: the commands a learner uses most."""
    section(
        story,
        "Шпаргалка команд",
        tr(
            "Все команды запускаются из корня проекта.",
            "Run every command from the project root.",
        ),
        INK,
    )
    rows = [
        (
            "uv sync --locked",
            tr("Установить зависимости из lock", "Install locked dependencies"),
        ),
        (
            "uv run --locked uvicorn app.main:app --port 8003",
            tr("Запустить API", "Start the API"),
        ),
        (
            "uv run --locked python -m app.demo",
            tr("Демонстрация домена без базы", "Domain demo without a database"),
        ),
        (
            "uv run --locked alembic upgrade head",
            tr("Применить миграции к DATABASE_URL", "Apply migrations to DATABASE_URL"),
        ),
        (
            "uv run --locked alembic check",
            tr("Сравнить модели и схему", "Compare models and schema"),
        ),
        (
            "uv run --locked python -m app.persistence_demo",
            tr("Демонстрация этапа 03", "Stage 03 demo"),
        ),
        (
            "... persistence_demo show WORKFLOW_ID",
            tr("Прочитать процесс и аудит", "Read a workflow and its audit"),
        ),
        (
            "... documents_cli write-samples var/samples",
            tr("Записать образцы (без базы)", "Write samples (no database)"),
        ),
        ("... documents_cli ingest FILE", tr("Принять документ", "Ingest a document")),
        (
            "... documents_cli evidence ID FIELD",
            tr("Открыть источник поля", "Open a field's source"),
        ),
        ("... documents_cli demo", tr("Демонстрация этапа 04", "Stage 04 demo")),
        ("... reconciliation_cli demo", tr("Демонстрация этапа 05", "Stage 05 demo")),
        (
            "... reconciliation_cli show SNAPSHOT_ID",
            tr("Отчёт снимка сверки", "Reconciliation snapshot report"),
        ),
        (
            "... users_cli create | token | revoke | list",
            tr("Сотрудники и токены", "Employees and tokens"),
        ),
        ("... decisions_demo", tr("Демонстрация этапа 06", "Stage 06 demo")),
        ("... agent_demo", tr("Агент этапа 07, без сети", "Stage 07 agent, offline")),
        (
            "... ui_demo_setup",
            tr(
                "Сотрудники, токены, образцы для страницы",
                "Employees, tokens, samples for the page",
            ),
        ),
        (
            "uv run --locked --group ui python -m streamlit run ui/app.py",
            tr("Страница этапа 08", "Stage 08 page"),
        ),
        ("uv run --locked ruff check .", tr("Линтер", "Linter")),
        (
            "uv run --locked --group ui mypy app tests scripts migrations ui",
            tr("Строгие типы", "Strict types"),
        ),
        (
            "uv run --locked --group ui pytest",
            tr(
                "Тесты; с TEST_DATABASE_URL — и базы",
                "Tests; with TEST_DATABASE_URL also the database",
            ),
        ),
        (
            "uv run --locked pytest -m postgres",
            tr("Только тесты PostgreSQL", "PostgreSQL tests only"),
        ),
        (
            "uv run --locked python scripts/check_docs.py",
            tr("Ссылки в документации", "Documentation links"),
        ),
        (
            "uv run --locked --group guide python scripts/build_guide.py",
            tr("Пересобрать оба PDF", "Rebuild both PDFs"),
        ),
    ]
    story += [
        simple_table(
            [tr("Команда", "Command"), tr("Зачем", "Purpose")],
            [[mono(command), text] for command, text in rows],
            [W * 0.62, W * 0.38],
            [CELL, CELL],
        ),
        Spacer(1, 6),
        p(
            tr(
                "«...» — сокращение для uv run --locked python -m app.",
                "“...” abbreviates uv run --locked python -m app.",
            ),
            SMALL,
        ),
    ]


def _common_errors(story: list[Any]) -> None:
    """Closing section: symptoms and what to do about them."""
    section(
        story,
        "Типичные ошибки",
        tr(
            "Сначала прочитай код ошибки: он говорит, что делать.",
            "Read the error code first: it says what to do.",
        ),
        INK,
    )
    rows = [
        (
            tr("Деньги не создаются из 12.50", "Money rejects 12.50"),
            tr('Нужен Decimal("12.50"), не float', 'Use Decimal("12.50"), not a float'),
        ),
        (
            tr("Чистая проверка не дала approved", "Clean check did not approve"),
            tr(
                "Так задумано: нужна команда approve",
                "By design: approve is a separate command",
            ),
        ),
        (
            mono("Set DATABASE_URL ..."),
            tr(
                "Задай DATABASE_URL в окружении или .env",
                "Set DATABASE_URL in the environment or .env",
            ),
        ),
        (
            tr("Драйвер postgresql://", "postgresql:// driver"),
            tr("Используй postgresql+psycopg://", "Use postgresql+psycopg://"),
        ),
        (
            tr("Схема не найдена", "Schema is missing"),
            tr("Выполни alembic upgrade head", "Run alembic upgrade head"),
        ),
        (
            tr("Тесты базы skipped", "Database tests skipped"),
            tr("Не задан TEST_DATABASE_URL", "TEST_DATABASE_URL is not set"),
        ),
        (
            mono("...must end with _test"),
            tr(
                "Нужна отдельная база с именем ..._test",
                "Use a separate ..._test database",
            ),
        ),
        (
            mono("version_conflict"),
            tr(
                "Прочитай текущую версию и реши заново",
                "Reload the current version and decide again",
            ),
        ),
        (
            mono("idempotency_key_reused"),
            tr("Новое намерение — новый ключ", "A new intent needs a new key"),
        ),
        (
            mono("stored_data_integrity"),
            tr(
                "Строка изменена в обход приложения",
                "A row was edited behind the application",
            ),
        ),
        (
            mono("rejected: unsupported_type"),
            tr("Нужен UTF-8 текст или PDF", "Use UTF-8 text or a PDF"),
        ),
        (
            mono("rejected: no_text_layer"),
            tr("Скан без текста; OCR нет", "A scan without text; no OCR"),
        ),
        (
            tr("needs_review: invalid_value", "needs_review: invalid_value"),
            tr(
                "Сумма вида 30.00, валюта USD/EUR/UZS",
                "Amount like 30.00, currency USD/EUR/UZS",
            ),
        ),
        (
            mono("no_rule_for_currency"),
            tr(
                "Добавь валюту в новую версию правил",
                "Add the currency in a new rule version",
            ),
        ),
        (
            tr("Снимок сверки устарел", "Reconciliation snapshot is stale"),
            tr(
                "revise на новый заказ, затем check и reconcile",
                "revise to the new order, then check and reconcile",
            ),
        ),
        (
            mono("401 unauthenticated"),
            tr(
                "Нет токена, он отозван или сотрудник отключён",
                "No token, a revoked one, or a deactivated user",
            ),
        ),
        (
            tr("404 для существующего процесса", "404 for an existing workflow"),
            tr("Процесс в чужом подразделении", "The workflow is in another unit"),
        ),
        (
            mono("409 stale_reconciliation"),
            tr(
                "Новая сверка и решение по новому снимку",
                "Reconcile again and decide on the new snapshot",
            ),
        ),
        (
            mono("too_many_refusals"),
            tr(
                "Модель трижды вызвала запрещённое; смотри трассировку",
                "The model called forbidden things three times; read the trace",
            ),
        ),
        (
            tr("next_step ≠ proposed_next_step", "next_step ≠ proposed_next_step"),
            tr(
                "Черновик не подтвердился фактами; см. override_reason",
                "Facts did not support the draft; see override_reason",
            ),
        ),
        (
            mono("ModuleNotFoundError: streamlit"),
            tr("Запускай с --group ui", "Run with --group ui"),
        ),
        (
            mono("api_unreachable"),
            tr(
                "Запусти uvicorn и проверь адрес API",
                "Start uvicorn and check the API URL",
            ),
        ),
        (
            mono("503 database_not_configured"),
            tr(
                "Задай DATABASE_URL и перезапусти API",
                "Set DATABASE_URL and restart the API",
            ),
        ),
        (
            tr("Тест свежести PDF упал", "PDF freshness test failed"),
            tr(
                "Пересобери оба PDF и проверь страницы",
                "Rebuild both PDFs and inspect the pages",
            ),
        ),
    ]
    story += [
        simple_table(
            [tr("Симптом", "Symptom"), tr("Что сделать", "What to do")],
            [[symptom, action] for symptom, action in rows],
            [W * 0.42, W * 0.58],
            [CELL, CELL],
        )
    ]


def _questions(story: list[Any]) -> None:
    """Closing section: questions to answer after reading the code."""
    section(
        story,
        "Проверь себя",
        tr(
            "Ответь своими словами, затем найди ответ в коде.",
            "Answer in your own words, then find it in the code.",
        ),
        INK,
    )
    questions = [
        tr(
            'Почему Decimal("0.1"), а не Decimal(0.1)?',
            'Why Decimal("0.1") rather than Decimal(0.1)?',
        ),
        tr(
            "Почему заявленный итог хранится отдельно от рассчитанного?",
            "Why is the declared total stored apart from the calculated one?",
        ),
        tr(
            "Почему чистая проверка ведёт к awaiting_approval, а не к approved?",
            "Why does a clean check lead to awaiting_approval, not approved?",
        ),
        tr(
            "Зачем проверять версию дважды: после чтения и в UPDATE ... WHERE?",
            "Why check the version twice: after reading and in UPDATE ... WHERE?",
        ),
        tr(
            "Почему ключ идемпотентности записывается до изменения процесса?",
            "Why is the idempotency key written before the workflow changes?",
        ),
        tr("Почему A → B → A даёт версию 3?", "Why does A → B → A give version 3?"),
        tr(
            "Что защищает хеш и что защищает триггер?",
            "What does the hash protect, and what does the trigger protect?",
        ),
        tr(
            "Почему формат файла определяется по байтам, а не по имени?",
            "Why is the file format detected from bytes, not the name?",
        ),
        tr(
            "Зачем читать PDF в отдельном процессе с флагом -I?",
            "Why parse PDFs in a separate process with -I?",
        ),
        tr(
            "Как проверка цитат остановит выдуманное значение модели?",
            "How does evidence checking stop a model's invented value?",
        ),
        tr(
            "Почему неполное извлечение не создаёт версию счёта?",
            "Why does an incomplete extraction create no invoice version?",
        ),
        tr(
            "Зачем допуск на итог, если есть допуск на единицу?",
            "Why a total tolerance when there is a unit tolerance?",
        ),
        tr(
            "Почему старый снимок сверки не исправляют, а пересчитывают?",
            "Why is an old reconciliation snapshot recomputed rather than edited?",
        ),
        tr(
            "Почему чужое подразделение даёт 404, а нехватка роли — 403?",
            "Why does another unit give 404 but a missing role 403?",
        ),
        tr(
            "Зачем одобрению блокировки, если свежесть снимка уже проверена?",
            "Why does approval need locks when freshness is already checked?",
        ),
        tr(
            "Почему событие outbox нельзя отправить прямо из команды?",
            "Why can't a command send the outbox event itself?",
        ),
        tr(
            "Почему у агента нет инструмента одобрения?",
            "Why does the agent have no approval tool?",
        ),
        tr(
            "Почему проверка черновика может только понизить шаг?",
            "Why can the draft check only lower the next step?",
        ),
        tr(
            "Почему ключ идемпотентности строится из намерения, а не случайно?",
            "Why is the idempotency key derived from the intent, not random?",
        ),
        tr(
            "Что пока не защищено и на каком этапе это появится?",
            "What is not protected yet, and in which stage will it be?",
        ),
    ]
    for index, question in enumerate(questions, start=1):
        story.append(p(f"{index}. {question}", QUESTION))
        story.append(Spacer(1, 16))


def _results(story: list[Any], verification: dict[str, Any]) -> None:
    """Last section: facts from verification.json, with remote CI kept apart."""
    section(
        story,
        "Итоги проверки",
        tr(
            "Только записанные результаты; удалённый CI отделён от локальных проверок.",
            "Recorded results only; remote CI is kept apart from local checks.",
        ),
        GREEN,
    )
    environment = verification["environment"]
    tests = verification["tests"]
    remote = verification["remote_ci"]
    story += [
        p(
            tr(
                f"Дата проверки: {verification['verified_on']} ({verification['timezone']}). "
                f"Базовый коммит: {verification['git']['base_commit']}. "
                f"Python {environment['python']}, uv {environment['uv']}, "
                f"PostgreSQL {environment['postgresql'].split()[0]}.",
                f"Verified on {verification['verified_on']} ({verification['timezone']}). "
                f"Base commit: {verification['git']['base_commit']}. "
                f"Python {environment['python']}, uv {environment['uv']}, "
                f"PostgreSQL {environment['postgresql'].split()[0]}.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Набор тестов", "Test run"), tr("Результат", "Result")],
            [
                [
                    tr("С TEST_DATABASE_URL", "With TEST_DATABASE_URL"),
                    tr(
                        f"{tests['with_database']['passed']} прошли, {tests['with_database']['failed']} упали, {tests['with_database']['skipped']} пропущены",
                        f"{tests['with_database']['passed']} passed, {tests['with_database']['failed']} failed, {tests['with_database']['skipped']} skipped",
                    ),
                ],
                [
                    tr("Без базы", "Without a database"),
                    tr(
                        f"{tests['without_database']['passed']} прошли, {tests['without_database']['skipped']} пропущены (PostgreSQL)",
                        f"{tests['without_database']['passed']} passed, {tests['without_database']['skipped']} skipped (PostgreSQL)",
                    ),
                ],
                [tr("Всего тестов", "Total tests"), str(tests["total"])],
            ],
            [170, W - 170],
        ),
        p(tr("Команды", "Commands"), H2),
        simple_table(
            [tr("Команда", "Command"), tr("Статус", "Status")],
            [
                [
                    mono(item["command"]),
                    tr("успешно", "passed")
                    if item["status"] == "passed"
                    else tr("ошибка", "failed"),
                ]
                for item in verification["checks"]
            ],
            [W - 80, 80],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 8),
        callout(
            tr(
                f"<b>GitHub Actions: {_REMOTE[remote['status']][0]}.</b> Локальный "
                "успех не заменяет удалённый запуск; статус меняется только после "
                "просмотра запуска для нужного коммита.",
                f"<b>GitHub Actions: {_REMOTE[remote['status']][1]}.</b> Local success "
                "does not replace a remote run; the status changes only after "
                "inspecting the run for the relevant commit.",
            ),
            AMBER,
            AMBER_L,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Не реализовано: адаптер настоящей LLM (07 работает с подставными "
                "провайдерами), лимит памяти PDF-процесса и доставка outbox (09), "
                "Docker-выпуск (10). Срок действия токенов и SSO — вне объёма.",
                "Not implemented: a real LLM adapter (07 runs substitute providers), "
                "a PDF worker memory limit and outbox delivery (09), Docker release "
                "(10). Token expiry and SSO are out of scope.",
            ),
            GREEN,
            GREEN_L,
        ),
    ]


def build_story(facts: Facts, verification: dict[str, Any]) -> list[Any]:
    """Return every flowable: cover placeholder, then all sections in order."""
    story: list[Any] = [NextPageTemplate("content"), PageBreak()]
    _overview(story)
    add_stage01(story, facts)
    add_stage02(story, facts)
    add_stage03(story, facts)
    add_stage04(story, facts)
    add_stage05(story, facts)
    add_stage06(story, facts)
    add_stage07(story, facts)
    add_stage08(story, facts)
    _cheat_sheet(story)
    _common_errors(story)
    _questions(story)
    _results(story, verification)
    return story
