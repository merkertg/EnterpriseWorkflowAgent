"""Sections for stage 05: reconciliation rules, line checks and snapshots."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import reconciliation_checks, snapshot_lifecycle
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
    GREEN,
    GREEN_L,
    H2,
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

_STALE_TEXT = {
    "workflow_inputs_changed": (
        "Процесс переведён на другие версии (revise)",
        "The workflow was revised to other versions",
    ),
    "purchase_order_changed": (
        "У заказа появилась новая версия",
        "A newer order version exists",
    ),
    "purchase_order_available": (
        "Заказа не было, теперь он записан",
        "An order now exists where none did",
    ),
    "rules_changed": ("У правил появилась новая версия", "A newer rule version exists"),
    "duplicates_changed": (
        "Изменился список процессов с тем же счётом",
        "Other workflows for the same invoice changed",
    ),
}


def add_stage05(story: list[Any], facts: Facts) -> None:
    """Sections 18–20: deterministic reconciliation against the order."""
    section(
        story,
        "Правила сверки",
        tr(
            "Допуски и округление задаёт отдельная версия правил, а не код.",
            "A separate rule version, not code, sets tolerances and rounding.",
        ),
        GREEN,
    )
    story += [
        p(
            tr(
                "Совпадение итогов не доказывает совпадение позиций: другой товар "
                "можно выставить на ту же сумму. Поэтому этап 05 сверяет строки по SKU "
                "под версией правил ReconciliationRules с правилом на каждую валюту.",
                "Equal totals do not prove equal items: another product can be "
                "billed for the same amount. Stage 05 therefore matches lines by SKU "
                "under a ReconciliationRules version with one rule per currency.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Параметр", "Parameter"), tr("Смысл", "Meaning")],
            [
                [
                    mono("unit_price_tolerance"),
                    tr(
                        "Допустимая разница цены единицы с заказом",
                        "Allowed unit price difference from the order",
                    ),
                ],
                [
                    mono("total_tolerance"),
                    tr(
                        "Допуск для заявленного итога и суммы сопоставленных строк",
                        "Tolerance for the declared total and the matched-lines total",
                    ),
                ],
                [
                    mono("decimal_places, rounding"),
                    tr(
                        "Записаны явно: только 2 и ROUND_HALF_UP — так считает денежный модуль",
                        "Stated explicitly: only 2 and ROUND_HALF_UP, as the money module computes",
                    ),
                ],
                [
                    mono("allow_partial_quantities"),
                    tr(
                        "Можно ли выставить меньше заказанного",
                        "Whether fewer units than ordered may be billed",
                    ),
                ],
                [
                    mono("require_source_document"),
                    tr(
                        "Требовать ли счёт из полного извлечения документа",
                        "Whether the invoice must come from a complete extraction",
                    ),
                ],
            ],
            [175, W - 175],
            [CELL, CELL],
        ),
        Spacer(1, 6),
        p(
            tr(
                "Правила хранятся версиями, как заказ и политика: то же содержимое — "
                "та же версия, изменённое — следующая. Хеш не зависит от порядка валют. "
                "Нет правила для валюты счёта — no_rule_for_currency, автоматического "
                "продвижения нет.",
                "Rules are stored as versions like orders and policies: the same "
                "content is the same version, changed content the next one. The hash "
                "ignores currency order. No rule for the invoice currency gives "
                "no_rule_for_currency and blocks automatic progress.",
            )
        ),
        p(
            tr("Граница допуска включительная", "The tolerance boundary is inclusive"),
            H2,
        ),
        simple_table(
            [
                tr("Цена заказа / счёта", "Order / invoice price"),
                tr("Разница", "Difference"),
                tr("Допуск 0.05", "Tolerance 0.05"),
            ],
            [
                ["5.00 / 5.05", "0.05", tr("проходит", "passes")],
                ["5.00 / 5.06", "0.06", mono("unit_price_mismatch")],
            ],
            [170, 100, W - 270],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 8),
        callout(
            tr(
                "<b>Малые разницы складываются.</b> Допуск на единицу 0.05, на итог "
                "0.10. Уплотнитель 12.54 вместо 12.50 (две штуки) и фильтр 5.04 вместо "
                "5.00: каждая строка в допуске, но 2 × 0.04 + 0.04 = 0.12 > 0.10 — "
                "expected_total_mismatch с цитатами всех изменённых цен.",
                "<b>Small differences add up.</b> Unit tolerance 0.05, total 0.10. "
                "A seal at 12.54 instead of 12.50 (two units) and a filter at 5.04 "
                "instead of 5.00: each line is inside tolerance, but 2 × 0.04 + 0.04 = "
                "0.12 > 0.10 gives expected_total_mismatch citing every changed price.",
            ),
            TEAL,
            TEAL_L,
        ),
    ]

    section(
        story,
        "Сверка строк",
        tr(
            "Код, ожидаемое и найденное значение, правило и цитата для каждой причины.",
            "Code, expected and actual value, rule and quote for every reason.",
        ),
        GREEN,
    )
    story += [
        reconciliation_checks(),
        Spacer(1, 8),
        p(
            tr(
                f"Всего {len(facts.discrepancy_codes)} кодов, всегда в одном порядке. "
                "Ожидаемая сумма строки — цена заказа × выставленное количество, "
                "округлённая один раз по ROUND_HALF_UP. Неизвестная строка отмечается "
                "unknown_line и не входит в итог сопоставленных строк, чтобы одна сумма "
                "не попала в отчёт дважды. Нет причин — matched; это ещё не одобрение.",
                f"There are {len(facts.discrepancy_codes)} codes, always in the same "
                "order. The expected line total is order price × invoiced quantity, "
                "rounded once with ROUND_HALF_UP. An unknown line is reported as "
                "unknown_line and left out of the matched-lines total, so one amount "
                "is never reported twice. No reason means matched; still no approval.",
            )
        ),
        p(tr("Строка отчёта", "A report entry"), H2),
        code_block(
            '{"code": "unit_price_mismatch", "field": "lines[2].unit_price",\n'
            ' "expected": "5.00 USD ± 0.05", "actual": "5.10 USD",\n'
            ' "rule": "USD.unit_price_tolerance=0.05",\n'
            ' "sources": [{"page": 1, "line_number": 7, "quote": "5.10"}]}'
        ),
        Spacer(1, 6),
        p(
            tr(
                "Поле называется по схеме этапа 04, поэтому цитата берётся из того "
                "извлечения, которое создало версию счёта. Если счёт пришёл несколькими "
                "файлами, используется самое раннее извлечение; его id записан в снимок.",
                "The field uses the Stage 04 schema name, so the quote comes from the "
                "extraction that produced the invoice version. If several files carried "
                "the invoice, the earliest extraction is used and its ID is recorded.",
            ),
            SMALL,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "<b>Повторный счёт.</b> Другой процесс не в состоянии rejected с тем же "
                "поставщиком и номером даёт duplicate_invoice со ссылкой на него. Тот же "
                "счёт под новым номером эта проверка не ловит.",
                "<b>Repeated invoice.</b> Another non-rejected workflow with the same "
                "supplier and number gives duplicate_invoice naming it. The same "
                "invoice under a new number is not caught.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]

    section(
        story,
        "Снимок и устаревание",
        tr(
            "Старый результат не исправляют: он устаревает и воспроизводится.",
            "An old result is never edited: it goes stale and stays reproducible.",
        ),
        GREEN,
    )
    story += [
        snapshot_lifecycle(),
        Spacer(1, 8),
        p(
            tr(
                "Снимок reconciliation_snapshots хранит версии и хеши счёта, заказа и "
                "правил, хеш исходного документа, извлечение с хешем полей и цитат, "
                f"список дубликатов и версию логики {facts.engine}. Из них считается "
                "хеш входов: повторная сверка с теми же входами возвращает тот же "
                "снимок и ничего не пишет.",
                "A reconciliation_snapshots row keeps invoice, order and rule versions "
                "with hashes, the source document hash, the extraction with a hash of "
                f"its fields and citations, the duplicate list and engine {facts.engine}. "
                "They form an inputs hash: reconciling again with equal inputs returns "
                "the same snapshot and writes nothing.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Причина устаревания", "Stale reason"), tr("Когда", "When")],
            [
                [mono(reason), tr(*_STALE_TEXT[reason])]
                for reason in facts.stale_reasons
            ],
            [185, W - 185],
            [CELL, CELL],
        ),
        Spacer(1, 6),
        code_block(
            "uv run --locked python -m app.reconciliation_cli demo\n"
            "uv run --locked python -m app.reconciliation_cli show SNAPSHOT_ID"
        ),
        Spacer(1, 6),
        p(
            tr(
                "reproduce загружает версии самого снимка, проверяет их хеши и считает "
                "заново; изменённые в обход приложения правила дают stored_data_integrity. "
                "Сверка не меняет состояние процесса и не пишет аудит.",
                "reproduce loads the snapshot's own versions, checks their hashes and "
                "recomputes; rules edited behind the application's back give "
                "stored_data_integrity. Reconciliation changes no workflow state and "
                "writes no audit event.",
            ),
            SMALL,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "С этапа 06 одобрение ссылается на снимок и в своей транзакции "
                "проверяет, что он свежий и matched (раздел «Одобрение по свежей "
                "сверке»).",
                "Since Stage 06 an approval cites a snapshot and checks inside its "
                "own transaction that it is fresh and matched (section “Approval "
                "on a fresh reconciliation”).",
            ),
            GREEN,
            GREEN_L,
        ),
    ]
