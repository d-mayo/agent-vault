base_sha: fcaee0e

# Research for design 1

Everything the design's reasoning rests on is below: the idea it absorbs, the two open issues it names, and the code and docs it describes, as they stood at `fcaee0e` (the last commit of 2026-10-04, when the design was sealed). The design's own account of what it checked on 2026-10-04 (a `Bash(~/...:*)` permission rule matching a script, a hook running a bare `sh` script) is a test on the user's PC and is stated in the design, not reproduced here. Open issues are shown as filed.

## The idea the design absorbs

Idea note "Make agent-vault portable across machines and setups" (2026-10-01):

`````markdown
# Make agent-vault portable across machines and setups
The installer writes this machine's absolute CLI path into the vault's `CLAUDE.md`, which syncs over iCloud, so two machines sharing a vault overwrite each other's path and the skills read the wrong one. Never run on macOS. Repo `CLAUDE.md` says "must run on Windows"; should say Windows and macOS. Longer-term goal: customizable to someone else's setup.
`````

## Open issues the design names

### #25 Generalize the 'never run install.py' rule in implement-story and retro
`````markdown
## Problem

`skills/implement-story/SKILL.md` and `skills/retro/SKILL.md` both include "Never run `install.py`" in their `## Never` list. Both skills are installed at the user level (`~/.claude/skills/`) and are meant to run against any repo with a sealed plan, not just agent-vault. Hardcoding the name of agent-vault's own installer script leaks project-specific knowledge into what's supposed to be a generic workflow skill: the rule is either meaningless (most repos have no `install.py`) or confusing when read out of context.

`skills/plan-story/SKILL.md` has no equivalent line at all, which is inconsistent either way — either it's missing a rule the other two need, or the other two are over-specific and it got this right.

## What to do

Reword the rule in `implement-story` and `retro` to state the underlying safety concern generically (something like "never run this repo's own installer, or anything that writes outside this repo's working tree") instead of naming `install.py` specifically. Decide whether `plan-story` needs an equivalent line for consistency, or is correctly exempt since it never touches installed code, and record that as a decision.

## Context

- `skills/implement-story/SKILL.md` `## Never`
- `skills/retro/SKILL.md` `## Never`
- Repo `CLAUDE.md` `## Gotchas`: "Remote tools can't write `~/.claude/` folders; only `install.py`, run on the PC, can." — the actual safety rationale these rules are protecting; worth pointing to rather than restating.

[... trimmed]
`````

### #42 retro: also audit the vault guide template and the wording in the code
`````markdown
## Problem
The retro audits the repo CLAUDE.md, the README and the project overview, one heading at a time, so claims in those three go stale only until the next retro. Two other places state the same facts and nothing audits them: `templates/vault-CLAUDE.md` (the vault guide Claude reads every vault session) and the docstrings and messages in the code, for example the guard's docstring and block messages. In #37 a new command made "decision add is the only way to write a decision record" false in the guide and in the guard docstring. Only review caught it; the retro as written would not have.

## Desired outcome
After a retro, every place that describes the workflow's rules has been checked against what the issue changed: the repo CLAUDE.md, the README, the project overview, `templates/vault-CLAUDE.md`, and the guard's and hooks' docstrings and block messages. A change that makes one of them wrong is either fixed before the retro seals or recorded as a follow-up, and `seal retro` refuses a retro that skipped the check.

## Constraints
- The new audit follows the existing audit mechanics: one audit line per heading, each `confirmed`, `rewritten` or `removed` with a reason, and `seal retro` refuses a missing, duplicate or leftover line.
- The vault guide is audited by heading, like the other docs. The code wording has no headings, so it needs a different check; the issue should settle what (for example, a retro line per file the issue's diff touched that holds rule text).
[... trimmed]
`````

## Code and docs at fcaee0e

### `agent_vault/lib.py` (lines 26-26)
`````python
CLI = f'"{Path(sys.executable).as_posix()}" "{(INSTALL_DIR / "vault.py").as_posix()}"'
`````

### `agent_vault/hooks/guard.py` (lines 53-68)
`````python
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
`````

### `agent_vault/github.py` (lines 42-56)
`````python
def gh_command() -> list[str]:
    """The argv prefix that runs gh: AGENT_VAULT_GH, else PATH, else its standard Windows location."""
    override = os.environ.get(GH_ENV)
    if override:
        if not Path(override).is_file() and not shutil.which(override):
            raise CmdError(f"{GH_ENV} points at {override!r}, which doesn't exist", "missing")
        return [sys.executable, override] if override.lower().endswith(".py") else [override]
    found = shutil.which("gh")
    if not found and sys.platform == "win32":
        found = next((str(p) for p in _windows_gh_paths() if p.is_file()), None)
    if not found:
        raise CmdError(f"gh (the GitHub CLI) was not found on PATH or in its standard install "
                       f"location. Install it from {INSTALL_URL} and run `gh auth login`, "
                       f"or set {GH_ENV} to its path", "missing")
    return [found]
`````

### `install.py` (lines 37-56)
`````python
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
`````

### `install.py` (lines 184-223)
`````python
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
        {"vault": vault.as_posix(), "python": Path(sys.executable).as_posix(),
         "managed_allow": allow}, indent=2, ensure_ascii=False) + "\n")

    template = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
    if write_if_changed(vault / "CLAUDE.md", template.replace("{{CLI}}", cli)):
        report.append(f"updated {vault / 'CLAUDE.md'}")

    migrate_v1(vault, report)
    set_git_hooks(repo, report)
    report.append(f"check: {cli} status")
