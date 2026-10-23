"""Guide layout: fonts, colours, text styles and shared page blocks.

Adapted from the sibling learning projects so all guides look alike. ReportLab
has no type hints, so its canvas and flowables are typed as Any here.
"""

import math
import re
from collections.abc import Callable, Sequence
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Paragraph, Preformatted, Table, TableStyle

type Canvas = Any
type Color = Any
# PT Sans has no arrows; they are drawn with PT Mono instead.
ARROWS = "→←↓↑"


def register_fonts() -> None:
    """Register Cyrillic PT fonts on macOS or DejaVu Sans on Linux."""
    mac_sans = Path("/System/Library/Fonts/Supplemental/PTSans.ttc")
    mac_mono = Path("/System/Library/Fonts/Supplemental/PTMono.ttc")
    if mac_sans.is_file() and mac_mono.is_file():
        font_files = {
            "PT": (mac_sans, 0),
            "PT-Bold": (mac_sans, 7),
            "PT-Italic": (mac_sans, 1),
            "PT-BoldItalic": (mac_sans, 6),
            "Mono": (mac_mono, 1),
            "Mono-Bold": (mac_mono, 0),
        }
    else:
        root = Path("/usr/share/fonts/truetype/dejavu")
        font_files = {
            "PT": (root / "DejaVuSans.ttf", 0),
            "PT-Bold": (root / "DejaVuSans-Bold.ttf", 0),
            "PT-Italic": (root / "DejaVuSans-Oblique.ttf", 0),
            "PT-BoldItalic": (root / "DejaVuSans-BoldOblique.ttf", 0),
            "Mono": (root / "DejaVuSansMono.ttf", 0),
            "Mono-Bold": (root / "DejaVuSansMono-Bold.ttf", 0),
        }
    missing = [str(path) for path, _ in font_files.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Cyrillic fonts not found: " + ", ".join(missing))
    for name, (path, index) in font_files.items():
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
    pdfmetrics.registerFontFamily(
        "PT",
        normal="PT",
        bold="PT-Bold",
        italic="PT-Italic",
        boldItalic="PT-BoldItalic",
    )
    pdfmetrics.registerFontFamily("Mono", normal="Mono", bold="Mono-Bold")


# ---------- Colours ----------
HEX = colors.HexColor
INK = HEX("#0F172A")
TEXT = HEX("#1E293B")
MUTED = HEX("#64748B")
LINE = HEX("#CBD5E1")
SOFT = HEX("#F1F5F9")
WHITE = colors.white
BLUE, BLUE_L = HEX("#2563EB"), HEX("#DBEAFE")
TEAL, TEAL_L = HEX("#0F766E"), HEX("#CCFBF1")
ORANGE, ORANGE_L = HEX("#C2410C"), HEX("#FFEDD5")
VIOLET, VIOLET_L = HEX("#6D28D9"), HEX("#EDE9FE")
GREEN, GREEN_L = HEX("#15803D"), HEX("#DCFCE7")
AMBER, AMBER_L = HEX("#B45309"), HEX("#FEF3C7")
RED, RED_L = HEX("#B91C1C"), HEX("#FEE2E2")
SLATE_L = HEX("#E2E8F0")
PINK, PINK_L = HEX("#BE185D"), HEX("#FCE7F3")

PAGE_W, PAGE_H = A4
MARGIN = 17 * mm
W = PAGE_W - 2 * MARGIN

# ---------- Text styles ----------
BODY = ParagraphStyle("body", fontName="PT", fontSize=10.5, leading=15, textColor=TEXT)
BODY_S = ParagraphStyle("bodys", parent=BODY, fontSize=9.5, leading=13.5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.6, leading=12, textColor=MUTED)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9, leading=12.2)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="PT-Bold")
CELL_M = ParagraphStyle(
    "cellm", parent=CELL, fontName="Mono", fontSize=8.3, leading=11.5
)
HEAD = ParagraphStyle("head", parent=CELL, fontName="PT-Bold", textColor=WHITE)
H2 = ParagraphStyle(
    "h2",
    parent=BODY,
    fontName="PT-Bold",
    fontSize=13,
    leading=17,
    textColor=INK,
    spaceBefore=6,
    spaceAfter=5,
)
CODE = ParagraphStyle(
    "code", fontName="Mono", fontSize=8.4, leading=11.4, textColor=INK
)
QUESTION = ParagraphStyle(
    "question", parent=BODY, fontName="PT-Bold", fontSize=10, leading=14
)


