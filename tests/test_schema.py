"""Schema v2: rule fixtures, rule-coverage meta-test, and per-AC checks.

Fixtures live in tests/fixtures/schema/<RULE-ID>/<pass|fail>[-label]/ and are
overlays on tests/fixtures/schema/_base (a small valid vault): files in the
overlay replace or add to the base, paths relative to the vault root.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import CODE, make_vault, run_py, tmpdir, write_config

sys.path.insert(0, str(CODE))
import lib  # noqa: E402
import vault  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "schema"
CLI = CODE / "vault.py"
HOOKS = CODE / "hooks"


class VaultCase(unittest.TestCase):
    """A temp vault, with lib pointed at it in-process."""

    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.vault = make_vault(self.root)
        self.cfg = write_config(self.root, self.vault)
        self._patches = [mock.patch.object(lib, "VAULT", self.vault),
                         mock.patch.object(lib, "AGENT", self.vault / "Agent")]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._t.cleanup()

    def put(self, relpath: str, text: str) -> Path:
        path = self.vault / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def load_base(self):
        shutil.copytree(FIXTURES / "_base", self.vault, dirs_exist_ok=True)

    def check(self):
        return lib.validate_all()

    def all_ids(self, found: dict) -> set:
        return {m[1:m.index("]")] for msgs in found.values() for m in msgs}


class RuleFixturesTest(VaultCase):
    def cases(self):
        out = []
        for rule_dir in sorted(p for p in FIXTURES.iterdir() if p.is_dir() and p.name != "_base"):
            for case in sorted(p for p in rule_dir.iterdir() if p.is_dir()):
                out.append((rule_dir.name, case))
        return out

    def run_fixture(self, case: Path):
        self.load_base()
        shutil.copytree(case, self.vault, dirs_exist_ok=True)
        return self.check()

    def test_base_is_valid(self):     # T2 -> AC2
        self.load_base()
        self.assertEqual(self.check(), ({}, {}))

    def test_fixtures(self):          # T2 -> AC2
        for rule, case in self.cases():
            with self.subTest(fixture=f"{rule}/{case.name}"):
                shutil.rmtree(self.vault / "Agent", ignore_errors=True)
                errors, warnings = self.run_fixture(case)
                found = self.all_ids(errors) | self.all_ids(warnings)
                if case.name.startswith("pass"):
                    self.assertEqual(errors, {})
                    self.assertNotIn(rule, found)
                elif case.name.startswith("fail"):
                    self.assertIn(rule, found, f"{errors} {warnings}")
                else:
                    self.fail(f"fixture dir must start with pass or fail: {case}")

    def test_every_rule_has_pass_and_fail_fixture(self):    # T2 -> AC2
        have = {}
        for rule, case in self.cases():
            have.setdefault(rule, set()).add(case.name.split("-")[0])
        for rule in lib.RULES:
            with self.subTest(rule=rule):
                self.assertEqual(have.get(rule), {"pass", "fail"}, f"{rule} needs pass and fail fixtures")
        self.assertEqual(set(have) - set(lib.RULES), set(), "fixtures for unknown rules")

    def test_rule_ids_in_messages_exist(self):
        self.load_base()
        for rule in lib.RULES:
            self.assertIn(lib.RULES[rule][0], ("error", "warning"))


class SchemaOutputTest(VaultCase):
    def out(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vault.cmd_schema(None)
        return buf.getvalue()

    def test_contains_constants(self):       # T1 -> AC1
        text = self.out()
        for t in lib.TYPES:
            self.assertIn(lib.PATHS[t], text)
            for f in lib.REQUIRED[t] + lib.OPTIONAL[t]:
                self.assertIn(f, text)
            for s in lib.SECTIONS[t]:
                self.assertIn(f"## {s}", text)
            for v in lib.STATUSES.get(t, []):
                self.assertIn(v, text)
        for cap in lib.SIZE_CAPS.values():
            self.assertIn(str(cap), text)
        for ev in lib.EVENTS + lib.IDEA_EVENTS:
            self.assertIn(ev, text)
        for rules in lib.LINE_FORMATS.values():
            for shape, _ in rules:
                self.assertIn(shape, text)
        for rid in lib.RULES:
            self.assertIn(rid, text)

    def test_patching_a_constant_changes_output(self):   # T1 -> AC1
        with mock.patch.dict(lib.SIZE_CAPS, {"project": 61}), \
                mock.patch.object(lib, "EVENTS", ["zzz-event"]), \
                mock.patch.dict(lib.RULES, {"NEW-RULE": ("error", "a brand new rule")}):
            text = self.out()
        self.assertIn("body cap: 61 lines", text)
        self.assertIn("zzz-event", text)
        self.assertIn("NEW-RULE", text)
        self.assertNotIn("zzz-event", self.out())


class LayoutAndFrontmatterTest(VaultCase):
    def setUp(self):
        super().setUp()
        self.load_base()

    def problems(self, relpath: str, text: str):
        self.put(relpath, text)
        res = lib.validate_file(self.vault / relpath)
        return res

    def test_valid_paths_for_all_types(self):     # T3 -> AC3
        self.assertEqual(self.check(), ({}, {}))
        types = set()
        for p in (self.vault / "Agent").rglob("*.md"):
            fm, _, _ = lib.split_frontmatter(p.read_text(encoding="utf-8"))
            types.add(fm["type"])
        self.assertEqual(types, set(lib.TYPES))

    def test_stray_and_wrong_names(self):         # T3 -> AC3
        idea = (self.vault / "Agent/Ideas/2026-09-26-an-idea.md").read_text(encoding="utf-8")
        for rel in ("Agent/Notes/x.md", "Agent/Intake/x.md", "Agent/Archive/x.md", "Agent/x.md"):
            with self.subTest(rel=rel):
                self.assertTrue(self.problems(rel, idea).errors[0].startswith("[PATH-STRAY]"))
        self.assertTrue(self.problems("Agent/Ideas/x.md", idea).errors[0].startswith("[PATH-NAME]"))

    def test_dotfiles_ignored(self):
        self.put("Agent/.handoff-log.json", "{}")
        self.assertEqual(self.check(), ({}, {}))

    def test_frontmatter_cases(self):              # T4 -> AC4
        base = (self.vault / "Agent/Projects/demo.md").read_text(encoding="utf-8")
        for label, old, new, rule in [
            ("missing", "status: active\n", "", "FM-REQUIRED"),
            ("unknown", "audited:", "extra: 1\naudited:", "FM-UNKNOWN"),
            ("enum", "status: active", "status: bogus", "FM-ENUM"),
            ("date", "audited: 2026-09-26", "audited: yesterday", "FM-DATE"),
            ("id", "id: demo", "id: nope", "FM-MATCH"),
        ]:
            with self.subTest(label):
                res = self.problems("Agent/Projects/demo.md", base.replace(old, new, 1))
                self.assertTrue(any(m.startswith(f"[{rule}]") for m in res.errors), res.errors)
        self.put("Agent/Projects/demo.md", base)

    def test_promoted_idea_needs_url(self):        # T4 -> AC4
        idea = (self.vault / "Agent/Ideas/2026-09-26-an-idea.md").read_text(encoding="utf-8")
        res = self.problems("Agent/Ideas/2026-09-26-an-idea.md", idea.replace("status: open", "status: promoted"))
        self.assertTrue(any(m.startswith("[FM-COND]") for m in res.errors))


class SectionsAndSizeTest(VaultCase):
    def setUp(self):
        super().setUp()
        self.load_base()

    def body_padded(self, relpath: str, heading: str, total: int) -> Path:
        path = self.vault / relpath
        text = path.read_text(encoding="utf-8")
        n = len(text.split("---\n", 2)[2].splitlines())
        path.write_text(text.replace(f"## {heading}\n", f"## {heading}\n" + "- filler\n" * (total - n), 1),
                        encoding="utf-8", newline="\n")
        return path

    def test_caps_all_types(self):       # T6 -> AC6
        for rel, heading in [("Agent/Projects/demo.md", "Current state"),
                             ("Agent/Work/demo/demo-1-impl.md", "Verification"),
                             ("Agent/Work/demo/demo-1-retro.md", "Summary")]:
            cap = lib.SIZE_CAPS[lib.split_frontmatter((self.vault / rel).read_text(encoding="utf-8"))[0]["type"]]
            with self.subTest(rel=rel):
                path = self.body_padded(rel, heading, cap)
                self.assertEqual(lib.validate_file(path).errors, [])
                path = self.body_padded(rel, heading, cap + 1)
                self.assertTrue(lib.validate_file(path).errors[0].startswith("[SIZE-CAP]"))
        idea = self.vault / "Agent/Ideas/2026-09-26-an-idea.md"
        text = idea.read_text(encoding="utf-8")
        idea.write_text(text + "x\n" * (lib.SIZE_CAPS["idea"] - 2), encoding="utf-8")
        self.assertEqual(lib.validate_file(idea).errors, [])
        idea.write_text(text + "x\n" * (lib.SIZE_CAPS["idea"] - 1), encoding="utf-8")
        self.assertTrue(lib.validate_file(idea).errors[0].startswith("[SIZE-CAP]"))

    def test_long_plan_warns_and_validate_exits_0(self):     # T6 -> AC6
        self.body_padded("Agent/Work/demo/demo-1-plan.md", "Context", 401)
        r = run_py(CLI, ["validate"], config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SIZE-WARN", r.stdout)
        self.assertNotIn("vault OK", r.stdout)

    def test_errors_exit_1(self):
        (self.vault / "Agent" / "x.md").write_text("x", encoding="utf-8")
        r = run_py(CLI, ["validate"], config=self.cfg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PATH-STRAY", r.stdout)


class CliAndHooksTest(VaultCase):
    def test_help_has_no_v1_commands(self):      # T10 -> AC10
        r = run_py(CLI, ["--help"], config=self.cfg)
        self.assertEqual(r.returncode, 0)
        for word in ("archive", "new note", "new daily", "--goal", "--title", "intake", "Intake"):
            self.assertNotIn(word, r.stdout)
        src = (CODE / "vault.py").read_text(encoding="utf-8")
        for word in ("intake", "ARCHIVE", "FOLDERS[\"note\"]"):
            self.assertNotIn(word, src)

    def test_hooks_run_against_v2_vault(self):   # T10 -> AC10
        self.load_base()
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": "t2"}, config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Active projects", r.stdout)
        self.assertNotIn("intake", r.stdout.lower())
        target = self.vault / "Agent/Work/demo/demo-1-impl.md"
        stdin = {"tool_name": "Edit", "cwd": str(self.vault), "tool_input": {"file_path": str(target)}}
        self.assertEqual(run_py(HOOKS / "post_edit.py", stdin=stdin, config=self.cfg).returncode, 0)
        target.write_text(target.read_text(encoding="utf-8").replace("## Discoveries", "## Nope"), encoding="utf-8")
        r = run_py(HOOKS / "post_edit.py", stdin=stdin, config=self.cfg)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SEC-MISSING", r.stderr)
        r = run_py(HOOKS / "stop_check.py", stdin={"session_id": "t2"}, config=self.cfg)
        self.assertIn(r.returncode, (0, 2))       # 2: asks for a log line, as designed
        self.assertNotIn("Traceback", r.stderr)

    def test_post_edit_does_not_block_on_warning(self):
        self.load_base()
        plan = self.vault / "Agent/Work/demo/demo-1-plan.md"
        text = plan.read_text(encoding="utf-8")
        plan.write_text(text.replace("## Context\n", "## Context\n" + "- filler\n" * 400, 1), encoding="utf-8")
        stdin = {"tool_name": "Edit", "cwd": str(self.vault), "tool_input": {"file_path": str(plan)}}
        self.assertEqual(run_py(HOOKS / "post_edit.py", stdin=stdin, config=self.cfg).returncode, 0)


class DraftPlanTest(VaultCase):
    """D1: completeness rules warn on a draft plan and error on a sealed one."""
    PLAN = "Agent/Work/demo/demo-1-plan.md"
    SOFT = [("PLAN-AC", "fail-none"), ("PLAN-STEP", "fail-none"), ("PLAN-T", "fail-none"),
            ("PLAN-COVER", "fail"), ("PLAN-FULL", "fail")]
    HARD = [("PLAN-AC", "fail-duplicate"), ("PLAN-AC", "fail-format"), ("PLAN-STEP", "fail-files"),
            ("PLAN-STEP", "fail-done-when"), ("PLAN-STEP", "fail-heading"), ("PLAN-T", "fail-arrow")]

    def as_status(self, rule, case, status):
        self.load_base()
        text = (FIXTURES / rule / case / self.PLAN).read_text(encoding="utf-8")
        self.put(self.PLAN, text.replace("status: sealed", f"status: {status}"))
        return lib.validate_file(self.vault / self.PLAN)

    def test_soft_rules_warn_on_draft_and_error_when_sealed(self):     # T4 -> AC5
        for rule, case in self.SOFT:
            with self.subTest(rule=rule):
                res = self.as_status(rule, case, "draft")
                self.assertTrue(any(m.startswith(f"[{rule}]") for m in res.warnings), res.warnings)
                self.assertFalse(any(m.startswith(f"[{rule}]") for m in res.errors), res.errors)
                res = self.as_status(rule, case, "sealed")
                self.assertTrue(any(m.startswith(f"[{rule}]") for m in res.errors), res.errors)

    def test_format_rules_stay_errors_on_draft(self):                  # T4 -> AC5
        for rule, case in self.HARD:
            with self.subTest(rule=rule, case=case):
                res = self.as_status(rule, case, "draft")
                self.assertTrue(any(m.startswith(f"[{rule}]") for m in res.errors), res.errors)

    def test_every_soft_rule_is_covered(self):
        self.assertEqual({r for r, _ in self.SOFT}, set(lib.DRAFT_SOFT))


class CrossNoteTest(VaultCase):
    def test_repos_empty_and_code_links(self):    # T9 -> AC9
        self.load_base()
        self.put("Agent/Projects/none.md", (self.vault / "Agent/Projects/demo.md").read_text(
            encoding="utf-8").replace("id: demo", "id: none").replace("repos: [me/demo]", "repos: []"))
        self.assertEqual(self.check(), ({}, {}))

    def test_dup_repo_flags_both_projects(self):  # T9 -> AC9
        self.load_base()
        self.put("Agent/Projects/other.md", (self.vault / "Agent/Projects/demo.md").read_text(
            encoding="utf-8").replace("id: demo", "id: other"))
        errors, _ = self.check()
        self.assertEqual(sorted(errors), ["Agent/Projects/demo.md", "Agent/Projects/other.md"])


if __name__ == "__main__":
    unittest.main()
