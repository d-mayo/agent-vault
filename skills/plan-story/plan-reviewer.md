# plan-story reviewer

The prompt `plan-story` sends to an independent reviewer subagent once the
draft plan passes `vault.py validate`, before the plain-language summary
goes to the user. The reviewer runs as an independent subagent on Opus by
explicit override — independence matters even though `plan-story` sessions
are themselves conventionally run on Opus (design §5); the drafting
session's own model is never assumed and never silently reused.

## Requesting it

Launch it as an agent with `model: "opus"` — an explicit override, never the
drafting session's own model. If the tool available in this session has no
way to choose a subagent's model, stop and ask the user instead of
reviewing on the drafting session's own model (`plan-story`'s own "Stop and
ask if").

## Prompt

Give the reviewer, verbatim and in full:

1. The draft plan, in full.
2. The GitHub issue: title and body (and the comments, marked as
   background only — they don't count as requirements, same as in
   Research).
3. The repo's `CLAUDE.md`, in full.
4. The spec sections and the code the plan's `Context` and `Files:` lines
   point at, and the neighbouring issues research turned up.
5. The project overview note.
6. The project's active decision records, in full (`vault.py decisions
   --project <id>` lists them; the notes are in `Agent/Decisions/<id>/`).
7. The exact instructions under "Findings format" below — the severity
   definitions and the requirement to answer with nothing but those lines.

Ask it to check the draft against all of the above:

- For every acceptance criterion, does its referenced test actually
  exercise it — not just cite the AC number? (`vault.py validate` already
  guarantees every AC has *a* test; the judgment call is whether that test
  is real.)
- Can every step be done exactly as its `Files:` and `Do:` say, with the
  repo as it stands?
- Does anything in the draft contradict the code, the spec, or a
  neighbouring issue's scope?
- Does anything in the draft contradict an active decision record without
  a decision saying `replaces [[<record>]]` for it?
- Does the plan's `Acceptance criteria` cover everything the issue's title
  and body actually require — not just what the draft's own `Out of scope`
  excludes?
- Does anything in the draft go beyond the issue itself?

## Findings format

The reviewer answers with one line per finding, most important first:

```
(major) <one-sentence finding, self-contained>
(minor) <one-sentence finding, self-contained>
```

or `No findings.` if there's nothing to report. Nothing else in the reply.

`major`: a referenced test doesn't actually exercise the acceptance
criterion it claims to cover, an acceptance criterion the issue requires is
missing from the plan altogether, a step can't be done as written, or the
draft contradicts the code, the spec, the repo `CLAUDE.md`, a
neighbouring issue's scope, or an active decision record that the draft
doesn't name as replaced. `minor`: everything else worth recording —
scope creep beyond the issue itself, a missed edge case, a small
inconsistency.

The reviewer doesn't fix anything and doesn't know which findings
`plan-story` will end up able to fix. `plan-story` fixes what it can in the
draft, re-runs `vault.py validate` after fixing, and folds the rest into
the summary's judgment calls, rather than numbering findings into the plan
itself — a plan has no `## Review findings` section (design §7.2); that
bookkeeping belongs to the impl note `implement-story` keeps once the plan
is sealed.
