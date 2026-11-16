"""Install agent-vault on this machine. Safe to rerun.

    python install.py --vault "C:/Users/<you>/iCloudDrive/iCloud~md~obsidian/Notes"

What it does:
  1. Copies agent_vault/ to ~/.claude/agent-vault/ and skills/*/ to ~/.claude/skills/, replacing
     the installed files file by file from a staged copy (so a hook that starts during a
     reinstall never finds its script missing) and deleting files the new version dropped.
  2. Writes ~/.claude/agent-vault.json (vault path, what the installer manages, and `source`, `commit`
     and `commit_time`: the clone installed from and its installed commit).
  3. Merges its hook entries and the CLI permission into ~/.claude/settings.json, and removes
     the ones older versions put in <vault>/.claude/settings.json. Only entries it created
     are replaced or removed; a .bak copy is written before any change.
  4. Writes <vault>/CLAUDE.md.
  5. Deletes v1 code from <vault>/Agent/_system/ (the phone log is kept).
  6. Points this repo's git hooks at agent_vault/githooks/ (main-branch protection); other repos
     get the installed copy in ~/.claude/agent-vault/githooks/ through `repo-init`.
Hooks run with the Python that runs this installer. Standard library only.

After this one manual run, the install keeps itself current: `vault.py merge` (after a merge) and every
session start run agent_vault/selfupdate.py, which exports `main` of `source` with `git archive` and
runs that copy's install.py with --commit when `main` is newer than the installed commit. An install
made before `source` was recorded needs this one manual run first.

    python install.py --uninstall

removes everything above that the installer manages (the code, the skills it installed, its
settings entries and permission, the config, this repo's core.hooksPath) and leaves the vault's
notes and <vault>/CLAUDE.md alone.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
MARK = "/.claude/agent-vault/"                  # identifies our hook commands
LEGACY_HOOK_MARK = "Agent/_system/hooks/"       # v1 hook commands
LEGACY_ALLOW = ["Bash(python Agent/_system/vault.py:*)"]
HOOKS_DIR = "agent_vault/githooks"           # this repo's hooks: the same file that gets installed
SKILL_MARKER = ".installed-by-agent-vault"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".gitkeep")
STAGING = "agent-vault.staging"                 # under ~/.claude/, never under skills/ (D6)
REPLACE_TRIES, REPLACE_PAUSE = 6, 0.25          # about 1.5 seconds in all


CLI = "~/.claude/agent-vault/bin/vault"      # the same text on every machine (D1 of #44); the shell expands ~


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
                       {"matcher": "Bash|PowerShell", "hooks": h("push_guard.py")}],
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
            tools = set(g.get("matcher", "").split("|"))
            if any(tools & set(o.get("matcher", "").split("|")) for o in existing.get(event, [])):
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


def replace_file(src: Path, dest: Path) -> None:
    """Move src over dest in one step, retrying briefly while the OS says it is in use."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dest)
            return
        except PermissionError as e:
            if attempt == REPLACE_TRIES - 1:
                sys.exit(f"error: could not replace {dest} ({e}): it is in use. Close what is "
                         "using it and rerun install.py; files not yet replaced are untouched.")
            time.sleep(REPLACE_PAUSE)


def clear_staging(root: Path) -> None:
    if root.exists():
        rmtree(root)


def copy_tree(src: Path, dest: Path, stage: Path, keep: tuple = ()) -> None:
    """Make dest match src file by file: stage a copy, move each staged file over its target,
    and only then delete what src doesn't have, so a file both versions have is never missing."""
    if stage.exists():
        rmtree(stage)
    shutil.copytree(src, stage, ignore=IGNORE)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for staged in sorted(stage.rglob("*")):
        rel = staged.relative_to(stage)
        target = dest / rel
        wanted.add(rel)
        if staged.is_dir():
            if target.exists() and not target.is_dir():
                os.remove(target)
            target.mkdir(exist_ok=True)
        else:
            if target.is_dir():
                rmtree(target)
            replace_file(staged, target)
    for old in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = old.relative_to(dest)
        if rel in wanted or rel.parts[0] in keep:
            continue
        if old.is_dir():
            if not any(old.iterdir()):
                os.rmdir(old)
        else:
            os.remove(old)
    rmtree(stage)


