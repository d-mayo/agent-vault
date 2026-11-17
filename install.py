"""Install agent-vault on this machine. Safe to rerun.

    python install.py --vault "C:/Users/<you>/iCloudDrive/iCloud~md~obsidian/Notes"

What it does:
  1. Copies agent_vault/ to ~/.claude/agent-vault/ and skills/*/ to ~/.claude/skills/, replacing
     the installed files file by file from a staged copy (so a hook that starts during a
     reinstall never finds its script missing) and deleting files the new version dropped.
  2. Writes ~/.claude/agent-vault.json (vault path, what the installer manages, the absolute paths of
     the `gh` and `git` it checked, and `source`, `commit` and `commit_time`: the clone installed from
     and its installed commit). Before writing anything it checks Python 3.9+, git, and gh (installed,
     2.94.0 or newer, logged in), refuses naming every failure and its fix, and warns about a missing ruff.
  3. Merges its hook entries and the CLI permission into ~/.claude/settings.json, and removes
     the ones older versions put in <vault>/.claude/settings.json. Only entries it created
     are replaced or removed; a .bak copy is written before any change.
  4. Writes <vault>/CLAUDE.md, whose first line is `<!-- agent-vault <committer-time> <commit> -->`
     (the commit installed). An install from an older commit than the one in that marker leaves the
     guide alone and warns to `git pull` and rerun, so a stale clone never undoes a newer machine's
     guide; `--force-guide` writes it anyway. An install that can't tell its commit keeps a marked guide.
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
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True      # importing agent_vault.github for the check must not leave __pycache__ in the clone
REPO = Path(__file__).resolve().parent
MARK = "/.claude/agent-vault/"                  # identifies our hook commands
LEGACY_HOOK_MARK = "Agent/_system/hooks/"       # v1 hook commands
LEGACY_ALLOW = ["Bash(python Agent/_system/vault.py:*)"]
HOOKS_DIR = "agent_vault/githooks"           # this repo's hooks: the same file that gets installed
SKILL_MARKER = ".installed-by-agent-vault"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".gitkeep")
STAGING = "agent-vault.staging"                 # under ~/.claude/, never under skills/ (D6)
REPLACE_TRIES, REPLACE_PAUSE = 6, 0.25          # about 1.5 seconds in all
MIN_PYTHON = (3, 9)
GH_URL = "https://cli.github.com"
TEST_PREREQS_ENV = "AGENT_VAULT_TEST_PREREQS"   # set only by tests: skips the prerequisite check (never by selfupdate)
CHECK_TIMEOUT = 20                              # seconds for one `gh` call of the check


CLI = "~/.claude/agent-vault/bin/vault"      # the same text on every machine (D1 of #44); the shell expands ~


BLOCKING_HOOKS = ("guard.py", "push_guard.py")   # the PreToolUse guards: a missing Python blocks the tool call


def tool_argv(path: str) -> list[str]:
    """The argv prefix that runs a recorded tool: a `.py` stand-in (tests) runs through this Python."""
    return [sys.executable, path] if path.lower().endswith(".py") else [path]


def _parse_version(text):
    lines = (text or "").strip().splitlines()
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", lines[0]) if lines else None
    return tuple(int(x) for x in m.groups()) if m else None


def check_prerequisites(recorded=None, which=shutil.which, run=subprocess.run, version=None):
    """Check Python, git and gh before the installer writes anything.
    Returns (tools, problems, warnings): `tools` maps "gh" and "git" to the absolute forward-slash
    paths checked; each problem is a failure with its fix. A tool is found on PATH, and only when PATH
    lacks it in the `recorded` config's path (when that file still exists)."""
    sys.path.insert(0, str(REPO))
    from agent_vault import github
    recorded = recorded or {}
    problems: list[str] = []
    warnings: list[str] = []
    tools: dict[str, str] = {}
    have_py = tuple(version or sys.version_info)[:2]
    if have_py < MIN_PYTHON:
        problems.append(f"Python {'.'.join(map(str, MIN_PYTHON))} or newer is required (this is "
                        f"{have_py[0]}.{have_py[1]}): install a newer Python and rerun with it")

    def find(name):
        found = which(name)
        if found:
            return Path(os.path.abspath(found)).as_posix()
        old = recorded.get(name)
        if isinstance(old, str) and old and Path(old).is_file():
            return Path(old).as_posix()
        return None
    git, gh = find("git"), find("gh")
    if git:
        tools["git"] = git
    else:
        problems.append("git is missing: install git")
    if not gh:
        problems.append(f"gh is missing: install gh from {GH_URL} (version {github.MIN_GH} or newer)")
    else:
        tools["gh"] = gh
        try:
            out = run(tool_argv(gh) + ["--version"], capture_output=True, text=True,
                      timeout=CHECK_TIMEOUT).stdout
        except Exception:
            out = ""
        have = _parse_version(out)
        if have is None:
            problems.append(f"gh at {gh} did not report a version: install or update gh from {GH_URL}")
        elif have < _parse_version(github.MIN_GH):
            problems.append(f"gh {'.'.join(map(str, have))} is older than {github.MIN_GH}: "
                            f"update gh from {GH_URL}")
        try:
            logged_in = run(tool_argv(gh) + ["auth", "token"], capture_output=True, text=True,
                            timeout=CHECK_TIMEOUT).returncode == 0     # the token is never read or printed
        except Exception:
            logged_in = False
        if not logged_in:
            problems.append("gh is not logged in: run `gh auth login`")
    if not which("ruff"):
        warnings.append("warning: ruff is not on PATH; it is needed only to run agent-vault's own "
                        "tests and `ruff check`, not to use it")
    return tools, problems, warnings


