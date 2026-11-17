"""Shared test helpers. Tests use unittest (stdlib) and pytest runs them too; only `ruff` has to be
on PATH, because a test runs `ruff check .` over the repo."""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODE = REPO / "agent_vault"


def skip_install_prereqs(case) -> None:
    """The explicit opt-out (D3 of #47) for a test that runs the installer without testing its
    prerequisite check: CI has a `gh` that is not logged in, and no test may depend on this machine's tools."""
    patch = mock.patch.dict(os.environ, {"AGENT_VAULT_TEST_PREREQS": "1"})
    patch.start()
    case.addCleanup(patch.stop)


def find_bash():
    """A bash that runs scripts: Git's on Windows (the first `bash` on PATH there can be System32's
    WSL launcher, which has no distro), else the one on PATH; None if there is none."""
    if sys.platform == "win32":
        try:
            out = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, check=True).stdout.strip()
            for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
                found = Path(out).parents[2] / rel
                if found.is_file():
                    return str(found)
        except (OSError, subprocess.CalledProcessError, IndexError):
            pass
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d and "system32" not in d.lower() and (Path(d) / "bash.exe").is_file():
                return str(Path(d) / "bash.exe")
        return None
    return shutil.which("bash")


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


_SNAPSHOTS: dict = {}


def restore_or_build(key, root: Path, build) -> None:
    """Build a test's scenario in `root` with `build()` the first time `key` is seen in this
    process and keep a copy; later calls copy that scenario over `root` instead, with every
    absolute path rewritten. `root` must hold only what `build()` starts from (same each time)."""
    saved = _SNAPSHOTS.get(key)
    if saved is None:
        build()
        keep = tempfile.TemporaryDirectory()
        atexit.register(keep.cleanup)
        copy_scenario(root, Path(keep.name))
        _SNAPSHOTS[key] = keep
    else:
        copy_scenario(Path(saved.name), root)


FAKE_INSTALLER = '''\
import json, os, sys, time
from pathlib import Path
home = Path(sys.argv[sys.argv.index("--home") + 1])
here = Path(__file__).resolve().parent
rec = {"argv": sys.argv[1:], "folder": str(here), "marker": (here / "marker.txt").read_text() if (here / "marker.txt").exists() else "",
       "has_feature_file": (here / "feature.txt").exists()}
with open(home / "record.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(rec) + "\\n")
'''


def make_source_clone(root: Path, installer: str = FAKE_INSTALLER) -> tuple[Path, str, int]:
    """A temp clone whose `main` holds `installer` as install.py and marker.txt ("main"); it is left
    on a feature branch with feature.txt committed and marker.txt changed but uncommitted.
    Returns (clone, main's sha, main's committer time)."""
    clone = root / "source"
    clone.mkdir(parents=True)

    def git(*args):
        return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True,
                              text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    (clone / "install.py").write_text(installer, encoding="utf-8")
    (clone / "marker.txt").write_text("main", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "main commit")
    sha, when = git("log", "-1", "--format=%H %ct").split()
    git("switch", "-q", "-c", "feat/1-x")
    (clone / "feature.txt").write_text("feature", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "feature commit")
    (clone / "marker.txt").write_text("uncommitted", encoding="utf-8")
    return clone, sha, int(when)


def write_source_config(root: Path, vault: Path, clone: Path, commit_time: int | None,
                        **extra) -> Path:
    """A config at <root>/.claude/agent-vault.json (so an automatic install may run, with
    `root` as its home) naming `clone` as the source."""
    cfg = root / ".claude" / "agent-vault.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    data = {"vault": vault.as_posix(), "python": Path(sys.executable).as_posix(),
            "source": clone.as_posix(), **extra}
    if commit_time is not None:
        data["commit_time"] = commit_time
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg
