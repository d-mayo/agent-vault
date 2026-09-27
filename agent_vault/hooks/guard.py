"""PreToolUse hook for Write/Edit/MultiEdit/NotebookEdit.

Exit code 2 blocks the tool call and shows stderr to Claude.
Rules:
  - The phone side (anything in the vault outside Agent/, CLAUDE.md and
    .claude/) is read-only. Phone notes enter via `vault.py handoff pull`.
  - .obsidian/ is Obsidian's config; change it in Obsidian.
  - New notes in Agent/ are created by vault.py, never written freehand.
  - Daily notes older than yesterday are closed.
  - No loose files at the top of Agent/.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lib  # noqa: E402

CLI = lib.CLI


def block(msg: str) -> None:
    print(f"Blocked by vault guard: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        return
    data = lib.read_hook_input()
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path")
    if not target:
        return
    parts = lib.vault_parts(target, data.get("cwd"))
    if parts is None or not parts:
        return  # outside the vault: not this hook's business

    agent = lib.AGENT.name.lower()
    if parts[0] == ".claude" or parts == ["claude.md"]:
        return
    if parts[0] == ".obsidian":
        block("that's Obsidian's own config. Ask the user to change it in Obsidian.")
    if parts[0] != agent:
        block("the phone side of the vault (everything outside Agent/) is read-only. "
              f"To act on a phone note, run: {CLI} handoff pull \"<path>\" [--project <id>]")

    if len(parts) == 1:
        return
    if len(parts) == 2 and parts[1] != "_system":
        block("no loose files at the top of Agent/. Use Daily/, Projects/, Notes/ or Intake/.")
    folder = parts[1]
    if folder == "_system":
        return

    path = Path(target)
    if not path.is_absolute():
        path = Path(data.get("cwd") or ".") / path
    if tool == "Write" and not path.exists() and path.suffix.lower() == ".md":
        block("new notes are created with the CLI so names and frontmatter are always right:\n"
              f"  {CLI} new project <id> --title \"...\" [--goal \"...\"] [--repo ...]\n"
              f"  {CLI} new note \"Title\" [--project <id>]\n"
              f"  {CLI} new daily\n"
              "Then edit the file it creates.")

    if folder == lib.FOLDERS["daily"].lower():
        try:
            day = dt.date.fromisoformat(Path(parts[-1]).stem)
        except ValueError:
            return
        if day < lib.today() - dt.timedelta(days=1):
            block(f"daily notes older than yesterday are closed. Log to today's note instead "
                  f"({CLI} log \"...\" --project <id>), or ask the user to edit the old one.")


if __name__ == "__main__":
    main()