def p(text: str, style: Any = BODY) -> Any:
    """A paragraph with ReportLab markup; arrows switch to PT Mono."""
    for arrow_char in ARROWS:
        text = text.replace(arrow_char, f'<font name="Mono">{arrow_char}</font>')
    return Paragraph(text, style)


def mono(text: str) -> str:
    """Inline monospace fragment; angle brackets and ampersands are escaped."""
    return f'<font name="Mono" size="8.8" color="#0F172A">{escape(text)}</font>'


def draw_text(
    c: Canvas,
    x: float,
    y: float,
    text: str,
    font: str,
    size: float,
    align: str = "left",
) -> None:
    """Draw one line on the canvas; arrows use Mono, everything else font."""
    parts = re.split(f"([{ARROWS}])", text)
    fonts = ["Mono" if part in ARROWS and part else font for part in parts]
    widths = [
        pdfmetrics.stringWidth(part, name, size)
        for part, name in zip(parts, fonts, strict=True)
    ]
    total = sum(widths)
    if align == "center":
        x -= total / 2
    elif align == "right":
        x -= total
    for part, name, width in zip(parts, fonts, widths, strict=True):
        c.setFont(name, size)
        c.drawString(x, y, part)
        x += width


# ---------- Drawing primitives ----------
def rbox(
    c: Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    fill: Color,
    stroke: Color | None = None,
    r: float = 6,
    lw: float = 1,
    dash: Sequence[float] | None = None,
) -> None:
    """A rounded rectangle."""
    c.saveState()
    c.setFillColor(fill)
    if stroke is not None:
        c.setStrokeColor(stroke)
        c.setLineWidth(lw)
    if dash:
        c.setDash(list(dash))
    c.roundRect(x, y, w, h, r, stroke=1 if stroke is not None else 0, fill=1)
    c.restoreState()


def lines_in(
    c: Canvas,
    cx: float,
    top: float,
    width: float,
    text: str,
    font: str = "PT",
    size: float = 9,
    leading: float | None = None,
    color: Color = TEXT,
    align: str = "center",
) -> float:
    """Wrap text to a width and draw it downwards; return the bottom y."""
    step = leading or size * 1.28
    c.saveState()
    c.setFillColor(color)
    y = top - size
    for paragraph in text.split("\n"):
        for line in simpleSplit(paragraph, font, size, width):
            draw_text(c, cx, y, line, font, size, align)
            y -= step
    c.restoreState()
    return float(y + step - size)


def node(
    c: Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    sub: str = "",
    fill: Color = BLUE_L,
    edge: Color = BLUE,
    tsize: float = 10,
    ssize: float = 8,
    dash: Sequence[float] | None = None,
    sub_font: str = "PT",
) -> None:
    """A diagram box with a centred title and optional explanation."""
    rbox(c, x, y, w, h, fill, edge, r=7, lw=1.1, dash=dash)
    sub_lines: list[str] = []
    for paragraph in sub.split("\n") if sub else []:
        sub_lines += simpleSplit(paragraph, sub_font, ssize, w - 12)
    title_lines = simpleSplit(title, "PT-Bold", tsize, w - 10)
    total = len(title_lines) * tsize * 1.2 + len(sub_lines) * ssize * 1.3
    if sub_lines:
        total += 3
    top = y + h / 2 + total / 2
    lines_in(c, x + w / 2, top, w - 10, title, "PT-Bold", tsize, tsize * 1.2, INK)
    if sub_lines:
        top -= len(title_lines) * tsize * 1.2 + 3
        lines_in(c, x + w / 2, top, w - 12, sub, sub_font, ssize, ssize * 1.3, MUTED)


