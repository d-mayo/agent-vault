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
with `main`. `vault.py merge`, after it merges a PR in the clone you installed from, installs what
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

**A history of `Agent/`.** Every note write is atomic (a hidden temporary file renamed over the note),
and the first SessionStart of a vault or repo session snapshots `Agent/` (when something changed
since the last snapshot) into a git repository kept beside the config,
`~/.claude/agent-vault-history.git`, never in the vault. `vault.py restore <note> [--snapshot <id>]`
puts a note back byte for byte from the newest snapshot or a named one, and `restore <note> --list`
shows the snapshots in which it changed. The Stop hook's message about a note changed through the
shell names the snapshot taken at the session's start and the exact `restore` command. The history
only grows, and nothing in it is cleaned up.

**To verify:** run the `check:` command it prints last. Then start Claude Code in
the vault and ask it to quote the context it was given at session start: it should
begin with "Vault context". (The app doesn't display that context; it goes straight
into Claude's context.)

## Work setup

Each vault can say how its stories and PRs work in a setup note, `Agent/Setup.md`: the tracker
(GitHub issues or Jira tickets), the merge style, whether PRs need approvals, whether `repo-init`
manages a repo's settings or only checks them, the branch-name pattern with the words for a story and
a bug, and, for Jira, the connector's name, the issue types and the planned label. The note holds the
same values on every machine, so it has no path in it. A vault with no setup note works exactly as
before: GitHub issues, squash merges, no required approvals, settings managed by `repo-init`,
branches `{type}/{issue}-{slug}`. Every command that takes an issue takes its number, or in a Jira
setup its ticket key (`PROJ-12`, with the prefix of the project's `jira_project`); branch names, the
pre-push hook and the retro's PR check all follow the setup's branch pattern.

In a Jira setup the CLI never calls Jira: a skill fetches the ticket through the connector and writes a
ticket file, JSON with `key`, `title`, `body`, `type`, `labels` and `parent`, outside the clone and the
vault, and hands it to `seal plan`, `preflight`, `branch`, `open-pr`, `seal retro` and `stage` with
`--ticket <file>`. The plan records a hash of the ticket's title and body instead of GitHub's edit time (a
changed ticket only warns), a story counts as merged when a PR carrying its key has, and nothing changes
a ticket's status. `vault.py tracker <repo>` prints the tracker and, for Jira, the connector, the
project's key, the ticket file's fields and the issue types, so a skill learns them without reading the setup note.

After `install.py`, start Claude Code in the vault and say "set up this vault" (the `setup` skill):
it interviews you on each value, shows exactly what it will write, and runs `vault.py setup` only
after you say yes. `vault.py setup` refuses an incomplete or contradictory setup, and refuses to
overwrite an existing note without `--replace`; `validate` checks the note and `schema` lists its
fields. `merge`, `seal retro`'s next step and `repo-init` read it. `repo-init` sets the setup's merge
style (allowing only that style) where repo settings are `managed`, and only checks that the repo
allows it where they are `checked`, refusing and naming what to ask a repo admin for if not. In a
Jira setup it creates no GitHub labels and writes no issue template.

## Git conventions

- Never commit to `main`. Use a branch named by the setup's branch pattern (default
  `<type>/<issue>-<slug>`, the issue a number or, in a Jira setup, the ticket key), for example
  `feat/12-note-schema`, and open a PR. Only the owner merges, with **Squash and merge**;
  the one exception is `vault.py merge` (the `merge` skill), which merges the PR itself, with the
  merge style of the vault's setup, once the owner (in a `handoff` run, the orchestrating session)
  has approved. `vault.py seal retro` only readies the PR: it pushes the `verified:` bump and records
  the commit it readied, and `merge` then merges exactly that commit, once its checks have passed and,
  where the setup requires approvals, the PR is approved. The merge also closes the issue's parent
  feature when every story of the feature's design is filed and no other sub-issue is open
  (`vault.py closes-feature` shows whether it will). Commits made after a retro need a follow-up
  retro (`vault.py new retro` again) before they merge.
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

The pre-push hook refuses a branch whose name doesn't follow the setup's branch pattern
(`vault.py check-branch`), runs `vault.py lint` before any push that creates or updates a branch, and
implement-story runs it before every review.
