"""Sections for stage 09: outbox delivery, redelivery, limits and evaluations."""

from typing import Any

from reportlab.platypus import Spacer

from guide.diagrams import delivery_path, redelivery
from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    CELL,
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

_STATUS_TEXT = {
    "pending": (
        "Попыток в этом круге ещё не было",
        "No attempt in this round yet",
    ),
    "retry_scheduled": (
        "Попытка не удалась; следующая в next_attempt_at",
        "An attempt failed; the next one is at next_attempt_at",
    ),
    "delivered": ("Получатель ответил 2xx", "The receiver answered 2xx"),
    "exhausted": (
        "Попытки кончились; оператор исправляет получателя и делает requeue",
        "Attempts ran out; an operator fixes the receiver and requeues",
    ),
}

_CATEGORY_TEXT = {
    "injection": (
        "Инструкция в тексте, модель выполняет поле, выдуманный итог",
        "Instruction in text, model obeying a field, invented total",
    ),
    "wrong_fields": (
        "Неверный итог, цена ниже заказа, запятая в сумме",
        "Wrong total, price below the order, comma decimal",
    ),
    "ambiguous_data": (
        "Два итога, нет валюты, один счёт в двух процессах",
        "Two totals, no currency, one invoice in two workflows",
    ),
    "provider_failure": (
        "Ошибка и отказ модели в агенте и при извлечении",
        "Model error and refusal in the agent and in extraction",
    ),
    "timeout": (
        "Медленная модель, PDF-процесс, получатель",
        "Slow model, PDF worker, receiver",
    ),
    "retry": (
        "Повтор одобрения, потерянный ответ, исчерпание, чужой секрет",
        "Replayed approval, lost answer, exhaustion, wrong secret",
    ),
    "concurrency": (
        "Два одобрения и два обработчика одновременно",
        "Two approvals and two workers at once",
    ),
    "malicious_document": (
        "PNG, управляющие символы, размер, PDF с паролем, битый, раздутый",
        "PNG, control characters, size, encrypted, broken, expanding PDF",
    ),
    "access": (
        "Чужое подразделение, наблюдатель, квота помощника, размер тела",
        "Another unit, viewer, assistant quota, body size",
    ),
}


def _described(values: tuple[str, ...], texts: dict[str, tuple[str, str]]) -> None:
    """Stop the build if the code gained a value the guide does not explain."""
    if set(values) != set(texts):
        raise ValueError(f"Describe exactly these values: {sorted(values)}")


