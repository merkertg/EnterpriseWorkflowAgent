"""The sandbox receiver: signature, freshness, size, deduplication and modes."""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sandbox.receiver import (
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    Behaviour,
    Mode,
    ReceiverStore,
    create_receiver,
    expected_signature,
    main,
)

SECRET = "fictional-receiver-secret-" + "r" * 16


def _body(event_id: str, workflow_id: str = "WF-R") -> bytes:
    """A minimal valid event body."""
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": "invoice.approved",
            "workflow_id": workflow_id,
        }
    ).encode()


def _headers(
    event_id: str, body: bytes, *, secret: str = SECRET, age: int = 0
) -> dict[str, str]:
    """Signed headers for a body, optionally signed some seconds ago."""
    timestamp = str(int(time.time()) - age)
    return {
        "Content-Type": "application/json",
        EVENT_HEADER: event_id,
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: expected_signature(secret.encode(), timestamp, body),
    }


@pytest.fixture
def setup(tmp_path: Path) -> tuple[TestClient, ReceiverStore, Behaviour]:
    """A receiver over a fresh SQLite file."""
    store = ReceiverStore(tmp_path / "receiver.sqlite3")
    behaviour = Behaviour()
    return TestClient(create_receiver(store, SECRET, behaviour)), store, behaviour


def test_one_action_per_event_id(
    setup: tuple[TestClient, ReceiverStore, Behaviour],
) -> None:
    """A redelivery is answered as a duplicate and only counted."""
    client, store, _behaviour = setup
    event_id = str(uuid4())
    body = _body(event_id)
    first = client.post("/webhook", content=body, headers=_headers(event_id, body))
    second = client.post("/webhook", content=body, headers=_headers(event_id, body))
    assert first.json() == {"status": "accepted", "event_id": event_id}
    assert second.json()["status"] == "duplicate"
    [action] = store.actions()
    assert (action.event_id, action.receipts) == (event_id, 2)
    assert client.get("/actions").json()[0]["receipts"] == 2


def test_same_id_with_another_body_is_a_conflict(
    setup: tuple[TestClient, ReceiverStore, Behaviour],
) -> None:
    """An event ID cannot be reused for different content."""
    client, store, _behaviour = setup
    event_id = str(uuid4())
    body = _body(event_id)
    client.post("/webhook", content=body, headers=_headers(event_id, body))
    other = _body(event_id, "WF-OTHER")
    answer = client.post("/webhook", content=other, headers=_headers(event_id, other))
    assert (answer.status_code, answer.json()["code"]) == (409, "event_id_reused")
    assert store.actions()[0].receipts == 1


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"secret": "w" * 40}, 401, "bad_signature"),
        ({"age": 301}, 401, "stale_timestamp"),
        ({"age": -301}, 401, "stale_timestamp"),
    ],
)
def test_unsigned_or_replayed_requests_take_no_action(
    setup: tuple[TestClient, ReceiverStore, Behaviour],
    change: dict[str, object],
    status: int,
    code: str,
) -> None:
    """A wrong secret or a timestamp outside ±300 s is refused."""
    client, store, _behaviour = setup
    event_id = str(uuid4())
    body = _body(event_id)
    headers = _headers(event_id, body, **change)  # type: ignore[arg-type]
    answer = client.post("/webhook", content=body, headers=headers)
    assert (answer.status_code, answer.json()["code"]) == (status, code)
    assert store.actions() == ()


def test_malformed_requests_are_refused(
    setup: tuple[TestClient, ReceiverStore, Behaviour],
) -> None:
    """Bad headers, a mismatched body ID and oversized bodies."""
    client, store, _behaviour = setup
    event_id = str(uuid4())
    body = _body(event_id)
    headers = _headers(event_id, body)
    bad_id = {**headers, EVENT_HEADER: "not-a-uuid"}
    assert client.post("/webhook", content=body, headers=bad_id).status_code == 400
    mismatched = _body(str(uuid4()))
    answer = client.post(
        "/webhook", content=mismatched, headers=_headers(event_id, mismatched)
    )
    assert (answer.status_code, answer.json()["code"]) == (400, "invalid_body")
    huge = b"x" * 70_000
    answer = client.post("/webhook", content=huge, headers=_headers(event_id, huge))
    assert (answer.status_code, answer.json()["code"]) == (413, "too_large")
    assert store.actions() == ()


def test_failure_modes(setup: tuple[TestClient, ReceiverStore, Behaviour]) -> None:
    """fail acts on nothing; lose_ack acts and still answers 503."""
    client, store, behaviour = setup
    event_id = str(uuid4())
    body = _body(event_id)
    behaviour.set(Mode.FAIL, remaining=1)
    down = client.post("/webhook", content=body, headers=_headers(event_id, body))
    assert down.status_code == 503 and store.actions() == ()
    behaviour.set(Mode.LOSE_ACK, remaining=1)
    lost = client.post("/webhook", content=body, headers=_headers(event_id, body))
    assert lost.status_code == 503 and len(store.actions()) == 1
    retry = client.post("/webhook", content=body, headers=_headers(event_id, body))
    assert retry.json()["status"] == "duplicate"


def test_clock_is_injectable(tmp_path: Path) -> None:
    """Demos shift time; the freshness window follows the injected clock."""
    future = datetime.now(UTC) + timedelta(hours=1)
    client = TestClient(
        create_receiver(
            ReceiverStore(tmp_path / "r.sqlite3"), SECRET, clock=lambda: future
        )
    )
    event_id = str(uuid4())
    body = _body(event_id)
    answer = client.post("/webhook", content=body, headers=_headers(event_id, body))
    assert answer.json()["code"] == "stale_timestamp"


def test_short_secret_and_missing_environment_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The receiver will not start with a guessable or missing secret."""
    with pytest.raises(ValueError):
        create_receiver(ReceiverStore(tmp_path / "r.sqlite3"), "short")
    monkeypatch.delenv("SANDBOX_WEBHOOK_SECRET", raising=False)
    assert main(["--database", str(tmp_path / "r.sqlite3")]) == 2
    assert "SANDBOX_WEBHOOK_SECRET" in capsys.readouterr().err


def test_behaviour_counts_down_then_accepts() -> None:
    """A limited failure mode returns to accept by itself."""
    behaviour = Behaviour(Mode.FAIL, remaining=2)
    assert [behaviour.next_mode() for _ in range(3)] == [
        Mode.FAIL,
        Mode.FAIL,
        Mode.ACCEPT,
    ]
    assert Behaviour(Mode.FAIL).next_mode() is Mode.FAIL  # None means forever
