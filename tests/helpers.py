"""Shared test helpers. Tests use unittest (stdlib) and pytest runs them too; only `ruff` has to be
on PATH, because a test runs `ruff check .` over the repo."""
from __future__ import annotations

import json
import os
import shutil
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
           cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env or {})}
    if config is not None:
        env["AGENT_VAULT_CONFIG"] = str(config)
    data = json.dumps(stdin) if isinstance(stdin, dict) else (stdin or "")
    return subprocess.run([sys.executable, str(script), *args], input=data, text=True,
                          capture_output=True, env=env, cwd=cwd)


def tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory()


def copy_scenario(src: Path, dst: Path) -> None:
    """Copy a prepared scenario folder to `dst` and point every absolute path in it at `dst`.

    Text files outside git's object stores (the config, the fake-gh state file, a clone's
    `.git/config`, vault notes) have `src` replaced by `dst`, in native, posix and JSON-escaped
    spelling."""
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    pairs = []
    for fmt in (lambda p: str(p), lambda p: p.as_posix(), lambda p: json.dumps(str(p))[1:-1]):
        old, new = fmt(src).encode(), fmt(dst).encode()
        if old not in [o for o, _ in pairs]:
            pairs.append((old, new))
    for path in dst.rglob("*"):
        if not path.is_file() or "objects" in path.relative_to(dst).parts or path.name == "index":
            continue
        data = path.read_bytes()
        out = data
        for old, new in pairs:
            out = out.replace(old, new)
        if out != data:
            path.write_bytes(out)
