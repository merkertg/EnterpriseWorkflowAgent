"""Guide language: every text is a Russian-English pair chosen at build time.

The language is set once before a build. Diagrams are drawn during the build,
so tr() inside drawing functions sees the same language.
"""

LANGUAGES = ("ru", "en")
_current = {"lang": "ru"}


def set_language(lang: str) -> None:
    """Select the build language: ru or en."""
    if lang not in LANGUAGES:
        raise ValueError(f"Unknown language: {lang}")
    _current["lang"] = lang


def language() -> str:
    """Return the current build language."""
    return _current["lang"]


def tr(ru: str, en: str) -> str:
    """Return the text for the current language."""
    return ru if _current["lang"] == "ru" else en
