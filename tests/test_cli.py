"""The v2 vault CLI: every command runs as a subprocess against a temp vault, and
every note it writes must pass `validate` (T1)."""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import sys

from tests.helpers import CODE, REPO, make_vault, run_py, tmpdir, write_config

sys.path.insert(0, str(CODE))
import lib  # noqa: E402
import vault  # noqa: E402

CLI = CODE / "vault.py"
HOOKS = CODE / "hooks"
TEMPLATE = CODE.parent / "templates" / "vault-CLAUDE.md"
TODAY = dt.date.today().isoformat()


class CliCase(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.vault = make_vault(self.root)
        self.cfg = write_config(self.root, self.vault)

    def tearDown(self):
        self._t.cleanup()

    def cli(self, *args, ok=True):
        r = run_py(CLI, list(args), config=self.cfg)
        if ok:
            self.assertEqual(r.returncode, 0, f"{args}: {r.stdout}{r.stderr}")
        else:
            self.assertEqual(r.returncode, 1, f"{args} should be refused: {r.stdout}{r.stderr}")
            self.assertNotIn("Traceback", r.stderr)
        return r

    def refused(self, *args, why=""):
        r = self.cli(*args, ok=False)
        self.assertIn(why, r.stderr)
        return r

    def path(self, rel: str) -> Path:
        return self.vault / "Agent" / rel

    def text(self, rel: str) -> str:
        return self.path(rel).read_text(encoding="utf-8")

    def put(self, rel: str, text: str) -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    def validate(self):
        r = run_py(CLI, ["validate"], config=self.cfg)
        self.assertNotIn(": error:", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def project(self, pid="demo", *repos):
        self.cli("new", "project", pid, *[a for r in repos for a in ("--repo", r)])

    def idea(self, name, created=TODAY, status="open", body="# T\n", extra=""):
        return self.put(f"Agent/Ideas/{name}.md",
                        f"---\ntype: idea\nsource: session\nstatus: {status}\ncreated: {created}\n{extra}---\n{body}")


class NewProjectTest(CliCase):
    def test_zero_one_two_repos(self):                    # T2 -> AC2
        self.cli("new", "project", "solo")
        self.cli("new", "project", "one", "--repo", "me/one", "--purpose", "Do one thing.")
        self.cli("new", "project", "two", "--repo", "me/two-a", "--repo", "me/two-b")
        self.assertIn("repos: []", self.text("Projects/solo.md"))
        self.assertIn("repos: [me/one]", self.text("Projects/one.md"))
        self.assertIn("Do one thing.", self.text("Projects/one.md"))
        self.assertIn("repos: [me/two-a, me/two-b]", self.text("Projects/two.md"))
        for section in ("Purpose", "Current state", "Architecture", "Standing decisions"):
            self.assertIn(f"## {section}\n", self.text("Projects/one.md"))
        self.assertTrue(self.text("Projects/one.md").endswith("## Standing decisions\nNone\n"))     # T3 -> AC5
        self.validate()

    def test_refusals(self):                              # T2 -> AC2
        self.project("demo", "me/demo")
        self.refused("new", "project", "Bad_Id", why="kebab-case")
        self.refused("new", "project", "demo", why="already exists")
        self.refused("new", "project", "x", "--repo", "demo", why="owner/name")
        self.refused("new", "project", "x", "--repo", "a/b/c", why="owner/name")
        self.refused("new", "project", "other", "--repo", "you/demo", why="already listed by project 'demo'")
        self.refused("new", "project", "twice", "--repo", "a/x", "--repo", "b/x", why="given twice")
        self.assertFalse(self.path("Projects/other.md").exists())
        self.assertFalse(self.path("Projects/twice.md").exists())
        self.validate()


class NewPlanTest(CliCase):
    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")

    def test_plan_and_impl_created(self):                 # T3 -> AC3
        self.cli("new", "plan", "demo", "7")
        plan, impl = self.text("Work/demo/demo-7-plan.md"), self.text("Work/demo/demo-7-impl.md")
        self.assertIn("status: draft", plan)
        self.assertIn("status: open", impl)
        self.assertNotIn("branch", impl)

    def test_every_required_section_present(self):        # T3 -> AC3
        self.cli("new", "plan", "demo", "7")
        for kind, ntype in (("plan", "plan"), ("impl", "impl")):
            text = self.text(f"Work/demo/demo-7-{kind}.md")
            heads = [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]
            self.assertEqual(heads, lib.SECTIONS[ntype])

    def test_refusals(self):                              # T3 -> AC3
        self.refused("new", "plan", "nope", "1", why="not listed in any project")
        self.refused("new", "plan", "demo", "abc", why="must be a number")
        self.refused("new", "plan", "demo", "0", why="must be a number")
        self.cli("new", "plan", "demo", "7")
        self.refused("new", "plan", "demo", "7", why="already exists")
        self.path("Work/demo/demo-7-plan.md").unlink()
        self.refused("new", "plan", "demo", "7", why="already exists")     # impl still there
        self.assertFalse(self.path("Work/demo/demo-7-plan.md").exists())

    def test_draft_has_warnings_only_and_sealed_has_errors(self):     # T4 -> AC3, AC5
        self.cli("new", "plan", "demo", "7")
        r = run_py(CLI, ["validate"], config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn(": error:", r.stdout)
        for rule in ("PLAN-AC", "PLAN-STEP", "PLAN-T", "PLAN-FULL"):
            self.assertIn(f"warning: [{rule}]", r.stdout)
        p = self.path("Work/demo/demo-7-plan.md")
        p.write_text(p.read_text(encoding="utf-8").replace(
            "status: draft", "status: sealed\nissue_updated: 2026-09-27T00:20:58Z\nbase_sha: e66091b"),
            encoding="utf-8", newline="\n")
        r = run_py(CLI, ["validate"], config=self.cfg)
        self.assertEqual(r.returncode, 1)
        for rule in ("PLAN-AC", "PLAN-STEP", "PLAN-T", "PLAN-FULL"):
            self.assertIn(f"error: [{rule}]", r.stdout)


class NewRetroTest(CliCase):
    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")

    def set_pr(self, pr):
        p = self.path("Work/demo/demo-7-impl.md")
        p.write_text(p.read_text(encoding="utf-8").replace("status: open", f"pr: {pr}\nstatus: open", 1),
                     encoding="utf-8", newline="\n")

    def test_copies_pr(self):                             # T5 -> AC4
        self.cli("new", "plan", "demo", "7")
        self.set_pr("21")
        self.cli("new", "retro", "demo", "7")
        retro = self.text("Work/demo/demo-7-retro.md")
        self.assertRegex(retro, r"(?m)^pr: 21$")
        self.assertIn("status: open", retro)
        heads = [ln[3:] for ln in retro.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, lib.SECTIONS["retro"])
        self.validate()

    def test_no_pr_plan_gives_a_closing_comment_section(self):     # T4 -> AC4
        self.cli("new", "plan", "demo", "7")
        p = self.path("Work/demo/demo-7-plan.md")
        p.write_text(p.read_text(encoding="utf-8").replace("status: draft", "pr: none\nstatus: draft"),
                     encoding="utf-8", newline="\n")
        self.cli("new", "retro", "demo", "7")                # no pr: in the impl note
        retro = self.text("Work/demo/demo-7-retro.md")
        self.assertRegex(retro, r"(?m)^pr: none$")
        heads = [ln[3:] for ln in retro.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, lib.SECTIONS["retro"] + ["Closing comment"])

    def test_pr_plan_has_no_closing_comment(self):        # T4 -> AC4
        self.cli("new", "plan", "demo", "7")
        self.set_pr("21")
        self.cli("new", "retro", "demo", "7")
        self.assertNotIn("Closing comment", self.text("Work/demo/demo-7-retro.md"))

    def test_refusals(self):                              # T5 -> AC4
        self.refused("new", "retro", "demo", "7", why="doesn't exist")
        self.cli("new", "plan", "demo", "7")
        self.refused("new", "retro", "demo", "7", why="no 'pr:'")
        self.set_pr("21")
        self.cli("new", "retro", "demo", "7")
        self.refused("new", "retro", "demo", "7", why="already exists")
        self.refused("new", "retro", "demo", "x", why="must be a number")

    def test_impl_branch_is_optional(self):               # T6 -> AC6
        self.cli("new", "plan", "demo", "7")
        self.validate()                                   # fresh impl has no branch
        p = self.path("Work/demo/demo-7-impl.md")
        p.write_text(p.read_text(encoding="utf-8").replace("status: open", "branch: feat/7-x\nstatus: open", 1),
                     encoding="utf-8", newline="\n")
        self.validate()


class IdeaTest(CliCase):
    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")

    def test_add_defaults_and_log(self):                  # T7 -> AC7
        self.cli("idea", "add", "Try Something New!")
        name = f"Ideas/{TODAY}-try-something-new.md"
        idea = self.text(name)
        self.assertIn("source: session", idea)
        self.assertIn("status: open", idea)
        self.assertIn(f"created: {TODAY}", idea)
        self.assertEqual(idea.split("---\n", 2)[2], "# Try Something New!\n")
        self.assertRegex(self.text(f"Daily/{TODAY}.md"),
                         rf"(?m)^- \d\d:\d\d idea-added — Try Something New! \[\[{TODAY}-try-something-new\]\]$")
        self.validate()

    def test_options_and_refusals(self):                  # T7 -> AC7
        self.cli("idea", "add", "Scoped", "--project", "demo", "--source", "retro demo#3")
        idea = self.text(f"Ideas/{TODAY}-scoped.md")
        self.assertIn("project: demo", idea)
        self.assertIn("source: retro demo#3", idea)
        self.refused("idea", "add", "X", "--project", "ghost", why="does not exist")
        self.refused("idea", "add", "X", "--source", "rumour", why="source must be")
        self.refused("idea", "add", "   ", why="empty")
        self.validate()

    def test_same_day_same_title_is_unique(self):         # T7 -> AC7
        for _ in range(3):
            self.cli("idea", "add", "Same title")
        for suffix in ("", "-2", "-3"):
            self.assertTrue(self.path(f"Ideas/{TODAY}-same-title{suffix}.md").is_file())
        self.validate()

    def test_drop_records_reason(self):                   # T7 -> AC7
        self.cli("idea", "add", "Bad idea")
        name = f"{TODAY}-bad-idea"
        self.cli("idea", "drop", name, "--reason", "Superseded by\nthe new design")
        idea = self.text(f"Ideas/{name}.md")
        self.assertIn("status: dropped", idea)
        self.assertTrue(idea.endswith("Dropped: Superseded by the new design\n"))
        self.validate()
        self.refused("idea", "drop", name, "--reason", "again", why="only open ideas")
        self.refused("idea", "drop", "no-such", "--reason", "x", why="no idea")

    def test_drop_keeps_the_cap(self):                    # T7 -> AC7
        self.idea("2026-01-01-long", body="# Long\n" + "context\n" * 7)          # 8 body lines: at the cap
        self.cli("idea", "drop", "2026-01-01-long.md", "--reason", "meh")
        idea = self.text("Ideas/2026-01-01-long.md")
        self.assertEqual(len(idea.split("---\n", 2)[2].splitlines()), 8)
        self.assertIn("Dropped: meh", idea)
        self.validate()


class IdeasListTest(CliCase):
    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")
        self.project("other")

    def ago(self, days):
        return (dt.date.today() - dt.timedelta(days=days)).isoformat()

    def test_filters_by_project_and_status(self):         # T8 -> AC8
        self.idea("2026-01-01-a", extra="project: demo\n")
        self.idea("2026-01-02-b", extra="project: other\n")
        self.idea("2026-01-03-c", status="dropped", extra="project: demo\n")
        out = self.cli("ideas").stdout
        self.assertIn("2026-01-01-a", out)
        self.assertIn("2026-01-02-b", out)
        self.assertNotIn("2026-01-03-c", out)
        out = self.cli("ideas", "--project", "demo").stdout
        self.assertIn("2026-01-01-a", out)
        self.assertNotIn("2026-01-02-b", out)
        out = self.cli("ideas", "--status", "dropped").stdout
        self.assertIn("2026-01-03-c", out)
        self.assertNotIn("2026-01-01-a", out)

    def test_review_uses_the_90_day_line(self):           # T8 -> AC8
        self.idea("2026-01-01-old", created=self.ago(91))
        self.idea("2026-01-02-edge", created=self.ago(90))
        self.idea("2026-01-03-new", created=self.ago(89))
        self.idea("2026-01-04-gone", created=self.ago(200), status="dropped")
        out = self.cli("ideas", "review").stdout
        self.assertIn("2026-01-01-old", out)
        self.assertNotIn("2026-01-02-edge", out)
        self.assertNotIn("2026-01-03-new", out)
        self.assertNotIn("2026-01-04-gone", out)

    def test_never_reads_bodies(self):                    # T8 -> AC8
        p = self.idea("2026-01-01-broken", created=self.ago(120))
        p.write_bytes(p.read_bytes() + b"\xff\xfe not utf-8 \x00\n")
        self.assertIn("2026-01-01-broken", self.cli("ideas").stdout)
        self.assertIn("2026-01-01-broken", self.cli("ideas", "review").stdout)


class HandoffTest(CliCase):
    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")
        self.put("Inbox/Garden Plan.md", "phone words\n")
        self.put("Inbox/Other.md", "more\n")

    def test_pull_creates_idea_and_log(self):             # T9 -> AC9
        self.cli("handoff", "pull", "Inbox/Garden Plan.md", "--project", "demo")
        name = f"Ideas/{TODAY}-garden-plan.md"
        idea = self.text(name)
        self.assertIn("source: phone:Inbox/Garden Plan.md", idea)
        self.assertIn("project: demo", idea)
        self.assertIn("[[Garden Plan]]", idea)
        self.assertIn(f"[[{TODAY}-garden-plan]]", self.text(f"Daily/{TODAY}.md"))
        self.assertIn("idea-added — Garden Plan", self.text(f"Daily/{TODAY}.md"))
        log = json.loads(self.path(".handoff-log.json").read_text(encoding="utf-8"))
        self.assertIn("Inbox/Garden Plan.md", log)
        self.validate()

    def test_refusals(self):                              # T9 -> AC9
        self.refused("handoff", "pull", "Agent/Projects/demo.md", why="only phone-side")
        self.refused("handoff", "pull", "agent/projects/demo.md", why="only phone-side")
        self.refused("handoff", "pull", "Inbox/Missing.md", why="not a file")
        self.refused("handoff", "pull", "../elsewhere.md", why="outside the vault")
        self.refused("handoff", "pull", "Inbox/Other.md", "--project", "ghost", why="does not exist")
        self.cli("handoff", "pull", "Inbox/Other.md")
        self.refused("handoff", "pull", "Inbox/Other.md", why="already pulled")
        self.validate()

    def test_list_hides_pulled(self):                     # T9 -> AC9
        out = self.cli("handoff", "list").stdout
        self.assertIn("Inbox/Garden Plan.md", out)
        self.assertIn("Inbox/Other.md", out)
        self.cli("handoff", "pull", "Inbox/Other.md")
        out = self.cli("handoff", "list").stdout
        self.assertIn("Inbox/Garden Plan.md", out)
        self.assertNotIn("Inbox/Other.md", out)

    def test_absolute_path_is_accepted(self):             # T9 -> AC9
        self.cli("handoff", "pull", str(self.vault / "Inbox" / "Other.md"))
        self.assertIn("Inbox/Other.md", self.path(".handoff-log.json").read_text(encoding="utf-8"))
        self.validate()


class LogTest(CliCase):
    def test_log_creates_daily_and_appends(self):         # T10 -> AC10
        self.project("demo", "me/demo")
        self.assertFalse(self.path(f"Daily/{TODAY}.md").exists())
        self.cli("log", "did a thing")
        self.cli("log", "did another\nthing", "--project", "demo")
        daily = self.text(f"Daily/{TODAY}.md")
        self.assertRegex(daily, r"(?m)^- \d\d:\d\d session — did a thing$")
        self.assertRegex(daily, r"(?m)^- \d\d:\d\d session — did another thing \[\[demo\]\]$")
        self.assertEqual(daily.count("## Log"), 1)
        self.validate()

    def test_unknown_project_and_empty_text_refused(self):        # T10 -> AC10
        self.refused("log", "x", "--project", "ghost", why="does not exist")
        self.refused("log", "  ", why="empty")
        self.assertFalse(self.path(f"Daily/{TODAY}.md").exists())


class DecisionAddTest(CliCase):                                    # T4, T5 -> AC3, AC4 (#36)
    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")
        self.project("other")

    def add(self, title="Keep it small", source="plan demo#2 D1", project="demo", *extra, ok=True):
        return self.cli("decision", "add", project, title, "--decision", "Keep the demo small.",
                        "--why", "It is a demo.", "--source", source, *extra, ok=ok)

    def snapshot(self):
        return {p: p.read_bytes() for p in (self.vault / "Agent").rglob("*.md")}

    def test_numbers_content_overview_and_log(self):
        self.add("Keep it small")
        self.add("Why: #1 [and more]", "issue demo#3")
        d1, d2 = "Decisions/demo/demo-d1-keep-it-small.md", "Decisions/demo/demo-d2-why-1-and-more.md"
        self.assertEqual(self.text(d1), f"---\ntype: decision\nproject: demo\nstatus: active\ncreated: {TODAY}\n"
                                        "source: plan demo#2 D1\n---\n# Keep it small\n\n## Decision\n"
                                        "Keep the demo small.\n\n## Why\nIt is a demo.\n")
        self.assertIn("# Why: #1 [and more]\n", self.text(d2))
        self.assertTrue(self.text("Projects/demo.md").endswith(
            "## Standing decisions\n- Keep it small [[demo-d1-keep-it-small]]\n"
            "- Why: #1 [and more] [[demo-d2-why-1-and-more]]\n"))
        log = self.text(f"Daily/{TODAY}.md")
        self.assertRegex(log, r"- \d\d:\d\d decision-recorded — Keep it small \[\[demo-d1-keep-it-small\]\]")
        self.assertIn("decision-recorded — Why: #1 [and more] [[demo-d2-why-1-and-more]]", log)
        self.add("Other thing", "session", "other")
        self.assertTrue(self.path("Decisions/other/other-d1-other-thing.md").is_file())
        self.validate()

    def test_replacing_supersedes_without_touching_the_body(self):
        self.add("Keep it small")
        old = "Decisions/demo/demo-d1-keep-it-small.md"
        before = self.text(old)
        self.add("Keep it tiny", "issue demo#4", "demo", "--replaces", "demo-d1-keep-it-small")
        after = self.text(old)
        self.assertEqual(after.split("---\n", 2)[2], before.split("---\n", 2)[2])
        self.assertIn("status: superseded\n", after)
        self.assertIn('superseded_by: "[[demo-d2-keep-it-tiny]]"\n', after)
        self.assertIn('replaces: "[[demo-d1-keep-it-small]]"\n', self.text("Decisions/demo/demo-d2-keep-it-tiny.md"))
        self.assertTrue(self.text("Projects/demo.md").endswith(
            "## Standing decisions\n- Keep it tiny [[demo-d2-keep-it-tiny]]\n"))
        self.validate()

    def test_standing_section_in_the_middle_keeps_what_follows(self):
        p = self.path("Projects/demo.md")
        p.write_text(p.read_text(encoding="utf-8") + "\n## Notes\nkept\n", encoding="utf-8", newline="\n")
        self.add()
        self.assertIn("[[demo-d1-keep-it-small]]\n", self.text("Projects/demo.md"))
        self.assertTrue(self.text("Projects/demo.md").endswith("\n## Notes\nkept\n"))

    def test_refusals_leave_every_note_unchanged(self):
        self.add("Keep it small")
        self.add("Second", "issue demo#9")
        self.cli("decision", "add", "demo", "Third", "--decision", "x", "--why", "y", "--source", "session",
                 "--replaces", "demo-d2-second")
        before = self.snapshot()
        cases = [
            (("demo", "  ", "--decision", "d", "--why", "w", "--source", "session"), "title is empty"),
            (("demo", "T", "--decision", " ", "--why", "w", "--source", "session"), "--decision is empty"),
            (("demo", "T", "--decision", "d", "--why", "  ", "--source", "session"), "--why is empty"),
            (("ghost", "T", "--decision", "d", "--why", "w", "--source", "session"), "does not exist"),
            (("demo", "T", "--decision", "d", "--why", "w", "--source", "somewhere"), "source must be"),
            (("demo", "T [[x]]", "--decision", "d", "--why", "w", "--source", "session"), "[["),
            (("demo", "T\nU", "--decision", "d", "--why", "w", "--source", "session"), "one line"),
            (("demo", "T", "--decision", "d", "--why", "w", "--source", "plan demo#2 D1"), "already recorded as demo-d1"),
            (("demo", "T", "--decision", "d", "--why", "w", "--source", "session", "--replaces", "demo-d2-second"),
             "already superseded"),
            (("demo", "T", "--decision", "d", "--why", "w", "--source", "session", "--replaces", "demo-d9-none"),
             "no decision record"),
            (("other", "T", "--decision", "d", "--why", "w", "--source", "session", "--replaces", "demo-d1-keep-it-small"),
             "record of project 'demo'"),
        ]
        for args, why in cases:
            with self.subTest(args=args):
                self.refused("decision", "add", *args, why=why)
                self.assertEqual(self.snapshot(), before)

    def test_a_refused_overview_rolls_everything_back(self):
        p = self.path("Projects/demo.md")
        p.write_text(p.read_text(encoding="utf-8").replace("## Standing decisions\nNone\n", ""),
                     encoding="utf-8", newline="\n")
        before = self.snapshot()
        self.refused("decision", "add", "demo", "T", "--decision", "d", "--why", "w", "--source", "session",
                     why="no '## Standing decisions' section")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.path("Decisions").exists())

    def test_decisions_lists_active_all_and_by_project(self):
        self.add("Keep it small")
        self.add("Keep it tiny", "issue demo#4", "demo", "--replaces", "demo-d1-keep-it-small")
        self.add("Elsewhere", "retro demo#5", "other")
        active = self.cli("decisions").stdout
        self.assertIn("demo-d2-keep-it-tiny.md  Keep it tiny  [issue demo#4]", active)
        self.assertIn("other-d1-elsewhere.md  Elsewhere  [retro demo#5]", active)
        self.assertNotIn("demo-d1-keep-it-small", active)
        everything = self.cli("decisions", "--all").stdout
        self.assertIn("demo-d1-keep-it-small.md  Keep it small  [plan demo#2 D1]  (superseded)", everything)
        only = self.cli("decisions", "--all", "--project", "demo").stdout
        self.assertNotIn("other-d1", only)
        self.assertIn("demo-d2", only)
        self.project("solo")
        self.assertIn("no decisions", self.cli("decisions", "--project", "solo").stdout)
        self.refused("decisions", "--project", "ghost", why="does not exist")


DESIGN_BODY = """# Portable setups

## Problem
It only works on one machine.

## Goals
- Work elsewhere.

## Non-goals
- Everything else.

## Ideas
{ideas}

## Options considered
- A or B.

## Chosen design
A.

## Decisions
{decisions}

## Stories
{stories}

## Open questions
None
"""
TWO_DECISIONS = ("### D1. Keep it small\nDecision: Keep the demo small.\nWhy: It is a demo.\n\n"
                 "### D2. Keep it tiny\nDecision: Keep it tiny.\nWhy: Smaller is better.\nReplaces: [[demo-d1-first]]")
TWO_STORIES = ("### S1. Build it\nOutcome: It exists.\nDepends on: None\n\n"
               "### S2. Ship it\nOutcome: It ships.\nDepends on: S1")


class DesignCase(CliCase):
    """Designs of project 'demo' (one repo), with an existing active record to replace."""

    def setUp(self):
        super().setUp()
        self.project("demo", "me/demo")
        self.project("other", "me/other")
        self.cli("decision", "add", "demo", "First", "--decision", "One.", "--why", "Because.", "--source", "session")
        self.idea("2026-09-01-one", extra="project: demo\n", body="# Idea one\n")
        self.idea("2026-09-01-two", body="# Idea two\n")

    def design(self, n=1, title="Portable setups", project="demo", ideas="- [[2026-09-01-one]]\n- [[2026-09-01-two]]",
               decisions=TWO_DECISIONS, stories=TWO_STORIES, status="draft"):
        rel = f"Agent/Designs/{project}/{project}-design-{n}-portable-setups.md"
        fm = f"---\ntype: design\nproject: {project}\nstatus: {status}\ncreated: {TODAY}\n---\n"
        self.put(rel, fm + DESIGN_BODY.format(ideas=ideas, decisions=decisions, stories=stories)
                 .replace("Portable setups", title, 1))
        return f"{project}-design-{n}-portable-setups"

    def snapshot(self):
        return {p: p.read_bytes() for p in (self.vault / "Agent").rglob("*.md")}


class NewDesignTest(DesignCase):
    def test_numbers_per_project_and_creates_every_section(self):        # T4 -> AC3
        r = self.cli("new", "design", "demo", "Portable setups")
        self.assertIn("created Agent/Designs/demo/demo-design-1-portable-setups.md", r.stdout)
        self.cli("new", "design", "demo", "Second look")
        self.cli("new", "design", "other", "Other thing")
        text = self.text("Designs/demo/demo-design-1-portable-setups.md")
        self.assertTrue(text.startswith(f"---\ntype: design\nproject: demo\nstatus: draft\ncreated: {TODAY}\n---\n"
                                        "# Portable setups\n"))
        for section in lib.SECTIONS["design"]:
            self.assertIn(f"\n## {section}\n", text)
        self.assertTrue(self.path("Designs/demo/demo-design-2-second-look.md").is_file())
        self.assertTrue(self.path("Designs/other/other-design-1-other-thing.md").is_file())
        self.validate()          # a fresh draft only has warnings

    def test_a_project_that_does_not_exist_yet(self):                    # T4 -> AC3
        self.cli("new", "design", "brand-new", "A new thing")
        self.assertTrue(self.path("Designs/brand-new/brand-new-design-1-a-new-thing.md").is_file())
        self.validate()

    def test_refusals(self):                                             # T4 -> AC3
        before = self.snapshot()
        self.refused("new", "design", "Not An Id", "T", why="lowercase-kebab-case")
        self.refused("new", "design", "demo", "   ", why="title is empty")
        self.assertEqual(self.snapshot(), before)


class SealDesignTest(DesignCase):
    def seal(self, name, ok=True, why=""):
        return self.cli("seal", "design", name) if ok else self.refused("seal", "design", name, why=why)

    def test_seals_and_records_everything(self):                         # T5 -> AC4
        name = self.design()
        r = self.seal(name)
        self.assertIn("2 decision record(s), 2 idea(s) promoted", r.stdout)
        sealed = self.text(f"Designs/demo/{name}.md")
        self.assertIn(f"status: sealed\ncreated: {TODAY}\nsealed: {TODAY}\n", sealed)
        d2 = self.text("Decisions/demo/demo-d3-keep-it-tiny.md")
        self.assertIn(f"source: design {name} D2\n", d2)
        self.assertIn('replaces: "[[demo-d1-first]]"\n', d2)
        self.assertIn(f"source: design {name} D1\n", self.text("Decisions/demo/demo-d2-keep-it-small.md"))
        old = self.text("Decisions/demo/demo-d1-first.md")
        self.assertIn("status: superseded\n", old)
        self.assertIn('superseded_by: "[[demo-d3-keep-it-tiny]]"\n', old)
        for idea in ("2026-09-01-one", "2026-09-01-two"):
            self.assertIn('status: promoted\n', self.text(f"Ideas/{idea}.md"))
            self.assertIn(f'promoted_to: "[[{name}]]"\n', self.text(f"Ideas/{idea}.md"))
        self.assertTrue(self.text("Projects/demo.md").endswith(
            "## Standing decisions\n- Keep it small [[demo-d2-keep-it-small]]\n- Keep it tiny [[demo-d3-keep-it-tiny]]\n"))
        log = self.text(f"Daily/{TODAY}.md")
        self.assertEqual(len(re.findall(r"decision-recorded — ", log)), 3)       # First, plus the two
        self.assertIn("decision-recorded — Keep it tiny [[demo-d3-keep-it-tiny]]", log)
        self.assertEqual(len(re.findall(r"idea-promoted — ", log)), 2)
        self.assertIn("idea-promoted — Idea one [[2026-09-01-one]]", log)
        self.assertRegex(log, rf"- \d\d:\d\d design-sealed — Portable setups \[\[{name}\]\]")
        self.assertTrue(log.rstrip().endswith(f"[[{name}]]"), "the design-sealed line comes last")
        self.validate()
        self.seal(name, ok=False, why="already sealed")
        self.cli("decision", "add", "demo", "T", "--decision", "d", "--why", "w", "--source", f"design {name} D1",
                 ok=False)

    def test_decision_add_refuses_a_used_design_source(self):            # T5 -> AC5
        name = self.design()
        self.seal(name)
        r = self.cli("decision", "add", "demo", "Again", "--decision", "d", "--why", "w",
                     "--source", f"design {name} D2", ok=False)
        self.assertIn("already recorded as demo-d3-keep-it-tiny", r.stderr)
        self.cli("decision", "add", "demo", "Fresh", "--decision", "d", "--why", "w", "--source", f"design {name} D9")
        self.validate()

    def test_a_design_without_ideas_or_replacements(self):               # T5 -> AC4
        name = self.design(ideas="None", decisions="### D1. Only\nDecision: x.\nWhy: y.")
        self.seal(name)
        self.assertIn("status: active\n", self.text("Decisions/demo/demo-d1-first.md"))
        self.validate()

    def test_refusals_leave_every_note_unchanged(self):                  # T6 -> AC4
        self.cli("decision", "add", "demo", "Third", "--decision", "x", "--why", "y", "--source", "session",
                 "--replaces", "demo-d1-first")
        self.cli("decision", "add", "demo", "Fourth", "--decision", "x", "--why", "y", "--source", "session")
        self.idea("2026-09-01-done", status="dropped")
        self.idea("2026-09-01-elsewhere", extra="project: other\n")
        one = "### D1. A\nDecision: x.\nWhy: y.\n"
        cases = {
            "missing project": (dict(project="ghost", ideas="None", decisions=one), "does not exist"),
            "sealed-rule failure": (dict(stories="### S1. A\nDepends on: None"), "DESIGN-STORY"),
            "replaces a superseded record": (dict(decisions=one + "Replaces: [[demo-d1-first]]"), "already superseded"),
            "replaces another project's record": (dict(decisions=one + "Replaces: [[other-d1-x]]"), "not a record of project"),
            "replaces nothing real": (dict(decisions=one + "Replaces: [[demo-d9-none]]"), "not a record of project"),
            "two replace one": (dict(decisions=one + "Replaces: [[demo-d3-fourth]]\n\n"
                                                      "### D2. B\nDecision: x.\nWhy: y.\nReplaces: [[demo-d3-fourth]]"),
                                "both replace"),
            "idea not open": (dict(ideas="- [[2026-09-01-done]]"), "dropped"),
            "idea of another project": (dict(ideas="- [[2026-09-01-elsewhere]]"), "belongs to project 'other'"),
            "idea that does not exist": (dict(ideas="- [[2026-09-01-nope]]"), "does not exist"),
            "story repo the project lacks": (dict(stories="### S1. A\nOutcome: x.\nDepends on: None\nRepo: nope"),
                                             "is not one of project 'demo'"),
        }
        for label, (kw, why) in cases.items():
            with self.subTest(label):
                name = self.design(**kw)
                before = self.snapshot()
                self.seal(name, ok=False, why=why)
                self.assertEqual(self.snapshot(), before)
        self.refused("seal", "design", "demo-design-9-none", why="no design")

    def test_a_story_without_repo_in_a_two_repo_project(self):           # T6 -> AC4
        self.cli("new", "project", "duo", "--repo", "me/duo-a", "--repo", "me/duo-b")
        name = self.design(project="duo", ideas="None", decisions="### D1. A\nDecision: x.\nWhy: y.")
        before = self.snapshot()
        self.seal(name, ok=False, why="has no 'Repo:' line")
        self.assertEqual(self.snapshot(), before)
        text = self.text(f"Designs/duo/{name}.md").replace("Depends on: None", "Depends on: None\nRepo: duo-b", 1)
        self.put(f"Agent/Designs/duo/{name}.md", text.replace("Depends on: S1", "Depends on: S1\nRepo: duo-a"))
        self.seal(name)
        self.validate()

    def test_a_project_without_repos_accepts_any_story(self):            # T6 -> AC4
        self.project("bare")
        self.seal(self.design(project="bare", ideas="None", decisions="### D1. A\nDecision: x.\nWhy: y."))
        self.validate()

    def test_a_failed_write_puts_everything_back(self):                  # T6 -> AC4
        name = self.design()
        before = self.snapshot()
        files_before = sorted(p.relative_to(self.vault).as_posix() for p in (self.vault / "Agent").rglob("*"))
        patches = [mock.patch.object(lib, "VAULT", self.vault), mock.patch.object(lib, "AGENT", self.vault / "Agent"),
                   mock.patch.object(vault, "AGENT", self.vault / "Agent"), mock.patch.object(vault, "VAULT", self.vault)]
        for p in patches:
            p.start()
        real = vault.append_log

        def fail_on_the_seal_line(entry):
            if lib.DESIGN_EVENT in entry:
                vault.die("simulated failure")
            return real(entry)
        try:
            with mock.patch.object(vault, "append_log", fail_on_the_seal_line), \
                    self.assertRaises(SystemExit):
                vault.cmd_seal_design(mock.Mock(design=name))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(p.relative_to(self.vault).as_posix() for p in (self.vault / "Agent").rglob("*")),
                         files_before)


class AllCommandsTest(CliCase):
    def test_every_note_writing_command_validates(self):          # T1 -> AC1
        self.put("Inbox/Phone.md", "x\n")
        self.cli("new", "project", "demo", "--repo", "me/demo", "--purpose", "A demo.")
        self.cli("new", "plan", "demo", "1")
        p = self.path("Work/demo/demo-1-impl.md")
        p.write_text(p.read_text(encoding="utf-8").replace("status: open", "pr: 5\nstatus: open", 1),
                     encoding="utf-8", newline="\n")
        self.cli("new", "retro", "demo", "1")
        self.cli("idea", "add", "First", "--project", "demo")
        self.cli("idea", "add", "Second")
        self.cli("idea", "drop", f"{TODAY}-second", "--reason", "no")
        self.cli("handoff", "pull", "Inbox/Phone.md")
        self.cli("log", "wrapped up", "--project", "demo")
        self.validate()


class StatusTest(CliCase):
    def test_status_lists_projects_and_open_ideas(self):          # T12 -> AC12
        self.cli("new", "project", "demo", "--repo", "me/demo", "--purpose", "A demo.")
        self.cli("new", "project", "bare")
        self.cli("idea", "add", "One")
        self.cli("idea", "add", "Two")
        self.cli("idea", "drop", f"{TODAY}-two", "--reason", "no")
        out = self.cli("status").stdout
        self.assertIn("A demo. (me/demo)", out)   # the dash is non-ASCII; run_py decodes with the locale
        self.assertIn("[[bare]]", out)
        self.assertIn("no repos", out)
        self.assertIn("Open ideas: 1", out)
        p = self.path("Projects/bare.md")
        p.write_text(p.read_text(encoding="utf-8").replace("status: active", "status: paused"),
                     encoding="utf-8", newline="\n")
        self.assertIn("Paused projects:", self.cli("status").stdout)

    def test_session_start_runs_on_a_v2_vault(self):              # T12 -> AC12
        self.cli("new", "project", "demo", "--repo", "me/demo")
        self.cli("idea", "add", "One")
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": "cli-t12", "cwd": str(self.vault)}, config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Active projects", r.stdout)
        self.assertIn("Open ideas: 1", r.stdout)
        self.assertIn("Vault check: OK", r.stdout)
        self.assertNotIn("failed", r.stdout)


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


