"""Sections for stage 10: images and compose, backups, release and limits."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import backup_cycle, compose_stack
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
    H2,
    RED,
    SMALL,
    TEAL,
    TEAL_L,
    W,
    callout,
    code_block,
    mono,
    p,
    simple_table,
)
from guide.sources import Facts
from guide.toc import section

_SERVICE_TEXT = {
    "postgres": (
        "PostgreSQL 18.3, тот же digest, что в CI; том pgdata",
        "PostgreSQL 18.3, the CI digest; volume pgdata",
    ),
    "migrate": (
        "alembic upgrade head, затем завершается",
        "alembic upgrade head, then exits",
    ),
    "api": (
        "Uvicorn на 8003, проверка здоровья по /health",
        "Uvicorn on 8003, health check on /health",
    ),
    "receiver": (
        "Получатель webhook на 127.0.0.1:8010, том receiver",
        "Webhook receiver on 127.0.0.1:8010, volume receiver",
    ),
    "worker": (
        "Обработчик outbox в сетевом пространстве receiver",
        "Outbox worker in the receiver's network namespace",
    ),
    "ui": (
        "Streamlit на 8501, обращается к http://api:8003",
        "Streamlit on 8501, calling http://api:8003",
    ),
}

_JOB_TEXT = {
    "checks": (
        "Ruff, формат, mypy, миграции, все тесты с PostgreSQL, ссылки",
        "Ruff, format, mypy, migrations, all tests with PostgreSQL, links",
    ),
    "evaluations": (
        "Набор оценок на своей базе; отчёт — артефакт на 14 дней",
        "Evaluation suite on its own database; report kept 14 days",
    ),
    "compose": (
        "Образы, стек, smoke, цикл копия → пустая база → восстановление",
        "Images, stack, smoke, backup → empty database → restore cycle",
    ),
}


def _described(values: tuple[str, ...], texts: dict[str, tuple[str, str]]) -> None:
    """Stop the build if the code gained a value the guide does not explain."""
    if set(values) != set(texts):
        raise ValueError(f"Describe exactly these values: {sorted(values)}")


def add_stage10(story: list[Any], facts: Facts) -> None:
    """Sections 34–36: images and compose, backup and restore, release."""
    _described(facts.compose_services, _SERVICE_TEXT)
    _described(facts.ci_jobs, _JOB_TEXT)
    section(
        story,
        "Образы и compose",
        tr(
            "Тот же код и те же версии поднимаются одной командой.",
            "The same code and versions come up with one command.",
        ),
        RED,
    )
    story += [
        p(
            tr(
                "Dockerfile собирает образ app (API, миграции, обработчик, получатель, "
                "команды) и образ ui (то же плюс Streamlit) из uv.lock. Базовые образы "
                "Python 3.12.15 и uv 0.12.23 закреплены по digest, процессы работают "
                "от пользователя app (uid 10001), в образы не попадают .git, .env, var/ "
                "и тесты.",
                "The Dockerfile builds an app image (API, migrations, worker, receiver, "
                "commands) and a ui image (the same plus Streamlit) from uv.lock. The "
                "Python 3.12.15 and uv 0.12.23 base images are pinned by digest, "
                "processes run as user app (uid 10001), and .git, .env, var/ and tests "
                "never reach an image.",
            )
        ),
        Spacer(1, 6),
        compose_stack(),
        Spacer(1, 6),
        simple_table(
            [tr("Сервис", "Service"), tr("Что делает", "Role")],
            [[mono(name), tr(*_SERVICE_TEXT[name])] for name in facts.compose_services],
            [W * 0.22, W * 0.78],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        code_block(
            'export POSTGRES_PASSWORD="$(openssl rand -hex 24)"\n'
            'export SANDBOX_WEBHOOK_SECRET="$(openssl rand -hex 24)"\n'
            "docker compose up -d --build --wait\n"
            "docker compose run --rm --no-deps api python -m app.smoke --api "
            "http://api:8003\n"
            "docker compose down   # keep the data volumes"
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Почему worker живёт в сетевом пространстве receiver? Этап 09 "
                "разрешает доставку только на 127.0.0.1 или [::1], а у каждого "
                "контейнера свой loopback. Общее пространство сохраняет правило без "
                "исключений. Без POSTGRES_PASSWORD и SANDBOX_WEBHOOK_SECRET compose "
                "не стартует. Для генерации нужен OpenSSL; вместо него можно вручную "
                "задать два независимых случайных секрета от 32 символов. Компоненты "
                "DATABASE_* передаются отдельно: SQLAlchemy экранирует пароль, "
                "сохраняя специальные символы.",
                "Why does the worker live in the receiver's network namespace? Stage 09 "
                "allows delivery only to 127.0.0.1 or [::1], and every container has "
                "its own loopback. A shared namespace keeps the rule without an "
                "exception. Without POSTGRES_PASSWORD and SANDBOX_WEBHOOK_SECRET "
                "compose refuses to start. Generation needs OpenSSL; alternatively "
                "set two independent random secrets of at least 32 characters manually. "
                "Separate DATABASE_* components let SQLAlchemy escape the password "
                "without changing its reserved characters.",
            ),
            TEAL,
            TEAL_L,
        ),
    ]

    section(
        story,
        "Резервная копия и восстановление",
        tr(
            "Копия проверяет себя: восстановление совпадает байт в байт или "
            "откатывается.",
            "A backup checks itself: a restore matches byte for byte or rolls back.",
        ),
        RED,
    )
    story += [
        p(
            tr(
                f"Формат {facts.backup_format}: каталог с файлом JSON Lines на таблицу "
                "и manifest.json (версия, ревизия Alembic, число строк и SHA-256 "
                "каждого файла). Строки упорядочены по первичному ключу, время "
                "приведено к UTC, каталог и файлы закрыты для других пользователей.",
                f"Format {facts.backup_format}: a directory with one JSON Lines file "
                "per table and manifest.json (version, Alembic revision, row count and "
                "SHA-256 of every file). Rows are ordered by primary key, times are in "
                "UTC, and the directory and files are private to the user.",
            )
        ),
        Spacer(1, 6),
        backup_cycle(),
        Spacer(1, 6),
        p(
            tr(
                "DATABASE_URL: исходная база для export, пустая база той же ревизии "
                "для restore; затем verify и check работают с восстановленной базой.",
                "DATABASE_URL: source for export, an empty database at the same "
                "revision for restore; verify and check then use the restored database.",
            ),
            SMALL,
        ),
        code_block(
            "uv run --locked python -m app.backup export var/backups/one\n"
            "uv run --locked python -m app.backup restore var/backups/one\n"
            "uv run --locked python -m app.backup verify var/backups/one\n"
            "uv run --locked python -m app.backup check"
        ),
        Spacer(1, 4),
        simple_table(
            [tr("Команда", "Command"), tr("Что проверяет", "What it checks")],
            [
                [
                    mono("export [DIR]"),
                    tr(
                        "Все таблицы в одном снимке REPEATABLE READ",
                        "Every table from one REPEATABLE READ snapshot",
                    ),
                ],
                [
                    mono("restore DIR"),
                    tr(
                        "Хеши файлов, та же ревизия, пустая база; сверка до COMMIT",
                        "File hashes, same revision, empty database; compare before COMMIT",
                    ),
                ],
                [
                    mono("verify DIR"),
                    tr(
                        "Живая база против копии, по таблицам",
                        "The live database against a backup, per table",
                    ),
                ],
                [
                    mono("check"),
                    tr(
                        "Хеши версий процессов, байты документов, повтор каждой сверки",
                        "Workflow version hashes, document bytes, every reconciliation again",
                    ),
                ],
            ],
            [W * 0.24, W * 0.76],
            [CELL, CELL],
            padding=2.5,
        ),
        p(tr("Цикл внешних ключей", "A foreign-key cycle"), H2),
        p(
            tr(
                "Процессы и снимки ссылаются друг на друга. Для обхода цикла эти "
                "столбцы сначала пустые и заполняются после всех таблиц: "
                + ", ".join(facts.deferred_keys)
                + ".",
                "Workflows and snapshots reference each other. To break the cycle, "
                "these columns stay empty until every table is loaded: "
                + ", ".join(facts.deferred_keys)
                + ".",
            ),
            SMALL,
        ),
        Spacer(1, 4),
        callout(
            tr(
                "Копия PostgreSQL содержит документы и хеши токенов: только var/, "
                "никогда не Git. Манифест не подписан. SQLite получателя не входит: "
                "его том сохраняется отдельно, в том числе в цикле CI. Атомарной "
                "копии двух хранилищ нет.",
                "A PostgreSQL backup holds documents and token hashes: var/ only, "
                "never Git. The manifest is unsigned. Receiver SQLite is excluded: "
                "preserve its volume separately, as the CI cycle does. There is no "
                "atomic backup across both stores.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]

    section(
        story,
        "Выпуск и ограничения",
        tr(
            f"Версия {facts.version}: три вида свидетельств перечислены отдельно.",
            f"Version {facts.version}: three kinds of evidence are kept apart.",
        ),
        RED,
    )
    story += [
        simple_table(
            [tr("Задание CI", "CI job"), tr("Что выполняет", "What it runs")],
            [[mono(name), tr(*_JOB_TEXT[name])] for name in facts.ci_jobs],
            [W * 0.22, W * 0.78],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 4),
        p(
            tr(
                "Секреты задания compose создаются случайно на каждый запуск и "
                "маскируются в журнале; действия GitHub закреплены по SHA.",
                "The compose job's secrets are random per run and masked in the log; "
                "GitHub actions are pinned by SHA.",
            ),
            SMALL,
        ),
        p(tr("Свидетельства", "Evidence"), H2),
        simple_table(
            [tr("Вид", "Kind"), tr("Статус", "Status")],
            [
                [
                    tr("Локальные проверки", "Local checks"),
                    tr(
                        "Выполнены на macOS arm64, записаны в VERIFICATION.md",
                        "Done on macOS arm64, recorded in VERIFICATION.md",
                    ),
                ],
                [
                    tr("Настоящий провайдер модели", "Real model provider"),
                    tr(
                        "Не проверялся: адаптера и ключа нет",
                        "Not checked: no adapter and no key",
                    ),
                ],
                [
                    tr("Удалённый CI", "Remote CI"),
                    tr(
                        "Не подтверждён: запуски не просматривались",
                        "Not verified: runs were not inspected",
                    ),
                ],
            ],
            [W * 0.34, W * 0.66],
            [CELL, CELL],
            padding=2.5,
        ),
        p(tr("Главные ограничения", "Main limitations"), H2),
        simple_table(
            [tr("Область", "Area"), tr("Чего нет", "What is missing")],
            [
                [
                    tr("Модель", "Model"),
                    tr(
                        "Настоящей LLM; оценки — только детерминированные провайдеры",
                        "A real LLM; evaluations use deterministic providers only",
                    ),
                ],
                [
                    tr("Доступ", "Access"),
                    tr(
                        "Срока жизни токенов, SSO, ограничения частоты запросов",
                        "Token expiry, SSO, request rate limits",
                    ),
                ],
                [
                    tr("Документы", "Documents"),
                    tr(
                        "OCR, ручной правки полей; лимита памяти на macOS",
                        "OCR, manual field correction; a memory limit on macOS",
                    ),
                ],
                [
                    tr("Эксплуатация", "Operations"),
                    tr(
                        "TLS, публикации образов, мониторинга, копий по расписанию",
                        "TLS, image publishing, monitoring, scheduled backups",
                    ),
                ],
                [
                    tr("Проверка", "Verification"),
                    tr(
                        "Linux и Windows локально, amd64-образов, удалённого CI",
                        "Linux and Windows locally, amd64 images, remote CI",
                    ),
                ],
            ],
            [W * 0.22, W * 0.78],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Это учебный проект на вымышленных данных. Пройденные тесты, оценки и "
                "smoke не означают готовности к промышленной эксплуатации. Полный "
                "список — docs/LIMITATIONS.md.",
                "This is a learning project on fictional data. Passing tests, "
                "evaluations and the smoke check do not mean production readiness. "
                "The full list is docs/LIMITATIONS.md.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]
