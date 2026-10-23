"""Diagrams drawn on the ReportLab canvas; texts follow the build language."""

from reportlab.pdfbase import pdfmetrics

from guide.i18n import tr
from guide.layout import (
    AMBER,
    AMBER_L,
    BLUE,
    BLUE_L,
    GREEN,
    GREEN_L,
    INK,
    MUTED,
    ORANGE,
    ORANGE_L,
    PINK,
    PINK_L,
    RED,
    RED_L,
    SLATE_L,
    TEAL,
    TEAL_L,
    VIOLET,
    VIOLET_L,
    WHITE,
    Canvas,
    Diagram,
    arrow,
    chip,
    circle_num,
    draw_text,
    lines_in,
    node,
    rbox,
)

STAGES_DONE = 6


def roadmap() -> Diagram:
    """Ten stages as tiles: implemented ones coloured, planned ones grey."""
    stages = [
        ("01", tr("Основа API", "API base")),
        ("02", tr("Домен", "Domain")),
        ("03", tr("PostgreSQL", "PostgreSQL")),
        ("04", tr("Документы", "Documents")),
        ("05", tr("Сверка", "Reconcile")),
        ("06", tr("Решение", "Decision")),
        ("07", tr("AI-агент", "AI agent")),
        ("08", "Streamlit"),
        ("09", tr("Устойчивость", "Hardening")),
        ("10", tr("Выпуск", "Release")),
    ]

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw two rows of five tiles with a legend underneath."""
        gap = 8
        tile_w = (w - gap * 4) / 5
        tile_h = 44
        for index, (number, label) in enumerate(stages):
            row, column = divmod(index, 5)
            x = column * (tile_w + gap)
            y = h - 4 - (row + 1) * tile_h - row * gap
            done = index < STAGES_DONE
            rbox(
                c,
                x,
                y,
                tile_w,
                tile_h,
                TEAL_L if done else SLATE_L,
                TEAL if done else None,
            )
            c.setFillColor(TEAL if done else MUTED)
            c.setFont("PT-Bold", 13)
            c.drawString(x + 8, y + tile_h - 18, number)
            c.setFont("PT", 9)
            c.setFillColor(INK if done else MUTED)
            c.drawString(x + 8, y + 9, label)
        c.setFillColor(MUTED)
        c.setFont("PT", 8.4)
        c.drawString(
            0,
            2,
            tr(
                f"Цветные — выполнены и проверены локально (01–{STAGES_DONE:02d}); "
                "серые — запланированы.",
                f"Coloured: implemented and verified locally (01–{STAGES_DONE:02d}); "
                "grey: planned.",
            ),
        )

    return Diagram(118, draw)


def layers() -> Diagram:
    """Dependency direction: adapters point inwards to the pure domain."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw adapters on top, application in the middle, domain at the bottom."""
        box_h = 46
        third = (w - 24) / 3
        top = h - box_h - 4
        node(
            c,
            0,
            top,
            third,
            box_h,
            "CLI / HTTP",
            tr("CLI, HTTP API с токеном", "CLIs, HTTP API with token"),
            VIOLET_L,
            VIOLET,
        )
        node(
            c,
            third + 12,
            top,
            third,
            box_h,
            "infrastructure/db",
            tr("SQLAlchemy, PostgreSQL", "SQLAlchemy, PostgreSQL"),
            ORANGE_L,
            ORANGE,
        )
        node(
            c,
            2 * third + 24,
            top,
            third,
            box_h,
            tr("infrastructure/документы", "infrastructure/documents"),
            tr("pypdf-процесс, парсер", "pypdf worker, parser"),
            ORANGE_L,
            ORANGE,
        )
        mid = top - box_h - 30
        node(
            c,
            w * 0.18,
            mid,
            w * 0.64,
            box_h,
            "application",
            tr(
                "сервисы, права доступа, порты",
                "services, access rules, ports",
            ),
            BLUE_L,
            BLUE,
        )
        low = mid - box_h - 30
        node(
            c,
            w * 0.18,
            low,
            w * 0.64,
            box_h,
            "domain",
            tr(
                "деньги, правила, переходы, схема полей — только stdlib",
                "money, rules, transitions, field schema — stdlib only",
            ),
            GREEN_L,
            GREEN,
        )
        for x in (third / 2, third + 12 + third / 2, 2 * third + 24 + third / 2):
            arrow(c, x, top, w / 2 + (x - w / 2) * 0.55, mid + box_h + 2)
        arrow(c, w / 2, mid, w / 2, low + box_h + 2)
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.6)
        c.drawString(
            w * 0.84,
            mid + box_h / 2 - 3,
            tr("стрелка = «импортирует»", "arrow = imports"),
        )

    return Diagram(196, draw)


