# agent-vault — design v1

Sealed design record, 2026-09-26. This is the input to the first issues, not a
living document. Once implemented, the truth lives in the code (schema in
`lib.py`), the repo `CLAUDE.md`, and the skills. Changes go through new issues;
this file is not edited.

## 1. Purpose

A personal development workflow in which Claude plans, implements and reviews
work on GitHub issues, and an Obsidian vault acts as Claude's working memory
across sessions. Built to fix the failure modes of the work setup:

- instructions that were suggestions (skills) instead of guarantees (hooks, scripts);
- stale and contradictory facts in vault notes and repo `CLAUDE.md` files;
- work attributed to the wrong project;
- notes that were written but never retrieved.

## 2. Principles

1. **Guarantees live in code.** Anything that must always happen is a hook or
   script. Skills hold judgment only. `CLAUDE.md` holds orientation only.
2. **Each fact has exactly one home.** GitHub holds *what* (issues, stage).
   The vault holds *how* (plans, notes, retros). Repo `CLAUDE.md` holds facts
   about a repo's code. The project overview holds facts spanning a project.
3. **Two kinds of notes.**
   - *Sealed*: dated, never edited once sealed (plan, impl notes, retro, idea
     entries, daily log). They can be old but never stale.
   - *Living*: short, rewritten not appended, always current (project
     overview, repo `CLAUDE.md`). Each has one pipeline stage that must audit it.
4. **Attribution is looked up, never guessed.** Branch → issue → repo →
   project, via fixed conventions and the `repos:` field.
5. **Notes are written for Claude.** Terse, structured, retrievable by
   frontmatter without reading bodies. The user gets information by asking
   Claude, not by browsing.
6. **Retrieval is automatic.** Session-start hooks hand Claude the context;
   nothing depends on Claude remembering to look.

## 3. Components and locations

| What | Where | Notes |
|---|---|---|
| Source of truth for the system | `github.com/d-mayo/agent-vault` | cloned in the user's code folder |
| Installed code (scripts, hooks) | `~/.claude/agent-vault/` | local disk, not iCloud |
| Skills | `~/.claude/skills/{plan-story,implement-story,retro}/` | user-level, available in every repo |
| User-level hooks | `~/.claude/settings.json` (merged, never overwritten) | fire in repo sessions |
| Vault | `…/iCloud~md~obsidian/Notes/` | notes only; no code |
| Vault hooks | `Notes/.claude/settings.json` | fire in vault sessions |
| Vault orientation | `Notes/CLAUDE.md` | |
| Config | `~/.claude/agent-vault.json` | `{ "vault": "<path to Notes>" }` |

`install.py` (run by the user) copies code to `~/.claude/agent-vault/`,
installs skills, merges hook entries into both settings files, writes the
config, and writes `Notes/CLAUDE.md`. It is idempotent. Installed code is
never edited in place: the guard hook blocks it, and changes go through this repo.

The phone side of the vault (everything in `Notes/` outside `Agent/`,
including `taxonomy.json`) is read-only to Claude.

## 4. Git and GitHub conventions

- `main` plus short-lived branches. Every change goes through a PR. Only the user merges.
- Branch name: `<type>/<issue>-<slug>`, where type is one of
  `feat fix chore docs refactor test perf hotfix`.
  Regex: `^(feat|fix|chore|docs|refactor|test|perf|hotfix)/(\d+)-[a-z0-9-]+$`.
- Commits: `<type>(<scope>): <subject>`.
- PR body contains `Closes #<issue>`. Merges are **squash only**; branches are
  deleted on merge.
- Releases get semver tags.
- `main` is protected by a pre-push hook in each repo that refuses pushes to
  `main` and branch names not matching the regex. GitHub branch protection is
  used where the plan allows it (free for public repos).
- `repo-init <path>` sets up a repo: pre-push hook, `planned` label,
  squash-only merge and delete-branch-on-merge via `gh api`, and a
  `CLAUDE.md` skeleton.

### Stage of an issue (derived, never stored)

| Stage | Condition |
|---|---|
| backlog | issue open, no sealed plan |
| planned | sealed plan exists and `planned` label is set (label set by script on seal) |
| in progress | branch `*/<n>-*` exists, no open PR |
| in review | open PR for the branch |
| done | issue closed (by merge with `Closes #n`) |

## 5. Pipeline