def prerequisites(recorded, report):
    """The checked tools, or exit naming every failure (before any write)."""
    if os.environ.get(TEST_PREREQS_ENV):
        return {n: Path(os.path.abspath(w)).as_posix() for n in ("git", "gh") if (w := shutil.which(n))}
    tools, problems, warnings = check_prerequisites(recorded)
    if problems:
        sys.exit("error: install.py can't continue, nothing was changed: " + "; ".join(problems) + ".")
    report += warnings
    return tools


def python_cmd() -> str:
    return f'"{Path(sys.executable).as_posix()}"'


def hook_cmd(install_dir: Path, script: str) -> str:
    """The hook launcher reads the Python from the config at run time, so no Python path is in the command."""
    blocking = " --blocking" if script in BLOCKING_HOOKS else ""
    return f'"{(install_dir / "bin" / "hook").as_posix()}" {script}{blocking}'


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


def set_git_hooks(repo: Path, report: list[str], git: str = "git") -> None:
    if not (repo / ".git").exists():
        return
    try:
        subprocess.run([git, "-C", str(repo), "config", "core.hooksPath", HOOKS_DIR],
                       check=True, capture_output=True)
        report.append(f"git hooks: core.hooksPath = {HOOKS_DIR}")
    except Exception as e:
        report.append(f"warning: could not set core.hooksPath ({e})")


def commit_info(repo: Path, commit: str | None, git: str = "git") -> dict:
    """{"commit": full sha, "commit_time": committer time in Unix seconds} for `commit` (else HEAD)
    in the clone `repo`; {} when git can't say (not a work tree, unknown sha, git missing)."""
    try:
        out = subprocess.run([git, "-C", str(repo), "log", "-1", "--format=%H %ct",
                              (commit or "HEAD") + "^{commit}", "--"],
                             capture_output=True, text=True, check=True).stdout.split()
        return {"commit": out[0], "commit_time": int(out[1])}
    except Exception:
        return {}


GUIDE_MARK = "<!-- agent-vault"
GUIDE_MARKER_RE = re.compile(r"^<!-- agent-vault (\d+) ([0-9a-f]{40}) -->$")
GUIDE_HELD = "warning: the vault guide is from"       # selfupdate looks for this in the output


