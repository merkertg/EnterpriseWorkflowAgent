"""The interface's HTTP client, keys and presenters, without a server."""

import json
import re
from typing import Any

import pytest

from ui.client import ApiClient, ApiError, RawResponse
from ui.keys import command_key
from ui.presenters import (
    delivery_notices,
    describe_error,
    discrepancy_rows,
    field_rows,
    outbox_rows,
    trace_rows,
)

TOKEN = "ewa_" + "a" * 43


class Recorder:
    """A transport that records requests and replies from a queue."""

    def __init__(self, *replies: RawResponse | Exception) -> None:
        """Keep the replies in order."""
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> RawResponse:
        """Record one request and return or raise the next reply."""
        self.calls.append((method, url, headers, body))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _json(status: int, data: Any) -> RawResponse:
    """A JSON reply."""
    return RawResponse(status, json.dumps(data).encode())


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://host", "127.0.0.1:8003", "http://"]
)
def test_only_http_urls_are_accepted(url: str) -> None:
    """urllib could open file:// URLs; the client refuses them."""
    with pytest.raises(ValueError):
        ApiClient(url, TOKEN)


def test_requests_carry_token_key_and_quoted_ids() -> None:
    """Path segments are quoted, so an ID cannot change the path."""
    transport = Recorder(_json(200, {"state": "approved", "replayed": False}))
    client = ApiClient("http://api.test/", TOKEN, transport)
    client.approve(
        "WF/1?x=../",
        version=2,
        reconciliation_snapshot_id="snap",
        reason="ok",
        key="ui-approve-1",
    )
    [(method, url, headers, body)] = transport.calls
    assert (method, url) == (
        "POST",
        "http://api.test/workflows/WF%2F1%3Fx%3D..%2F/approve",
    )
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Idempotency-Key"] == "ui-approve-1"
    assert json.loads(body or b"") == {
        "expected_version": 2,
        "reconciliation_snapshot_id": "snap",
        "reason": "ok",
    }


def test_uploads_send_raw_bytes_with_the_name_in_the_query() -> None:
    """The document goes as octet-stream; the name is only a label."""
    transport = Recorder(_json(200, {"extraction_status": "complete"}))
    ApiClient("http://api.test", TOKEN, transport).upload(b"%PDF-1.4", "a b.pdf")
    [(method, url, headers, body)] = transport.calls
    assert (method, url, body) == (
        "POST",
        "http://api.test/documents?name=a+b.pdf",
        b"%PDF-1.4",
    )
    assert headers["Content-Type"] == "application/octet-stream"


def test_errors_keep_code_message_and_details() -> None:
    """A stale snapshot's reasons reach the interface."""
    transport = Recorder(
        _json(
            409,
            {
                "error": {
                    "code": "stale_reconciliation",
                    "message": "Stale.",
                    "reasons": ["purchase_order_changed"],
                }
            },
        )
    )
    with pytest.raises(ApiError) as caught:
        ApiClient("http://api.test", TOKEN, transport).reject(
            "WF-1", version=2, reason="no", key="k"
        )
    error = caught.value
    assert (error.status, error.code, error.details) == (
        409,
        "stale_reconciliation",
        {"reasons": ["purchase_order_changed"]},
    )
    title, hint = describe_error(error)
    assert "purchase_order_changed" in title and "reconcile again" in hint


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (RawResponse(502, b"<html>bad gateway</html>"), "unexpected_response"),
        (ConnectionRefusedError("refused"), "api_unreachable"),
        (TimeoutError("slow"), "api_unreachable"),
    ],
)
def test_transport_failures_never_show_the_token(
    reply: RawResponse | Exception, code: str
) -> None:
    """Broken answers and unreachable servers give a code without secrets."""
    with pytest.raises(ApiError) as caught:
        ApiClient("http://api.test", TOKEN, Recorder(reply)).me()
    assert caught.value.code == code
    assert TOKEN not in str(caught.value)


def test_missing_document_or_reconciliation_is_none() -> None:
    """404 means 'nothing yet' for these two reads; other errors still raise."""
    not_found = _json(404, {"error": {"code": "not_found", "message": "None."}})
    client = ApiClient("http://api.test", TOKEN, Recorder(not_found, not_found))
    assert client.workflow_document("WF-1") is None
    assert client.latest_reconciliation("WF-1") is None
    forbidden = _json(401, {"error": {"code": "unauthenticated", "message": "No."}})
    with pytest.raises(ApiError):
        ApiClient("http://api.test", TOKEN, Recorder(forbidden)).workflow_document("x")


def test_command_keys_are_stable_short_and_specific() -> None:
    """Same intent, same key; another version or action, another key."""
    first = command_key("approve", "WF-1" * 40, 2, "snap")
    assert first == command_key("approve", "WF-1" * 40, 2, "snap")
    assert len(first) <= 60 and re.fullmatch(r"[A-Za-z0-9._:-]+", first)
    assert first != command_key("approve", "WF-1" * 40, 3, "snap")
    assert first != command_key("reject", "WF-1" * 40, 2, "snap")
    with pytest.raises(ValueError):
        command_key("pay", "WF-1")


def test_presenters_reshape_without_deciding() -> None:
    """Rows copy server values; nothing is computed or filtered."""
    document = {
        "fields": [
            {
                "name": "declared_total",
                "status": "found",
                "value": "30.00",
                "citations": [
                    {"page": 1, "line_number": 8, "line": "Total due: 30.00"}
                ],
            },
            {"name": "currency", "status": "missing", "value": None, "citations": []},
        ]
    }
    assert field_rows(document)[0]["source line"] == "Total due: 30.00"
    assert field_rows(document)[1]["page"] == ""
    report = {
        "discrepancies": [
            {
                "code": "unit_price_mismatch",
                "field": "lines[1].unit_price",
                "expected": "12.50 USD ± 0.05",
                "actual": "12.40 USD",
                "rule": "USD.unit_price_tolerance=0.05",
                "sources": [{"page": 1, "line_number": 6, "quote": "12.40"}],
            }
        ]
    }
    assert discrepancy_rows(report)[0]["source"] == "p.1 l.6: 12.40"
    run = {
        "steps": [
            {
                "seq": 1,
                "kind": "refused_call",
                "tool_name": "approve_workflow",
                "error_code": "unknown_tool",
                "result_bytes": None,
            }
        ]
    }
    assert trace_rows(run) == [
        {
            "step": 1,
            "kind": "refused_call",
            "tool": "approve_workflow",
            "code": "unknown_tool",
            "bytes": "",
        }
    ]


def test_delivery_rows_and_notices_copy_the_server_state() -> None:
    """Pending needs no notice; retries warn; exhaustion is an error."""

    def event(status: str) -> dict[str, Any]:
        """A minimal event in one delivery status."""
        return {
            "event_id": "e1",
            "event_type": "invoice.rejected",
            "workflow_version": 3,
            "payload": {},
            "delivery": {
                "status": status,
                "attempts": 2,
                "total_attempts": 4,
                "last_error": None if status == "delivered" else "http_503",
                "next_attempt_at": "soon",
            },
        }

    assert outbox_rows([event("retry_scheduled")]) == [
        {
            "event": "invoice.rejected",
            "version": 3,
            "decided by": "",
            "delivery": "retry_scheduled",
            "attempts": 4,
            "last error": "http_503",
        }
    ]
    assert delivery_notices([event("pending"), event("delivered")]) == []
    levels = [
        level
        for level, _ in delivery_notices([event("retry_scheduled"), event("exhausted")])
    ]
    assert levels == ["warning", "error"]
