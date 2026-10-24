"""Sandbox webhook receiver: verify the signature, then act once per event ID.

It stands in for the next system in the process. Its only "business action"
is a row in its own SQLite file noting that a decision arrived; it pays,
sends and books nothing. It imports nothing from ``app``: it checks the
signature with its own code, as a real external receiver would.

``python -m sandbox.receiver`` serves it on 127.0.0.1:8010. A mode can make it
fail on purpose so retries, exhaustion and duplicates can be observed:
``accept`` (default), ``fail`` (503 before acting), ``lose_ack`` (act, then
answer 503 as if the answer was lost) and ``slow`` (answer after a delay).
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

EVENT_HEADER = "X-EWA-Event-Id"
TIMESTAMP_HEADER = "X-EWA-Timestamp"
SIGNATURE_HEADER = "X-EWA-Signature"
MAX_BODY_BYTES = 65_536
# A signed request older or newer than this is refused as a possible replay.
TOLERANCE_SECONDS = 300
MIN_SECRET_LENGTH = 32
DEFAULT_DATABASE = Path("var/sandbox/receiver.sqlite3")
DEFAULT_PORT = 8010


class Mode(StrEnum):
    """How the receiver answers; anything but accept is a deliberate failure."""

    ACCEPT = "accept"
    FAIL = "fail"
    LOSE_ACK = "lose_ack"
    SLOW = "slow"


@dataclass(slots=True)
class Behaviour:
    """Current mode; ``remaining`` failures, then back to accept (None: forever)."""

    mode: Mode = Mode.ACCEPT
    remaining: int | None = None
    delay_seconds: float = 0.0
    # Guards the counter shared by concurrent requests.
    _lock: threading.Lock = field(
        init=False, default_factory=threading.Lock, repr=False, compare=False
    )

    def next_mode(self) -> Mode:
        """Return the mode for this request and count one use of it."""
        with self._lock:
            if self.mode is Mode.ACCEPT or self.remaining is None:
                return self.mode
            if self.remaining <= 0:
                return Mode.ACCEPT
            self.remaining -= 1
            return self.mode

    def set(
        self, mode: Mode, remaining: int | None = None, delay_seconds: float = 0.0
    ) -> None:
        """Switch mode, e.g. between demo scenarios."""
        with self._lock:
            self.mode = mode
            self.remaining = remaining
            self.delay_seconds = delay_seconds


@dataclass(frozen=True, slots=True)
class Action:
    """The one action taken for an event, and how often it was received."""

    event_id: str
    event_type: str
    workflow_id: str
    body_sha256: str
    receipts: int
    created_at: str


class ReceiverStore:
    """SQLite file with one action per event ID and a receipt counter."""

    def __init__(self, path: Path) -> None:
        """Create the tables if needed; each call opens its own connection."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                " event_id TEXT PRIMARY KEY,"
                " event_type TEXT NOT NULL,"
                " workflow_id TEXT NOT NULL,"
                " body_sha256 TEXT NOT NULL,"
                " receipts INTEGER NOT NULL,"
                " created_at TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One short transaction; IMMEDIATE serialises concurrent writers."""
        with closing(sqlite3.connect(self._path, timeout=5.0)) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    def record(
        self, event_id: str, event_type: str, workflow_id: str, body_sha256: str
    ) -> str:
        """Act on a new event; count a repeat. Returns accepted, duplicate or
        conflict (same ID, different body)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT body_sha256 FROM actions WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO actions VALUES (?, ?, ?, ?, 1, ?)",
                    (
                        event_id,
                        event_type,
                        workflow_id,
                        body_sha256,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                return "accepted"
            if row[0] != body_sha256:
                return "conflict"
            connection.execute(
                "UPDATE actions SET receipts = receipts + 1 WHERE event_id = ?",
                (event_id,),
            )
            return "duplicate"

    def actions(self) -> tuple[Action, ...]:
        """Every action taken, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id, event_type, workflow_id, body_sha256, receipts,"
                " created_at FROM actions ORDER BY created_at, event_id"
            ).fetchall()
        return tuple(Action(*row) for row in rows)


def expected_signature(secret: bytes, timestamp: str, body: bytes) -> str:
    """The receiver's own HMAC-SHA256 over ``timestamp.body``."""
    digest = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"v1={digest.hexdigest()}"


def _refuse(status: int, code: str) -> JSONResponse:
    """A short JSON error; nothing from the request is echoed."""
    return JSONResponse({"status": "refused", "code": code}, status_code=status)


