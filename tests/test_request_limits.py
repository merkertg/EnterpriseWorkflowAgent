"""Command bodies are bounded before a route reads them; uploads are exempt."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.limits import MAX_JSON_BODY_BYTES


def test_oversized_command_body_is_refused_with_413(client: TestClient) -> None:
    """The size is checked from Content-Length, before authentication."""
    body = b"{" + b" " * MAX_JSON_BODY_BYTES + b"}"
    answer = client.post(
        "/workflows/WF-1/approve",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "too_large"


def _chunks() -> Iterator[bytes]:
    """A body sent without a declared length."""
    yield b'{"expected_version": '
    yield b"1}"


def test_chunked_command_body_needs_a_length(client: TestClient) -> None:
    """Without Content-Length the body is refused with 411."""
    answer = client.post(
        "/workflows/WF-1/check",
        content=_chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert answer.status_code == 411
    assert answer.json()["error"]["code"] == "length_required"


@pytest.mark.parametrize(
    ("method", "path", "content"),
    [
        ("post", "/workflows/WF-1/check", b'{"expected_version": 1}'),
        ("get", "/workflows", None),
        # Uploads stream with their own 1 MiB limit, so they pass this check.
        ("post", "/documents", b"x" * (MAX_JSON_BODY_BYTES + 1)),
    ],
)
def test_small_bodies_reads_and_uploads_pass_through(
    client: TestClient, method: str, path: str, content: bytes | None
) -> None:
    """These reach the routes, which answer 503 without a database."""
    answer = client.request(method, path, content=content)
    assert answer.status_code == 503
