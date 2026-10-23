"""Sections for stage 06: sign-in and roles, approval, HTTP API and outbox."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import approval_checks, outbox_commit, token_flow
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
    H2,
    PINK,
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

_ROLE_TEXT = {
    "viewer": ("наблюдатель", "observer"),
    "clerk": ("специалист", "clerk"),
    "approver": ("согласующий", "approver"),
}

# Permission and purpose of every business route; the build fails on a gap.
_ROUTE_TEXT = {
    ("GET", "/me"): ("—", "Кто я, роли, права", "Who am I, roles, permissions"),
    ("POST", "/documents"): (
        "submit",
        "Загрузка байтов документа",
        "Upload document bytes",
    ),
    ("GET", "/reference/{kind}/{business_id}"): (
        "view",
        "Последняя версия заказа, политики, правил",
        "Latest order, policy or rules version",
    ),
    ("POST", "/workflows"): (
        "submit",
        "Запуск в своём подразделении",
        "Start in own unit",
    ),
    ("GET", "/workflows/{workflow_id}"): (
        "view",
        "Состояние, входы, решение",
        "State, inputs, decision",
    ),
    ("POST", "/workflows/{workflow_id}/check"): (
        "submit",
        "Базовая проверка",
        "Basic check",
    ),
    ("POST", "/workflows/{workflow_id}/revise"): (
        "submit",
        "Другие версии входов",
        "Other input versions",
    ),
    ("POST", "/workflows/{workflow_id}/reconciliations"): (
        "reconcile",
        "Сверка и отчёт",
        "Reconcile and report",
    ),
    ("GET", "/workflows/{workflow_id}/reconciliations/latest"): (
        "view",
        "Последняя сверка, актуальность",
        "Latest reconciliation, freshness",
    ),
    ("GET", "/reconciliations/{snapshot_id}"): (
        "view",
        "Отчёт по снимку",
        "Snapshot report",
    ),
    ("POST", "/workflows/{workflow_id}/approve"): (
        "decide",
        "Одобрение по снимку",
        "Approve on a snapshot",
    ),
    ("POST", "/workflows/{workflow_id}/reject"): ("decide", "Отклонение", "Reject"),
    ("GET", "/workflows/{workflow_id}/audit"): ("view", "Аудит", "Audit trail"),
    ("GET", "/workflows/{workflow_id}/outbox"): (
        "view",
        "События решений",
        "Decision events",
    ),
    ("POST", "/workflows/{workflow_id}/agent-runs"): (
        "assist",
        "Запуск агента (этап 07)",
        "Run the agent (stage 07)",
    ),
    ("GET", "/agent-runs/{run_id}"): ("view", "Трассировка агента", "Agent trace"),
}

_OUTBOX_TEXT = {
    "invoice.approved": (
        "Одобрение; в данных ID снимка сверки и хеш его входов",
        "Approval; payload holds the snapshot ID and its inputs hash",
    ),
    "invoice.rejected": ("Отклонение", "Rejection"),
}


def _routes(facts: Facts) -> list[tuple[str, str]]:
    """Return the business routes; fail the build if one lacks a description."""
    if set(facts.routes) != set(_ROUTE_TEXT):
        raise ValueError("Describe every business route in _ROUTE_TEXT.")
    return list(facts.routes)


def _error_rows(facts: Facts) -> list[list[str]]:
    """Group error codes by HTTP status, adding domain and database codes."""
    groups: dict[int, list[str]] = {}
    for code, status in facts.error_statuses:
        groups.setdefault(status, []).append(code)
    # Mapped outside ERROR_STATUS: domain state codes, request and DB errors.
    groups[409] += ["invalid_transition", "stale_validation"]
    groups.setdefault(422, []).insert(0, "invalid_request")
    groups[503].append("database_unavailable")
    return [[str(status), ", ".join(codes)] for status, codes in sorted(groups.items())]


def add_stage06(story: list[Any], facts: Facts) -> None:
    """Sections 21–24: who may decide, how approval is checked, API, outbox."""
    section(
        story,
        "Вход и роли",
        tr(
            "Сервер узнаёт сотрудника по токену и проверяет роль в подразделении.",
            "The server identifies an employee by token and checks the role in a unit.",
        ),
        PINK,
    )
    story += [
        p(
            tr(
                "До этапа 06 actor_id был просто строкой. Теперь сотрудник — запись "
                "users с ролями и подразделениями. Роль описывает должность, право — "
                "действие; у руководителя может быть несколько ролей.",
                "Before Stage 06 actor_id was just a string. Now an employee is a "
                "users row with roles and business units. A role describes a job, a "
                "permission an action; a team lead may hold several roles.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [tr("Роль", "Role"), *facts.permissions],
            [
                [
                    f"{mono(role)} · {tr(*_ROLE_TEXT[role])}",
                    *(
                        tr("да", "yes") if item in allowed else "—"
                        for item in facts.permissions
                    ),
                ]
                for role, allowed in facts.roles.items()
            ],
            [170] + [(W - 170) / len(facts.permissions)] * len(facts.permissions),
        ),
        Spacer(1, 6),
        p(
            tr(
                "Процесс принадлежит подразделению. Чужое подразделение — not_found "
                "(404): сотрудник не узнаёт даже о существовании процесса. Своё "
                "подразделение без нужного права — missing_permission (403). "
                "Справочники общие и требуют только view.",
                "A workflow belongs to a business unit. Another unit gives not_found "
                "(404): the employee does not even learn that the workflow exists. "
                "The own unit without the permission gives missing_permission (403). "
                "Reference data is shared and needs only view.",
            ),
        ),
        p(tr("Токен", "Token"), H2),
        token_flow(),
        Spacer(1, 6),
        code_block(
            "... users_cli create demo-approver --name Demo "
            "--role approver --unit DEMO-UNIT-A\n"
            "... users_cli token demo-approver --label laptop\n"
            "... users_cli deactivate demo-approver | revoke TOKEN_ID | list"
        ),
        Spacer(1, 4),
        p(
            tr(
                "Почему не JWT: подписанный токен живёт до истечения срока, а здесь "
                "каждый запрос всё равно читает базу — отзыв и отключение действуют "
                "сразу. Учётные записи создаются только из командной строки.",
                "Why not a JWT: a signed token lives until it expires, while here every "
                "request reads the database anyway, so revocation and deactivation "
                "apply at once. Accounts are created only from the command line.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Одобрение по свежей сверке",
        tr(
            "Решение ссылается на снимок; его свежесть проверяется под блокировками.",
            "A decision cites a snapshot; its freshness is checked under locks.",
        ),
        PINK,
    )
    story += [
        p(
            tr(
                "Запрос одобрения несёт ожидаемую версию процесса, ID снимка сверки и "
                "причину. Права проверяются в прикладном слое внутри транзакции: "
                "сотрудник перечитывается с FOR SHARE, поэтому отключение между входом "
                "и командой отклоняет команду.",
                "An approval request carries the expected workflow version, a "
                "reconciliation snapshot ID and a reason. Rights are checked in the "
                "application layer inside the transaction: the user is re-read with "
                "FOR SHARE, so a deactivation between sign-in and command refuses it.",
            )
        ),
        Spacer(1, 6),
        approval_checks(),
        p(tr("Зачем блокировки", "Why locks"), H2),
        code_block(
            tr(
                "Одобрение:  снимок свежий? да ──────── запись решения ── COMMIT\n"
                "Закупки:           новая версия заказа ── COMMIT",
                "Approval:   snapshot fresh? yes ────── write decision ── COMMIT\n"
                "Purchasing:        new order version ── COMMIT",
            )
        ),
        Spacer(1, 4),
        p(
            tr(
                "Без блокировки решение легло бы на устаревший заказ. Одобрение и все, "
                "кто меняет эти данные, берут одни и те же транзакционные "
                "advisory-блокировки; ключи сортируются, ожидание ограничено "
                "lock_timeout = 5 с.",
                "Without a lock the decision would rest on a stale order. Approval and "
                "every writer of this data take the same transaction advisory locks; "
                "keys are sorted and waiting is bounded by lock_timeout = 5 s.",
            ),
            SMALL,
        ),
        simple_table(
            [tr("Ключ", "Key"), tr("Кто ещё его берёт", "Who else takes it")],
            [
                [
                    mono("purchase-order:<id>"),
                    tr("Запись новой версии заказа", "Recording a new order version"),
                ],
                [
                    mono("reconciliation-rules:<id>"),
                    tr("Запись новой версии правил", "Recording a new rule version"),
                ],
                [
                    mono("invoice-number:<supplier>|<number>"),
                    tr(
                        "Запуск процесса по тому же счёту",
                        "Starting a workflow for the same invoice",
                    ),
                ],
            ],
            [215, W - 215],
            [CELL, CELL],
        ),
        Spacer(1, 6),
        callout(
            tr(
                "<b>Четыре глаза и отклонение.</b> Запустивший процесс не может его "
                "одобрить (self_approval_forbidden). Отклонение не требует снимка "
                "сверки: отказать можно и по неполным данным.",
                "<b>Four eyes and rejection.</b> The employee who started a workflow "
                "cannot approve it (self_approval_forbidden). Rejection needs no "
                "snapshot: one may refuse on incomplete data.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]

    section(
        story,
        "HTTP API",
        tr(
            "Маршруты только переводят HTTP в вызовы сервисов с проверенным сотрудником.",
            "Routes only translate HTTP into service calls with a checked employee.",
        ),
        PINK,
    )
    story += [
        p(
            tr(
                "Без DATABASE_URL работает только /health, остальное отвечает 503. "
                "Запрос несёт Authorization: Bearer ewa_…; любая неудача входа — "
                "одинаковый 401 с WWW-Authenticate: Bearer. В таблице {id} — "
                "workflow_id.",
                "Without DATABASE_URL only /health works; everything else answers "
                "503. A request carries Authorization: Bearer ewa_…; any sign-in "
                "failure is the same 401 with WWW-Authenticate: Bearer. In the table "
                "{id} is the workflow_id.",
            )
        ),
        Spacer(1, 6),
        simple_table(
            [
                tr("Метод и адрес", "Method and path"),
                tr("Право", "Permission"),
                tr("Что делает", "Purpose"),
            ],
            [
                [
                    mono(f"{method} {path.replace('{workflow_id}', '{id}')}"),
                    mono(_ROUTE_TEXT[(method, path)][0]),
                    tr(*_ROUTE_TEXT[(method, path)][1:]),
                ]
                for method, path in _routes(facts)
            ],
            [W * 0.52, 62, W * 0.48 - 62],
            [CELL, CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        p(
            tr(
                "Команды процесса требуют Idempotency-Key (1–60 символов); сервер "
                "добавляет к нему ID сотрудника, и повтор возвращает прежний ответ с "
                'replayed: true. Тела строгие: лишнее поле или "2" вместо 2 — 422. '
                "Загрузка читается только до 1 МиБ, дальше — 413.",
                "Workflow commands need an Idempotency-Key (1–60 characters); the "
                "server prefixes it with the user ID, and a repeat returns the stored "
                "answer with replayed: true. Bodies are strict: an extra field or "
                '"2" instead of 2 is 422. Uploads are read only up to 1 MiB, then 413.',
            ),
            SMALL,
        ),
        p(
            tr(
                "Ошибки: одна форма, стабильные коды", "Errors: one shape, stable codes"
            ),
            H2,
        ),
        simple_table(
            ["HTTP", tr("Коды", "Codes")],
            [[status, mono(codes)] for status, codes in _error_rows(facts)],
            [50, W - 50],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 4),
        p(
            tr(
                'Тело ошибки: {"error": {"code", "message"}}; для устаревшего снимка '
                "добавляется reasons, для 422 — места ошибок без присланных значений.",
                'Error body: {"error": {"code", "message"}}; a stale snapshot adds '
                "reasons, a 422 lists locations without the submitted values.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Outbox и аудит решения",
        tr(
            "Событие для следующего шага пишется в той же транзакции, что и решение.",
            "The event for the next step is written in the decision's transaction.",
        ),
        PINK,
    )
    story += [
        p(
            tr(
                "Отправить сообщение прямо из команды нельзя: при сбое после отправки "
                "решение откатится, а сообщение уже уйдёт. Поэтому решение записывает "
                "событие в таблицу outbox_events, а доставит его отдельный обработчик.",
                "A command must not send a message itself: if it fails after sending, "
                "the decision rolls back but the message is already out. So the "
                "decision writes an event to outbox_events, and a separate worker "
                "will deliver it.",
            )
        ),
        Spacer(1, 6),
        outbox_commit(),
        Spacer(1, 6),
        simple_table(
            [tr("Событие", "Event"), tr("Когда", "When")],
            [[mono(kind), tr(*_OUTBOX_TEXT[kind])] for kind in facts.outbox_types],
            [130, W - 130],
            [CELL, CELL],
        ),
        Spacer(1, 4),
        p(
            tr(
                "В данных события только ID, версии, хеши, подразделение и автор, без "
                "текста документа; таблица только для добавления. Аудит решения "
                "ссылается на снимок сверки и на событие. Тест с искусственным сбоем "
                "после записи outbox подтверждает: не остаётся ничего.",
                "The payload holds only IDs, versions, hashes, the unit and the "
                "decider, never document text; the table is append-only. The "
                "decision's audit event references the snapshot and the event. A test "
                "that fails after the outbox insert shows that nothing remains.",
            ),
            SMALL,
        ),
        p(tr("Демонстрация", "Demonstration"), H2),
        code_block(
            "uv run --locked python -m app.decisions_demo\n"
            "uv run --locked python -m app.decisions_demo show WORKFLOW_ID"
        ),
        Spacer(1, 4),
        simple_table(
            [tr("Шаг", "Step"), tr("Результат", "Result")],
            [
                [
                    tr("Наблюдатель одобряет", "A viewer approves"),
                    mono("missing_permission"),
                ],
                [
                    tr(
                        "Согласующий другого подразделения",
                        "An approver of another unit",
                    ),
                    mono("not_found"),
                ],
                [
                    tr("Запустивший одобряет сам", "The starter approves"),
                    mono("self_approval_forbidden"),
                ],
                [
                    tr(
                        "Правила изменились после сверки",
                        "Rules changed after reconciling",
                    ),
                    mono("stale_reconciliation"),
                ],
                [
                    tr("Устаревшая версия процесса", "Stale workflow version"),
                    mono("version_conflict"),
                ],
                [
                    tr("Свежий matched-снимок", "Fresh matched snapshot"),
                    mono("approved") + tr(" + событие", " + event"),
                ],
                [
                    tr("Тот же запрос ещё раз", "The same request again"),
                    mono("replayed: true"),
                ],
                [
                    tr("Счёт с расхождением цены", "Invoice with a price difference"),
                    mono("reconciliation_not_matched"),
                ],
                [
                    tr("Отклонение этого счёта", "Rejecting that invoice"),
                    mono("rejected") + tr(" + событие", " + event"),
                ],
                [
                    tr(
                        "Отозванный токен, отключённый сотрудник",
                        "Revoked token, deactivated user",
                    ),
                    mono("unauthenticated"),
                ],
            ],
            [W * 0.55, W * 0.45],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Доставки событий нет: обработчик с повторами и подписанным sandbox "
                "webhook появится на этапе 09. Одобрение не запускает платёж.",
                "Events are not delivered: a worker with retries and a signed sandbox "
                "webhook arrives in Stage 09. Approval starts no payment.",
            ),
            TEAL,
            TEAL_L,
        ),
    ]
