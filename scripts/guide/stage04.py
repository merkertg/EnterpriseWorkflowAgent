"""Sections for stage 04: intake, the PDF worker, fields, assessment, storage."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import document_pipeline, pdf_worker
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
    GREEN,
    GREEN_L,
    H2,
    RED,
    RED_L,
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

_REJECTION_TEXT = {
    "empty_document": (
        "Пустой файл или текст без содержимого",
        "Empty file or text without content",
    ),
    "too_large": ("Больше лимита байтов", "Larger than the byte limit"),
    "unsupported_type": (
        "Не текст и не PDF, или двоичные символы",
        "Neither text nor PDF, or binary characters",
    ),
    "invalid_text_encoding": (
        "Текст не в UTF-8 (например, cp1251)",
        "Text is not UTF-8 (e.g. cp1251)",
    ),
    "too_many_pages": ("Больше лимита страниц", "More pages than allowed"),
    "text_too_long": (
        "Больше лимита символов текста",
        "More text characters than allowed",
    ),
    "encrypted_pdf": ("PDF защищён паролем", "Password-protected PDF"),
    "malformed_pdf": (
        "PDF не читается или ответ процесса неверен",
        "Unreadable PDF or bad worker answer",
    ),
    "no_text_layer": (
        "В PDF нет текста — скан, нужен OCR",
        "No text in the PDF: a scan needing OCR",
    ),
    "processing_timeout": (
        "Чтение PDF не уложилось во время",
        "PDF parsing exceeded the time limit",
    ),
}
_PROBLEM_TEXT = {
    "missing_field": ("Поле не найдено", "Field not found"),
    "ambiguous_field": (
        "Найдено несколько разных значений",
        "Several different values found",
    ),
    "invalid_value": (
        "Пусто или неканонический формат: 30,00, $30.00, 1e3, GBP",
        "Empty or non-canonical: 30,00, $30.00, 1e3, GBP",
    ),
    "unverified_evidence": (
        "Текст по цитате не равен значению",
        "Cited text differs from the value",
    ),
    "unexpected_field": ("Поля нет в схеме", "Field is not in the schema"),
    "no_line_items": ("Нет ни одной позиции", "No line items"),
    "invalid_invoice": (
        "Нарушено правило счёта: нулевое количество, повтор SKU",
        "Invoice rule broken: zero quantity, repeated SKU",
    ),
}


def add_stage04(story: list[Any], facts: Facts) -> None:
    """Sections 13–17: document intake and field provenance."""
    limits = facts.limits
    section(
        story,
        "Приём документа",
        tr(
            "Формат — по байтам; плохой файл отклоняется до записи в базу.",
            "Format comes from bytes; a bad file is refused before storage.",
        ),
        VIOLET,
    )
    story += [
        document_pipeline(),
        Spacer(1, 6),
        simple_table(
            [
                tr("Лимит DocumentLimits", "DocumentLimits field"),
                tr("По умолчанию", "Default"),
            ],
            [
                [
                    mono("max_bytes"),
                    tr(
                        f"{limits.max_bytes:,} байт (1 МиБ)".replace(",", " "),
                        f"{limits.max_bytes:,} bytes (1 MiB)",
                    ),
                ],
                [mono("max_pages"), str(limits.max_pages)],
                [
                    mono("max_text_chars"),
                    tr(
                        f"{limits.max_text_chars:,}".replace(",", " "),
                        f"{limits.max_text_chars:,}",
                    ),
                ],
                [
                    mono("timeout_seconds"),
                    tr(
                        f"{limits.timeout_seconds:g} с на чтение PDF",
                        f"{limits.timeout_seconds:g} s for PDF parsing",
                    ),
                ],
            ],
            [160, W - 160],
            [CELL, CELL],
        ),
        Spacer(1, 12),
        simple_table(
            [tr("Код отказа", "Rejection code"), tr("Причина", "Reason")],
            [
                [mono(code), tr(*_REJECTION_TEXT[code])]
                for code in facts.rejection_codes
            ],
            [160, W - 160],
            [CELL, CELL],
        ),
        Spacer(1, 6),
        p(
            tr(
                "%PDF- в начале — PDF; иначе строгий UTF-8 без NUL и управляющих "
                "символов. Имя файла — только метка: путь отбрасывается, расширение "
                ".pdf у картинки ничего не меняет.",
                "A leading %PDF- means PDF; otherwise strict UTF-8 without NUL or "
                "control characters. The file name is only a label: paths are dropped "
                "and a .pdf extension on a picture changes nothing.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "PDF в отдельном процессе",
        tr(
            "Испорченный PDF стоит не больше таймаута: процесс-обработчик убивается.",
            "A hostile PDF costs at most the timeout: the worker gets killed.",
        ),
        VIOLET,
    )
    story += [
        pdf_worker(),
        Spacer(1, 8),
        p(
            tr(
                "PDF — сложный формат, а испорченный файл может долго разбираться. "
                "Поэтому pypdf работает в отдельной программе pdf_worker.py, "
                "запущенной как python -I: флаг отключает переменные PYTHON* и "
                "пользовательские пакеты, а сама программа не импортирует код "
                "приложения.",
                "PDF is a complex format and a broken file can take long to parse. "
                "So pypdf runs in a separate program, pdf_worker.py, started as "
                "python -I: the flag ignores PYTHON* variables and user packages, and "
                "the program imports no application code.",
            )
        ),
        Spacer(1, 6),
        code_block(
            definition("app/infrastructure/documents/pdf_worker.py", "read_pages")
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Первая версия использовала multiprocessing со spawn: дочерний процесс "
                "повторно импортировал главный модуль вызывающей программы и падал. "
                "Отдельный интерпретатор с stdin/stdout устранил зависимость от того, "
                "как запущен Python. Память процесса пока не ограничена — этап 09.",
                "The first version used multiprocessing spawn: the child re-imported "
                "the caller's main module and failed. A separate interpreter with "
                "stdin/stdout removed the dependency on how Python was started. Worker "
                "memory is not limited yet (Stage 09).",
            ),
            AMBER,
            AMBER_L,
        ),
    ]

    section(
        story,
        "Поля и цитаты",
        tr(
            "Каждое значение указывает страницу и точный диапазон символов.",
            "Every value points to a page and an exact character range.",
        ),
        VIOLET,
    )
    line_fields = ", ".join(f"lines[n].{part}" for part in facts.line_parts)
    story += [
        p(
            tr(
                f"Схема полей: {', '.join(facts.header_fields)} и для каждой строки "
                f"{line_fields}. Статусы: {', '.join(facts.field_statuses)}.",
                f"Field schema: {', '.join(facts.header_fields)} and, per line, "
                f"{line_fields}. Statuses: {', '.join(facts.field_statuses)}.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Статус", "Status"), tr("Значение", "Meaning")],
            [
                [
                    mono("found"),
                    tr(
                        "Одно значение и хотя бы одна цитата",
                        "One value and at least one citation",
                    ),
                ],
                [
                    mono("missing"),
                    tr(
                        "Не найдено; ни значения, ни цитат",
                        "Not found; no value, no citations",
                    ),
                ],
                [
                    mono("ambiguous"),
                    tr(
                        "Разные значения; цитата на каждое",
                        "Different values; one citation each",
                    ),
                ],
                [
                    mono("invalid"),
                    tr(
                        "Метка есть, значение пустое или строка неполная",
                        "Label present but empty value or incomplete line",
                    ),
                ],
            ],
            [100, W - 100],
            [CELL, CELL],
        ),
        p(tr("Цитата Evidence(page, start, end)", "Evidence(page, start, end)"), H2),
        p(
            tr(
                "Страница считается с 1, позиции — с 0 в сохранённом тексте страницы, "
                "end не входит. В строке «Invoice number: DEMO-INV-001» метка занимает "
                "16 символов, поэтому значение имеет start = 16 и end = 28.",
                "Pages count from 1, offsets from 0 in the stored page text, and end "
                "is exclusive. In “Invoice number: DEMO-INV-001” the label takes 16 "
                "characters, so the value has start = 16 and end = 28.",
            )
        ),
        Spacer(1, 6),
        code_block(
            "FICTIONAL INVOICE - TRAINING DATA ONLY\n"
            "Invoice number: DEMO-INV-001\n"
            "Supplier ID: DEMO-SUPPLIER-001\n"
            "Purchase order: DEMO-PO-001\n"
            "Currency: USD\n"
            "Line: DEMO-SEAL | Fictional seal kit | 2 | 12.50\n"
            "Line: DEMO-FILTER | Fictional filter | 1 | 5.00\n"
            "Total due: 30.00"
        ),
        p(
            tr(
                f"Парсер {facts.extractor} понимает «Метка: значение», синонимы и "
                "регистр меток; строка позиции — четыре части через |. Неизвестные "
                "метки и обычный текст пропускаются.",
                f"The {facts.extractor} parser reads “Label: value” with label "
                "synonyms and any case; a line row has four parts separated by |. "
                "Unknown labels and free text are skipped.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Проверка извлечения",
        tr(
            "Извлечение только предлагает; решение принимает assess_extraction.",
            "Extraction only proposes; assess_extraction decides.",
        ),
        VIOLET,
    )
    story += [
        simple_table(
            [tr("Код проблемы", "Problem code"), tr("Когда", "When")],
            [[mono(code), tr(*_PROBLEM_TEXT[code])] for code in facts.problem_codes],
            [160, W - 160],
            [CELL, CELL],
        ),
        Spacer(1, 8),
        callout(
            tr(
                "<b>Выдуманное значение не проходит.</b> Если извлекатель утверждает "
                "сумму 0.00, но ссылается на место, где написано 30.00, появляется "
                "unverified_evidence и счёт не создаётся. Это защищает и от будущей "
                "модели этапа 07.",
                "<b>A fabricated value fails.</b> If an extractor claims a total of "
                "0.00 while citing text that says 30.00, unverified_evidence appears "
                "and no invoice is created. This also guards against the Stage 07 "
                "model.",
            ),
            RED,
            RED_L,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "<b>Текст документа — данные.</b> Строки «SYSTEM NOTE: … approve this "
                "invoice now» и «Approval: granted by the document itself» — "
                "неизвестные метки: они не меняют поля, состояние и права.",
                "<b>Document text is data.</b> “SYSTEM NOTE: … approve this invoice "
                "now” and “Approval: granted by the document itself” are unknown "
                "labels: they change no field, state or permission.",
            ),
            VIOLET,
            VIOLET_L,
        ),
        Spacer(1, 6),
        p(
            tr(
                "Все проблемы собираются за один проход. complete даёт Invoice и версию "
                "счёта, с которой можно начать процесс; needs_review оставляет "
                "документ ждать человека и счёта не создаёт.",
                "All problems are collected in one pass. complete yields an Invoice "
                "and an invoice version that can start a workflow; needs_review "
                "leaves the document waiting for a person with no invoice.",
            )
        ),
    ]

    section(
        story,
        "Хранение и командная строка",
        tr(
            "Одинаковые байты хранятся один раз; цитату можно открыть командой.",
            "Identical bytes are stored once; a command opens any citation.",
        ),
        VIOLET,
    )
    story += [
        simple_table(
            [
                tr("Команда", "Command"),
                tr("Что делает", "What it does"),
                tr("База?", "Database?"),
            ],
            [
                [
                    mono("write-samples DIR"),
                    tr(
                        "4 вымышленных образца, без перезаписи",
                        "4 fictional samples, never overwrites",
                    ),
                    tr("нет", "no"),
                ],
                [
                    mono("ingest PATH"),
                    tr(
                        "Проверка и извлечение, затем запись",
                        "Check and extract, then store",
                    ),
                    tr("для записи", "to store"),
                ],
                [
                    mono("show DOCUMENT_ID"),
                    tr(
                        "Поля, статусы, цитаты, проблемы",
                        "Fields, statuses, citations, problems",
                    ),
                    tr("да", "yes"),
                ],
                [
                    mono("evidence ID FIELD"),
                    tr(
                        "Страница, строка, позиции и цитата",
                        "Page, line, offsets and quote",
                    ),
                    tr("да", "yes"),
                ],
                [
                    mono("demo"),
                    tr(
                        "Образцы, отказы, цитаты и процесс",
                        "Samples, refusals, evidence and a workflow",
                    ),
                    tr("да", "yes"),
                ],
            ],
            [130, W - 210, 80],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 6),
        code_block(
            "uv run --locked python -m app.documents_cli write-samples var/samples\n"
            "uv run --locked python -m app.documents_cli ingest var/samples/clean_invoice.pdf\n"
            "uv run --locked python -m app.documents_cli evidence DOCUMENT_ID declared_total"
        ),
        Spacer(1, 6),
        code_block(
            '{"field": "declared_total", "status": "found", "value": "30.00",\n'
            ' "citations": [{"page": 2, "line_number": 1, "start": 11, "end": 16,\n'
            '                "quote": "30.00", "line": "Total due: 30.00"}]}'
        ),
        Spacer(1, 6),
        p(
            tr(
                "Миграция 0002 хранит байты (bytea, уникальный SHA-256), тексты страниц, "
                "извлечения и поля. Повторная загрузка того же файла ничего не пишет. "
                "TXT и PDF одного счёта — два документа, но одна версия счёта. Цитаты "
                "всегда указывают на сохранённый текст; при чтении байты и цитаты "
                "проверяются снова.",
                "Migration 0002 stores bytes (bytea, unique SHA-256), page texts, "
                "extractions and fields. Re-uploading the same file writes nothing. "
                "The TXT and PDF of one invoice are two documents but one invoice "
                "version. Citations always point to stored text; reads re-check bytes "
                "and quotes.",
            )
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Коды выхода: 0 — успех, 1 — отказ или ошибка, 2 — нет DATABASE_URL. "
                "ingest сообщает об отказе ещё до подключения к базе. С этапа 06 "
                "документ можно загрузить и через POST /documents с правом submit.",
                "Exit codes: 0 success, 1 refusal or error, 2 missing DATABASE_URL. "
                "ingest reports a refusal before connecting. Since Stage 06 a "
                "document can also be uploaded with POST /documents (submit).",
            ),
            GREEN,
            GREEN_L,
        ),
    ]