`````

### `agent_vault/githooks/pre-push` (lines 20-34)
`````bash

# Lint once per push, on the working tree. Python comes from the config the installer writes
# (never PATH); vault.py is the one next to this hook's folder. Git runs us in the clone's top level.
config="${AGENT_VAULT_CONFIG:-$HOME/.claude/agent-vault.json}"
py=""
if [ -f "$config" ]; then
  py=$(sed -n 's/^[[:space:]]*"python":[[:space:]]*"\(.*\)"[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p' "$config" | head -n 1)
fi
vault_py="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vault.py"
if [ -z "$py" ] || ! [ -f "$py" ] || ! [ -f "$vault_py" ]; then
  echo "pre-push: cannot find agent-vault's Python or vault.py (config: $config); rerun install.py" >&2
  exit 1
fi
if ! "$py" "$vault_py" lint >&2; then
  echo "pre-push: lint failed, so the push is refused. Fix it, or run: \"$py\" \"$vault_py\" lint" >&2
`````

### `agent_vault/hooks/session_start.py` (lines 85-92)
`````python
    out = [f"claudemd-lint found {len(result.errors)} problem(s):"]
    out += [f"- {e}" for e in result.errors[:MAX_PROBLEMS]]
    if len(result.errors) > MAX_PROBLEMS:
        out.append(f"- ... run `{lib.CLI} claudemd-lint` for all {len(result.errors)}")
    if result.stale:
        out.append(f"({result.stale} stale section(s); run `{lib.CLI} claudemd-lint` to see them)")
    return out
`````
### `agent_vault/hooks/session_start.py` (lines 124-131)
`````python
        out += flat[:MAX_PROBLEMS]
        if len(flat) > MAX_PROBLEMS:
            out.append(f"- ... run `{lib.CLI} validate` for all {len(flat)}")
    else:
        out.append("Vault check: OK")
    n_warn = sum(len(m) for m in warnings.values())
    if n_warn:
        out.append(f"({n_warn} vault warning(s); run `{lib.CLI} validate` to see them)")
`````

### `templates/vault-CLAUDE.md` (line 14, and two lines that use the placeholder the installer fills)
`````markdown
The scripts live outside the vault, in `~/.claude/agent-vault/`, installed from the `d-mayo/agent-vault` repo; change them there, never in place. Code repos live outside the vault. A finished project gets `status: archived` in place.
- Log work with `{{CLI}} log "..." --project <id>`. Past daily notes are closed.
- `{{CLI}} log "<text>" [--project <id>]`
`{{CLI}} lint` (run inside a clone) runs `claudemd-lint` and then the repo's declared `Lint:` command; it fails rather than skip a missing tool. `{{CLI}} schema` prints the full note rules. `{{CLI}} status` lists projects and the open idea count. `{{CLI}} validate` checks everything.
`````

The template has no heading named "CLI after install". The skills (`skills/design`, `implement-story`, `issue` and `retro` SKILL.md) each say the CLI is "the line under 'CLI after install'", and the repo CLAUDE.md line 12 (repo-guide.md) is the only place that heading text appears.

### `README.md` (Install)
`````markdown

## Install

Requires Python 3.9+ and git. On Windows, run these in Git Bash or PowerShell.

```bash
git clone https://github.com/d-mayo/agent-vault
cd agent-vault
python install.py --vault "C:/Users/<you>/iCloudDrive/iCloud~md~obsidian/Notes"
```

The installer is safe to rerun after every `git pull`. It:

- copies the code to `~/.claude/agent-vault/` and any skills to `~/.claude/skills/`;
- writes `~/.claude/agent-vault.json` with the vault path and the installer's Python (the pre-push hook runs the lint with it);
- merges every hook and the permission to run the CLI into `~/.claude/settings.json`,
  only ever replacing entries it created, backing the file up to `.bak` first, and removes
  the entries older versions put in `<vault>/.claude/settings.json`;
- writes `<vault>/CLAUDE.md`;
- deletes the v1 scripts from `<vault>/Agent/_system/` if they're still there (the handoff log is kept);
- turns on this repo's git hooks (`agent_vault/githooks/`); other repos get the installed copy from `repo-init`.

Hooks run with the same Python that ran the installer, so if you switch Python
installs, rerun it.

**To verify:** run the `check:` command it prints last. Then start Claude Code in
`````

