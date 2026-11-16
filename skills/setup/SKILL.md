---
name: setup
description: 'Record how stories and PRs work in this vault (tracker, merge style, approvals, who owns repo settings, branch names and, for Jira, the connector, issue types and planned label) by interviewing the user and writing Agent/Setup.md through ~/.claude/agent-vault/bin/vault setup once they say yes. Use for requests like "set up this vault", "run the setup", "change the vault setup", or "switch this vault to Jira".'
---

# setup

Writes the vault's setup note, `Agent/Setup.md`: one place that says how this
vault's stories and PRs work. A vault with no setup note behaves as
agent-vault always has (GitHub issues, squash merges, no required approvals,
repo settings managed by `repo-init`, branches `{type}/{issue}-{slug}`).
The setup's tracker decides how issues are read and filed. Run this once
after `install.py` on a new vault, or whenever the way of working changes.

The CLI is `~/.claude/agent-vault/bin/vault`; every command below is written in full, as
`~/.claude/agent-vault/bin/vault <command>`.

This is not a pipeline skill: it is a one-time interview, not a stage of a
story, so it sends no push notification and uses no approval prompt. It still
writes nothing until the user has said an explicit yes to the exact values.

## Never

- Never edit `Agent/Setup.md` with the edit tools (the guard blocks it);
  only `~/.claude/agent-vault/bin/vault setup` writes it, because it checks the whole setup first.
- Never pass `--replace` before you have shown the user what changes.
- Never put a path, a machine name or anything else that differs between the
  user's machines into a value: the note is shared by every machine on the
  vault.
- Never call Jira or change anything in it; this skill only records names.

## 1. Read what is there

- Run `~/.claude/agent-vault/bin/vault schema` and read its "Setup note" lines: the fields, their
  allowed values and defaults, and the branch-pattern rules.
- Read `Agent/Setup.md` if it exists. If it does, this run changes an
  existing setup: keep its values as the starting point of every question.
- List the projects in `Agent/Projects/` and the repos each one names; a
  Jira setup needs each project's Jira key later.

## 2. Interview

Ask one topic at a time, in open-ended prose (never multiple choice), and
explain what each choice changes. Offer the current value (or the default)
as the answer if the user has no preference. Topics, in this order:

1. Tracker: where stories live, GitHub issues (`github`) or Jira tickets
   (`jira`). This decides how issues are read and filed.
2. Merge style: how a finished PR is merged, `squash`, `merge` or `rebase`.
3. Approvals: whether a PR needs a team approval before it merges
   (`required`) or not (`none`).
4. Repo settings: whether `repo-init` may set a repo's merge settings
   (`managed`), or only check that the repo allows the merge style because
   the team owns the settings (`checked`). Labels and the issue template
   follow the tracker (GitHub only), either way.
5. Branch names: the pattern, built from `{type}`, `{issue}` and `{slug}`
   (`{issue}` and `{type}` exactly once, `{slug}` at most once, something
   between two placeholders), and the words `{type}` becomes for a story
   (`branch_feature`, default `feat`) and for a bug (`branch_bug`, default
   `fix`): lowercase words, different from each other, never `hotfix`.

For a Jira setup, also:

6. The Jira connector: the name of the MCP server that reaches the team's
   Jira. Check that this session has tools from a server of that name; if it
   has none, say so and ask whether the name is right before going on.
7. The issue types a feature, a story and a bug become (e.g. `Epic`,
   `Story`, `Bug`), exactly as Jira names them.
8. The planned label: the label a ticket gets once its plan is sealed, or
   `none` for no label.
9. Each project's Jira key (e.g. `PROJ`), for every project with repos.

Ask a follow-up wherever an answer is unclear; don't guess.

## 3. Show the exact values, then write

- Show the exact command you will run, `~/.claude/agent-vault/bin/vault setup --tracker github|jira`
  with every flag the answers set, and the full list of values the note will
  hold (every field, defaults included).
- If a setup note exists, show only what changes, each as old value → new
  value, and say the command will carry `--replace`.
- Ask the user, in prose, whether to write it. Run the command only on an
  explicit yes; on anything else, take up what they want changed and show
  the values again.
- Run it: `~/.claude/agent-vault/bin/vault setup --tracker <tracker> --merge <style> --approvals <a> --repo-settings <r> --branch <pattern> --branch-feature <word> --branch-bug <word>`,
  plus, for Jira, `--jira-feature-type <type> --jira-story-type <type> --jira-bug-type <type> --jira-planned-label <label> --jira-connector <name>`,
  and `--replace` when a note exists. Quote the branch pattern for the shell.
- If the command refuses, it has written nothing: tell the user what it
  said, settle the value with them, and show the values again.
- In a Jira setup, tell the user to add `jira_project: <KEY>` to each
  project note with repos (the keys from step 2); `~/.claude/agent-vault/bin/vault validate` warns
  for any project still without one. A GitHub setup refuses while any
  project has `jira_project`, so in a switch back to GitHub those lines
  come out first.
- Finish with `~/.claude/agent-vault/bin/vault validate` and report what it shows.
