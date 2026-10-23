"""Both PDF guides exist, are current and contain every section in their language.

The build stores a fingerprint of its inputs in the PDF keywords. Changing the
generator, verification.json, application code, migrations or uv.lock without
rebuilding makes these tests fail. ReportLab is not needed to run them.
"""

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    "ru": ROOT / "docs/guide/enterprise-workflow-agent-guide.pdf",
    "en": ROOT / "docs/guide/enterprise-workflow-agent-guide-en.pdf",
}
REBUILD = "Rebuild: uv run --locked --group guide python scripts/build_guide.py"
CYRILLIC = re.compile("[А-Яа-яЁё]")


def _fingerprint_module() -> ModuleType:
    """Load scripts/guide/fingerprint.py by path, without the guide package."""
    spec = importlib.util.spec_from_file_location(
        "guide_fingerprint", ROOT / "scripts/guide/fingerprint.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section_titles() -> list[tuple[str, str]]:
    """Read CONTENTS from toc.py as data; importing it would need ReportLab."""
    tree = ast.parse((ROOT / "scripts/guide/toc.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CONTENTS"
            for target in node.targets
        ):
            titles = ast.literal_eval(node.value)
            assert isinstance(titles, list)
            return [(str(ru), str(en)) for ru, en in titles]
    raise AssertionError("CONTENTS not found in toc.py")


def _text(lang: str) -> str:
    """Extract all text with whitespace collapsed, so wrapped titles still match."""
    reader = PdfReader(GUIDES[lang])
    return " ".join(
        " ".join(page.extract_text() or "" for page in reader.pages).split()
    )


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_guide_was_built_from_current_sources(lang: str) -> None:
    """The embedded fingerprint equals a fresh hash of every guide input."""
    module = _fingerprint_module()
    metadata = PdfReader(GUIDES[lang]).metadata
    assert metadata is not None
    expected = module.PREFIX + module.source_fingerprint(ROOT)
    assert metadata.get("/Keywords") == expected, REBUILD


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_guide_has_every_section_in_its_language(lang: str) -> None:
    """Each section title appears; English has no Cyrillic, Russian does."""
    text = _text(lang)
    for ru, en in _section_titles():
        assert (ru if lang == "ru" else en) in text
    if lang == "en":
        assert CYRILLIC.findall(text) == [], "Russian text in the English guide"
    else:
        assert CYRILLIC.search(text) is not None