0. **Issue.** Created by the user, or by a retro follow-up the user explicitly approved.
1. **plan-story** (Opus). Reads the issue (`gh issue view --json`), the repo
   `CLAUDE.md` and the code. Interviews the user until nothing is ambiguous.
   Writes the plan draft. On user approval, `seal plan` records
   `issue_updated` and `base_sha`, sets the `planned` label and logs `planned`.
2. **implement-story** (cheaper model, fresh context). The preflight script
   fails if there is no sealed plan, or if the issue changed after
   `issue_updated` (re-plan needed); it warns if `base_sha` is far behind.
   It creates the branch, then implements step by step with a review subagent
   and test loops, recording deviations and review findings in impl notes.
   It opens the PR (`Closes #n`) and logs `pr-opened`.
3. **User review.** Requested changes go back through implement-story on the
   same branch, and impl notes stay open.
4. **retro**, before merge. Runs `claudemd-lint`, then audits every section of
   the repo `CLAUDE.md` and every section of the project overview, rewriting
   or removing as needed (in the same PR). It writes the retro and sorts
   follow-ups into *issue* (created only on explicit approval), *idea*, or
   *dropped*. Facts go into living docs as part of the audit. `seal retro`
   verifies audit coverage, seals the impl notes and retro, and logs `retro-done`.
5. **Merge** by the user. The issue closes automatically.

## 6. Vault layout

```
Notes/
├── CLAUDE.md
├── .claude/settings.json
├── <phone side: taxonomy.json and its buckets>     read-only
└── Agent/
    ├── Projects/<project-id>.md
    ├── Work/<repo>/<repo>-<n>-plan.md
    ├── Work/<repo>/<repo>-<n>-impl.md
    ├── Work/<repo>/<repo>-<n>-retro.md
    ├── Ideas/<YYYY-MM-DD>-<slug>.md
    └── Daily/<YYYY-MM-DD>.md
```

No Intake, Notes or Archive folders. Phone captures become ideas via
`handoff pull`. A finished project gets `status: archived` in place.
`<repo>` is the GitHub repo name. Repo names are unique across projects.

## 7. Note types

Common rules: YAML frontmatter with `type`; dates `YYYY-MM-DD`; timestamps
ISO 8601 UTC; wikilinks quoted in frontmatter; every `[[link]]` in `Agent/`
must resolve; required `##` sections must appear in the given order;
unknown frontmatter fields are rejected; notes are created only by the CLI.

### 7.1 Project overview (living, max 60 lines)

```yaml
type: project
id: agent-vault            # kebab-case, equals filename
status: active             # active | paused | archived
repos: [d-mayo/agent-vault]
audited: 2026-09-26        # set by `seal retro`
```

Sections: `Purpose` (1–3 lines) · `Current state` · `Architecture`
(how the repos fit together, nothing internal to one repo) · `Standing decisions`
(one line each, with the reason; superseded ones removed).
Never contains in-flight work (that's on GitHub) or single-repo facts (those
go in the repo `CLAUDE.md`). Each repo appears in exactly one project.

### 7.2 Plan (sealed at approval, no hard cap; warns above 400 lines)

```yaml
type: plan
repo: agent-vault
issue: 12
issue_updated: 2026-09-26T21:04:00Z
base_sha: 3f9c2e1
status: draft              # draft | sealed
```

Sections: `Goal` · `Acceptance criteria` (`- AC<n>: …`) · `Decisions`
(`- D<n>: <decision>, because <reason>`) · `Implementer's discretion` ·
`Context` (pointers to code, not copies) · `Steps` (`### <n>. <title>`, each
with `Files:`, `Do:`, `Done when:`) · `Tests` (`- T<n> → AC<m>: …`, then a
`Full check:` line) · `Stop and ask if` · `Out of scope`.

Checks: every AC is referenced by at least one test; every step has `Files:`
and `Done when:`; `base_sha` and `issue_updated` are set before sealing.
Above 400 lines, a warning suggests splitting the issue.

### 7.3 Implementation notes (open until the retro, max 80 lines)

```yaml
type: impl
repo: agent-vault
issue: 12
branch: feat/12-note-schema
pr: 14                     # set by the script that opens the PR
status: open               # open | sealed
```

Sections: `Deviations` (`- Step <n>: …, because …`) · `Review findings`
(`- R<n> (major|minor): … → fixed in <sha>` or `→ won't fix: <reason>`) ·
`Verification` (final command results) · `Discoveries` (input for the retro).
"None" is valid for any section.

