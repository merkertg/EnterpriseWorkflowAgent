"""Check a running deployment end to end over HTTP, with fictional data.

``python -m app.smoke --api URL`` prepares fictional employees, tokens and
reference data through ``DATABASE_URL`` (as ``app.ui_demo_setup`` does), then
drives the API exactly like a client: health, sign-in, upload, start, check,
reconcile, approve, and finally waits until the outbox worker has delivered
the decision to the sandbox receiver. It prints one JSON report and exits 0
only if every step gave the expected answer.

It is meant for the compose stack and for a native setup alike; it uses the
interface's standard-library HTTP client, so it needs no extra dependency.
"""

import argparse
import json
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import Settings
from app.infrastructure.db.session import create_database_engine
from app.ui_demo_setup import ORDER_ID, POLICY_ID, RULES_ID, setup_ui_demo
from ui.client import DEFAULT_API_URL, ApiClient, ApiError
from ui.keys import command_key

_UNIT = "DEMO-UNIT-A"


def _health(api_url: str) -> dict[str, Any]:
    """GET /health without a token."""
    with urllib.request.urlopen(f"{api_url}/health", timeout=10) as response:
        answer: dict[str, Any] = json.loads(response.read())
    return answer


def run_smoke(
    engine: Engine,
    api_url: str,
    wait_seconds: float,
    client: Callable[[str, str], ApiClient] | None = None,
) -> dict[str, Any]:
    """Run the scenario; every step records what the server answered."""
    make = client or (lambda url, token: ApiClient(url, token))
    steps: list[dict[str, Any]] = []

    def step(name: str, expected: object, observed: object) -> None:
        """Record one step and whether it matched."""
        steps.append(
            {
                "step": name,
                "expected": expected,
                "observed": observed,
                "ok": expected == observed,
            }
        )

    step("health", "ok", _health(api_url).get("status"))
    with tempfile.TemporaryDirectory(prefix="ewa-smoke-") as folder:
        setup = setup_ui_demo(engine, Path(folder))
        tokens: dict[str, str] = setup["tokens"]  # type: ignore[assignment]
        clean = Path(folder, "clean.txt").read_bytes()
    clerk = make(api_url, tokens["ui-clerk"])
    approver = make(api_url, tokens["ui-approver"])
    step("sign-in", "ui-clerk", clerk.me()["user_id"])

    upload = clerk.upload(clean, "clean.txt")
    step("upload", "complete", upload["extraction_status"])
    invoice_version = upload["invoice_version"]["version_id"]
    workflow_id = f"SMOKE-{uuid4().hex[:8].upper()}"
    order = clerk.reference("purchase-orders", ORDER_ID)
    policy = clerk.reference("policies", POLICY_ID)
    started = clerk.start(
        workflow_id,
        business_unit=_UNIT,
        invoice_version_id=invoice_version,
        purchase_order_version_id=order["version_id"],
        policy_version_id=policy["version_id"],
        key=command_key("start", workflow_id, invoice_version),
    )
    step("start", "received", started["state"])
    checked = clerk.check(
        workflow_id, version=1, key=command_key("check", workflow_id, 1)
    )
    step("check", "awaiting_approval", checked["state"])
    rules = clerk.reference("reconciliation-rules", RULES_ID)
    report = clerk.reconcile(workflow_id, rules["version_id"])
    step("reconcile", "matched", report["status"])
    snapshot_id = str(report["snapshot_id"])
    approved = approver.approve(
        workflow_id,
        version=2,
        reconciliation_snapshot_id=snapshot_id,
        reason="Fictional invoice matches the order",
        key=command_key("approve", workflow_id, 2, snapshot_id),
    )
    step("approve", "approved", approved["state"])

    status = None
    if wait_seconds > 0:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            [event] = approver.outbox(workflow_id)
            status = event["delivery"]["status"]
            if status in ("delivered", "exhausted"):
                break
            time.sleep(1)
        step("delivery", "delivered", status)
    return {
        "api": api_url,
        "workflow_id": workflow_id,
        "steps": steps,
        "ok": all(item["ok"] for item in steps),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run against the given API; print the report; exit 0 only on success."""
    parser = argparse.ArgumentParser(
        prog="python -m app.smoke",
        description="End-to-end HTTP check of a running deployment (fictional data).",
    )
    parser.add_argument("--api", default=DEFAULT_API_URL, help="base URL of the API")
    parser.add_argument(
        "--wait",
        type=float,
        default=60.0,
        help="seconds to wait for outbox delivery; 0 skips the delivery check",
    )
    arguments = parser.parse_args(argv)
    settings = Settings()
    if settings.database_url is None:
        print(
            "Set DATABASE_URL: the smoke check prepares its own users.", file=sys.stderr
        )
        return 2
    try:
        engine = create_database_engine(settings.database_url.get_secret_value())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        report = run_smoke(engine, arguments.api.rstrip("/"), arguments.wait)
    except ApiError as error:
        print(
            f"api_error: {error.status} {error.code}: {error.message}", file=sys.stderr
        )
        return 1
    except OSError as error:
        print(f"Cannot reach the API at {arguments.api}: {error}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Cannot connect to the database in DATABASE_URL.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Schema is missing: run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
