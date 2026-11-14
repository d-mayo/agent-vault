# agent-vault

## Purpose
A personal workflow in which Claude plans, implements and reviews GitHub issues, with this Obsidian vault as its working memory between sessions. Design: `docs/design-v1.md` in the repo.

## Architecture
- One repo, `d-mayo/agent-vault`, cloned locally.
- `install.py` copies the code to `~/.claude/agent-vault/` and skills to `~/.claude/skills/`, and merges every hook and the CLI permission into `~/.claude/settings.json`; after the first manual run, `seal retro` and session start rerun it from `main` (`agent_vault/selfupdate.py`). The hooks fire in all sessions and decide by working folder (vault, registered repo, or neither) and by target path.
- The vault holds notes only; the installed code never lives in it.
