"""The Streamlit page in edge states, run headless with a fake API client.

Needs the ``ui`` dependency group; without it these tests are skipped.
"""

from pathlib import Path
from typing import Any

import pytest

from ui.client import ApiClient, ApiError

testing = pytest.importorskip("streamlit.testing.v1")
APP = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")
ME = {
    "user_id": "approver-1",
    "roles": ["approver"],
    "units": ["UNIT-A"],
    "permissions": ["assist", "decide", "reconcile", "view"],
}
WORKFLOW = {
    "workflow_id": "WF-1",
    "state": "awaiting_approval",
    "version": 2,
    "business_unit": "UNIT-A",
    "created_by": "clerk-1",
    "decision": None,
    "issue_codes": [],
    "invoice_version": {"version_id": "i1", "business_id": "INV-1"},
    "purchase_order_version": {"version_id": "p1", "business_id": "PO-1"},
    "policy_version": {"version_id": "c1", "business_id": "policy"},
}
SUMMARY = {
    "workflow_id": "WF-1",
    "invoice_id": "INV-1",
    "state": "awaiting_approval",
    "version": 2,
    "business_unit": "UNIT-A",
    "created_by": "clerk-1",
}


class FakeClient(ApiClient):
    """Canned answers; any method listed in ``errors`` raises that error."""

    def __init__(self, **overrides: Any) -> None:
        """Start from a healthy workflow and apply overrides."""
        super().__init__("http://fake.test", "ewa_fake")
        self.items = overrides.pop("items", [SUMMARY])
        self.report = overrides.pop("report", None)
        self.upload_result = overrides.pop("upload_result", None)
        self.agent = overrides.pop("agent", None)
        self.errors: dict[str, ApiError] = overrides.pop("errors", {})
        self.sent: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        """Raise the configured error for this call."""
        self.sent.append(name)
        if name in self.errors:
            raise self.errors[name]

    def me(self) -> dict[str, Any]:
        """The signed-in approver."""
        self._maybe_fail("me")
        return ME

    def list_workflows(self, state: str | None = None) -> list[dict[str, Any]]:
        """The configured list."""
        self._maybe_fail("list_workflows")
        return list(self.items)

    def workflow(self, workflow_id: str) -> dict[str, Any]:
        """The canned workflow."""
        self._maybe_fail("workflow")
        return WORKFLOW

    def workflow_document(self, workflow_id: str) -> dict[str, Any] | None:
        """No source document."""
        self._maybe_fail("workflow_document")
        return None

    def latest_reconciliation(self, workflow_id: str) -> dict[str, Any] | None:
        """The configured report."""
        self._maybe_fail("latest_reconciliation")
        report: dict[str, Any] | None = self.report
        return report

    def audit(self, workflow_id: str) -> list[dict[str, Any]]:
        """No history."""
        return []

    def outbox(self, workflow_id: str) -> list[dict[str, Any]]:
        """No events."""
        return []

    def approve(self, workflow_id: str, **kwargs: Any) -> dict[str, Any]:
        """Approve or fail as configured."""
        self._maybe_fail("approve")
        return {"state": "approved", "version": 3, "replayed": False}

    def upload(self, content: bytes, name: str) -> dict[str, Any]:
        """Upload or fail as configured."""
        self._maybe_fail("upload")
        assert self.upload_result is not None
        return dict(self.upload_result)

    def run_agent(self, workflow_id: str) -> dict[str, Any]:
        """The configured agent run."""
        self._maybe_fail("run_agent")
        assert self.agent is not None
        return dict(self.agent)


def _run(client: ApiClient | None = None) -> Any:
    """Run the page once, optionally with an injected client."""
    app = testing.AppTest.from_file(APP, default_timeout=30)
    if client is not None:
        app.session_state["api_client"] = client
    return app.run()


def _texts(elements: Any) -> list[str]:
    """Values of message elements."""
    return [str(item.value) for item in elements]


