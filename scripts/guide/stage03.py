"""Sections for stage 03: layers, tables, versions, transactions, locking, audit."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import layers, transaction_steps
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
    GREEN,
    GREEN_L,
    H2,
    ORANGE,
    ORANGE_L,
    SMALL,
    VIOLET,
    VIOLET_L,
    W,
    callout,
    code_block,
    mono,
    p,
    simple_table,
)
from guide.sources import Facts, definition
from guide.toc import section

_TABLE_TEXT = {
    "invoice_versions": (
        "Версия счёта: заголовок, NUMERIC-итог",
        "Invoice version: header, NUMERIC total",
    ),
    "invoice_version_lines": ("Позиции версии счёта", "Lines of an invoice version"),
    "purchase_order_versions": ("Версия заказа", "Purchase order version"),
    "purchase_order_version_lines": ("Позиции версии заказа", "Lines of a PO version"),
    "policy_versions": ("Версия политики проверки", "Validation policy version"),
    "workflows": (
        "Текущее состояние и версия записи",
        "Current state and record version",
    ),
    "validation_snapshots": ("Результат каждой проверки", "Result of every check"),
    "audit_events": ("Журнал изменений состояния", "State-change audit log"),
    "idempotency_records": ("Результат по ключу повтора", "Result per idempotency key"),
    "source_documents": (
        "Исходные байты документа (этап 04)",
        "Original document bytes (stage 04)",
    ),
    "document_pages": ("Текст страниц (этап 04)", "Page texts (stage 04)"),
    "document_extractions": (
        "Результат извлечения (этап 04)",
        "Extraction result (stage 04)",
    ),
    "extracted_fields": (
        "Поля с цитатами (этап 04)",
        "Fields with evidence (stage 04)",
    ),
    "reconciliation_rule_versions": (
        "Версия правил сверки (этап 05)",
        "Reconciliation rule version (stage 05)",
    ),
    "reconciliation_currency_rules": (
        "Допуски по валюте (этап 05)",
        "Tolerances per currency (stage 05)",
    ),
    "reconciliation_snapshots": (
        "Снимок сверки (этап 05)",
        "Reconciliation snapshot (stage 05)",
    ),
}


def _ordered_tables(facts: Facts) -> list[str]:
    """List tables by topic; fail the build if a table lacks a description."""
    if set(facts.tables) != set(_TABLE_TEXT):
        raise ValueError("Describe every table in _TABLE_TEXT and nothing else.")
    return list(_TABLE_TEXT)


def add_stage03(story: list[Any], facts: Facts) -> None:
    """Sections 7–12: the persistence design and its guarantees."""
    section(
        story,
        "Слои и порты",
        tr(
            "Сценарии знают о базе только через протоколы; домен не знает о ней вовсе.",
            "Use cases see storage only through protocols; the domain not at all.",
        ),
        ORANGE,
    )
    story += [
        layers(),
        Spacer(1, 8),
        p(
            tr(
                "<b>Порт</b> — протокол, описывающий, что сценарию нужно от хранилища: "
                "WorkflowRepository, DocumentRepository, UnitOfWork. <b>Адаптер</b> — "
                "его реализация для PostgreSQL в app/infrastructure/db. <b>Unit of "
                "Work</b> — одна транзакция: блок with, явный commit(), иначе откат.",
                "A <b>port</b> is a protocol for what a use case needs from storage: "
                "WorkflowRepository, DocumentRepository, UnitOfWork. An <b>adapter</b> "
                "implements it for PostgreSQL in app/infrastructure/db. A <b>unit of "
                "work</b> is one transaction: a with block, an explicit commit(), "
                "otherwise a rollback.",
            )
        ),
        Spacer(1, 6),
        code_block(
            "with self._unit_of_work() as unit:\n"
            "    current = unit.repository.get_workflow(workflow_id)\n"
            "    ...                       # domain computes the change\n"
            "    unit.commit()             # leaving without commit rolls back"
        ),
        Spacer(1, 6),
        callout(
            tr(
                "tests/test_architecture.py разбирает импорты: app/domain и "
                "app/application не должны импортировать FastAPI, Pydantic, SQLAlchemy, "
                "Alembic, psycopg и pypdf. Нарушение границы — красный тест.",
                "tests/test_architecture.py parses imports: app/domain and "
                "app/application must not import FastAPI, Pydantic, SQLAlchemy, "
                "Alembic, psycopg or pypdf. Crossing the boundary fails a test.",
            ),
            ORANGE,
            ORANGE_L,
        ),
    ]

    section(
        story,
        "Таблицы и миграции",
        tr(
            "Схема меняется только миграциями Alembic; история защищена триггером.",
            "The schema changes only through Alembic; history is trigger-protected.",
        ),
        ORANGE,
    )
    append_only = set(facts.append_only)
    story += [
        simple_table(
            [
                tr("Таблица", "Table"),
                tr("Что хранит", "Contents"),
                tr("Изменяется?", "Mutable?"),
            ],
            [
                [
                    mono(name),
                    tr(*_TABLE_TEXT[name]),
                    tr("нет", "no") if name in append_only else tr("да", "yes"),
                ]
                for name in _ordered_tables(facts)
            ],
            [175, W - 245, 70],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 6),
        p(
            tr(
                "Деньги — NUMERIC(14,2), количество — NUMERIC(12,6): драйвер "
                "возвращает Decimal без float. CHECK-ограничения повторяют ключевые "
                "правила домена для ручного SQL.",
                "Money is NUMERIC(14,2) and quantity NUMERIC(12,6): the driver returns "
                "Decimal, never float. CHECK constraints repeat key domain rules for "
                "hand-written SQL.",
            ),
            SMALL,
        ),
        p(tr("Миграции", "Migrations"), H2),
        code_block(
            "\n".join(
                f"{command:<44}# {comment}"
                for command, comment in (
                    (
                        "uv run --locked alembic upgrade head",
                        tr("применить все ревизии", "apply all revisions"),
                    ),
                    (
                        "uv run --locked alembic current",
                        tr("текущая версия схемы", "current schema revision"),
                    ),
                    (
                        "uv run --locked alembic check",
                        tr("модели = схема?", "models equal schema?"),
                    ),
                    (
                        "uv run --locked alembic upgrade head --sql",
                        tr("только показать SQL", "print SQL only"),
                    ),
                )
            )
        ),
        p(
            tr(
                f"Ревизии: {', '.join(facts.migrations)}. Применённую ревизию не "
                "редактируют — изменение схемы оформляется новой. downgrade base "
                "удаляет все таблицы вместе с данными.",
                f"Revisions: {', '.join(facts.migrations)}. An applied revision is "
                "never edited; a schema change is a new revision. downgrade base "
                "drops every table with its data.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Версии и хеши",
        tr(
            "Счёт, заказ и политика хранятся версиями; SHA-256 проверяется при чтении.",
            "Invoices, POs and policies are versions; SHA-256 is re-checked on read.",
        ),
        ORANGE,
    )
    story += [
        p(
            tr(
                "record_purchase_order(order) сохраняет заказ как версию. Совпадает с "
                "последней — возвращается она же; отличается — создаётся следующий "
                "номер. История правок сохраняется полностью:",
                "record_purchase_order(order) stores an order as a version. Identical "
                "to the latest: that version is returned; different: the next number "
                "is created. The edit history is kept in full:",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Запись", "Record"), tr("Результат", "Result")],
            [
                [tr("Содержимое A", "Content A"), tr("версия 1", "version 1")],
                [
                    tr("Снова A", "A again"),
                    tr(
                        "та же версия 1, строк не добавлено",
                        "the same version 1, no new rows",
                    ),
                ],
                [tr("Содержимое B", "Content B"), tr("версия 2", "version 2")],
                [
                    tr("Снова A", "A again"),
                    tr(
                        "версия 3: возврат к прежнему — тоже правка",
                        "version 3: going back is also an edit",
                    ),
                ],
            ],
            [160, W - 160],
        ),
        p(tr("Канонический JSON", "Canonical JSON"), H2),
        p(
            tr(
                "Хеш считается от однозначной записи: ключи отсортированы, пробелов "
                "нет, суммы — строками, есть метка схемы. Одинаковое содержимое — "
                "одинаковый хеш, любое изменение — другой.",
                "The hash covers an unambiguous form: sorted keys, no spaces, amounts "
                "as strings and a schema tag. Equal content gives an equal hash; any "
                "change gives another.",
            )
        ),
        Spacer(1, 6),
        code_block(
            '{"currency":"USD","lines":[{"description":"Fictional item",\n'
            ' "quantity":"2.000000","sku":"SKU-1","unit_price":"12.50"}],\n'
            ' "purchase_order_id":"PO-HASH-1","schema":"purchase_order.v1",\n'
            ' "supplier_id":"SUP-1"}'
        ),
        Spacer(1, 6),
        callout(
            tr(
                "При каждом чтении версия собирается в доменный объект и хеш "
                "вычисляется заново. Строка, изменённая в обход приложения, даёт "
                "stored_data_integrity, а не подменённые данные.",
                "Every read rebuilds the domain object and recomputes the hash. A row "
                "edited behind the application's back raises stored_data_integrity "
                "instead of returning altered data.",
            ),
            VIOLET,
            VIOLET_L,
        ),
    ]

    section(
        story,
        "Команда — одна транзакция",
        tr(
            "start, check, approve, reject и revise пишут всё или ничего.",
            "start, check, approve, reject and revise write everything or nothing.",
        ),
        ORANGE,
    )
    story += [
        transaction_steps(),
        Spacer(1, 8),
        p(
            tr(
                "Процесс закрепляет конкретные версии входов: start и revise принимают "
                "их идентификаторы, check проверяет именно их. Новая версия заказа "
                "сама процесс не меняет — нужен revise.",
                "A workflow pins exact input versions: start and revise take their "
                "IDs, and check evaluates exactly those. A new PO version does not "
                "change the workflow by itself; revise does.",
            )
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Тест test_failure_before_commit_leaves_no_partial_state ломает шаг 7 "
                "и проверяет: в таблицах нет ни ключа, ни снимка, ни новой версии "
                "процесса. Тот же ключ потом успешно выполняет команду.",
                "test_failure_before_commit_leaves_no_partial_state breaks step 7 and "
                "checks that no key, snapshot or new workflow version remains. The "
                "same key then runs the command successfully.",
            ),
            GREEN,
            GREEN_L,
        ),
    ]

    section(
        story,
        "Блокировка и повтор",
        tr(
            "Версия записи защищает от потерянного обновления, ключ — от двойного.",
            "The record version stops lost updates; the key stops double execution.",
        ),
        ORANGE,
    )
    story += [
        p(tr("Оптимистическая блокировка", "Optimistic locking"), H2),
        p(
            tr(
                "Команда принимает expected_version — версию, которую видел вызывающий. "
                "Сервис сравнивает её после чтения, а затем обновляет строку условно:",
                "A command takes expected_version, the version the caller saw. The "
                "service compares it after reading, then updates conditionally:",
            )
        ),
        Spacer(1, 6),
        code_block(
            definition("app/infrastructure/db/repository.py", "update_workflow")
        ),
        p(
            tr(
                "Ноль обновлённых строк значит, что другая транзакция успела раньше: "
                "version_conflict, ничего не записано.",
                "Zero updated rows means another transaction won: version_conflict, "
                "nothing written.",
            ),
            SMALL,
        ),
        p(tr("Идемпотентность", "Idempotency"), H2),
        simple_table(
            [tr("Ситуация", "Situation"), tr("Результат", "Result")],
            [
                [
                    tr("Новый ключ", "New key"),
                    tr(
                        "Выполняется; результат сохраняется вместе с ключом",
                        "Runs; the result is stored with the key",
                    ),
                ],
                [
                    tr("Тот же ключ и запрос", "Same key and request"),
                    tr(
                        "Прежний результат, replayed=True, новых строк нет",
                        "Original result, replayed=True, no new rows",
                    ),
                ],
                [
                    tr("Тот же ключ, другой запрос", "Same key, other request"),
                    mono("idempotency_key_reused"),
                ],
                [
                    tr("Команда упала", "Command failed"),
                    tr(
                        "Ключ не сохраняется; повтор возможен",
                        "Key not stored; a retry is possible",
                    ),
                ],
            ],
            [170, W - 170],
        ),
        Spacer(1, 8),
        callout(
            tr(
                "<b>Гонка, найденная тестом.</b> Дубликат не нашёл ключ, затем первый "
                "запрос зафиксировался, и дубликат прочитал уже версию 2 — получал "
                "version_conflict вместо повтора (8 из 10 запусков). Теперь после "
                "конфликта ключ ищется заново; детерминированный тест воспроизводит "
                "этот порядок событий.",
                "<b>A race found by a test.</b> A duplicate found no key, then the "
                "first request committed and the duplicate read version 2, getting "
                "version_conflict instead of a replay (8 of 10 runs). After a conflict "
                "the key is now looked up again; a deterministic test reproduces this "
                "ordering.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]

    section(
        story,
        "Аудит и снимки",
        tr(
            "Журнал отвечает «кто, когда, что», снимок — «по каким именно данным».",
            "The audit says who, when and what; a snapshot says on exactly which data.",
        ),
        ORANGE,
    )
    story += [
        simple_table(
            [tr("Поле аудита", "Audit field"), tr("Смысл", "Meaning")],
            [
                [
                    mono("command, previous_state, new_state"),
                    tr("Что произошло", "What happened"),
                ],
                [
                    mono("workflow_version"),
                    tr(
                        "Версия процесса после команды",
                        "Workflow version after the command",
                    ),
                ],
                [
                    mono("*_version_id, snapshot_id"),
                    tr(
                        "Какие версии входов и какой результат",
                        "Which input versions and which result",
                    ),
                ],
                [
                    mono("issue_codes"),
                    tr(
                        "Только коды причин, без текста документа",
                        "Reason codes only, no document text",
                    ),
                ],
                [
                    mono("actor_id, source, idempotency_key"),
                    tr(
                        "Кто и откуда — заявлено вызывающим",
                        "Who and from where, caller-declared",
                    ),
                ],
                [
                    mono("occurred_at"),
                    tr(
                        "Время транзакции из PostgreSQL",
                        "Transaction time from PostgreSQL",
                    ),
                ],
            ],
            [210, W - 210],
            [CELL, CELL],
        ),
        p(
            tr(
                "Прежние версии не перезаписываются",
                "Earlier versions are never rewritten",
            ),
            H2,
        ),
        code_block(
            "check  → S1: PO v1 (30.00), policy v1, "
            + tr("причин нет", "no issues")
            + "\nrecord → PO v2 (17.50), policy v2 (USD)\n"
            "revise → "
            + tr("процесс закреплён за v2", "workflow pinned to v2")
            + "\ncheck  → S2: PO v2, policy v2, purchase_order_total_exceeded"
        ),
        p(
            tr(
                "S1 по-прежнему загружается с заказом v1, итогом 30.00 и прежними "
                "хешами. Пометка устаревших результатов — этап 05, проверка версий "
                "в транзакции согласования — этап 06.",
                "S1 still loads with PO v1, the 30.00 total and the original hashes. "
                "Marking stale results is Stage 05; checking versions inside the "
                "approval transaction is Stage 06.",
            ),
            SMALL,
        ),
        Spacer(1, 8),
        code_block(
            "uv run --locked alembic upgrade head\n"
            "uv run --locked python -m app.persistence_demo\n"
            "uv run --locked python -m app.persistence_demo show DEMO-WF-xxxxxxxxxxxx"
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Демонстрация: start → check → повтор с тем же ключом → approve по "
                "устаревшей версии (version_conflict) → новые версии → revise → check "
                "(needs_review) → reject. show читает результат в новом процессе.",
                "The demo: start → check → replay with the same key → approve on a "
                "stale version (version_conflict) → new versions → revise → check "
                "(needs_review) → reject. show reads it back in a new process.",
            )
        ),
    ]