def install_skills(claude: Path, report: list[str]) -> None:
    for skill in sorted(p for p in (REPO / "skills").iterdir() if p.is_dir()):
        dest = claude / "skills" / skill.name
        if dest.exists() and not (dest / SKILL_MARKER).exists():
            report.append(f"warning: skipped skill {skill.name}: {dest} exists and wasn't "
                          "installed by agent-vault")
            continue
        copy_tree(skill, dest, claude / STAGING / skill.name, keep=(SKILL_MARKER,))
        (dest / SKILL_MARKER).write_text("", encoding="utf-8")
        report.append(f"installed skill {skill.name}")


def load_log(path: Path):
    """The entries of a pulled-notes log, {} if the file is missing; None if it is unreadable."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def migrate_phone_log(vault: Path, report: list[str]) -> None:
    """Carry Agent/.handoff-log.json over to Agent/.phone-log.json (the new log wins a conflict)."""
    old, new = vault / "Agent" / ".handoff-log.json", vault / "Agent" / ".phone-log.json"
    if not old.exists():
        return
    old_entries, new_entries = load_log(old), load_log(new)
    if old_entries is None or new_entries is None:
        bad = old if old_entries is None else new
        report.append(f"warning: {bad} is unreadable; left {old} and {new} as they were")
        return
    write_if_changed(new, json.dumps({**old_entries, **new_entries}, indent=2, sort_keys=True) + "\n")
    old.unlink()
    report.append(f"moved the pulled-notes log {old} into {new}")


def migrate_v1(vault: Path, report: list[str]) -> None:
    old = vault / "Agent" / "_system"
    if not old.exists():
        return
    log, new_log = old / "handoff-log.json", vault / "Agent" / ".phone-log.json"
    if log.exists():
        try:
            old_entries = json.loads(log.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            report.append(f"warning: {log} is unreadable ({e}); left v1 folder in place")
            return
        new_entries = load_log(new_log)
        if new_entries is None:
            report.append(f"warning: {new_log} is unreadable; left the v1 folder in place")
            return
        merged = {**old_entries, **new_entries}
        write_if_changed(new_log, json.dumps(merged, indent=2, sort_keys=True) + "\n")
        report.append(f"phone log kept at {new_log}")
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


def commit_info(repo: Path, commit: str | None) -> dict:
    """{"commit": full sha, "commit_time": committer time in Unix seconds} for `commit` (else HEAD)
    in the clone `repo`; {} when git can't say (not a work tree, unknown sha, git missing)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%H %ct",
                              (commit or "HEAD") + "^{commit}", "--"],
                             capture_output=True, text=True, check=True).stdout.split()
        return {"commit": out[0], "commit_time": int(out[1])}
    except Exception:
        return {}


