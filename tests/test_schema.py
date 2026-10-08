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

    def test_shows_the_no_pr_rules(self):    # T1 -> AC1
        text = self.out()
        self.assertIn("pr, if present, is none", text)
        self.assertIn("## Closing comment", text)

    def test_patching_a_constant_changes_output(self):   # T1 -> AC1
        with mock.patch.dict(lib.SIZE_CAPS, {"project": 61}), \
                mock.patch.object(lib, "EVENTS", ["zzz-event"]), \
                mock.patch.dict(lib.RULES, {"NEW-RULE": ("error", "a brand new rule")}):
            text = self.out()
        self.assertIn("body cap: 61 lines", text)
        self.assertIn("zzz-event", text)
        self.assertIn("NEW-RULE", text)
        self.assertNotIn("zzz-event", self.out())

    def test_issue_body_line(self):          # T1 -> AC1
        line = next(ln for ln in self.out().splitlines() if ln.startswith("Issue body"))
        pos = [line.index(f"## {n}") for n in lib.ISSUE_SECTIONS]
        self.assertEqual(pos, sorted(pos))
        with mock.patch.object(lib, "ISSUE_SECTIONS", {"Zzz": "h"}):
            self.assertIn("## Zzz", self.out())
        self.assertNotIn("## Zzz", self.out())


class IssueBodyTest(unittest.TestCase):
    def body(self, **over):
        parts = {n: over.get(n, "text") for n in lib.ISSUE_SECTIONS}
        return "\n".join(f"## {n}\n{t}\n" for n, t in parts.items() if t is not None)

    def test_valid_body(self):                  # T1 -> AC1
        self.assertEqual(lib.issue_body_problems(self.body()), [])
        self.assertEqual(lib.issue_body_problems(self.body(Constraints="None")), [])
        self.assertEqual(lib.issue_body_problems("\n\n" + self.body()), [])

    def test_each_mismatch_is_reported(self):   # T1 -> AC1
        cases = {
            "missing": (self.body(Constraints=None), "missing section '## Constraints'"),
            "unknown": (self.body() + "## Extra\nx\n", "unknown section '## Extra'"),
            "duplicate": (self.body() + "## Problem\nagain\n", "'## Problem' appears 2 times"),
            "empty": (self.body(Source=""), "'## Source' is empty"),
            "comment-only": (self.body(Source="<!-- hint\nmore -->"), "'## Source' is empty"),
            "unclosed-comment": (self.body(Source="<!-- never closed"), "'## Source' is empty"),
            "leading": ("intro\n" + self.body(), "text before the first"),
        }
        for label, (text, want) in cases.items():
            with self.subTest(label):
                found = lib.issue_body_problems(text)
                self.assertTrue(any(want in p for p in found), found)
        swapped = (self.body().replace("## Problem", "## X").replace("## Source", "## Problem")
                   .replace("## X", "## Source"))
        self.assertTrue(any("out of order" in p for p in lib.issue_body_problems(swapped)))

    def test_two_problems_both_named(self):     # T1 -> AC1
        self.assertEqual(len(lib.issue_body_problems("lead\n" + self.body(Source=None))), 2)

    def test_heading_in_code_fence_ignored(self):   # T1 -> AC1
        text = self.body(Problem="```\n## Fake\n```")
        self.assertEqual(lib.issue_body_problems(text), [])

    def test_skeleton_and_template_use_the_constant(self):   # T1 -> AC1
        for n in lib.ISSUE_SECTIONS:
            self.assertIn(f"## {n}\n", lib.issue_template_file())
        self.assertEqual([ln[3:] for ln in lib.issue_body_skeleton().splitlines() if ln],
                         list(lib.ISSUE_SECTIONS))
        self.assertTrue(lib.issue_template_file().startswith("---\nname:"))


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
        self.assertEqual(types, set(lib.TYPES) - {"decision"})     # records have their own fixtures

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
            ntype = lib.split_frontmatter((self.vault / rel).read_text(encoding="utf-8"))[0]["type"]
            cap = lib.SIZE_CAPS[ntype] + (1 if ntype == "project" else 0)   # the Standing decisions line is free
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
        for word in ("archive", "new note", "new daily", "--goal", "intake", "Intake"):
            self.assertNotIn(word, r.stdout)
        src = (CODE / "vault.py").read_text(encoding="utf-8")
        for word in ("intake", "ARCHIVE", "FOLDERS[\"note\"]"):
            self.assertNotIn(word, src)

    def test_hooks_run_against_v2_vault(self):   # T10 -> AC10
        self.load_base()
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": "t2", "cwd": str(self.vault)}, config=self.cfg)
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


