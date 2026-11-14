---
type: plan
repo: agent-vault
issue: 58
---
# agent-vault-58-plan

## Goal
Derive a branch's `fix/` type from the issue rather than from the implementing session's judgment. `vault.py branch` gives a `bug`-labelled issue a `fix/` branch and refuses any other type for it. It refuses `fix` and `hotfix` for every other issue, which still defaults to `feat/`. implement-story's wording about `--type` matches. This is story S3 of [[agent-vault-design-2-features-group-issues-as-github-sub-issues]] (its D4, record d70). #57 (S2) already added `issue create --bug`, which sets the label.

## Acceptance criteria
- AC1: For an issue labelled `bug`, `vault.py branch <repo> <issue>` with no `--type` creates, pushes and records `fix/<issue>-<slug>`. With `--type fix` it does the same. With any other offered `--type` (e.g. `feat` or `chore`) it refuses before creating or pushing any branch or touching the impl note, with a message saying the issue is labelled `bug` and so its branch is `fix/`.
- AC2: For an issue without the `bug` label, `vault.py branch` with no `--type` creates `feat/<issue>-<slug>` as before. `--type` with `chore`, `docs`, `refactor`, `test`, `perf` or `feat` works as before. `--type fix` is refused before any branch is created or pushed or the impl note is touched, with a message saying `fix/` is only for an issue labelled `bug` and that the label is added on GitHub first if the issue is a bug. `--type hotfix` is refused for every issue, bug or not, as an invalid `--type` choice (D1), also before anything is created.
- AC3: `vault.py branch --help` and the module docstring's usage line no longer show `--type feat` as the default. They show that the type comes from the `bug` label when it is left out. `hotfix` is not among the `--type` choices `branch` offers (D1).
- AC4: implement-story's branch step says the type comes from the issue's `bug` label: leave `--type` out for a bug, and pass `--type` only for a non-bug whose plan clearly calls for something other than `feat`, never `fix` or `hotfix`. The vault guide template's `branch` line matches.

## Decisions
- D1: `vault.py branch` never creates a `hotfix/` branch: `hotfix` is dropped from `branch`'s `--type` choices, while `lib.BRANCH_TYPES`, the pre-push regex and SessionStart's branch parser keep accepting it, because the issue's rules leave no issue that may get one, and a branch name that already exists must still parse and push (user decision) (lasting)
- D2: The label is read from the `labels` that `check_preflight` already returns through `github.issue_info`, because that is the issue state `branch` already fetches, so no extra GitHub call is needed.
- D3: `--type`'s argparse default becomes `None` and `cmd_branch` resolves it (`fix` for a bug, `feat` otherwise), because argparse can't tell an explicit `--type feat` from the default, and AC1 must refuse an explicit `feat` on a bug.
- D4: The type check runs after preflight and before `require_clone` and any git command, because a refusal must leave no branch and no impl-note change, and preflight's own refusals (unsealed plan, closed issue) stay first, as now.

## Implementer's discretion
- The exact wording of both refusals, within AC1 and AC2.
- Whether the resolution is a small helper beside `default_slug` or inline in `cmd_branch`.
- The exact `--help` text for `--type`.

