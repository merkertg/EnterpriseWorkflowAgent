"""Sections for stages 01 and 02: launch, checks, money, rules and states."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import states
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    BLUE,
    BLUE_L,
    CELL,
    CELL_M,
    GREEN,
    GREEN_L,
    H2,
    SMALL,
    TEAL,
    TEAL_L,
    W,
    callout,
    cards,
    code_block,
    mono,
    p,
    simple_table,
)
from guide.sources import Facts, definition
from guide.toc import section

_ISSUE_TEXT = {
    "unsupported_currency": (
        "Валюта не разрешена политикой",
        "Currency not allowed by the policy",
    ),
    "invoice_total_mismatch": (
        "Заявленный итог ≠ сумма позиций",
        "Declared total differs from line totals",
    ),
    "missing_purchase_order": ("Заказ не передан", "No purchase order supplied"),
    "purchase_order_reference_mismatch": (
        "Счёт ссылается на другой заказ",
        "Invoice references another PO",
    ),
    "supplier_mismatch": ("Поставщики различаются", "Suppliers differ"),
    "currency_mismatch": (
        "Валюты различаются; суммы не сравниваются",
        "Currencies differ; totals are not compared",
    ),
    "purchase_order_total_exceeded": (
        "Итог счёта больше итога заказа",
        "Invoice total exceeds the PO total",
    ),
}


def add_stage01(story: list[Any], facts: Facts) -> None:
    """Sections 2–3: environment, launch, settings, checks and CI."""
    section(
        story,
        "Окружение и запуск",
        tr(
            "uv ставит точные версии из uv.lock; сервис отвечает на GET /health.",
            "uv installs exact versions from uv.lock; the service answers GET /health.",
        ),
        BLUE,
    )
    versions = facts.versions
    story += [
        p(
            tr(
                f"Проект написан на Python {facts.python}. Менеджер <b>uv</b> создаёт "
                "окружение .venv и устанавливает пакеты строго по uv.lock: "
                f"{facts.lock_packages} пакетов с хешами. Флаг --locked запрещает "
                "незаметно менять lock-файл.",
                f"The project uses Python {facts.python}. The <b>uv</b> manager "
                "creates .venv and installs packages exactly as uv.lock says: "
                f"{facts.lock_packages} packages with hashes. --locked forbids "
                "silent lock-file changes.",
            )
        ),
        Spacer(1, 6),
        code_block(
            "uv sync --locked\n"
            "uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8003\n"
            "curl http://127.0.0.1:8003/health\n"
            '# {"status":"ok","service":"enterprise-workflow-agent"}'
        ),
        p(tr("Маршрут целиком", "The whole route"), H2),
        code_block(definition("app/api/routes/health.py", "health")),
        p(
            tr(
                "/health подтверждает только, что процесс отвечает. Базу, документы "
                "и модель он не проверяет. Деловые адреса этапа 06 требуют "
                "DATABASE_URL и токен.",
                "/health only confirms that the process answers. It does not check "
                "the database, documents or a model. Stage 06 business endpoints "
                "need DATABASE_URL and a token.",
            ),
            SMALL,
        ),
        p(tr("Настройки", "Settings"), H2),
        simple_table(
            [
                tr("Переменная", "Variable"),
                tr("По умолчанию", "Default"),
                tr("Смысл", "Meaning"),
            ],
            [
                [
                    mono("APP_NAME"),
                    mono("enterprise-workflow-agent"),
                    tr("Имя в ответе /health", "Name in the /health answer"),
                ],
                [
                    mono("APP_ENV"),
                    mono("development"),
                    tr(
                        "development, test или production",
                        "development, test or production",
                    ),
                ],
                [
                    mono("APP_DEBUG"),
                    mono("false"),
                    tr(
                        "Подробные ошибки; прав не даёт",
                        "Detailed errors; grants no access",
                    ),
                ],
                [
                    mono("DATABASE_URL"),
                    tr("не задан", "unset"),
                    tr(
                        "postgresql+psycopg://…, скрыт при печати",
                        "postgresql+psycopg://…, hidden when printed",
                    ),
                ],
            ],
            [95, 170, W - 265],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Порядок источников: аргументы Settings(...) → переменные окружения → "
                ".env → значения в коде. Файл .env не коммитится; пример — .env.example.",
                "Source order: Settings(...) arguments → environment variables → "
                ".env → code defaults. .env is never committed; see .env.example.",
            )
        ),
        p(tr("Ключевые версии из uv.lock", "Key versions from uv.lock"), H2),
        simple_table(
            [tr("Пакет", "Package"), tr("Версия", "Version"), tr("Зачем", "Purpose")],
            [
                ["FastAPI", versions["fastapi"], tr("HTTP API", "HTTP API")],
                [
                    "Pydantic",
                    versions["pydantic"],
                    tr("Настройки и схемы ответа", "Settings and response schemas"),
                ],
                [
                    "SQLAlchemy",
                    versions["sqlalchemy"],
                    tr("Таблицы и запросы (этап 03)", "Tables and queries (stage 03)"),
                ],
                [
                    "Alembic",
                    versions["alembic"],
                    tr("Миграции схемы", "Schema migrations"),
                ],
                [
                    "psycopg",
                    versions["psycopg"],
                    tr("Драйвер PostgreSQL", "PostgreSQL driver"),
                ],
                [
                    "pypdf",
                    versions["pypdf"],
                    tr("Текст PDF (этап 04)", "PDF text (stage 04)"),
                ],
                [
                    "ReportLab",
                    versions["reportlab"],
                    tr(
                        "Только сборка этого PDF (группа guide)",
                        "Only builds this PDF (guide group)",
                    ),
                ],
            ],
            [110, 80, W - 190],
            [CELL, CELL_M, CELL],
        ),
    ]

    section(
        story,
        "Проверки и CI",
        tr(
            "Пять локальных проверок; CI повторяет их с настоящим PostgreSQL.",
            "Five local checks; CI repeats them with a real PostgreSQL.",
        ),
        BLUE,
    )
    story += [
        code_block(
            "uv run --locked ruff check .\n"
            "uv run --locked ruff format --check .\n"
            "uv run --locked mypy app tests scripts migrations\n"
            "uv run --locked pytest\n"
            "uv run --locked python scripts/check_docs.py"
        ),
        Spacer(1, 8),
        cards(
            [
                (
                    "Ruff",
                    tr(
                        "Ошибки и единый стиль кода; --check ничего не меняет.",
                        "Lint errors and one code style; --check changes nothing.",
                    ),
                    BLUE,
                    BLUE_L,
                ),
                (
                    "mypy --strict",
                    tr(
                        "Типы во всём коде, включая этот генератор PDF.",
                        "Types everywhere, including this PDF generator.",
                    ),
                    TEAL,
                    TEAL_L,
                ),
                (
                    "pytest",
                    tr(
                        "Поведение: нормальные, граничные и запрещённые случаи.",
                        "Behaviour: normal, boundary and forbidden cases.",
                    ),
                    GREEN,
                    GREEN_L,
                ),
            ]
        ),
        p(tr("Тесты базы данных", "Database tests"), H2),
        p(
            tr(
                "Тесты в tests/persistence помечены postgres и выполняются, только если "
                "в окружении процесса задан TEST_DATABASE_URL. Имя базы обязано "
                "оканчиваться на _test: тесты удаляют и заново создают её схему. "
                "Без адреса они пропускаются — в итоге pytest это видно как skipped.",
                "Tests in tests/persistence are marked postgres and run only when the "
                "process environment sets TEST_DATABASE_URL. The database name must end "
                "in _test because tests drop and recreate its schema. Without the URL "
                "they are skipped, which pytest reports as skipped.",
            )
        ),
        Spacer(1, 6),
        code_block(
            "TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/"
            "enterprise_workflow_test \\\n  uv run --locked pytest"
        ),
        p(tr("GitHub Actions", "GitHub Actions"), H2),
        p(
            tr(
                "Workflow .github/workflows/ci.yml запускает сервис postgres:18.3 "
                "(закреплён по digest), применяет миграции, проверяет alembic check и "
                "выполняет все тесты с REQUIRE_TEST_DATABASE=1: потерянный адрес базы "
                "превращается в ошибку, а не в пропуск.",
                "The .github/workflows/ci.yml workflow starts a postgres:18.3 service "
                "(pinned by digest), applies migrations, runs alembic check and every "
                "test with REQUIRE_TEST_DATABASE=1, so a lost database URL fails "
                "instead of skipping.",
            )
        ),
        Spacer(1, 6),
        callout(
            tr(
                "<b>Настроено ≠ проверено.</b> Наличие файла workflow не доказывает "
                "успешный запуск. Статус удалённого CI записывается отдельно — см. "
                "раздел «Итоги проверки».",
                "<b>Configured is not verified.</b> A workflow file does not prove a "
                "successful run. Remote CI status is recorded separately; see "
                "“Verification results”.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]


def add_stage02(story: list[Any], facts: Facts) -> None:
    """Sections 4–6: exact money, basic rules and workflow states."""
    section(
        story,
        "Деньги и документы",
        tr(
            "Decimal и явная валюта; округление один раз на строку.",
            "Decimal and an explicit currency; rounding once per line.",
        ),
        TEAL,
    )
    story += [
        p(
            tr(
                "Домен в app/domain использует только стандартную библиотеку. Деньги — "
                "это Money(Decimal, Currency): float не принимается, валюта обязательна, "
                "конвертации нет. Поддерживаются USD, EUR и UZS, все с двумя знаками.",
                "The domain in app/domain uses only the standard library. Money is "
                "Money(Decimal, Currency): no floats, a required currency and no "
                "conversion. USD, EUR and UZS are supported, all with two decimals.",
            )
        ),
        Spacer(1, 6),
        code_block(definition("app/domain/money.py", "multiply")),
        Spacer(1, 6),
        callout(
            tr(
                "<b>Порядок округления важен.</b> 0.01 × 0.5 = 0.005 → HALF_UP даёт "
                "0.01 на строку. Две такие строки дают 0.02, хотя округление общей "
                "суммы 0.010 дало бы 0.01. Проект округляет каждую строку, затем "
                "складывает.",
                "<b>Rounding order matters.</b> 0.01 × 0.5 = 0.005 → HALF_UP gives "
                "0.01 per line. Two such lines give 0.02, whereas rounding the overall "
                "0.010 would give 0.01. The project rounds each line, then sums.",
            ),
            TEAL,
            TEAL_L,
        ),
        p(tr("Ограничения входа", "Input bounds"), H2),
        simple_table(
            [tr("Значение", "Value"), tr("Правило", "Rule")],
            [
                [
                    tr("Сумма", "Amount"),
                    tr(
                        "Decimal, конечная, 0 ≤ x ≤ 999 999 999 999.99, ровно в сотых: 1.005 — ошибка",
                        "Decimal, finite, 0 ≤ x ≤ 999,999,999,999.99, exact in cents: 1.005 is an error",
                    ),
                ],
                [
                    tr("Количество", "Quantity"),
                    tr(
                        "Decimal > 0, не больше 999 999.999999, до 6 знаков",
                        "Decimal > 0, at most 999,999.999999, up to 6 decimals",
                    ),
                ],
                [
                    tr("Позиции", "Lines"),
                    tr(
                        "От 1 до 100, SKU не повторяются, валюта как у документа",
                        "1 to 100, unique SKUs, document currency",
                    ),
                ],
                [
                    tr("Заявленный итог", "Declared total"),
                    tr(
                        "Хранится отдельно от рассчитанного, чтобы ошибка была видна",
                        "Stored apart from the calculated one so a mismatch stays visible",
                    ),
                ],
            ],
            [110, W - 110],
        ),
        Spacer(1, 6),
        code_block(
            'Money(Decimal("12.50"), Currency.USD)   # ok\n'
            "Money(12.50, Currency.USD)              # invalid_type: float\n"
            'Money(Decimal("1.005"), Currency.USD)  # invalid_precision'
        ),
    ]

    section(
        story,
        "Базовые правила",
        tr(
            "Детерминированная проверка возвращает коды причин, а не текст.",
            "A deterministic check returns reason codes, not prose.",
        ),
        TEAL,
    )
    story += [
        p(
            tr(
                "evaluate_invoice(invoice, purchase_order, policy) без побочных эффектов "
                "возвращает ValidationResult: точные входы и упорядоченный список "
                "RuleIssue с кодом, полем, ожидаемым и фактическим значением.",
                "evaluate_invoice(invoice, purchase_order, policy) has no side effects "
                "and returns a ValidationResult: the exact inputs plus an ordered list "
                "of RuleIssue values with code, field, expected and actual.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Код", "Code"), tr("Когда появляется", "When it appears")],
            [[mono(code), tr(*_ISSUE_TEXT[code])] for code in facts.issue_codes],
            [215, W - 215],
            [CELL, CELL],
        ),
        Spacer(1, 8),
        callout(
            tr(
                "Пример: позиции дают 30.00, заявлено 31.00, итог заказа 30.00 → сразу "
                "два кода: invoice_total_mismatch и purchase_order_total_exceeded.",
                "Example: lines total 30.00, declared 31.00, PO total 30.00 → two codes "
                "at once: invoice_total_mismatch and purchase_order_total_exceeded.",
            )
        ),
        Spacer(1, 6),
        p(
            tr(
                "Это базовые правила этапа 02. Построчная сверка SKU, количества и цены, "
                "допуски и дубликаты счетов — этап 05: совпадение итогов ещё не "
                "доказывает совпадение позиций.",
                "These are Stage 02 basic rules. Line-by-line SKU, quantity and price "
                "matching, tolerances and duplicate invoices are Stage 05: matching "
                "totals do not prove matching lines.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Состояния процесса",
        tr(
            "Чистая проверка не одобряет: одобрение — отдельное решение человека.",
            "A clean check never approves: approval is a separate human decision.",
        ),
        TEAL,
    )
    story += [
        states(),
        Spacer(1, 8),
        simple_table(
            [tr("Команда", "Command"), tr("Откуда", "From"), tr("Результат", "Result")],
            [
                [
                    mono("check"),
                    tr("любое открытое", "any open state"),
                    tr(
                        "awaiting_approval или needs_review",
                        "awaiting_approval or needs_review",
                    ),
                ],
                [
                    mono("approve"),
                    mono("awaiting_approval"),
                    tr(
                        "approved; нужна свежая чистая проверка и решение",
                        "approved; needs a fresh clean check and a decision",
                    ),
                ],
                [
                    mono("reject"),
                    tr("любое открытое", "any open state"),
                    tr("rejected с причиной", "rejected with a reason"),
                ],
                [
                    mono("revise"),
                    tr("любое открытое", "any open state"),
                    tr(
                        "received; прежняя проверка снята",
                        "received; the previous check is cleared",
                    ),
                ],
            ],
            [70, 125, W - 195],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 8),
        p(
            tr(
                f"Состояния в коде: {', '.join(facts.states)}. Объекты неизменяемы: "
                "команда возвращает новый Workflow, прежний снимок остаётся прежним.",
                f"States in code: {', '.join(facts.states)}. Objects are immutable: a "
                "command returns a new Workflow and the old snapshot stays unchanged.",
            )
        ),
        Spacer(1, 6),
        callout(
            tr(
                "В домене HumanDecision(actor_id, reason) — просто данные. С этапа 06 "
                "сервис заполняет их сотрудником, которого узнал по токену и чьи "
                "роль и подразделение только что проверил.",
                "In the domain HumanDecision(actor_id, reason) is plain data. Since "
                "Stage 06 the service fills it with an employee identified by token "
                "whose role and business unit it has just checked.",
            ),
            GREEN,
            GREEN_L,
        ),
    ]
