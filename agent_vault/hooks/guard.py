"""PreToolUse hook for Write/Edit/MultiEdit/NotebookEdit. Runs in every session
and decides by the target path alone, because the vault can be edited from anywhere.

Exit code 2 blocks the tool call and shows stderr to Claude.
Blocked:
  - the phone side (anything in the vault outside Agent/, CLAUDE.md and .claude/):
    phone notes enter via `vault.py handoff pull`;
  - .obsidian/ (Obsidian's own config);
  - the installed code in ~/.claude/agent-vault/ and the vault's CLAUDE.md: both are
    written by install.py from the repo;
  - sealed plan, impl, retro and design notes (a later design extends or replaces a sealed design);
  - decision records, existing or new: `vault.py decision add` is the only way to write one;
  - anything in Daily/: daily notes are written only by the CLI;
  - new notes in Agent/ (they come from the CLI) and loose files at the top of Agent/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lib  # noqa: E402

CLI = lib.CLI
INSTALLED = Path.home() / ".claude" / "agent-vault"


def block(msg: str) -> None:
    print(f"Blocked by vault guard: {msg}", file=sys.stderr)
    sys.exit(2)


def inside(path: Path, root: Path) -> bool:
    a, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([a, r]) == r
    except ValueError:          # different drives on Windows
        return False


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        return
    data = lib.read_hook_input()
    ti = data.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path")
    if not target:
        return
    path = Path(target)
    if not path.is_absolute():
        path = Path(data.get("cwd") or ".") / path

    if inside(path, INSTALLED):
        block("the installed code in ~/.claude/agent-vault/ is overwritten by every install. "
              "Change it in the d-mayo/agent-vault repo and rerun `python install.py --vault <path>`.")
    parts = lib.vault_parts(path)
    if not parts:
        return  # outside the vault: not this hook's business

    agent = lib.AGENT.name.lower()
    if parts == ["claude.md"]:
        block("the vault's CLAUDE.md is generated. Change templates/vault-CLAUDE.md in the "
              "d-mayo/agent-vault repo and rerun `python install.py --vault <path>`.")
    if parts[0] == ".claude":
        return
    if parts[0] == ".obsidian":
        block("that's Obsidian's own config. Ask the user to change it in Obsidian.")
    if parts[0] != agent:
        block("the phone side of the vault (everything outside Agent/) is read-only. "
              f"To act on a phone note, run: {CLI} handoff pull \"<path>\" [--project <id>]")

    folder = parts[1] if len(parts) > 1 else ""
    if len(parts) == 1:
        return
    if len(parts) == 2:
        block("no loose files at the top of Agent/. Use Projects/, Work/, Ideas/ or Daily/.")
    if folder == lib.FOLDERS["daily"].lower():
        block("daily notes are written only by the CLI, never edited: log with "
              f"`{CLI} log \"<what changed and why>\" --project <id>`. "
              "If a daily line is wrong, tell the user; they fix it in Obsidian.")
    if folder == lib.FOLDERS["decision"].lower():
        block("a decision record is never edited, and a new one comes only from the CLI, which also keeps "
              "the project overview's Standing decisions in step:\n"
              f"  {CLI} decision add <project> \"<title>\" --decision \"...\" --why \"...\" "
              "--source \"plan <repo>#<n> D<k> | design <design> D<k> | issue <repo>#<n> | retro <repo>#<n> "
              "| session\" "
              "[--replaces <record>]\n"
              "To change a decision, add a record that replaces it; if a record is wrong, tell the user.")
    if lib.is_sealed_note(path) and folder == lib.FOLDERS["design"].lower():
        block("this design is sealed and can't be edited: later plans are checked against it as approved. "
              "To change it, start a design that extends or replaces it "
              f"(`{CLI} new design <project> \"<title>\"`, then set `extends` or `replaces` to this design).")
    if lib.is_sealed_note(path):
        block("this note is sealed and can't be edited. If something in it turned out wrong, note it "
              "in the issue's impl note (Deviations or Discoveries) while that is open, or tell the user.")
    if not path.exists() and path.suffix.lower() == ".md":
        block("a note in Agent/ must come from the CLI so names and frontmatter are always right:\n"
              f"  {CLI} new project <id> [--repo owner/name] [--purpose \"...\"]\n"
              f"  {CLI} new plan <repo> <issue>   (a draft plan and an open impl note)\n"
              f"  {CLI} new retro <repo> <issue>\n"
              f"  {CLI} new design <project> \"<title>\"   (a draft design)\n"
              f"  {CLI} idea add \"<title>\" [--project <id>]\n"
              f"  {CLI} decision add <project> \"<title>\" --decision \"...\" --why \"...\" --source \"...\"\n"
              "Then edit the file it creates.")


if __name__ == "__main__":
    lib.run_hook(main, "guard")