class RetroReadmeAuditTest(VaultCase):        # T1 -> AC6
    def setUp(self):
        super().setUp()
        self.load_base()
        self.retro = self.vault / "Agent/Work/demo/demo-1-retro.md"

    def test_missing_readme_audit_section_fails(self):
        text = self.retro.read_text(encoding="utf-8").replace("## README audit\nNone\n\n", "")
        self.retro.write_text(text, encoding="utf-8", newline="\n")
        res = lib.validate_file(self.retro)
        self.assertTrue(any(m.startswith("[SEC-MISSING]") for m in res.errors), res.errors)

    def test_bad_readme_audit_line_fails(self):
        text = self.retro.read_text(encoding="utf-8").replace(
            "## README audit\nNone\n", "## README audit\nnot a valid line\n")
        self.retro.write_text(text, encoding="utf-8", newline="\n")
        res = lib.validate_file(self.retro)
        self.assertTrue(any(m.startswith("[RETRO-AUDIT]") for m in res.errors), res.errors)

    def test_readme_audit_with_real_lines_passes(self):
        text = self.retro.read_text(encoding="utf-8").replace(
            "## README audit\nNone\n", "## README audit\n- Usage: confirmed — still right\n")
        self.retro.write_text(text, encoding="utf-8", newline="\n")
        self.assertEqual(lib.validate_file(self.retro).errors, [])


