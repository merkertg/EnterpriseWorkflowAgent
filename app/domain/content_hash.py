"""Canonical content and SHA-256 hashes for immutable workflow inputs.

The hash identifies exact business content, independent of storage layout. It
uses only the standard library so persistence and future adapters agree on it.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence

from app.domain.errors import DomainValidationError
from app.domain.extraction import ExtractionResult
from app.domain.models import Invoice, LineItem, PurchaseOrder
from app.domain.reconciliation import ReconciliationRules
from app.domain.rules import ValidationPolicy

type Hashable = Invoice | PurchaseOrder | ValidationPolicy | ReconciliationRules

type CanonicalValue = (
    str | int | bool | None | Sequence[CanonicalValue] | Mapping[str, CanonicalValue]
)

# A schema tag changes the hash if the canonical layout itself is changed later.
_INVOICE_SCHEMA = "invoice.v1"
_PURCHASE_ORDER_SCHEMA = "purchase_order.v1"
_POLICY_SCHEMA = "validation_policy.v1"
_RULES_SCHEMA = "reconciliation_rules.v1"
_EXTRACTION_SCHEMA = "extraction.v1"


def _line_content(line: LineItem) -> dict[str, CanonicalValue]:
    """Represent decimals as normalized strings so floats never enter a hash."""
    return {
        "sku": line.sku,
        "description": line.description,
        "quantity": str(line.quantity),
        "unit_price": str(line.unit_price.amount),
    }


def canonical_content(value: Hashable) -> dict[str, CanonicalValue]:
    """Return the ordered business fields whose exact values define a version."""
    if type(value) is Invoice:
        return {
            "schema": _INVOICE_SCHEMA,
            "invoice_id": value.invoice_id,
            "supplier_id": value.supplier_id,
            "purchase_order_id": value.purchase_order_id,
            "currency": value.currency.value,
            "declared_total": str(value.declared_total.amount),
            "lines": [_line_content(line) for line in value.lines],
        }
    if type(value) is PurchaseOrder:
        return {
            "schema": _PURCHASE_ORDER_SCHEMA,
            "purchase_order_id": value.purchase_order_id,
            "supplier_id": value.supplier_id,
            "currency": value.currency.value,
            "lines": [_line_content(line) for line in value.lines],
        }
    if type(value) is ValidationPolicy:
        return {
            "schema": _POLICY_SCHEMA,
            "policy_id": value.policy_id,
            "allowed_currencies": [item.value for item in value.allowed_currencies],
        }
    if type(value) is ReconciliationRules:
        return {
            "schema": _RULES_SCHEMA,
            "rules_id": value.rules_id,
            "allow_partial_quantities": value.allow_partial_quantities,
            "require_source_document": value.require_source_document,
            "currency_rules": [
                {
                    "currency": rule.currency.value,
                    "unit_price_tolerance": str(rule.unit_price_tolerance.amount),
                    "total_tolerance": str(rule.total_tolerance.amount),
                    "decimal_places": rule.decimal_places,
                    "rounding": rule.rounding,
                }
                for rule in value.currency_rules
            ],
        }
    raise DomainValidationError(
        "invalid_type", "value", "Use an Invoice, PurchaseOrder or a policy or rules"
    )


def canonical_json(data: Mapping[str, CanonicalValue]) -> bytes:
    """Serialize with sorted keys and no optional whitespace for stable bytes."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: Mapping[str, CanonicalValue]) -> str:
    """Hash canonical JSON; equal content gives equal lowercase hex digests."""
    return hashlib.sha256(canonical_json(data)).hexdigest()


def content_hash(value: Hashable) -> str:
    """Fingerprint an input version; any business field change gives a new hash."""
    return sha256_hex(canonical_content(value))


def extraction_hash(result: ExtractionResult) -> str:
    """Fingerprint the exact extracted fields and evidence a result relied on."""
    if type(result) is not ExtractionResult:
        raise DomainValidationError("invalid_type", "result", "Use ExtractionResult.")
    return sha256_hex(
        {
            "schema": _EXTRACTION_SCHEMA,
            "extractor": result.extractor,
            "fields": [
                {
                    "name": item.name,
                    "status": item.status.value,
                    "value": item.value,
                    "message": item.message,
                    "evidence": [[e.page, e.start, e.end] for e in item.evidence],
                }
                for item in result.fields
            ],
        }
    )
