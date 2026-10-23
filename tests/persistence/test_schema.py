"""Migrations are reproducible, match the models and enforce storage rules."""

from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError

from app.application.access import Principal
from app.application.documents import DocumentService
from app.application.reconciliation import ReconciliationService
from app.application.workflows import WorkflowService
from app.infrastructure.db.models import APPEND_ONLY_TABLES, Base
from app.samples import clean_invoice_text
from tests.persistence.conftest import (
    TABLES,
    USD_RULES,
    Seed,
    alembic_config,
    context,
)

pytestmark = pytest.mark.postgres


def test_migrations_round_trip_and_match_models(
    engine: Engine, test_database_url: str
) -> None:
    """Downgrade removes every table; upgrade recreates exactly the ORM schema."""
    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(config, "head")
    assert set(TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_money_and_quantity_columns_are_exact_numeric(engine: Engine) -> None:
    """Money is NUMERIC(14,2) and quantity NUMERIC(12,6), never float columns."""
    query = text(
        "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale "
        "FROM information_schema.columns WHERE table_schema = 'public' "
        "AND column_name IN ('declared_total', 'unit_price', 'calculated_total', "
        "'quantity', 'expected_total', 'order_total', 'unit_price_tolerance', "
        "'total_tolerance')"
    )
    with engine.connect() as connection:
        rows = {(row[0], row[1]): tuple(row[2:]) for row in connection.execute(query)}
    assert rows == {
        ("invoice_versions", "declared_total"): ("numeric", 14, 2),
        ("invoice_version_lines", "unit_price"): ("numeric", 14, 2),
        ("invoice_version_lines", "quantity"): ("numeric", 12, 6),
        ("purchase_order_version_lines", "unit_price"): ("numeric", 14, 2),
        ("purchase_order_version_lines", "quantity"): ("numeric", 12, 6),
        ("validation_snapshots", "calculated_total"): ("numeric", 14, 2),
        ("reconciliation_currency_rules", "unit_price_tolerance"): ("numeric", 14, 2),
        ("reconciliation_currency_rules", "total_tolerance"): ("numeric", 14, 2),
        ("reconciliation_snapshots", "calculated_total"): ("numeric", 14, 2),
        ("reconciliation_snapshots", "expected_total"): ("numeric", 14, 2),
        ("reconciliation_snapshots", "order_total"): ("numeric", 14, 2),
    }


def test_stored_amounts_are_decimal_values(engine: Engine, seed: Seed) -> None:
    """The driver returns Decimal, so no float rounding enters on reads."""
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT declared_total FROM invoice_versions")
        ).scalar_one()
    assert type(value) is Decimal
    assert value == Decimal("30.00")


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_history_tables_reject_update_and_delete(
    engine: Engine,
    service: WorkflowService,
    document_service: DocumentService,
    reconciliation_service: ReconciliationService,
    approver: Principal,
    seed: Seed,
    table: str,
) -> None:
    """A trigger blocks edits, so stored history cannot be rewritten silently."""
    service.check(seed.workflow_id, expected_version=1, context=context("check"))
    document_service.ingest(clean_invoice_text().encode(), "clean_invoice.txt")
    rules = reconciliation_service.record_rules(USD_RULES)
    reconciliation_service.reconcile(
        seed.workflow_id, rules_version_id=rules.version_id
    )
    # A decision writes the outbox row; every table now has at least one row.
    service.reject(
        seed.workflow_id,
        expected_version=2,
        reason="Fictional rejection",
        actor=approver,
        context=context("reject"),
    )
    for statement in (f"UPDATE {table} SET {_column(table)}", f"DELETE FROM {table}"):
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(text(statement))


def test_database_rejects_values_outside_domain_rules(engine: Engine) -> None:
    """Check constraints repeat key domain bounds for manual SQL."""
    with pytest.raises(DBAPIError, match="ck_invoice_versions_currency"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO invoice_versions (id, invoice_id, version_no, "
                    "content_hash, supplier_id, purchase_order_id, currency, "
                    "declared_total) VALUES (gen_random_uuid(), 'I', 1, "
                    f"'{'a' * 64}', 'S', 'P', 'GBP', 1.00)"
                )
            )


def _column(table: str) -> str:
    """Pick a harmless assignment that would otherwise be a valid UPDATE."""
    if table.endswith("_lines"):
        return "line_no = line_no"
    if table == "audit_events":
        return "source = source"
    if table == "idempotency_records":
        return "command = command"
    if table == "document_pages":
        return "text = text"
    if table == "extracted_fields":
        return "position = position"
    if table in ("document_extractions", "reconciliation_snapshots"):
        return "status = status"
    if table == "reconciliation_currency_rules":
        return "currency = currency"
    return "created_at = created_at"
