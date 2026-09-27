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
  `pre-push` hook (`agent_vault/githooks/`), and a Claude Code hook that blocks pushes to `main`
  and `--no-verify` in every session.

## Tests

```bash
python -m unittest discover -s tests -t .
```
