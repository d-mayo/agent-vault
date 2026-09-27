# implement-story reviewer

The prompt `implement-story` sends to an independent reviewer subagent after
every step and once more over the whole PR diff before opening it, and again
for each fix made while addressing PR review. The reviewer runs on a
stronger model than the implementer (Opus), because judgment is where the
stronger model pays off; the implementer's own session model is never
switched.

## Requesting it

Launch it as an agent with `model: "opus"` — an explicit override, never the
implementer's own model. If the tool available in this session has no way to
choose a subagent's model, stop and ask the user instead of reviewing on the
implementer's own model (`implement-story`'s own "Stop and ask if").

## Prompt

Give the reviewer, verbatim and in full:

1. What's under review:
   - a step: its `Files:`, `Do:` and `Done when:` lines;
   - the final review: "the whole PR diff, against every acceptance
     criterion below, not just the last step";
   - a PR-review fix: the request that was made (the comment or review text)
     and what it's being checked against, since there's no `Files:`/`Do:`
     for it.
2. The plan's `## Acceptance criteria`.
3. The repo's `CLAUDE.md`, in full.
4. The diff: `git diff <from>..<to>` — exactly this step's (or fix's) commit
   for a per-step or PR-review review, `<base>..HEAD` for the final review.
5. The exact instructions under "Findings format" below — the severity
   definitions and the requirement to answer with nothing but those lines.

Ask it to check the diff against all of the above: does it do what was
asked, does it hold every acceptance criterion it touches, does it follow
the repo's own conventions — and nothing else. No style opinions the repo
`CLAUDE.md` doesn't already state, and no re-reviewing anything outside this
diff.

## Findings format

The reviewer answers with one line per finding, most important first:

```
(major) <one-sentence finding, self-contained>
(minor) <one-sentence finding, self-contained>
```

or `No findings.` if there's nothing to report. Nothing else in the reply.

`major`: the diff doesn't do what it claims, breaks an acceptance criterion
it touches, or contradicts the repo `CLAUDE.md`. `minor`: everything else
worth recording — a missed edge case, a simplification, a small
inconsistency.

The reviewer doesn't number or resolve its own findings, doesn't tag them
with a step, and doesn't know the eventual fix's commit sha —
`implement-story` adds all of that once it has acted on each one, continuing
the numbering already in the impl note's `## Review findings`:

```
- R<n> (major|minor): Step <s>: <finding> → fixed in <sha>
- R<n> (major|minor): Final review: <finding> → won't fix: <reason>
- R<n> (major|minor): PR review: <finding> → fixed in <sha>
```

A review that comes back `No findings.` still needs recording — not as a
`## Review findings` line (there's no finding to put there), but as a
`## Verification` line (`- Step <s> reviewed: no findings`,
`- Final review: no findings`, or `- PR review reviewed: no findings`), so a
later session can tell a step that was reviewed clean from one that was
never reviewed at all.
