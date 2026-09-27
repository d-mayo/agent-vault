# agent-vault

## Purpose
Scripts, hooks and skills for the Claude + Obsidian workflow; installed to `~/.claude/` by `install.py`.
Project: agent-vault. Design record: `docs/design-v1.md` (sealed; the code is the source of truth).

## Commands
<!-- covers: install.py, tests/** -->
- Run tests: `python -m unittest discover -s tests -t .` (stdlib only; pytest also works)
- Install locally: `python install.py --vault <path to Notes>`
- CLI after install: `<installer's python> ~/.claude/agent-vault/vault.py <command>`; the exact string (which the vault's permission rule matches) is lib.CLI, printed by the installer and written into the vault's CLAUDE.md.

## Layout
<!-- covers: agent_vault/**, install.py, templates/**, skills/**, agent_vault/githooks/** -->
- `agent_vault/`: everything copied to `~/.claude/agent-vault/`. `agent_vault/lib.py` holds the schema and shared helpers; `agent_vault/vault.py` is the CLI; `agent_vault/github.py` is the only place that runs `gh` and `git` (`AGENT_VAULT_GH` overrides where `gh` is found); `agent_vault/hooks/` holds the Claude Code hooks.
- `agent_vault/hooks/push_guard.py`: user-level Bash guard (no pushes to main, no `--no-verify`).
- `install.py`: idempotent installer; owns only settings entries whose command contains `/.claude/agent-vault/`.
- `templates/vault-CLAUDE.md`: the vault's `CLAUDE.md`, with `{{CLI}}` filled in at install.
- `skills/`: user-level skills; each subfolder is copied to `~/.claude/skills/`.
- `skills/implement-story/`: takes a sealed plan from branch to open PR, step by step, with a review after every step; `skills/implement-story/reviewer.md` holds the reviewer's own prompt and findings format.
- `skills/plan-story/`: turns an issue into a sealed plan, researching first, interviewing only where research leaves something ambiguous, and reviewing the draft with an independent reviewer before the user approves it; `skills/plan-story/plan-reviewer.md` holds the reviewer's own prompt and findings format.
- `skills/retro/`: closes out an issue before merge — audits the repo `CLAUDE.md`, its `README.md` and the project overview one heading at a time, sorts follow-ups into an issue, an idea or dropped, and seals; also runs as a backfill for an issue that already merged without one.
- `agent_vault/githooks/pre-push`: blocks pushes to `main` and bad branch names. The one copy: this repo's `core.hooksPath` points at it, and `repo-init` points other repos at the installed `~/.claude/agent-vault/githooks/`.
- `tests/`: unittest suites; hooks and the CLI are run as subprocesses with `AGENT_VAULT_CONFIG`. `tests/fake_gh.py` stands in for `gh` (`AGENT_VAULT_GH`); tests never touch GitHub.

## Conventions
<!-- covers: agent_vault/** -->
- Standard library only; Python 3.9+; must run on Windows.
- Scripts find the vault through `~/.claude/agent-vault.json` (`lib.VAULT`); `AGENT_VAULT_CONFIG` overrides it. With no config, the CLI exits with an error and hooks exit 0 silently.
- Hooks never write `__pycache__` (`sys.dont_write_bytecode`), never block a session from starting, and report blocks with exit code 2 and a message on stderr.
- All note rules live as constants in `agent_vault/lib.py`; `vault.py schema` prints them from there.

## Gotchas
<!-- covers: agent_vault/hooks/**, install.py -->
- Hook commands are absolute forward-slash paths to the installer's Python (`sys.executable`) and the script, so they don't depend on PATH or how the Windows hook runner expands `$HOME`. `lib.CLI` builds the same string at runtime; the vault's `Bash(...)` permission must match it exactly.
- `.gitattributes` pins LF endings: a CRLF `agent_vault/githooks/pre-push` fails in bash and blocks every push.
- `agent_vault/hooks/push_guard.py` tokenizes quote-aware and treats heredoc bodies as data; add a test case for every new command shape it must handle.
- Remote tools can't write `~/.claude/` folders; only `install.py`, run on the PC, can.
