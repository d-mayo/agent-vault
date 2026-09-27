"""Stop hook: the bookkeeping that must not depend on Claude remembering.

1. Any project note changed this session gets `updated:` set to today.
2. If anything in Agent/ changed after the last write to today's daily note,
   Claude is sent back to add a log entry before it can finish.

Changes are found by file modification time, so it catches edits made
through any tool (Edit, Write, or the vault.py CLI via Bash).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lib  # noqa: E402
import vault  # noqa: E402

IGNORE = {".handoff-log.json"}


def changed_since(t0: float) -> list[Path]:
    out = []
    for p in lib.AGENT.rglob("*"):
        if not p.is_file() or lib.is_hidden(p) or p.name in IGNORE or "__pycache__" in p.parts:
            continue
        if p.stat().st_mtime > t0:
            out.append(p)
    return out


def bump_projects(paths: list[Path]) -> None:
    today = lib.today().isoformat()
    for p in paths:
        if p.parent.name != lib.FOLDERS["project"] or p.suffix != ".md":
            continue
        fm, _, _ = lib.split_frontmatter(lib.read_text(p))
        if fm and "updated" in fm and fm["updated"] != today:   # v2 projects have no `updated`
            st = p.stat()
            vault.set_fields(p, updated=today)
            os.utime(p, (st.st_atime, st.st_mtime))  # bookkeeping isn't "new work"


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        return
    data = lib.read_hook_input()
    state = lib.load_state(data.get("session_id", ""))
    t0 = state.get("started")
    if t0 is None:
        return  # SessionStart didn't run; nothing reliable to compare against

    daily = lib.daily_path()
    changed = changed_since(t0)
    bump_projects(changed)
    work = [p for p in changed if p != daily]
    if not work or data.get("stop_hook_active"):
        return  # nothing to log, or we already sent Claude back once

    latest_work = max(p.stat().st_mtime for p in work)
    daily_mtime = daily.stat().st_mtime if daily.exists() else 0
    if daily_mtime >= latest_work:
        return

    files = "\n".join(f"- {lib.rel(p)}" for p in sorted(work)[:10])
    print("Before finishing, log this session's work in today's daily note. Run:\n"
          f'  {lib.CLI} log "<one line: what changed and why>" --project <id>\n'
          "(Add --project only if it clearly belongs to one; if unsure, ask the user.)\n"
          "If a project's current state or next actions changed, update its project note too.\n"
          f"Changed this session:\n{files}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"(vault Stop hook failed: {e})", file=sys.stderr)
