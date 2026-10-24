"""The webhook sender over real loopback HTTP: answers, failures, no detours."""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.delivery import WebhookMessage, build_message
from app.application.ports import OutboxEvent, OutboxEventType
from app.infrastructure.webhook.sender import UrllibWebhookSender
from sandbox.receiver import Behaviour, Mode, ReceiverStore, create_receiver, serving

SECRET = "fictional-signing-secret-" + "y" * 16


def _message(secret: str = SECRET) -> WebhookMessage:
    """A signed message for a new fictional event."""
    event = OutboxEvent(
        uuid4(), OutboxEventType.INVOICE_REJECTED, "WF-S", 3, {}, datetime.now(UTC)
    )
    return build_message(event, secret.encode(), int(time.time()))


@pytest.fixture
def receiver(tmp_path: Path) -> Iterator[tuple[str, Behaviour, ReceiverStore]]:
    """The sandbox receiver served on a free loopback port."""
    store = ReceiverStore(tmp_path / "receiver.sqlite3")
    behaviour = Behaviour()
    with serving(create_receiver(store, SECRET, behaviour)) as url:
        yield url, behaviour, store


def test_delivery_and_duplicate_flag(
    receiver: tuple[str, Behaviour, ReceiverStore],
) -> None:
    """The second send of one event is recognised; the receiver acts once."""
    url, _behaviour, store = receiver
    sender = UrllibWebhookSender(url)
    message = _message()
    first = sender.send(message, 2)
    second = sender.send(message, 2)
    assert (first.http_status, first.duplicate, first.delivered) == (200, False, True)
    assert (second.http_status, second.duplicate) == (200, True)
    [action] = store.actions()
    assert action.receipts == 2


def test_error_answers_and_timeouts_become_codes(
    receiver: tuple[str, Behaviour, ReceiverStore],
) -> None:
    """503, a wrong signature and a slow answer never raise."""
    url, behaviour, _store = receiver
    sender = UrllibWebhookSender(url)
    behaviour.set(Mode.FAIL, remaining=1)
    down = sender.send(_message(), 2)
    assert (down.http_status, down.error_code, down.delivered) == (
        503,
        "http_503",
        False,
    )
    forged = sender.send(_message("z" * 40), 2)
    assert (forged.http_status, forged.error_code) == (401, "http_401")
    behaviour.set(Mode.SLOW, remaining=1, delay_seconds=1.0)
    slow = sender.send(_message(), 0.3)
    assert (slow.http_status, slow.error_code) == (None, "timeout")


def test_a_closed_port_is_a_connection_error(tmp_path: Path) -> None:
    """Nothing listening any more: a code, not an exception."""
    store = ReceiverStore(tmp_path / "gone.sqlite3")
    with serving(create_receiver(store, SECRET)) as gone:
        pass
    answer = UrllibWebhookSender(gone).send(_message(), 1)
    assert (answer.http_status, answer.error_code) == (None, "connection_error")


class _Redirect(BaseHTTPRequestHandler):
    """Answers every POST with a redirect to another host."""

    def do_POST(self) -> None:  # noqa: N802 - name required by http.server
        """Send 307 to a place the sender must not follow."""
        self.send_response(307)
        self.send_header("Location", "http://198.51.100.7/elsewhere")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        """Keep test output quiet."""


def test_redirects_are_refused() -> None:
    """A receiver answer cannot move the event to another destination."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/webhook"
        answer = UrllibWebhookSender(url).send(_message(), 2)
    finally:
        server.shutdown()
        server.server_close()
    assert (answer.http_status, answer.error_code) == (307, "redirect_refused")


def test_proxy_variables_are_ignored(
    receiver: tuple[str, Behaviour, ReceiverStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy from the environment cannot see or divert the event."""
    url, _behaviour, store = receiver
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    answer = UrllibWebhookSender(url).send(_message(), 2)
    assert answer.delivered and len(store.actions()) == 1


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8010/webhook", "https://example.com/hook", "file:///etc/hosts"],
)
def test_sender_accepts_only_loopback_urls(url: str) -> None:
    """The destination is checked when the sender is built."""
    with pytest.raises(ValueError):
        UrllibWebhookSender(url)
