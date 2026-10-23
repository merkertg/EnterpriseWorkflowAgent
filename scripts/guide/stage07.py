"""Sections for stage 07: the bounded agent, its tools and the draft check."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import agent_loop, injection_path
from guide.i18n import tr
from guide.layout import (
    AMBER,
    CELL,
    H2,
    RED,
    RED_L,
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

_TOOL_TEXT = {
    "get_workflow": (
        "Состояние, версия, подразделение, коды проверки, автор решения",
        "State, version, unit, check codes, decider",
    ),
    "get_reconciliation": (
        "Последняя сверка: статус, свежесть, расхождения; строки по флагу",
        "Latest reconciliation: status, freshness, discrepancies; lines on request",
    ),
    "get_extracted_fields": (
        "Поля документа; значения — данные, не длиннее 200 символов",
        "Document fields; values are data, at most 200 characters",
    ),
    "get_audit_trail": (
        "Последние 20 событий: команда, состояние, сотрудник",
        "Last 20 events: command, state, employee",
    ),
}

_LIMIT_TEXT = (
    ("max_provider_calls", "provider_call_limit"),
    ("max_tool_calls", "tool_call_limit"),
    ("max_refused_calls", "too_many_refusals"),
    ("provider_timeout_seconds", "provider_timeout"),
    ("tool_timeout_seconds", "tool_timeout"),
    ("max_result_bytes", "result_too_large"),
)


def _tools(facts: Facts) -> list[tuple[str, tuple[str, ...]]]:
    """Return the tools; fail the build if one lacks a description."""
    if {name for name, _ in facts.agent_tools} != set(_TOOL_TEXT):
        raise ValueError("Describe every agent tool in _TOOL_TEXT.")
    return list(facts.agent_tools)


def add_stage07(story: list[Any], facts: Facts) -> None:
    """Sections 25–27: the loop and limits, tools and refusals, draft and trace."""
    section(
        story,
        "Агент и его границы",
        tr(
            "Модель предлагает вызовы и черновик; всё остальное решает код.",
            "The model proposes calls and a draft; code decides everything else.",
        ),
        AMBER,
    )
    story += [
        p(
            tr(
                "Агент помогает сотруднику понять процесс: читает данные через "
                "инструменты и пишет объяснение и черновик следующего шага. Модель "
                "спрятана за портом ModelProvider: ход — это вызовы инструментов, "
                "итоговый ответ или отказ, а результаты возвращаются по ID вызова. "
                "Черновик никогда не меняет процесс.",
                "The agent helps an employee understand a workflow: it reads data "
                "through tools and writes an explanation and a draft next step. The "
                "model sits behind the ModelProvider port: a turn is tool calls, a "
                "final answer or a refusal, and results return by call ID. A draft "
                "never changes the workflow.",
            )
        ),
        Spacer(1, 6),
        agent_loop(),
        Spacer(1, 6),
        simple_table(
            [
                tr("Лимит AgentLimits", "AgentLimits field"),
                tr("По умолчанию", "Default"),
                tr("При превышении", "When exceeded"),
            ],
            [
                [mono(name), str(getattr(facts.agent_limits, name)), mono(outcome)]
                for name, outcome in _LIMIT_TEXT
            ],
            [190, 90, W - 280],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 6),
        p(
            tr(
                "Вызовы модели и инструментов идут в отдельном потоке с таймаутом; "
                "зависший поток не убивается, запуск просто перестаёт его ждать.",
                "Provider and tool calls run in a worker thread with a timeout; a "
                "stuck thread is not killed, the run just stops waiting for it.",
            ),
            SMALL,
        ),
        Spacer(1, 6),
        callout(
            tr(
                "<b>Без сети и без ключа.</b> Работают два подставных провайдера: "
                "demo-rules/1 — правило вместо модели (HTTP API и демонстрация) и "
                "ScriptedProvider — заранее заданные ходы, которыми тесты изображают "
                "плохо ведущую себя модель. Настоящая LLM не подключена.",
                "<b>No network, no key.</b> Two substitute providers run: "
                "demo-rules/1, a rule standing in for a model (HTTP API and demo), "
                "and ScriptedProvider, fixed turns that tests use to play a "
                "misbehaving model. No real LLM is connected.",
            ),
            TEAL,
            TEAL_L,
        ),
    ]

    section(
        story,
        "Инструменты и отказы",
        tr(
            "Четыре инструмента чтения; неверный вызов получает код, а не действие.",
            "Four read-only tools; a bad call gets a code, not an action.",
        ),
        AMBER,
    )
    story += [
        simple_table(
            [
                tr("Инструмент", "Tool"),
                tr("Аргументы", "Arguments"),
                tr("Что возвращает", "Returns"),
            ],
            [
                [mono(name), mono(", ".join(arguments)), tr(*_TOOL_TEXT[name])]
                for name, arguments in _tools(facts)
            ],
            [130, 145, W - 275],
            [CELL, CELL, CELL],
        ),
        Spacer(1, 6),
        p(
            tr(
                "Схемы строгие: workflow_id обязателен, лишние ключи запрещены, "
                "строки ограничены, true не считается числом. Инструмент принимает "
                "только процесс запуска и в своей транзакции заново проверяет право "
                "view сотрудника. Запуск требует нового права assist (clerk, approver). "
                "Инструментов одобрения, отклонения, платежа и выполнения кода нет.",
                "Schemas are strict: workflow_id is required, extra keys are refused, "
                "strings are bounded, true is not a number. A tool accepts only the "
                "run's workflow and re-checks the employee's view permission in its "
                "own transaction. Starting a run needs the new assist permission "
                "(clerk, approver). There is no approve, reject, pay or code tool.",
            )
        ),
        Spacer(1, 4),
        simple_table(
            [tr("Код отказа", "Refusal code"), tr("Когда", "When")],
            [
                [mono("unknown_tool"), tr("Такого инструмента нет", "No such tool")],
                [
                    mono("invalid_arguments"),
                    tr(
                        "Лишний ключ, неверный тип, длина, нет workflow_id",
                        "Extra key, wrong type or length, no workflow_id",
                    ),
                ],
                [mono("out_of_scope"), tr("Другой процесс", "Another workflow")],
                [
                    mono("not_found / missing_permission"),
                    tr(
                        "Права сотрудника не позволяют",
                        "The employee's rights forbid it",
                    ),
                ],
                [
                    mono("result_too_large"),
                    tr("Результат больше лимита байт", "Result above the byte limit"),
                ],
                [
                    mono("invalid_call, tool_failed, timeout"),
                    tr("Сбой вызова", "A failed call"),
                ],
            ],
            [215, W - 215],
            [CELL, CELL],
            padding=2.5,
        ),
        p(tr("Инструкция в документе", "An instruction inside a document"), H2),
        injection_path(),
        Spacer(1, 4),
        p(
            tr(
                "Защита не опирается на послушание модели: даже подчинившись тексту, "
                "она не может вызвать то, чего нет в списке инструментов.",
                "The protection does not rely on the model obeying: even when it "
                "follows the text, it cannot call what is not in the tool list.",
            ),
            SMALL,
        ),
    ]

    section(
        story,
        "Черновик и трассировка",
        tr(
            "Черновик сверяется с фактами; в базе остаются коды и хеши, не текст.",
            "The draft is checked against facts; the database keeps codes and hashes.",
        ),
        AMBER,
    )
    story += [
        p(
            tr(
                "Итоговый ответ — JSON: explanation (до 2000 символов), next_step из "
                f"списка {', '.join(facts.next_steps)} и cited_codes. Функция "
                "review_draft сверяет его с сохранёнными фактами и может только "
                "понизить шаг до needs_human_review.",
                "The final answer is JSON: explanation (up to 2000 characters), "
                f"next_step from {', '.join(facts.next_steps)} and cited_codes. "
                "review_draft checks it against stored facts and can only lower the "
                "step to needs_human_review.",
            )
        ),
        Spacer(1, 4),
        simple_table(
            [tr("Условие", "Condition"), tr("Причина замены", "Override reason")],
            [
                [tr("Процесс закрыт", "Workflow closed"), mono("workflow_closed")],
                [
                    tr(
                        "Код в cited_codes не из фактов",
                        "A cited code is not in the facts",
                    ),
                    mono("unsupported_citation"),
                ],
                [
                    tr(
                        "ready_for_approval, но не awaiting_approval",
                        "ready_for_approval, not awaiting_approval",
                    ),
                    mono("workflow_not_awaiting_approval"),
                ],
                [
                    tr(
                        "ready_for_approval без сверки",
                        "ready_for_approval without reconciliation",
                    ),
                    mono("no_reconciliation"),
                ],
                [
                    tr(
                        "ready_for_approval при устаревшей сверке",
                        "ready_for_approval on a stale reconciliation",
                    ),
                    mono("reconciliation_stale"),
                ],
                [
                    tr(
                        "ready_for_approval при расхождениях",
                        "ready_for_approval with discrepancies",
                    ),
                    mono("reconciliation_not_matched"),
                ],
            ],
            [W - 200, 200],
            [CELL, CELL],
            padding=2.5,
        ),
        p(tr("Трассировка", "Trace"), H2),
        simple_table(
            [
                tr("Хранится (agent_runs, agent_steps)", "Stored"),
                tr("Не хранится", "Not stored"),
            ],
            [
                [
                    tr(
                        f"Шаги {', '.join(facts.step_kinds)}; инструмент, код ошибки, "
                        "размер, длительность, SHA-256 аргументов и результата",
                        f"Steps {', '.join(facts.step_kinds)}; tool, error code, size, "
                        "duration, SHA-256 of arguments and result",
                    ),
                    tr(
                        "Инструкции модели, текст документов, значения полей",
                        "Model instructions, document text, field values",
                    ),
                ],
                [
                    tr(
                        "Провайдер, причина остановки, счётчики, предложенный и "
                        "показанный шаг, SHA-256 и длина объяснения",
                        "Provider, stop reason, counters, proposed and shown step, "
                        "SHA-256 and length of the explanation",
                    ),
                    tr(
                        "Само объяснение: оно возвращается один раз",
                        "The explanation itself: it is returned once",
                    ),
                ],
            ],
            [W / 2, W / 2],
            [CELL, CELL],
        ),
        p(tr("Поля от модели", "Fields from a model"), H2),
        simple_table(
            [
                tr("Ответ ModelFieldExtractor", "ModelFieldExtractor answer"),
                tr("Итог", "Outcome"),
            ],
            [
                [
                    tr("Значения совпадают с цитатами", "Values match their citations"),
                    mono("complete"),
                ],
                [
                    tr(
                        "Выдуманная сумма 3.00 вместо 30.00",
                        "A made-up total 3.00 instead of 30.00",
                    ),
                    mono("needs_review: unverified_evidence"),
                ],
                [
                    tr("Не по схеме, отказ, таймаут", "Off-schema, refusal, timeout"),
                    mono("needs_review: missing_field"),
                ],
            ],
            [W - 215, 215],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        code_block(
            "uv run --locked python -m app.agent_demo\n"
            "uv run --locked python -m app.agent_demo show RUN_ID\n"
            "POST /workflows/{id}/agent-runs   GET /agent-runs/{run_id}"
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Даже принятый ready_for_approval ничего не одобряет: решение — "
                "команда сотрудника с проверками этапа 06.",
                "Even an accepted ready_for_approval approves nothing: the decision "
                "is an employee's command with the Stage 06 checks.",
            ),
            RED,
            RED_L,
        ),
    ]