def guide_state(path: Path):
    """None for a guide with no marker (or none at all); (time, commit) for a marked one;
    (None, None) for a first line that starts the marker but doesn't parse."""
    try:
        first = (path.read_text(encoding="utf-8-sig").splitlines() or [""])[0]
    except OSError:
        return None
    if not first.startswith(GUIDE_MARK):
        return None
    m = GUIDE_MARKER_RE.match(first)
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def write_guide(vault: Path, cli: str, info: dict, force: bool, report: list[str]) -> None:
    path = vault / "CLAUDE.md"
    template = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
    text = template.replace("{{CLI}}", cli)
    if info:
        text = f"{GUIDE_MARK} {info['commit_time']} {info['commit']} -->\n" + text
    state = guide_state(path)
    hold = "Run `git pull` and rerun install.py, or pass --force-guide."
    if state is not None and not force:
        theirs, sha = state
        if theirs is not None and info and info["commit_time"] >= theirs:
            pass                                    # same age or newer: write it
        elif theirs is not None and info:
            report.append(f"{GUIDE_HELD} a newer agent-vault (commit {sha}); left it alone. {hold}")
            return
        else:
            why = ("this install can't tell its own commit" if theirs is not None
                   else "its marker can't be read")
            report.append(f"{GUIDE_HELD} an agent-vault of unknown age ({why}); left it alone. {hold}")
            return
    if write_if_changed(path, text):
        report.append(f"updated {path}")


def install(vault: Path, home: Path, repo: Path = REPO, commit: str | None = None,
            force_guide: bool = False) -> list[str]:
    vault = Path(os.path.abspath(vault))
    if not (vault / ".obsidian").is_dir():
        sys.exit(f"error: {vault} is not an Obsidian vault (no .obsidian/ folder)")
    home = home.resolve()
    report: list[str] = []
    config_path = home / ".claude" / "agent-vault.json"
    tools = prerequisites(read_json(config_path), report)     # before anything is written
    if sys.prefix != sys.base_prefix:
        report.append("warning: running inside a virtualenv; hooks will use its Python, and "
                      "if it's deleted tool calls stay blocked until install.py is rerun. Prefer rerunning "
                      "with your system Python.")
    claude = home / ".claude"
    install_dir = claude / "agent-vault"
    cli = CLI
    direct = f'{python_cmd()} "{(install_dir / "vault.py").as_posix()}"'
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
    info = commit_info(repo, commit, tools.get("git", "git"))
    config = {"vault": vault.as_posix(), "python": Path(sys.executable).as_posix(), **tools,
              "managed_allow": allow, "source": Path(os.path.abspath(repo)).as_posix(), **info}
    write_if_changed(config_path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")

    write_guide(vault, cli, info, force_guide, report)

    migrate_v1(vault, report)
    set_git_hooks(repo, report, tools.get("git", "git"))
    report.append(f"check: {cli} status")
    if sys.platform == "win32":      # the launcher runs only in Bash (D5 of #44); PowerShell and cmd use Python directly
        report.append(f"check (PowerShell or cmd): {direct} status")
    return report


def unset_git_hooks(repo: Path, report: list[str], git: str = "git") -> bool:
    """Unset core.hooksPath in this clone if it still points at our hooks (git would otherwise
    run a pre-push hook that refuses every push once the config is gone)."""
    if not (repo / ".git").exists():
        return False
    try:
        cur = subprocess.run([git, "-C", str(repo), "config", "--get", "core.hooksPath"],
                             capture_output=True, text=True).stdout.strip()
        if cur != HOOKS_DIR:
            return False
        subprocess.run([git, "-C", str(repo), "config", "--unset", "core.hooksPath"],
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
    recorded = config.get("git")
    unset_git_hooks(repo, report, recorded if isinstance(recorded, str) and Path(recorded).is_file()
                    else "git")
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
    ap.add_argument("--force-guide", action="store_true",
                    help="write the vault guide even if it is from a newer agent-vault")
    args = ap.parse_args()
    if args.uninstall:
        for line in uninstall(Path(args.home), Path(args.repo)):
            print(line)
        return
    if not args.vault:
        ap.error("--vault is required (or use --uninstall)")
    for line in install(Path(args.vault), Path(args.home), Path(args.repo), args.commit,
                        args.force_guide):
        print(line)


if __name__ == "__main__":
    main()
