"""Stop hook: the bookkeeping that must not depend on Claude remembering. Runs in vault
and registered-repo sessions (as recorded by SessionStart) and blocks at most once.

1. Log rule: if files in Agent/ (vault session) or the repo's working tree or commits
   (repo session) changed this session and nothing was logged to today's daily note after
   the last change, Claude is sent back to log it. A pipeline event logged for the repo
   this session counts as logging.
2. Shell-write check: sealed notes and closed daily notes are fingerprinted at session
   start. The guard only sees the edit tools, so one that changed anyway was written
   through the shell; Claude must tell the user, not fix it.

Vault changes are found by file modification time; repo changes by git (HEAD moved, or a
path in `git status` was touched, or the status differs from the one at session start).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import github  # noqa: E402
import lib  # noqa: E402

IGNORE = {".handoff-log.json"}
MAX_LISTED = 10


def vault_changes(t0: float, daily: Path) -> tuple[list[str], float]:
    """(names, latest mtime) of Agent/ files changed since t0, not counting today's daily note."""
    names, latest = [], 0.0
    for p in lib.AGENT.rglob("*"):
        if not p.is_file() or lib.is_hidden(p) or p.name in IGNORE or "__pycache__" in p.parts or p == daily:
            continue
        m = p.stat().st_mtime
        if m > t0:
            names.append(lib.rel(p))
            latest = max(latest, m)
    return names, latest


def repo_changes(state: dict) -> tuple[list[str], float]:
    """(names, latest change time) of what this session changed in the repo."""
    root, t0, before = state["root"], state["started"], state.get("git") or {}
    names, latest = [], 0.0
    head = github.git("rev-parse", "HEAD", cwd=root, check=False) or None
    if head != before.get("head"):
        names.append("commits since session start")
        ct = github.git("log", "-1", "--format=%ct", cwd=root, check=False)
        latest = float(ct) if ct.isdigit() else 0.0
    status = github.status_lines(root)
    for line in status:
        path = line[3:].split(" -> ")[-1].strip('"')
        try:
            m = os.stat(os.path.join(root, path)).st_mtime
        except OSError:
            m = 0.0                                   # deleted: counts if the status is new
        if m > t0 or line not in before.get("status", []):
            names.append(path)
            latest = max(latest, m)
    return names, latest


def logged_since_start(state: dict, daily: Path, repo: str | None) -> bool:
    """A pipeline event line for this repo, appended to today's daily note during the session."""
    if not repo or not daily.exists():
        return False
    pat = re.compile(rf"^- \S+ {re.escape(repo)}#[1-9]\d* (?:{'|'.join(lib.EVENTS)})\b")
    new = lib.read_text(daily).splitlines()[state.get("daily_lines", 0):]
    return any(pat.match(ln) for ln in new)


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        return
    data = lib.read_hook_input()
    if data.get("stop_hook_active"):
        return  # we already sent Claude back once
    state = lib.load_state(data.get("session_id", ""))
    t0, kind = state.get("started"), state.get("kind")
    if t0 is None or kind not in ("vault", "repo"):
        return  # SessionStart didn't run here; nothing reliable to compare against

    problems = []
    tampered = lib.changed_fingerprints(state.get("fingerprints") or {})
    if tampered:
        problems.append(
            "These sealed or closed notes changed during this session, so something wrote them "
            "through the shell (the edit guard can't see that):\n"
            + "\n".join(f"- {n}" for n in tampered[:MAX_LISTED])
            + "\nDon't try to fix them. Tell the user which notes changed and how, so they can "
              "review or restore them.")

    daily = lib.daily_path()
    repo = state.get("repo")
    names, latest = vault_changes(t0, daily) if kind == "vault" else repo_changes(state)
    if names and not logged_since_start(state, daily, repo):
        logged_at = daily.stat().st_mtime if daily.exists() else 0
        if logged_at < latest or not latest:
            project = f" --project {state['project']}" if state.get("project") else " --project <id>"
            problems.append(
                "Before finishing, log this session's work in today's daily note. Run:\n"
                f'  {lib.CLI} log "<one line: what changed and why>"{project}\n'
                + ("(Add --project only if it clearly belongs to one; if unsure, ask the user.)\n"
                   "If a project's current state or next actions changed, update its project note too.\n"
                   if kind == "vault" else "")
                + "Changed this session:\n" + "\n".join(f"- {n}" for n in sorted(names)[:MAX_LISTED]))
    if problems:
        print("\n\n".join(problems), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    lib.run_hook(main, "Stop")
