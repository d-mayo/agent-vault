# agent-vault

Scripts, hooks and skills for a Claude "second brain" that lives in Obsidian.
Claude plans, implements and reviews work on GitHub issues. The vault is its
working memory between sessions. Design: [`docs/design-v1.md`](docs/design-v1.md).

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
the vault and ask it to quote the context it was given at session start: it should
begin with "Vault context". (The app doesn't display that context; it goes straight
into Claude's context.)

## Git conventions

- Never commit to `main`. Use a branch named `<type>/<issue>-<slug>`, for example
  `feat/12-note-schema`, and open a PR. Only the owner merges, with **Squash and merge**.
- Commit messages follow `<type>(<scope>): <subject>`.
- Two local guards enforce this without depending on GitHub settings: the
  `pre-push` hook (`agent_vault/githooks/`, which also runs the lint), and a Claude Code hook that blocks pushes to `main`
  and `--no-verify` in every session. The same hook blocks `gh issue create` in registered
  repos: issues go through `vault.py issue create` (the `issue` skill), which checks the body
  against one template.

## Lint and tests

Development needs [ruff](https://docs.astral.sh/ruff/) on `PATH`: `python -m pip install ruff`. On
Windows pip's `Scripts` folder (for the Microsoft Store Python,
`...\LocalCache\local-packages\Python312\Scripts`) often isn't on `PATH`; add it, then check
`ruff --version` in a new shell. Each repo declares its lint command on a `Lint:` line under
`## Commands` in its `CLAUDE.md` (here `ruff check .`, rules pinned in `ruff.toml`).

```bash
ruff check .                                  # the lint alone
python ~/.claude/agent-vault/vault.py lint    # claudemd-lint, then the repo's Lint: command
python -m unittest discover -s tests -t .     # the tests; they need ruff on PATH too
```

The pre-push hook runs `vault.py lint` before any push that creates or updates a branch, and
implement-story runs it before every review. After pulling this change, rerun `install.py`: the
hook needs the `python` field it now writes to the config.