def states() -> Diagram:
    """The five workflow states and the commands between them."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw two rows of states; labels sit beside arrows, never on them."""
        bh = 38
        top = h - bh - 6
        bottom = 30
        middle = (top + bottom) / 2
        left_w, mid_w, right_w = 100, 128, 104
        mid_x = w * 0.34
        right_x = w - right_w
        node(c, 0, middle, left_w, bh, "received", "", BLUE_L, BLUE, 10)
        node(c, mid_x, top, mid_w, bh, "needs_review", "", AMBER_L, AMBER, 10)
        node(c, mid_x, bottom, mid_w, bh, "awaiting_approval", "", TEAL_L, TEAL, 9.5)
        node(c, right_x, top, right_w, bh, "rejected", "", RED_L, RED, 10)
        node(c, right_x, bottom, right_w, bh, "approved", "", GREEN_L, GREEN, 10)
        arrow(c, left_w, middle + bh * 0.8, mid_x - 2, top + bh / 2)
        arrow(c, left_w, middle + bh * 0.2, mid_x - 2, bottom + bh / 2)
        c.setFillColor(MUTED)
        c.setFont("PT", 8.2)
        c.drawString(
            0, middle + bh + 8, tr("check: есть причины", "check: issues found")
        )
        c.drawString(0, middle - 14, tr("check: чисто", "check: clean"))
        arrow(
            c, mid_x + mid_w + 2, bottom + bh / 2, right_x - 2, bottom + bh / 2, GREEN
        )
        c.setFillColor(GREEN)
        c.drawCentredString(
            (mid_x + mid_w + right_x) / 2,
            bottom + bh / 2 + 5,
            tr("approve + решение", "approve + decision"),
        )
        arrow(c, mid_x + mid_w + 2, top + bh / 2, right_x - 2, top + bh / 2, RED)
        arrow(
            c,
            mid_x + mid_w + 2,
            bottom + bh - 4,
            right_x - 2,
            top + 4,
            RED,
            dash=(3, 2),
        )
        c.setFillColor(RED)
        c.drawCentredString(
            (mid_x + mid_w + right_x) / 2,
            top + bh / 2 + 5,
            tr("reject + причина", "reject + reason"),
        )
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.2)
        c.drawString(
            0,
            6,
            tr(
                "reject и revise доступны в любом открытом состоянии; revise "
                "возвращает в received. approved и rejected — конечные.",
                "reject and revise work in any open state; revise returns to "
                "received. approved and rejected are terminal.",
            ),
        )

    return Diagram(178, draw)


