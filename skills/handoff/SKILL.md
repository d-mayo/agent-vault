---
name: handoff
description: 'Run stories from issue to merged PR in one top-level Opus session, asking the user only when unsure: plans each story, has a Sonnet subagent implement it, reviews, runs the retro and merges the PR (where the vault needs no approvals). Takes one issue, several issues, or a feature. Use for requests like "handoff #78", "handoff #78 #79 #80", "handoff the feature #50", or "hand these issues off".'
---

# handoff

An orchestrator for stories the user hands over entirely. One top-level Opus
session plans each story, has a Sonnet subagent implement it, runs the
independent review, writes the retro, which readies the PR, and merges it
through `~/.claude/agent-vault/bin/vault merge` (the merge skill's step) unless the vault requires
approvals, where the run stops at ready. The default path (plan-story,
implement-story, retro, merge, each with the user) is unchanged;
this skill follows their procedures with the changes below. It is not a
pipeline skill: it carries no shared approvals section, asks no yes/no
approvals, and its pings are the ones named under "Asking the user".

The CLI is `~/.claude/agent-vault/bin/vault`; every command below is written in full, as
`~/.claude/agent-vault/bin/vault <command>`. `<repo>` is the
plain repo name. Work inside the repo clone.

## Never

- Never create or edit a GitHub issue on your own. Follow-ups become ideas or
  are dropped; anything that seems to need an issue is a question for the user.
- Never merge except through `~/.claude/agent-vault/bin/vault merge`, and never push to `main`.
- Never run two stories at once.
- Never run `install.py` yourself: only `~/.claude/agent-vault/bin/vault merge` (after a merge) and session start install, and only from `main`.
- Never edit a sealed plan. If it turns out to be wrong, stop and ask.

## The run

1. **Collect the issues.** One issue, several, or a feature (an issue
   labelled `feature`).
   - A feature runs its open sub-issues that are design stories first, in the
     design's story order (`~/.claude/agent-vault/bin/vault design stories <design>`), then any other
     open sub-issues in issue-number order.
   - List unfiled stories in the report and never file them: filing is the
     design skill's job.
   - Skip closed issues and issues whose impl note is sealed (merged).
2. **Upfront scan.** Read every issue in the run before the first story and ask
   the user, in open-ended prose, any questions you can already tell you will
   need. Keep the answers in the session; when planning each story, record each
   as a user decision in that story's plan. Answers live only in the session: a
   resumed run asks them again where still needed.
3. **One story at a time.** Take the stories in order, running **Plan**,
   **Implement**, **Finish**, **Retro**, **Merge** for one before starting the
   next. Plan the next story only after the previous one has merged, from the
   default branch `~/.claude/agent-vault/bin/vault merge` leaves checked out: a later story may build
   on it. Where the vault requires approvals the run stops when its current
   story is ready (see **Merge**), so the stories not started wait for it.
   When an earlier story contradicts a
   later issue, or an answer the user gave earlier, stop and ask rather than
   guessing, and say so in the report.
4. **Resume.** Re-invoking handoff with the same issues resumes from the vault
   notes. For each issue: impl note sealed or issue closed: skip; retro sealed,
   impl note open: with approvals `none`, merge; with `required`, the run stops
   there until it has merged; impl note has a `pr:` and no sealed retro: the
   retro; plan sealed: implementation, the implementer resuming from its
   commits; plan still a draft: drafting continues; no plan yet: planning.
   Rerunning handoff once the user has merged resumes with the next story.
5. At the end, send the final push notification and give the combined report.

## Plan

Follow plan-story's procedure (research, draft, plan review), with these changes:

- Interview only where you aren't sure. Skip its approval summary.
- The independent plan reviewer (`plan-reviewer.md`) still runs on Opus.
- Seal the plan with `~/.claude/agent-vault/bin/vault seal plan` after `~/.claude/agent-vault/bin/vault validate` and the
  review pass, without asking for approval.
- Skip plan-story's Handoff push notification.
- Never edit or create a GitHub issue on your own; where plan-story would, ask
  the user instead.
- If the story turns out to need `pr: none`, stop and ask the user instead of
  sealing.

## Implement

Launch a subagent (the Agent tool) with an explicit `model: "sonnet"`, telling
it to follow implement-story's Steps only route for that issue. If the
subagent's model can't be chosen, stop and ask.

When the implementer returns a stop-and-ask question, answer it if you are sure
and otherwise ask the user. Either way resume the same implementer with the
answer (`SendMessage`). Only if that implementer can't be resumed, start a new
one on the same route, which picks up from the commits.

## Finish

Once the implementer reports every step committed and lint passing, run
implement-story's Finish yourself, except that every code change goes to the
implementer:

- Run the `Full check:` and the Opus review in parallel, following
  `reviewer.md`, and record the `Review <k>:` and `R<n>` lines in the impl note.
- Send major findings, `Full check:` failures and lint failures back to the same
  implementer, which fixes each in its own commit and runs `~/.claude/agent-vault/bin/vault lint`.
- Run at most two reviews, then stop and ask.
- Fill in `## Verification` and `## Discoveries`, and add a
  `- Handed off: <run>` line under `## Verification` naming the run's issues.
- Open the PR with `~/.claude/agent-vault/bin/vault open-pr`, sending no push notification for that.

## Retro

Follow the retro skill's procedure, without approval prompts or its Seal push
notification:

- Decide audit lines and rewrites yourself.
- Sort every follow-up and left-open finding into an idea or dropped, never
  into a created or amended issue. A follow-up you think must become an issue
  counts as not sure, so ask.
- Record lasting decisions and drop ideas the issue resolved without asking.
- Run `~/.claude/agent-vault/bin/vault closes-feature <repo> <issue>` and name any feature that
  the merge will close in the report; `~/.claude/agent-vault/bin/vault merge` closes it. If it
  prints a feature-close warning, report the printed command and ask before
  running it.
- Before sealing, comment on the PR (`gh pr comment`) with a condensed report:
  decisions made without the user (plan judgment calls, lasting decisions
  recorded, retro dispositions), deviations, left-open findings and how they
  were sorted, questions asked or re-asked with their answers, and any feature
  the merge closes.
- Run `~/.claude/agent-vault/bin/vault seal retro <repo> <issue>` without approval; it readies the PR
  (the bump pushed, the commit recorded in the retro) and merges nothing. If
  the PR's checks haven't finished, wait for them (`gh pr checks <pr> --watch`)
  and retry. If a check fails, or `seal retro` refuses for any other reason,
  stop and ask.

## Merge

Learn the vault's approvals setting from the next step `seal retro` printed:
never read `Agent/Setup.md` yourself.

- `run ~/.claude/agent-vault/bin/vault merge` (approvals `none`): run `~/.claude/agent-vault/bin/vault merge <repo> <issue>`
  right away, without asking; merging is part of the handoff. If it refuses
  over checks that haven't finished or haven't started, wait
  (`gh pr checks <pr> --watch`) and run it again. If GitHub says the branch must
  be up to date, or it refuses for any other reason, stop and ask: never update
  the branch yourself.
- `collect the approvals, then run` (approvals `required`): the run stops
  here for this story. The PR is ready (retro sealed, PR open, the handoff
  comment on it), so send the push notification asking the user to collect
  approvals and run `merge #<issue>`, and end with the combined report, naming
  the stories not started. The next story is planned only after this one has
  merged, so none of them is touched. Rerunning handoff once the user has
  merged resumes with the next story.

## Jira setup

Run `~/.claude/agent-vault/bin/vault tracker <repo>` first. If it prints `tracker: github`, ignore this
section. If it prints `tracker: jira`, the issues are Jira tickets identified by
their keys (e.g. `PROJ-12`), and everything above applies with these changes:

- Learn the connector, the issue types, the planned label and the ticket file's
  fields from `~/.claude/agent-vault/bin/vault tracker <repo>`; never read `Agent/Setup.md` yourself.
- Fetch each ticket through the connector and write the ticket file (JSON with
  the fields `tracker` names) in a scratchpad or temp folder outside both the
  clone and the vault; fetch it again before each command that takes
  `--ticket`. The planning, implementing (the Sonnet subagent gets the same
  instructions) and `open-pr` steps pass it as implement-story's and
  plan-story's Jira sections say.
- "Handoff the feature <KEY>" expands from the ticket map: the feature's key
  is recorded there with its design, so run `~/.claude/agent-vault/bin/vault design stories <design>`
  for the design's stories with their keys and repos, in story order, and run
  the filed ones. List the unfiled stories in the report and never file them.
  Keys filed under the feature but outside the design's stories run last.
- Skip a story `~/.claude/agent-vault/bin/vault stage <repo> <KEY>` reports `done` (a sealed impl note
  or a merged PR carrying the key): never judge it from the ticket's status.
  `stage` needs no ticket file, and never calls Jira.
- A Jira setup closes no feature (`~/.claude/agent-vault/bin/vault closes-feature` says so): report
  that the user closes the feature's ticket where the GitHub path would name
  the feature the merge closes.
- Never create, edit or change the status of a ticket (no transition, no
  "Done"); the only things agent-vault posts are the planned label and the
  retro's closing comment, through the skills that do so.

## Asking the user

Whenever you need the user, first send a push notification (`PushNotification`)
naming what you need, then ask in open-ended prose; never a yes/no approval
prompt. Send one more push notification when the run ends. These are the only
pings: the stage-end pings of the skills followed here are skipped.

## The combined report

The run ends with one report in chat covering every story: what merged, the
decisions made without the user, deviations, left-open findings and their
sorting, questions asked, features closed, unfiled stories, the stories not
started and why, and anything that stopped the run.
