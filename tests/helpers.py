"""Shared test helpers. Tests use unittest (stdlib) so no install is needed;
pytest runs them too."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODE = REPO / "agent_vault"


def make_vault(root: Path) -> Path:
    vault = root / "Notes"
    (vault / ".obsidian").mkdir(parents=True)
    return vault


def write_config(root: Path, vault: Path) -> Path:
    cfg = root / "agent-vault.json"
    cfg.write_text(json.dumps({"vault": vault.as_posix()}), encoding="utf-8")
    return cfg


def run_py(script: Path, args=(), stdin: dict | str | None = None, config: Path | None = None,
           cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if config is not None:
        env["AGENT_VAULT_CONFIG"] = str(config)
    data = json.dumps(stdin) if isinstance(stdin, dict) else (stdin or "")
    return subprocess.run([sys.executable, str(script), *args], input=data, text=True,
                          capture_output=True, env=env, cwd=cwd)


def tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory()