class ClaudemdLintCliTest(CliCase):        # T1 -> AC1
    def make_repo(self) -> Path:
        repo = self.root / "repo"
        repo.mkdir()
        git("init", "-q", "-b", "main", cwd=repo)
        for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            git("config", k, v, cwd=repo)
        return repo

    def commit(self, repo: Path):
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "x", cwd=repo)

    def test_default_path_uses_the_current_clone(self):
        repo = self.make_repo()
        (repo / "CLAUDE.md").write_text("# demo\n\n## Purpose\nx.\n\n## Commands\n- Lint: `git --version`\n",
                                        encoding="utf-8", newline="\n")
        self.commit(repo)
        r = run_py(CLI, ["claudemd-lint"], config=self.cfg, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("claudemd-lint OK", r.stdout)

    def test_explicit_path_and_failure_exit_code(self):
        repo = self.make_repo()
        (repo / "CLAUDE.md").write_text("# demo\n\n## Layout\n- `missing.py`: nope.\n",
                                        encoding="utf-8", newline="\n")
        self.commit(repo)
        r = run_py(CLI, ["claudemd-lint", str(repo)], config=self.cfg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing.py", r.stdout)

    def test_refuses_outside_a_clone_without_a_path(self):
        r = run_py(CLI, ["claudemd-lint"], config=self.cfg, cwd=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not inside a git clone", r.stderr)


class V1RemovedTest(CliCase):
    def test_help_has_no_v1_options(self):                        # T13 -> AC13
        texts = [self.cli("--help").stdout, self.cli("new", "project", "--help").stdout]
        for text in texts:
            for word in ("--goal", "URL", "new daily", "daily"):
                self.assertNotIn(word, text)
        self.refused_argparse("new", "daily")
        self.refused_argparse("new", "project", "x", "--goal", "g")
        self.refused_argparse("new", "project", "x", "--title", "t")

    def refused_argparse(self, *args):
        r = run_py(CLI, list(args), config=self.cfg)
        self.assertEqual(r.returncode, 2, f"{args}: {r.stdout}{r.stderr}")

    def test_vault_guide_is_v2(self):                             # T13 -> AC13
        text = TEMPLATE.read_text(encoding="utf-8")
        for word in ("Notes/", "Intake", "Archive/", "Archive`"):
            self.assertNotIn(word, text)
        for word in ("Projects/", "Work/", "Ideas/", "Daily/", "{{CLI}} idea add", "{{CLI}} handoff pull",
                     "{{CLI}} new plan"):
            self.assertIn(word, text)
        self.assertFalse(re.search(r"new daily|--goal", text))


class IssueDocsTest(CliCase):                                     # T11 -> AC11 (#27)
    def test_usage_and_guide_name_the_issue_commands(self):
        usage = vault.__doc__
        guide = TEMPLATE.read_text(encoding="utf-8")
        for text in (usage, guide):
            self.assertIn("issue create", text)
            self.assertIn("idea promote <file> --body-file", text)
        self.assertIn("issue create", self.cli("--help").stdout + self.cli("issue", "--help").stdout)
        self.assertIn("`issue` skill", guide)
        repo_init = next(ln for ln in guide.splitlines() if "repo-init <path>" in ln)
        self.assertIn(".github/ISSUE_TEMPLATE/issue.md", repo_init)


class LintCliTest(CliCase):                                        # T3-T5, T8 -> AC3-AC5, AC8 (#33)
    def clone(self, lint: str | None = "git --version", purpose: bool = True) -> Path:
        repo = self.root / "repo"
        repo.mkdir()
        git("init", "-q", "-b", "main", cwd=repo)
        for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            git("config", k, v, cwd=repo)
        text = "# demo\n\n## Purpose\nx.\n\n## Commands\n- Run tests: `git --version`\n"
        if lint is not None:
            text += f"- Lint: `{lint}`\n"
        if not purpose:
            text += "\n## Layout\n- `missing.py`: nope.\n"
        (repo / "CLAUDE.md").write_text(text, encoding="utf-8", newline="\n")
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "x", cwd=repo)
        return repo

    def lint(self, repo: Path, cwd: Path | None = None, config: Path | None = None):
        return run_py(CLI, ["lint"], config=config or self.cfg, cwd=cwd or repo)

    def test_both_pass(self):
        r = self.lint(self.clone())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lint OK", r.stdout)

    def test_lint_command_fails(self):
        r = self.lint(self.clone("git not-a-subcommand"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("git not-a-subcommand", r.stderr)
        self.assertIn("run it by hand", r.stderr)

    def test_claudemd_lint_fails_but_the_lint_command_still_runs(self):
        repo = self.clone(purpose=False)
        (repo / "marker.py").write_text("", encoding="utf-8")
        r = self.lint(repo)
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing.py", r.stdout)
        self.assertIn("claudemd-lint failed", r.stderr)
        self.assertIn("claudemd-lint", r.stderr.split("run it by hand with:")[1])
        self.assertIn("git version", r.stdout)               # the lint command ran: `git --version`

    def test_missing_tool_is_named_and_never_skipped(self):
        r = self.lint(self.clone("not-a-real-linter-xyz ."))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not-a-real-linter-xyz", r.stderr)
        self.assertIn("not installed", r.stderr)

    def test_no_lint_line_fails(self):
        r = self.lint(self.clone(lint=None))
        self.assertEqual(r.returncode, 1)
        self.assertIn("Lint:", r.stderr)

    def test_runs_from_the_top_level_when_started_in_a_subfolder(self):
        repo = self.clone("git ls-files CLAUDE.md")
        (repo / "sub").mkdir()
        r = self.lint(repo, cwd=repo / "sub")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CLAUDE.md", r.stdout)

    def test_works_when_the_vault_path_does_not_exist(self):
        cfg = self.root / "other.json"
        cfg.write_text(json.dumps({"vault": (self.root / "gone").as_posix()}), encoding="utf-8")
        r = self.lint(self.clone(), config=cfg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_other_commands_still_need_a_vault(self):
        cfg = self.root / "other.json"
        cfg.write_text(json.dumps({"vault": (self.root / "gone").as_posix()}), encoding="utf-8")
        r = run_py(CLI, ["status"], config=cfg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no usable vault", r.stderr)

    def test_readme_names_ruff_path_and_the_command(self):         # T10 -> AC10
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for needle in ("ruff", "PATH", "ruff check ."):
            self.assertIn(needle, readme)

    def test_usage_and_guide_name_lint(self):                      # T10 -> AC10
        self.assertIn("\n  lint\n", vault.__doc__)
        self.assertIn("{{CLI}} lint", TEMPLATE.read_text(encoding="utf-8"))
        self.assertIn("lint", self.cli("--help").stdout)


if __name__ == "__main__":
    unittest.main()
