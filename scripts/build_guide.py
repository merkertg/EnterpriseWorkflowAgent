"""Build the bilingual PDF guide for Enterprise Workflow Agent.

Run from the project root; ReportLab comes from the locked ``guide`` group:

    uv run --locked --group guide python scripts/build_guide.py

Both docs/guide/enterprise-workflow-agent-guide.pdf (Russian) and
docs/guide/enterprise-workflow-agent-guide-en.pdf (English) are built unless
--lang selects one. Section texts live in scripts/guide/.
"""

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The guide reads enums and limits from the application package.
sys.path.insert(0, str(PROJECT_ROOT))

from guide.diagrams import STAGES_DONE  # noqa: E402
from guide.fingerprint import PREFIX, source_fingerprint  # noqa: E402
from guide.i18n import LANGUAGES, set_language, tr  # noqa: E402
from guide.layout import (  # noqa: E402
    HEX,
    LINE,
    MARGIN,
    MUTED,
    PAGE_H,
    PAGE_W,
    WHITE,
    Canvas,
    W,
    arrow,
    rbox,
    register_fonts,
    stat_tiles,
)
from guide.sources import Facts, load_facts, load_verification  # noqa: E402
from guide.story import build_story  # noqa: E402
from guide.toc import CONTENTS, STAGE_SECTIONS  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate  # noqa: E402

GUIDE_DIR = PROJECT_ROOT / "docs/guide"
OUTPUTS = {
    "ru": GUIDE_DIR / "enterprise-workflow-agent-guide.pdf",
    "en": GUIDE_DIR / "enterprise-workflow-agent-guide-en.pdf",
}
STAGE_COLOURS = {
    "01": "#93C5FD",
    "02": "#5EEAD4",
    "03": "#FDBA74",
    "04": "#C4B5FD",
    "05": "#86EFAC",
    "06": "#F9A8D4",
}


