"""Release files keep their promises: pinned images, no secrets, loopback ports."""

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

from app import __version__
from app.backup import late_columns
from app.backup import main as backup_main
from app.smoke import main as smoke_main

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def test_every_base_image_is_pinned_by_digest() -> None:
    """A rebuild cannot silently pick up a different base image."""
    images = re.findall(r"^(?:ARG PYTHON_IMAGE=|FROM )(\S+)", DOCKERFILE, re.M)
    external = [
        image for image in images if not image.startswith(("$", "build", "runtime"))
    ]
    assert external and all(DIGEST.search(image) for image in external)
    assert "uv sync --locked" in DOCKERFILE
    assert re.search(r"^USER app$", DOCKERFILE, re.M)


def test_compose_publishes_on_loopback_and_needs_secrets() -> None:
    """Ports bind 127.0.0.1; passwords and secrets must come from outside."""
    ports = re.findall(r'^\s+- "([^"]+:\d+:\d+)"$', COMPOSE, re.M)
    assert ports and all(port.startswith("127.0.0.1:") for port in ports)
    assert "${POSTGRES_PASSWORD:?" in COMPOSE
    assert "${SANDBOX_WEBHOOK_SECRET:?" in COMPOSE
    assert 'network_mode: "service:receiver"' in COMPOSE
    for line in COMPOSE.splitlines():
        if re.match(r"\s+(?:POSTGRES|DATABASE)_PASSWORD:", line):
            assert "${POSTGRES_PASSWORD:?" in line


def test_compose_wait_checks_the_streamlit_http_endpoint() -> None:
    """The UI must answer HTTP before Compose reports the whole stack ready."""
    match = re.search(r"^  ui:\n(.*?)(?=^volumes:|\Z)", COMPOSE, re.M | re.S)
    assert match is not None
    healthcheck = re.search(r"^      test: (\[.*\])$", match[1], re.M)
    assert healthcheck is not None
    command = json.loads(healthcheck[1])
    assert command[:3] == ["CMD", "python", "-c"]
    assert "http://127.0.0.1:8501/_stcore/health" in command[3]
    assert "timeout=" in command[3]


def _ci_backup_script() -> str:
    """Read the executable backup step without adding a YAML test dependency."""
    match = re.search(
        r"^      - name: Backup, wipe, restore and verify\n"
        r"        run: \|\n((?:          [^\n]*\n|\n)+)",
        CI,
        re.M,
    )
    assert match is not None
    return textwrap.dedent(match[1])


@pytest.mark.parametrize("volumes", [(), ("only-pgdata",), ("first", "second")])
def test_ci_restore_replaces_only_one_postgres_volume(
    volumes: tuple[str, ...], tmp_path: Path
) -> None:
    """A fake Docker proves the real shell step preserves the receiver ledger.

    Zero or multiple matching volumes must stop before any deletion. The stub
    records commands only; no Docker daemon, database or real volume is touched.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("The GitHub Actions backup step requires Bash.")
    executable = tmp_path / "docker"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$STUB_LOG"\n'
        'if [ "$1" = "volume" ] && [ "$2" = "ls" ]; then\n'
        '  printf "%s" "$STUB_VOLUMES"\nfi\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    log = tmp_path / "docker.log"
    project = "isolated-release-test"
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", _ci_backup_script()],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
            "PWD": str(tmp_path),
            "COMPOSE_PROJECT_NAME": project,
            "STUB_LOG": str(log),
            "STUB_VOLUMES": "".join(name + "\n" for name in volumes),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [shlex.split(line) for line in log.read_text().splitlines()]
    assert ["compose", "down"] in calls
    assert all(call[:3] != ["compose", "down", "-v"] for call in calls)
    listing = next(call for call in calls if call[:2] == ["volume", "ls"])
    assert f"label=com.docker.compose.project={project}" in listing
    assert "label=com.docker.compose.volume=pgdata" in listing
    removed = [call for call in calls if call[:2] == ["volume", "rm"]]
    if len(volumes) == 1:
        assert result.returncode == 0, result.stderr
        assert removed == [["volume", "rm", volumes[0]]]
        assert any("restore" in call for call in calls)
        assert any("verify" in call for call in calls)
    else:
        assert result.returncode != 0
        assert removed == []
        assert not any("restore" in call for call in calls)


def test_compose_and_ci_use_the_same_database_image() -> None:
    """One PostgreSQL digest for local stacks and CI."""
    [local] = re.findall(r"image: (postgres:\S+)", COMPOSE)
    assert DIGEST.search(local) and local in CI


def test_build_context_excludes_secrets_and_local_data() -> None:
    """.env, Git history, the virtualenv and var/ never reach an image."""
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").split())
    assert {".env", ".env.*", ".git", ".venv", "var"} <= ignored


def test_versions_agree() -> None:
    """pyproject, the package and the compose image tags name one version."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__ == "0.10.0"
    assert f"enterprise-workflow-agent:{__version__}" in COMPOSE
    assert f"enterprise-workflow-agent-ui:{__version__}" in COMPOSE


def test_only_workflows_needs_deferred_keys() -> None:
    """The schema's key cycles are restorable: nullable, on a mutable table."""
    assert {name: set(columns) for name, columns in late_columns().items()} == {
        "workflows": {"last_snapshot_id", "decision_reconciliation_id"}
    }


@pytest.mark.parametrize("command", [["check"], ["export"]])
def test_backup_needs_a_database(
    command: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Without DATABASE_URL the commands stop with code 2."""
    assert backup_main(command) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_smoke_needs_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    """The smoke check prepares users through the database."""
    assert smoke_main(["--wait", "0"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
