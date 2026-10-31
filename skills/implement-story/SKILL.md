---
name: implement-story
description: 'Take a GitHub issue with a sealed agent-vault plan from its branch to an open pull request, or address PR review comments already requested on one. Implements step by step with one commit and the relevant tests per step, then one independent review of the whole diff at the end (re-reviews cover only the fixes), keeps the impl note current, and resumes cleanly in a new session. Use for requests like "implement #9", "implement issue 9", "continue implementing agent-vault#9", or "address the review on #9".'
---

# implement-story

Turns a sealed plan into commits, review findings and an open pull request,
recording deviations and findings in the issue's impl note as it goes. The
reviewer's brief and findings format are in `reviewer.md`, next to this file.

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); it always ends in
`vault.py`, so `vault.py preflight <repo> <issue>` means "run that string
followed by `preflight <repo> <issue>`". `<repo>` is always the plain repo
name (e.g. `agent-vault`), never `owner/name`.

## Never

- Never merge a PR, and never push to `main`.
- Never edit a sealed plan. If it turns out to be wrong, stop and ask.
- Never run `install.py`.

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

## Which procedure applies

- The sealed plan's frontmatter says `pr: none`: go to **No-PR plan**.
- The impl note already has a `pr:`, or the request is about review comments
  or requested changes rather than implementing: go to **Address PR review**.
- Otherwise: go to **Start** — it reads the plan regardless of whether a
  branch already exists, and its own step 3 hands off to **Resume** for
  whatever's already committed. Never jump straight to **Resume**: it has no
  preflight check and doesn't read the plan or check out anything.

## Resolve the repo and issue

- Vault and CLI: `~/.claude/agent-vault.json`'s `vault` field is the vault
  path; the exact `vault.py` invocation is the line under "CLI after
  install" in `<vault>/CLAUDE.md`. A repo session's own startup context
  doesn't carry either.
- Repo: `git config --get remote.origin.url` in the current clone gives a
  URL such as `git@github.com:owner/name.git` or `https://github.com/owner/name`;
  strip the host and any trailing `.git` to get `owner/name`. The part after
  the `/` is `<repo>` for every command below.
- Issue: the number the user gave, or, on an existing branch, the `<n>` in
  `<type>/<n>-<slug>` (`git branch --show-current`).
- If neither gives a number, or the clone has no GitHub `origin`, stop and ask.
- `<base>`, used below: `git fetch origin <default>` (the repo's default
  branch), then `git merge-base HEAD origin/<default>`. Always this, never
  the plan's `base_sha` — by the time you implement, other sealed plans may
  already have merged past it.

## Start

1. Run `vault.py preflight <repo> <issue>`. If it fails — no sealed plan, the
   issue is closed, or the issue changed since the plan was sealed — stop and
   report the reason; don't improvise a plan of your own. If it passes but
   prints warnings (`base_sha` far behind, or commits since sealing touching
   files the plan names), read them: if the plan now rests on something no
   longer true, stop and ask instead of implementing around it.
2. Read the sealed plan (`Agent/Work/<repo>/<repo>-<issue>-plan.md`) and the
   impl note (`-impl.md`) in the vault, and the repo's `CLAUDE.md`.
