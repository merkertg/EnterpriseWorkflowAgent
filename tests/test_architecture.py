"""Import boundaries: inner layers never import outer frameworks."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORKS = (
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "alembic",
    "pypdf",
)
FORBIDDEN = {
    "app/domain": (*FRAMEWORKS, "psycopg", "app.application", "app.infrastructure"),
    "app/application": (*FRAMEWORKS, "psycopg", "app.infrastructure", "app.api"),
    # The interface is an HTTP client: no application code, no database.
    "ui": ("app", "sqlalchemy", "psycopg", "alembic", "pypdf", "fastapi"),
}


def _imports(path: Path) -> set[str]:
    """Collect absolute module names imported anywhere in a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_inner_layers_do_not_import_outer_layers(package: str) -> None:
    """The domain stays standard-library only; use cases depend on ports."""
    files = sorted((ROOT / package).glob("*.py"))
    assert len(files) > 1, "the boundary check must scan real modules"
    violations = [
        f"{path.relative_to(ROOT)} imports {name}"
        for path in files
        for name in _imports(path)
        if any(
            name == banned or name.startswith(f"{banned}.")
            for banned in FORBIDDEN[package]
        )
    ]
    assert violations == []
