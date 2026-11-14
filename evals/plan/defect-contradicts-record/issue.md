# implement-story: faster, more consistent Finish loop

## Problem
implement-story's **Finish** loop (and the same loop in **Address PR review**) spends a long time on reviews and full checks, and its results vary between runs. The impl notes show where:

- Every re-review reads the whole diff again with a fresh reviewer, so it surfaces new or repeated minor findings instead of checking the fixes (movie-nite#5: "the same three minors again, now R2-R4"). About a quarter of issues needed a second round, and movie-nite#100 and #105 needed three.
- The `Full check:` reruns in full after every fix round, even when the fix can't affect the tests. In agent-vault the suite takes about 6.5 minutes (#57 ran it twice).
- The review waits for the full check to finish, though the two are independent.
- Known, expected test failures (movie-nite's 3 CRLF failures in `src/ci.test.ts`, `WheelPanel` timing tests under load) are re-judged on every run.
- The loop isn't followed consistently: movie-nite#104 notes "the later changes above were not reviewed again".

## Desired outcome
- A re-review (Review 2 onward, and PR review 2 onward) covers only the commits since the last review, checked against the earlier findings, not the whole diff again.
- After a fix round, the `Full check:` is rerun only when the fix touched something it covers; otherwise the impl note records why it was skipped.
- The full check and the first review can run at the same time; the review is thrown away if the check fails.
- At most two review rounds before stopping to ask the user, in both Finish and Address PR review.
- A repo's `CLAUDE.md` can list known, expected test failures, and implement-story treats exactly those as not blocking (and records them), instead of deciding each time.
- `reviewer.md` reflects the narrower re-review scope.

## Constraints
- The PR the user sees after implement-story has been reviewed at least once; review doesn't move to retro.
- Per-step relevant tests and green commits per step stay as they are.
- The shared `## Approvals, pings and the closing summary` section stays identical across skills.

## Out of scope
- Fixing the known failing tests themselves (movie-nite's CRLF tests have their own PR).
- Making the agent-vault suite itself faster.
- Changing plan-story's or design's reviewers.

## Source
session
