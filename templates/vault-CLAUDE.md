# Vault guide

This vault has two sides.

- **Phone side**: everything outside `Agent/`, including `taxonomy.json`. It's my phone capture system. Read it freely; never change it. To act on a phone note, pull it in with `{{CLI}} handoff pull "<path>"`; that creates an idea.
- **`Agent/`**: your workspace.
  - `Projects/<id>.md`: one overview note per project; it lists the project's GitHub repos in `repos:`.
  - `Work/<repo>/<repo>-<n>-plan.md`, `-impl.md`, `-retro.md`: one set per GitHub issue.
  - `Ideas/<date>-<slug>.md`: one idea per file.
  - `Decisions/<project>/<project>-d<n>-<slug>.md`: one decision record per lasting decision; never edited, only replaced by a later record.
  - `Designs/<project>/<project>-design-<n>-<slug>.md`: one design per larger piece of work, a draft until sealed; a sealed design is never edited, a later design extends or replaces it.
  - `Daily/<date>.md`: the log, one file per day.

The scripts live outside the vault, in `~/.claude/agent-vault/`, installed from the `d-mayo/agent-vault` repo; change them there, never in place. Code repos live outside the vault. A finished project gets `status: archived` in place.

## Rules

Hooks in `~/.claude/settings.json` enforce these. If a hook blocks you, do what its message says; don't work around it.

- Create notes only with the commands below, then edit the file they create. Daily notes are written only by `log` and the pipeline commands; never edit them.
- A project reference is always `[[project-id]]` of a project that exists. If it's unclear which project something belongs to, ask me instead of guessing.
- Never write vault files through the shell (`echo >`, `sed -i`, `Set-Content`, `mv`, …): use the CLI commands below or the edit tools, which the hooks check. Sealed notes and past daily notes are never edited; if one is wrong, tell me.
- Log work with `{{CLI}} log "..." --project <id>`. Past daily notes are closed.
- A project's current truth lives in its project note (`Purpose`, `Current state`, `Architecture`, `Standing decisions`, the last generated from the decision records). Daily notes are history.

## Commands

- `{{CLI}} new project <id> [--repo owner/name ...] [--purpose "..."]`
- `{{CLI}} new design <project> "<title>"`: the next draft design of a project (the project may not exist yet). `{{CLI}} seal design <design>`: after I approve a design; creates a decision record for each of its decisions (source `design <design> D<k>`), promotes its listed ideas, rewrites `Standing decisions` and seals it, or refuses and changes nothing. `{{CLI}} design stories <design>` lists a sealed design's stories with the issues filed for them, or "not filed".
- `{{CLI}} new plan <repo> <issue>`: a draft plan and an open impl note. `{{CLI}} new retro <repo> <issue>`: the retro, once the impl note has a `pr:`.
- `{{CLI}} seal plan <repo> <issue>`: after I approve a plan; records the issue's edit time and the base commit, adds the `planned` label and logs it. `{{CLI}} preflight <repo> <issue>` gates implementation; `{{CLI}} branch <repo> <issue> [--type feat] [--slug ...]` creates and pushes the branch; `{{CLI}} open-pr <repo> <issue> [--body-file <file>]` opens the PR with `Closes #<issue>`; `{{CLI}} stage <repo> <issue>` says where the issue stands. `branch` and `open-pr` run inside a clone of the repo.
- To file an issue, say "file an issue for ..." or "turn this idea into an issue" (the `issue` skill); it drafts the title and body in the issue template, asks about anything unclear, and creates it through `{{CLI}} issue create` only after I approve the exact wording. Never `gh issue create`: a hook blocks it in registered repos.
- To design something larger than one issue (a new project, a feature, a refactor, or several ideas combined), start a fresh session on Opus (by convention) and say "design ..." (the `design` skill); it researches first, interviews me over as many rounds as it needs, gets an independent Opus review, seals the design only once I approve the summary, then drafts one issue per story for me to approve. Ask it later to "file the stories of <design>" for any left unfiled.
- To plan an issue, start a fresh session on Opus (by convention) inside the repo clone and say "plan #<issue>" (the `plan-story` skill); it researches first, interviews me only on what research can't settle, gets an independent Opus review of the draft, and seals only once I approve the summary.
- To implement a sealed plan, start a fresh session inside the repo clone and say "implement #<issue>" (the `implement-story` skill); it stays on this session's model (Sonnet, by convention) and runs its own review on Opus.
- `{{CLI}} repo-init <path>`: one-time setup of a repo clone (squash-only merges, `planned` label, shared pre-push hook, `CLAUDE.md` skeleton, `.github/ISSUE_TEMPLATE/issue.md`; the last two are left uncommitted).
- `{{CLI}} idea add "<title>" [--project <id>] [--source ...]` and `{{CLI}} idea drop <file> --reason "..."`, `{{CLI}} idea promote <file> --body-file <file> [--title "..."] [--repo <name>]` (creates the GitHub issue from a body in the issue template; only after I approve)
- `{{CLI}} issue create <repo> --title "..." --body-file <file> [--idea <file>]`: the one way to create a GitHub issue; refuses a body that isn't the issue template (`{{CLI}} schema` lists its sections), and only after I approve the exact title and body
- `{{CLI}} decision add <project> "<title>" --decision "..." --why "..." --source "plan <repo>#<n> D<k> | design <design> D<k> | issue <repo>#<n> | retro <repo>#<n> | session" [--replaces <record>]`: with `{{CLI}} seal design <design>`, the only way to write a decision record; it also rewrites the project note's `Standing decisions` (generated, never hand-edited). `{{CLI}} decisions [--project <id>] [--all]` lists the active records (`--all`: superseded too).
- `{{CLI}} ideas [--project <id>] [--status open]` lists ideas; `{{CLI}} ideas review` lists open ideas older than 90 days.
- `{{CLI}} handoff list` and `{{CLI}} handoff pull "<path>" [--project <id>]`
- `{{CLI}} log "<text>" [--project <id>]`

`{{CLI}} lint` (run inside a clone) runs `claudemd-lint` and then the repo's declared `Lint:` command; it fails rather than skip a missing tool. `{{CLI}} schema` prints the full note rules. `{{CLI}} status` lists projects and the open idea count. `{{CLI}} validate` checks everything.
