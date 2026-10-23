"""Database URL checks run without a server and never print the password."""

import pytest

from app.infrastructure.db.session import parse_database_url, require_test_database_url

SECRET = "fictional-password"


def test_postgresql_psycopg_url_is_accepted() -> None:
    """The project uses the psycopg 3 driver through SQLAlchemy."""
    parsed = parse_database_url(f"postgresql+psycopg://u:{SECRET}@127.0.0.1/demo")
    assert parsed.database == "demo"


@pytest.mark.parametrize(
    "url",
    [
        f"sqlite:///{SECRET}.db",
        f"postgresql://u:{SECRET}@127.0.0.1/demo",
        f"postgresql+psycopg://u:{SECRET}@127.0.0.1",
        "not a url",
    ],
)
def test_other_drivers_missing_names_and_garbage_are_rejected(url: str) -> None:
    """Errors describe the rule without echoing the URL or its password."""
    with pytest.raises(ValueError) as error:
        parse_database_url(url)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize(
    "database", ["enterprise_workflow", "enterprise_workflow_test_copy", "test"]
)
def test_test_database_name_must_end_with_test(database: str) -> None:
    """Destructive tests cannot point at a development database by mistake."""
    with pytest.raises(ValueError, match="_test"):
        require_test_database_url(
            f"postgresql+psycopg://u:{SECRET}@127.0.0.1/{database}"
        )


def test_test_database_suffix_is_accepted() -> None:
    """A name such as enterprise_workflow_test is allowed for integration tests."""
    parsed = require_test_database_url(
        "postgresql+psycopg://u@127.0.0.1:5432/enterprise_workflow_test"
    )
    assert parsed.database == "enterprise_workflow_test"