def transaction_steps() -> Diagram:
    """The ordered writes of one command inside one transaction."""
    steps = [
        tr("Найти ключ идемпотентности", "Look up the idempotency key"),
        tr("Прочитать процесс, сравнить версию", "Read workflow, compare version"),
        tr("Вычислить новый снимок (домен)", "Compute new snapshot (domain)"),
        tr("Записать ключ с результатом", "Insert key with its result"),
        tr("Записать снимок проверки", "Insert validation snapshot"),
        tr("UPDATE ... WHERE version = N", "UPDATE ... WHERE version = N"),
        tr("Добавить событие аудита", "Append audit event"),
        "COMMIT",
    ]

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw two columns of numbered steps inside a transaction frame."""
        rbox(c, 0, 0, w, h, WHITE, BLUE, r=10, lw=1.2, dash=(4, 3))
        c.setFillColor(BLUE)
        c.setFont("PT-Bold", 9)
        c.drawString(
            12, h - 16, tr("Одна транзакция PostgreSQL", "One PostgreSQL transaction")
        )
        column_w = (w - 36) / 2
        for index, text in enumerate(steps):
            column, row = divmod(index, 4)
            x = 12 + column * (column_w + 12)
            y = h - 46 - row * 30
            fill = GREEN if text == "COMMIT" else BLUE
            circle_num(c, x + 10, y + 8, 10, str(index + 1), fill)
            c.setFillColor(INK)
            c.setFont("PT", 9.5)
            c.drawString(x + 26, y + 4, text)
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.4)
        c.drawString(
            12,
            8,
            tr(
                "Ошибка на любом шаге — ROLLBACK: в базе не остаётся ни одной из записей.",
                "A failure at any step rolls back: none of the writes remain.",
            ),
        )

    return Diagram(172, draw)


def document_pipeline() -> Diagram:
    """From bytes to an invoice version or a review queue."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw five stages and the two possible outcomes."""
        items = [
            (
                tr("Байты", "Bytes"),
                tr("до 1 МиБ, формат по байтам", "up to 1 MiB, format from bytes"),
                SLATE_L,
                MUTED,
            ),
            (
                tr("Страницы", "Pages"),
                tr("UTF-8 или PDF-процесс", "UTF-8 or PDF worker"),
                ORANGE_L,
                ORANGE,
            ),
            (
                tr("Поля", "Fields"),
                tr("извлекатель предлагает", "extractor proposes"),
                VIOLET_L,
                VIOLET,
            ),
            (
                tr("Оценка", "Assessment"),
                tr("цитаты, схема, форматы", "evidence, schema, formats"),
                BLUE_L,
                BLUE,
            ),
        ]
        gap = 14
        bw = (w - gap * 3) / 4
        bh = 52
        y = h - bh - 4
        for index, (title, sub, fill, edge) in enumerate(items):
            x = index * (bw + gap)
            node(c, x, y, bw, bh, title, sub, fill, edge, 10, 7.8)
            if index:
                arrow(c, x - gap + 1, y + bh / 2, x - 1, y + bh / 2)
        out_w = (w - gap) / 2
        low = 8
        node(
            c,
            0,
            low,
            out_w,
            44,
            "complete",
            tr(
                "Invoice → версия счёта → процесс",
                "Invoice → invoice version → workflow",
            ),
            GREEN_L,
            GREEN,
            10,
            7.8,
        )
        node(
            c,
            out_w + gap,
            low,
            out_w,
            44,
            "needs_review",
            tr("список проблем, счёта нет", "problem list, no invoice"),
            AMBER_L,
            AMBER,
            10,
            7.8,
        )
        last_x = 3 * (bw + gap) + bw / 2
        arrow(c, last_x - 8, y, out_w / 2 + 40, low + 46, GREEN)
        arrow(c, last_x + 4, y, out_w + gap + out_w / 2, low + 46, AMBER)
        c.setFillColor(RED)
        c.setFont("PT", 8.2)
        lines_in(
            c,
            bw * 0.5 + 0,
            y - 6,
            bw + 20,
            tr("отказ → ничего не сохраняется", "refusal → nothing is stored"),
            "PT",
            8.2,
            10,
            RED,
        )

    return Diagram(150, draw)


