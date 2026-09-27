"""PostToolUse hook for Write/Edit/MultiEdit: validate the note in Agent/ that was just
changed, in every session. Exit code 2 shows its errors to Claude so it fixes them right
away; warnings never block."""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lib  # noqa: E402


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        return
    data = lib.read_hook_input()
    ti = data.get("tool_input") or {}
    target = ti.get("file_path")
    if not target:
        return
    parts = lib.vault_parts(target, data.get("cwd"))
    if not parts or parts[0] != lib.AGENT.name.lower() or len(parts) < 2 or parts[1] == "_system":
        return
    path = Path(target)
    if not path.is_absolute():
        path = Path(data.get("cwd") or ".") / path
    if path.suffix.lower() != ".md" or not path.exists():
        return
    problems = lib.validate_file(path.resolve()).errors   # warnings never block
    if problems:
        msg = "\n".join(f"- {p}" for p in problems)
        print(f"{lib.rel(path.resolve())} breaks the vault schema. Fix it now:\n{msg}\n"
              f"(Run `{lib.CLI} schema` for the rules.)", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    lib.run_hook(main, "PostToolUse")
