---
name: design
description: 'Work a larger idea (a new project, a feature, a refactor, or several open ideas together) through with the user into a sealed agent-vault design: research first, interview over as many rounds as it takes, draft options and decisions, have an independent reviewer check the draft, seal it only once the user approves a plain-language summary, then file one issue per story. Use for requests like "design a ...", "let''s design ...", "combine these ideas into a design", or "file the stories of <design>".'
---

# design

The step before issues exist. It turns a large idea into a sealed design
note (`Agent/Designs/<project>/<project>-design-<n>-<slug>.md`), one decision
record for every decision it made, and one issue per story, so each issue
reaches `plan-story` already placed in a reasoned whole. The reviewer's brief
and findings format are in `design-reviewer.md`, next to this file.

By convention a design session runs on Opus: design is judgment work. The
reviewer is a separate subagent on Opus, whatever model this session runs on.

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); it always ends in
`vault.py`, so `vault.py new design <project> "<title>"` means "run that
string followed by `new design <project> "<title>"`". `<repo>` is always the
plain repo name (e.g. `agent-vault`), never `owner/name`; `<design>` is the
design's file name without `.md`.

## Never

- Never write code, create a branch, or plan an issue; stories go through the
  normal pipeline afterwards.
- Never edit a sealed design. A change is a later design that extends or
  replaces it (`extends:` or `replaces:` in its frontmatter).
- Never write a decision record by hand: `vault.py seal design` creates them
  from the design's `### D<n>` entries.
- Never seal before the user has approved the summary, and never create an
  issue before the user has approved its exact title and body.
- Never write a risk or an unknown up as a decision; it is an open question.

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

## Resolve the vault, CLI and project

- Vault and CLI: `~/.claude/agent-vault.json`'s `vault` field is the vault
  path; the exact `vault.py` invocation is the line under "CLI after
  install" in `<vault>/CLAUDE.md`.
- Project: the one the user names, or the project whose `repos:` lists the
  current clone's `origin`. A brand-new project has no note yet: its id is a
  lowercase-kebab-case name you agree with the user, and the note is created
  after approval (see Approval).
- Resume, don't restart: list `Agent/Designs/<project>/`. A draft there is
  the design in progress; read it and carry on from where it stopped instead
  of starting another. Only a sealed design is finished, and a new design may
  name one in `extends` or `replaces`.

## Research

Before asking the user anything:

1. Read the project overview (`Agent/Projects/<id>.md`), if the project
   exists.
2. Read the project's active decision records: `vault.py decisions
   --project <id>` lists them; read the ones that bear on the idea. A design
   must not contradict one unless a decision in it says `Replaces:` that
   record.
3. Read the open ideas (`vault.py ideas --project <id>`, and the ones with
   no project), and the ones the user names; these are what the design may
   absorb.
4. Read the repo's open issues (`gh issue list --state open --limit 200`)
   to see what is already planned or in flight.
5. Read the code the idea touches and the repo's `CLAUDE.md`.
6. When the topic needs knowledge from outside the codebase (a library, a
   protocol, a platform's limits, how others solved it), research it: web
   search, official documentation, comparable projects. Cite what you
   found when it shapes a question or an option.

## Interview

Ask only what research couldn't settle, in plain prose, open-ended, with no
list of options to pick from. Each question states the fact that raised it
(what you found, and where). Give a recommendation only when you're
confident, and always with the reason.

Run as many rounds as the design needs, not a fixed number. In every round,
challenge assumptions: ask what the user is taking for granted, what the
simplest alternative would be, what would make this wrong, who else it
affects. Keep going until nothing that would change the design is still
ambiguous. Save the state of the design into the draft as you go, so another
session can resume it.

## Rigor

Every design is held to these rules:

- Every major choice has at least two genuinely different options, each
  with its trade-offs, under `Options considered`; variants of one idea
  don't count.
- Every decision has a reason (`Why:`); a decision with no reason is not
  one yet.
- The technology the design needs is named in `Chosen design`: languages,
  libraries, services, formats, with the version or constraint that
  matters.
- Where structure or data flow reads better drawn than written, add a
  Mermaid diagram (a fenced `mermaid` code block) in `Chosen design`.
- Risks and unknowns stay in `Open questions`, each with what would
  settle it. They are never decisions.

## Draft

1. `vault.py new design <project> "<title>"` scaffolds the draft (it is
   fine for the project not to exist yet). Fill in, in order: `Problem` ·
   `Goals` · `Non-goals` · `Ideas` (`- [[<idea>]]` for each idea the design
   absorbs, or `None`) · `Options considered` · `Chosen design` ·
   `Decisions` · `Stories` · `Open questions`.
2. Decisions: `### D<n>. <title>` (one line, no `[[` or `]]`), then a
   `Decision:` line, a `Why:` line and, only when it reverses an active
   record, `Replaces: [[<record>]]`. Each becomes a decision record.
3. Stories: `### S<n>. <title>`, then `Outcome:` (what is true afterwards,
   observable from outside), `Depends on:` (`None`, or earlier stories such
   as `S1, S2`) and, in a project with several repos, `Repo: <name>`. Each
   story must be buildable and shippable on its own once its dependencies
   are done.
4. `vault.py validate` checks the draft; format problems show as warnings on
   a draft and become errors once sealed, so clear every warning about the
   draft before review.

