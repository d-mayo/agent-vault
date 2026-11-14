<!-- [[agent-vault-d1-every-note-rule-is-a-named-entry-in-lib-py-s-rule-table-mess]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#2 D2
---
# Every note rule is a named entry in `lib.py`'s rule table; messages carry the rule ID and `schema` prints the table

## Decision
Each note rule is a named entry (ID plus description) in the rule table in `lib.py`, and problem messages carry the ID, so `schema` prints the same table.

## Why
Rule coverage can then be checked mechanically, and `schema` can print the table the validator itself uses.

<!-- [[agent-vault-d2-validate-fails-only-on-errors-warnings-never-block]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#2 D1
---
# `validate` fails only on errors; warnings never block

## Decision
The validator returns errors and warnings separately, and `validate` exits 1 only on errors.

## Why
The 400-line plan rule is a warning by spec.

<!-- [[agent-vault-d3-plan-completeness-checks-warn-on-drafts-and-error-on-sealed]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#3 D1
---
# Plan completeness checks warn on drafts and error on sealed plans

## Decision
The plan completeness rules (PLAN-AC minimum, PLAN-STEP minimum, PLAN-T minimum, PLAN-COVER, PLAN-FULL) are warnings on draft plans and errors on sealed ones.

## Why
`new plan` must produce a valid blank draft, and drafting shouldn't trip the post-edit hook.

<!-- [[agent-vault-d4-a-repo-s-name-is-its-identity-and-is-unique-across-projects]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#2 D4
---
# A repo's name is its identity and is unique across projects

## Decision
`repos:` entries are `owner/name`, and the repo name is the identity used for `Work/<repo>/`, for the `repo:` field of plan, impl and retro notes, and for uniqueness across projects.

## Why
The design uses the name for folders and says repo names are unique across projects.

<!-- [[agent-vault-d5-a-project-may-have-no-repos-so-non-code-projects-fit]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#2 D5
---
# A project may have no repos, so non-code projects fit

## Decision
A project may have `repos: []`.

## Why
The vault should still hold non-code projects.

<!-- [[agent-vault-d6-validate-checks-a-note-s-own-shape-checks-against-real-files]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#2 D11
---
# `validate` checks a note's own shape; checks against real files belong to the sealing commands and immutability to the guard

## Decision
`validate` checks only the shape of a note (for example the syntax of retro audit lines); checking audit lines against real headings is left to `seal retro`, and keeping sealed notes unchanged to the guard.

## Why
Coverage against real headings is the sealing command's job and immutability is the guard's job, so each check lives in one place.

<!-- [[agent-vault-d7-daily-notes-are-written-only-by-the-cli-and-sessionstart-and]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#5 D7
---
# Daily notes are written only by the CLI and SessionStart and are never edited

## Decision
Daily notes are created and written only by the CLI (`log` and the pipeline commands) and SessionStart, never edited; the guard blocks them with no exceptions, and corrections are made by the user in Obsidian.

## Why
The design makes daily notes script-written, and `new daily` is not a user command because dailies are written as a side effect.

<!-- [[agent-vault-d8-the-issue-changed-means-a-title-or-body-edit-after-sealing-n]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#4 D1
---
# "The issue changed" means a title or body edit after sealing, never `updatedAt`

## Decision
An issue counts as changed only when its title or body was edited after sealing, read from GitHub's edit history (body `lastEditedAt`, last title-rename event); `issue_updated` stores that edit time.

## Why
Comments, labels and cross-references bump `updatedAt`, and sealing itself adds a label.

<!-- [[agent-vault-d9-all-hooks-and-the-cli-permission-live-in-user-level-settings]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#5 D1
---
# All hooks and the CLI permission live in user-level settings

## Decision
The guard, PostToolUse and Stop hooks and the CLI permission all live in user-level settings, not in the vault's.

## Why
Vault-level copies would run twice in vault sessions, and repo sessions must be able to run `vault.py log`.

<!-- [[agent-vault-d10-a-session-s-kind-comes-from-its-working-folder-the-guard-and]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#5 D5
---
# A session's kind comes from its working folder; the guard and post-edit hooks decide by the target path

## Decision
A session is a vault session inside the vault, a repo session inside a clone whose `origin` is a registered repo, and otherwise neither; the guard and post-edit rules depend on the target path, not the session kind.

## Why
The vault can be edited from any session.

<!-- [[agent-vault-d11-shell-writes-are-caught-after-the-fact-by-fingerprints-plus]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#5 D2
---
# Shell writes are caught after the fact by fingerprints plus the written rule

## Decision
Writes through the shell are caught after the fact by content fingerprints of sealed and closed notes (the Stop hook compares them) plus the rule in the vault's `CLAUDE.md`.

## Why
The guard only sees the edit tools.

<!-- [[agent-vault-d12-sessionstart-s-github-calls-are-time-boxed-and-never-delay-a]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#5 D4
---
# SessionStart's GitHub calls are time-boxed and never delay a session

## Decision
SessionStart gets a repo's stage from `stage` with a 5-second limit.

## Why
A slow or offline GitHub must never delay a session noticeably.

<!-- [[agent-vault-d13-the-command-guard-covers-powershell-as-well-as-bash]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#27 D2
---
# The command guard covers PowerShell as well as Bash

## Decision
The command guard hook is registered for the PowerShell tool as well as Bash.

## Why
Sessions on Windows have both tools, and this block has no second layer behind it the way pushes have the pre-push hook.

<!-- [[agent-vault-d14-the-command-guard-allows-what-it-can-t-parse-the-pre-push-ho]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#27 D13
---
# The command guard allows what it can't parse; the pre-push hook is the second layer for pushes

## Decision
The guard tokenizes commands as Bash would and allows a command it can't parse (PowerShell-only syntax included); pushes get a second layer from the pre-push hook.

## Why
The guard already allows anything it can't parse, and a PowerShell parser is a separate piece of work.

<!-- [[agent-vault-d15-git-uses-https-with-gh-as-the-credential-helper]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: session
---
# Git uses HTTPS with `gh` as the credential helper

## Decision
Git remotes use HTTPS, with `gh` as the credential helper.

## Why
The SSH key `id_ed25519` is a deploy key scoped to macro-tracking-app.

<!-- [[agent-vault-d16-other-repos-share-the-installed-pre-push-hook-through-core-h]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#4 D3
---
# Other repos share the installed pre-push hook through `core.hooksPath`

## Decision
Other repos get the pre-push hook from the shared installed copy via `core.hooksPath`; `repo-init` accepts an existing hooks path only if its `pre-push` is agent-vault's, recognised by its marker comment.

## Why
Nothing then needs committing and there is one copy to update.

<!-- [[agent-vault-d17-branch-pushes-a-new-branch-immediately]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#4 D2
---
# `branch` pushes a new branch immediately

## Decision
`branch` pushes the new branch as soon as it creates it.

## Why
`stage` must show "in progress" from any device.

<!-- [[agent-vault-d18-commands-that-need-a-clone-run-inside-it-and-check-origin-cl]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#4 D5
---
# Commands that need a clone run inside it and check `origin`; clone paths never go in the vault

## Decision
Commands that need a local clone (`branch`, `open-pr`) run inside it and check that `origin` matches the repo's `owner/name`; clone paths are never stored in the vault.

## Why
Clone paths differ per machine and don't belong in the synced vault.

<!-- [[agent-vault-d19-a-retro-refuses-to-seal-an-open-issue-whose-pr-won-t-close-i]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: issue agent-vault#35
---
# A retro refuses to seal an open issue whose PR won't close it on merge, judged by GitHub's own closing references, never the description

## Decision
`seal retro` refuses to seal an open issue unless its PR will close the issue on merge, judged by GitHub's own closing references (`closingIssuesReferences`), never by parsing the description.

## Why
A rewritten PR description once dropped the `Closes` line and left #30 open unnoticed, and GitHub's own answer also counts issues linked by hand.

<!-- [[agent-vault-d20-cli-commands-never-ask-for-approval-the-calling-skill-does]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#4 D9
---
# CLI commands never ask for approval; the calling skill does

## Decision
CLI commands such as `idea promote` never ask for approval themselves; they run only when told, and the calling skill obtains approval.

## Why
Approval is the calling skill's job.

<!-- [[agent-vault-d21-anything-posted-to-github-needs-the-user-s-approval-of-the-e]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#7 D4
---
# Anything posted to GitHub needs the user's approval of the exact wording first

## Decision
GitHub edits are posted only after the user approves the exact wording.

## Why
They publish under the user's name.

<!-- [[agent-vault-d22-issues-are-created-only-through-issue-create-which-never-cha]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: issue agent-vault#27
---
# Issues are created only through `issue create`, which never changes the approved body

## Decision
Issues are created only through `vault.py issue create`, after the user approves the exact title and body; the command refuses a body that doesn't match the issue template and never changes the body it is given.

## Why
Plans treat an issue's title and body as the only requirements, so every issue needs one shape, and what is posted must be what the user approved.

<!-- [[agent-vault-d23-a-discovery-that-changes-an-existing-unplanned-issue-goes-in]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: session
---
# A discovery that changes an existing, unplanned issue goes into that issue: GitHub holds what, the vault holds how

## Decision
A discovery that changes an existing, unplanned issue goes into that issue, not into the vault.

## Why
GitHub holds what is to be done and the vault holds how.

<!-- [[agent-vault-d24-anything-that-must-always-happen-is-enforced-by-code-skills]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#8 D4
---
# Anything that must always happen is enforced by code; skills hold judgment only

## Decision
Mechanical checks stay in `vault.py` and the hooks (`preflight`, `branch`, `open-pr`, `seal`); skills hold judgment only.

## Why
The design puts guarantees in code.

<!-- [[agent-vault-d25-the-user-approves-plans-and-retros-from-a-plain-language-sum]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#7 D5
---
# The user approves plans and retros from a plain-language summary, never the raw note, and nothing seals without that approval

## Decision
Plans and retros are put to the user as a plain-language summary instead of the raw note, and nothing is sealed without the user's explicit approval.

## Why
The user doesn't read raw plan notes, and sealing is permanent.

<!-- [[agent-vault-d26-an-independent-reviewer-checks-every-plan-draft-before-the-u]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#7 D1
---
# An independent reviewer checks every plan draft before the user sees it

## Decision
An independent plan reviewer checks every draft before the user sees the summary.

## Why
Plan mistakes flow into implementation and reviews, where they cost the most.

<!-- [[agent-vault-d27-reviewers-are-independent-subagents-on-opus-and-never-fall-b]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#8 D2
---
# Reviewers are independent subagents on Opus and never fall back to the session's model; planning runs on Opus and implementing on Sonnet by convention

## Decision
The plan reviewer and the implementation reviewer are independent subagents on Opus and never fall back to the session's own model; planning sessions use Opus and implementation sessions Sonnet by convention, and no skill switches the session's model.

## Why
Judgment is where the stronger model pays off.

<!-- [[agent-vault-d28-issues-are-implemented-in-a-fresh-session-in-the-repo-from-t]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: session
---
# Issues are implemented in a fresh session in the repo, from the sealed plan

## Decision
An issue is implemented in a fresh session started inside the repo clone, from its sealed plan.

## Why
The sealed plan is the implementer's whole input, so the session starts from it rather than from the planning conversation.

<!-- [[agent-vault-d29-each-plan-step-is-one-commit-so-a-resumed-session-has-a-chec]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#8 D3
---
# Each plan step is one commit, so a resumed session has a checkpoint

## Decision
Each plan step is committed once, with the step's own title, never squashed with another or split across commits.

## Why
A resumed session finds a step done when its commit exists.

<!-- [[agent-vault-d30-implementation-is-reviewed-once-at-the-end-three-rounds-at-m]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: issue agent-vault#30
---
# Implementation is reviewed once at the end, three rounds at most, with minor findings left open and listed; this replaces #8's review after every step

## Decision
implement-story reviews the whole diff once after the last step, repeating after fixes for at most three reviews and then stopping to ask; the reviewer reports every major finding and at most three minor ones, which are left open and listed in the PR for the user. This replaces the review after every step.

## Why
Reviewing after every step produced 77 findings across #7-#9, 9 of them major, and the final review still raised new findings after every step had been reviewed; nearly every minor was fixed and each fix grew the skills.

<!-- [[agent-vault-d31-planning-interviews-are-open-ended-prose-never-option-lists]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: issue agent-vault#30
---
# Planning interviews are open-ended prose, never option lists

## Decision
plan-story's interview asks plain-prose questions, each stating the fact that raised it, with no option lists; a recommendation is given only when the skill is confident, and always with its reason.

## Why
Options with a recommended one first let the user click through without engaging.

<!-- [[agent-vault-d32-every-retro-audits-the-readme-and-seal-retro-enforces-it]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#9 D3
---
# Every retro audits the README, and `seal retro` enforces it

## Decision
Every retro audits the README in a required section, and `seal retro` enforces it.

## Why
The README went stale in #5 with nothing watching it.

<!-- [[agent-vault-d33-the-retro-audit-turns-restated-facts-into-pointers-to-their]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#9 D4
---
# The retro audit turns restated facts into pointers to their real home

## Decision
The retro audit asks of every section whether it restates a fact whose home is elsewhere, and rewrites it into a pointer to that home.

## Why
Copies are how "where hooks live" drifted in #5.

<!-- [[agent-vault-d34-a-backfill-retro-runs-on-a-branch-that-s-already-open-never]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#9 D1
---
# A backfill retro runs on a branch that's already open, never a new branch or PR

## Decision
A closed issue's retro is sealed on whatever non-default branch is already checked out, and never on a new branch or PR.

## Why
The merged issues' branches were deleted, and recreating them would mean a PR each.

<!-- [[agent-vault-d35-a-claude-md-section-s-staleness-is-a-warning-cleared-by-veri]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#6 D1
---
# A `CLAUDE.md` section's staleness is a warning cleared by `verified:` dates that `seal retro` bumps; broken paths and commands are failures

## Decision
A section's staleness is a warning, cleared by a `verified:` date in its covers comment that `seal retro` bumps, committing and pushing the bump; broken paths, commands, globs and the line cap are failures.

## Why
Confirming a section doesn't edit it, so git alone would keep it stale forever, while staleness is expected between retros and the retro resolves it.

<!-- [[agent-vault-d36-every-registered-repo-declares-one-lint-command-run-before-e]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: issue agent-vault#33
---
# Every registered repo declares one `Lint:` command, run before every review and on every push; a missing tool fails

## Decision
Every registered repo declares one lint command on a `Lint:` line in its `CLAUDE.md`; `vault.py lint` runs it after `claudemd-lint` before each review and on every branch push, and a missing tool fails the lint rather than skipping.

## Why
Nothing ran a linter, mechanical mistakes were caught only if the reviewer noticed, and a skipped lint would let them through unnoticed.

<!-- [[agent-vault-d37-this-repo-lints-with-ruff-rules-pinned-to-e4-e7-e9-and-f-wit]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#33 D1
---
# This repo lints with `ruff`, rules pinned to E4, E7, E9 and F, with `ruff` required on PATH

## Decision
This repo's `Lint:` command is `ruff check .`, with `ruff.toml` selecting exactly E4, E7, E9 and F for Python 3.9, and `ruff` must be on `PATH` with no `python -m ruff` fallback.

## Why
A pinned rule list means a ruff upgrade can't change what blocks a push, and the issue fixes the command as `ruff check .`.

<!-- [[agent-vault-d38-the-installer-records-its-python-in-agent-vault-json-and-the]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#33 D3
---
# The installer records its Python in `agent-vault.json`, and the hooks and the git hook read it from there

## Decision
The installer writes a `python` field (its own interpreter, forward slashes) to `agent-vault.json`, and the Claude hooks and the pre-push hook read it from there.

## Why
One shared hook file can't carry a baked-in path.

<!-- [[agent-vault-d39-every-review-finding-left-open-is-sorted-by-the-retro-into-a]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: issue agent-vault#32
---
# Every review finding left open is sorted by the retro into an issue, an idea or dropped, and `seal retro` refuses otherwise

## Decision
Every `left open` review finding in the impl note is sorted by the retro into exactly one of an issue, an idea or dropped with a reason, named by `(R<n>)` on its follow-up line, and `seal retro` refuses while one is unsorted.

## Why
A left-open finding is neither a discovery nor an audit point, so a retro could otherwise seal with nobody deciding what happens to it.

<!-- [[agent-vault-d40-decision-records-live-in-agent-decisions-project-and-are-nam]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D1
---
# Decision records live in `Agent/Decisions/<project>/` and are named `<project>-d<n>-<slug>.md`

## Decision
Records live in `Agent/Decisions/<project>/`, named `<project>-d<n>-<slug>.md`, with `n` counting up per project.

## Why
The folder mirrors `Work/<repo>/`, the project prefix keeps file names unique across the vault so links resolve to one note, and the number gives each record a short stable name.

<!-- [[agent-vault-d41-a-decision-record-is-only-ever-changed-by-being-superseded]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D2
---
# A decision record is only ever changed by being superseded

## Decision
Superseding is the only change a record goes through. The CLI sets `status: active` and `superseded_by` on the old record and never touches its body.

## Why
A record has to be trustworthy later.

<!-- [[agent-vault-d42-the-stop-hook-s-fingerprint-of-a-record-leaves-out-only-its]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D3
---
# The Stop hook's fingerprint of a record leaves out only its `status` and `superseded_by` lines

## Decision
The fingerprint of a record, in both `fingerprints` and `changed_fingerprints`, leaves out only its `status` and `superseded_by` lines.

## Why
The CLI legitimately changes those two when superseding, and every other change to a record is a forbidden edit.

<!-- [[agent-vault-d44-the-overview-s-standing-decisions-is-written-only-by-the-cli]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D6
---
# The overview's Standing decisions is written only by the CLI and checked by `validate`

## Decision
The project overview's "Standing decisions" section is written only by the CLI and checked by `validate` against the records.

## Why
The user no longer maintains it by hand, and a check keeps a hand edit from drifting it.

<!-- [[agent-vault-d45-the-standing-decisions-lines-don-t-count-toward-the-overview]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D7
---
# The Standing decisions lines don't count toward the overview's 60-line cap

## Decision
The lines under "Standing decisions" are left out of the overview's 60-line cap.

## Why
The cap exists to keep hand-written prose short, a generated list can't sprawl or go stale, and about 39 links would otherwise leave no room for the next record (user decision).

<!-- [[agent-vault-d46-a-lasting-plan-decision-is-marked-by-lasting-at-the-very-end]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D8
---
# A lasting plan decision is marked by ` (lasting)` at the very end of its line

## Decision
A lasting decision ends its plan line with ` (lasting)`, after ` (user decision)` when both apply.

## Why
The plan decision line format only allows text after "because", and a fixed suffix is easy to parse.

<!-- [[agent-vault-d47-seal-retro-finds-a-lasting-decision-s-record-by-its-source-a]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D9
---
# `seal retro` finds a lasting decision's record by its source, and `decision add` refuses a second record with the same plan source

## Decision
`seal retro` looks up each lasting plan decision's record by its source `plan <repo>#<n> D<k>`, and `decision add` refuses a second record with the same plan source.

## Why
That ties the two together without listing every record under the retro's 40-line cap, and a resumed retro must not leave a duplicate that can never be removed.

<!-- [[agent-vault-d48-there-is-no-command-to-retire-a-decision-without-a-replaceme]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D14
---
# There is no command to retire a decision without a replacement

## Decision
A decision is only ever ended by a record that replaces it.

## Why
A replacing record that says the rule no longer applies does the same job and keeps the history.

<!-- [[agent-vault-d49-only-the-phrase-replaces-followed-by-a-record-link-in-a-last]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#36 D10
---
# Only the phrase `replaces` followed by a record link in a lasting plan decision line counts as a replacement

## Decision
A plan decision that reverses an active record says `replaces [[<record>]]` and is lasting, and only that phrase counts as a replacement.

## Why
A lasting decision may cite an active record without reversing it, and `seal retro` must not demand a supersession it shouldn't.

<!-- [[agent-vault-d50-designs-live-in-agent-designs-project-numbered-per-project]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D1
---
# Designs live in Agent/Designs/<project>/, numbered per project

## Decision
Designs are named <project>-design-<n>-<slug>.md and numbered per project.

## Why
It mirrors decision records, keeps file names unique across the vault for links, and gives each design a short stable name.

<!-- [[agent-vault-d51-a-sealed-design-is-never-edited]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D2
---
# A sealed design is never edited

## Decision
A later design names the one it extends or replaces in its extends or replaces field.

## Why
A design is the reasoning later plans are checked against and has to stay as it was approved.

<!-- [[agent-vault-d52-records-come-in-through-decision-add-or-seal-design-and-neve]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D3
replaces: "[[agent-vault-d43-all-of-a-record-s-content-comes-in-through-decision-add]]"
---
# Records come in through decision add or seal design, and never change after

## Decision
Every decision in a design becomes a decision record, created by seal design itself from the approved decision entries; a record's content is set once by the CLI and never edited.

## Why
Architecture-level decisions are made in designs, and creating the records in the sealing command leaves no gap for one to fall through. What the replaced record protected (content fixed at creation by the CLI, never edited) still holds.

<!-- [[agent-vault-d53-validate-checks-a-sealed-design-against-its-records-and-idea]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D4
---
# validate checks a sealed design against its records and ideas

## Decision
A sealed design needs a decision record for each of its decisions and each listed idea promoted to it.

## Why
The guard cannot stop an edit from turning a draft into a sealed design, and a check on the result catches one that skipped seal design.

<!-- [[agent-vault-d54-a-story-s-issue-is-linked-through-the-issue-s-source-section]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D8
---
# A story's issue is linked through the issue's Source section

## Decision
The link is the design name and story number under the issue's Source, and design stories finds it by reading every issue in the project's repos and filtering locally.

## Why
A sealed design cannot record issue numbers, GitHub holds that fact, and its search index lags behind just-filed issues.

<!-- [[agent-vault-d55-seal-design-promotes-the-design-s-listed-ideas]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D10
---
# seal design promotes the design's listed ideas

## Decision
It marks each listed idea promoted and links the design in promoted_to.

## Why
Doing it in the sealing command means an absorbed idea cannot stay open by accident.

<!-- [[agent-vault-d56-architecture-diagrams-are-mermaid-blocks-in-the-design-note]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D12
---
# Architecture diagrams are Mermaid blocks in the design note

## Decision
Diagrams live inside the design note as Mermaid code blocks.

## Why
Obsidian renders them natively and they stay text that the note's own history keeps.

<!-- [[agent-vault-d57-designs-follow-rigor-rules]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: plan agent-vault#37 D13
---
# Designs follow rigor rules

## Decision
Each major choice has at least two genuinely different options with trade-offs, every decision has a reason, the technology is named, risks and unknowns stay open questions, and research outside the codebase is done when the topic needs it.

## Why
The user wants a rigorous session that ends in well-made documentation.

<!-- [[agent-vault-d58-nothing-specific-to-one-machine-is-written-into-the-vault]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D1
---
# Nothing specific to one machine is written into the vault

## Decision
The installer and the CLI write no machine-specific text (absolute paths, interpreters, user names) into the vault; everything machine-specific lives in `~/.claude/` on that machine.

## Why
The vault syncs between machines, so any machine-specific text written there is overwritten by the next machine's install and then wrong on the other.

<!-- [[agent-vault-d59-the-cli-is-run-everywhere-as-claude-agent-vault-bin-vault]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D2
---
# The CLI is run everywhere as ~/.claude/agent-vault/bin/vault

## Decision
The installer writes a POSIX sh launcher at `~/.claude/agent-vault/bin/vault` that runs the Python recorded in `agent-vault.json` on the installed `vault.py`; `lib.CLI`, the vault guide, the skills and the CLI permission rule all use that exact string.

## Why
The same text is right on every machine, so the shared vault and skills can show a runnable command that one permission rule matches, and a later plugin's `bin/` uses the same mechanism.

<!-- [[agent-vault-d60-claude-hooks-run-through-a-launcher-that-fails-closed-when-p]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D3
---
# Claude hooks run through a launcher that fails closed when Python is gone

## Decision
Hook commands in user settings call `~/.claude/agent-vault/bin/hook` by absolute path; it reads the Python from `agent-vault.json`, and when the config exists but that Python is missing it blocks the PreToolUse guards with "rerun install.py" and reports the same as a visible non-blocking error in SessionStart, PostToolUse and Stop; with no config it stays silent.

## Why
Homebrew and the Microsoft Store move Python on upgrade, and a hook that fails to start is a non-blocking error, so without this the guards silently switch off; blocking tool calls until the user reruns the installer is preferred to unguarded sessions, while never blocking a session from starting or stopping.

<!-- [[agent-vault-d61-agent-vault-supports-windows-and-macos-and-ci-runs-the-suite]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D4
---
# agent-vault supports Windows and macOS, and CI runs the suite on both

## Decision
The supported platforms are Windows and macOS; a GitHub Actions workflow runs the lint and the tests on both, with Python 3.9 and 3.12, for every PR and push to `main`, without being a required check.

## Why
It catches a platform regression on every PR at no cost for a public repo, while the local pre-push hook stays the gate.

<!-- [[agent-vault-d62-gh-and-git-are-found-through-paths-the-installer-records]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D5
---
# gh and git are found through paths the installer records

## Decision
The installer records the paths of `gh` and `git` in `agent-vault.json`; the code uses `AGENT_VAULT_GH` (for `gh`), then the recorded path, then PATH, and has no per-platform search lists.

## Why
A Claude app launched from the macOS Finder often lacks Homebrew's PATH, and recording at install time follows the rule d38 set for Python with one code path for every platform.

<!-- [[agent-vault-d63-the-installer-checks-its-prerequisites-before-changing-anyth]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D6
---
# The installer checks its prerequisites before changing anything

## Decision
`install.py` checks Python 3.9+, `git`, and `gh` installed and logged in before it writes anything, stops on a missing one naming the fix, only warns about a missing `ruff`, and does not check how git authenticates.

## Why
A second machine should either install completely or say exactly what is missing, and leaving git authentication unchecked keeps d15's personal choice from locking out a different setup.

<!-- [[agent-vault-d64-the-installer-never-downgrades-the-shared-vault-guide]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D7
---
# The installer never downgrades the shared vault guide

## Decision
The vault guide carries the committer time and id of the agent-vault commit it was written from, and the installer rewrites it only when its own `HEAD` is the same age or newer, unless forced.

## Why
Machines install from their own clones and may lag, and a stale machine must not undo the guide a newer machine wrote to the shared vault; commit time, unlike ancestry, still works with squash-only merges.

<!-- [[agent-vault-d65-portability-work-adds-no-absolute-paths-to-shared-or-package]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-04
source: design agent-vault-design-1-portable-across-machines-and-setups D8
---
# Portability work adds no absolute paths to shared or packaged files

## Decision
Code, skills and templates derive every path from the install folder, the config or `~`, never from an absolute path baked in at install, outside per-machine user settings and `agent-vault.json`.

## Why
It keeps a later move to a Claude Code plugin, whose folder changes on every update, a packaging change instead of a rewrite.

<!-- [[agent-vault-d66-seal-retro-merges-the-pr-otherwise-only-the-user-merges]] -->
---
type: decision
project: agent-vault
status: active
created: 2026-10-07
source: plan agent-vault#51 D2
---
# seal retro merges the PR; otherwise only the user merges

## Decision
seal retro squash-merges an open issue's PR once its retro is approved. Otherwise only the user merges: no other command or skill merges a PR, and a backfill never merges.

## Why
A merge made by the command that seals the retro can't happen before the retro. The earlier rule that only the user merges relied on habit, and the merge was a separate step that could be done too early or forgotten.