def cover_page(c: Canvas, doc: Any) -> None:
    """Draw the cover: title, request path, key numbers and contents."""
    facts: Facts = doc.facts
    verification: dict[str, Any] = doc.verification
    c.saveState()
    c.setFillColor(HEX("#0F172A"))
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    c.setFillColor(HEX("#1E293B"))
    c.circle(PAGE_W - 40, PAGE_H - 60, 170, stroke=0, fill=1)
    c.setFillColor(HEX("#1E1B4B"))
    c.circle(PAGE_W - 120, PAGE_H - 250, 70, stroke=0, fill=1)
    left = MARGIN + 4
    c.setFillColor(HEX("#5EEAD4"))
    c.setFont("PT-Bold", 10)
    c.drawString(
        left,
        PAGE_H - 92,
        tr(
            "УЧЕБНЫЙ ПРОЕКТ  ·  ВЫМЫШЛЕННЫЕ СЧЕТА И ЗАКАЗЫ",
            "LEARNING PROJECT  ·  FICTIONAL INVOICES AND ORDERS",
        ),
    )
    c.setFillColor(WHITE)
    c.setFont("PT-Bold", 38)
    c.drawString(left, PAGE_H - 145, "Enterprise Workflow Agent")
    c.setFillColor(HEX("#FDBA74"))
    c.setFont("PT-Bold", 17)
    c.drawString(
        left,
        PAGE_H - 175,
        tr(
            f"Этапы 01–{STAGES_DONE:02d} · от счёта к проверяемому решению",
            f"Stages 01–{STAGES_DONE:02d} · from an invoice to a checked decision",
        ),
    )
    c.setFont("PT", 13.5)
    c.setFillColor(HEX("#CBD5E1"))
    y = PAGE_H - 203
    for line in simpleSplit(
        tr(
            "Наглядное руководство: uv и FastAPI, точные деньги и правила, "
            "состояния процесса, PostgreSQL и миграции, транзакции, аудит и повторы, "
            "документы с цитатами, сверка с заказом и решение сотрудника с ролями.",
            "A visual guide: uv and FastAPI, exact money and rules, workflow states, "
            "PostgreSQL and migrations, transactions, audit and retries, documents "
            "with citations, order reconciliation and role-checked employee decisions.",
        ),
        "PT",
        13.5,
        W - 30,
    ):
        c.drawString(left, y, line)
        y -= 18

    steps = [
        (tr("Документ", "Document"), "TXT · PDF"),
        (tr("Поля", "Fields"), tr("с цитатами", "with citations")),
        (tr("Проверка", "Check"), tr("коды причин", "reason codes")),
        ("PostgreSQL", tr("версии и аудит", "versions, audit")),
        (tr("Сотрудник", "Employee"), tr("решение", "decision")),
    ]
    top = PAGE_H - 360
    box_w, box_h, gap = 88, 54, 12
    x0 = (PAGE_W - (len(steps) * box_w + (len(steps) - 1) * gap)) / 2
    accents = ["#FDBA74", "#C4B5FD", "#5EEAD4", "#60A5FA", "#86EFAC"]
    for index, (title, sub) in enumerate(steps):
        x = x0 + index * (box_w + gap)
        rbox(c, x, top, box_w, box_h, HEX("#1E293B"), HEX(accents[index]), r=9, lw=1.4)
        c.setFillColor(WHITE)
        c.setFont("PT-Bold", 11)
        c.drawCentredString(x + box_w / 2, top + 31, title)
        c.setFillColor(HEX("#94A3B8"))
        c.setFont("PT", 8.6)
        c.drawCentredString(x + box_w / 2, top + 16, sub)
        if index < len(steps) - 1:
            arrow(
                c,
                x + box_w + 2,
                top + box_h / 2,
                x + box_w + gap - 2,
                top + box_h / 2,
                HEX("#94A3B8"),
                1.2,
                4.5,
            )
    c.setFillColor(HEX("#94A3B8"))
    c.setFont("PT-Italic", 9.5)
    c.drawCentredString(
        PAGE_W / 2,
        top - 22,
        tr(
            "Код считает и проверяет, модель только предлагает, решает человек.",
            "Code computes and checks, a model only proposes, a person decides.",
        ),
    )

    tests = verification["tests"]
    stat_tiles(
        c,
        MARGIN,
        360,
        W,
        74,
        [
            (f"{STAGES_DONE} / 10", tr("этапов выполнено", "stages done")),
            (
                f"{tests['with_database']['passed']} / {tests['total']}",
                tr("тестов прошли", "tests passed"),
            ),
            (str(len(facts.tables)), tr("таблиц PostgreSQL", "PostgreSQL tables")),
            (
                str(len(facts.discrepancy_codes)),
                tr("кодов расхождений", "discrepancy codes"),
            ),
            (str(facts.lock_packages), tr("пакетов в uv.lock", "packages in uv.lock")),
        ],
        HEX("#1E293B"),
        WHITE,
        HEX("#94A3B8"),
        HEX("#334155"),
    )
    c.setFillColor(HEX("#CBD5E1"))
    c.setFont("PT-Bold", 10)
    c.drawString(MARGIN, 326, tr("Что внутри", "Contents"))
    c.setFont("PT", 7.8)
    c.setFillColor(HEX("#64748B"))
    ranges = " · ".join(
        f"{stage}: {numbers[0]}–{numbers[-1]}"
        for stage, numbers in STAGE_SECTIONS.items()
    )
    c.drawString(MARGIN + 80, 326, tr("этапы ", "stages ") + ranges)
    rows = (len(CONTENTS) + 1) // 2
    for index, (ru, en) in enumerate(CONTENTS):
        column, row = divmod(index, rows)
        number = index + 1
        colour = "#94A3B8"
        for stage, numbers in STAGE_SECTIONS.items():
            if number in numbers:
                colour = STAGE_COLOURS[stage]
        c.setFillColor(HEX(colour))
        c.setFont("PT", 9)
        c.drawString(MARGIN + column * 250, 306 - row * 17, f"{number}  {tr(ru, en)}")
    c.setStrokeColor(HEX("#334155"))
    c.line(MARGIN, 82, PAGE_W - MARGIN, 82)
    c.setFillColor(HEX("#64748B"))
    c.setFont("PT", 8.4)
    environment = verification["environment"]
    c.drawString(
        MARGIN,
        66,
        tr("Проверка", "Verified")
        + f" {verification['verified_on']} · "
        + tr("база", "base")
        + f" {verification['git']['base_commit']} · uv {environment['uv']} · "
        f"Python {environment['python']} · PostgreSQL 18.3",
    )
    c.drawString(
        MARGIN,
        53,
        tr(
            f"Руководства: docs/STAGE_01.md–STAGE_{STAGES_DONE:02d}.md; "
            "отчёт: docs/VERIFICATION.md",
            f"Stage guides: docs/STAGE_01.md–STAGE_{STAGES_DONE:02d}.md (RU); "
            "instructions: docs/instructions/",
        ),
    )
    c.restoreState()