## Design review

1. Launch an independent reviewer subagent on Opus (see
   `design-reviewer.md`) with the draft, the research and the project's
   active decision records.
2. It checks for goals no story delivers, stories that can't be built or
   shipped alone, contradictions with active decision records that are not
   replaced, open questions passed off as decisions, and options that aren't
   genuinely different.
3. Fix what you can in the draft. Anything you can't fix, or chose not to,
   goes into the summary instead of being silently dropped.

## Summary

Present a plain-language summary in chat; never ask the user to read the raw
design:

- the problem and the goals,
- the options weighed and the one chosen, with the technology it needs,
- the user's own decisions from the interview, and the skill's judgment
  calls flagged for the user to check,
- each decision that becomes a record, marked as new or as replacing a named
  record,
- the stories, in order, with their dependencies,
- the open questions that remain,
- one pointed question about the design's riskiest judgment call, which the
  user answers before approving.

## Approval

1. Seal only after the user has answered the closing question and explicitly
   approved, asked with the approval prompt. For requested changes: edit the draft, run another review if
   the change is substantial, re-run `vault.py validate`, summarise the
   change, and ask again.
2. For a new project, create its note now, after approval and before
   sealing: `vault.py new project <id> --repo <owner/name> --purpose "..."`
   (leave `--repo` out when no repo exists yet; the project then has none
   and its stories wait). A project must exist before its design can be
   sealed.
3. `vault.py seal design <design>` refuses, changing nothing, if anything is
   wrong, and names each problem. Fix the draft and run it again. When it
   succeeds, the design is sealed, every decision has its record, each
   listed idea is promoted to the design and the project overview's
   `Standing decisions` is updated.

## File the stories

1. First the design's feature: one issue on GitHub, labelled `feature`, that
   the design's stories become sub-issues of. Draft it in the issue template:
   - `Problem`: the design's Problem;
   - `Desired outcome`: the design's Goals;
   - `Constraints`: the design's decisions, each by `D<n>` and title;
   - `Out of scope`: the design's Non-goals;
   - `Source`: `[[<design>]]` with no story number (this link is how
     `design stories` and `features` find it).
   It goes in the project's only repo or, with several, the repo the user
   names. A project with no repo yet files neither the feature nor its
   stories: say so. Show the user the title and body exactly as they will be
   posted and, after they approve that wording, write the body to a temporary
   file outside the clone and the vault and run `vault.py issue create <repo>
   --title "<title>" --body-file <file> --feature <design>`. It attaches the
   stories already filed that have no parent; if it exits non-zero, some
   attach failed: it printed the `gh issue edit` command for each, so run
   those, or tell the user which stories to attach by hand.
2. For each story of the sealed design, draft an issue in the same template:
   - `Problem`: why this story exists, from the design's Problem and Goals;
   - `Desired outcome`: the story's `Outcome:`;
   - `Constraints`: the story's dependencies as `Depends on: S<n> (<title>)`
     lines, naming the issue once it is filed, plus the design decisions the
     work must respect, each by `D<n>` and title;
   - `Out of scope`: what belongs to other stories or the design's Non-goals;
   - `Source`: `[[<design>]] S<n>` (this link is how `design stories` finds
     the issue, so write it exactly like that, under Source).
3. The repo is the story's `Repo:` line, or the project's only repo. A
   story of a project with no repo yet waits: say so, and don't file it.
4. Show the user every title and body, exactly as they will be posted, and
   file each only after they approve that wording (the approval prompt). Write each body to a
   temporary file outside the clone and the vault, then run `vault.py issue
   create <repo> --title "<title>" --body-file <file> --parent <n>`, where
   `<n>` is the feature's number; for a story whose repo isn't the feature's,
   `--parent <feature repo>#<n>`. Never run `gh issue create`. If the CLI
   refuses a body, fix what it names, show the user the changed wording and
   ask the approval prompt again.
5. Finish with `vault.py design stories <design>`: it should show the feature
   and every story filed under it. Report any story still "not filed" and why
   (a new project with no repo yet, or one the user chose to hold back), and
   any story marked as not under the feature. Then send the push notification:
   the next stage is to plan a story (or, when no story was filed, which
   stories wait and why).

## Filing later

Asked to file a sealed design's stories later ("file the stories of
<design>"): run `vault.py design stories <design>`. When it says `no feature
yet`, create the feature first, exactly as in File the stories step 1, even
when every story is already filed (it attaches them). Then, for each story it
shows as "not filed", draft, show, get approval for and create the issue
exactly as in File the stories, with `--parent`, then run the command again.
Nothing refuses to continue while stories are unfiled; this skill is how they
get filed. When the command shows the stories filed, send the push
notification, naming a story to plan next (or which stories wait and why).

## Stop and ask if

- Research turns up something the idea doesn't cover, or a contradiction with
  an active decision record that the design doesn't replace.
- The reviewer can't be run on Opus: never silently fall back to reviewing on
  this session's own model.
- The user's answers still leave something ambiguous that would change the
  design.
- `vault.py seal design` refuses for a reason you can't fix in the draft
  (for example, a record it would replace is no longer active).
- A rigor rule can't be met for a choice without inventing options the user
  didn't ask for.