def _event_fields(body: bytes, event_id: str) -> tuple[str, str] | None:
    """Event type and workflow ID from a body whose event ID matches the header."""
    try:
        document = json.loads(body)
    except ValueError:
        return None
    if not isinstance(document, dict) or document.get("event_id") != event_id:
        return None
    event_type, workflow_id = document.get("event_type"), document.get("workflow_id")
    if not isinstance(event_type, str) or not isinstance(workflow_id, str):
        return None
    return event_type, workflow_id


def create_receiver(
    store: ReceiverStore,
    secret: str,
    behaviour: Behaviour | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the receiver app; tests pass their own store, mode and clock."""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"Use a secret of at least {MIN_SECRET_LENGTH} characters.")
    key = secret.encode("utf-8")
    mode_source = Behaviour() if behaviour is None else behaviour
    now = clock or (lambda: datetime.now(UTC))
    receiver = FastAPI(title="sandbox-webhook-receiver", docs_url=None, redoc_url=None)
    receiver.state.behaviour = mode_source

    @receiver.get("/health")
    def health() -> dict[str, str]:
        """Liveness only."""
        return {"status": "ok"}

    @receiver.get("/actions")
    def actions() -> list[dict[str, Any]]:
        """Inspect the actions taken; demos and tests count them."""
        return [asdict(item) for item in store.actions()]

    @receiver.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        """Check size, timestamp and signature, then act once per event ID."""
        declared = request.headers.get("content-length", "")
        if not declared.isdigit() or int(declared) > MAX_BODY_BYTES:
            return _refuse(413, "too_large")
        body = await request.body()
        event_id = request.headers.get(EVENT_HEADER, "")
        timestamp = request.headers.get(TIMESTAMP_HEADER, "")
        signature = request.headers.get(SIGNATURE_HEADER, "")
        try:
            UUID(event_id)
            sent_at = int(timestamp)
        except ValueError:
            return _refuse(400, "invalid_headers")
        if abs(now().timestamp() - sent_at) > TOLERANCE_SECONDS:
            return _refuse(401, "stale_timestamp")
        if not hmac.compare_digest(expected_signature(key, timestamp, body), signature):
            return _refuse(401, "bad_signature")
        fields = _event_fields(body, event_id)
        if fields is None:
            return _refuse(400, "invalid_body")
        mode = mode_source.next_mode()
        if mode is Mode.FAIL:
            return _refuse(503, "unavailable")
        if mode is Mode.SLOW:
            await asyncio.sleep(mode_source.delay_seconds)
        outcome = await asyncio.to_thread(
            store.record, event_id, *fields, hashlib.sha256(body).hexdigest()
        )
        if outcome == "conflict":
            return _refuse(409, "event_id_reused")
        if mode is Mode.LOSE_ACK:
            return _refuse(503, "answer_lost")
        return JSONResponse({"status": outcome, "event_id": event_id})

    return receiver


@contextmanager
def serving(app: FastAPI) -> Iterator[str]:
    """Serve on a free 127.0.0.1 port in a thread; yield the webhook URL.

    Demos and evaluations use this to talk to a real HTTP server without a
    second terminal. The socket is bound before the thread starts, so the
    port is known and nobody else can take it.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, log_level="warning", lifespan="off", access_log=False, ws="none"
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("The sandbox receiver did not start.")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/webhook"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the receiver on loopback with the secret from the environment."""
    parser = argparse.ArgumentParser(
        prog="python -m sandbox.receiver",
        description="Fictional sandbox webhook receiver (loopback only).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--mode", choices=[m.value for m in Mode], default="accept")
    parser.add_argument(
        "--failures",
        type=int,
        default=None,
        help="apply the mode to this many requests, then accept (default: always)",
    )
    parser.add_argument("--delay", type=float, default=6.0, help="seconds for slow")
    arguments = parser.parse_args(argv)
    secret = os.environ.get("SANDBOX_WEBHOOK_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        print(
            f"Set SANDBOX_WEBHOOK_SECRET ({MIN_SECRET_LENGTH}+ characters).",
            file=sys.stderr,
        )
        return 2
    behaviour = Behaviour(Mode(arguments.mode), arguments.failures, arguments.delay)
    app = create_receiver(ReceiverStore(arguments.database), secret, behaviour)
    # Plain HTTP only: the receiver has no use for WebSockets.
    uvicorn.run(
        app, host="127.0.0.1", port=arguments.port, log_level="warning", ws="none"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
