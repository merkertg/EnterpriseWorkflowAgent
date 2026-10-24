"""PostgreSQL fixtures: a guarded _test database, fresh migrations, clean tables.

Without TEST_DATABASE_URL these tests are skipped. CI sets
REQUIRE_TEST_DATABASE=1 so a missing database fails instead of skipping.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from app.application.access import Principal
from app.application.documents import DocumentService
from app.application.ports import CommandContext, CommandResult, VersionRef
from app.application.reconciliation import ReconciliationService
from app.application.users import UserService
from app.application.workflows import WorkflowService
from app.demo import build_demo_documents
from app.domain.models import Invoice, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.reconciliation import CurrencyRule, ReconciliationRules
from app.domain.rules import ValidationPolicy
from app.infrastructure.db.session import (
    build_document_service,
    build_reconciliation_service,
    build_user_service,
    build_workflow_service,
    create_database_engine,
    require_test_database_url,
)

ROOT = Path(__file__).resolve().parents[2]
# Every test workflow belongs to this fictional business unit.
UNIT = "UNIT-A"
# Fictional USD/EUR tolerances shared by reconciliation tests.
USD_RULES = ReconciliationRules(
    "test-rules",
    (
        CurrencyRule(
            Currency.USD,
            Money(Decimal("0.05"), Currency.USD),
            Money(Decimal("0.10"), Currency.USD),
        ),
    ),
)
TABLES = (
    "agent_steps",
    "agent_runs",
    "outbox_attempts",
    "outbox_deliveries",
    "outbox_events",
    "api_tokens",
    "users",
    "reconciliation_snapshots",
    "reconciliation_currency_rules",
    "reconciliation_rule_versions",
    "extracted_fields",
    "document_extractions",
    "document_pages",
    "source_documents",
    "audit_events",
    "idempotency_records",
    "validation_snapshots",
    "workflows",
    "invoice_version_lines",
    "invoice_versions",
    "purchase_order_version_lines",
    "purchase_order_versions",
    "policy_versions",
)


def alembic_config(database_url: str) -> Config:
    """Point Alembic at the test URL without reading DATABASE_URL or .env."""
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Read only the process variable, then refuse names without _test."""
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        if os.environ.get("REQUIRE_TEST_DATABASE") == "1":
            pytest.fail("REQUIRE_TEST_DATABASE=1, but TEST_DATABASE_URL is not set.")
        pytest.skip("Set TEST_DATABASE_URL to a disposable database ending in _test.")
    require_test_database_url(url)
    return url


@pytest.fixture(scope="session")
def migrated_engine(test_database_url: str) -> Iterator[Engine]:
    """Rebuild the schema from zero once per session through the real migrations."""
    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_database_engine(test_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    """Start each test with empty tables; TRUNCATE bypasses row triggers."""
    with migrated_engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )
    return migrated_engine


@pytest.fixture
def service(engine: Engine) -> WorkflowService:
    """A service that opens a new transaction for every call."""
    return build_workflow_service(engine)


@pytest.fixture
def document_service(engine: Engine) -> DocumentService:
    """Document intake with the real PDF worker and deterministic parser."""
    return build_document_service(engine)


@pytest.fixture
def reconciliation_service(engine: Engine) -> ReconciliationService:
    """Reconciliation over the same test database."""
    return build_reconciliation_service(engine)


@pytest.fixture
def user_service(engine: Engine) -> UserService:
    """Employee and token management over the same test database."""
    return build_user_service(engine)


def employee(
    users: UserService, user_id: str, *roles: str, units: tuple[str, ...] = (UNIT,)
) -> Principal:
    """Create a fictional employee and return their current rights."""
    return users.create_user(
        user_id, display_name=f"Fictional {user_id}", roles=roles, units=units
    ).principal()


@pytest.fixture
def approver(user_service: UserService) -> Principal:
    """An approver in UNIT-A who did not start any test workflow."""
    return employee(user_service, "approver-1", "approver")


def context(key: str) -> CommandContext:
    """Build test command metadata with a caller-chosen idempotency key."""
    return CommandContext(key, "pytest")


@dataclass(frozen=True, slots=True)
class Seed:
    """Stored fictional inputs and a started workflow."""

    invoice: Invoice
    purchase_order: PurchaseOrder
    policy: ValidationPolicy
    invoice_version: VersionRef
    purchase_order_version: VersionRef
    policy_version: VersionRef
    started: CommandResult

    @property
    def workflow_id(self) -> str:
        """Return the started workflow ID."""
        return self.started.workflow_id


@pytest.fixture
def seed(service: WorkflowService) -> Seed:
    """Record demo-shaped inputs and start WF-TEST-1 at version 1."""
    invoice, purchase_order = build_demo_documents()
    invoice = replace(invoice, invoice_id="INV-TEST-1")
    policy = ValidationPolicy("policy-test")
    invoice_version = service.record_invoice(invoice)
    order_version = service.record_purchase_order(purchase_order)
    policy_version = service.record_policy(policy)
    started = service.start(
        "WF-TEST-1",
        invoice_version_id=invoice_version.version_id,
        purchase_order_version_id=order_version.version_id,
        policy_version_id=policy_version.version_id,
        business_unit=UNIT,
        context=context("start-1"),
    )
    return Seed(
        invoice,
        purchase_order,
        policy,
        invoice_version,
        order_version,
        policy_version,
        started,
    )