### 7.4 Retro (sealed at completion, max 40 lines)

```yaml
type: retro
repo: agent-vault
issue: 12
pr: 14
status: open               # open | sealed
```

Sections: `Summary` (max 5 lines) · `CLAUDE.md audit` · `Overview audit` ·
`Follow-ups`.
Audit lines: `- <section heading>: confirmed|rewritten|removed — <reason>`.
`seal retro` fails unless every current `##` heading of the repo `CLAUDE.md`
and of the project overview has exactly one audit line. (Removed sections are
listed too, and must no longer exist.)
Follow-up lines: `- issue #<n> created`, `- idea [[<idea>]]`, or `- dropped: <why>`.

### 7.5 Idea (one per file, body max 8 lines)

```yaml
type: idea
project: agent-vault       # optional; blank = general
source: retro agent-vault#12   # retro <repo>#<n> | phone:<path> | session
status: open               # open | promoted | dropped
created: 2026-09-26
promoted_to: ""            # issue URL, set by `idea promote`
```

Body: a one-line `#` title plus up to a few lines of context. Open ideas
older than 90 days are listed by `ideas review` for keep or drop.

### 7.6 Daily log (script-written only)

```yaml
type: daily
date: 2026-09-26
```

Single section `Log`. Lines are `- HH:MM <repo>#<n> <event>[ — detail]` or
`- HH:MM session — <summary> [[<project>]]`. Events: `planned`, `started`,
`pr-opened`, `retro-done`, `idea-added`, `idea-promoted`. Claude never edits
daily notes directly; only `log` and pipeline commands write them.

## 8. Repo `CLAUDE.md` (living, max 120 lines, lives in each repo)

Sections: `Purpose` (2 lines plus the project name) · `Commands` · `Layout` ·
`Conventions` · `Gotchas`. Each section may carry
`<!-- covers: <glob>[, <glob>] -->`.

`claudemd-lint` checks:
- every backticked path exists;
- every listed command resolves (npm scripts in `package.json`, make/just
  targets, executables on PATH);
- every `covers` glob matches at least one file;
- **staleness**: sections whose covered files changed in commits after the
  last commit that touched that section;
- the line cap.

## 9. Hooks

| Hook | Scope | Behavior |
|---|---|---|
| SessionStart | user (repo sessions) | Resolve origin → repo → project. Parse the branch → issue. Print: project overview, issue stage and which notes exist, last 5 daily lines for the project, open idea count, and any `claudemd-lint` failures. Silent outside registered repos. |
| SessionStart | vault | Project one-liners, last 10 daily lines, `validate` results. |
| PreToolUse (Write/Edit/MultiEdit/NotebookEdit) | both | Block: phone side, `.obsidian/`, installed code in `~/.claude/agent-vault/`, sealed notes, any direct edit to `Daily/`, and new notes written without the CLI. |
| PostToolUse (Write/Edit/MultiEdit) | both | Validate the changed note, including size caps; exit 2 with problems. |
| Stop | both | Block once if files changed this session and no daily line was written since the last change. Skips if a pipeline event was logged. |

All hooks are Python standard library, never write `__pycache__`, and never
block a session from starting.

## 10. CLI (`~/.claude/agent-vault/vault.py`)

```
new project <id> --repo <owner/name> [--repo …]
new plan <repo> <issue>          # scaffolds plan and impl drafts
seal plan <repo> <issue>         # sets issue_updated, base_sha, label; logs
preflight <repo> <issue>         # implement-story gate
branch <repo> <issue> --type feat --slug …
open-pr <repo> <issue>           # PR with Closes #n; records pr; logs
new retro <repo> <issue>
seal retro <repo> <issue>        # audit coverage; seals impl + retro; sets audited; logs
idea add "<title>" [--project <id>] [--source …]
idea promote <file> [--title …]  # creates issue after approval, records URL; logs
idea drop <file> --reason …
ideas [--project <id>] [--status open]
ideas review
handoff list | handoff pull <phone path> [--project <id>]   # creates an idea
log "<text>" [--project <id>]
stage <repo> <issue>
repo-init <path>
claudemd-lint [<repo path>]
validate | status | schema
```

## 11. Out of scope for v1

- The ideas skill (proposes projects, features and POCs from open ideas and
  phone captures). Recorded as the first idea.
- Automating the pipeline end to end without the user.
- Any change to the phone capture system.
