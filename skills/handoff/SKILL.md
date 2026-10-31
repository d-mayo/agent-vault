---
name: handoff
description: 'Run stories from issue to merged PR in one top-level Opus session, asking the user only when unsure: plans each story, has a Sonnet subagent implement it, reviews, runs the retro and lets the retro merge. Takes one issue, several issues, or a feature. Use for requests like "handoff #78", "handoff #78 #79 #80", "handoff the feature #50", or "hand these issues off".'
---

# handoff

An orchestrator for stories the user hands over entirely. One top-level Opus
session plans each story, has a Sonnet subagent implement it, runs the
independent review, writes the retro and lets `seal retro` merge. The default
path (plan-story, implement-story, retro, each with the user) is unchanged;
this skill follows their procedures with the changes below. It is not a
pipeline skill: it carries no shared approvals section, asks no yes/no
approvals, and its pings are the ones named under "Asking the user".

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); `<repo>` is the
plain repo name. Work inside the repo clone.

## Never

- Never create or edit a GitHub issue on your own. Follow-ups become ideas or
  are dropped; anything that seems to need an issue is a question for the user.
- Never merge except through `vault.py seal retro`, and never push to `main`.
- Never run two stories at once, and never run `install.py`.
- Never edit a sealed plan. If it turns out to be wrong, stop and ask.

## The run

1. **Collect the issues.** One issue, several, or a feature (an issue
   labelled `feature`).
   - A feature runs its open sub-issues that are design stories first, in the
     design's story order (`vault.py design stories <design>`), then any other
     open sub-issues in issue-number order.
   - List unfiled stories in the report and never file them: filing is the
     design skill's job.
   - Skip closed issues and issues whose retro is sealed.
2. **Upfront scan.** Read every issue in the run before the first story and ask
   the user, in open-ended prose, any questions you can already tell you will
   need. Keep the answers in the session; when planning each story, record each
   as a user decision in that story's plan. Answers live only in the session: a
   resumed run asks them again where still needed.
3. **One story at a time.** Take the stories in order, running **Plan**,
   **Implement**, **Finish**, **Retro** for one before starting the next. Plan
   the next story only after the previous one has merged, from the default
   branch `seal retro` leaves checked out. When an earlier story contradicts a
   later issue, or an answer the user gave earlier, stop and ask rather than
   guessing, and say so in the report.
4. **Resume.** Re-invoking handoff with the same issues resumes from the vault
   notes. For each issue: retro sealed or issue closed: skip; impl note has a
   `pr:`: the retro; plan sealed: implementation, the implementer resuming from
   its commits; plan still a draft: drafting continues; no plan yet: planning.
5. At the end, send the final push notification and give the combined report.

## Plan

Follow plan-story's procedure (research, draft, plan review), with these changes:

- Interview only where you aren't sure. Skip its approval summary.
- The independent plan reviewer (`plan-reviewer.md`) still runs on Opus.
- Seal the plan with `vault.py seal plan` after `vault.py validate` and the
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
  implementer, which fixes each in its own commit and runs `vault.py lint`.
- Run at most two reviews, then stop and ask.
- Fill in `## Verification` and `## Discoveries`, and add a
  `- Handed off: <run>` line under `## Verification` naming the run's issues.
- Open the PR with `vault.py open-pr`, sending no push notification for that.

## Retro

Follow the retro skill's procedure, without approval prompts or its Seal push
notification:

- Decide audit lines and rewrites yourself.
- Sort every follow-up and left-open finding into an idea or dropped, never
  into a created or amended issue. A follow-up you think must become an issue
  counts as not sure, so ask.
- Record lasting decisions and drop ideas the issue resolved without asking.
- Run `vault.py closes-feature <repo> <issue>` and name any feature that
  sealing will close in the report; `seal retro` closes it. If `seal retro`
  prints a feature-close warning, report the printed command and ask before
  running it.
- Before sealing, comment on the PR (`gh pr comment`) with a condensed report:
  decisions made without the user (plan judgment calls, lasting decisions
  recorded, retro dispositions), deviations, left-open findings and how they
  were sorted, questions asked or re-asked with their answers, and any feature
  the merge closes.
- Run `vault.py seal retro <repo> <issue>` without approval; it merges the PR.
  If the PR's checks haven't finished, wait for them (`gh pr checks <pr> --watch`)
  and retry. If a check fails, or `seal retro` refuses for any other reason,
  stop and ask.

## Asking the user

Whenever you need the user, first send a push notification (`PushNotification`)
naming what you need, then ask in open-ended prose; never a yes/no approval
prompt. Send one more push notification when the run ends. These are the only
pings: the stage-end pings of the skills followed here are skipped.

## The combined report

The run ends with one report in chat covering every story: what merged, the
decisions made without the user, deviations, left-open findings and their
sorting, questions asked, features closed, unfiled stories, and anything that
stopped the run.
