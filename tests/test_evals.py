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
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.helpers import REPO, make_vault, tmpdir

_spec = importlib.util.spec_from_file_location("evals_score", REPO / "evals" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)
_pspec = importlib.util.spec_from_file_location("evals_prompt", REPO / "evals" / "prompt.py")
prompt = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(prompt)

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


class RealCases(Base):
    """T6 -> AC6."""

    def test_real_reviewer_folders_are_well_formed(self):
        for reviewer in score.REVIEWER_FILES:
            with self.subTest(reviewer):
                self.assertEqual(score.check_reviewer(REPO / "evals", reviewer), [])
                self.assertEqual(score.check_limit(REPO / "evals", reviewer), [])

    def test_map_names_existing_files(self):
        for reviewer, rel in score.REVIEWER_FILES.items():
            with self.subTest(reviewer):
                self.assertTrue((REPO / rel).is_file(), rel)

    def test_baseline_is_an_object(self):
        data = json.loads((REPO / "evals" / "baseline.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_limit_check_fails_over_the_limit(self):
        for i in range(score.MAX_REVIEWS // score.RUNS_PER_CASE + 1):
            write_case(self.root, "implement", "c%d" % i)
        self.assertEqual(len(score.check_limit(self.root, "implement")), 1)
        self.assertEqual(score.check_limit(self.root, "plan"), [])


class RepoWiring(Base):
    """T7 -> AC7."""

    def test_implement_samples(self):
        kinds = set()
        for case in score.case_dirs(REPO / "evals", "implement"):
            kinds.add(json.loads((case / "expect.json").read_text(encoding="utf-8"))["kind"])
        self.assertEqual(kinds, {"defect", "clean"})

    def test_out_is_ignored(self):
        lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("evals/out/", [ln.strip() for ln in lines])

    def test_install_copies_nothing_from_evals(self):
        from tests import test_install as ti
        t = tmpdir()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        home, vault = root / "home", make_vault(root)
        (home / ".claude").mkdir(parents=True)
        fake_repo = root / "repo"
        (fake_repo / ".git").mkdir(parents=True)
        ti.install.install(vault, home, repo=fake_repo)
        self.assertEqual([p for p in home.rglob("*") if "evals" in p.parts or p.name == "score.py"], [])
        self.assertEqual([p for p in vault.rglob("score.py")], [])

    def test_claude_md_layout_line(self):
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        layout = text.split("## Layout", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`evals/`", layout)
        self.assertIn("evals/**", layout.splitlines()[1])
        self.assertNotIn("`evals/out/`", layout)

REVIEWER_TEXT = (
    "# reviewer\n\nIntro \u2014 text.\n\n## Requesting it\n\nLaunch it.\n\n"
    "## Prompt\n\nGive it everything \u2014 in full.\n\n## Findings format\n\n"
    "```\n## not a heading\n(major) x\n```\n\nOne line each.\n\n## After\n\nTail text.\n")


class PromptBuilder(Base):
    """T1 -> AC1 (the prompt builder and score.py --check)."""

    def setUp(self):
        super().setUp()
        for rel in score.REVIEWER_FILES.values():
            dest = self.tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(REVIEWER_TEXT.encode("utf-8"))
        self.expect = {"kind": "defect", "about": "hand-written: secret-about",
                       "defects": [{"name": "secretdefect", "patterns": ["secretpattern"]}]}

    def test_sections_opening_and_material_order(self):
        for reviewer in ("implement", "plan", "design"):
            with self.subTest(reviewer):
                case = write_case(self.root, reviewer, "the-case", expect=self.expect)
                for i, f in enumerate(score.MATERIAL[reviewer]):
                    (case / f).write_text("BODY-%d of %s\n" % (i, f), encoding="utf-8")
                text = prompt.build(reviewer, "the-case", self.root, self.tmp)
                self.assertIn("Judge from that text alone", text)
                self.assertIn("open no file", text)
                self.assertIn("run no command", text)
                self.assertIn("## Prompt\n\nGive it everything \u2014 in full.", text)
                self.assertIn("## Findings format\n\n```\n## not a heading\n(major) x\n```"
                              "\n\nOne line each.", text)
                self.assertNotIn("Tail text", text)
                self.assertNotIn("Launch it.", text)
                pos = [text.index("BODY-%d of %s" % (i, f))
                       for i, f in enumerate(score.MATERIAL[reviewer])]
                self.assertEqual(pos, sorted(pos))
                self.assertGreater(min(pos), text.index("## Findings format"))
                for leak in ("the-case", str(self.root), "expect.json", "secretpattern",
                             "secretdefect", "secret-about"):
                    self.assertNotIn(leak, text)

    def test_errors_exit_2_and_write_no_file(self):
        write_case(self.root, "implement", "good", expect=self.expect)
        write_case(self.root, "implement", "nomaterial", **{"diff.patch": None})
        for argv in (["nope", "good"], ["implement", "missing"],
                     ["implement", "nomaterial"], ["implement", "../good"]):
            with self.subTest(argv):
                out = self.tmp / "out.txt"
                err = io.StringIO()
                with redirect_stderr(err):
                    code = prompt.main(argv + ["--out", str(out)], self.root, self.tmp)
                self.assertEqual(code, 2)
                self.assertTrue(err.getvalue().strip())
                self.assertFalse(out.exists())

    def test_subprocess_writes_utf8_and_prints_the_path(self):
        case = score.case_dirs(REPO / "evals", "implement")[0].name
        out = self.tmp / "p.txt"
        proc = subprocess.run([sys.executable, str(REPO / "evals" / "prompt.py"),
                               "implement", case, "--out", str(out)],
                              capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), str(out))
        text = out.read_bytes().decode("utf-8")
        self.assertIn("\u2014", text)
        self.assertNotIn("expect.json", text)
        bad = subprocess.run([sys.executable, str(REPO / "evals" / "prompt.py"),
                              "implement", "no-such-case", "--out", str(self.tmp / "q.txt")],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertFalse((self.tmp / "q.txt").exists())

    def test_check(self):
        write_case(self.root, "implement", "ok")
        code, out = run_main(["--check", "implement"], self.root)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        write_case(self.root, "implement", "bad", **{"diff.patch": None})
        code, out = run_main(["--check", "implement"], self.root)
        self.assertEqual(code, 1)
        self.assertIn("missing diff.patch", out)
        code, out = run_main(["--check", "plan"], self.root)
        self.assertEqual(code, 1)
        self.assertIn("no cases", out)
        for i in range(9):
            write_case(self.root, "design", "c%d" % i)
        code, out = run_main(["--check", "design"], self.root)
        self.assertEqual(code, 1)
        self.assertIn("over the limit", out)
        self.assertEqual(run_main(["--check"], self.root)[0], 2)


class ReviewerEvalsSkill(Base):
    """T2 -> AC2."""

    PATH = REPO / ".claude" / "skills" / "reviewer-evals" / "SKILL.md"

    def test_frontmatter_and_text(self):
        text = self.PATH.read_text(encoding="utf-8")
        head = text.split("---")[1]
        self.assertIn("name: reviewer-evals", head)
        self.assertIn("run the reviewer evals", head)
        for needle in ("evals/prompt.py", "--out", 'model: "opus"', "evals/out/",
                       "run-<k>.txt", "RUNS_PER_CASE", "python evals/score.py", "--check",
                       "--update", "ask which one", "stop and report", "Delete that reviewer's",
                       "never fall back", "unchanged and in full", "Ask the user",
                       "nothing blocks on it", "never a\n      line-numbered"):
            with self.subTest(needle):
                self.assertIn(needle, text)

    def test_install_creates_no_such_skill(self):
        from tests import test_install as ti
        t = tmpdir()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        home, vault = root / "home", make_vault(root)
        (home / ".claude").mkdir(parents=True)
        fake_repo = root / "repo"
        (fake_repo / ".git").mkdir(parents=True)
        ti.install.install(vault, home, repo=fake_repo)
        self.assertTrue((home / ".claude" / "skills").is_dir())
        self.assertEqual([p for p in home.rglob("*") if "reviewer-evals" in p.parts], [])


class RepoGuideEvals(Base):
    """T4 -> AC4."""

    def test_claude_md_mentions(self):
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        for needle in ("reviewer-evals skill", "python evals/score.py --status",
                       ".claude/skills/reviewer-evals/", "evals/prompt.py"):
            with self.subTest(needle):
                self.assertIn(needle, text)
        layout = text.split("## Layout", 1)[1]
        self.assertIn(".claude/skills/**", layout.splitlines()[1])


if __name__ == "__main__":
    unittest.main()