def pdf_worker() -> Diagram:
    """The parent process, the isolated worker and the timeout."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw the parent and the worker with stdin, stdout and a kill path."""
        bw, bh = w * 0.30, 78
        y = h - bh - 6
        node(
            c,
            0,
            y,
            bw,
            bh,
            tr("Приложение", "Application"),
            tr(
                "LimitedTextExtractor\nждёт не дольше 5 с",
                "LimitedTextExtractor\nwaits at most 5 s",
            ),
            BLUE_L,
            BLUE,
            10.5,
            8.4,
        )
        node(
            c,
            w - bw,
            y,
            bw,
            bh,
            "python -I pdf_worker.py",
            tr(
                "только pypdf,\nбез кода приложения", "pypdf only,\nno application code"
            ),
            ORANGE_L,
            ORANGE,
            10,
            8.4,
            sub_font="PT",
        )
        arrow(c, bw + 4, y + bh * 0.70, w - bw - 4, y + bh * 0.70, INK)
        arrow(c, w - bw - 4, y + bh * 0.30, bw + 4, y + bh * 0.30, INK)
        c.setFillColor(INK)
        c.setFont("Mono", 8)
        c.drawCentredString(
            w / 2, y + bh * 0.70 + 6, tr("stdin: байты PDF", "stdin: PDF bytes")
        )
        c.drawCentredString(w / 2, y + bh * 0.30 + 6, 'stdout: {"status": "ok", ...}')
        # draw_text switches arrows to PT Mono; PT Sans has no arrow glyphs.
        c.setFillColor(RED)
        draw_text(
            c,
            w / 2,
            16,
            tr(
                "Нет ответа за timeout → процесс убит → processing_timeout",
                "No answer within timeout → process killed → processing_timeout",
            ),
            "PT",
            8.6,
            "center",
        )
        c.setFillColor(MUTED)
        draw_text(
            c,
            w / 2,
            3,
            tr(
                "Неожиданный ответ → malformed_pdf", "Unexpected answer → malformed_pdf"
            ),
            "PT",
            8.6,
            "center",
        )

    return Diagram(122, draw)


def reconciliation_checks() -> Diagram:
    """The four groups of checks and the codes each group can produce."""
    groups = [
        (
            tr("Входы", "Inputs"),
            tr(
                "документ, дубликат, правила, заказ",
                "document, duplicate, rules, order",
            ),
            [
                "missing_source_document",
                "duplicate_invoice",
                "no_rule_for_currency",
                "missing_purchase_order",
            ],
            AMBER_L,
            AMBER,
        ),
        (
            tr("Заголовок", "Header"),
            tr("точное совпадение", "exact match"),
            [
                "purchase_order_reference_mismatch",
                "supplier_mismatch",
                "currency_mismatch",
            ],
            BLUE_L,
            BLUE,
        ),
        (
            tr("Строки по SKU", "Lines by SKU"),
            tr("количество и цена единицы", "quantity and unit price"),
            [
                "unknown_line",
                "quantity_exceeds_order",
                "quantity_below_order",
                "unit_price_mismatch",
            ],
            VIOLET_L,
            VIOLET,
        ),
        (
            tr("Итоги", "Totals"),
            tr("допуск на итог", "total tolerance"),
            ["declared_total_mismatch", "expected_total_mismatch"],
            TEAL_L,
            TEAL,
        ),
    ]

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw one band per group: a title box and its code chips."""
        band_h, gap, label_w = 40, 8, 150
        for index, (title, sub, codes, fill, edge) in enumerate(groups):
            y = h - (index + 1) * band_h - index * gap
            node(c, 0, y, label_w, band_h, title, sub, fill, edge, 9.5, 7.6)
            x, row_y = label_w + 12, y + band_h - 17
            for code in codes:
                width = pdfmetrics.stringWidth(code, "Mono", 7.6) + 8
                if x + width > w:
                    x, row_y = label_w + 12, row_y - 17
                chip(c, x, row_y, code, fill, INK)
                x += width + 6
            if index < len(groups) - 1:
                arrow(c, label_w / 2, y - 1, label_w / 2, y - gap + 1)

    return Diagram(4 * 40 + 3 * 8 + 4, draw)


def snapshot_lifecycle() -> Diagram:
    """A snapshot goes stale when inputs change; a new one becomes current."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw inputs, the first snapshot, the change and the new snapshot."""
        bw, bh = (w - 3 * 22) / 4, 66
        y = h - bh - 4
        boxes = [
            (
                tr("Входы", "Inputs"),
                tr(
                    "счёт v1, заказ v1,\nправила v1, документ, поля",
                    "invoice v1, order v1,\nrules v1, document, fields",
                ),
                SLATE_L,
                MUTED,
            ),
            ("S1", tr("matched, актуален", "matched, current"), GREEN_L, GREEN),
            (
                "S1",
                tr(
                    "устарел: purchase_order_changed,\nrules_changed",
                    "stale: purchase_order_changed,\nrules_changed",
                ),
                AMBER_L,
                AMBER,
            ),
            (
                "S2",
                tr("новый снимок по v2,\nактуален", "new snapshot on v2,\ncurrent"),
                GREEN_L,
                GREEN,
            ),
        ]
        labels = [
            "reconcile",
            tr("заказ v2, правила v2", "order v2, rules v2"),
            "revise · check · reconcile",
        ]
        for index, (title, sub, fill, edge) in enumerate(boxes):
            x = index * (bw + 22)
            node(c, x, y, bw, bh, title, sub, fill, edge, 10.5, 7.6)
            if index:
                arrow(c, x - 21, y + bh / 2, x - 1, y + bh / 2)
                c.setFillColor(MUTED)
                c.setFont("PT", 7.4)
                lines_in(
                    c, x - 11, y - 4, bw * 0.9, labels[index - 1], "PT", 7.4, 9, MUTED
                )
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.4)
        c.drawString(
            0,
            4,
            tr(
                "S1 не меняется: reproduce(S1) из его собственных версий даёт тот же результат.",
                "S1 never changes: reproduce(S1) from its own versions gives the same result.",
            ),
        )

    return Diagram(118, draw)


