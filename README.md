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
- writes `~/.claude/agent-vault.json` with the vault path;
- merges its hooks into `~/.claude/settings.json` and `<vault>/.claude/settings.json`,
  only ever replacing entries it created, and backing up each file to `.bak` first;
- writes `<vault>/CLAUDE.md`;
- deletes the v1 scripts from `<vault>/Agent/_system/` if they're still there (the handoff log is kept);
- turns on this repo's git hooks (`.githooks/`).

Hooks run with the same Python that ran the installer, so if you switch Python
installs, rerun it.

**To verify:** run the `check:` command it prints last. Then start Claude Code in
the vault: its first reply should contain "Vault context".

## Git conventions

- Never commit to `main`. Use a branch named `<type>/<issue>-<slug>`, for example
  `feat/12-note-schema`, and open a PR. Only the owner merges, with **Squash and merge**.
- Commit messages follow `<type>(<scope>): <subject>`.
- Two local guards enforce this without depending on GitHub settings: the
  `.githooks/pre-push` hook, and a Claude Code hook that blocks pushes to `main`
  and `--no-verify` in every session.

## Tests

```bash
python -m unittest discover -s tests -t .
```
