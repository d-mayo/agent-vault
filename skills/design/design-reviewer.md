# design reviewer

The prompt `design` sends to an independent reviewer subagent once the draft
design validates, before the plain-language summary goes to the user. The
reviewer runs as an independent subagent on Opus by explicit override:
independence matters even though design sessions are themselves
conventionally run on Opus; the drafting session's own model is never assumed
and never silently reused.

## Requesting it

Launch it as an agent with `model: "opus"`: an explicit override, never the
drafting session's own model. If the tool available in this session has no
way to choose a subagent's model, stop and ask the user instead of reviewing
on the drafting session's own model (`design`'s own "Stop and ask if").

## Prompt

Give the reviewer, verbatim and in full:

1. The draft design, in full.
2. The project overview note and the project's active decision records, in
   full (`vault.py decisions --project <id>` lists them; the notes are in
   `Agent/Decisions/<id>/`).
3. The research the draft rests on: the ideas it absorbs, the open issues,
   the code and outside sources it cites, and the repo's `CLAUDE.md`.
4. The exact instructions under "Findings format" below: the severity
   definitions and the requirement to answer with nothing but those lines.

Ask it to check the draft against all of the above:

- Is there a goal that no story delivers?
- Is there a story that can't be built or shipped alone once its listed
  dependencies are done, or that depends on something the design never
  provides?
- Does a decision contradict an active decision record without a
  `Replaces:` line naming it?
- Is an open question, risk or unknown written up as a decision, or a
  decision resting on something the design itself lists as unknown?
- Are the options for a major choice genuinely different, each with its
  trade-offs, or variants of one idea or a straw man beside the favourite?

## Findings format

The reviewer answers with one line per finding, most important first:

```
(major) <one-sentence finding, self-contained>
(minor) <one-sentence finding, self-contained>
```

or `No findings.` if there's nothing to report. Nothing else in the reply.

`major`: a goal no story delivers, a story that can't be built or shipped
alone, a contradiction with an active decision record that isn't replaced,
an open question passed off as a decision, or options that aren't genuinely
different. `minor`: everything else worth recording: a missing trade-off, an
unclear story outcome, a diagram that would help.

The reviewer doesn't fix anything. `design` fixes what it can in the draft,
re-runs `vault.py validate` after fixing, and folds the rest into the
summary's judgment calls.