def add_stage09(story: list[Any], facts: Facts) -> None:
    """Sections 31–33: delivery, failures and redelivery, limits and evaluations."""
    _described(facts.delivery_statuses, _STATUS_TEXT)
    _described(tuple(name for name, _ in facts.eval_categories), _CATEGORY_TEXT)
    policy = facts.delivery_policy
    section(
        story,
        "Доставка outbox",
        tr(
            "Подписанный запрос одному локальному получателю, с повторами.",
            "A signed request to one local receiver, with retries.",
        ),
        TEAL,
    )
    story += [
        p(
            tr(
                "Решение пишет событие и строку доставки pending в одной транзакции. "
                "Обработчик берёт по одному событию, подписывает канонический JSON, "
                "отправляет и записывает попытку до COMMIT. Получатель — вымышленная "
                "«следующая система» на 127.0.0.1: она не импортирует app и проверяет "
                "подпись своим кодом.",
                "The decision writes the event and a pending delivery in one "
                "transaction. The worker takes one event at a time, signs canonical "
                "JSON, sends it and records the attempt before COMMIT. The receiver is "
                "a fictional next system on 127.0.0.1: it imports nothing from app and "
                "checks the signature with its own code.",
            )
        ),
        Spacer(1, 6),
        delivery_path(),
        Spacer(1, 6),
        simple_table(
            [tr("Статус", "Status"), tr("Значение", "Meaning")],
            [
                [mono(status), tr(*_STATUS_TEXT[status])]
                for status in facts.delivery_statuses
            ],
            [W * 0.3, W * 0.7],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        code_block(
            "export SANDBOX_WEBHOOK_SECRET=\"$(python -c 'import secrets; "
            "print(secrets.token_urlsafe(32))')\"\n"
            "uv run --locked python -m sandbox.receiver\n"
            "uv run --locked python -m app.outbox_worker run\n"
            "uv run --locked python -m app.outbox_worker status\n"
            "uv run --locked python -m app.outbox_worker requeue EVENT_ID"
        ),
        Spacer(1, 6),
        callout(
            tr(
                f"Попыток {policy.max_attempts}, задержки от "
                f"{policy.base_delay_seconds:g} с вдвое до {policy.max_delay_seconds:g} "
                f"с, таймаут {policy.timeout_seconds:g} с; без случайного разброса, "
                "чтобы тесты были воспроизводимы. Адрес — только SANDBOX_WEBHOOK_URL "
                "на IP 127.0.0.1 или [::1]; имя localhost не принимается.",
                f"{policy.max_attempts} attempts, delays from "
                f"{policy.base_delay_seconds:g} s doubling up to "
                f"{policy.max_delay_seconds:g} s, a {policy.timeout_seconds:g} s "
                "timeout; no jitter, so tests are reproducible. The address is only "
                "SANDBOX_WEBHOOK_URL on the IP 127.0.0.1 or [::1]; the name "
                "localhost is refused.",
            ),
            TEAL,
            TEAL_L,
        ),
    ]

    section(
        story,
        "Сбои и повторная доставка",
        tr(
            "Событие не теряется; повтор не создаёт второго действия.",
            "No event is lost; a repeat creates no second action.",
        ),
        TEAL,
    )
    story += [
        p(
            tr(
                "Строка доставки заблокирована, пока идёт отправка: второй обработчик "
                "её пропускает (SKIP LOCKED). Если процесс упал до COMMIT, попытка "
                "откатывается и событие снова ждёт. Получатель мог уже выполнить "
                "действие, поэтому он сам узнаёт повтор по ID события.",
                "The delivery row stays locked while sending: a second worker skips "
                "it (SKIP LOCKED). If the process dies before COMMIT, the attempt rolls "
                "back and the event waits again. The receiver may have acted already, "
                "so it recognises the repeat by event ID itself.",
            )
        ),
        Spacer(1, 6),
        redelivery(),
        Spacer(1, 6),
        simple_table(
            [
                tr("Сценарий app.outbox_demo", "app.outbox_demo scenario"),
                tr("Попытки", "Attempts"),
                tr("Действий", "Actions"),
            ],
            [
                [
                    tr("Получатель недоступен дважды", "Receiver down twice"),
                    mono("503, 503, 200"),
                    "1",
                ],
                [
                    tr("Ответ потерян после действия", "Answer lost after acting"),
                    mono("503, 200 duplicate"),
                    "1",
                ],
                [
                    tr(
                        "Таймаут, получатель ещё работал",
                        "Timeout, receiver still busy",
                    ),
                    mono("timeout, 200 duplicate"),
                    "1",
                ],
                [
                    tr(
                        "Чужой секрет, затем верный", "Wrong secret, then the right one"
                    ),
                    mono("401, 200"),
                    "1",
                ],
                [
                    tr("Попытки кончились, requeue", "Attempts exhausted, requeue"),
                    mono("503 ×4 → exhausted, 200"),
                    "1",
                ],
                [
                    tr("Два обработчика, шесть событий", "Two workers, six events"),
                    mono("6"),
                    "6",
                ],
            ],
            [W * 0.46, W * 0.38, W * 0.16],
            [CELL, CELL, CELL],
            padding=2.5,
        ),
        p(tr("Подпись", "Signature"), H2),
        simple_table(
            [tr("Заголовок", "Header"), tr("Содержимое", "Contents")],
            [
                [
                    mono("X-EWA-Event-Id"),
                    tr("ID события, как в теле", "Event ID, as in the body"),
                ],
                [
                    mono("X-EWA-Timestamp"),
                    tr("Секунды Unix; окно ±300 с", "Unix seconds; ±300 s window"),
                ],
                [
                    mono("X-EWA-Signature"),
                    mono("v1=HMAC-SHA256(secret, timestamp.body)"),
                ],
            ],
            [W * 0.3, W * 0.7],
            [CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 4),
        p(
            tr(
                "Получатель отказывает: 413 тело больше 64 КиБ, 401 stale_timestamp или "
                "bad_signature, 400 invalid_body, 409 event_id_reused. Секрет и тело в "
                "журнал попыток не попадают.",
                "The receiver refuses: 413 body over 64 KiB, 401 stale_timestamp or "
                "bad_signature, 400 invalid_body, 409 event_id_reused. The secret and "
                "the body never reach the attempt log.",
            ),
            SMALL,
        ),
    ]

    limits = facts.limits
    agent = facts.agent_limits
    section(
        story,
        "Ограничения и оценки",
        tr(
            "Лимиты ресурсов и 31 сценарий с заранее записанным итогом.",
            "Resource limits and 31 cases with outcomes written in advance.",
        ),
        TEAL,
    )
    story += [
        simple_table(
            [
                tr("Ограничение", "Limit"),
                tr("Значение", "Value"),
                tr("Отказ", "Refusal"),
            ],
            [
                [
                    tr("Процессор PDF-процесса", "PDF worker CPU"),
                    tr(
                        f"{limits.worker_cpu_seconds} с (RLIMIT_CPU)",
                        f"{limits.worker_cpu_seconds} s (RLIMIT_CPU)",
                    ),
                    mono("resource_limit"),
                ],
                [
                    tr("Память PDF-процесса", "PDF worker memory"),
                    tr(
                        f"{limits.worker_memory_bytes // 1_048_576} МиБ; только Linux",
                        f"{limits.worker_memory_bytes // 1_048_576} MiB; Linux only",
                    ),
                    mono("resource_limit"),
                ],
                [
                    tr("Текст PDF", "PDF text"),
                    tr(
                        f"{limits.max_text_chars} символов, внутри процесса",
                        f"{limits.max_text_chars} characters, inside the worker",
                    ),
                    mono("text_too_long"),
                ],
                [
                    tr("Тело команды API", "API command body"),
                    tr(
                        f"{facts.json_body_limit // 1024} КиБ, нужен Content-Length",
                        f"{facts.json_body_limit // 1024} KiB, Content-Length required",
                    ),
                    mono("413 / 411"),
                ],
                [
                    tr("Запуски помощника", "Assistant runs"),
                    tr(
                        f"{agent.max_runs_per_hour} на процесс в час",
                        f"{agent.max_runs_per_hour} per workflow per hour",
                    ),
                    mono("429 agent_run_limit"),
                ],
            ],
            [W * 0.3, W * 0.42, W * 0.28],
            [CELL, CELL, CELL],
            padding=2.5,
        ),
        p(tr("Набор оценок", "Evaluation suite"), H2),
        simple_table(
            [
                tr("Категория", "Category"),
                tr("Случаев", "Cases"),
                tr("Что проверяет", "What it probes"),
            ],
            [
                [mono(name), str(count), tr(*_CATEGORY_TEXT[name])]
                for name, count in facts.eval_categories
            ],
            [W * 0.27, W * 0.1, W * 0.63],
            [CELL, CELL, CELL],
            padding=2.5,
        ),
        Spacer(1, 6),
        code_block(
            "uv run --locked python -m app.evals list\n"
            "uv run --locked python -m app.evals run   # var/evals/eval-*.json, *.md"
        ),
        Spacer(1, 6),
        callout(
            tr(
                "Сценарий пройден, только если наблюдение целиком равно ожиданию. "
                "Провайдеры детерминированные, набор маленький и написан авторами кода, "
                "всё на одной машине. Успех набора — не заявление о готовности к "
                "эксплуатации; отчёт всегда перечисляет эти ограничения.",
                "A case passes only if the observation equals the expectation exactly. "
                "Providers are deterministic, the set is small and written by the "
                "code's authors, everything runs on one machine. Passing the suite is "
                "not a claim of production readiness; the report always lists these "
                "limits.",
            ),
            AMBER,
            AMBER_L,
        ),
    ]
