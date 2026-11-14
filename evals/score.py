"""Scores saved reviewer replies against the eval cases under evals/.

Usage:
    python evals/score.py [<reviewer>]         score replies, baseline beside
    python evals/score.py --status             list reviewer files changed since baseline
    python evals/score.py --update <reviewer>  record the current scores as the baseline

Layout (see the repo CLAUDE.md): cases in evals/<reviewer>/<case>/, saved replies in
evals/out/<reviewer>/<case>/run-<k>.txt (git-ignored), the baseline in evals/baseline.json.
Results are advisory: scoring always exits 0 (only usage errors and an empty --update
exit non-zero). Standard library only.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

RUNS_PER_CASE = 3
MAX_REVIEWS = 24

REVIEWER_FILES = {
    "implement": "skills/implement-story/reviewer.md",
    "plan": "skills/plan-story/plan-reviewer.md",
    "design": "skills/design/design-reviewer.md",
}

MATERIAL = {
    "implement": ["acceptance-criteria.md", "repo-guide.md", "diff.patch"],
    "plan": ["plan.md", "issue.md", "repo-guide.md", "context.md", "overview.md",
             "decisions.md"],
    "design": ["design.md", "overview.md", "decisions.md", "research.md", "repo-guide.md"],
}

CASE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
EXPECT_KEYS = {"kind", "about", "defects"}
DEFAULT_ROOT = Path(__file__).resolve().parent


def _visible(folder):
    return sorted((p for p in folder.iterdir() if not p.name.startswith(".")),
                  key=lambda p: p.name)


def check_case(case_dir, reviewer):
    """Problems with one case folder (empty when well-formed)."""
    case_dir = Path(case_dir)
    problems = []
    label = "%s/%s" % (reviewer, case_dir.name)
    if not CASE_NAME.match(case_dir.name):
        problems.append("%s: bad case name (use [a-z0-9][a-z0-9-]*)" % label)
    wanted = MATERIAL[reviewer] + ["expect.json"]
    present = {p.name: p for p in _visible(case_dir)}
    for name in wanted:
        if name not in present:
            problems.append("%s: missing %s" % (label, name))
    for name in sorted(present):
        if name not in wanted:
            problems.append("%s: unexpected %s" % (label, name))
    for name in MATERIAL[reviewer]:
        path = present.get(name)
        if path is None:
            continue
        if not path.is_file():
            problems.append("%s: %s is not a file" % (label, name))
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            problems.append("%s: %s is not valid UTF-8" % (label, name))
            continue
        if not text.strip():
            problems.append("%s: %s is empty" % (label, name))
    expect = present.get("expect.json")
    if expect is not None:
        problems.extend(_check_expect(expect, label))
    return problems


def _check_expect(path, label):
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, OSError):
        return ["%s: expect.json is not valid JSON" % label]
    if not isinstance(data, dict):
        return ["%s: expect.json is not an object" % label]
    problems = []
    for key in sorted(set(data) - EXPECT_KEYS):
        problems.append("%s: expect.json has unknown key %s" % (label, ascii(key)))
    kind = data.get("kind")
    if kind not in ("defect", "clean"):
        problems.append("%s: expect.json kind must be \"defect\" or \"clean\"" % label)
    if "about" in data and not isinstance(data["about"], str):
        problems.append("%s: expect.json about must be a string" % label)
    if kind == "clean" and "defects" in data:
        problems.append("%s: a clean case has no defects" % label)
    if kind == "defect":
        defects = data.get("defects")
        if not isinstance(defects, list) or not defects:
            problems.append("%s: a defect case needs a non-empty defects list" % label)
        else:
            for i, defect in enumerate(defects, 1):
                problems.extend(_check_defect(defect, "%s: defect %d" % (label, i)))
    return problems


def _check_defect(defect, label):
    if not isinstance(defect, dict):
        return ["%s is not an object" % label]
    problems = []
    for key in sorted(set(defect) - {"name", "patterns"}):
        problems.append("%s has unknown key %s" % (label, ascii(key)))
    name = defect.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("%s needs a non-empty name" % label)
    patterns = defect.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        problems.append("%s needs a non-empty patterns list" % label)
        return problems
    for pat in patterns:
        if not isinstance(pat, str) or not pat:
            problems.append("%s has a pattern that is not a non-empty string" % label)
            continue
        try:
            re.compile(pat)
        except re.error:
            problems.append("%s has a pattern that does not compile: %s" % (label, ascii(pat)))
    return problems


def case_dirs(root, reviewer):
    folder = Path(root) / reviewer
    if not folder.is_dir():
        return []
    return [p for p in _visible(folder) if p.is_dir()]


def check_reviewer(root, reviewer):
    """Problems with a whole reviewer folder, stray files included."""
    folder = Path(root) / reviewer
    if not folder.is_dir():
        return []
    problems = []
    for p in _visible(folder):
        if not p.is_dir():
            problems.append("%s: stray file %s" % (reviewer, p.name))
        else:
            problems.extend(check_case(p, reviewer))
    return problems


def check_limit(root, reviewer):
    """Problems when the reviewer's cases need more than MAX_REVIEWS reviews."""
    n = len(case_dirs(root, reviewer))
    if n * RUNS_PER_CASE > MAX_REVIEWS:
        return ["%s: %d cases x %d runs = %d reviews, over the limit of %d"
                % (reviewer, n, RUNS_PER_CASE, n * RUNS_PER_CASE, MAX_REVIEWS)]
    return []


def _format_problem(lines):
    if not lines:
        return "the reply is empty"
    if lines == ["No findings."]:
        return None
    for line in lines:
        if not (line.startswith("(major) ") or line.startswith("(minor) ")):
            return "malformed line: %s" % ascii(line)
    return None


