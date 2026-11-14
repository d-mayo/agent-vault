---
type: plan
repo: agent-vault
issue: 76
---
# agent-vault-76-plan

## Goal
Cut implement-story's waiting time in **Finish** and **Address PR review**, and make its results less variable, without moving review or tests out of implementation. The full diff is reviewed once. Each re-review covers only the fixes since the last review. The full check runs in parallel with the review instead of before it, and reruns after a fix only when the fix can affect it. There are at most two reviews before stopping to ask. Known test failures a repo lists in its `CLAUDE.md` stop being judged again on every run. These are judgment rules for the implementer, so they live in the skill text (`skills/implement-story/SKILL.md`, `reviewer.md`), not in `vault.py`.

## Acceptance criteria
- AC1: In **Finish**, Review 1 covers the whole diff (`git diff <base>..HEAD`) as today. Review 2 covers only the commits since Review 1 (`git diff <reviewed>..HEAD`, where `<reviewed>` is the tip Review 1 saw), and the reviewer also gets the major findings from Review 1 whose fixes it must check, and still gets the acceptance criteria, the repo `CLAUDE.md` and the findings format (`reviewer.md`'s Prompt items 2, 3 and 5). It also gets the whole diff (`git diff <base>..HEAD`) as read-only context, so it can flag a fix that breaks something elsewhere in the diff. Each `## Verification` review line records the tip it reviewed: `- Review <k>: R<n>-R<n2> (at <sha>)` or `- Review <k>: no findings (at <sha>)`, so a resumed session can scope the next re-review. `reviewer.md` describes both scopes: whole diff for the first review, and for a re-review the fix diff, checked against the earlier major findings, with the whole diff as context: it reports problems the fixes cause, anywhere in the diff, and doesn't re-raise findings about unchanged code. No commit lands after the last recorded review tip before the PR opens. If one must (e.g. a fix for a lint failure), it gets a re-review within the two-review cap, or the implementer stops and asks.
- AC2: **Finish** and **Address PR review** each allow at most two reviews. If the second still has a major finding, the implementer stops and asks the user and never starts a third. The skill's `## Stop and ask if` and `reviewer.md` say two, not three.
- AC3: Before each review, `vault.py lint` runs first and must pass, as today. Then the plan's `Full check:` and the review start at the same time (the check in the background, the reviewer subagent alongside it). If the full check fails, the review's findings are discarded: they are not recorded, and the round doesn't count. The implementer fixes the failure, then starts the check and the review again. No fix for a review's findings starts until both the check and the review have finished, so a discarded review never leaves fixes behind. After a round of fixes, the full check reruns, in parallel with the re-review, unless the fix commits change no file the full check reads. A skipped rerun is recorded under `## Verification` as `- Full check skipped after <sha>: <reason>`.
- AC4: In **Address PR review**, the first review covers all the fixes (`git diff <tip>..HEAD`, as today). A second review covers only the commits since the first, with the first review's major findings and all the PR-review fixes (`git diff <tip>..HEAD`) as context, and is recorded with `(at <sha>)` the same way. AC3's ordering and skip rule apply to it too.
- AC5: A test failure that the repo `CLAUDE.md` lists on a `Known test failures:` line under `## Gotchas` doesn't block a step's commit or a review. The implementer records it by name under `## Verification`, and doesn't judge it again. **Per step**'s rule that a failing test is never part of a step's commit gains this one exception, and says so. Any failure not listed there blocks, as today, including one that only fails under load.
- AC6: The repo `CLAUDE.md`'s `skills/implement-story/` Layout bullet says re-reviews cover only the fixes, there are at most two reviews, and known test failures come from a `Known test failures:` Gotchas line.

## Decisions
- D1: Implementation is reviewed in full once at the end. Each re-review covers only the commits since the last review, checked against that review's major findings, with the whole diff as read-only context so a fix that breaks something elsewhere is still caught. There are at most two reviews before stopping to ask, minor findings are left open and listed in the PR, and the same holds for PR-review fixes. This replaces [[agent-vault-d30-implementation-is-reviewed-once-at-the-end-three-rounds-at-m]], because a fresh reviewer re-reading the whole diff re-raises old minors (movie-nite#5) and costs a full review per round, and few runs ever needed a third round. The reviewer still reports every major finding and at most three minor ones, as under d30 (lasting)
- D2: `vault.py lint` always runs first. Then the full check and the review run in parallel, and a review whose full check failed is discarded. After fixes, the full check reruns, again in parallel, unless the fix commits change no file it reads, and a skipped rerun is recorded with its reason, because a full check can take minutes (movie-nite's lint, test and build; agent-vault's suite took about 6.5 minutes before #53 and about 65 s since) and d36 still requires lint before every review (user decision) (lasting)
- D3: A repo lists its known, expected test failures on a `Known test failures:` line under its `CLAUDE.md`'s `## Gotchas`. Only those failures are non-blocking, and each is recorded by name, because judging the same CRLF and timing failures again on every run costs time and gives varying answers. It goes in Gotchas, not Commands, because `claudemd-lint` resolves every backticked token in `## Commands` as a command (lasting)
- D4: These rules live in the skill text only, with no CLI or hook check, because which files a full check reads, or whether a failure is the listed one, is judgment, so d24 (what must always happen is enforced by code) has nothing mechanical to enforce here.
- D5: A review discarded because its full check failed is neither recorded nor counted as a round, because the diff it saw is about to change, and counting it would spend one of only two rounds on nothing.
- D6: The reviewed tip goes on the `Review <k>:` Verification line as `(at <sha>)`, because a resumed session counts rounds from those lines and needs the same lines to scope the next re-review. No code parses them.

## Implementer's discretion
- The exact wording in `SKILL.md` and `reviewer.md`, within AC1–AC5, as long as the skill doesn't grow much (aim for roughly net-zero lines in **Finish**). **Finish** keeps its seven numbered steps and **Address PR review** its five, so the ping-point test's step numbers still hold.
- Whether the frontmatter `description` changes (it names no round count today).
- How to start the check and the review together (a background Bash run and an Agent call in the same turn, or similar).
- Whether the earlier findings reach the reviewer verbatim or as their `R<n>` lines from the impl note.
- How existing phrase checks in `tests/test_skills.py` are updated to the new rules, as long as each keeps testing the behaviour it tested (lint before every review, minors left open, PR review counted separately).

## Context
- `skills/implement-story/SKILL.md`: `## Resume` step 2 (counts rounds from `Review <k>:` lines); `## Per step` step 3 (relevant tests); `## Finish` steps 1–4; `## Address PR review` step 3; `## Stop and ask if` ("The third review still has a major finding").
- `skills/implement-story/reviewer.md`: the intro ("up to three reviews"), `## Prompt` items 1 and 4, and `## Findings format`'s closing paragraph about `Review <k>:` lines.
- `tests/test_skills.py` `ImplementStoryProcedureTest`: `test_single_end_review_with_three_round_limit` (phrases "review the whole diff again", "third review still has a major finding, stop and ask", "never start a fourth review"), `test_lint_runs_with_full_check_before_every_review` (counts `` `vault.py lint` `` in Finish, lint before "Review the whole diff", "Request no review while either fails"), `test_pr_review_fixes_get_one_review`, `test_per_step_commits_without_review` (forbids "after every step"). Also the shared-section identity test and the ping-point test (`Finish` step 6, `Address PR review` step 4), which must keep passing.
- `agent_vault/claudemd.py` `check_commands`: why D3's line can't go in `## Commands`.
- `CLAUDE.md`: the `skills/implement-story/` Layout bullet.
- Records: d30 (replaced by D1), d36 (lint before every review: kept), d27 (reviewers on Opus: kept), d29 (one commit per step: kept), d24 (code vs skills: D4).
- Evidence: the vault's impl notes. About a quarter of issues needed Review 2, and movie-nite#100 and #105 needed three. #57's full check took about 6.5 minutes, before #53 cut the suite to about 65 s. movie-nite#5's Review 2 repeated the same minors. movie-nite#102, #106 and #109 re-judged the same known failures.

## Steps
### 1. Fix-only re-reviews, parallel checks and two rounds
Files: `skills/implement-story/SKILL.md`, `skills/implement-story/reviewer.md`, `tests/test_skills.py`
Do: Rewrite **Finish** steps 1–4 and **Address PR review** step 3 for AC1–AC4 (D1, D2, D5, D6): lint first, then the full check and the review in parallel, discarding a review whose check failed. Re-reviews cover only the commits since the last review, with the earlier major findings. The full check reruns after fixes unless the fix touched no file it reads, and a skip is recorded. At most two reviews. Update `## Resume` step 2 if needed so it reads `(at <sha>)`, plus `## Stop and ask if` and `reviewer.md` (intro, Prompt, Findings format). Update the affected phrase checks and add T1–T4.
Done when: T1–T4 pass and the whole suite passes.

### 2. Known test failures and the docs
Files: `skills/implement-story/SKILL.md`, `CLAUDE.md`, `tests/test_skills.py`
Do: In **Per step** step 3 and **Finish**, treat only failures listed on the repo `CLAUDE.md`'s `## Gotchas` `Known test failures:` line as non-blocking, recorded by name under `## Verification` (AC5, D3). Update the repo `CLAUDE.md`'s `skills/implement-story/` Layout bullet (AC6). Add T5 and T6.
Done when: T5 and T6 pass, the whole suite passes, and `vault.py lint` passes.

## Tests
- T1 → AC1: implement-story's **Finish** mentions "(at <sha>)", a re-review diff starting at the reviewed tip (not `<base>`), and that a commit after the last review tip needs a re-review; it no longer says "review the whole diff again". `reviewer.md` says a re-review covers only the commits since the last review, gets the earlier major findings and the whole diff as context, reports only problems the fixes cause, and still gets the acceptance criteria and the repo `CLAUDE.md`.
- T2 → AC2: **Finish** and **Address PR review** each say the second review with a major finding means stop and ask, and "never start a third review"; `## Stop and ask if` names the second review, and `reviewer.md` says "up to two reviews". Neither `SKILL.md` nor `reviewer.md` mentions "third review still has", "fourth review" or "up to three reviews".
- T3 → AC3: In **Finish**, `` `vault.py lint` `` comes before the review starts, the full check and the review are said to run in parallel (or "at the same time"), a review whose full check failed is "discarded", no fix starts until both have finished, and "Full check skipped after <sha>" appears. The existing lint-before-every-review test still passes, adjusted only as far as the new wording needs.
- T4 → AC4: **Address PR review** says its second review covers only the commits since the first, mentions "(at <sha>)", and points to the same parallel and skip rules.
- T5 → AC5: implement-story mentions "Known test failures:" and `## Gotchas`, in both **Per step** and **Finish**; says each listed failure is recorded by name under `## Verification`; and says a failure not listed there blocks, even one that only fails under load.
- T6 → AC6: The repo `CLAUDE.md`'s `skills/implement-story/` bullet mentions "Known test failures:", at most two reviews, and re-reviews covering only the fixes.
Full check: python -m unittest discover -s tests -t .

## Stop and ask if
- A rule here can't be written without contradicting an active decision record other than d30 (in particular d36's lint before every review, or d27's Opus reviewer).
- An existing test outside `ImplementStoryProcedureTest` would have to lose a check rather than be adjusted.
- Running the full check and the review in parallel isn't possible with the tools a session has (e.g. no background Bash).

## Out of scope
- Fixing the known failing tests themselves (movie-nite's CRLF tests have their own PR), and adding a `Known test failures:` line to any other repo's `CLAUDE.md`.
- Making the agent-vault suite faster, or changing the parallel test runner (#70).
- plan-story's and design's reviewers, and the retro skill.
- Any `vault.py` or hook change, including a lint check of the `Known test failures:` line.
- The repo-init skeleton (`agent_vault/templates/repo-CLAUDE.md`).
