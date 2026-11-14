---
name: reviewer-evals
description: 'Run the reviewer evals of this repo for one reviewer prompt (implement, plan or design): launch the reviewer on Opus three times per case, save the replies under evals/out/, show the scores beside the baseline and offer to record them. Use for requests like "run the reviewer evals for implement" or "eval the plan reviewer", in a session inside the agent-vault clone.'
---

# reviewer-evals

A project skill of this repo only: `install.py` never copies it. It measures one
reviewer prompt against its cases under `evals/<reviewer>/`: the reviewer runs
on Opus `score.RUNS_PER_CASE` times per case (in `evals/score.py`, three), each
reply is saved verbatim, and `evals/score.py` scores them against the cases'
`expect.json` and the baseline in `evals/baseline.json`. The score is advice:
nothing blocks on it, and a result below the baseline is for the user to judge.

Run every command below from the clone's root.

## Procedure

1. **One reviewer.** `implement`, `plan` or `design`. If the request names none,
   or more than one, ask which one; don't guess.
2. **Check the cases.** `python evals/score.py --check <reviewer>`. If it prints
   a problem (a malformed case, too many cases, no cases), stop and report it;
   run no case.
3. **Clear the old replies.** Delete that reviewer's `evals/out/<reviewer>/`
   folder, so no stale reply is scored.
4. **For each case** (`evals/<reviewer>/<case>/`):
   1. Build the prompt into a file outside `evals/` (this session's scratch
      folder): `python evals/prompt.py <reviewer> <case> --out <file>`. It prints
      only the file's path.
   2. Load the file's exact text with `cat` in the Bash tool, never a
      line-numbered or truncated view.
   3. Launch `score.RUNS_PER_CASE` reviewer subagents with an explicit
      `model: "opus"`, each given only that text, unchanged and in full: nothing
      added, no case name, no path, no hint of what to find. If this session
      can't choose a subagent's model, stop and ask the user; never fall back to
      another model. Runs of a case, and cases, may go in parallel.
   4. Save each reply verbatim (exactly what the subagent returned) to
      `evals/out/<reviewer>/<case>/run-<k>.txt`, `<k>` from 1.
5. **Score.** `python evals/score.py <reviewer>` and show its output.
6. **Offer to record it.** Ask the user, as a question, whether to record this
   result as the new baseline. Only on a yes: `python evals/score.py --update
   <reviewer>`, then commit `evals/baseline.json`. On anything else, change
   nothing.
