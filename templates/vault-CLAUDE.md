# Vault guide

This vault has two sides.

- **Phone side**: everything outside `Agent/`, including `taxonomy.json`. It's my phone capture system. Read it freely; never change it. To act on a phone note, pull it in with `{{CLI}} handoff pull "<path>"`; that creates an idea.
- **`Agent/`**: your workspace.
  - `Projects/<id>.md`: one overview note per project; it lists the project's GitHub repos in `repos:`.
  - `Work/<repo>/<repo>-<n>-plan.md`, `-impl.md`, `-retro.md`: one set per GitHub issue.
  - `Ideas/<date>-<slug>.md`: one idea per file.
  - `Daily/<date>.md`: the log, one file per day.

The scripts live outside the vault, in `~/.claude/agent-vault/`, installed from the `d-mayo/agent-vault` repo; change them there, never in place. Code repos live outside the vault. A finished project gets `status: archived` in place.

## Rules

Hooks in `.claude/settings.json` enforce these. If a hook blocks you, do what its message says; don't work around it.

- Create notes only with the commands below, then edit the file they create. Daily notes are written only by `log` and the pipeline commands; never edit them.
- A project reference is always `[[project-id]]` of a project that exists. If it's unclear which project something belongs to, ask me instead of guessing.
- Log work with `{{CLI}} log "..." --project <id>`. Past daily notes are closed.
- A project's current truth lives in its project note (`Purpose`, `Current state`, `Architecture`, `Standing decisions`). Daily notes are history.

## Commands

- `{{CLI}} new project <id> [--repo owner/name ...] [--purpose "..."]`
- `{{CLI}} new plan <repo> <issue>`: a draft plan and an open impl note. `{{CLI}} new retro <repo> <issue>`: the retro, once the impl note has a `pr:`.
- `{{CLI}} idea add "<title>" [--project <id>] [--source ...]` and `{{CLI}} idea drop <file> --reason "..."`
- `{{CLI}} ideas [--project <id>] [--status open]` lists ideas; `{{CLI}} ideas review` lists open ideas older than 90 days.
- `{{CLI}} handoff list` and `{{CLI}} handoff pull "<path>" [--project <id>]`
- `{{CLI}} log "<text>" [--project <id>]`

`{{CLI}} schema` prints the full note rules. `{{CLI}} status` lists projects and the open idea count. `{{CLI}} validate` checks everything.
