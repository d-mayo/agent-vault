"""Builds the full prompt for one eval review of one case.

Usage:
    python evals/prompt.py <reviewer> <case> --out <file>

Writes the prompt to <file> as UTF-8 and prints only that path (so no console
encoding can garble the text). The prompt is built only from the reviewer file
named in score.REVIEWER_FILES and the case's material files (score.MATERIAL);
it never carries the case's path, its name or expect.json. Exit 2 with a message
(and no file) for an unknown reviewer, a missing case or a malformed case.
Standard library only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("evals_score", _HERE / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)

HEADINGS = {
    "acceptance-criteria.md": "Acceptance criteria",
    "repo-guide.md": "Repo guide (the repo's CLAUDE.md)",
    "diff.patch": "Diff",
    "plan.md": "Plan",
    "issue.md": "Issue",
    "context.md": "Context",
    "overview.md": "Project overview",
    "decisions.md": "Decisions",
    "design.md": "Design",
    "research.md": "Research",
}

SECTIONS = ("Prompt", "Findings format")


class PromptError(Exception):
    pass


def section(text, title):
    """The body of the `## <title>` section (heading included), fence-aware."""
    lines = text.replace("\r\n", "\n").split("\n")
    out, inside, fence = [], False, False
    for line in lines:
        if line.startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            if inside:
                break
            inside = line.strip() == "## " + title
        if inside:
            out.append(line)
    if not out:
        raise PromptError("the reviewer file has no '## %s' section" % title)
    return "\n".join(out).rstrip("\n")


def build(reviewer, case, evals_root=None, repo_root=None):
    """The prompt text for one review of `case`; raises PromptError."""
    root = Path(evals_root) if evals_root is not None else _HERE
    repo = Path(repo_root) if repo_root is not None else root.parent
    if reviewer not in score.REVIEWER_FILES:
        raise PromptError("unknown reviewer %s (one of: %s)"
                          % (ascii(reviewer), ", ".join(score.REVIEWER_FILES)))
    case_dir = root / reviewer / case
    if not score.CASE_NAME.match(case) or not case_dir.is_dir():
        raise PromptError("no case %s for reviewer %s" % (ascii(case), reviewer))
    problems = score.check_case(case_dir, reviewer)
    if problems:
        raise PromptError("malformed case: " + "; ".join(problems))
    rfile = repo / score.REVIEWER_FILES[reviewer]
    try:
        rtext = rfile.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise PromptError("cannot read %s: %s" % (score.REVIEWER_FILES[reviewer], e))
    material = score.MATERIAL[reviewer]
    listing = ", ".join(HEADINGS[m] for m in material)
    parts = [
        "You are the reviewer that the text below describes. Everything you need "
        "is pasted in full below: %s. Judge from that text alone: open no file "
        "other than this one and run no command. The two sections that follow are the instructions "
        "for your review (they are worded for whoever launches you; the "
        "material they list is already pasted below). Answer with nothing but the findings, in the "
        "format given under \"Findings format\"." % listing,
    ]
    for title in SECTIONS:
        parts.append(section(rtext, title))
    for name in material:
        body = (case_dir / name).read_bytes().decode("utf-8")
        parts.append("# %s\n\n%s" % (HEADINGS[name], body.rstrip("\n")))
    return "\n\n".join(parts) + "\n"


def main(argv=None, evals_root=None, repo_root=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 4 or argv[2] != "--out":
        print("usage: prompt.py <reviewer> <case> --out <file>", file=sys.stderr)
        return 2
    reviewer, case, _, out = argv
    try:
        text = build(reviewer, case, evals_root, repo_root)
    except PromptError as e:
        sys.stderr.write(str(e).encode("ascii", "backslashreplace").decode("ascii") + "\n")
        return 2
    Path(out).write_bytes(text.encode("utf-8"))
    sys.stdout.write(str(out).encode("ascii", "backslashreplace").decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
