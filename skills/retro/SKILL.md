---
name: retro
description: 'Close out a GitHub issue before merge: audit the repo CLAUDE.md, its README and the project overview one heading at a time, sort every discovery and open point into an issue, an idea, or dropped, then seal the retro. Use for requests like "retro #9", "retro issue 9", or "write the retro for #9".'
---

# retro

Runs last in the pipeline, before merge (design §5 step 4). Audits the repo's
living docs against what the PR actually did, replaces restated facts with
pointers to their real home, sorts what's left into an issue, an idea, or
dropped, then seals the impl note and the retro. There is no reviewer
subagent here — this skill is out of scope for one (see the plan).

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); it always ends in
`vault.py`, so `vault.py new retro <repo> <issue>` means "run that string
followed by `new retro <repo> <issue>`". `<repo>` is always the plain repo
name (e.g. `agent-vault`), never `owner/name`.

## Never

- Never merge a PR, and never push to `main`.
- Never edit a sealed note. If a plan, impl note or retro turns out to be
  wrong, stop and ask.
- Never run `install.py`.
- Never post a GitHub issue edit before the user approves its exact wording.

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

## Preflight

1. Read the impl note (`Agent/Work/<repo>/<repo>-<issue>-impl.md`) in the
   vault. With no `pr:` field, the PR hasn't opened yet; stop and say so
   rather than improvising a retro for unfinished work.
2. `gh issue view <issue> --json state` to learn whether the issue is open
   or closed.
   - Open: check out the impl note's `branch:` field (`git switch <branch>`,
     fetching first if it isn't local yet). This must stay the current
     branch for the whole retro — `seal retro` checks it. If `branch:` is
     empty or the branch is gone, stop and ask.
   - Closed (a backfill, run after the merge): stay on whatever non-default
     branch is already checked out in this session — normally another
     issue's still-open feature branch running the backlog (D1: the
     backfilled issue's own branch is usually already deleted, and this
     avoids one throwaway branch and PR per backfilled issue). Never create
     a new branch or open a new PR for a backfill, and never check out the
     default branch itself.
3. Either way, the working tree must be clean before `seal retro` runs at
   the end; commit or stash anything in the way now.

## Audit

1. `vault.py claudemd-lint` on the current clone, and fix its failures
   (bad paths, commands that don't resolve, covers globs matching nothing,
   the line cap) before continuing — the audit below assumes CLAUDE.md is
   already accurate about paths and commands.
2. Check whether the retro note (`Agent/Work/<repo>/<repo>-<issue>-retro.md`)
   already exists in the vault first — `vault.py new retro <repo> <issue>`
   refuses if it does. None yet: run it to scaffold one. Already there and
   `status: open` (a resumed session): read what's already filled in and
   continue from there instead of re-scaffolding. Already `status: sealed`:
   stop and ask — `seal retro` would refuse it anyway.
3. Read the issue (`gh issue view <issue> --json title,body,comments`), the
   sealed plan, the impl note (its Deviations, Review findings and
   Discoveries), and the PR diff (`gh pr diff <pr>`, `<pr>` from the impl
   note's `pr:`).
4. Give every `## ` section of the repo's `CLAUDE.md`, of its `README.md`
   (if it has one), and of the project overview note exactly one audit
   line — `- <heading>: confirmed|rewritten|removed — <reason>` — under the
   matching retro section (`## CLAUDE.md audit`, `## README audit`,
   `## Overview audit`):
   - For every section, ask whether it restates a fact whose home is
     elsewhere — the code, another doc, the design record. If so, rewrite
     it into a pointer to that home instead of a copy.
   - `rewritten`: edit the section now, in this repo's `CLAUDE.md` or
     `README.md`; the audit line records what changed and why.
   - `removed`: delete the section now, same as above.
   - `confirmed`: leave it as-is; `seal retro` bumps the `verified:` date on
     any `CLAUDE.md` section that carries a `covers:` comment.
   - The project overview lives in the vault, not the repo: rewrite it
     there directly. Across every one of its sections, it never gets
     in-flight work (that belongs on GitHub, in the impl note) or
     single-repo facts, even for a single-repo project (those go in the
     repo's own `CLAUDE.md` instead, so they still move if the project
     later grows a second repo).
   - With no `README.md`, or one with no `##` headings, `## README audit`
     is `None`.
5. Repo-file rewrites (`CLAUDE.md`, `README.md`) are committed and pushed
   on the current branch before moving on — `seal retro` refuses a dirty
   tree, and it needs the audited state already on the remote.

## Follow-ups

1. Sort every impl-note discovery and every open point the audit raised
   into exactly one of:
   - `- issue #<n> created`: propose the exact title and body to the user;
     post it (`gh issue create`) only after they approve it.
   - `- issue #<n> amended`: an existing issue with no sealed plan for
     it — check `Agent/Work/<repo>/<repo>-<n>-plan.md`'s `status:` in the
     vault (missing file, or `status: draft`, both count); an issue with a
     sealed plan is never amended this way, since that would invalidate the
     plan at `preflight`. Propose the exact wording; edit it only after
     approval.
   - `- idea [[<idea>]]`: `vault.py idea add "<title>" --project <id>
     --source "retro <repo>#<issue>"`, `<id>` the project whose `repos:`
     lists this repo.
   - `- dropped: <why>`.
2. Check the project's open ideas (`vault.py ideas --project <id>`, same
   `<id>`) and propose dropping, with `vault.py idea drop <file> --reason
   "..."`, the ones this issue resolved.
3. Write the sorted lines under the retro's `## Follow-ups`.

## Seal

1. `vault.py validate` must pass with no errors before moving on; fix
   whatever it flags and re-run.
2. Present a plain-language summary of the retro in chat — what got
   audited and how, and the follow-ups and their disposition — and get the
   user's explicit approval before sealing anything.
3. `vault.py seal retro <repo> <issue>`.

## Backfill (a closed issue with no retro yet)

Same Audit, Follow-ups and Seal procedure as above, run on whatever
non-default branch is already checked out (see Preflight) instead of the
impl note's `branch:`, which may already be gone. That branch already has
an upstream and, usually, an open PR (the one running the backlog), so the
Audit step's repo-doc commits reach it the normal way — plain `git push`.
`seal retro` for a closed issue needs no `branch:` in the impl note; it
still refuses a dirty tree, and it commits and pushes its own `verified:`
bump to the current branch, giving that branch an upstream itself if it
somehow doesn't have one yet.

## Stop and ask if

- The impl note has no `pr:` yet.
- A retro note for the issue already exists and is `status: sealed`.
- `vault.py claudemd-lint` reports a failure that needs a decision rather
  than a fix (e.g. a covers glob matching nothing because the path it
  covered was intentionally removed elsewhere).
- An audited section's disposition (confirmed, rewritten or removed) isn't
  obvious from the PR diff and the code.
- A follow-up needs a GitHub issue created or amended, and the user hasn't
  approved the exact wording yet.
- `vault.py validate` still fails after fixing what's fixable.
- For an open issue, the branch to check out isn't the impl note's
  `branch:` — the branch was deleted or never existed; don't improvise one.