def content_page(c: Canvas, doc: Any) -> None:
    """Draw the footer of a normal page."""
    c.saveState()
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(MARGIN, 13 * mm, PAGE_W - MARGIN, 13 * mm)
    c.setFillColor(MUTED)
    c.setFont("PT", 8)
    c.drawString(
        MARGIN,
        9 * mm,
        tr(
            f"Enterprise Workflow Agent · этапы 01–{STAGES_DONE:02d} · "
            "наглядное руководство",
            f"Enterprise Workflow Agent · stages 01–{STAGES_DONE:02d} · visual guide",
        ),
    )
    c.drawRightString(PAGE_W - MARGIN, 9 * mm, str(doc.page))
    c.restoreState()


def build_guide(
    output: Path,
    facts: Facts,
    verification: dict[str, Any],
    lang: str,
    fingerprint: str,
) -> None:
    """Build one language; the old PDF is replaced only after success."""
    set_language(lang)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = Path(tempfile.gettempdir()) / f"{output.stem}-before-{stamp}.pdf"
        shutil.copy2(output, backup)
        print(f"Previous PDF saved: {backup}")
    with tempfile.NamedTemporaryFile(
        prefix=".enterprise-workflow-guide-",
        suffix=".pdf",
        dir=output.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        document = BaseDocTemplate(
            str(temporary_path),
            pagesize=A4,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=16 * mm,
            bottomMargin=17 * mm,
            title="Enterprise Workflow Agent - "
            + tr(
                f"этапы 01–{STAGES_DONE:02d}: домен, хранение, документы, сверка "
                "и решения",
                f"stages 01–{STAGES_DONE:02d}: domain, storage, documents, "
                "reconciliation, decisions",
            ),
            author="Enterprise Workflow Agent",
            subject=tr(
                "uv, FastAPI, Decimal, PostgreSQL, Alembic, аудит, идемпотентность, "
                "PDF, цитаты полей, сверка, роли и outbox",
                "uv, FastAPI, Decimal, PostgreSQL, Alembic, audit, idempotency, "
                "PDF, field evidence, reconciliation, roles and outbox",
            ),
            keywords=PREFIX + fingerprint,
            lang=lang,
        )
        # The cover reads these values while it is drawn.
        document.facts = facts
        document.verification = verification
        frame = Frame(
            MARGIN,
            17 * mm,
            W,
            PAGE_H - 33 * mm,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover")
        document.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[cover_frame], onPage=cover_page),
                PageTemplate(id="content", frames=[frame], onPage=content_page),
            ]
        )
        document.build(build_story(facts, verification))
        temporary_path.replace(output)
        # Temporary files are owner-only; a published guide is world-readable.
        output.chmod(0o644)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"Guide built ({lang}): {output}")


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and build the requested languages."""
    parser = argparse.ArgumentParser(description="Build the RU/EN PDF guides.")
    parser.add_argument("--lang", choices=[*LANGUAGES, "all"], default="all")
    parser.add_argument("--output", type=Path, help="PDF path; needs --lang ru or en")
    parser.add_argument(
        "--verification", type=Path, default=GUIDE_DIR / "verification.json"
    )
    args = parser.parse_args(argv)
    if args.output and args.lang == "all":
        parser.error("--output needs --lang ru or --lang en")
    verification = load_verification(args.verification.resolve())
    facts = load_facts()
    fingerprint = source_fingerprint()
    register_fonts()
    languages = LANGUAGES if args.lang == "all" else (args.lang,)
    for lang in languages:
        output = args.output.resolve() if args.output else OUTPUTS[lang]
        build_guide(output, facts, verification, lang, fingerprint)


if __name__ == "__main__":
    main()
