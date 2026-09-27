# Vault guide

This vault has two sides.

- **Phone side**: everything outside `Agent/`, including `taxonomy.json`. It's my phone capture system. Read it freely; never change it. To act on a phone note, pull it in with `{{CLI}} handoff pull "<path>"`.
- **`Agent/`**: your workspace. `Daily/` (one log per day), `Projects/` (one note per project), `Notes/` (reference notes), `Intake/` (items pulled from the phone side), `Archive/` (finished projects).

The scripts live outside the vault, in `~/.claude/agent-vault/`, installed from the `d-mayo/agent-vault` repo; change them there, never in place. Code repos live outside the vault. A project note points to its repo with `repo:`.

## Rules

Hooks in `.claude/settings.json` enforce these. If a hook blocks you, do what its message says; don't work around it.

- Create notes only with `{{CLI}} new ...`, then edit the file it creates.
- A project reference is always `[[project-id]]` of a project that exists. If it's unclear which project something belongs to, ask me instead of guessing.
- Log work with `{{CLI}} log "..." --project <id>`. Past daily notes are closed.
- A project's current truth lives in its project note (`Current state`, `Next actions`, `Decisions`). Daily notes are history.

`{{CLI}} schema` prints the full note rules. `{{CLI}} status` lists projects and open intake. `{{CLI}} validate` checks everything.
