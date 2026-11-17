"""Keep the installed copy in ~/.claude/ current with the `main` of the clone it was installed from.

`selfupdate()` is called at session start and after `vault.py merge` merges. It installs only what
`main` commits, never a branch or a working tree: `main` is exported with `git archive` into a
temp folder and that copy's own `install.py` is run. It compares local refs only (no network),
installs only when `main`'s tip has a newer committer time than the installed commit, and takes
a lock file so two sessions never install at once. It returns one line to show the user, or None
when there is nothing to say, and never raises. Standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import github  # noqa: E402
import lib  # noqa: E402

LOCK_NAME = "agent-vault.update.lock"
LOCK_STALE_SECONDS = 120
GUIDE_HELD = "warning: the vault guide is from"   # install.py prints it when it keeps a newer guide
TEST_PREREQS_ENV = "AGENT_VAULT_TEST_PREREQS"   # install.py's test-only opt-out; the child never gets it
INSTALL_LIMIT = 60                 # seconds the installer may run


def _by_hand(why: str, source: str, vault: str) -> str:
    return (f"agent-vault: couldn't update the install from main: {why}. "
            f"To install by hand, run `python install.py --vault \"{vault}\"` from {source}.")


def _take_lock(lock: Path) -> bool:
    """Create the lock exclusively; replace one older than LOCK_STALE_SECONDS. False if held."""
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue
            if age < LOCK_STALE_SECONDS:
                return False
            try:
                lock.unlink()
            except OSError:
                return False
            continue
        os.close(fd)
        return True
    return False


def _export(source: str, sha: str, dest: Path) -> None:
    archive = dest / "main.tar"
    github.archive(source, sha, archive)
    kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    with tarfile.open(archive) as tar:
        tar.extractall(dest / "tree", **kwargs)
    archive.unlink()


def selfupdate() -> str | None:
    try:
        return _selfupdate()
    except Exception as e:
        return f"agent-vault: couldn't update the install from main ({type(e).__name__}: {e})."


def _selfupdate() -> str | None:
    config_path = Path(lib.CONFIG)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        source = config.get("source")
    except Exception:
        return None
    if not isinstance(source, str) or not source:
        return None                     # installed by an older install.py (D8)
    vault = str(config.get("vault") or "<vault>")
    if not Path(source).is_dir():
        return _by_hand(f"the clone {source} is missing", source, vault)
    try:
        sha, _, ctime = github.git("log", "-1", "--format=%H %ct", "refs/heads/main", "--",
                                   cwd=source).partition(" ")
        main_time = int(ctime)
    except (github.CmdError, ValueError) as e:
        return _by_hand(f"can't read main in {source} ({e})", source, vault)
    installed = config.get("commit_time")
    if isinstance(installed, int) and not isinstance(installed, bool) and main_time <= installed:
        return None
    if config_path.parent.name != ".claude":
        return _by_hand(f"{config_path} isn't in a .claude folder, so there's no install to update",
                        source, vault)
    python = config.get("python")
    if not python:
        return _by_hand("the config has no python", source, vault)
    lock = config_path.with_name(LOCK_NAME)
    try:
        if not _take_lock(lock):
            return None
    except OSError as e:
        return _by_hand(f"can't take the install lock ({e})", source, vault)
    tmp = Path(tempfile.mkdtemp(prefix="agent-vault-main-"))
    try:
        try:
            _export(source, sha, tmp)
        except (github.CmdError, OSError, tarfile.TarError) as e:
            return _by_hand(f"can't export main ({e})", source, vault)
        argv = [python, str(tmp / "tree" / "install.py"), "--vault", vault, "--repo", source,
                "--commit", sha, "--home", str(config_path.parent.parent)]
        try:
            env = {k: v for k, v in os.environ.items() if k != TEST_PREREQS_ENV}   # never skip the check
            proc = subprocess.run(argv, cwd=tmp / "tree", capture_output=True, text=True, env=env,
                                  encoding="utf-8", errors="replace", timeout=INSTALL_LIMIT)
        except subprocess.TimeoutExpired:
            return _by_hand(f"install.py took longer than {INSTALL_LIMIT} seconds", source, vault)
        except OSError as e:
            return _by_hand(f"can't run {python} ({e})", source, vault)
        if proc.returncode != 0:
            last = next((ln for ln in reversed((proc.stderr or "").splitlines()) if ln.strip()), "")
            return _by_hand(f"install.py exited {proc.returncode}" + (f" ({last.strip()})" if last else ""),
                            source, vault)
        held = next((ln.strip() for ln in (proc.stdout or "").splitlines()
                     if ln.strip().startswith(GUIDE_HELD)), "")
        line = (f"agent-vault installed main ({sha[:7]}) from {source}; the hooks, skills and vault "
                "guide it changed take effect in the next session.")
        return f"{line} {held}" if held else line
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            lock.unlink()
        except OSError:
            pass