def test_without_a_token_the_page_asks_for_one() -> None:
    """Nothing is requested until a token is pasted."""
    app = _run()
    assert "Paste your API token" in " ".join(_texts(app.info))
    assert app.text_input(key="api_token").value == ""
    app.text_input(key="api_url").input("file:///etc/passwd")
    app.text_input(key="api_token").input("ewa_x").run()
    assert "http://" in " ".join(_texts(app.sidebar.error))
    assert not app.exception


def test_rejected_token_shows_the_server_code() -> None:
    """A 401 is shown with its hint and nothing else is loaded."""
    error = ApiError(401, "unauthenticated", "A valid API token is required.")
    app = _run(FakeClient(errors={"me": error}))
    assert "401 unauthenticated" in " ".join(_texts(app.error))
    assert "revoked" in " ".join(_texts(app.caption))


def test_empty_list_and_missing_inputs_are_explained() -> None:
    """No workflows, no document and no reconciliation are each stated."""
    assert "No workflows" in " ".join(_texts(_run(FakeClient(items=[])).info))
    app = _run(FakeClient())
    info = " ".join(_texts(app.info))
    assert "no source document" in info and "No reconciliation yet" in info
    app.button(key="do_approve").click().run()
    assert "reconcile the workflow first" in " ".join(_texts(app.error))


def test_refused_approval_shows_reasons_and_hint() -> None:
    """A stale reconciliation is explained, not hidden."""
    report = {
        "snapshot_id": "s1",
        "status": "matched",
        "current": False,
        "stale_reasons": ["purchase_order_changed"],
        "rules_version": {"business_id": "rules", "version_no": 1},
        "discrepancies": [],
    }
    error = ApiError(
        409,
        "stale_reconciliation",
        "The reconciliation is stale.",
        {"reasons": ["purchase_order_changed"]},
    )
    client = FakeClient(report=report, errors={"approve": error})
    app = _run(client)
    assert "stale: purchase_order_changed" in " ".join(m.value for m in app.markdown)
    app.text_input(key="decision_reason").input("Looks fine")
    app.button(key="do_approve").click().run()
    message = " ".join(_texts(app.error))
    assert "409 stale_reconciliation" in message and "purchase_order_changed" in message
    assert "reconcile again" in " ".join(_texts(app.caption))
    assert client.sent.count("approve") == 1


def test_agent_override_is_visible() -> None:
    """A replaced draft shows both steps and the reason."""
    agent = {
        "workflow_id": "WF-1",
        "stop_reason": "final_answer",
        "proposed_next_step": "ready_for_approval",
        "next_step": "needs_human_review",
        "override_reason": "reconciliation_not_matched",
        "explanation": "All good.",
        "steps": [],
    }
    app = _run(FakeClient(agent=agent))
    app.button(key="do_agent").click().run()
    warning = " ".join(_texts(app.warning))
    assert "ready_for_approval" in warning and "reconciliation_not_matched" in warning


@pytest.mark.parametrize(
    ("result", "error", "expected"),
    [
        (
            None,
            ApiError(422, "unsupported_type", "Not text or PDF."),
            "422 unsupported_type",
        ),
        (
            {
                "extraction_status": "needs_review",
                "reused_document": False,
                "problems": [{"code": "missing_field", "field": "currency"}],
                "fields": [],
                "invoice_version": None,
            },
            None,
            "No invoice was created",
        ),
    ],
)
def test_upload_refusals_and_reviews_are_shown(
    result: dict[str, Any] | None, error: ApiError | None, expected: str
) -> None:
    """Refused documents and incomplete extractions do not start anything."""
    client = FakeClient(
        upload_result=result, errors={} if error is None else {"upload": error}
    )
    app = _run(client)
    app.radio(key="page").set_value("New invoice").run()
    app.text_area(key="invoice_text").input("Invoice number: X").run()
    app.button(key="do_upload").click().run()
    shown = " ".join(_texts(app.error) + _texts(app.info) + _texts(app.warning))
    assert expected in shown
    assert not app.exception
