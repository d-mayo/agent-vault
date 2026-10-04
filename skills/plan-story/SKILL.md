---
name: plan-story
description: 'Turn a GitHub issue into a sealed agent-vault plan: research the issue, the codebase and neighbouring issues before asking anything, interview the user only on what research can''t settle, draft the plan in the design''s plan format, have an independent reviewer check it, then seal it only once the user approves a plain-language summary. Use for requests like "plan #9", "plan issue 9", or "draft a plan for agent-vault#9".'
---

# plan-story

Turns a GitHub issue into a sealed plan: research before asking, a focused
interview, a draft checked by an independent reviewer, a plain-language
summary, and sealing only on explicit approval. The reviewer's brief and
findings format are in `plan-reviewer.md`, next to this file.

Every `vault.py <command>` below means the exact CLI string embedded in any
command shown in the current vault's `CLAUDE.md` (the `{{CLI}}` placeholder
there is expanded to it at install time); it always ends in `vault.py`, so
`vault.py new plan <repo> <issue>` means "run that string followed by
`new plan <repo> <issue>`". `<repo>` is always the plain repo name (e.g.
`agent-vault`), never `owner/name`.

## Never

- Never write code, and never create a branch.
- Never edit a sealed plan. If it turns out to be wrong, stop and ask.
- Never post a GitHub edit before the user approves its exact wording.

## Resolve the repo and issue

- Vault and CLI: `~/.claude/agent-vault.json`'s `vault` field is the vault
  path; the exact `vault.py` invocation is embedded in any command shown in
  `<vault>/CLAUDE.md`. A repo session's own startup context doesn't carry
  either.
- Repo: `git config --get remote.origin.url` in the current clone gives a
  URL such as `git@github.com:owner/name.git` or `https://github.com/owner/name`;
  strip the host and any trailing `.git` to get `owner/name`. The part after
  the `/` is `<repo>` for every command below.
- Issue: the number the user gave. If none was given, or the clone has no
  GitHub `origin`, stop and ask.

## Research

Before asking the user anything:

1. Read the issue: `gh issue view <issue> --json title,body,comments`. Only
   the title and body are requirements; comments give background but don't
   count as requirements on their own.
2. Read the repo's other open issues (`gh issue list --state open --limit
   200`), reading the body of any that look related, to fix the scope
   boundary against work that's already planned or in flight.
3. Read the spec sections the issue cites.
4. Read the repo's `CLAUDE.md`.
5. Read the project overview note in the vault (`Agent/Projects/<id>.md`) —
   the project whose `repos:` lists this repo's `owner/name`.
6. Read the project's active decision records: `vault.py decisions
   --project <id>` lists them (`<id>` as in step 5), then read the ones
   that bear on this issue in `Agent/Decisions/<id>/`. A plan must not
   contradict one unless a decision in it replaces that record.
7. If the issue's `Source` links a design (`[[<design>]] S<n>`), read that design in
   `Agent/Designs/<project>/` (the note is sealed) and the story `S<n>`: the
   design's Problem, Chosen design and decisions are the reasoning the plan
   must stay consistent with, and the story's outcome and dependencies bound
   the issue's scope.
8. Read the code the issue will touch.

## Interview

Ask only what research couldn't settle. Write each question in plain prose,
open-ended, with no list of options, and state the fact that raised it (what
you found, and where). Give a recommendation only when you're confident, and
always with the reason for it. Batch questions, at most 4 at a time. Repeat
until nothing that would change the plan is still ambiguous.

## Draft

1. `vault.py new plan <repo> <issue>` scaffolds the draft plan and an open
   impl note.
2. Fill in the plan format: `Goal` · `Acceptance criteria` (`- AC<n>: …`) ·
   `Decisions` (`- D<n>: <decision>, because <reason>`; a decision that came
   from the user's answer in the interview ends the line with
   " (user decision)" after the reason, e.g. `- D1: <decision>, because
   <reason> (user decision)` — `lib.py`'s format only allows text after
   "because", never between the number and the colon). A decision that
   later work must keep following, beyond this issue, is lasting: end its
   line with " (lasting)", after " (user decision)" when both apply. One
   that reverses an active decision record says `replaces [[<record>]]`
   in its line and is lasting; citing a record without reversing it never
   uses that phrase. Decisions only about this issue aren't lasting ·
   `Implementer's
   discretion` · `Context` (pointers to code, not copies) · `Steps`
   (`### <n>. <title>`, each with `Files:`, `Do:`, `Done when:`) · `Tests`
   (`- T<n> → AC<m>: …`, then a `Full check:` line) · `Stop and ask if` ·
   `Out of scope`.
3. `vault.py validate` must pass with no errors or warnings before moving
   on; fix the draft and re-run until it's clean. `validate` checks the
   whole vault, so a problem it reports outside this plan's own file is
   pre-existing — investigate rather than treating it as blocking this
   draft.

## Plan review

1. Launch an independent reviewer subagent on Opus (see `plan-reviewer.md`)
   with the draft plan, the issue, and the research above.
2. The reviewer checks for: acceptance criteria not covered by any test,
   steps that can't be done as written, contradictions with the code, the
   spec, or a neighbouring issue's scope, and scope beyond the issue.
3. Fix what you can in the draft. Anything you can't fix, or chose not to,
   goes into the summary instead of being silently dropped.

## Summary

Present a plain-language summary in chat — never asking the user to read
the raw plan:

- what gets built,
- the user's decisions from the interview,
- the skill's own judgment calls, flagged for the user to check,
- the lasting decisions, each marked as new or as replacing a named
  record, so the user confirms what becomes permanent,
- what's out of scope,
- when the implementer must stop and ask,
- one pointed question about the plan's riskiest judgment call, which the
  user answers before approving.

## Approval

Run `vault.py seal plan <repo> <issue>` only after the user has answered
the summary's closing question and explicitly approved it, and only after
any approved issue-hygiene edits to the planned issue (see below) are posted — sealing records the issue's
current edit time, so an edit made afterwards would make `preflight` reject
the sealed plan later. For requested changes: edit the draft, running
another plan review (see Plan review) if the change is substantial, re-run
`vault.py validate`, summarise the change list, and ask again — never seal
against a draft the user hasn't approved.

## Issue hygiene

- Discoveries that belong to a different, unplanned issue; acceptance
  criteria that turned out impossible; needed edits to the issue being
  planned: propose the exact wording to the user before doing anything.
- Post GitHub edits only after the user approves that wording.
- A new issue is created only through `vault.py issue create`, after the
  user approves its exact title and body, written in the template that
  `vault.py schema` prints; the `issue` skill does this. Edits to existing
  issues stay as above.
- Edits to the issue being planned happen before sealing, so the sealed
  plan matches the issue it was sealed against.

## Handoff

Once sealed, tell the user how to start implementation: a fresh session on
Sonnet inside the repo clone (a new git worktree instead, if the clone is
already on another issue's branch), then "implement #<issue>" (the
`implement-story` skill). This skill never creates the branch itself.

## Stop and ask if

- Research turns up something the issue doesn't cover, or a contradiction
  with the spec, the code, or another issue's scope.
- The reviewer can't be run on Opus — don't silently fall back to
  reviewing on this session's own model.
- The user's answers still leave something ambiguous that would change the
  plan.
- A GitHub edit is needed but the user hasn't approved its exact wording.
- `vault.py new plan` refuses: a plan or impl note for the issue already
  exists (a draft from an earlier session, or an already-sealed plan), or
  `<repo>` isn't listed in any vault project's `repos:`.