def arrow(
    c: Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: Color = MUTED,
    lw: float = 1.3,
    head: float = 5.5,
    dash: Sequence[float] | None = None,
) -> None:
    """A line with an arrowhead at its end."""
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(lw)
    if dash:
        c.setDash(list(dash))
    angle = math.atan2(y2 - y1, x2 - x1)
    ex, ey = x2 - head * 0.9 * math.cos(angle), y2 - head * 0.9 * math.sin(angle)
    c.line(x1, y1, ex, ey)
    c.setDash([])
    path = c.beginPath()
    path.moveTo(x2, y2)
    path.lineTo(x2 - head * math.cos(angle - 0.42), y2 - head * math.sin(angle - 0.42))
    path.lineTo(x2 - head * math.cos(angle + 0.42), y2 - head * math.sin(angle + 0.42))
    path.close()
    c.drawPath(path, fill=1, stroke=0)
    c.restoreState()


def circle_num(
    c: Canvas,
    x: float,
    y: float,
    r: float,
    text: str,
    fill: Color = BLUE,
    color: Color = WHITE,
    size: float = 10,
) -> None:
    """A filled circle with a step number."""
    c.saveState()
    c.setFillColor(fill)
    c.circle(x, y, r, stroke=0, fill=1)
    c.setFillColor(color)
    c.setFont("PT-Bold", size)
    c.drawCentredString(x, y - size * 0.35, text)
    c.restoreState()


def chip(
    c: Canvas,
    x: float,
    y: float,
    text: str,
    fill: Color,
    color: Color,
    font: str = "Mono",
    size: float = 7.6,
    pad: float = 4,
    h: float = 13,
) -> float:
    """A small label with background; return its width."""
    width = float(pdfmetrics.stringWidth(text, font, size)) + pad * 2
    rbox(c, x, y, width, h, fill, None, r=3)
    c.saveState()
    c.setFillColor(color)
    c.setFont(font, size)
    c.drawString(x + pad, y + (h - size) / 2 + 1.2, text)
    c.restoreState()
    return width


class Diagram(Flowable):
    """A layout block drawn by its own function."""

    def __init__(
        self,
        height: float,
        draw: Callable[[Canvas, float, float], None],
        width: float = W,
    ) -> None:
        """Remember the size and the drawing function."""
        super().__init__()
        self.width, self.height, self.draw_fn = width, height, draw

    def wrap(self, *_: object) -> tuple[float, float]:
        """Return the space the block needs."""
        return self.width, self.height

    def draw(self) -> None:
        """Draw the diagram on the current page."""
        self.draw_fn(self.canv, self.width, self.height)


class SectionTitle(Flowable):
    """Section number, title and subtitle."""

    def __init__(
        self, number: str, title: str, subtitle: str, color: Color = BLUE
    ) -> None:
        """Remember the texts and pre-wrap the subtitle."""
        super().__init__()
        self.number, self.title, self.subtitle, self.color = (
            number,
            title,
            subtitle,
            color,
        )
        self.sub_lines = simpleSplit(subtitle, "PT", 10.5, W - 46)
        self.height = 36 + len(self.sub_lines) * 14

    def wrap(self, *_: object) -> tuple[float, float]:
        """Return the space the block needs."""
        return W, self.height

    def draw(self) -> None:
        """Draw the number in a coloured square, then the title and subtitle."""
        c = self.canv
        top = self.height
        rbox(c, 0, top - 30, 34, 30, self.color, None, r=7)
        c.setFillColor(WHITE)
        c.setFont("PT-Bold", 15)
        c.drawCentredString(17, top - 21, self.number)
        c.setFillColor(INK)
        c.setFont("PT-Bold", 21)
        c.drawString(46, top - 22, self.title)
        c.setFillColor(MUTED)
        y = top - 40
        for line in self.sub_lines:
            draw_text(c, 46, y, line, "PT", 10.5)
            y -= 14


