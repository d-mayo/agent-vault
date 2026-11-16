---
name: merge
description: 'Merge the PR of a story once its retro has readied it: confirm the readied commit, ask for approval when run on its own, run `vault.py merge` (which merges with the merge style of the vault setup, and only with approvals in place if the setup requires them), then close a finished feature and bring the default branch up to date. Use for requests like "merge #9", "merge issue 9", or "merge agent-vault#9".'
---

# merge

Runs last in the pipeline, after the retro. `seal retro` readied the PR and
recorded its commit in the sealed retro; this skill merges exactly that commit
through `vault.py merge`, which merges with the vault setup's style (squash
unless the setup says otherwise), seals the impl note, closes a finished
feature, brings the default branch up to date and updates the install. There
is no reviewer subagent here: the code was reviewed and the docs audited
before the retro sealed.

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); it always ends in
`vault.py`, so `vault.py merge <repo> <issue>` means "run that string
followed by `merge <repo> <issue>`". `<repo>` is always the plain repo
name (e.g. `agent-vault`), never `owner/name`.

## Never

- Merge only through `vault.py merge`: never `gh pr merge`, never GitHub's
  merge button. Never push to `main`.
- Never update the PR's branch yourself (no merging or rebasing the default
  branch into it, no `gh pr update-branch`): that adds commits no retro
  covered, so it is the user's call.
- Never edit a sealed note. If a plan, impl note or retro turns out to be
  wrong, stop and ask.
- Never run `install.py` yourself: only `vault.py merge` (after a merge) and session start install, and only from `main`.

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

## Merge

1. Read the impl note (`Agent/Work/<repo>/<repo>-<issue>-impl.md`) and the
   issue's latest retro note (`<repo>-<issue>-retro.md`, or the highest
   `<repo>-<issue>-retro-<n>.md`) in the vault. The impl note needs a `pr:` and
   `status: open`; the retro must be `status: sealed` and carry a `commit:`
   (the commit `seal retro` readied). With no sealed retro, or a plan with
   `pr: none` (which has no PR to merge), stop and say so: the retro comes
   first, and a `pr: none` story is closed by its retro.
2. Run `vault.py closes-feature <repo> <issue>`: it says whether the merge
   will also close the issue's parent feature.
3. Compare the PR's head (`gh pr view <pr> --json headRefOid`) with the
   retro's `commit:`. If the head moved past it, commits came after the
   retro: run a follow-up retro with the retro skill on just the commits
   since that commit (it starts with `vault.py new retro <repo> <issue>`).
   Then, with `approvals: required`, stop: the approvals given earlier covered
   the old head, so tell the user to collect approvals again and run `merge
   #<issue>`, and send the push notification for that. With `approvals: none`
   carry on once that retro is sealed; its approved summary covers the merge.
   The approvals value is the one `seal retro` printed in its next step; run
   on its own, read `approvals` and `merge` from `Agent/Setup.md` in the
   vault (no note means `none` and `squash`).
4. Approval. Run straight after an approved retro, whose summary named this
   PR and the feature to close, ask nothing more. Run on its own, show a short
   summary — the PR (number and title), the readied commit, the merge style,
   whether the setup requires approvals, and the feature the merge closes (or
   none) — and ask the approval prompt. Only `Yes` continues.
5. Run `vault.py merge <repo> <issue>`. It merges only the readied commit, so
   it refuses, changing nothing, if anything moved or isn't ready: say what it
   refused over and what it says to do. Wait for checks that haven't finished
   (`gh pr checks <pr> --watch`) and run it again; when the checks haven't
   started yet, wait a moment and run it again. If GitHub refuses the merge
   because the branch must be up to date with its base, tell the user they
   must update the branch, which needs a follow-up retro and, where approvals
   are required, new approvals; never update it yourself. Any other refusal:
   report the reason and stop.
6. Once it reports the PR merged, send the push notification: the user starts
   the next issue. Report what it printed: the merge, the sealed impl note,
   any feature it closed, and the default branch brought up to date. A feature
   warning it prints stays the user's call: report the command it names.

## Stop and ask if

- The retro isn't sealed, has no `commit:`, or the impl note has no `pr:`.
- `vault.py merge` refuses and the reason isn't one this skill's steps
  already cover (a closed PR, a repo that disallows the merge style, a failed
  check, a missing approval): report it and never merge any other way.
- The PR's head moved and the story needs a follow-up retro, but the user
  hasn't asked for one.
- `vault.py merge` prints a feature-close warning: everything else is done;
  report the command it printed and run it only after the approval prompt is
  answered Yes.