def score_reply(text, expect):
    """(passed, reason) for one reply; reason is '' when it passed."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bad = _format_problem(lines)
    if bad:
        return False, bad
    majors = [ln for ln in lines if ln.startswith("(major) ")]
    if expect["kind"] == "clean":
        if majors:
            return False, "major finding on a clean case: %s" % ascii(majors[0])
        return True, ""
    missed = []
    for defect in expect["defects"]:
        regexes = [re.compile(p, re.IGNORECASE) for p in defect["patterns"]]
        if not any(r.search(m) for m in majors for r in regexes):
            missed.append(defect["name"])
    if missed:
        return False, "missed defect(s): %s" % ", ".join(ascii(n) for n in missed)
    return True, ""


def _run_files(root, reviewer, case):
    folder = Path(root) / "out" / reviewer / case
    if not folder.is_dir():
        return []
    return sorted(folder.glob("run-*.txt"), key=lambda p: p.name)


def score_case(root, reviewer, case_dir):
    """(passes, runs, [(run name, reason)]) from the saved replies of one case."""
    expect = json.loads((case_dir / "expect.json").read_bytes().decode("utf-8"))
    passes, failures = 0, []
    files = _run_files(root, reviewer, case_dir.name)
    for f in files:
        ok, reason = score_reply(f.read_bytes().decode("utf-8", "replace"), expect)
        if ok:
            passes += 1
        else:
            failures.append((f.stem, reason))
    return passes, len(files), failures


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_baseline(root):
    path = Path(root) / "baseline.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("baseline.json is not an object")
    return data


def status_lines(root, reviewers, baseline):
    repo = Path(root).parent
    lines = []
    for reviewer in reviewers:
        if not case_dirs(root, reviewer):
            continue
        entry = baseline.get(reviewer)
        if not isinstance(entry, dict):
            lines.append("no baseline: %s" % reviewer)
        elif entry.get("sha256") != file_hash(repo / REVIEWER_FILES[reviewer]):
            lines.append("changed since its baseline (%s): %s (%s)"
                         % (entry.get("date", "?"), reviewer, REVIEWER_FILES[reviewer]))
    return lines or ["all reviewer files match their baselines"]


def _emit(text):
    sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def _frac(entry):
    if not isinstance(entry, dict) or "passes" not in entry or "runs" not in entry:
        return "-"
    return "%s/%s" % (entry["passes"], entry["runs"])


def report(root, reviewers, baseline):
    for reviewer in reviewers:
        cases = case_dirs(root, reviewer)
        if not cases:
            continue
        entry = baseline.get(reviewer)
        base_cases = entry.get("cases", {}) if isinstance(entry, dict) else {}
        _emit("%s (%s)" % (reviewer, REVIEWER_FILES[reviewer]))
        total_p = total_r = 0
        for case in cases:
            passes, runs, failures = score_case(root, reviewer, case)
            total_p += passes
            total_r += runs
            _emit("  %-30s %s   baseline %s"
                  % (case.name, "%d/%d" % (passes, runs), _frac(base_cases.get(case.name))))
            for run, reason in failures:
                _emit("    %s failed: %s" % (run, reason))
        base_total = "-"
        if base_cases:
            bp = sum(c.get("passes", 0) for c in base_cases.values())
            br = sum(c.get("runs", 0) for c in base_cases.values())
            base_total = "%d/%d" % (bp, br)
        _emit("  %-30s %s   baseline %s" % ("total", "%d/%d" % (total_p, total_r), base_total))


def update(root, reviewer, today=None):
    """Rewrite one reviewer's baseline entry; returns (exit code, message)."""
    cases = {}
    saved = False
    for case in case_dirs(root, reviewer):
        passes, runs, _ = score_case(root, reviewer, case)
        saved = saved or runs > 0
        cases[case.name] = {"passes": passes, "runs": runs}
    if not saved:
        return 1, "no saved replies for any %s case; baseline unchanged" % reviewer
    baseline = load_baseline(root)
    baseline[reviewer] = {
        "file": REVIEWER_FILES[reviewer],
        "sha256": file_hash(Path(root).parent / REVIEWER_FILES[reviewer]),
        "date": today or datetime.date.today().isoformat(),
        "cases": cases,
    }
    text = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    (Path(root) / "baseline.json").write_bytes(text.encode("utf-8"))
    tp = sum(c["passes"] for c in cases.values())
    tr = sum(c["runs"] for c in cases.values())
    return 0, "baseline for %s updated: %d/%d over %d case(s)" % (reviewer, tp, tr, len(cases))


def main(argv=None, root=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(root) if root is not None else DEFAULT_ROOT
    flag = None
    if argv and argv[0] in ("--status", "--update"):
        flag = argv.pop(0)
    if len(argv) > 1 or (flag == "--update" and not argv) or (flag == "--status" and argv):
        _emit("usage: score.py [<reviewer>] | --status | --update <reviewer>")
        return 2
    if argv and argv[0] not in REVIEWER_FILES:
        _emit("unknown reviewer %s (one of: %s)" % (ascii(argv[0]), ", ".join(REVIEWER_FILES)))
        return 2
    reviewers = [argv[0]] if argv else list(REVIEWER_FILES)
    if flag == "--update":
        code, message = update(root, reviewers[0])
        _emit(message)
        return code
    try:
        baseline = load_baseline(root)
    except (ValueError, OSError) as e:
        _emit("cannot read baseline.json: %s" % e)
        return 2
    if flag != "--status":
        report(root, reviewers, baseline)
        _emit("")
    for line in status_lines(root, reviewers, baseline):
        _emit(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