def callout(
    text: str, color: Color = BLUE, fill: Color = BLUE_L, style: Any = BODY_S
) -> Any:
    """A highlighted block with a coloured bar on the left."""
    table = Table([[p(text, style)]], colWidths=[W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("LINEBEFORE", (0, 0), (0, -1), 3.5, color),
                ("LEFTPADDING", (0, 0), (-1, -1), 11),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def code_block(text: str, width: float = W) -> Any:
    """Commands or code in monospace on a light background."""
    table = Table([[Preformatted(text.rstrip("\n"), CODE)]], colWidths=[width])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def simple_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    widths: Sequence[float],
    styles: Sequence[Any] | None = None,
    padding: float = 4,
) -> Any:
    """A striped table with a dark header; styles set each column's font.

    A smaller vertical padding fits long lists on one page without shrinking text.
    """
    column_styles = list(styles or [CELL_B] + [CELL] * (len(headers) - 1))
    prepared = [[p(value, HEAD) for value in headers]]
    for row in rows:
        prepared.append(
            [p(value, style) for value, style in zip(row, column_styles, strict=True)]
        )
    table = Table(prepared, colWidths=list(widths), repeatRows=1)
    commands: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), padding + 1),
    ]
    for index in range(2, len(prepared), 2):
        commands.append(("BACKGROUND", (0, index), (-1, index), SOFT))
    table.setStyle(TableStyle(commands))
    return table


def cards(
    items: Sequence[tuple[str, str, Color, Color]], columns: int = 3, gap: float = 10
) -> Any:
    """A row of cards: (title, text, colour, light colour)."""
    width = (W - gap * (columns - 1)) / columns
    title_style = ParagraphStyle("ct", parent=BODY, fontSize=10.5, leading=13)
    text_style = ParagraphStyle("cb", parent=BODY, fontSize=9, leading=12.6)
    cells = []
    for title, text, color, fill in items:
        hex_color = color.hexval().replace("0x", "#")
        inner = Table(
            [
                [p(f'<font color="{hex_color}"><b>{title}</b></font>', title_style)],
                [p(text, text_style)],
            ],
            colWidths=[width],
        )
        inner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), fill),
                    ("LINEABOVE", (0, 0), (-1, 0), 3, color),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        cells.append(inner)
    grid_rows = []
    for start in range(0, len(cells), columns):
        line: list[Any] = []
        for cell in cells[start : start + columns]:
            line += [cell, ""]
        while len(line) < columns * 2:
            line += ["", ""]
        grid_rows.append(line[:-1])
    widths: list[float] = []
    for _ in range(columns):
        widths += [width, gap]
    table = Table(grid_rows, colWidths=widths[:-1])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), gap),
            ]
        )
    )
    return table


def stat_tiles(
    c: Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    items: Sequence[tuple[str, str]],
    fill: Color,
    value_color: Color,
    label_color: Color,
    edge: Color | None = None,
) -> None:
    """A row of "number + label" tiles; long values shrink to fit."""
    gap = 8
    width = (w - gap * (len(items) - 1)) / len(items)
    for index, (value, label) in enumerate(items):
        left = x + index * (width + gap)
        rbox(c, left, y, width, h, fill, edge, r=8, lw=0.8)
        c.setFillColor(value_color)
        size = 22.0
        while size > 13 and pdfmetrics.stringWidth(value, "PT-Bold", size) > width - 14:
            size -= 1
        c.setFont("PT-Bold", size)
        c.drawCentredString(left + width / 2, y + h - 30, value)
        lines_in(
            c,
            left + width / 2,
            y + h - 38,
            width - 12,
            label,
            "PT",
            8.4,
            10.6,
            label_color,
        )
