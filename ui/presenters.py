"""Turn API answers into table rows and readable hints.

These functions only reshape what the server returned. They decide nothing:
whether an action is allowed is always the server's answer.
"""

from typing import Any

from ui.client import ApiError

# What an employee can do next, keyed by the server's stable error code.
ERROR_HINTS = {
    "api_unreachable": "Start the API (uvicorn) and check the API URL.",
    "unauthenticated": "Paste a valid token; it may be revoked or the user disabled.",
    "missing_permission": "Your roles do not allow this action in this unit.",
    "self_approval_forbidden": "You started this workflow; another approver decides.",
    "not_found": "It does not exist or belongs to another business unit.",
    "version_conflict": "Someone changed the workflow; the page shows the new version.",
    "invalid_transition": "The workflow's state does not allow this command.",
    "stale_validation": "Run the basic check again on the current inputs.",
    "stale_reconciliation": "Inputs changed after reconciling: reconcile again.",
    "reconciliation_not_matched": "Discrepancies were found; review or reject.",
    "reconciliation_not_for_workflow": "Use a reconciliation of this workflow.",
    "idempotency_key_reused": "This command was already sent with other data.",
    "workflow_exists": "Choose another workflow ID.",
    "invalid_request": "Fill in every field; the reason must not be empty.",
    "too_large": "The document is larger than 1 MiB.",
    "unsupported_type": "Use UTF-8 text or a PDF with a text layer.",
    "no_text_layer": "Scanned PDFs are not supported (no OCR).",
    "database_not_configured": "Start the API with DATABASE_URL set.",
    "database_unavailable": "Start PostgreSQL and run alembic upgrade head.",
}


def describe_error(error: ApiError) -> tuple[str, str]:
    """A one-line title with the server's code and a hint, if known."""
    title = f"{error.status} {error.code}: {error.message}"
    reasons = error.details.get("reasons")
    if isinstance(reasons, list) and reasons:
        title += " Reasons: " + ", ".join(str(item) for item in reasons) + "."
    return title, ERROR_HINTS.get(error.code, "")


def workflow_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows for the workflow list."""
    return [
        {
            "workflow": item["workflow_id"],
            "invoice": item["invoice_id"],
            "state": item["state"],
            "version": item["version"],
            "unit": item["business_unit"],
            "started by": item["created_by"] or "",
        }
        for item in items
    ]


def field_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per extracted field with its first citation."""
    rows = []
    for item in document["fields"]:
        citation = item["citations"][0] if item["citations"] else None
        rows.append(
            {
                "field": item["name"],
                "status": item["status"],
                "value": item["value"] or "",
                "page": "" if citation is None else citation["page"],
                "line": "" if citation is None else citation["line_number"],
                "source line": "" if citation is None else citation["line"],
            }
        )
    return rows


def upload_rows(upload: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for the fields of a fresh upload."""
    return [
        {"field": item["name"], "status": item["status"], "value": item["value"] or ""}
        for item in upload["fields"]
    ]


def extracted_value(upload: dict[str, Any], name: str) -> str:
    """The value of one uploaded field, or an empty string."""
    for item in upload["fields"]:
        if item["name"] == name and item["value"]:
            return str(item["value"])
    return ""


def discrepancy_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Discrepancies with expected and actual values and quoted sources."""
    return [
        {
            "code": item["code"],
            "field": item["field"],
            "expected": item["expected"] or "",
            "actual": item["actual"] or "",
            "rule": item["rule"],
            "source": "; ".join(
                f"p.{s['page']} l.{s['line_number']}: {s['quote']}"
                for s in item["sources"]
            ),
        }
        for item in report["discrepancies"]
    ]


def audit_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Audit events with actor and links to snapshot and outbox event."""
    return [
        {
            "version": item["workflow_version"],
            "command": item["command"],
            "state": item["new_state"],
            "actor": item["actor_id"] or "",
            "source": item["source"],
            "reconciliation": "yes" if item["reconciliation_snapshot_id"] else "",
            "outbox": "yes" if item["outbox_event_id"] else "",
        }
        for item in events
    ]


def outbox_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stored decision events."""
    return [
        {
            "event": item["event_type"],
            "version": item["workflow_version"],
            "decided by": item["payload"].get("decided_by", ""),
        }
        for item in events
    ]


def trace_rows(run: dict[str, Any]) -> list[dict[str, Any]]:
    """The agent's trace: step kinds, tools, codes and sizes."""
    return [
        {
            "step": item["seq"],
            "kind": item["kind"],
            "tool": item["tool_name"] or "",
            "code": item["error_code"] or "",
            "bytes": "" if item["result_bytes"] is None else item["result_bytes"],
        }
        for item in run["steps"]
    ]
