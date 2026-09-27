---
name: implement-story
description: 'Take a GitHub issue with a sealed agent-vault plan from its branch to an open pull request, or address PR review comments already requested on one. Implements step by step with an independent review after every step, runs the relevant tests, keeps the impl note current, and resumes cleanly in a new session. Use for requests like "implement #9", "implement issue 9", "continue implementing agent-vault#9", or "address the review on #9".'
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

## Which procedure applies

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
     `vault.py branch <repo> <issue> [--type <type>] [--slug <slug>]`
     (`--type` if the plan's steps clearly call for something other than
     `feat`, `--slug` to override the default slug). This checks out the new
     branch and records it in the impl note.
   - Found: fetch and check it out (`git switch <branch>`, or
     `git switch -c <branch> origin/<branch>` if it isn't local yet). If the
     impl note's `branch:` field is still empty (e.g. the branch was made by
     hand), record it there now, directly — `open-pr` needs it later and
     nothing else will set it. Then go to **Resume**.

## Resume (a step, or the whole issue, already has commits)

1. `git log --oneline <base>..HEAD` to see which steps already have a
   `<type>(<scope>): <step title> (#<issue>)` commit.
2. Cross-check against the impl note's `## Verification`: a step needs its
   review run (or re-run) unless a `Step <s> reviewed: …` line already
   covers it (see **Per step**) — a commit alone doesn't mean the review
   happened.
3. Continue from the first step that has neither a commit nor a recorded
   deviation covering it.

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
   commits.
5. Review the step: follow `reviewer.md`'s "Prompt" section exactly — it
   lists everything to send the reviewer and the format it must answer in.
   Diff to send: `git diff <prev-step-commit>..HEAD` (`<base>`, for step 1).
6. Fix every `major` finding before moving to the next step, and a `minor`
   one now if it's quick (leave it otherwise). Amend the step's commit for
   each fix, so the diff a later review sees is still exactly this step's
   change. Once you're done fixing for this step, record the outcome in the
   impl note — write the resolution against the commit's *final* sha, not an
   intermediate one an earlier amend already replaced:
   - Under `## Verification`, note that the step was reviewed, e.g.
     `- Step <s> reviewed: no findings` or `- Step <s> reviewed: R<r>-R<r2>`
     — **Resume** relies on this line existing to know the review ran, since
     a clean review adds nothing to `## Review findings`.
   - For every finding, add a line under `## Review findings`, numbered on
     from the highest existing `R<n>`:
     `- R<n> (major|minor): Step <s>: <finding> → fixed in <sha>` or
     `→ won't fix: <reason>`.

## Finish

Once every step is committed and reviewed:

1. Run the plan's `Full check:` command, from its `## Tests` section. If it
   fails, fix it before opening the PR, and record what broke under
   `## Discoveries` (or as a deviation, if the fix touched files beyond the
   step that broke it).
2. Review the whole PR diff the same way as a step (`git diff <base>..HEAD`,
   following `reviewer.md`'s "Prompt" section), against every acceptance
   criterion and the repo `CLAUDE.md`, not only the last step. There's no
   step to amend here: fix findings with their own commit(s). Record them
   tagged `Final review:` instead of a step number (e.g. `- R<n>
   (major|minor): Final review: <finding> → …`), and note the review itself
   under `## Verification` (`- Final review: no findings` or
   `- Final review: R<n>-R<n2>`). If you fixed anything, re-run the plan's
   `Full check:` again before opening the PR.
3. Fill in the impl note's `## Verification` (each command you ran and what
   it showed, alongside the per-step review lines already there) and
   `## Discoveries` (anything worth the retro that isn't already a deviation
   or a finding). Keep both terse — impl notes cap at 80 body lines.
4. Write a short summary of the impl note (goal, what changed, notable
   findings — not the raw note) to a temp file *outside* the clone (e.g. your
   scratchpad directory); an untracked file inside the clone would make the
   next command's clean-tree check fail. Open the PR:
   `vault.py open-pr <repo> <issue> --body-file <file>`. `Closes #<issue>` is
   added automatically.
5. Leave the impl note `status: open`; it's sealed later, by the retro.

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
   with the same discipline as a step (its own commit, its own review
   following `reviewer.md`) or decline it with a reason, then add a line
   under the impl note's `## Review findings`:
   `- R<n> (major|minor): PR review: <what was asked> → fixed in <sha>` or
   `→ won't fix: <reason>` — major if it blocks the change, minor otherwise.
   Note the review under `## Verification` too (`- PR review reviewed: no
   findings` or `- PR review: R<n>-R<n2>`).
3. Push the branch.
4. Leave the impl note open; don't reseal or reopen anything else.

## Stop and ask if

- `vault.py preflight` fails, or passes with a warning that means the plan
  now rests on something no longer true.
- Any "Stop and ask if" line in the plan applies, or a step turns out to
  rest on something that's no longer true.
- The reviewer can't be run on a stronger model (see `reviewer.md`) — don't
  silently fall back to reviewing on the implementer's own model.
- A step's `Files:`/`Do:` doesn't match what the repo actually needs, beyond
  a small, worth-recording deviation.
- A PR-review request conflicts with the plan or the acceptance criteria.
