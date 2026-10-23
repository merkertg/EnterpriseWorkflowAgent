"""The PostgreSQL demo CLI fails clearly before connecting when unconfigured."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_explains_missing_configuration(tmp_path: Path) -> None:
    """Without DATABASE_URL the demo exits with code 2 and no traceback."""
    environment = {
        key: value for key, value in os.environ.items() if key != "DATABASE_URL"
    }
    completed = subprocess.run(
        [sys.executable, "-m", "app.persistence_demo"],
        cwd=tmp_path,
        env={**environment, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 2
    assert "DATABASE_URL" in completed.stderr
    assert "Traceback" not in completed.stderr
