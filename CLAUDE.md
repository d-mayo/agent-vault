# agent-vault

## Purpose
Scripts, hooks and skills for the Claude + Obsidian workflow; installed to `~/.claude/` by `install.py`.
Project: agent-vault. Design record: `docs/design-v1.md` (sealed; the code is the source of truth).

## Commands
<!-- covers: install.py, tests/**; verified: 2026-10-04 -->
- Run tests: `python -m unittest discover -s tests -t .` (stdlib only, but the tests need `ruff` on your PATH; pytest also works)
- Lint: `ruff check .`
- Install locally: `python install.py --vault <path to Notes>`
- CLI after install: `<installer's python> ~/.claude/agent-vault/vault.py <command>`; the exact string (which the vault's permission rule matches) is lib.CLI, printed by the installer and written into the vault's CLAUDE.md.

## Layout
<!-- covers: agent_vault/**, install.py, templates/**, skills/**, agent_vault/githooks/**, .github/**; verified: 2026-10-01 -->
- `agent_vault/`: everything copied to `~/.claude/agent-vault/`. `agent_vault/lib.py` holds the schema and shared helpers; `agent_vault/vault.py` is the CLI; `agent_vault/github.py` is the only place that runs `gh` and `git` (`AGENT_VAULT_GH` overrides where `gh` is found); `agent_vault/claudemd.py` lints `CLAUDE.md` (paths, commands, covers globs, size cap, the `Lint:` line and staleness; `vault.py claudemd-lint` runs it; `vault.py lint` runs it and then the repo's declared `Lint:` command); `agent_vault/hooks/` holds the Claude Code hooks.
- `agent_vault/hooks/push_guard.py`: user-level guard for the Bash and PowerShell tools (no pushes to main, no `--no-verify`, no `gh issue create` or `gh issue new` aimed at a registered repo: issues go through `vault.py issue create`).
- `install.py`: idempotent installer; owns only settings entries whose command contains `/.claude/agent-vault/`; writes the vault path, its own Python (`python`) and the CLI permission to `~/.claude/agent-vault.json`.
- `templates/vault-CLAUDE.md`: the vault's `CLAUDE.md`, with `{{CLI}}` filled in at install.
- `agent_vault/templates/repo-CLAUDE.md`: the skeleton `repo-init` writes for a newly registered repo's own `CLAUDE.md`.
- `skills/`: user-level skills; each subfolder is copied to `~/.claude/skills/`.
- `skills/implement-story/`: takes a sealed plan from branch to open PR, step by step, with one review at the end; `skills/implement-story/reviewer.md` holds the reviewer's own prompt and findings format.
- `skills/plan-story/`: turns an issue into a sealed plan, researching first, interviewing only where research leaves something ambiguous, and reviewing the draft with an independent reviewer before the user approves it; `skills/plan-story/plan-reviewer.md` holds the reviewer's own prompt and findings format.
- `skills/design/`: turns a larger idea into a sealed design note, decision records and one issue per story: researches, interviews over as many rounds as needed, holds the draft to the rigor rules, has an independent Opus reviewer check it (`skills/design/design-reviewer.md`), seals with `vault.py seal design` once the user approves, then files the stories through `vault.py issue create`; `vault.py design stories` shows which are unfiled.
- `skills/issue/`: the one way to create a GitHub issue: drafts the title and body in the issue template (`lib.ISSUE_SECTIONS`), asks about what's unclear, and calls `vault.py issue create` only after the user approves the exact wording; the retro and plan-story skills call that command too.
- `skills/retro/`: closes out an issue before merge — audits the repo `CLAUDE.md`, its `README.md` and the project overview one heading at a time, sorts follow-ups, including every `→ left open` review finding (named by a trailing `(R<n>)`; `seal retro` refuses an unsorted one), into an issue, an idea or dropped, and seals (`seal retro` also refuses an open issue whose PR no longer closes it, and then squash-merges that PR itself once its checks have passed and the user has approved the retro; otherwise only the user merges); also runs as a backfill for an issue that already merged without one.
- Decision records (`Agent/Decisions/<project>/`): one per lasting decision, created only by `vault.py decision add` or, for a design's decisions, `vault.py seal design`, never edited and replaced by a later record (`--replaces`). The guard blocks the edit tools on them and the Stop hook fingerprints all but `status` and `superseded_by`. A plan decision line ending ` (lasting)` needs a record before `seal retro`; the overview's `## Standing decisions` is generated from the records.
- Designs (`Agent/Designs/<project>/`): a draft until `vault.py seal design` records its decisions, promotes its ideas and seals it; a sealed design is never edited (a later one `extends` or `replaces` it). The guard blocks the edit tools on a sealed one and the Stop hook fingerprints it; `vault.py design stories` shows which stories have no issue yet.
- `agent_vault/githooks/pre-push`: blocks pushes to `main` and bad branch names, and runs `vault.py lint` on any push that creates or updates a branch. The one copy: this repo's `core.hooksPath` points at it, and `repo-init` points other repos at the installed `~/.claude/agent-vault/githooks/`.
- `.github/ISSUE_TEMPLATE/issue.md`: the GitHub issue template for this repo, identical to what `repo-init` writes (generated from `lib.ISSUE_SECTIONS`; a test keeps them equal).
- `tests/`: unittest suites; hooks and the CLI are run as subprocesses with `AGENT_VAULT_CONFIG`. `tests/fake_gh.py` stands in for `gh` (`AGENT_VAULT_GH`); tests never touch GitHub.

## Conventions
<!-- covers: agent_vault/**; verified: 2026-10-04 -->
- Standard library only; Python 3.9+; must run on Windows.
- Scripts find the vault through `~/.claude/agent-vault.json` (`lib.VAULT`); `AGENT_VAULT_CONFIG` overrides it. With no config, the CLI exits with an error (except `lint`, which reads nothing from the vault) and Claude hooks exit 0 silently.
- Hooks never write `__pycache__` (`sys.dont_write_bytecode`), never block a session from starting, and report blocks with exit code 2 and a message on stderr.
- All note rules live as constants in `agent_vault/lib.py`; `vault.py schema` prints them from there.

## Gotchas
<!-- covers: agent_vault/hooks/**, install.py; verified: 2026-10-04 -->
- Hook commands are absolute forward-slash paths to the installer's Python (`sys.executable`) and the script, so they don't depend on PATH or how the Windows hook runner expands `$HOME`. `lib.CLI` builds the same string at runtime; the vault's `Bash(...)` permission must match it exactly.
- `.gitattributes` pins LF endings: a CRLF `agent_vault/githooks/pre-push` fails in bash and blocks every push.
- `agent_vault/hooks/push_guard.py` tokenizes quote-aware and treats heredoc bodies as data; add a test case for every new command shape it must handle. Its `gh issue create` rule reads the vault config, so those tests must pass a temp config (`AGENT_VAULT_CONFIG`) or they read the real machine's.
- The pre-push hook is one shared file, so it takes Python from the `python` field of the config (never `PATH`) and runs the vault CLI script next to its own folder; with either missing it refuses the push and says to rerun `install.py`.
- Remote tools can't write `~/.claude/` folders; only `install.py`, run on the PC, can.