3. Check whether work already started: the impl note's `branch:` field, then
   (if that's empty) a local or remote branch matching `<type>/<issue>-*`
   (`git branch --list` and `git ls-remote --heads origin`) — a branch can
   exist without being checked out, or only on GitHub in a fresh clone.
   - None found: make sure the working tree is clean, then run
     `vault.py branch <repo> <issue> [--type <type>] [--slug <slug>]`.
     The type comes from the issue's `bug` label: a bug gets `fix/`, so
     leave `--type` out; pass `--type` only for a non-bug whose plan
     clearly calls for something other than `feat`, and never `fix` or
     `hotfix`. `--slug` overrides the default slug. This checks out the new
     branch and records it in the impl note.
   - Found: fetch and check it out (`git switch <branch>`, or
     `git switch -c <branch> origin/<branch>` if it isn't local yet). If the
     impl note's `branch:` field is still empty (e.g. the branch was made by
     hand), record it there now, directly — `open-pr` needs it later and
     nothing else will set it. Then go to **Resume**.

## Resume (a step, or the whole issue, already has commits)

1. `git log --oneline <base>..HEAD` to see which steps already have a
   `<type>(<scope>): <step title> (#<issue>)` commit.
2. A step is done when its commit exists. Continue from the first step that
   has neither a commit nor a recorded deviation covering it. If every step
   is committed, go to **Finish**; its review rounds are counted from the
   impl note's `## Verification` `Review <k>:` lines, whose `(at <sha>)` is the
   tip to scope the next re-review from (`PR review <k>:` lines belong to
   **Address PR review**).

## Per step

Work through the plan's `## Steps` in order. For each step (numbered `<s>`):

1. **Implement exactly what its `Files:` and `Do:` say.** If you touch a file
   the step doesn't list, or do something it doesn't ask for, that's a
   deviation: add a line under the impl note's `## Deviations`
   (`- Step <s>: <what, and why>`) as soon as you do it, not at the end.
2. Check the step's own "Stop and ask if" conditions and the plan's
   `## Stop and ask if` section. If one applies, or the step turns out to
   rest on something that's no longer true, stop and ask the user instead of
   guessing past it.
3. Run the tests relevant to this step (not necessarily the whole suite
   yet). If any fail, fix the step before committing — a failing test is
   never part of a step's checkpoint commit.
4. Commit: `<type>(<scope>): <step title> (#<issue>)` — `<type>` is one of
   `feat fix chore docs refactor test perf hotfix`, `<scope>` is the area the
   step touches, and `<step title>` is the step's own title. One commit per
   step: never squash two steps together, never split one step across
   commits. There is no review per step; the review comes in **Finish**.

## Finish

Once every step is committed:

1. Run `vault.py lint` (`claudemd-lint`, then the repo's `Lint:` command). If
   it fails, fix it first (a lint failure in its own commit), and record what
   broke under `## Discoveries` (or as a deviation, if the fix touched files
   beyond the step that broke it). Request no review while it fails.
2. Start the plan's `Full check:` command (from its `## Tests` section) in
   the background and the review at the same time, following `reviewer.md`'s
   "Prompt" section (`git diff <base>..HEAD`, every acceptance criterion, the
   repo `CLAUDE.md`). If the full check fails, the review's findings are
   discarded: not recorded, and the round doesn't count; fix the failure
   (record it as in step 1), then run `vault.py lint` and start both again.
   Once both have finished, record the review under `## Verification` as
   `- Review <k>: R<n>-R<n2> (at <sha>)` or `- Review <k>: no findings (at <sha>)`
   (`<sha>` is the tip it reviewed), and each finding under `## Review findings`,
   numbered on from the highest existing `R<n>`:
   `- R<n> (major|minor): Review <k>: <finding> → fixed in <sha>`,
   `→ won't fix: <reason>`, or, for a minor one you're leaving, `→ left open`.
3. Only now fix every `major` finding, each fix its own commit (no amending),
   then run `vault.py lint` and start the `Full check:` and a re-review
   (`Review <k+1>`) at the same time. The re-review covers only the commits
   since the last review: `git diff <reviewed>..HEAD`, where `<reviewed>` is
   the `(at <sha>)` of the last `Review <k>:` line, with that review's major
   findings and the whole diff as context. Skip the `Full check:` rerun if the
   fix commits change no file it reads, and record
   `- Full check skipped after <sha>: <reason>` under `## Verification`. There
   are at most two reviews: if the second still has a major finding, stop and
   ask the user; never start a third review. A commit after the last review
   tip (e.g. a lint fix) gets a re-review within that cap, or stop and ask.
4. Minor findings are not fixed here unless a major fix already touches
   them; record them as `→ left open`. The reviewer reports at most three.
5. Fill in `## Verification` (each command you ran and what it showed,
   alongside the `Review <k>:` lines) and `## Discoveries` (anything worth
   the retro that isn't already a deviation or a finding). Keep both terse —
   impl notes cap at 80 body lines.
6. Write a short summary of the impl note (goal, what changed, notable
   findings — not the raw note) to a temp file *outside* the clone (e.g. your
   scratchpad directory); an untracked file inside the clone would make the
   next command's clean-tree check fail. End it with a "Left open" list of
   the minor findings still open, for the user to decide. Open the PR:
   `vault.py open-pr <repo> <issue> --body-file <file>`. `Closes #<issue>` is
   added automatically. Then send the push notification: the user reviews the
   PR and runs `retro #<issue>` next.
7. Leave the impl note `status: open`; it's sealed later, by the retro.

## No-PR plan

A plan with `pr: none` changes no repo file (`seal plan` guarantees every
step's `Files:` is `none`), so there is no branch, no commit, no diff
review and no pull request. `branch` and `open-pr` refuse such an issue.

1. Run `vault.py preflight <repo> <issue>`, as in **Start**, and read the
   plan and impl note.
2. Do each step's `Do:` in order (vault notes, GitHub work, anything the
   plan describes), keeping to the plan's "Stop and ask if" lines. After
   each step, add `- Step <n>: done` to the impl note's `## Verification`
   straight away: with no commits, that line is the resume marker. A
   resumed session continues from the first step with no such line.
3. When every step is done, run the plan's `Full check:` if it names one
   that applies, and record what you ran and what it showed under
   `## Verification`, and anything worth the retro under `## Discoveries`.
   Skip the diff review: there is no diff.
4. Leave the impl note `status: open`, and tell the user the next step is
   the retro (`retro #<issue>`), whose sealing closes the issue, and send the
   push notification naming `retro #<issue>` as the next stage.

## Address PR review

Asked to act on review comments for an issue that already has an open PR
(the impl note's `pr:`):

1. From inside the clone, on the issue's branch: read both
   `gh pr view --json reviews,comments` (review summaries and general PR
   comments) **and**
   `gh api --paginate repos/<owner>/<repo>/pulls/<pr>/comments` (inline line
   comments) — neither alone has everything that was requested.
2. For each requested change: if it conflicts with the plan or you're unsure
   how to resolve it, stop and ask the user first (see "Stop and ask if")
   rather than recording a resolution before you have one. Otherwise fix it
   in its own commit, or decline it with a reason. If one comment's fix
   touches several files or changes behaviour, tell the reviewer which
   commit to look at first; it gets no extra review.
3. Run `vault.py lint`, then start the `Full check:` and the review at the
   same time, covering all the fixes: `git diff <tip>..HEAD`, where `<tip>`
   is the branch tip before the first fix, with the request texts supplied
   (see `reviewer.md`). The same rules as **Finish** steps 1-3 apply: a review
   whose full check failed is discarded, no fix starts until both have
   finished, and the `Full check:` rerun may be skipped and recorded. Fix every
   major finding; a second review covers only the commits since the first
   (with the first review's major findings and `git diff <tip>..HEAD` as
   context), at most two reviews, then stop and ask; never start a third
   review. Record each finding under `## Review findings` as
   `- R<n> (major|minor): PR review: <finding> → fixed in <sha>`,
   `→ won't fix: <reason>` or (minor only) `→ left open`, and the review under
   `## Verification` (`- PR review <k>: R<n>-R<n2> (at <sha>)` or `- PR review <k>: no
   findings (at <sha>)`); its rounds are counted separately from **Finish**'s,
   starting again at 1.
4. Push the branch, then send the push notification: the user should
   re-review the PR next.
5. Leave the impl note open; don't reseal or reopen anything else.

## Stop and ask if

- `vault.py preflight` fails, or passes with a warning that means the plan
  now rests on something no longer true.
- Any "Stop and ask if" line in the plan applies, or a step turns out to
  rest on something that's no longer true.
- The reviewer can't be run on a stronger model (see `reviewer.md`) — don't
  silently fall back to reviewing on the implementer's own model.
- A step's `Files:`/`Do:` doesn't match what the repo actually needs, beyond
  a small, worth-recording deviation.
- The second review still has a major finding.
- A PR-review request conflicts with the plan or the acceptance criteria.