## Context
- `agent_vault/vault.py`: `cmd_branch`, `check_preflight` (returns `info` with `labels`), `default_slug`, the module docstring's `branch` usage line, and `build_parser`'s loop that adds `branch`'s `--type` (`default="feat", choices=lib.BRANCH_TYPES`).
- `agent_vault/lib.py`: `BRANCH_TYPES`, `BRANCH_RE`, `BUG_LABEL` (its name is `bug`). `agent_vault/githooks/pre-push` and `agent_vault/hooks/session_start.py` mirror `BRANCH_TYPES` and stay unchanged (D1).
- `tests/test_github.py`: `BranchTest` (`test_type_and_slug_options` uses `--type fix` on an unlabelled issue and must change), `GhCase` setup (the fake issue #7 has `labels: []`; the repo's labels include `bug`), `self.issue()` for changing an issue's labels.
- `skills/implement-story/SKILL.md`: step 3's `vault.py branch` bullet. Its Per step commit types are commit types, not branch types, and stay as they are.
- `templates/vault-CLAUDE.md`: the `branch <repo> <issue> [--type feat] [--slug ...]` line.
- `tests/test_skills.py`: `VaultCommandsTest` checks every `vault.py` option a skill names exists.
- Design: [[agent-vault-design-2-features-group-issues-as-github-sub-issues]] (Options considered "How bugs are marked", Chosen design CLI `vault.py branch`, D4, S3). Record d70 is its D4.
- Neighbours: #49 changes what `branch` does after a refused push, a separate fix. #59 is S4.

## Steps
### 1. branch derives its type from the bug label
Files: `agent_vault/vault.py`, `tests/test_github.py`
Do: Set `branch`'s `--type` default to `None` and its choices to `lib.BRANCH_TYPES` without `hotfix` (D1, D3). In `cmd_branch`, after `check_preflight` and before `require_clone`, resolve the type from `info["labels"]` (D2, D4): a bug gets `fix` and refuses any other explicit type; any other issue refuses `fix` (`hotfix` is no longer a choice) and defaults to `feat`. Update the module docstring's usage line and `--type`'s help (AC3). Change `test_type_and_slug_options` to use a non-fix type, and add T1–T3.
Done when: T1, T2 and T3 pass and the whole suite passes.

### 2. Wording in implement-story and the vault guide
Files: `skills/implement-story/SKILL.md`, `templates/vault-CLAUDE.md`, `tests/test_skills.py`
Do: Reword implement-story's `vault.py branch` bullet and the vault guide template's `branch` line per AC4. Add T4.
Done when: T4 passes and `vault.py lint` passes.

## Tests
- T1 → AC1: after sealing, `bug` is added to fake issue #7's existing labels (it already carries `planned`), then `branch` with no `--type` creates, pushes and records `fix/7-<slug>` in the impl note. `--type fix` gives the same. `--type feat` and `--type chore` are each refused with a message naming `bug` and `fix/`, after which the current branch is still `main`, there is no `7-` branch locally or on the fake remote, and the impl note has no `branch:`.
- T2 → AC2: with #7 unlabelled, no `--type` gives `feat/7-…` (the existing `test_creates_pushes_and_records`). `--type chore --slug frob-fix` gives `chore/7-frob-fix`. `--type fix` is refused with a message naming the `bug` label, leaving no branch and no `branch:` in the impl note. `--type hotfix` exits non-zero with argparse's invalid-choice error naming `hotfix`, for #7 both unlabelled and labelled `bug`, with no branch created either time.
- T3 → AC3: `vault.py branch --help` output doesn't contain `hotfix` and mentions `bug`. The module docstring's `branch` line doesn't contain `--type feat`, still shows `--type`, and mentions `bug`.
- T4 → AC4: implement-story's branch step mentions the `bug` label, `fix` and `hotfix` (as never passed for a non-bug), and no longer says `--type` "if the plan's steps clearly call for something other than `feat`" without the label rule. `templates/vault-CLAUDE.md`'s `branch` line doesn't contain `--type feat` and mentions `bug`. `VaultCommandsTest` passes.
Full check: python -m unittest discover -s tests -t .

## Stop and ask if
- Removing `hotfix` from `--type`'s choices breaks any caller other than the tests named here.
- A test outside `BranchTest` relies on `branch --type fix` for an unlabelled issue.
- Anything other than `pre-push`, `session_start.py` and `BRANCH_RE` would need to change for old `hotfix/` branch names to keep working.

## Out of scope
- Labelling past issues as bugs, and title prefixes on issues or PRs.
- Commit message types in implement-story (they keep `fix` and `hotfix`).
- What `branch` does when its push is refused (#49).
- Re-checking the label after the branch exists (`open-pr`, `seal retro`).
