"""`vault.py doctor`: one read-only check of a broken or half-installed agent-vault.

Checks the config and its Python, `gh` and `git`, the Claude hook entries in `settings.json`, the git hooks of
the current clone and of the config's `source` clone, and whether the installed copy is behind `main`. Each
check gives lines that start `ok`, `problem` (which names its fix) or `skipped` (a check that can't apply).
It changes nothing and makes no network call: the only `gh` calls are `gh --version` and `gh auth token`
(whose output is never read), and it never calls selfupdate or writes a snapshot. It must run when the config
is missing or its Python has moved, so it reads the config itself and never lets a failed tool lookup raise.
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import github  # noqa: E402
import lib  # noqa: E402

LIMIT = 60                                  # seconds all the tool calls together may take
INSTALL_MARK = "/.claude/agent-vault/"      # install.py's MARK: identifies our hook commands
LEGACY_MARK = "Agent/_" + "system/hooks/"    # a v1 hook command, which install.is_our_hook also counts as ours (split: a test bans the word in code)
SOURCE_HOOKS = "agent_vault/githooks"       # install.py's HOOKS_DIR: the source clone's core.hooksPath
# The hook scripts install.user_hooks writes, by event; a test keeps this equal to it (install.py isn't installed).
EXPECTED_HOOKS = {
    "SessionStart": ["session_start.py"],
    "PreToolUse": ["guard.py", "push_guard.py"],
    "PostToolUse": ["post_edit.py"],
    "Stop": ["stop_check.py"],
}


def _ok(what: str) -> list[str]:
    return [f"ok: {what}"]


def _problem(what: str, fix: str) -> str:
    return f"problem: {what}. Fix: {fix}"


def _skipped(what: str) -> list[str]:
    return [f"skipped: {what}"]


def _version(text: str):
    first = (text or "").strip().splitlines()[:1]
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", first[0]) if first else None
    return tuple(int(x) for x in m.groups()) if m else None


def _shown(version) -> str:
    return ".".join(map(str, version))


def read_config() -> tuple[dict | None, list[str]]:
    """The config as a dict (None when it can't be read) and the lines of the config and Python checks."""
    path = lib.CONFIG
    reinstall = "rerun install.py --vault <path to Notes>"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, [_problem(f"config: {path} is missing", reinstall)]
    except OSError as e:
        return None, [_problem(f"config: {path} can't be read ({e})", reinstall)]
    try:
        config = json.loads(text)
    except ValueError as e:
        return None, [_problem(f"config: {path} isn't valid JSON ({e})", reinstall)]
    if not isinstance(config, dict):
        return None, [_problem(f"config: {path} isn't a JSON object", reinstall)]
    out, problems = [], []
    vault = config.get("vault")
    if isinstance(vault, str) and vault:
        if (Path(vault) / ".obsidian").is_dir():
            out.append(f"ok: config: {path}, vault {vault}")
        else:
            problems.append(_problem(f"config: the vault {vault} has no .obsidian folder (moved or not synced?)",
                                     f'rerun install.py --vault "{vault}", or with the vault\'s new path'))
    else:
        problems.append(_problem(f"config: {path} records no vault", reinstall))
    py = config.get("python")
    if isinstance(py, str) and py and Path(py).is_file():
        out.append(f"ok: python: {py}")
    elif isinstance(py, str) and py:
        problems.append(_problem(f"python: the recorded Python {py} no longer exists", reinstall))
    else:
        problems.append(_problem(f"python: {path} records no Python", reinstall))
    return config, problems + out


def _recorded_gone(config: dict, name: str, reinstall: str) -> str | None:
    old = (config or {}).get(name)
    if isinstance(old, str) and old and not Path(old).is_file():
        return _problem(f"{name}: the recorded path {old} no longer exists (a different {name} may be found "
                        "on PATH, not the one the installer checked)", reinstall)
    return None


def check_gh(config: dict | None) -> list[str]:
    reinstall = "rerun install.py"
    lines = []
    gone = _recorded_gone(config, "gh", reinstall)
    if gone:
        lines.append(gone)
    try:
        where = " ".join(github.gh_command())
    except github.CmdError as e:
        return lines + [_problem(f"gh: {e}", f"install gh from {github.INSTALL_URL} (version {github.MIN_GH} or "
                                 "newer), run `gh auth login`, then rerun install.py")]
    try:
        have = _version(github.run(["gh", "--version"], check=False).stdout)
    except github.CmdError as e:
        return lines + [_problem(f"gh: can't run {where} ({e})", f"install gh from {github.INSTALL_URL}")]
    update = f"update gh from {github.INSTALL_URL}"
    if have is None:
        lines.append(_problem(f"gh: {where} did not report a version", update))
    elif have < _version(github.MIN_GH):
        lines.append(_problem(f"gh: {_shown(have)} is older than {github.MIN_GH}", update))
    else:
        lines.append(f"ok: gh {_shown(have)} ({where})")
    try:
        code = github.run(["gh", "auth", "token"], check=False).returncode      # the token is never read
    except github.CmdError as e:
        return lines + [_problem(f"gh: can't check the login ({e})", "run `gh auth login`")]
    lines.append("ok: gh is logged in" if code == 0 else _problem("gh: not logged in to GitHub", "run `gh auth login`"))
    return lines


def git_found() -> bool:
    return bool(lib.tool_path("git") or shutil.which("git"))


def check_git(config: dict | None) -> list[str]:
    lines = []
    gone = _recorded_gone(config, "git", "rerun install.py")
    if gone:
        lines.append(gone)
    if not git_found():
        return lines + [_problem("git: not found", "install git, then rerun install.py")]
    return lines + _ok("git: " + " ".join(github.git_command()))


def _commands(settings: dict):
    """(event, command) for every hook command in a settings dict."""
    hooks = settings.get("hooks")
    for event, groups in (hooks.items() if isinstance(hooks, dict) else []):
        for group in groups if isinstance(groups, list) else []:
            for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    yield event, h["command"]


def check_hooks() -> list[str]:
    reinstall = "rerun install.py"
    path = lib.CONFIG.parent / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        return [_problem(f"hooks: {path} is missing", reinstall)]
    except (OSError, ValueError) as e:
        return [_problem(f"hooks: {path} is unreadable ({e})", reinstall)]
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for event, command in _commands(settings):
        if LEGACY_MARK in command:
            problems.append(_problem(f"hooks: {event} still has a v1 hook entry ({command})",
                                     f"{reinstall} (it removes the entry)"))
            continue
        if INSTALL_MARK not in command:
            continue
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = []
        if len(tokens) < 2:
            problems.append(_problem(f"hooks: {event} entry {command!r} isn't a launcher and a script", reinstall))
            continue
        launcher, script = Path(tokens[0]), tokens[1]
        seen.add((event, script))
        if not launcher.is_file():
            problems.append(_problem(f"hooks: the {event} launcher {launcher} is missing", reinstall))
        elif not (launcher.parent.parent / "hooks" / script).is_file():
            problems.append(_problem(f"hooks: the {event} script {script} is missing beside {launcher}", reinstall))
    for event, scripts in EXPECTED_HOOKS.items():
        for script in scripts:
            if (event, script) not in seen:
                problems.append(_problem(f"hooks: {path} has no {event} entry for {script}", reinstall))
    return problems or _ok(f"hooks: every agent-vault hook has an entry in {path}")


def _hooks_path(top, hooks_path_state, target: Path) -> list[str]:
    state, cur = hooks_path_state(top, target)
    if state == "ours":
        if (target / "pre-push").is_file():
            return _ok(f"git hooks: {top} uses the installed hooks")
        return [_problem(f"git hooks: {target / 'pre-push'} is missing", "rerun install.py")]
    if state == "accepted":
        return _ok(f"git hooks: {top} uses agent-vault's pre-push at {cur}")
    if state == "unset":
        return [_problem(f"git hooks: {top} has no core.hooksPath", f"run `{lib.CLI} repo-init {top}`")]
    return [_problem(f"git hooks: {top} has core.hooksPath {cur}, and its pre-push isn't agent-vault's",
                     f"change or unset it by hand, then run `{lib.CLI} repo-init {top}`")]


def _source_hooks(source: str) -> list[str]:
    cur = github.git("config", "--local", "--get", "core.hooksPath", cwd=source, check=False)
    if cur == SOURCE_HOOKS:
        return _ok(f"git hooks: the source clone {source} uses {SOURCE_HOOKS}")
    return [_problem(f"git hooks: the source clone {source} has core.hooksPath {cur or 'unset'}, not {SOURCE_HOOKS}",
                     "rerun install.py")]


def check_git_hooks(config: dict | None, hooks_path_state) -> list[str]:
    note = ["ok: git hooks of other clones aren't checked; run doctor inside one, or `repo-init <path>` there"]
    if not git_found():
        return _skipped("git hooks: git isn't available") + note
    lines: list[str] = []
    source = (config or {}).get("source")
    source = source if isinstance(source, str) and source and Path(source).is_dir() else None
    top = None
    try:
        found = github.git("rev-parse", "--show-toplevel", cwd=os.getcwd(), check=False)
        top = Path(found) if found else None
    except (github.CmdError, OSError):
        pass
    is_source = bool(top and source and lib.norm_path(top) == lib.norm_path(source))
    if top is None:
        lines += _skipped("git hooks: not inside a git clone")
    elif is_source:
        pass                                   # checked once, by the source rule below
    elif lib.VAULT is None:
        lines += _skipped(f"git hooks: no usable vault to tell whether {top} is registered")
    else:
        try:
            slug = github.origin_slug(top)
        except github.CmdError:
            lines += _skipped(f"git hooks: {top} has no GitHub origin, so it isn't a registered repo")
        else:
            try:
                registered = lib.Index().slugs.get(slug.split("/")[1], "").lower() == slug.lower()
            except Exception as e:             # a note the index can't read: say so, never crash
                lines += _skipped(f"git hooks: can't read the vault's projects ({e})")
            else:
                if registered:
                    lines += _hooks_path(top, hooks_path_state, lib.INSTALL_DIR / "githooks")
                else:
                    lines += _skipped(f"git hooks: {slug} isn't listed in any project's repos:")
    if source:
        lines += _source_hooks(source)
    else:
        lines += _skipped("git hooks: no source clone to check (see the out-of-date check)")
    return lines + note


def check_out_of_date(config: dict | None) -> list[str]:
    """selfupdate's comparison (the source's local `main` against the config's commit_time), never its install."""
    start = "start a new session (SessionStart installs main), or run install.py from the source clone"
    if not git_found():
        return _skipped("up to date: git isn't available")
    source = (config or {}).get("source")
    if not isinstance(source, str) or not source:
        return [_problem("up to date: the config records no source clone", "run install.py from the clone")]
    if not Path(source).is_dir():
        return [_problem(f"up to date: the source clone {source} is missing", "run install.py from a clone")]
    try:
        _, _, ctime = github.git("log", "-1", "--format=%H %ct", "refs/heads/main", "--", cwd=source).partition(" ")
        main_time = int(ctime)
    except (github.CmdError, ValueError) as e:
        return [_problem(f"up to date: can't read main in {source} ({e})", "run install.py from the clone")]
    installed = config.get("commit_time")
    if not isinstance(installed, int) or isinstance(installed, bool):
        return [_problem("up to date: the config records no commit_time", "run install.py from the source clone")]
    if main_time > installed:
        return [_problem(f"up to date: main in {source} is newer than the installed copy", start)]
    return _ok(f"up to date: the installed copy is main of {source} or newer")


def run(hooks_path_state) -> tuple[list[str], int]:
    """(lines, number of problems). `hooks_path_state` is vault.py's (it is passed in so this module
    doesn't import vault.py). Reads only; never raises."""
    github.DEADLINE = time.monotonic() + LIMIT
    config, lines = read_config()
    steps = [lambda: check_gh(config), lambda: check_git(config), check_hooks,
             lambda: check_git_hooks(config, hooks_path_state), lambda: check_out_of_date(config)]
    for step in steps:
        try:
            lines += step()
        except Exception as e:                 # a check that breaks is itself a finding, never a traceback
            lines.append(_problem(f"{type(e).__name__} while checking: {e}", "report this to the agent-vault maintainer"))
    return lines, sum(1 for ln in lines if ln.startswith("problem"))
