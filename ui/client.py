"""HTTP client for the business API: the interface's only way to the data.

It uses only the standard library. The transport is a plain function, so
tests can send the same requests through FastAPI's in-process client. The
bearer token is sent with every request and never appears in an error.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlparse

DEFAULT_API_URL = "http://127.0.0.1:8003"


@dataclass(frozen=True, slots=True)
class RawResponse:
    """Status code and body bytes of one HTTP exchange."""

    status: int
    body: bytes


type Transport = Callable[[str, str, dict[str, str], bytes | None], RawResponse]


class ApiError(Exception):
    """A refused or failed request: status, stable code and short message."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Keep the server's code and any reasons or problems it sent."""
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{status} {code}: {message}")


def urllib_transport(timeout: float = 30.0) -> Transport:
    """Send requests with urllib; HTTP errors still return their body."""

    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> RawResponse:
        """Perform one request and return its status and body."""
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return RawResponse(response.status, response.read())
        except urllib.error.HTTPError as error:
            return RawResponse(error.code, error.read())

    return send


@dataclass
class ApiClient:
    """Typed calls for every endpoint the interface uses."""

    base_url: str
    token: str
    transport: Transport = field(default_factory=urllib_transport)

    def __post_init__(self) -> None:
        """Accept only http(s) URLs, so a file:// URL cannot be opened."""
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("The API URL must start with http:// or https://.")
        self.base_url = self.base_url.rstrip("/")

    # Transport --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        key: str | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        """Send one request and decode JSON, or raise ApiError."""
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content is not None:
            body = content
            headers["Content-Type"] = "application/octet-stream"
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            response = self.transport(method, url, headers, body)
        except (OSError, ValueError) as error:
            raise ApiError(
                0, "api_unreachable", f"Cannot reach the API at {self.base_url}."
            ) from error
        try:
            data = json.loads(response.body) if response.body else None
        except ValueError:
            data = None
        if 200 <= response.status < 300:
            return data
        problem = data.get("error") if isinstance(data, dict) else None
        if not isinstance(problem, dict):
            raise ApiError(response.status, "unexpected_response", "Unexpected answer.")
        details = {k: v for k, v in problem.items() if k not in ("code", "message")}
        raise ApiError(
            response.status,
            str(problem.get("code", "error")),
            str(problem.get("message", "")),
            details,
        )

    @staticmethod
    def _segment(value: str) -> str:
        """Quote one path segment, so an ID cannot change the path."""
        return quote(value, safe="")

    # Identity and lists ------------------------------------------------------

    def me(self) -> dict[str, Any]:
        """The caller's roles, units and permissions."""
        result: dict[str, Any] = self._request("GET", "/me")
        return result

    def list_workflows(self, state: str | None = None) -> list[dict[str, Any]]:
        """Recently changed workflows in the caller's units."""
        result: list[dict[str, Any]] = self._request(
            "GET", "/workflows", query=None if state is None else {"state": state}
        )
        return result

    def workflow(self, workflow_id: str) -> dict[str, Any]:
        """One workflow with pinned versions and decision."""
        result: dict[str, Any] = self._request(
            "GET", f"/workflows/{self._segment(workflow_id)}"
        )
        return result

    def workflow_document(self, workflow_id: str) -> dict[str, Any] | None:
        """Cited fields of the invoice's source document, or None if absent."""
        try:
            result: dict[str, Any] = self._request(
                "GET", f"/workflows/{self._segment(workflow_id)}/document"
            )
        except ApiError as error:
            if error.status == 404:
                return None
            raise
        return result

    def reference(self, kind: str, business_id: str) -> dict[str, Any]:
        """Latest version of purchase-orders, policies or reconciliation-rules."""
        result: dict[str, Any] = self._request(
            "GET", f"/reference/{self._segment(kind)}/{self._segment(business_id)}"
        )
        return result

    # Documents and workflow commands -------------------------------------

    def upload(self, content: bytes, name: str) -> dict[str, Any]:
        """Upload raw document bytes; the server detects the format."""
        result: dict[str, Any] = self._request(
            "POST", "/documents", content=content, query={"name": name}
        )
        return result

    def start(
        self,
        workflow_id: str,
        *,
        business_unit: str,
        invoice_version_id: str,
        purchase_order_version_id: str | None,
        policy_version_id: str,
        key: str,
    ) -> dict[str, Any]:
        """Start a workflow with explicit input versions."""
        result: dict[str, Any] = self._request(
            "POST",
            "/workflows",
            key=key,
            json_body={
                "workflow_id": workflow_id,
                "business_unit": business_unit,
                "invoice_version_id": invoice_version_id,
                "purchase_order_version_id": purchase_order_version_id,
                "policy_version_id": policy_version_id,
            },
        )
        return result

    def check(self, workflow_id: str, *, version: int, key: str) -> dict[str, Any]:
        """Run the basic check on the pinned inputs."""
        result: dict[str, Any] = self._request(
            "POST",
            f"/workflows/{self._segment(workflow_id)}/check",
            key=key,
            json_body={"expected_version": version},
        )
        return result

    def revise(
        self,
        workflow_id: str,
        *,
        version: int,
        invoice_version_id: str,
        purchase_order_version_id: str | None,
        policy_version_id: str,
        key: str,
    ) -> dict[str, Any]:
        """Pin other input versions."""
        result: dict[str, Any] = self._request(
            "POST",
            f"/workflows/{self._segment(workflow_id)}/revise",
            key=key,
            json_body={
                "expected_version": version,
                "invoice_version_id": invoice_version_id,
                "purchase_order_version_id": purchase_order_version_id,
                "policy_version_id": policy_version_id,
            },
        )
        return result

    def reconcile(self, workflow_id: str, rules_version_id: str) -> dict[str, Any]:
        """Reconcile under a rule version; equal inputs reuse the snapshot."""
        result: dict[str, Any] = self._request(
            "POST",
            f"/workflows/{self._segment(workflow_id)}/reconciliations",
            json_body={"rules_version_id": rules_version_id},
        )
        return result

    def latest_reconciliation(self, workflow_id: str) -> dict[str, Any] | None:
        """The newest reconciliation report, or None if there is none yet."""
        try:
            result: dict[str, Any] = self._request(
                "GET",
                f"/workflows/{self._segment(workflow_id)}/reconciliations/latest",
            )
        except ApiError as error:
            if error.status == 404:
                return None
            raise
        return result

    def approve(
        self,
        workflow_id: str,
        *,
        version: int,
        reconciliation_snapshot_id: str,
        reason: str,
        key: str,
    ) -> dict[str, Any]:
        """Approve on a reconciliation snapshot; the server re-checks it."""
        result: dict[str, Any] = self._request(
            "POST",
            f"/workflows/{self._segment(workflow_id)}/approve",
            key=key,
            json_body={
                "expected_version": version,
                "reconciliation_snapshot_id": reconciliation_snapshot_id,
                "reason": reason,
            },
        )
        return result

    def reject(
        self, workflow_id: str, *, version: int, reason: str, key: str
    ) -> dict[str, Any]:
        """Reject with a reason."""
        result: dict[str, Any] = self._request(
            "POST",
            f"/workflows/{self._segment(workflow_id)}/reject",
            key=key,
            json_body={"expected_version": version, "reason": reason},
        )
        return result

    # History and assistant -------------------------------------------------

    def audit(self, workflow_id: str) -> list[dict[str, Any]]:
        """Audit events in commit order."""
        result: list[dict[str, Any]] = self._request(
            "GET", f"/workflows/{self._segment(workflow_id)}/audit"
        )
        return result

    def outbox(self, workflow_id: str) -> list[dict[str, Any]]:
        """Decision events waiting for delivery."""
        result: list[dict[str, Any]] = self._request(
            "GET", f"/workflows/{self._segment(workflow_id)}/outbox"
        )
        return result

    def run_agent(self, workflow_id: str) -> dict[str, Any]:
        """Run the bounded review agent; the draft never changes anything."""
        result: dict[str, Any] = self._request(
            "POST", f"/workflows/{self._segment(workflow_id)}/agent-runs"
        )
        return result
