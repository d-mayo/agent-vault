# implement-story reviewer

The prompt `implement-story` sends to an independent reviewer subagent over
the whole diff once all steps are committed (again after each round of fixes,
up to three reviews), and once over all the fixes made while addressing PR
review. The reviewer runs on a stronger model than the implementer (Opus),
because judgment is where the stronger model pays off; the implementer's own
session model is never switched.

## Requesting it

Launch it as an agent with `model: "opus"` — an explicit override, never the
implementer's own model. If the tool available in this session has no way to
choose a subagent's model, stop and ask the user instead of reviewing on the
implementer's own model (`implement-story`'s own "Stop and ask if").

## Prompt

Give the reviewer, verbatim and in full:

1. What's under review: the whole diff, against every acceptance criterion
   below, not just the last commit. For PR-review fixes, also the request
   texts (the comments or review) the fixes answer, and which commit to look
   at first if one fix is large.
2. The plan's `## Acceptance criteria`.
3. The repo's `CLAUDE.md`, in full.
4. The diff: `git diff <base>..HEAD` for the end review, `git diff
   <tip>..HEAD` (the branch tip before the first fix) for PR-review fixes.
5. The exact instructions under "Findings format" below — the severity
   definitions, the reporting limit and the requirement to answer with
   nothing but those lines.

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

It reports every major finding and at most three minor ones, most important
first; it drops the rest.

`major`: the diff doesn't do what it claims, breaks an acceptance criterion
it touches, or contradicts the repo `CLAUDE.md`. `minor`: everything else
worth recording — a missed edge case, a simplification, a small
inconsistency.

The reviewer doesn't number or resolve its own findings, doesn't tag them,
and doesn't know the eventual fix's commit sha — `implement-story` adds all
of that, continuing the numbering already in the impl note's
`## Review findings`:

```
- R<n> (major|minor): Review <k>: <finding> → fixed in <sha>
- R<n> (major|minor): Review <k>: <finding> → won't fix: <reason>
- R<n> (minor): Review <k>: <finding> → left open
- R<n> (major|minor): PR review: <finding> → fixed in <sha>
```

A review that comes back `No findings.` is recorded as a `## Verification`
line (`- Review <k>: no findings`), not under `## Review findings`; the
`Review <k>:` lines are also how a later session counts the rounds already
used.