def install(vault: Path, home: Path, repo: Path = REPO, commit: str | None = None) -> list[str]:
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
    cli = CLI
    direct = f'{python_cmd()} "{(install_dir / "vault.py").as_posix()}"'
    config_path = claude / "agent-vault.json"
    managed_before = read_json(config_path).get("managed_allow", [])
    allow = [f"Bash({cli}:*)"]

    migrate_phone_log(vault, report)
    clear_staging(claude / STAGING)
    copy_tree(REPO / "agent_vault", install_dir, claude / STAGING / "_code")
    for launcher in sorted((install_dir / "bin").glob("*")):
        if launcher.is_file():
            launcher.chmod(launcher.stat().st_mode | 0o111)
    report.append(f"installed code to {install_dir}")
    install_skills(claude, report)
    clear_staging(claude / STAGING)

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
    config = {"vault": vault.as_posix(), "python": Path(sys.executable).as_posix(),
              "managed_allow": allow, "source": Path(os.path.abspath(repo)).as_posix(),
              **commit_info(repo, commit)}
    write_if_changed(config_path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")

    template = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
    if write_if_changed(vault / "CLAUDE.md", template.replace("{{CLI}}", cli)):
        report.append(f"updated {vault / 'CLAUDE.md'}")

    migrate_v1(vault, report)
    set_git_hooks(repo, report)
    report.append(f"check: {cli} status")
    if sys.platform == "win32":      # the launcher runs only in Bash (D5 of #44); PowerShell and cmd use Python directly
        report.append(f"check (PowerShell or cmd): {direct} status")
    return report


def unset_git_hooks(repo: Path, report: list[str]) -> bool:
    """Unset core.hooksPath in this clone if it still points at our hooks (git would otherwise
    run a pre-push hook that refuses every push once the config is gone)."""
    if not (repo / ".git").exists():
        return False
    try:
        cur = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
                             capture_output=True, text=True).stdout.strip()
        if cur != HOOKS_DIR:
            return False
        subprocess.run(["git", "-C", str(repo), "config", "--unset", "core.hooksPath"],
                       check=True, capture_output=True)
    except Exception as e:
        report.append(f"warning: could not unset core.hooksPath in {repo} ({e})")
        return False
    report.append(f"unset core.hooksPath in {repo}")
    return True


def uninstall(home: Path, repo: Path = REPO) -> list[str]:
    """Remove what install() manages; never touches the vault's notes or its CLAUDE.md."""
    home = home.resolve()
    claude = home / ".claude"
    config_path = claude / "agent-vault.json"
    config = read_json(config_path)
    managed = config.get("managed_allow", [])
    report: list[str] = []
    settings_paths = [claude / "settings.json"]
    if config.get("vault"):
        settings_paths.append(Path(config["vault"]) / ".claude" / "settings.json")
    for path in settings_paths:
        if not path.exists():
            continue
        data = read_json(path)
        merged, _ = merge_settings(copy.deepcopy(data), {}, [], managed)
        if merged != data:
            write_if_changed(path, json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
                             backup=True)
            report.append(f"removed agent-vault entries from {path} (backup: {path.name}.bak)")
    install_dir = claude / "agent-vault"
    if install_dir.exists():
        rmtree(install_dir)
        report.append(f"removed {install_dir}")
    staging = claude / STAGING
    if staging.exists():
        rmtree(staging)
        report.append(f"removed {staging}")
    skills = claude / "skills"
    if skills.is_dir():
        for skill in sorted(p for p in skills.iterdir() if (p / SKILL_MARKER).exists()):
            rmtree(skill)
            report.append(f"removed skill {skill.name}")
    if config_path.exists():
        config_path.unlink()
        report.append(f"removed {config_path}")
    unset_git_hooks(repo, report)
    if report:
        report.append("restart open Claude sessions so they stop running the removed hooks")
    else:
        report.append("nothing was installed")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Install agent-vault. Safe to rerun.")
    ap.add_argument("--vault", help="path to the Obsidian vault (the Notes folder)")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove what the installer manages (the vault's notes are kept)")
    ap.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)  # tests
    ap.add_argument("--home", default=str(Path.home()), help=argparse.SUPPRESS)  # tests
    ap.add_argument("--commit", help=argparse.SUPPRESS)  # the commit this copy was exported from
    args = ap.parse_args()
    if args.uninstall:
        for line in uninstall(Path(args.home), Path(args.repo)):
            print(line)
        return
    if not args.vault:
        ap.error("--vault is required (or use --uninstall)")
    for line in install(Path(args.vault), Path(args.home), Path(args.repo), args.commit):
        print(line)


if __name__ == "__main__":
    main()
