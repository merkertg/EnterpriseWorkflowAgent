"""Input versions are append-only, hash-verified and safe under concurrency."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from app.application.errors import StoredDataIntegrityError
from app.application.ports import VersionRef
from app.application.workflows import WorkflowService
from app.domain.content_hash import content_hash
from app.domain.models import LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.rules import ValidationPolicy
from tests.persistence.conftest import Seed

pytestmark = pytest.mark.postgres


def test_identical_content_reuses_the_latest_version(
    service: WorkflowService, seed: Seed
) -> None:
    """Recording the same order twice does not create version 2."""
    again = service.record_purchase_order(seed.purchase_order)
    assert again == seed.purchase_order_version
    assert again.version_no == 1
    assert again.content_hash == content_hash(seed.purchase_order)


def test_changed_content_appends_and_returning_content_appends_again(
    service: WorkflowService, seed: Seed
) -> None:
    """A -> B -> A gives versions 1, 2, 3: history shows every edit."""
    first_line = seed.purchase_order.lines[0]
    changed = replace(
        seed.purchase_order,
        lines=(
            replace(first_line, quantity=Decimal("1")),
            *seed.purchase_order.lines[1:],
        ),
    )
    second = service.record_purchase_order(changed)
    third = service.record_purchase_order(seed.purchase_order)
    assert [second.version_no, third.version_no] == [2, 3]
    assert second.content_hash != seed.purchase_order_version.content_hash
    assert third.content_hash == seed.purchase_order_version.content_hash
    assert third.version_id != seed.purchase_order_version.version_id
    # Version 1 is still loadable with its original content.
    old = service.get_purchase_order_version(seed.purchase_order_version.version_id)
    assert old.value == seed.purchase_order


def test_exact_decimal_values_round_trip(service: WorkflowService) -> None:
    """Six-decimal quantities and cent prices are read back unchanged."""
    order = PurchaseOrder(
        "PO-DECIMAL",
        "SUP-1",
        Currency.EUR,
        (
            LineItem(
                "SKU-1",
                "Fictional fractional service",
                Decimal("0.333333"),
                Money(Decimal("999999.99"), Currency.EUR),
            ),
        ),
    )
    ref = service.record_purchase_order(order)
    loaded = service.get_purchase_order_version(ref.version_id).value
    assert loaded == order
    assert loaded.lines[0].quantity == Decimal("0.333333")
    # 999999.99 x 0.333333 = 333332.99666667 -> HALF_UP 333333.00 (worked by hand).
    assert loaded.total.amount == Decimal("333333.00")


def test_policy_versions_preserve_currency_order(service: WorkflowService) -> None:
    """Policy content is versioned like documents."""
    first = service.record_policy(ValidationPolicy("p", (Currency.USD, Currency.EUR)))
    second = service.record_policy(ValidationPolicy("p", (Currency.EUR, Currency.USD)))
    assert (first.version_no, second.version_no) == (1, 2)


def test_edited_row_is_detected_by_its_hash(
    engine: Engine, service: WorkflowService, seed: Seed
) -> None:
    """Even with the trigger bypassed, a changed row cannot pass as the original."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE purchase_order_version_lines "
                "DISABLE TRIGGER trg_purchase_order_version_lines_append_only"
            )
        )
        connection.execute(
            text(
                "UPDATE purchase_order_version_lines SET quantity = 3 WHERE line_no = 1"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE purchase_order_version_lines "
                "ENABLE TRIGGER trg_purchase_order_version_lines_append_only"
            )
        )
    with pytest.raises(StoredDataIntegrityError, match="hash"):
        service.get_purchase_order_version(seed.purchase_order_version.version_id)
    with pytest.raises(StoredDataIntegrityError):
        service.get_workflow(seed.workflow_id)


def test_concurrent_recording_of_new_content_yields_one_version(
    service: WorkflowService,
) -> None:
    """Writers racing for version 1 retry; both receive the same stored version."""
    order = PurchaseOrder(
        "PO-RACE",
        "SUP-1",
        Currency.USD,
        (
            LineItem(
                "SKU-1",
                "Fictional item",
                Decimal("1"),
                Money(Decimal("1.00"), Currency.USD),
            ),
        ),
    )
    barrier = threading.Barrier(4)

    def record() -> VersionRef:
        """Start all writers at once to make the race likely."""
        barrier.wait()
        return service.record_purchase_order(order)

    with ThreadPoolExecutor(4) as pool:
        refs = list(pool.map(lambda _: record(), range(4)))
    assert {ref.version_no for ref in refs} == {1}
    assert len({ref.version_id for ref in refs}) == 1
