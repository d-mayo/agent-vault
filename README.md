# agent-vault

Scripts, hooks and skills for a Claude "second brain" that lives in Obsidian.
Claude plans, implements and reviews work on GitHub issues. The vault is its
working memory between sessions. Design: [`docs/design-v1.md`](docs/design-v1.md).

## Install

Requires Python 3.9+, git and gh 2.94.0 or later (the first release with sub-issues). On Windows, run these in Git Bash or PowerShell.

```bash
git clone https://github.com/d-mayo/agent-vault
cd agent-vault
python install.py --vault "C:/Users/<you>/iCloudDrive/iCloud~md~obsidian/Notes"
```

The installer is safe to rerun after every `git pull`. It:

- copies the code to `~/.claude/agent-vault/` and any skills to `~/.claude/skills/`, replacing the installed files file by file (from a staged copy, so a running session never finds a hook script missing) and deleting the ones the new version dropped;
- writes `~/.claude/agent-vault.json` with the vault path, the installer's Python (the pre-push hook runs the lint with it), and the clone it installed from (`source`) with the commit installed (`commit`, `commit_time`);
- merges every hook and the permission to run the CLI into `~/.claude/settings.json`,
  only ever replacing entries it created, backing the file up to `.bak` first, and removes
  the entries older versions put in `<vault>/.claude/settings.json`;
- writes `<vault>/CLAUDE.md`;
- deletes the v1 scripts from `<vault>/Agent/_system/` if they're still there (the phone log is kept);
- turns on this repo's git hooks (`agent_vault/githooks/`); other repos get the installed copy from `repo-init`.

Hooks run with the same Python that ran the installer, so if you switch Python
installs, rerun it.

After that first run you don't have to rerun it by hand: the installed copy keeps itself current
with `main`. `seal retro`, after it merges a PR in the clone you installed from, installs what
`main` now holds, and every session start checks whether that clone's local `main` has a newer commit
than the installed one and installs it if so. Both install only `main`'s committed tree (never a
branch or uncommitted changes), look at local refs only (a `git pull` brings in a merge made on
GitHub), say what they did, or how to run `install.py` by hand when they can't, and take effect in
the next session. This happens on the PC only; phone and cloud sessions have no config. An install
from before this change has no `source`, so after pulling it run `install.py` once by hand.

To remove it, run `python install.py --uninstall` (no `--vault` needed). It takes out the installed
code, the skills it installed, its entries and permission in the settings files, its config and this
clone's `core.hooksPath`, and leaves the vault's notes and `<vault>/CLAUDE.md` alone; then restart
open Claude sessions.

**To verify:** run the `check:` command it prints last. Then start Claude Code in
the vault and ask it to quote the context it was given at session start: it should
begin with "Vault context". (The app doesn't display that context; it goes straight
into Claude's context.)

## Git conventions

- Never commit to `main`. Use a branch named `<type>/<issue>-<slug>`, for example
  `feat/12-note-schema`, and open a PR. Only the owner merges, with **Squash and merge**;
  the one exception is `vault.py seal retro`, which squash-merges the PR itself once the
  owner (in a `handoff` run, the orchestrating session) has approved the retro and the PR's checks have passed. It also closes the issue's parent feature when every story of the feature's
  design is filed and no other sub-issue is open (`vault.py closes-feature` shows whether it will).
- Commit messages follow `<type>(<scope>): <subject>`.
- Two local guards enforce this without depending on GitHub settings: the
  `pre-push` hook (`agent_vault/githooks/`, which also runs the lint), and a Claude Code hook that blocks pushes to `main`
  and `--no-verify` in every session. The same hook blocks `gh issue create` in registered
  repos: issues go through `vault.py issue create` with a placement flag (`--parent`, `--standalone` or `--bug`; the `issue` skill), which checks the body
  against one template. A design's stories are filed under its feature issue (`issue create --feature`,
  then `--parent`), so GitHub shows them as its sub-issues.

## Lint and tests

Development needs [ruff](https://docs.astral.sh/ruff/) on `PATH`: `python -m pip install ruff`. On
Windows pip's `Scripts` folder (for the Microsoft Store Python,
`...\LocalCache\local-packages\Python312\Scripts`) often isn't on `PATH`; add it, then check
`ruff --version` in a new shell. Each repo declares its lint command on a `Lint:` line under
`## Commands` in its `CLAUDE.md` (here `ruff check .`, rules pinned in `ruff.toml`).

```bash
ruff check .                                  # the lint alone
python ~/.claude/agent-vault/vault.py lint    # claudemd-lint, then the repo's Lint: command
python -m unittest discover -s tests -t .     # the tests, classes in parallel; ruff on PATH too (AGENT_VAULT_TESTS_SERIAL=1: one process)
```

The pre-push hook runs `vault.py lint` before any push that creates or updates a branch, and
implement-story runs it before every review. After pulling this change, rerun `install.py`: the
hook needs the `python` field it now writes to the config.
