"""Guide contents and the section header that starts each section on a new page."""

from typing import Any

from reportlab.platypus import PageBreak, Spacer

from guide.i18n import tr
from guide.layout import BLUE, Color, SectionTitle

# Section titles (Russian, English). Section number = position + 1.
CONTENTS = [
    ("Проект за минуту", "Project in a minute"),
    ("Окружение и запуск", "Environment and launch"),
    ("Проверки и CI", "Checks and CI"),
    ("Деньги и документы", "Money and documents"),
    ("Базовые правила", "Basic rules"),
    ("Состояния процесса", "Workflow states"),
    ("Слои и порты", "Layers and ports"),
    ("Таблицы и миграции", "Tables and migrations"),
    ("Версии и хеши", "Versions and hashes"),
    ("Команда — одна транзакция", "One command, one transaction"),
    ("Блокировка и повтор", "Locking and retries"),
    ("Аудит и снимки", "Audit and snapshots"),
    ("Приём документа", "Document intake"),
    ("PDF в отдельном процессе", "PDF in a separate process"),
    ("Поля и цитаты", "Fields and evidence"),
    ("Проверка извлечения", "Assessing an extraction"),
    ("Хранение и командная строка", "Storage and command line"),
    ("Правила сверки", "Reconciliation rules"),
    ("Сверка строк", "Line reconciliation"),
    ("Снимок и устаревание", "Snapshot and staleness"),
    ("Вход и роли", "Sign-in and roles"),
    ("Одобрение по свежей сверке", "Approval on a fresh reconciliation"),
    ("HTTP API", "HTTP API"),
    ("Outbox и аудит решения", "Outbox and decision audit"),
    ("Агент и его границы", "The agent and its bounds"),
    ("Инструменты и отказы", "Tools and refusals"),
    ("Черновик и трассировка", "Draft check and trace"),
    ("Интерфейс поверх API", "An interface over the API"),
    ("Повтор и перезагрузка", "Repeats and reloads"),
    ("Ошибки и проверка страницы", "Errors and page checks"),
    ("Шпаргалка команд", "Command cheat sheet"),
    ("Типичные ошибки", "Common errors"),
    ("Проверь себя", "Check yourself"),
    ("Итоги проверки", "Verification results"),
]

# Sections per stage; the cover colours its contents list with these ranges.
STAGE_SECTIONS = {
    "01": range(2, 4),
    "02": range(4, 7),
    "03": range(7, 13),
    "04": range(13, 18),
    "05": range(18, 21),
    "06": range(21, 25),
    "07": range(25, 28),
    "08": range(28, 31),
}


def number_of(ru_title: str) -> int:
    """Find a section number by its Russian title, so references never drift."""
    for index, (ru, _) in enumerate(CONTENTS):
        if ru == ru_title:
            return index + 1
    raise ValueError(f"No section {ru_title!r}")


def title(number: int) -> str:
    """Return a section title in the current language."""
    ru, en = CONTENTS[number - 1]
    return tr(ru, en)


def section(
    story: list[Any], ru_title: str, subtitle: str, color: Color = BLUE
) -> None:
    """Start a section on a new page with its number, title and subtitle."""
    number = number_of(ru_title)
    if len(story) > 2:
        story.append(PageBreak())
    story.append(SectionTitle(str(number), title(number), subtitle, color))
    story.append(Spacer(1, 8))
