"""The interface setup command runs in a separate process and can repeat."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.application.workflows import WorkflowService
from app.ui_demo_setup import ORDER_ID

pytestmark = pytest.mark.postgres


def test_setup_command_runs_twice(
    service: WorkflowService, test_database_url: str, tmp_path: Path
) -> None:
    """A second run reuses the employees and issues new tokens."""
    root = Path(__file__).resolve().parents[2]

    def run() -> dict[str, Any]:
        """Run the setup in a fresh interpreter."""
        completed = subprocess.run(
            [sys.executable, "-m", "app.ui_demo_setup", "--samples", str(tmp_path)],
            cwd=root,
            env={**os.environ, "DATABASE_URL": test_database_url},
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        parsed: dict[str, Any] = json.loads(completed.stdout)
        return parsed

    first, second = run(), run()
    assert sorted(first["tokens"]) == [
        "ui-approver",
        "ui-clerk",
        "ui-outsider",
        "ui-viewer",
    ]
    assert first["tokens"]["ui-clerk"] != second["tokens"]["ui-clerk"]
    assert second["reference"]["purchase_order"] == f"{ORDER_ID} v1"
    assert sorted(Path(p).name for p in second["samples"]) == [
        "clean.txt",
        "instruction_in_text.txt",
        "price_difference.txt",
    ]
