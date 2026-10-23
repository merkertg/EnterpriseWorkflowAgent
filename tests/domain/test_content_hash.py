"""Content hashes follow a written canonical form rather than the implementation."""

import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.content_hash import canonical_json, content_hash
from app.domain.errors import DomainValidationError
from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.money import Currency, Money
from app.domain.rules import ValidationPolicy


def _order(quantity: str = "2") -> PurchaseOrder:
    """Build a one-line order whose canonical JSON is written out in a test."""
    return PurchaseOrder(
        purchase_order_id="PO-HASH-1",
        supplier_id="SUP-1",
        currency=Currency.USD,
        lines=(
            LineItem(
                "SKU-1",
                "Fictional item",
                Decimal(quantity),
                Money(Decimal("12.50"), Currency.USD),
            ),
        ),
    )


def test_purchase_order_hash_matches_documented_canonical_json() -> None:
    """Sorted keys, no spaces, decimals as normalized strings, a schema tag."""
    expected_json = (
        '{"currency":"USD","lines":[{"description":"Fictional item",'
        '"quantity":"2.000000","sku":"SKU-1","unit_price":"12.50"}],'
        '"purchase_order_id":"PO-HASH-1","schema":"purchase_order.v1",'
        '"supplier_id":"SUP-1"}'
    )
    expected = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
    assert content_hash(_order()) == expected


def test_policy_hash_matches_documented_canonical_json() -> None:
    """Currency order is part of policy content."""
    expected_json = (
        '{"allowed_currencies":["USD","EUR"],"policy_id":"p-1",'
        '"schema":"validation_policy.v1"}'
    )
    policy = ValidationPolicy("p-1", (Currency.USD, Currency.EUR))
    assert (
        content_hash(policy)
        == hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
    )
    reordered = ValidationPolicy("p-1", (Currency.EUR, Currency.USD))
    assert content_hash(reordered) != content_hash(policy)


def test_equal_values_hash_equally_after_decimal_normalization() -> None:
    """Insignificant trailing zeroes are normalized by the domain before hashing."""
    assert content_hash(_order("2")) == content_hash(_order("2.000"))


def test_any_business_change_changes_the_hash() -> None:
    """Quantity, text and line order all belong to an input version."""
    original = _order()
    assert content_hash(_order("3")) != content_hash(original)
    renamed = replace(original, supplier_id="SUP-2")
    assert content_hash(renamed) != content_hash(original)


def test_invoice_hash_includes_declared_total(invoice: Invoice) -> None:
    """A changed declared total is new content even if lines are unchanged."""
    changed = replace(invoice, declared_total=Money(Decimal("31.00"), Currency.USD))
    assert content_hash(changed) != content_hash(invoice)
    assert len(content_hash(invoice)) == 64


def test_non_ascii_text_is_encoded_as_utf8() -> None:
    """Russian descriptions keep stable UTF-8 bytes instead of escapes."""
    assert canonical_json({"text": "Счёт"}) == '{"text":"Счёт"}'.encode()


def test_unsupported_values_are_rejected() -> None:
    """Only the three versioned input types have a defined content hash."""
    with pytest.raises(DomainValidationError) as error:
        content_hash("not an input")  # type: ignore[arg-type]
    assert error.value.code == "invalid_type"