def token_flow() -> Diagram:
    """A token is shown once, stored as a hash and resolved on every request."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw the four steps and the revocation branch beneath them."""
        bw, bh, gap = (w - 3 * 20) / 4, 62, 20
        y = h - bh - 4
        boxes = [
            (
                "users_cli token",
                tr("ewa_… показан один раз", "ewa_… shown once"),
                VIOLET_L,
                VIOLET,
            ),
            (
                "api_tokens",
                tr("хранится только SHA-256", "only SHA-256 is stored"),
                SLATE_L,
                MUTED,
            ),
            (
                "Bearer",
                tr("каждый запрос: хеш → строка", "each request: hash → row"),
                BLUE_L,
                BLUE,
            ),
            (
                "Principal",
                tr("текущие роли и подразделения", "current roles and units"),
                GREEN_L,
                GREEN,
            ),
        ]
        for index, (title, sub, fill, edge) in enumerate(boxes):
            x = index * (bw + gap)
            node(c, x, y, bw, bh, title, sub, fill, edge, 10, 7.8)
            if index:
                arrow(c, x - gap + 1, y + bh / 2, x - 1, y + bh / 2)
        branch_y = y - 52
        node(
            c,
            bw + gap,
            branch_y,
            2 * bw + gap,
            36,
            tr("revoke · deactivate", "revoke · deactivate"),
            tr(
                "следующий запрос: 401 unauthenticated",
                "next request: 401 unauthenticated",
            ),
            RED_L,
            RED,
            9.5,
            7.8,
        )
        arrow(c, 1.5 * bw + gap, branch_y + 36, 1.5 * bw + gap, y - 1, RED)
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.4)
        c.drawString(
            0,
            4,
            tr(
                "Неверный, отозванный токен и отключённый сотрудник дают одинаковый ответ.",
                "A wrong or revoked token and a deactivated user get the same answer.",
            ),
        )

    return Diagram(150, draw)


