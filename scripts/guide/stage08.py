"""Sections for stage 08: the Streamlit interface over the HTTP API."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import repeat_paths, ui_layers
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
    H2,
    SMALL,
    TEAL,
    TEAL_L,
    VIOLET,
    W,
    callout,
    code_block,
    mono,
    p,
    simple_table,
)
from guide.sources import Facts
from guide.toc import section


def add_stage08(story: list[Any], facts: Facts) -> None:
    """Sections 28–30: the page and client, repeats, errors and checks."""
    section(
        story,
        "Интерфейс поверх API",
        tr(
            "Страница — обычный клиент HTTP API: без базы и без бизнес-правил.",
            "The page is a plain HTTP API client: no database, no business rules.",
        ),
        VIOLET,
    )
    story += [
        p(
            tr(
                "Сотрудник загружает счёт, видит поля с источниками, сверку, черновик "
                "помощника, историю и принимает решение — в браузере. Каждая цифра "
                "и каждый отказ приходят от сервера; страница их только показывает.",
                "An employee uploads an invoice, sees fields with sources, the "
                "reconciliation, the assistant's draft and the history, and decides — "
                "in a browser. Every number and every refusal comes from the server; "
                "the page only shows them.",
            )
        ),
        Spacer(1, 6),
        ui_layers(),
        Spacer(1, 6),
        simple_table(
            [tr("Блок страницы", "Page block"), tr("Запрос", "Request")],
            [
                [
                    tr(
                        "Список процессов своих подразделений", "Workflows of own units"
                    ),
                    mono("GET /workflows"),
                ],
                [
                    tr(
                        "Поля документа и строки источника",
                        "Document fields and source lines",
                    ),
                    mono("GET /workflows/{id}/document"),
                ],
                [
                    tr(
                        "Сверка, актуальность, расхождения",
                        "Reconciliation, freshness, discrepancies",
                    ),
                    mono("GET …/reconciliations/latest"),
                ],
                [
                    tr(
                        "Проверка, сверка, закрепить заказ",
                        "Check, reconcile, pin the order",
                    ),
                    mono("POST …/check, …/reconciliations, …/revise"),
                ],
                [
                    tr("Одобрить, отклонить", "Approve, reject"),
                    mono("POST …/approve, …/reject"),
                ],
                [
                    tr("Помощник; аудит и outbox", "Assistant; audit and outbox"),
                    mono("POST …/agent-runs; GET …/audit, …/outbox"),
                ],
                [
                    tr(
                        "Новый счёт: загрузка и запуск", "New invoice: upload and start"
                    ),
                    mono("POST /documents, /workflows"),
                ],
            ],
            [W * 0.45, W * 0.55],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        code_block(
            "uv sync --locked --group ui\n"
            "uv run --locked python -m app.ui_demo_setup\n"
            "uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8003\n"
            "uv run --locked --group ui python -m streamlit run ui/app.py"
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Токен вставляется в поле «пароль» и живёт только в сессии вкладки. "
                "Streamlit — отдельная группа зависимостей ui: API от неё не "
                "зависит. Клиент на urllib принимает только http(s), иначе поле "
                "адреса могло бы открыть file://.",
                "The token goes into a password field and lives only in the tab's "
                "session. Streamlit is the separate ui dependency group: the API does "
                "not depend on it. The urllib client accepts only http(s); otherwise "
                "the address field could open file://.",
            ),
            TEAL,
            TEAL_L,
        ),
    ]

    section(
        story,
        "Повтор и перезагрузка",
        tr(
            "Ключ из намерения: второй клик не принимает второго решения.",
            "A key from the intent: a second click never makes a second decision.",
        ),
        VIOLET,
    )
    story += [
        p(
            tr(
                "Каждый клик в Streamlit перезапускает скрипт страницы. Команды несут "
                "Idempotency-Key, построенный из действия, процесса, версии и значимых "
                "входов, поэтому тот же запрос всегда даёт тот же ключ — даже в новой "
                "вкладке и после перезагрузки.",
                "Every click in Streamlit reruns the page script. Commands carry an "
                "Idempotency-Key built from the action, workflow, version and relevant "
                "inputs, so the same request always gives the same key — even in a "
                "new tab and after a reload.",
            )
        ),
        Spacer(1, 6),
        repeat_paths(),
        Spacer(1, 6),
        simple_table(
            [tr("Ситуация", "Situation"), tr("Ответ сервера", "Server answer")],
            [
                [
                    tr(
                        "Форма запуска отправлена ещё раз",
                        "The start form is sent again",
                    ),
                    tr("сохранённый результат", "stored result"),
                ],
                [
                    tr("Одобрение из старой вкладки", "Approval from an old tab"),
                    mono("409 invalid_transition"),
                ],
                [
                    tr("Тот же ключ, другая причина", "Same key, another reason"),
                    mono("409 idempotency_key_reused"),
                ],
                [
                    tr("Те же байты документа", "The same document bytes"),
                    tr("тот же документ", "the same document"),
                ],
                [
                    tr("Сверка с теми же входами", "Reconcile with equal inputs"),
                    tr("тот же снимок", "the same snapshot"),
                ],
                [
                    tr("Помощник ещё раз", "The assistant again"),
                    tr(
                        "новая трассировка; процесс не меняется",
                        "a new trace; the workflow is unchanged",
                    ),
                ],
            ],
            [W * 0.5, W * 0.5],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        p(
            tr(
                "Проверено и в тестах, и в браузере: после повторного клика в базе "
                "одно событие outbox, а аудит показывает start, check и approve.",
                "Checked both in tests and in a browser: after a repeated click the "
                "database holds one outbox event, and the audit shows start, check and "
                "approve.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Ошибки и проверка страницы",
        tr(
            "Код и сообщение сервера плюс подсказка; страница ничего не скрывает.",
            "The server's code and message plus a hint; the page hides nothing.",
        ),
        VIOLET,
    )
    story += [
        simple_table(
            [
                tr("Код сервера", "Server code"),
                tr("Подсказка на странице", "Hint on the page"),
            ],
            [[mono(code), hint] for code, hint in facts.ui_hints],
            [190, W - 190],
            [CELL, CELL],
            padding=2,
        ),
        Spacer(1, 4),
        p(
            tr(
                "Пограничные состояния названы явно: нет процессов, нет документа "
                "(счёт создан кодом), нет сверки, извлечение ждёт человека, черновик "
                "помощника заменён. Кнопки не прячутся по роли или состоянию — отказ "
                "показывает сервер.",
                "Edge states are stated: no workflows, no document (an invoice made "
                "in code), no reconciliation, an extraction waiting for a person, a "
                "replaced draft. Buttons are not hidden by role or state — the server "
                "shows the refusal.",
            ),
            SMALL,
        ),
        p(tr("Как проверена страница", "How the page is checked"), H2),
        simple_table(
            [tr("Проверка", "Check"), tr("Что подтверждает", "What it shows")],
            [
                [
                    tr("Клиент без сервера", "Client without a server"),
                    tr(
                        "заголовки, пути, ошибки, ключи", "headers, paths, errors, keys"
                    ),
                ],
                [
                    tr("AppTest с поддельным клиентом", "AppTest with a fake client"),
                    tr(
                        "пограничные состояния и отказы на экране",
                        "edge states and refusals on screen",
                    ),
                ],
                [
                    tr("AppTest + API + PostgreSQL", "AppTest + API + PostgreSQL"),
                    tr(
                        "весь сценарий, повторы, права, устаревание",
                        "full scenario, repeats, rights, staleness",
                    ),
                ],
                [
                    tr("Настоящий браузер", "A real browser"),
                    tr(
                        "вход, загрузка, отказ 403, одобрение, повтор",
                        "sign-in, upload, 403, approval, repeat",
                    ),
                ],
            ],
            [W * 0.42, W * 0.58],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Без --group ui модули тестов страницы пропускаются целиком — смотри "
                "итог pytest. CI ставит группу ui, поэтому там они выполняются.",
                "Without --group ui the page test modules are skipped as a whole — "
                "read the pytest summary. CI installs the ui group, so they run there.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]
