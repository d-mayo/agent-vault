"""Install agent-vault on this machine. Safe to rerun.

    python install.py --vault "C:/Users/<you>/iCloudDrive/iCloud~md~obsidian/Notes"

What it does:
  1. Copies agent_vault/ to ~/.claude/agent-vault/ and skills/*/ to ~/.claude/skills/.
  2. Writes ~/.claude/agent-vault.json (vault path and what the installer manages).
  3. Merges its hook entries and the CLI permission into ~/.claude/settings.json, and removes
     the ones older versions put in <vault>/.claude/settings.json. Only entries it created
     are replaced or removed; a .bak copy is written before any change.
  4. Writes <vault>/CLAUDE.md.
  5. Deletes v1 code from <vault>/Agent/_system/ (the handoff log is kept).
  6. Points this repo's git hooks at agent_vault/githooks/ (main-branch protection); other repos
     get the installed copy in ~/.claude/agent-vault/githooks/ through `repo-init`.
Hooks run with the Python that runs this installer. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
MARK = "/.claude/agent-vault/"                  # identifies our hook commands
LEGACY_HOOK_MARK = "Agent/_system/hooks/"       # v1 hook commands
LEGACY_ALLOW = ["Bash(python Agent/_system/vault.py:*)"]
HOOKS_DIR = "agent_vault/githooks"           # this repo's hooks: the same file that gets installed
SKILL_MARKER = ".installed-by-agent-vault"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".gitkeep")


def python_cmd() -> str:
    return f'"{Path(sys.executable).as_posix()}"'


def hook_cmd(install_dir: Path, script: str) -> str:
    return f'{python_cmd()} "{(install_dir / "hooks" / script).as_posix()}"'


def user_hooks(install_dir: Path) -> dict:
    """Every hook is user-level (design §9): they decide by session and target path, and
    vault-level copies would run twice in vault sessions."""
    h = lambda s: [{"type": "command", "command": hook_cmd(install_dir, s)}]  # noqa: E731
    return {
        "SessionStart": [{"hooks": h("session_start.py")}],
        "PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit", "hooks": h("guard.py")},
                       {"matcher": "Bash", "hooks": h("push_guard.py")}],
        "PostToolUse": [{"matcher": "Write|Edit|MultiEdit", "hooks": h("post_edit.py")}],
        "Stop": [{"hooks": h("stop_check.py")}],
    }


def is_our_hook(command: str) -> bool:
    return MARK in command or LEGACY_HOOK_MARK in command


def merge_settings(settings: dict, hooks: dict, allow: list[str],
                   previously_managed: list[str]) -> tuple[dict, list[str]]:
    """Replace our hook entries and the exact permission strings we wrote before;
    keep everything else. Returns the merged settings and notes."""
    notes = []
    existing = settings.setdefault("hooks", {})
    for event in list(existing):
        groups = []
        for g in existing[event]:
            kept = [x for x in g.get("hooks", []) if not is_our_hook(x.get("command", ""))]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            existing[event] = groups
        else:
            del existing[event]
    for event, groups in hooks.items():
        for g in groups:
            if any(o.get("matcher", "") == g.get("matcher", "") for o in existing.get(event, [])):
                notes.append(f"{event} [{g.get('matcher') or '*'}] also has hooks not managed "
                             "by agent-vault; both will run.")
            existing.setdefault(event, []).append(g)
    if not existing:
        del settings["hooks"]
    remove = set(previously_managed) | set(LEGACY_ALLOW)
    perms = settings.get("permissions")
    if perms is not None or allow:
        perms = settings.setdefault("permissions", {})
        cur = [a for a in perms.get("allow", []) if a not in remove]
        cur += [a for a in allow if a not in cur]
        if cur or "allow" in perms:
            perms["allow"] = cur
    return settings, notes


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"error: {path} is not valid JSON ({e}); fix it and rerun")


def write_if_changed(path: Path, text: str, backup: bool = False) -> bool:
    old = path.read_text(encoding="utf-8-sig") if path.exists() else None
    if old == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and old is not None:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def _clear_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly)
    else:
        shutil.rmtree(path, onerror=_clear_readonly)


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        rmtree(dest)
    shutil.copytree(src, dest, ignore=IGNORE)


def install_skills(claude: Path, report: list[str]) -> None:
    for skill in sorted(p for p in (REPO / "skills").iterdir() if p.is_dir()):
        dest = claude / "skills" / skill.name
        if dest.exists() and not (dest / SKILL_MARKER).exists():
            report.append(f"warning: skipped skill {skill.name}: {dest} exists and wasn't "
                          "installed by agent-vault")
            continue
        copy_tree(skill, dest)
        (dest / SKILL_MARKER).write_text("", encoding="utf-8")
        report.append(f"installed skill {skill.name}")


def migrate_v1(vault: Path, report: list[str]) -> None:
    old = vault / "Agent" / "_system"
    if not old.exists():
        return
    log, new_log = old / "handoff-log.json", vault / "Agent" / ".handoff-log.json"
    if log.exists():
        try:
            old_entries = json.loads(log.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            report.append(f"warning: {log} is unreadable ({e}); left v1 folder in place")
            return
        merged = {**old_entries, **read_json(new_log)}
        write_if_changed(new_log, json.dumps(merged, indent=2, sort_keys=True) + "\n")
        report.append(f"handoff log kept at {new_log}")
    try:
        rmtree(old)
        report.append(f"deleted v1 code from {old}")
    except OSError as e:
        report.append(f"warning: could not delete {old} ({e}); delete it by hand")


def set_git_hooks(repo: Path, report: list[str]) -> None:
    if not (repo / ".git").exists():
        return
    try:
        subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", HOOKS_DIR],
                       check=True, capture_output=True)
        report.append(f"git hooks: core.hooksPath = {HOOKS_DIR}")
    except Exception as e:
        report.append(f"warning: could not set core.hooksPath ({e})")


def install(vault: Path, home: Path, repo: Path = REPO) -> list[str]:
    vault = Path(os.path.abspath(vault))
    if not (vault / ".obsidian").is_dir():
        sys.exit(f"error: {vault} is not an Obsidian vault (no .obsidian/ folder)")
    home = home.resolve()
    report: list[str] = []
    if sys.prefix != sys.base_prefix:
        report.append("warning: running inside a virtualenv; hooks will use its Python and stop "
                      "working if it's deleted. Prefer rerunning with your system Python.")
    claude = home / ".claude"
    install_dir = claude / "agent-vault"
    cli = f'{python_cmd()} "{(install_dir / "vault.py").as_posix()}"'
    config_path = claude / "agent-vault.json"
    managed_before = read_json(config_path).get("managed_allow", [])
    allow = [f"Bash({cli}:*)"]

    copy_tree(REPO / "agent_vault", install_dir)
    report.append(f"installed code to {install_dir}")
    install_skills(claude, report)

    vault_settings = vault / ".claude" / "settings.json"
    for path, hooks, perms in [
        (claude / "settings.json", user_hooks(install_dir), allow),
        (vault_settings, {}, []),        # older installs put our hooks and permission here
    ]:
        if path == vault_settings and not path.exists():
            continue
        merged, notes = merge_settings(read_json(path), hooks, perms, managed_before)
        report += [f"note: {path}: {n}" for n in notes]
        if write_if_changed(path, json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
                            backup=True):
            report.append(f"updated {path}")
    write_if_changed(config_path, json.dumps(
        {"vault": vault.as_posix(), "managed_allow": allow}, indent=2, ensure_ascii=False) + "\n")

    template = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
    if write_if_changed(vault / "CLAUDE.md", template.replace("{{CLI}}", cli)):
        report.append(f"updated {vault / 'CLAUDE.md'}")

    migrate_v1(vault, report)
    set_git_hooks(repo, report)
    report.append(f"check: {cli} status")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Install agent-vault. Safe to rerun.")
    ap.add_argument("--vault", required=True, help="path to the Obsidian vault (the Notes folder)")
    ap.add_argument("--home", default=str(Path.home()), help=argparse.SUPPRESS)  # tests
    args = ap.parse_args()
    for line in install(Path(args.vault), Path(args.home)):
        print(line)


if __name__ == "__main__":
    main()