def approval_checks() -> Diagram:
    """Ten approval steps inside one transaction, locks before freshness."""
    steps = [
        (tr("Ключ идемпотентности", "Idempotency key"), BLUE),
        (tr("Версия процесса", "Workflow version"), BLUE),
        (
            tr(
                "Сотрудник: активен, decide, подразделение",
                "User: active, decide, unit",
            ),
            PINK,
        ),
        (tr("Не тот, кто запустил", "Not the starter"), PINK),
        (tr("Снимок этого процесса", "Snapshot of this workflow"), PINK),
        (tr("Блокировки: заказ, правила, счёт", "Locks: order, rules, invoice"), AMBER),
        (tr("Входы снимка = текущие", "Snapshot inputs = current"), AMBER),
        (tr("Статус matched", "Status matched"), AMBER),
        (tr("Переход в домене", "Domain transition"), TEAL),
        (
            tr("Процесс, outbox, аудит → COMMIT", "Workflow, outbox, audit → COMMIT"),
            GREEN,
        ),
    ]

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw two columns of numbered steps inside a transaction frame."""
        rbox(c, 0, 0, w, h, WHITE, PINK, r=10, lw=1.2, dash=(4, 3))
        c.setFillColor(PINK)
        c.setFont("PT-Bold", 9)
        c.drawString(
            12,
            h - 16,
            tr(
                "approve: одна транзакция PostgreSQL",
                "approve: one PostgreSQL transaction",
            ),
        )
        column_w = (w - 36) / 2
        for index, (text, colour) in enumerate(steps):
            column, row = divmod(index, 5)
            x = 12 + column * (column_w + 12)
            y = h - 44 - row * 27
            circle_num(c, x + 10, y + 8, 10, str(index + 1), colour)
            c.setFillColor(INK)
            draw_text(c, x + 26, y + 4, text, "PT", 9.3)
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.4)
        c.drawString(
            12,
            8,
            tr(
                "Любой отказ — ROLLBACK: ни решения, ни события, ни ключа не остаётся.",
                "Any refusal rolls back: no decision, event or key remains.",
            ),
        )

    return Diagram(5 * 27 + 54, draw)


def outbox_commit() -> Diagram:
    """Decision, outbox event and audit commit together; delivery comes later."""

    def draw(c: Canvas, w: float, h: float) -> None:
        """Draw the transaction on the left and the planned delivery on the right."""
        frame_w = w * 0.58
        rbox(c, 0, 18, frame_w, h - 22, WHITE, GREEN, r=10, lw=1.2, dash=(4, 3))
        c.setFillColor(GREEN)
        c.setFont("PT-Bold", 9)
        c.drawString(12, h - 18, tr("Одна транзакция", "One transaction"))
        rows = [
            (
                "workflows",
                tr("approved, версия 3", "approved, version 3"),
                TEAL_L,
                TEAL,
            ),
            ("outbox_events", "invoice.approved", PINK_L, PINK),
            (
                "audit_events",
                tr("→ снимок сверки, → событие", "→ snapshot, → event"),
                BLUE_L,
                BLUE,
            ),
        ]
        bw, bh = frame_w - 24, 30
        for index, (title, sub, fill, edge) in enumerate(rows):
            y = h - 60 - index * (bh + 8)
            node(c, 12, y, bw, bh, title, sub, fill, edge, 9.2, 7.6)
        right_x = frame_w + 30
        right_w = w - right_x
        mid = 18 + (h - 22) / 2
        arrow(c, frame_w + 2, mid, right_x - 2, mid, MUTED, 1.2, 5, (3, 2))
        node(
            c,
            right_x,
            mid - 34,
            right_w,
            68,
            tr("Обработчик outbox", "Outbox worker"),
            tr(
                "этап 09: повторы,\nподписанный sandbox webhook",
                "Stage 09: retries,\nsigned sandbox webhook",
            ),
            SLATE_L,
            MUTED,
            9.5,
            7.6,
            dash=(3, 2),
        )
        c.setFillColor(MUTED)
        c.setFont("PT-Italic", 8.4)
        c.drawString(
            0,
            4,
            tr(
                "Нет решения без события и события без решения; доставки пока нет.",
                "No decision without an event and no event without a decision; no delivery yet.",
            ),
        )

    return Diagram(160, draw)
