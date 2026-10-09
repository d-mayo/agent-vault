---
name: issue
description: 'Create a GitHub issue in a registered repo from one body template: draft the title and body, ask about anything unclear, show the exact wording, and create it only after the user approves it. Use for requests like "file an issue for ...", "open an issue about ...", or "turn this idea into an issue".'
---

# issue

The one path for creating a GitHub issue. Plans treat an issue's title and
body as the only requirements, so every issue has the same body shape; the
CLI refuses a body that doesn't match it, and a guard blocks raw
`gh issue create` in registered repos.

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); it always ends in
`vault.py`, so `vault.py schema` means "run that string followed by
`schema`". `<repo>` is always the plain repo name (e.g. `agent-vault`),
never `owner/name`.

## Never

- Never run `gh issue create`; the CLI is the only way an issue gets
  created (the guard would block it anyway).
- Never edit an existing issue; that is a different request and keeps its
  own approval step in the skill that asks for it.
- Never create the issue before the user has approved the exact title and
  body you showed.
- Never change the body after it was approved: what is posted is what the
  user read.

## Approvals, pings and the closing summary

- Every yes/no approval this skill asks for is one `AskUserQuestion` prompt
  (below, "the approval prompt") with exactly three options: `Yes`, `No` and
  `Hold on`. Only `Yes` proceeds.
  - `Hold on`: stop, take up what the user wants to discuss first, and ask the
    same prompt again once that's settled.
  - `No`: change nothing, ask in prose why, and work through the reason before
    asking again.
  - A free-text answer (the prompt's own "Other" choice) that starts with a
    clear yes counts as `Yes`, unless it also asks for a change: then make the
    change and ask again, since what is approved must be what was shown. Any
    other free-text answer counts as `Hold on`.
  - Without `AskUserQuestion`, ask in prose with the same three answers; only
    an explicit Yes counts.
  - Questions that aren't approvals (interviews, clarifying questions, a
    summary's closing question) stay open-ended prose.
- When this skill's stage is finished and the next stage is the user's to
  start, send one push notification (the `PushNotification` tool): a single
  line under 200 characters naming what finished and what the user starts
  next. Send none for anything else. If the tool isn't available or doesn't
  send, carry on.
- The final response of a finished stage ends with at most three bullets, each
  one short sentence: the facts the user most needs to know, with nothing after
  them.

## Resolve the vault, CLI and repo

- Vault and CLI: `~/.claude/agent-vault.json`'s `vault` field is the vault
  path; the exact `vault.py` invocation is the line under "CLI after
  install" in `<vault>/CLAUDE.md`.
- Repo: for an idea, the repo of its `project:` (the project note's
  `repos:`; ask if the project has several, or the idea has none). Otherwise
  the current clone's `origin` (`git config --get remote.origin.url`, with
  the host and any trailing `.git` stripped, the part after the `/`) when
  it is registered; otherwise ask which repo.

## Place

Before drafting, decide where the issue belongs.

1. Run `vault.py features --project <id>` (the repo's project) and read the
   repo's open issues (`gh issue list --state open --repo <owner/name>`).
2. Suggest the feature the issue plainly belongs to (its open `<repo>#<n>`),
   or standalone, and say why. Ask the user whether it is a bug, in prose.
3. The placement becomes one flag: `--parent <ref>` under that open feature,
   `--standalone` under none, or `--bug` for a standalone issue labelled
   `bug`. A design's story (a Source of `[[<design>]] S<n>`) is never filed
   here: send the user to the design skill, which files it under its
   design's feature.

## Draft

1. Read the body template from `vault.py schema` (the "Issue body" line):
   the `##` sections, in order, none empty. Write `None` under a section
   with nothing to say.
2. Read what the issue is about: the idea note (`Agent/Ideas/<file>.md`) or
   the user's request. Fill each section from it: `Problem` (what is wrong
   or missing, and for whom), `Desired outcome` (what is true afterwards,
   observable from outside), `Constraints`, `Out of scope`, and `Source`
   (the idea as `[[<idea>]]`, `retro <repo>#<n>`, or `session`).
3. Pick a short title on one line.
4. Where something is unclear and the request or note doesn't settle it,
   ask the user open-ended questions in prose, never multiple choice, and
   fold the answers in. Don't invent requirements.

## Approval

1. Show the exact title and the exact body, as they will be posted, and
   name the placement: under feature `<repo>#<n>`, standalone, or
   standalone bug. Ask for approval of that wording and placement with the approval prompt.
2. Change nothing until the user answers; if they ask for edits, redraft
   and show it again. Only an explicit approval of the shown wording counts.

## Create

1. Write the body to a temporary file outside the vault and the working
   tree (your scratchpad directory, for example).
2. Run `vault.py issue create <repo> --title "<title>" --body-file <file>
   <placement>`, where `<placement>` is the approved flag (`--parent <ref>`,
   `--standalone` or `--bug`), adding `--idea <file>` when the issue comes from an idea (the CLI then
   marks the idea promoted and logs it). The idea must be open; if it
   has a `project:`, that project must list the repo (an idea with none
   goes into the repo you name).
3. If it refuses the body, it names each problem: fix those, show the user
   the changed wording again, and put it to the approval prompt again before retrying.
4. Report the issue URL, the last line the command prints.

## Stop and ask if

- The repo isn't registered in any project, or you can't tell which repo
  the issue belongs to.
- The idea is not open, or belongs to a project that doesn't list the repo.
- The user asks to change an existing issue instead of creating one.
- The issue is a design's story (its Source names `[[<design>]] S<n>`):
  point the user to the design skill instead.
