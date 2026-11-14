"""evals/score.py: the case format, reply scoring, the report, the baseline.

The scorer is loaded by path (evals/ is not a package and stays out of test
discovery); every test works in its own temp folder."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.helpers import REPO

_spec = importlib.util.spec_from_file_location("evals_score", REPO / "evals" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)

DEFECT_EXPECT = {
    "kind": "defect",
    "defects": [
        {"name": "off by one", "patterns": ["off-by-one", "one too many"]},
        {"name": "no test", "patterns": ["untested"]},
    ],
}
CLEAN_EXPECT = {"kind": "clean"}


def write_case(root: Path, reviewer: str, name: str, expect=None, **over) -> Path:
    """A well-formed case; `over` maps a file name to its content (None removes it)."""
    case = root / reviewer / name
    case.mkdir(parents=True)
    for f in score.MATERIAL[reviewer]:
        (case / f).write_text("material\n", encoding="utf-8")
    (case / "expect.json").write_text(json.dumps(expect or CLEAN_EXPECT), encoding="utf-8")
    for f, content in over.items():
        if content is None:
            (case / f).unlink()
        elif isinstance(content, bytes):
            (case / f).write_bytes(content)
        else:
            (case / f).write_text(content, encoding="utf-8")
    return case


def save_runs(root: Path, reviewer: str, case: str, replies: list) -> None:
    folder = root / "out" / reviewer / case
    folder.mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(replies, 1):
        (folder / ("run-%d.txt" % i)).write_text(text, encoding="utf-8")


def run_main(argv, root):
    out = io.StringIO()
    with redirect_stdout(out):
        code = score.main(argv, root)
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.root = self.tmp / "evals"
        self.root.mkdir()

    def copy_reviewer_files(self, text=None):
        for rel in score.REVIEWER_FILES.values():
            dest = self.tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((REPO / rel).read_bytes() if text is None else text)


class CaseFormat(Base):
    """T1 -> AC1."""

    def problems(self, reviewer="implement", **over):
        case = write_case(self.root, reviewer, "c1", **over)
        return score.check_case(case, reviewer)

    def test_well_formed_case_of_each_reviewer(self):
        for i, reviewer in enumerate(score.MATERIAL):
            case = write_case(self.root, reviewer, "ok-%d" % i)
            self.assertEqual(score.check_case(case, reviewer), [])
        defect = write_case(self.root, "plan", "d1", expect={**DEFECT_EXPECT, "about": "x"})
        self.assertEqual(score.check_case(defect, "plan"), [])

    def test_one_problem_each(self):
        bad_expect = [
            ("expect.json missing", {"expect.json": None}),
            ("expect.json not JSON", {"expect.json": "{nope"}),
            ("expect.json not an object", {"expect.json": "[1]"}),
            ("unknown kind", {"expect.json": json.dumps({"kind": "other"})}),
            ("unknown key", {"expect.json": json.dumps({"kind": "clean", "x": 1})}),
            ("defect with no defects", {"expect.json": json.dumps({"kind": "defect"})}),
            ("empty patterns", {"expect.json": json.dumps(
                {"kind": "defect", "defects": [{"name": "a", "patterns": []}]})}),
            ("pattern does not compile", {"expect.json": json.dumps(
                {"kind": "defect", "defects": [{"name": "a", "patterns": ["("]}]})}),
            ("defect without name", {"expect.json": json.dumps(
                {"kind": "defect", "defects": [{"patterns": ["a"]}]})}),
            ("clean with defects", {"expect.json": json.dumps(
                {"kind": "clean", "defects": [{"name": "a", "patterns": ["a"]}]})}),
            ("missing material", {"diff.patch": None}),
            ("extra file", {"notes.txt": "x"}),
            ("CLAUDE.md", {"CLAUDE.md": "x"}),
            ("empty material", {"diff.patch": "  \n"}),
            ("not UTF-8", {"diff.patch": b"\xff\xfe\x00bad"}),
        ]
        for label, over in bad_expect:
            with self.subTest(label):
                shutil_root = tempfile.TemporaryDirectory()
                self.addCleanup(shutil_root.cleanup)
                case = write_case(Path(shutil_root.name), "implement", "c1", **over)
                self.assertEqual(len(score.check_case(case, "implement")), 1,
                                 score.check_case(case, "implement"))

    def test_bad_case_name(self):
        for name in ("Bad", "-x", "a_b"):
            with self.subTest(name):
                case = write_case(self.root, "implement", name)
                self.assertEqual(len(score.check_case(case, "implement")), 1)

    def test_stray_file_in_reviewer_folder(self):
        write_case(self.root, "implement", "c1")
        (self.root / "implement" / "stray.txt").write_text("x", encoding="utf-8")
        probs = score.check_reviewer(self.root, "implement")
        self.assertEqual(len(probs), 1)
        self.assertIn("stray.txt", probs[0])

    def test_dotfiles_ignored(self):
        case = write_case(self.root, "implement", "c1")
        (case / ".DS_Store").write_bytes(b"\x00")
        (self.root / "implement" / ".DS_Store").write_bytes(b"\x00")
        self.assertEqual(score.check_case(case, "implement"), [])
        self.assertEqual(score.check_reviewer(self.root, "implement"), [])


class ReplyScoring(unittest.TestCase):
    """T2 -> AC2."""

    def test_no_findings(self):
        self.assertEqual(score.score_reply("No findings.\n", CLEAN_EXPECT), (True, ""))
        ok, reason = score.score_reply("No findings.", DEFECT_EXPECT)
        self.assertFalse(ok)
        self.assertIn("off by one", reason)
        self.assertIn("no test", reason)

    def test_defects_matched_by_major_lines(self):
        reply = "\n  (major) An OFF-BY-ONE in the loop.  \n\n(major) The change is Untested.\n"
        self.assertEqual(score.score_reply(reply, DEFECT_EXPECT), (True, ""))

    def test_any_pattern_of_a_defect_matches(self):
        reply = "(major) one too many\n(major) untested"
        self.assertTrue(score.score_reply(reply, DEFECT_EXPECT)[0])

    def test_minor_line_does_not_count(self):
        reply = "(major) off-by-one\n(minor) untested"
        ok, reason = score.score_reply(reply, DEFECT_EXPECT)
        self.assertFalse(ok)
        self.assertIn("no test", reason)
        self.assertNotIn("off by one", reason)

    def test_clean_case(self):
        self.assertTrue(score.score_reply("(minor) a nit", CLEAN_EXPECT)[0])
        ok, reason = score.score_reply("(minor) a nit\n(major) it breaks", CLEAN_EXPECT)
        self.assertFalse(ok)
        self.assertIn("(major) it breaks", reason)

    def test_format_breaks_fail_whatever_else(self):
        good = "(major) off-by-one\n(major) untested"
        for label, reply in [
            ("code fence", "```\n" + good + "\n```"),
            ("heading", "# Findings\n" + good),
            ("bullet", "- (major) off-by-one\n(major) untested"),
            ("no findings beside a finding", "No findings.\n" + good),
            ("empty", ""),
            ("blank only", " \n\n"),
        ]:
            with self.subTest(label):
                ok, reason = score.score_reply(reply, DEFECT_EXPECT)
                self.assertFalse(ok)
                self.assertNotIn("missed defect", reason)

    def test_reason_is_ascii(self):
        ok, reason = score.score_reply("(major) café — →", CLEAN_EXPECT)
        self.assertFalse(ok)
        reason.encode("ascii")


class Report(Base):
    """T3 -> AC3."""

    def setUp(self):
        super().setUp()
        self.copy_reviewer_files()
        write_case(self.root, "implement", "alpha", expect=DEFECT_EXPECT)
        write_case(self.root, "implement", "beta")
        write_case(self.root, "implement", "gamma")
        save_runs(self.root, "implement", "alpha", [
            "(major) off-by-one\n(major) untested",
            "(major) off-by-one\n(major) untested",
            "(major) off-by-one",
        ])
        save_runs(self.root, "implement", "beta", ["No findings."] * 3)
        (self.root / "baseline.json").write_text(json.dumps({"implement": {
            "file": score.REVIEWER_FILES["implement"], "sha256": "x", "date": "2026-01-01",
            "cases": {"alpha": {"passes": 1, "runs": 3}}}}), encoding="utf-8")

    def test_report(self):
        code, out = run_main([], self.root)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        alpha = next(ln for ln in lines if ln.strip().startswith("alpha"))
        self.assertIn("2/3", alpha)
        self.assertIn("baseline 1/3", alpha)
        beta = next(ln for ln in lines if ln.strip().startswith("beta"))
        self.assertIn("3/3", beta)
        self.assertIn("baseline -", beta)
        gamma = next(ln for ln in lines if ln.strip().startswith("gamma"))
        self.assertIn("0/0", gamma)
        self.assertIn("run-3 failed: missed defect(s): 'no test'", out)
        total = next(ln for ln in lines if ln.strip().startswith("total"))
        self.assertIn("5/6", total)
        self.assertIn("baseline 1/3", total)

    def test_unknown_reviewer_exits_2(self):
        code, out = run_main(["nope"], self.root)
        self.assertEqual(code, 2)
        self.assertIn("unknown reviewer", out)

    def test_limit_to_one_reviewer(self):
        write_case(self.root, "plan", "p1")
        _, out = run_main(["implement"], self.root)
        self.assertNotIn("p1", out)

    def test_exit_0_when_everything_fails(self):
        save_runs(self.root, "implement", "beta", ["junk"] * 3)
        code, _ = run_main([], self.root)
        self.assertEqual(code, 0)

    def test_output_is_ascii(self):
        save_runs(self.root, "implement", "beta", ["(major) a — b → c"])
        code, out = run_main([], self.root)
        self.assertEqual(code, 0)
        out.encode("ascii")
        self.assertIn("\\u2014", out)
        self.assertIn("\\u2192", out)

    def test_status_subprocess_from_other_folder(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        proc = subprocess.run([sys.executable, str(REPO / "evals" / "score.py"), "--status"],
                              cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip())
        proc.stdout.encode("ascii")


class Baseline(Base):
    """T4 -> AC4."""

    def setUp(self):
        super().setUp()
        self.copy_reviewer_files()
        write_case(self.root, "implement", "alpha")
        self.rel = score.REVIEWER_FILES["implement"]

    def write_baseline(self, sha, date="2026-02-03"):
        (self.root / "baseline.json").write_text(json.dumps({"implement": {
            "file": self.rel, "sha256": sha, "date": date, "cases": {}}}), encoding="utf-8")

    def test_current(self):
        self.write_baseline(score.file_hash(self.tmp / self.rel))
        code, out = run_main(["--status"], self.root)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("match", out)

    def test_changed(self):
        self.write_baseline(score.file_hash(self.tmp / self.rel))
        with open(self.tmp / self.rel, "ab") as f:
            f.write(b"more\n")
        for argv in (["--status"], []):
            code, out = run_main(argv, self.root)
            self.assertEqual(code, 0)
            self.assertIn("changed since its baseline (2026-02-03): implement", out)

    def test_no_baseline(self):
        code, out = run_main(["--status"], self.root)
        self.assertEqual(code, 0)
        self.assertIn("no baseline: implement", out)

    def test_hash_ignores_crlf(self):
        a, b = self.tmp / "a.md", self.tmp / "b.md"
        a.write_bytes(b"x\ny\n")
        b.write_bytes(b"x\r\ny\r\n")
        self.assertEqual(score.file_hash(a), score.file_hash(b))
        b.write_bytes(b"x\r\nz\r\n")
        self.assertNotEqual(score.file_hash(a), score.file_hash(b))


class Update(Base):
    """T5 -> AC5."""

    def setUp(self):
        super().setUp()
        self.copy_reviewer_files()
        write_case(self.root, "implement", "alpha")
        write_case(self.root, "implement", "beta")
        save_runs(self.root, "implement", "alpha", ["No findings.", "(major) x"])
        self.path = self.root / "baseline.json"

    def test_update_writes_entry(self):
        other = {"plan": {"file": "f", "sha256": "s", "date": "2026-01-01", "cases": {}}}
        self.path.write_text(json.dumps(other), encoding="utf-8")
        code, out = run_main(["--update", "implement"], self.root)
        self.assertEqual(code, 0)
        self.assertIn("1/2", out)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        data = json.loads(text)
        self.assertEqual(text, json.dumps(data, indent=2, sort_keys=True) + "\n")
        entry = data["implement"]
        self.assertEqual(entry["file"], score.REVIEWER_FILES["implement"])
        self.assertEqual(entry["sha256"], score.file_hash(
            self.tmp / score.REVIEWER_FILES["implement"]))
        import datetime
        self.assertEqual(entry["date"], datetime.date.today().isoformat())
        self.assertEqual(entry["cases"], {"alpha": {"passes": 1, "runs": 2},
                                          "beta": {"passes": 0, "runs": 0}})
        self.assertEqual(data["plan"], other["plan"])

    def test_no_replies_changes_nothing(self):
        import shutil
        shutil.rmtree(self.root / "out")
        before = '{"plan": {}}\n'
        self.path.write_text(before, encoding="utf-8")
        code, out = run_main(["--update", "implement"], self.root)
        self.assertEqual(code, 1)
        self.assertTrue(out.strip())
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_no_replies_creates_no_file(self):
        import shutil
        shutil.rmtree(self.root / "out")
        code, _ = run_main(["--update", "implement"], self.root)
        self.assertEqual(code, 1)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