class RetroFollowRefTest(VaultCase):      # T1 -> AC1
    def matches(self, line: str) -> bool:
        return any(rx.match(line) for _, rx in lib.LINE_FORMATS["RETRO-FOLLOW"])

    def test_forms_with_and_without_reference(self):
        for base in ("- issue #4 created", "- issue #4 amended", "- idea [[some idea]]",
                     "- dropped: not worth it"):
            for suffix in ("", " (R2)", " (R2, R5)"):
                self.assertTrue(self.matches(base + suffix), base + suffix)

    def test_malformed_suffix_rejected(self):
        for base in ("- issue #4 created", "- issue #4 amended", "- idea [[some idea]]"):
            for suffix in (" (2)", " (R)", " (R2,R5)", " (R2) "):
                self.assertFalse(self.matches(base + suffix), base + suffix)

    def test_dropped_needs_reason_besides_reference(self):
        self.assertFalse(self.matches("- dropped: (R2)"))
        self.assertFalse(self.matches("- dropped: (R2, R5)"))
        self.assertTrue(self.matches("- dropped: (R2 was a typo) (R2)"))

    def test_schema_shows_reference(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vault.cmd_schema(None)
        text = buf.getvalue()
        self.assertIn("- issue #<n> created[ (R<n>[, R<m>…])]", text)
        self.assertIn("(R<n>[, R<m>…])", text)


class DecisionRecordTest(VaultCase):
    """Decision records, their links and the generated Standing decisions (#36)."""
    D1, D2 = "demo-d1-keep-it-small", "demo-d2-keep-it-tiny"

    def record(self, **over):
        fm = {"type": "decision", "project": "demo", "status": "active", "created": "2026-09-26",
              "source": "plan demo#1 D1", **over}
        fm = {k: v for k, v in fm.items() if v is not None}
        return lib.render_frontmatter(fm) + "# Keep it small\n\n## Decision\nSmall.\n\n## Why\nDemo.\n"

    def setUp(self):
        super().setUp()
        self.load_base()

    def standing(self, *lines):
        path = self.vault / "Agent/Projects/demo.md"
        head, _ = path.read_text(encoding="utf-8").split("## Standing decisions\n")
        path.write_text(head + "## Standing decisions\n" + "\n".join(lines or ["None"]) + "\n",
                        encoding="utf-8", newline="\n")

    def rules(self, relpath):
        res = lib.validate_file(self.vault / relpath)
        return sorted({m[1:m.index("]")] for m in res.errors})

    def test_well_formed_record_validates(self):           # T1 -> AC1
        self.put(f"Agent/Decisions/demo/{self.D1}.md", self.record())
        self.standing(f"- Keep it small [[{self.D1}]]")
        self.assertEqual(self.check(), ({}, {}))

    def test_record_problems_each_fail(self):              # T1 -> AC1, AC2
        rel = f"Agent/Decisions/demo/{self.D1}.md"
        good = self.record()
        cases = {
            "missing field": (good.replace("created: 2026-09-26\n", ""), "FM-REQUIRED"),
            "bad source": (good.replace("plan demo#1 D1", "somewhere"), "FM-FORMAT"),
            "bad status": (good.replace("status: active", "status: gone"), "FM-ENUM"),
            "missing section": (good.replace("## Why", "## Because"), "SEC-MISSING"),
            "no title": (good.replace("# Keep it small\n\n", ""), "DEC-TITLE"),
            "too long": (good + "- x\n" * 7, "SIZE-CAP"),
            "superseded without link": (good.replace("status: active", "status: superseded"), "FM-COND"),
            "link without superseded": (good.replace("source:", 'superseded_by: "[[x]]"\nsource:'), "FM-COND"),
            "unquoted link": (good.replace("source:", "replaces: [[x]]\nsource:"), "FM-PARSE"),
        }
        for label, (text, rule) in cases.items():
            with self.subTest(label):
                self.put(rel, text)
                self.assertIn(rule, self.rules(rel))

    def test_every_source_form_is_accepted(self):          # T1 -> AC2
        rel = f"Agent/Decisions/demo/{self.D1}.md"
        for source in ("plan demo#1 D1", "plan demo#12 D3", "issue demo#4", "retro demo#4", "session"):
            with self.subTest(source):
                self.put(rel, self.record(source=source))
                self.assertEqual(self.rules(rel), [])

    def test_links_between_records(self):                  # T1 -> AC2
        old, new = f"Agent/Decisions/demo/{self.D1}.md", f"Agent/Decisions/demo/{self.D2}.md"
        self.put(old, self.record(status="superseded", superseded_by=f"[[{self.D2}]]"))
        self.put(new, self.record(replaces=f"[[{self.D1}]]", source="issue demo#2"))
        self.assertEqual((self.rules(old), self.rules(new)), ([], []))
        self.put(new, self.record(source="issue demo#2"))                     # no longer replaces back
        self.assertEqual(self.rules(old), ["X-DECISION-LINK"])
        self.put(new, self.record(replaces="[[demo-d9-gone]]", source="issue demo#2"))
        self.assertEqual(self.rules(new), ["X-DECISION-LINK"])
        self.put(new, self.record(replaces=f"[[{self.D1}]]", source="issue demo#2"))
        self.put(old, self.record())                                          # replaced but still active
        self.assertEqual(self.rules(old), ["X-DECISION-ACTIVE"])

    def test_standing_decisions_match_active_records(self):    # T3 -> AC5
        old, new = f"Agent/Decisions/demo/{self.D1}.md", f"Agent/Decisions/demo/{self.D2}.md"
        proj = "Agent/Projects/demo.md"
        self.standing()
        self.assertEqual(self.rules(proj), [])                                 # None, no records
        self.put(old, self.record())
        self.put(new, self.record(source="issue demo#2"))
        line1, line2 = f"- Keep it small [[{self.D1}]]", f"- Keep it small [[{self.D2}]]"
        for label, lines, ok in [("both", (line1, line2), True), ("none", (), False),
                                 ("missing", (line1,), False), ("extra", (line1, line2, line2), False),
                                 ("reordered", (line2, line1), False),
                                 ("wrong title", (f"- Other [[{self.D1}]]", line2), False)]:
            with self.subTest(label):
                self.standing(*lines)
                self.assertEqual(self.rules(proj) == [], ok, self.rules(proj))
        self.put(old, self.record(status="superseded", superseded_by=f"[[{self.D2}]]"))
        self.put(new, self.record(replaces=f"[[{self.D1}]]", source="issue demo#2"))
        self.standing(line1, line2)
        self.assertEqual(self.rules(proj), ["X-STANDING"])
        self.standing(line2)
        self.assertEqual(self.rules(proj), [])

    def test_standing_lines_do_not_count_toward_the_cap(self):   # T3 -> AC5
        path = self.vault / "Agent/Projects/demo.md"
        text = path.read_text(encoding="utf-8").replace("## Architecture\n", "## Architecture\n" + "- filler\n" * 40, 1)
        path.write_text(text, encoding="utf-8", newline="\n")
        stems = [f"demo-d{n}-record-{n}" for n in range(1, 71)]
        for n, stem in enumerate(stems, 1):
            self.put(f"Agent/Decisions/demo/{stem}.md",
                     self.record(source=f"issue demo#{n}").replace("Keep it small", f"Record {n}", 1))
        self.standing(*[f"- Record {n} [[{stem}]]" for n, stem in enumerate(stems, 1)])
        self.assertGreater(len(path.read_text(encoding="utf-8").splitlines()), 100)
        self.assertEqual(self.rules("Agent/Projects/demo.md"), [])

    def test_schema_shows_the_decision_type(self):         # T1 -> AC1
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vault.cmd_schema(None)
        text = buf.getvalue()
        for want in ("Decisions/<project>/<project>-d<n>-<slug>.md", "superseded_by", "## Decision, ## Why",
                     "decision-recorded", "- decision [[<record>]]", "X-STANDING", "plan <repo>#<n> D<k>"):
            self.assertIn(want, text)


class LastingMarkerTest(unittest.TestCase):
    def body(self, *lines):
        return "## Decisions\n" + "\n".join(lines) + "\n\n## Tests\n"

    def test_marker_after_because_and_after_user_decision(self):     # T2 -> AC7
        body = self.body("- D1: a, because b (lasting)", "- D2: a, because b (user decision) (lasting)",
                         "- D3: a, because b", "- D4: a, because b (user decision)")
        self.assertEqual(lib.plan_lasting_decisions(body), [(1, None), (2, None)])
        for ln in ("- D1: a, because b (lasting)", "- D2: a, because b (user decision) (lasting)"):
            self.assertTrue(any(rx.match(ln) for _, rx in lib.LINE_FORMATS["PLAN-D"]))

    def test_replaces_phrase_only(self):                             # T2 -> AC7
        body = self.body("- D1: reverse, replaces [[demo-d3-x]], because b (lasting)",
                         "- D2: cite [[demo-d4-y]], because b (lasting)")
        self.assertEqual(lib.plan_lasting_decisions(body), [(1, "demo-d3-x"), (2, None)])

    def test_marker_must_end_the_line_and_sit_in_decisions(self):    # T2 -> AC7
        self.assertEqual(lib.plan_lasting_decisions(self.body("- D1: a, because b (lasting) more")), [])
        self.assertEqual(lib.plan_lasting_decisions("## Goal\n- D1: a, because b (lasting)\n"), [])

    def test_followup_and_daily_forms(self):                         # T2 -> AC8
        def follow(ln):
            return any(rx.match(ln) for _, rx in lib.LINE_FORMATS["RETRO-FOLLOW"])

        def daily(ln):
            return any(rx.match(ln) for _, rx in lib.LINE_FORMATS["DAILY-LINE"])
        self.assertTrue(follow("- decision [[demo-d1-x]]") and follow("- decision [[demo-d1-x]] (R2)"))
        self.assertFalse(follow("- decision demo-d1-x") or follow("- decision [[demo-d1-x]] (2)"))
        self.assertTrue(daily("- 09:30 decision-recorded — Why: #1 [[demo-d1-x]]"))
        self.assertFalse(daily("- 09:30 decision-recorded — no link"))


class DesignNoteTest(VaultCase):
    """The design note type: layout, sections, entry formats, links, sealing checks (#37)."""
    D1 = "Agent/Designs/demo/demo-design-1-first.md"

    def setUp(self):
        super().setUp()
        self.load_base()
        self.draft = (self.vault / self.D1).read_text(encoding="utf-8")

    def rules(self, text, relpath=None, warnings=False):
        rel = relpath or self.D1
        self.put(rel, text)
        res = lib.validate_file(self.vault / rel)
        return sorted({m[1:m.index("]")] for m in (res.warnings if warnings else res.errors)})

    def sealed(self, text=None):
        return (text or self.draft).replace("status: draft", "status: sealed\nsealed: 2026-09-27")

    def test_draft_and_sealed_validate(self):                      # T1 -> AC1
        self.assertEqual(self.check(), ({}, {}))
        shutil.copytree(FIXTURES / "DESIGN-SEALED" / "pass", self.vault, dirs_exist_ok=True)
        self.assertEqual(self.check(), ({}, {}))

    def test_frontmatter_problems(self):                           # T1 -> AC1
        for label, old, new, rule in [
                ("missing field", "created: 2026-09-26\n", "", "FM-REQUIRED"),
                ("unknown field", "status: draft", "status: draft\nextra: 1", "FM-UNKNOWN"),
                ("bad status", "status: draft", "status: open", "FM-ENUM"),
                ("project mismatch", "project: demo", "project: other", "FM-MATCH"),
                ("sealed without date", "status: draft", "status: sealed", "DESIGN-SEALED"),
                ("date on a draft", "status: draft", "status: draft\nsealed: 2026-09-27", "DESIGN-SEALED"),
                ("unquoted link", "status: draft", "status: draft\nextends: [[x]]", "FM-PARSE"),
                ("link as text", "status: draft", "status: draft\nextends: x", "FM-FORMAT")]:
            with self.subTest(label):
                self.assertIn(rule, self.rules(self.draft.replace(old, new, 1)))

    def test_sections_in_order(self):                              # T1 -> AC1
        swapped = self.draft.replace("## Goals", "## Tmp").replace("## Non-goals", "## Goals").replace("## Tmp", "## Non-goals")
        for label, text, rule in [("missing", self.draft.replace("## Non-goals\n", ""), "SEC-MISSING"),
                                  ("misordered", swapped, "SEC-ORDER"),
                                  ("extra", self.draft + "\n## Appendix\n", "SEC-EXTRA")]:
            with self.subTest(label):
                self.assertIn(rule, self.rules(text))

    def test_over_400_lines_warns_but_has_no_cap(self):            # T1 -> AC1
        text = self.draft.replace("## Problem\n", "## Problem\n" + "- filler\n" * 400, 1)
        self.assertEqual(self.rules(text), [])
        self.assertEqual(self.rules(text, warnings=True), ["SIZE-WARN"])

    def test_schema_shows_the_design_type(self):                   # T1 -> AC1
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vault.cmd_schema(None)
        text = buf.getvalue()
        for want in ("Designs/<project>/<project>-design-<n>-<slug>.md", "extends", "## Non-goals, ## Ideas",
                     "design-sealed", "DESIGN-DEC", "X-DESIGN-SEALED", "design <design> D<k>", "### S<n>. <title>"):
            self.assertIn(want, text)

    def test_entry_problems_warn_on_a_draft_and_error_when_sealed(self):   # T2 -> AC2
        bad = {
            ("## Decisions", "DESIGN-DEC"): {
                "no why": "### D1. Keep it small\nDecision: Keep it small.",
                "no decision": "### D1. Keep it small\nWhy: because.",
                "bracket title": "### D1. See [[demo]]\nDecision: a\nWhy: b",
                "duplicate number": "### D1. A\nDecision: a\nWhy: b\n\n### D1. B\nDecision: a\nWhy: b",
                "unnumbered heading": "### Keep it small\nDecision: a\nWhy: b"},
            ("## Stories", "DESIGN-STORY"): {
                "no outcome": "### S1. A\nDepends on: None",
                "no depends": "### S1. A\nOutcome: x",
                "later dependency": "### S1. A\nOutcome: x\nDepends on: S2\n\n### S2. B\nOutcome: y\nDepends on: None",
                "missing dependency": "### S1. A\nOutcome: x\nDepends on: S3",
                "bad dependency": "### S1. A\nOutcome: x\nDepends on: 1",
                "bad repo": "### S1. A\nOutcome: x\nDepends on: None\nRepo: me/demo"},
            ("## Ideas", "DESIGN-IDEA"): {"not a link": "- an idea", "prose": "Some ideas."}}
        good = {"## Decisions": "### D1. Keep it small\nDecision: Keep it small.\nWhy: It is a demo.",
                "## Stories": "### S1. Build it\nOutcome: It exists.\nDepends on: None",
                "## Ideas": "None"}

        def with_section(name, body):
            text = self.draft
            for head, ok in good.items():
                before, after = text.split(head + "\n", 1)
                rest = after.split("\n## ", 1)[1]
                text = before + head + "\n" + (body if head == name else ok) + "\n\n## " + rest
            return text

        for (head, rule), cases in bad.items():
            for label, body in cases.items():
                with self.subTest(rule=rule, case=label):
                    text = with_section(head, body)
                    self.assertEqual(self.rules(text), [], "a draft only warns")
                    self.assertIn(rule, self.rules(text, warnings=True))
                    self.assertIn(rule, self.rules(self.sealed(text)))

    def test_entry_parsers(self):                                  # T2 -> AC2
        body = self.draft.split("---\n", 2)[2]
        self.assertEqual([(d["n"], d["title"], d["replaces"]) for d in lib.design_decisions(body)],
                         [(1, "Keep it small", None)])
        stories = lib.design_stories(body)
        self.assertEqual([(s["n"], s["depends"], s["repo"]) for s in stories], [(1, [], None), (2, [1], "demo")])
        self.assertEqual(lib.design_ideas(body.replace("## Ideas\nNone", "## Ideas\n- [[a]]\n- [[b c]]")), ["a", "b c"])

    def test_a_missing_project_fails_only_when_sealed(self):       # T2 -> AC3
        ghost = "Agent/Designs/ghost/ghost-design-1-x.md"
        text = self.draft.replace("project: demo", "project: ghost")
        self.assertEqual(self.rules(text, ghost), [])
        self.assertIn("X-PROJECT", self.rules(self.sealed(text), ghost))

    def test_sealed_design_needs_its_records_and_promoted_ideas(self):     # T3 -> AC6
        shutil.copytree(FIXTURES / "DESIGN-SEALED" / "pass", self.vault, dirs_exist_ok=True)
        rec = "Agent/Decisions/demo/demo-d1-keep-it-small.md"
        sealed = (self.vault / self.D1).read_text(encoding="utf-8")
        two = sealed.replace("## Stories", "### D2. Second\nDecision: b\nWhy: c\n\n## Stories")
        self.assertIn("X-DESIGN-SEALED", self.rules(two))                      # D2 has no record
        self.put(rec.replace("d1-keep-it-small", "d2-second"),
                 (self.vault / rec).read_text(encoding="utf-8").replace("D1", "D2"))
        self.assertNotIn("X-DESIGN-SEALED", self.rules(two))
        idea = "Agent/Ideas/2026-09-26-an-idea.md"
        text = (self.vault / idea).read_text(encoding="utf-8")
        for label, new in (("open", text.replace("status: promoted", "status: open")),
                           ("elsewhere", text.replace("[[demo-design-1-first]]", "[[demo-design-2-x]]"))):
            with self.subTest(label):
                self.put(idea, new)
                self.assertIn("X-DESIGN-SEALED", self.rules(sealed))

    def test_promoted_to_takes_an_issue_url_or_a_design_link(self):        # T3 -> AC9
        idea = "Agent/Ideas/2026-09-26-an-idea.md"
        text = (self.vault / idea).read_text(encoding="utf-8").replace('promoted_to: ""', "promoted_to: PLACE") \
            .replace("status: open", "status: promoted")
        self.put("Agent/Designs/demo/demo-design-2-next.md", self.draft.replace("First", "Next"))
        for value, ok in (("https://github.com/me/demo/issues/4", True), ('"[[demo-design-1-first]]"', True),
                          ('"[[demo-design-2-next]]"', True), ("https://example.com/4", False),
                          ('"[[demo-d1-keep-it-small]]"', False), ('"[[demo-design-7-gone]]"', False),
                          ("https://github.com/me/demo/pull/4", False), ("4", False)):
            with self.subTest(value):
                self.put(idea, text.replace("PLACE", value))
                rules = {m[1:m.index("]")] for m in lib.validate_file(self.vault / idea).errors}
                self.assertEqual("FM-FORMAT" not in rules, ok, rules)

    def test_decision_record_with_a_design_source(self):           # T3 -> AC5
        rel = "Agent/Decisions/demo/demo-d1-keep-it-small.md"
        record = lib.render_frontmatter({"type": "decision", "project": "demo", "status": "active",
                                         "created": "2026-09-26", "source": "design demo-design-1-first D2"}) \
            + "# Keep it small\n\n## Decision\nSmall.\n\n## Why\nDemo.\n"
        self.put(rel, record)
        self.assertEqual([m for m in lib.validate_file(self.vault / rel).errors if "FM-FORMAT" in m], [])
        for bad in ("design First D2", "design demo-design-1-first", "design demo-design-1-first D0"):
            self.put(rel, record.replace("design demo-design-1-first D2", bad))
            self.assertTrue(any("FM-FORMAT" in m for m in lib.validate_file(self.vault / rel).errors), bad)

    def test_design_sealed_daily_line(self):                       # T3 -> AC1
        def daily(ln):
            return any(rx.match(ln) for _, rx in lib.LINE_FORMATS["DAILY-LINE"])
        self.assertTrue(daily("- 09:30 design-sealed — Portable setups [[demo-design-1-first]]"))
        self.assertFalse(daily("- 09:30 design-sealed — no link"))

    def test_sealed_designs_are_sealed_notes_and_fingerprinted(self):      # T3 -> AC8
        path = self.vault / self.D1
        self.assertFalse(lib.is_sealed_note(path))
        self.assertNotIn(lib.rel(path), lib.fingerprints())
        path.write_text(self.sealed(), encoding="utf-8", newline="\n")
        self.assertTrue(lib.is_sealed_note(path))
        before = lib.fingerprints()
        self.assertIn(lib.rel(path), before)
        path.write_text(self.sealed().replace("The demo", "A demo"), encoding="utf-8", newline="\n")
        self.assertEqual(lib.changed_fingerprints(before), [lib.rel(path)])


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
